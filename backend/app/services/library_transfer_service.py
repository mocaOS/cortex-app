"""Library export/import service for full knowledge graph + document transfer."""

import os
import io
import json
import logging
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from neo4j.time import DateTime as Neo4jDateTime

from app.config import get_settings

logger = logging.getLogger(__name__)

EXPORT_VERSION = "1.0"


def _serialize_value(val):
    """Serialize Neo4j types to JSON-safe values."""
    if isinstance(val, Neo4jDateTime):
        return val.isoformat()
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, (list, tuple)):
        return [_serialize_value(v) for v in val]
    if isinstance(val, dict):
        return {k: _serialize_value(v) for k, v in val.items()}
    return val


def _serialize_record(record: dict) -> dict:
    """Serialize a Neo4j record dict to JSON-safe dict."""
    return {k: _serialize_value(v) for k, v in record.items()}


def _write_ndjson(zf: zipfile.ZipFile, filename: str, records) -> int:
    """Stream records into a zip entry as NDJSON, one JSON object per line.

    `records` is any iterable of dicts. Only a single serialized line is held in
    memory at a time, so embedding-heavy data (chunks, entities) never accumulates
    into a giant list + join copy — the original cause of export OOM kills.

    Passing a string filename inherits the archive's compression (ZIP_DEFLATED);
    force_zip64 keeps large entries valid. Returns the number of records written.
    """
    count = 0
    with zf.open(filename, "w", force_zip64=True) as fh:
        for record in records:
            fh.write((json.dumps(_serialize_record(record)) + "\n").encode("utf-8"))
            count += 1
    return count


def _iter_ndjson(zf: zipfile.ZipFile, filename: str):
    """Yield parsed records from a zip NDJSON entry one line at a time.

    Reads through a streaming TextIOWrapper over the decompressed entry, so the
    whole file is never held in memory — the import-side mirror of _write_ndjson.
    Missing entries yield nothing.
    """
    if filename not in zf.namelist():
        return
    with zf.open(filename, "r") as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8")
        for line in text:
            line = line.strip()
            if line:
                yield json.loads(line)


def _iter_ndjson_batches(zf: zipfile.ZipFile, filename: str, batch_size: int):
    """Yield records from a zip NDJSON entry in lists of up to batch_size.

    Only one batch is held in memory at a time, so embedding-heavy entries
    (chunks, entities) stream into batched inserts without buffering the file.
    """
    batch = []
    for record in _iter_ndjson(zf, filename):
        batch.append(record)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def _count_ndjson(zf: zipfile.ZipFile, filename: str) -> int:
    """Count non-blank lines in a zip NDJSON entry without parsing or buffering.

    Used for pre-import plan-limit guards so a 20K-entity archive isn't loaded
    into RAM (with embeddings) just to call len() on it.
    """
    if filename not in zf.namelist():
        return 0
    count = 0
    with zf.open(filename, "r") as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8")
        for line in text:
            if line.strip():
                count += 1
    return count


