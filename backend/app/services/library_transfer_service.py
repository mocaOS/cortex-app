"""Library export/import service for full knowledge graph + document transfer."""

import os
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

                # 2. Documents
                step += 1
                update_progress(task_id, step, total_steps, f"Exporting {stats_dict['document_count']} documents...")
                documents = self.neo4j.export_all_documents()
                lines = [json.dumps(_serialize_record(d)) for d in documents]
                zf.writestr("documents.ndjson", "\n".join(lines) + "\n" if lines else "")

                # 3. Chunks (batched)
                step += 1
                chunk_count = self.neo4j.export_chunk_count()
                update_progress(task_id, step, total_steps, f"Exporting {chunk_count} chunks...")
                chunk_lines = []
                batch_size = 500
                offset = 0
                while True:
                    batch = self.neo4j.export_all_chunks_batched(batch_size=batch_size, skip=offset)
                    if not batch:
                        break
                    for item in batch:
                        record = {
                            "chunk": _serialize_record(item["chunk"]),
                            "document_id": item["document_id"],
                        }
                        chunk_lines.append(json.dumps(record))
                    offset += batch_size
                    update_progress(task_id, step, total_steps, f"Exporting chunks ({min(offset, chunk_count)}/{chunk_count})...")
                zf.writestr("chunks.ndjson", "\n".join(chunk_lines) + "\n" if chunk_lines else "")

                # 4. Entities
                step += 1
                update_progress(task_id, step, total_steps, f"Exporting {stats_dict['entity_count']} entities...")
                entities = self.neo4j.export_all_entities()
                lines = [json.dumps(_serialize_record(e)) for e in entities]
                zf.writestr("entities.ndjson", "\n".join(lines) + "\n" if lines else "")

                # 5. Relationships
                step += 1
                update_progress(task_id, step, total_steps, f"Exporting {stats_dict['relationship_count']} relationships...")
                relationships = self.neo4j.export_all_entity_relationships()
                lines = [json.dumps(_serialize_record(r)) for r in relationships]
                zf.writestr("relationships.ndjson", "\n".join(lines) + "\n" if lines else "")

                # 6. Communities
                step += 1
                update_progress(task_id, step, total_steps, f"Exporting {stats_dict['community_count']} communities...")
                communities = self.neo4j.export_all_communities()
                lines = [json.dumps(_serialize_record(c)) for c in communities]
                zf.writestr("communities.ndjson", "\n".join(lines) + "\n" if lines else "")

                # 7. Community members
                step += 1
                update_progress(task_id, step, total_steps, "Exporting community memberships...")
                community_members = self.neo4j.export_community_members()
                lines = [json.dumps(_serialize_record(m)) for m in community_members]
                zf.writestr("community_members.ndjson", "\n".join(lines) + "\n" if lines else "")

                # 8. Collections
                step += 1
                update_progress(task_id, step, total_steps, "Exporting collections...")
                collections = self.neo4j.export_all_collections()
                lines = [json.dumps(_serialize_record(c)) for c in collections]
                zf.writestr("collections.ndjson", "\n".join(lines) + "\n" if lines else "")

                # 9. Collection members
                step += 1
                update_progress(task_id, step, total_steps, "Exporting collection memberships...")
                collection_members = self.neo4j.export_collection_members()
                lines = [json.dumps(_serialize_record(m)) for m in collection_members]
                zf.writestr("collection_members.ndjson", "\n".join(lines) + "\n" if lines else "")

                # 10. Chunk mentions
                step += 1
                update_progress(task_id, step, total_steps, "Exporting chunk-entity links...")
                mentions = self.neo4j.export_all_chunk_mentions()
                lines = [json.dumps(_serialize_record(m)) for m in mentions]
                zf.writestr("chunk_mentions.ndjson", "\n".join(lines) + "\n" if lines else "")

                # 11. Merge history
                step += 1
                update_progress(task_id, step, total_steps, "Exporting merge history...")
                merge_history = self.neo4j.export_all_merge_history()
                lines = [json.dumps(_serialize_record(h)) for h in merge_history]
                zf.writestr("merge_history.ndjson", "\n".join(lines) + "\n" if lines else "")

                # 12. System meta
                step += 1
                update_progress(task_id, step, total_steps, "Exporting system metadata...")
                system_meta = self.neo4j.export_all_system_meta()
                lines = [json.dumps(_serialize_record(m)) for m in system_meta]
                zf.writestr("system_meta.ndjson", "\n".join(lines) + "\n" if lines else "")

                # 13. Skills (nodes + files)
                step += 1
                skills = self.neo4j.export_all_skills()
                update_progress(task_id, step, total_steps, f"Exporting {len(skills)} skills...")
                lines = [json.dumps(_serialize_record(s)) for s in skills]
                zf.writestr("skills.ndjson", "\n".join(lines) + "\n" if lines else "")
                # Bundle skill directories (SKILL.md, tools.json, etc.)
                for skill in skills:
                    skill_dir = Path(skill.get("directory_path", ""))
                    skill_id = skill.get("skill_id", "")
                    if skill_dir.is_dir() and skill_id:
                        for f in skill_dir.iterdir():
                            if f.is_file():
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

                # 2. Read manifest
                step += 1
                update_progress(task_id, step, total_steps, "Reading manifest...")
                manifest = json.loads(zf.read("manifest.json"))
                export_version = manifest.get("version", "unknown")
                if export_version != EXPORT_VERSION:
                    warnings.append(f"Export version mismatch: archive is v{export_version}, expected v{EXPORT_VERSION}")

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

                # 8. Import chunks (batched)
                step += 1
                chunks = read_ndjson("chunks.ndjson")
                chunks_imported = 0
                batch_size = 500
                for i in range(0, len(chunks), batch_size):
                    batch = chunks[i:i + batch_size]
                    update_progress(
                        task_id, step, total_steps,
                        f"Importing chunks ({min(i + batch_size, len(chunks))}/{len(chunks)})..."
                    )
                    chunks_imported += self.neo4j.import_chunks_batch(batch)

                # 9. Import entities
                step += 1
                entities = read_ndjson("entities.ndjson")
                update_progress(task_id, step, total_steps, f"Importing {len(entities)} entities...")
                entities_imported = 0
                if entities:
                    # Batch entities in groups of 500 to avoid memory issues
                    for i in range(0, len(entities), 500):
                        batch = entities[i:i + 500]
                        entities_imported += self.neo4j.import_entities_batch(batch)

                # 10. Import chunk mentions
                step += 1
                mentions = read_ndjson("chunk_mentions.ndjson")
                update_progress(task_id, step, total_steps, f"Importing {len(mentions)} chunk-entity links...")
                mentions_imported = 0
                if mentions:
                    for i in range(0, len(mentions), 500):
                        batch = mentions[i:i + 500]
                        mentions_imported += self.neo4j.import_chunk_mentions_batch(batch)

                # 11. Import relationships
                step += 1
                relationships = read_ndjson("relationships.ndjson")
                update_progress(task_id, step, total_steps, f"Importing {len(relationships)} relationships...")
                rels_imported = 0
                for idx, rel in enumerate(relationships):
                    if idx % 100 == 0 and idx > 0:
                        update_progress(
                            task_id, step, total_steps,
                            f"Importing relationships ({idx}/{len(relationships)})..."
                        )
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
