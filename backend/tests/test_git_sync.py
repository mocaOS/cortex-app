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