class LibraryTransferService:
    """Handles full library export and import."""

    def __init__(self, neo4j_service):
        self.neo4j = neo4j_service
        self.settings = get_settings()

    def export_library(self, task_id: str, output_path: str, update_progress, complete_task_fn, fail_task_fn):
        """
        Build a library export ZIP. Runs synchronously (called from background task).

        Args:
            task_id: Task ID for progress tracking
            output_path: Path to write the ZIP file
            update_progress: Callable(task_id, current, total, message)
            complete_task_fn: Callable(task_id, result_dict)
            fail_task_fn: Callable(task_id, error_str)
        """
        try:
            total_steps = 15
            step = 0

            update_progress(task_id, step, total_steps, "Gathering statistics...")

            # Get stats for manifest
            stats = self.neo4j.get_stats()
            stats_dict = {
                "document_count": stats["document_count"],
                "chunk_count": stats["chunk_count"],
                "entity_count": stats["entity_count"],
                "relationship_count": stats["relationship_count"],
                "community_count": stats["community_count"],
                "collection_count": stats["collection_count"],
                "skill_count": len(self.neo4j.export_all_skills()),
            }

            manifest = {
                "version": EXPORT_VERSION,
                "export_date": datetime.utcnow().isoformat() + "Z",
                "embedding_model": self.settings.embedding_model,
                "embedding_dimension": self.settings.embedding_dimension,
                "stats": stats_dict,
            }

            with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:

                # 1. Manifest
                step += 1
                update_progress(task_id, step, total_steps, "Writing manifest...")
                zf.writestr("manifest.json", json.dumps(manifest, indent=2))

                batch_size = 500

                # 2. Documents — kept as a list (small, no embeddings) because the
                #    file-packaging step below needs file_path/id for every doc.
                step += 1
                update_progress(task_id, step, total_steps, f"Exporting {stats_dict['document_count']} documents...")
                documents = self.neo4j.export_all_documents()
                _write_ndjson(zf, "documents.ndjson", documents)

                # 3. Chunks (batched + streamed — carry embeddings, the heaviest payload)
                step += 1
                chunk_count = self.neo4j.export_chunk_count()
                update_progress(task_id, step, total_steps, f"Exporting {chunk_count} chunks...")

                def _chunk_records():
                    offset = 0
                    written = 0
                    while True:
                        batch = self.neo4j.export_all_chunks_batched(batch_size=batch_size, skip=offset)
                        if not batch:
                            break
                        for item in batch:
                            yield {"chunk": item["chunk"], "document_id": item["document_id"]}
                        written += len(batch)
                        offset += batch_size
                        update_progress(task_id, step, total_steps, f"Exporting chunks ({min(written, chunk_count)}/{chunk_count})...")

                _write_ndjson(zf, "chunks.ndjson", _chunk_records())

                # 4. Entities (batched + streamed — also carry embeddings)
                step += 1
                entity_count = self.neo4j.export_entity_count()
                update_progress(task_id, step, total_steps, f"Exporting {entity_count} entities...")

                def _entity_records():
                    offset = 0
                    written = 0
                    while True:
                        batch = self.neo4j.export_all_entities_batched(batch_size=batch_size, skip=offset)
                        if not batch:
                            break
                        for entity in batch:
                            yield entity
                        written += len(batch)
                        offset += batch_size
                        update_progress(task_id, step, total_steps, f"Exporting entities ({min(written, entity_count)}/{entity_count})...")

                _write_ndjson(zf, "entities.ndjson", _entity_records())

                # 5. Relationships (batched + streamed)
                step += 1
                relationship_count = self.neo4j.export_relationship_count()
                update_progress(task_id, step, total_steps, f"Exporting {relationship_count} relationships...")

                def _relationship_records():
                    offset = 0
                    written = 0
                    while True:
                        batch = self.neo4j.export_all_entity_relationships_batched(batch_size=batch_size, skip=offset)
                        if not batch:
                            break
                        for rel in batch:
                            yield rel
                        written += len(batch)
                        offset += batch_size
                        update_progress(task_id, step, total_steps, f"Exporting relationships ({min(written, relationship_count)}/{relationship_count})...")

                _write_ndjson(zf, "relationships.ndjson", _relationship_records())

                # 6. Communities
                step += 1
                update_progress(task_id, step, total_steps, f"Exporting {stats_dict['community_count']} communities...")
                _write_ndjson(zf, "communities.ndjson", self.neo4j.export_all_communities())

                # 7. Community members
                step += 1
                update_progress(task_id, step, total_steps, "Exporting community memberships...")
                _write_ndjson(zf, "community_members.ndjson", self.neo4j.export_community_members())

                # 8. Collections
                step += 1
                update_progress(task_id, step, total_steps, "Exporting collections...")
                _write_ndjson(zf, "collections.ndjson", self.neo4j.export_all_collections())

                # 9. Collection members
                step += 1
                update_progress(task_id, step, total_steps, "Exporting collection memberships...")
                _write_ndjson(zf, "collection_members.ndjson", self.neo4j.export_collection_members())

                # 10. Chunk mentions
                step += 1
                update_progress(task_id, step, total_steps, "Exporting chunk-entity links...")
                _write_ndjson(zf, "chunk_mentions.ndjson", self.neo4j.export_all_chunk_mentions())

                # 11. Merge history
                step += 1
                update_progress(task_id, step, total_steps, "Exporting merge history...")
                _write_ndjson(zf, "merge_history.ndjson", self.neo4j.export_all_merge_history())

                # 12. System meta
                step += 1
                update_progress(task_id, step, total_steps, "Exporting system metadata...")
                _write_ndjson(zf, "system_meta.ndjson", self.neo4j.export_all_system_meta())

                # 13. Skills (nodes + files)
                step += 1
                skills = self.neo4j.export_all_skills()
                update_progress(task_id, step, total_steps, f"Exporting {len(skills)} skills...")
                _write_ndjson(zf, "skills.ndjson", skills)
                # Bundle skill directories (SKILL.md, tools.json, etc.).
                # config.json is sanitized: secret-typed fields (per the skill's
                # config_schema) are stripped so exports never carry credentials
                # — plaintext or ciphertext. Admins re-enter secrets after import.
                for skill in skills:
                    skill_dir = Path(skill.get("directory_path", ""))
                    skill_id = skill.get("skill_id", "")
                    if skill_dir.is_dir() and skill_id:
                        secret_names = set()
                        try:
                            schema = json.loads(skill.get("config_schema") or "[]")
                            secret_names = {
                                v["name"] for v in schema
                                if v.get("type") == "secret" and "name" in v
                            }
                        except (json.JSONDecodeError, TypeError, AttributeError):
                            pass
                        for f in skill_dir.iterdir():
                            if not f.is_file():
                                continue
                            if f.name == "config.json" and secret_names:
                                try:
                                    cfg = json.loads(f.read_text(encoding="utf-8"))
                                    sanitized = {
                                        k: v for k, v in cfg.items()
                                        if k not in secret_names
                                    }
                                    zf.writestr(
                                        f"skills/{skill_id}/config.json",
                                        json.dumps(sanitized, indent=2),
                                    )
                                except (json.JSONDecodeError, OSError) as e:
                                    logger.warning(
                                        f"Skipping config.json for skill "
                                        f"'{skill_id}' in export: {e}"
                                    )
                                continue
                            zf.write(str(f), f"skills/{skill_id}/{f.name}")

                # 14. Document files
                step += 1
                missing_files = []
                file_count = 0
                update_progress(task_id, step, total_steps, "Packaging document files...")
                for doc in documents:
                    file_path = doc.get("file_path")
                    if not file_path:
                        continue
                    fp = Path(file_path)
                    if not fp.exists():
                        missing_files.append(doc.get("id", "unknown"))
                        continue
                    # Store as files/{doc_id}{extension}
                    doc_id = doc.get("id", "unknown")
                    arcname = f"files/{doc_id}{fp.suffix}"
                    zf.write(str(fp), arcname)
                    file_count += 1

                # 15. Done
                step += 1
                update_progress(task_id, step, total_steps, "Finalizing export...")

            file_size = os.path.getsize(output_path)
            result = {
                "file_path": output_path,
                "file_size": file_size,
                "stats": stats_dict,
                "files_exported": file_count,
                "missing_files": missing_files,
            }
            complete_task_fn(task_id, result)
            logger.info(f"Library export completed: {file_size} bytes, {file_count} files")

        except Exception as e:
            logger.error(f"Library export failed: {e}", exc_info=True)
            fail_task_fn(task_id, str(e))

    def import_library(self, task_id: str, zip_path: str, mode: str, update_progress, complete_task_fn, fail_task_fn):
        """
        Import a library from an export ZIP. Runs synchronously (called from background task).
        """
        try:
            total_steps = 17
            step = 0
            warnings = []

            update_progress(task_id, step, total_steps, "Validating archive...")

            # 1. Validate ZIP
            if not zipfile.is_zipfile(zip_path):
                fail_task_fn(task_id, "Invalid ZIP file")
                return

            with zipfile.ZipFile(zip_path, "r") as zf:
                if "manifest.json" not in zf.namelist():
                    fail_task_fn(task_id, "Invalid export archive: missing manifest.json")
                    return

                # Helper to read NDJSON
                def read_ndjson(filename):
                    if filename not in zf.namelist():
                        return []
                    data = zf.read(filename).decode("utf-8")
                    items = []
                    for line in data.strip().split("\n"):
                        line = line.strip()
                        if line:
                            items.append(json.loads(line))
                    return items

                # 2. Read manifest
                step += 1
                update_progress(task_id, step, total_steps, "Reading manifest...")
                manifest = json.loads(zf.read("manifest.json"))
                export_version = manifest.get("version", "unknown")
                if export_version != EXPORT_VERSION:
                    warnings.append(f"Export version mismatch: archive is v{export_version}, expected v{EXPORT_VERSION}")

                # Enforce MAX_FILES before any destructive action (e.g. replace-mode reset).
                if self.settings.max_files > 0:
                    incoming_doc_count = _count_ndjson(zf, "documents.ndjson")
                    if incoming_doc_count > self.settings.max_files:
                        fail_task_fn(
                            task_id,
                            f"File limit reached: import would add {incoming_doc_count} documents, "
                            f"exceeding your plan limit ({self.settings.max_files}). "
                            f"Upgrade your plan or use a smaller export."
                        )
                        return

                # Enforce MAX_ENTITIES before any destructive action.
                if self.settings.max_entities > 0:
                    incoming_entity_count = _count_ndjson(zf, "entities.ndjson")
                    if incoming_entity_count > self.settings.max_entities:
                        fail_task_fn(
                            task_id,
                            f"Entity limit reached: import would add {incoming_entity_count} entities, "
                            f"exceeding your plan limit ({self.settings.max_entities}). "
                            f"Upgrade your plan or use a smaller export."
                        )
                        return

                # 3. Check embedding compatibility
                step += 1
                update_progress(task_id, step, total_steps, "Checking compatibility...")
                embedding_compatible = True
                if manifest.get("embedding_model") != self.settings.embedding_model:
                    embedding_compatible = False
                    warnings.append(
                        f"Embedding model mismatch: archive used '{manifest.get('embedding_model')}', "
                        f"this instance uses '{self.settings.embedding_model}'. "
                        "Vector search may not work correctly. Consider reprocessing documents."
                    )
                if manifest.get("embedding_dimension") != self.settings.embedding_dimension:
                    embedding_compatible = False
                    warnings.append(
                        f"Embedding dimension mismatch: archive has {manifest.get('embedding_dimension')}, "
                        f"this instance expects {self.settings.embedding_dimension}."
                    )

                # 4. Check target state / reset if needed
                step += 1
                update_progress(task_id, step, total_steps, "Preparing target instance...")
                current_stats = self.neo4j.get_stats()

                if mode == "clean":
                    if current_stats["document_count"] > 0 or current_stats["entity_count"] > 0:
                        fail_task_fn(
                            task_id,
                            "Target instance is not empty. Use 'replace' mode to overwrite existing data, "
                            "or reset the system first from Settings > Danger Zone."
                        )
                        return
                elif mode == "replace":
                    update_progress(task_id, step, total_steps, "Clearing existing data...")
                    self._full_reset()

                # 5. Import collections
                step += 1
                collections = read_ndjson("collections.ndjson")
                update_progress(task_id, step, total_steps, f"Importing {len(collections)} collections...")
                collections_imported = 0
                if collections:
                    collections_imported = self.neo4j.import_collections_batch(collections)

                # 6. Import documents (nodes only, files later)
                step += 1
                documents = read_ndjson("documents.ndjson")
                update_progress(task_id, step, total_steps, f"Importing {len(documents)} documents...")
                docs_imported = 0
                if documents:
                    # Remap file paths to this instance
                    for doc in documents:
                        old_path = doc.get("file_path", "")
                        if old_path:
                            fname = Path(old_path).name
                            if doc.get("is_custom_input"):
                                doc["file_path"] = str(Path(self.settings.custom_inputs_dir) / fname)
                            else:
                                doc["file_path"] = str(Path(self.settings.upload_dir) / fname)
                        # Set processing status to completed (already processed)
                        doc["processing_status"] = "completed"
                    docs_imported = self.neo4j.import_documents_batch(documents)

                # 7. Copy document files from ZIP
                step += 1
                update_progress(task_id, step, total_steps, "Restoring document files...")
                files_imported = 0
                upload_dir = Path(self.settings.upload_dir)
                custom_inputs_dir = Path(self.settings.custom_inputs_dir)
                upload_dir.mkdir(parents=True, exist_ok=True)
                custom_inputs_dir.mkdir(parents=True, exist_ok=True)

                for doc in documents:
                    doc_id = doc.get("id", "")
                    old_file_path = doc.get("file_path", "")
                    if not old_file_path:
                        continue
                    fname = Path(old_file_path).name
                    # Find the file in ZIP - look for files/{doc_id}.ext
                    zip_entries = [n for n in zf.namelist() if n.startswith(f"files/{doc_id}")]
                    if not zip_entries:
                        warnings.append(f"Missing file for document '{doc.get('filename', doc_id)}'")
                        continue
                    zip_entry = zip_entries[0]
                    if doc.get("is_custom_input"):
                        dest = custom_inputs_dir / fname
                    else:
                        dest = upload_dir / fname
                    with zf.open(zip_entry) as src, open(str(dest), "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    files_imported += 1

                # Manifest stats drive progress totals so we never pre-read the
                # heavy NDJSON files just to count them.
                manifest_stats = manifest.get("stats", {})

                # 8. Import chunks (streamed in batches — carry embeddings)
                step += 1
                chunk_total = manifest_stats.get("chunk_count", 0)
                update_progress(task_id, step, total_steps, f"Importing {chunk_total} chunks...")
                chunks_imported = 0
                batch_size = 500
                processed = 0
                for batch in _iter_ndjson_batches(zf, "chunks.ndjson", batch_size):
                    chunks_imported += self.neo4j.import_chunks_batch(batch)
                    processed += len(batch)
                    update_progress(
                        task_id, step, total_steps,
                        f"Importing chunks ({processed}/{chunk_total or processed})..."
                    )

                # 9. Import entities (streamed in batches — carry embeddings)
                step += 1
                entity_total = manifest_stats.get("entity_count", 0)
                update_progress(task_id, step, total_steps, f"Importing {entity_total} entities...")
                entities_imported = 0
                processed = 0
                for batch in _iter_ndjson_batches(zf, "entities.ndjson", batch_size):
                    entities_imported += self.neo4j.import_entities_batch(batch)
                    processed += len(batch)
                    update_progress(
                        task_id, step, total_steps,
                        f"Importing entities ({processed}/{entity_total or processed})..."
                    )

                # 10. Import chunk mentions (streamed in batches)
                step += 1
                update_progress(task_id, step, total_steps, "Importing chunk-entity links...")
                mentions_imported = 0
                for batch in _iter_ndjson_batches(zf, "chunk_mentions.ndjson", batch_size):
                    mentions_imported += self.neo4j.import_chunk_mentions_batch(batch)
                    update_progress(
                        task_id, step, total_steps,
                        f"Importing chunk-entity links ({mentions_imported} imported)..."
                    )

                # 11. Import relationships (streamed one at a time)
                step += 1
                rel_total = manifest_stats.get("relationship_count", 0)
                update_progress(task_id, step, total_steps, f"Importing {rel_total} relationships...")
                rels_imported = 0
                idx = 0
                for rel in _iter_ndjson(zf, "relationships.ndjson"):
                    if idx % 100 == 0 and idx > 0:
                        update_progress(
                            task_id, step, total_steps,
                            f"Importing relationships ({idx}/{rel_total or idx})..."
                        )
                    idx += 1
                    props = {
                        "description": rel.get("description", ""),
                        "weight": rel.get("weight", 5.0),
                        "confidence": rel.get("confidence"),
                        "extraction_method": rel.get("extraction_method", ""),
                        "source_document_id": rel.get("source_document_id"),
                        "extracted_at": rel.get("extracted_at"),
                    }
                    try:
                        if self.neo4j.import_relationship(
                            rel["source"], rel["target"], rel["rel_type"], props
                        ):
                            rels_imported += 1
                    except Exception as e:
                        logger.warning(f"Failed to import relationship {rel.get('source')} -> {rel.get('target')}: {e}")

                # 12. Import communities
                step += 1
                communities = read_ndjson("communities.ndjson")
                update_progress(task_id, step, total_steps, f"Importing {len(communities)} communities...")
                communities_imported = 0
                if communities:
                    communities_imported = self.neo4j.import_communities_batch(communities)

                # 13. Import community members
                step += 1
                community_members = read_ndjson("community_members.ndjson")
                update_progress(task_id, step, total_steps, "Importing community memberships...")
                if community_members:
                    self.neo4j.import_community_members_batch(community_members)

                # 14. Import collection members
                step += 1
                collection_members = read_ndjson("collection_members.ndjson")
                update_progress(task_id, step, total_steps, "Importing collection memberships...")
                if collection_members:
                    self.neo4j.import_collection_members_batch(collection_members)

                # 15. Import merge history
                step += 1
                merge_history = read_ndjson("merge_history.ndjson")
                update_progress(task_id, step, total_steps, "Importing merge history...")
                merge_history_imported = 0
                if merge_history:
                    merge_history_imported = self.neo4j.import_merge_history_batch(merge_history)

                # 16. Import system meta
                step += 1
                system_meta = read_ndjson("system_meta.ndjson")
                update_progress(task_id, step, total_steps, "Importing system metadata...")
                if system_meta:
                    self.neo4j.import_system_meta_batch(system_meta)

                # 17. Import skills (nodes + files)
                step += 1
                skills = read_ndjson("skills.ndjson")
                update_progress(task_id, step, total_steps, f"Importing {len(skills)} skills...")
                skills_imported = 0
                if skills:
                    skills_dir = Path(self.settings.skills_dir)
                    skills_dir.mkdir(parents=True, exist_ok=True)
                    # Restore skill files from ZIP
                    for skill in skills:
                        skill_id = skill.get("skill_id", "")
                        if not skill_id:
                            continue
                        target_dir = skills_dir / skill_id
                        target_dir.mkdir(parents=True, exist_ok=True)
                        prefix = f"skills/{skill_id}/"
                        for entry_name in zf.namelist():
                            if entry_name.startswith(prefix) and not entry_name.endswith("/"):
                                fname = Path(entry_name).name
                                with zf.open(entry_name) as src, open(str(target_dir / fname), "wb") as dst:
                                    shutil.copyfileobj(src, dst)
                        # Remap directory_path to this instance
                        skill["directory_path"] = str(target_dir)
                    skills_imported = self.neo4j.import_skills_batch(skills)

                # 18. Done
                step += 1
                update_progress(task_id, step, total_steps, "Finalizing import...")

            # Clean up temp ZIP
            try:
                os.unlink(zip_path)
            except Exception:
                pass

            result = {
                "documents_imported": docs_imported,
                "chunks_imported": chunks_imported,
                "entities_imported": entities_imported,
                "relationships_imported": rels_imported,
                "communities_imported": communities_imported,
                "collections_imported": collections_imported,
                "files_imported": files_imported,
                "merge_history_imported": merge_history_imported,
                "skills_imported": skills_imported,
                "embedding_compatible": embedding_compatible,
                "warnings": warnings,
            }
            complete_task_fn(task_id, result)
            logger.info(f"Library import completed: {docs_imported} docs, {entities_imported} entities, {rels_imported} relationships")

        except Exception as e:
            logger.error(f"Library import failed: {e}", exc_info=True)
            fail_task_fn(task_id, str(e))

    def _full_reset(self):
        """Clear all data from the instance (reuses existing delete methods)."""
        settings = self.settings
        # Delete graph data
        self.neo4j.delete_all_documents()
        self.neo4j.delete_all_merge_history()
        self.neo4j.delete_all_system_meta()
        self.neo4j.delete_all_collections()
        self.neo4j.delete_all_skills()

        # Delete files from disk
        for dir_path in [settings.upload_dir, settings.custom_inputs_dir]:
            p = Path(dir_path)
            if p.exists():
                for f in p.iterdir():
                    if f.is_file():
                        try:
                            f.unlink()
                        except Exception as e:
                            logger.warning(f"Failed to delete {f}: {e}")

        # Delete skill directories
        skills_dir = Path(settings.skills_dir)
        if skills_dir.exists():
            for entry in skills_dir.iterdir():
                if entry.is_dir():
                    try:
                        shutil.rmtree(entry)
                    except Exception as e:
                        logger.warning(f"Failed to delete skill dir {entry}: {e}")


# Singleton
_transfer_service: Optional[LibraryTransferService] = None


def get_library_transfer_service():
    global _transfer_service
    if _transfer_service is None:
        from app.services.neo4j_service import get_neo4j_service
        _transfer_service = LibraryTransferService(get_neo4j_service())
    return _transfer_service
