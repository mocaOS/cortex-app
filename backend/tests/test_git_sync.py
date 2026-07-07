"""Tests for the git connector's change-set engine against real temp repos.

These exercise the pure git plumbing (clone/diff/ls-tree parsing, glob + extension
filtering) without Neo4j. They require the `git` binary (present in the backend
image).
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from app.services.git_connector_service import GitConnectorService


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture
def repo(tmp_path):
    """A temp git repo with one commit, plus a helper to make more commits."""
    d = tmp_path / "repo"
    d.mkdir()
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "t@t.co")
    _git(d, "config", "user.name", "t")
    (d / "src").mkdir()
    (d / "src" / "a.py").write_text("print('a')\n")
    (d / "README.md").write_text("# hello\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "c1")
    return d


def _rev(d, ref="HEAD"):
    return subprocess.run(["git", "rev-parse", ref], cwd=d, check=True,
                          capture_output=True, text=True).stdout.strip()


def test_list_tree_parses_blobs(repo):
    svc = GitConnectorService()
    items = asyncio.run(svc._list_tree(repo, _rev(repo)))
    paths = {p for (p, _sha, _size) in items}
    assert paths == {"src/a.py", "README.md"}
    # every entry has a sha + non-negative size
    assert all(len(sha) >= 7 and size >= 0 for (_p, sha, size) in items)


def test_diff_ops_add_modify_delete(repo):
    old = _rev(repo)
    (repo / "src" / "a.py").write_text("print('a2')\n")     # modify
    (repo / "src" / "b.py").write_text("print('b')\n")      # add
    _git(repo, "rm", "-q", "README.md")                      # delete
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "c2")
    new = _rev(repo)

    svc = GitConnectorService()
    ops = asyncio.run(svc._diff_ops(repo, old, new))
    by_path = {o["path"]: o["status"] for o in ops}
    assert by_path["src/a.py"] == "M"
    assert by_path["src/b.py"] == "A"
    assert by_path["README.md"] == "D"


def test_diff_ops_detects_pure_rename(repo):
    old = _rev(repo)
    _git(repo, "mv", "src/a.py", "src/renamed.py")           # pure rename
    _git(repo, "commit", "-qm", "c2")
    new = _rev(repo)

    svc = GitConnectorService()
    ops = asyncio.run(svc._diff_ops(repo, old, new))
    renames = [o for o in ops if o["status"] == "R"]
    assert len(renames) == 1
    assert renames[0]["old_path"] == "src/a.py"
    assert renames[0]["path"] == "src/renamed.py"
    assert renames[0]["modified"] is False  # R100 → identical content


def test_is_ancestor_and_has_commit(repo):
    old = _rev(repo)
    (repo / "c.txt").write_text("c\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "c2")
    new = _rev(repo)
    svc = GitConnectorService()
    assert asyncio.run(svc._has_commit(repo, old)) is True
    assert asyncio.run(svc._has_commit(repo, "deadbeef" * 5)) is False
    assert asyncio.run(svc._is_ancestor(repo, old, new)) is True
    assert asyncio.run(svc._is_ancestor(repo, new, old)) is False


def test_supported_extensions_filter():
    svc = GitConnectorService()
    assert svc._supported("src/main.py") is True
    assert svc._supported("docs/guide.md") is True
    assert svc._supported("docs/spec.pdf") is True       # doc format → Docling
    assert svc._supported("report.docx") is True
    assert svc._supported("assets/logo.png") is False    # images excluded
    assert svc._supported("bin/tool") is False


def test_glob_filtering():
    svc = GitConnectorService()
    inc, exc = svc._build_specs(["src/**"], ["**/test_*.py"])
    assert svc._passes_globs("src/app/main.py", inc, exc) is True
    assert svc._passes_globs("docs/readme.md", inc, exc) is False     # not in include
    assert svc._passes_globs("src/app/test_main.py", inc, exc) is False  # excluded
    # no include spec → everything except excludes passes
    none_inc, exc2 = svc._build_specs([], ["*.lock"])
    assert svc._passes_globs("anything.py", none_inc, exc2) is True
    assert svc._passes_globs("yarn.lock", none_inc, exc2) is False


def test_scrub_fetch_head_removes_token(tmp_path):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    fetch_head = git_dir / "FETCH_HEAD"
    fetch_head.write_text(
        "abc123\t\tbranch 'main' of https://x-access-token:ghp_secret@github.com/o/r\n"
    )
    GitConnectorService._scrub_fetch_head(tmp_path, "ghp_secret")
    text = fetch_head.read_text()
    assert "ghp_secret" not in text
    assert "***" in text
    # no-ops: missing file / empty token don't raise
    GitConnectorService._scrub_fetch_head(tmp_path / "nope", "ghp_secret")
    GitConnectorService._scrub_fetch_head(tmp_path, "")


def test_wiki_sha_is_content_derived():
    from app.services.git_connector_service import _wiki_sha
    a, b = _wiki_sha(b"hello"), _wiki_sha(b"hello")
    assert a == b and a.startswith("wiki:")
    assert _wiki_sha(b"other") != a


def test_ingest_raw_skips_unchanged_content(monkeypatch):
    """An existing wiki doc with a matching content sha must not be re-ingested."""

    class FakeNeo4j:
        def __init__(self):
            self.calls = []

        def find_git_document(self, cid, path):
            self.calls.append("find")
            return {"id": "doc1", "git_blob_sha": "wiki:abcd", "git_sync_status": "synced"}

        def __getattr__(self, name):  # any write would betray a re-ingest
            raise AssertionError(f"unexpected neo4j call: {name}")

    fake = FakeNeo4j()
    monkeypatch.setattr(GitConnectorService, "neo4j", property(lambda self: fake))
    svc = GitConnectorService()
    stats = {"created": 0, "modified": 0}
    touched = []
    asyncio.run(svc._ingest_raw("conn1", "wiki/Home.md", b"x", None, "sha",
                                stats, touched, blob_sha="wiki:abcd"))
    assert fake.calls == ["find"]
    assert touched == [] and stats == {"created": 0, "modified": 0}


def test_git_subprocess_timeout(monkeypatch):
    """A hung git process is killed and surfaces as GitSyncError."""
    import app.services.git_connector_service as mod
    from app.services.git_connector_service import GitSyncError
    monkeypatch.setattr(mod, "_GIT_CMD_TIMEOUT", 0.05)

    class HungProc:
        killed = False

        async def communicate(self):
            await asyncio.sleep(10)

        def kill(self):
            self.killed = True

        async def wait(self):
            return -9

    proc = HungProc()
    with pytest.raises(GitSyncError, match="timed out"):
        asyncio.run(GitConnectorService._communicate(proc, "fetch"))
    assert proc.killed is True


def _backoff_fake(monkeypatch, conn):
    """Fake neo4j capturing the sync-state write; sync body always raises."""
    from app.services.git_connector_service import GitSyncError

    class FakeNeo4j:
        def __init__(self):
            self.state = None

        def get_git_connection(self, cid):
            return conn

        def set_git_connection_sync_state(self, cid, **state):
            self.state = state

    fake = FakeNeo4j()
    monkeypatch.setattr(GitConnectorService, "neo4j", property(lambda self: fake))
    svc = GitConnectorService()

    async def boom(*a, **k):
        raise GitSyncError("clone failed")

    monkeypatch.setattr(svc, "_sync_connection_inner", boom)
    return svc, fake, GitSyncError


def test_sync_failure_records_exponential_backoff(monkeypatch):
    """A failing sync must set sync_status=error and push next_sync_due out
    exponentially — otherwise the scheduler re-clones it every poll tick."""
    from datetime import datetime, timedelta, timezone

    conn = {"id": "c1", "sync_interval_minutes": 5, "consecutive_sync_failures": 2}
    svc, fake, GitSyncError = _backoff_fake(monkeypatch, conn)

    with pytest.raises(GitSyncError):
        asyncio.run(svc.sync_connection("c1"))

    assert fake.state["sync_status"] == "error"
    assert fake.state["consecutive_sync_failures"] == 3
    # third consecutive failure -> 5 * 2^2 = 20 minutes out
    due = datetime.fromisoformat(fake.state["next_sync_due"])
    delta = due - datetime.now(timezone.utc)
    assert timedelta(minutes=18) < delta <= timedelta(minutes=20, seconds=5)


def test_sync_failure_backoff_caps_at_24h(monkeypatch):
    from datetime import datetime, timedelta, timezone

    conn = {"id": "c1", "sync_interval_minutes": 5, "consecutive_sync_failures": 50}
    svc, fake, GitSyncError = _backoff_fake(monkeypatch, conn)

    with pytest.raises(GitSyncError):
        asyncio.run(svc.sync_connection("c1"))

    due = datetime.fromisoformat(fake.state["next_sync_due"])
    delta = due - datetime.now(timezone.utc)
    assert delta <= timedelta(hours=24, seconds=5)


def test_sync_failure_manual_connection_gets_no_due_date(monkeypatch):
    """interval=0 (manual-only) connections are never scheduler-driven, so a
    failure records the error without inventing a next_sync_due."""
    conn = {"id": "c1", "sync_interval_minutes": 0}
    svc, fake, GitSyncError = _backoff_fake(monkeypatch, conn)

    with pytest.raises(GitSyncError):
        asyncio.run(svc.sync_connection("c1"))

    assert fake.state["sync_status"] == "error"
    assert fake.state["consecutive_sync_failures"] == 1
    assert "next_sync_due" not in fake.state
