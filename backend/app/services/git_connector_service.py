"""Git connector — incremental, versioning-aware repo ingestion.

Clones a connection's repo into a persisted work dir, computes the change set
since the last synced commit (``git diff``, with a full-tree reconcile fallback
when history was rewritten or the old commit isn't available), and maps each
change to a document operation:

    Added    → create a pending document (fast-path ingest)
    Modified → tear down the old chunks/graph + re-ingest in place
    Deleted  → flag the document 'orphaned' for user review (never auto-delete)
    Renamed  → remap the document's path (+ re-ingest if content also changed)

Only text/code files (the document processor's RAW_TEXT_EXTENSIONS) and wiki
pages are ingested. Binaries and oversized files are skipped and reported.
The PAT is injected into git via the clone URL only; it is never persisted in
.git/config and is scrubbed from all logs/errors.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..config import get_settings
from .git_providers import get_provider, GitProviderError, parse_insecure_hosts

logger = logging.getLogger(__name__)

# Graph-staleness sentinel already understood by the stats/UI staleness logic.
_STALE_SENTINEL = "2000-01-01T00:00:00+00:00"

# Document formats (beyond code/markdown) that the connector will ingest when a
# glob matches them — routed through Docling. Deliberately excludes images/audio
# so a repo sync doesn't OCR every logo; the default include-glob is .pdf + .md.
_GIT_DOC_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".html", ".htm",
}


class GitSyncError(Exception):
    """Raised when a sync cannot proceed (clone failure, size guard, etc.)."""


class GitConnectorService:
    def __init__(self):
        self.settings = get_settings()

    # ----- lazy service handles (avoid import cycles at module load) ---------

    @property
    def neo4j(self):
        from .neo4j_service import get_neo4j_service
        return get_neo4j_service()

    @property
    def processor(self):
        from .document_processor import get_document_processor
        return get_document_processor()

    # ----- paths -------------------------------------------------------------

    def _conn_dir(self, connection_id: str) -> Path:
        return Path(self.settings.git_work_dir) / connection_id

    def _repo_dir(self, connection_id: str) -> Path:
        return self._conn_dir(connection_id) / "repo"

    def _files_dir(self, connection_id: str) -> Path:
        return self._conn_dir(connection_id) / "files"

    def _content_path(self, connection_id: str, git_path: str) -> Path:
        """Deterministic on-disk path for a repo file's persisted content."""
        safe = git_path.replace("..", "_")
        return self._files_dir(connection_id) / safe

    # ----- git subprocess helpers -------------------------------------------

    async def _git(self, args: list[str], cwd: Optional[Path] = None, token: str = "") -> str:
        """Run a git command, returning stdout. Raises GitSyncError (token scrubbed)."""
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            msg = err.decode("utf-8", "replace").strip() or "git command failed"
            if token:
                msg = msg.replace(token, "***")
            raise GitSyncError(f"git {args[0]} failed: {msg[:400]}")
        return out.decode("utf-8", "replace")

    def _tls_git_config(self, host: str) -> list[str]:
        """Per-invocation -c flags disabling TLS verification for allowlisted hosts."""
        if host in parse_insecure_hosts():
            return ["-c", "http.sslVerify=false"]
        return []

    async def _clone_or_fetch(self, connection_id: str, clone_url: str, branch: str,
                              host: str, token: str) -> str:
        """Ensure the work tree exists and the remote branch is fetched. Returns NEW head sha."""
        repo_dir = self._repo_dir(connection_id)
        depth = max(1, int(self.settings.git_clone_depth or 1))
        tls = self._tls_git_config(host)
        if not (repo_dir / ".git").exists():
            repo_dir.parent.mkdir(parents=True, exist_ok=True)
            await self._git(
                tls + ["clone", "--depth", str(depth), "--single-branch",
                       "--branch", branch, clone_url, str(repo_dir)],
                token=token,
            )
            # Scrub the token out of the persisted remote.
            clean_url = clone_url.split("@", 1)[-1]
            await self._git(["-C", str(repo_dir), "remote", "set-url", "origin",
                             f"https://{clean_url}"], token=token)
        else:
            await self._git(
                tls + ["-C", str(repo_dir), "fetch", "--depth", str(depth), clone_url, branch],
                token=token,
            )
        head = await self._git(["-C", str(repo_dir), "rev-parse", "FETCH_HEAD"], token=token) \
            if (repo_dir / ".git" / "FETCH_HEAD").exists() else \
            await self._git(["-C", str(repo_dir), "rev-parse", "HEAD"], token=token)
        return head.strip()

    async def _has_commit(self, repo_dir: Path, sha: str) -> bool:
        try:
            await self._git(["-C", str(repo_dir), "cat-file", "-e", sha])
            return True
        except GitSyncError:
            return False

    async def _is_ancestor(self, repo_dir: Path, old: str, new: str) -> bool:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(repo_dir), "merge-base", "--is-ancestor", old, new,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        return proc.returncode == 0

    async def _blob_size(self, repo_dir: Path, sha: str) -> int:
        try:
            out = await self._git(["-C", str(repo_dir), "cat-file", "-s", sha])
            return int(out.strip())
        except (GitSyncError, ValueError):
            return 0

    async def _blob_bytes(self, repo_dir: Path, sha: str) -> bytes:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(repo_dir), "cat-file", "-p", sha,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise GitSyncError(f"cat-file failed: {err.decode('utf-8','replace')[:200]}")
        return out

    # ----- change-set computation -------------------------------------------

    def _supported(self, path: str) -> bool:
        ext = Path(path).suffix.lower()
        return ext in self.processor.RAW_TEXT_EXTENSIONS or ext in _GIT_DOC_EXTENSIONS

    def _passes_globs(self, path: str, spec_inc, spec_exc) -> bool:
        if spec_exc is not None and spec_exc.match_file(path):
            return False
        if spec_inc is not None and not spec_inc.match_file(path):
            return False
        return True

    def _build_specs(self, include_globs, exclude_globs):
        try:
            import pathspec
        except ImportError:
            logger.warning("pathspec not installed — glob filters ignored")
            return None, None
        inc = pathspec.PathSpec.from_lines("gitwildmatch", include_globs) if include_globs else None
        exc = pathspec.PathSpec.from_lines("gitwildmatch", exclude_globs) if exclude_globs else None
        return inc, exc

    async def _list_tree(self, repo_dir: Path, sha: str) -> list[tuple[str, str, int]]:
        """Return [(path, blob_sha, size)] for every blob in the tree at `sha`."""
        out = await self._git(["-C", str(repo_dir), "ls-tree", "-r", "-l", "-z", sha])
        items = []
        for entry in out.split("\0"):
            if not entry.strip():
                continue
            meta, _, path = entry.partition("\t")
            parts = meta.split()
            if len(parts) < 4 or parts[1] != "blob":
                continue
            blob_sha = parts[2]
            size = int(parts[3]) if parts[3].isdigit() else 0
            items.append((path, blob_sha, size))
        return items

    async def _diff_ops(self, repo_dir: Path, old: str, new: str) -> list[dict]:
        """Parse `git diff --name-status -M -z old new` into operation dicts."""
        out = await self._git(["-C", str(repo_dir), "diff", "--name-status", "-M", "-z", old, new])
        tokens = out.split("\0")
        ops, i = [], 0
        while i < len(tokens):
            status = tokens[i].strip()
            if not status:
                i += 1
                continue
            code = status[0]
            if code in ("R", "C"):
                old_path, new_path = tokens[i + 1], tokens[i + 2]
                similarity = int(status[1:]) if status[1:].isdigit() else 100
                ops.append({"status": "R", "path": new_path, "old_path": old_path,
                            "modified": similarity < 100})
                i += 3
            else:
                path = tokens[i + 1]
                ops.append({"status": code, "path": path})
                i += 2
        return ops

    # ----- public entry point ------------------------------------------------

    async def sync_connection(self, connection_id: str, task_id: Optional[str] = None,
                              progress=None) -> dict:
        """Sync one connection. `progress(current, total, message)` is optional."""
        conn = self.neo4j.get_git_connection(connection_id)
        if not conn:
            raise GitSyncError(f"Connection {connection_id} not found")

        token = conn["pat"]
        vendor = conn["vendor"]
        owner, name = conn["repo_owner"], conn["repo_name"]
        branch = conn.get("branch") or conn.get("default_branch") or "main"
        base_url = conn.get("base_url")
        collection_id = conn.get("collection_id")
        provider = get_provider(vendor, token, base_url)
        host = provider.host

        def report(cur, total, msg):
            if progress:
                try:
                    progress(cur, total, msg)
                except Exception:
                    pass

        report(0, 100, "Cloning repository...")
        repo_dir = self._repo_dir(connection_id)
        clone_url = provider.authenticated_clone_url(owner, name)
        new_head = await self._clone_or_fetch(connection_id, clone_url, branch, host, token)

        # Size guard
        max_repo = int(self.settings.git_max_repo_size_mb or 0)
        if max_repo > 0:
            total_bytes = sum(f.stat().st_size for f in repo_dir.rglob("*") if f.is_file())
            if total_bytes > max_repo * 1024 * 1024:
                raise GitSyncError(f"Repository exceeds {max_repo} MB size limit")

        old_head = conn.get("last_synced_sha")
        inc, exc = self._build_specs(conn.get("include_globs"), conn.get("exclude_globs"))
        max_file = int(self.settings.git_sync_max_file_size_mb or 0) * 1024 * 1024

        stats = {"created": 0, "modified": 0, "orphaned": 0, "renamed": 0, "skipped": 0, "failed": 0}
        touched_doc_ids: list[str] = []
        commit_sha = new_head

        # Decide diff vs full-tree reconcile
        use_diff = bool(old_head) and await self._has_commit(repo_dir, old_head) \
            and await self._is_ancestor(repo_dir, old_head, new_head)

        report(10, 100, "Computing changes...")
        if use_diff:
            ops = await self._diff_ops(repo_dir, old_head, new_head)
            await self._apply_diff_ops(connection_id, repo_dir, ops, inc, exc, max_file,
                                       collection_id, commit_sha, stats, touched_doc_ids, report)
        else:
            await self._apply_fulltree(connection_id, repo_dir, new_head, inc, exc, max_file,
                                       collection_id, commit_sha, stats, touched_doc_ids, report)

        # Wiki
        if conn.get("wiki_enabled"):
            report(60, 100, "Syncing wiki...")
            try:
                await self._sync_wiki(connection_id, provider, owner, name, host, token,
                                      collection_id, commit_sha, stats, touched_doc_ids)
            except Exception as e:
                logger.warning(f"wiki sync failed for {connection_id}: {provider._scrub(str(e)) if hasattr(provider,'_scrub') else e}")

        # Process newly-pending (created + modified + wiki) documents
        changed = stats["created"] + stats["modified"] + stats["renamed"]
        if touched_doc_ids:
            report(70, 100, f"Ingesting {len(touched_doc_ids)} document(s)...")
            await self.processor.process_pending_documents(
                progress_callback=lambda c, t, m: report(70 + int(25 * c / max(t, 1)), 100,
                                                          f"Ingesting: {m}")
            )

        # Bump graph staleness so the UI flags re-extraction (only if something changed)
        if changed or stats["orphaned"]:
            self.neo4j.set_meta("last_relationship_analysis_at", _STALE_SENTINEL)
            self.neo4j.set_meta("last_community_detection_at", _STALE_SENTINEL)

        # Advance the synced SHA only when no hard failures occurred
        now = datetime.now(timezone.utc).isoformat()
        sync_state = {"last_synced_at": now,
                      "sync_status": "success" if stats["failed"] == 0 else "partial"}
        if stats["failed"] == 0:
            sync_state["last_synced_sha"] = new_head
        interval = int(conn.get("sync_interval_minutes") or 0)
        if interval > 0:
            from datetime import timedelta
            sync_state["next_sync_due"] = (
                datetime.now(timezone.utc) + timedelta(minutes=interval)
            ).isoformat()
        self.neo4j.set_git_connection_sync_state(connection_id, **sync_state)

        report(100, 100, "Sync complete")
        stats["new_sha"] = new_head
        stats["mode"] = "diff" if use_diff else "fulltree"
        return stats

    # ----- op application ----------------------------------------------------

    async def _ingest_file(self, connection_id, repo_dir, path, blob_sha, size, max_file,
                           collection_id, commit_sha, stats, touched, *, force_modify=False):
        """Create-or-update a single repo file as a document. Idempotent (A falls back to M)."""
        if max_file and size > max_file:
            stats["skipped"] += 1
            return
        try:
            content = await self._blob_bytes(repo_dir, blob_sha)
        except GitSyncError:
            stats["failed"] += 1
            return
        dest = self._content_path(connection_id, path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)

        existing = self.neo4j.find_git_document(connection_id, path)
        provenance = {
            "git_connection_id": connection_id,
            "git_path": path,
            "git_blob_sha": blob_sha,
            "git_commit_sha": commit_sha,
            "git_sync_status": "synced",
        }
        if existing or force_modify:
            doc_id = existing["id"]
            self.neo4j.delete_document_chunks(doc_id)  # clears chunks + rels (global fix)
            self.processor.neo4j.update_document_status(doc_id, _pending_status(), 0)
            self.neo4j.set_document_git_provenance(
                doc_id, blob_sha=blob_sha, commit_sha=commit_sha, sync_status="synced")
            touched.append(doc_id)
            if not force_modify:
                stats["modified"] += 1
        else:
            doc_id = await self.processor.store_file_only(
                str(dest), path, len(content), collection_id,
                source=f"git:{connection_id}", git_provenance=provenance,
            )
            touched.append(doc_id)
            stats["created"] += 1

    async def _apply_diff_ops(self, connection_id, repo_dir, ops, inc, exc, max_file,
                              collection_id, commit_sha, stats, touched, report):
        total = len(ops) or 1
        for idx, op in enumerate(ops):
            path = op["path"]
            report(10 + int(45 * idx / total), 100, f"Applying changes ({idx+1}/{total})")
            if op["status"] in ("A", "M", "R") and not (self._supported(path) and self._passes_globs(path, inc, exc)):
                # If a file became excluded/unsupported, drop any prior doc.
                if op["status"] == "M":
                    prior = self.neo4j.find_git_document(connection_id, path)
                    if prior:
                        self.neo4j.mark_git_document_orphaned(prior["id"])
                        stats["orphaned"] += 1
                continue
            try:
                if op["status"] == "D":
                    doc = self.neo4j.find_git_document(connection_id, path)
                    if doc:
                        self.neo4j.mark_git_document_orphaned(doc["id"])
                        stats["orphaned"] += 1
                elif op["status"] == "R":
                    old_doc = self.neo4j.find_git_document(connection_id, op["old_path"])
                    if old_doc:
                        self.neo4j.remap_git_document(old_doc["id"], path, path)
                        stats["renamed"] += 1
                    if op.get("modified") or not old_doc:
                        blob_sha = (await self._git(["-C", str(repo_dir), "rev-parse", f"{commit_sha}:{path}"])).strip()
                        size = await self._blob_size(repo_dir, blob_sha)
                        await self._ingest_file(connection_id, repo_dir, path, blob_sha, size,
                                                max_file, collection_id, commit_sha, stats, touched,
                                                force_modify=bool(old_doc))
                else:  # A or M
                    blob_sha = (await self._git(["-C", str(repo_dir), "rev-parse", f"{commit_sha}:{path}"])).strip()
                    size = await self._blob_size(repo_dir, blob_sha)
                    await self._ingest_file(connection_id, repo_dir, path, blob_sha, size,
                                            max_file, collection_id, commit_sha, stats, touched)
            except Exception as e:
                logger.warning(f"git sync op {op['status']} {path} failed: {e}")
                stats["failed"] += 1

    async def _apply_fulltree(self, connection_id, repo_dir, new_head, inc, exc, max_file,
                              collection_id, commit_sha, stats, touched, report):
        """Reconcile by comparing the full tree to stored docs (force-push / glob-change safe)."""
        tree = await self._list_tree(repo_dir, new_head)
        current_paths = set()
        candidates = [(p, s, z) for (p, s, z) in tree
                      if self._supported(p) and self._passes_globs(p, inc, exc)]
        total = len(candidates) or 1
        for idx, (path, blob_sha, size) in enumerate(candidates):
            current_paths.add(path)
            report(10 + int(45 * idx / total), 100, f"Reconciling ({idx+1}/{total})")
            existing = self.neo4j.find_git_document(connection_id, path)
            if existing and existing.get("git_blob_sha") == blob_sha \
                    and existing.get("git_sync_status") != "orphaned":
                continue  # unchanged
            try:
                await self._ingest_file(connection_id, repo_dir, path, blob_sha, size,
                                        max_file, collection_id, commit_sha, stats, touched)
            except Exception as e:
                logger.warning(f"git fulltree ingest {path} failed: {e}")
                stats["failed"] += 1
        # Deletes: stored docs whose path no longer exists in the tree
        for doc in self.neo4j.list_documents_for_git_connection(connection_id):
            gp = doc.get("git_path")
            if gp and gp not in current_paths and not (gp.startswith("wiki/")):
                self.neo4j.mark_git_document_orphaned(doc["id"])
                stats["orphaned"] += 1

    async def _sync_wiki(self, connection_id, provider, owner, name, host, token,
                         collection_id, commit_sha, stats, touched):
        """Sync wiki pages. GitHub clones repo.wiki.git; GitLab/Gitea use the wiki API."""
        wiki_clone = provider.wiki_clone_url(owner, name)
        if wiki_clone:
            # GitHub: clone the wiki repo and reconcile its markdown like the main tree.
            wiki_dir = self._conn_dir(connection_id) / "wiki"
            tls = self._tls_git_config(host)
            if not (wiki_dir / ".git").exists():
                await self._git(tls + ["clone", "--depth", "1", wiki_clone, str(wiki_dir)], token=token)
            else:
                await self._git(tls + ["-C", str(wiki_dir), "fetch", wiki_clone], token=token)
                await self._git(["-C", str(wiki_dir), "reset", "--hard", "FETCH_HEAD"], token=token)
            for md in wiki_dir.rglob("*.md"):
                rel = "wiki/" + str(md.relative_to(wiki_dir))
                content = md.read_bytes()
                await self._ingest_raw(connection_id, rel, content, collection_id, commit_sha,
                                       stats, touched, blob_sha=f"wiki:{md.stat().st_mtime_ns}")
        else:
            pages = await provider.list_wiki_pages(owner, name)
            for page in (pages or []):
                rel = "wiki/" + page.slug
                content = page.content.encode("utf-8")
                import hashlib
                sha = "wiki:" + hashlib.sha256(content).hexdigest()[:16]
                existing = self.neo4j.find_git_document(connection_id, rel)
                if existing and existing.get("git_blob_sha") == sha:
                    continue
                await self._ingest_raw(connection_id, rel, content, collection_id, commit_sha,
                                       stats, touched, blob_sha=sha)

    async def _ingest_raw(self, connection_id, rel_path, content: bytes, collection_id,
                          commit_sha, stats, touched, *, blob_sha: str):
        """Ingest already-fetched content (wiki pages) as a markdown document."""
        dest = self._content_path(connection_id, rel_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.name.endswith(".md"):
            dest = dest.with_suffix(".md")
        dest.write_bytes(content)
        existing = self.neo4j.find_git_document(connection_id, rel_path)
        if existing:
            doc_id = existing["id"]
            self.neo4j.delete_document_chunks(doc_id)
            self.processor.neo4j.update_document_status(doc_id, _pending_status(), 0)
            self.neo4j.set_document_git_provenance(doc_id, blob_sha=blob_sha,
                                                   commit_sha=commit_sha, sync_status="synced")
            touched.append(doc_id)
            stats["modified"] += 1
        else:
            doc_id = await self.processor.store_file_only(
                str(dest), rel_path, len(content), collection_id,
                source=f"git_wiki:{connection_id}",
                git_provenance={
                    "git_connection_id": connection_id, "git_path": rel_path,
                    "git_blob_sha": blob_sha, "git_commit_sha": commit_sha,
                    "git_sync_status": "synced",
                },
            )
            touched.append(doc_id)
            stats["created"] += 1


def _pending_status():
    from ..models import ProcessingStatus
    return ProcessingStatus.PENDING


_git_connector_service: Optional[GitConnectorService] = None


def get_git_connector_service() -> GitConnectorService:
    global _git_connector_service
    if _git_connector_service is None:
        _git_connector_service = GitConnectorService()
    return _git_connector_service
