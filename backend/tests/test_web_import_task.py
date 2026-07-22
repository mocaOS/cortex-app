"""Behavioural test for the web-import background runner (`_run_web_import_task`).

Verifies the stage-then-hand-off contract: the import task COMPLETES as soon as
the crawled pages are staged (so the "Import complete" popup fires when the
documents are *added*), it does NOT run the extract/embed pass inline, and it
hands that pass to a separate `batch_processing` task (watchable/resumable on
the Documents page). Drives the real function from `app.main` with mocks — no
network, no Neo4j, no crawl4ai.
"""

import os

import pytest

from app import main
from app.services import crawl_client


def _patch_common(monkeypatch, tmp_path, crawl_impl):
    """Wire up fakes shared by the tests. Returns captured-state dicts."""
    staged = []

    class FakeProcessor:
        async def store_file_only(self, file_path, filename, file_size, collection_id, source=None):
            staged.append(
                {
                    "file_path": file_path,
                    "filename": filename,
                    "collection_id": collection_id,
                    "source": source,
                }
            )
            return "doc-" + filename

        async def process_pending_documents(self, *a, **k):  # pragma: no cover
            raise AssertionError(
                "process_pending_documents must NOT run inside the import task — "
                "processing is handed to a separate batch_processing task"
            )

    monkeypatch.setattr(main, "get_document_processor", lambda: FakeProcessor())
    monkeypatch.setattr(crawl_client, "crawl_markdown", crawl_impl)

    settings = main.get_settings()
    monkeypatch.setattr(settings, "custom_inputs_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(settings, "crawl_concurrency", 5, raising=False)
    monkeypatch.setattr(settings, "batch_processing_concurrency", 4, raising=False)

    created = []

    class FakeTask:
        def __init__(self, task_type):
            self.task_type = task_type
            self.task_id = f"task-{task_type}-{len(created)}"
            self.message = ""

    def fake_create_task(task_type, resume_context=None):
        t = FakeTask(task_type)
        created.append({"task": t, "resume_context": resume_context})
        return t

    monkeypatch.setattr(main, "create_task", fake_create_task)

    completed, failed, spawned = {}, {}, []
    monkeypatch.setattr(main, "update_task_progress", lambda *a, **k: None)
    monkeypatch.setattr(main, "complete_task", lambda tid, result: completed.update(tid=tid, result=result))
    monkeypatch.setattr(main, "fail_task", lambda tid, msg: failed.update(tid=tid, msg=msg))
    # Return a plain sentinel (not a coroutine) so nothing is left un-awaited.
    monkeypatch.setattr(
        main,
        "_run_batch_processing_task",
        lambda task_id, concurrency, chain=None: ("BATCH", task_id, concurrency, chain),
    )
    monkeypatch.setattr(main, "_spawn_chain_task", lambda x: spawned.append(x))

    return {"staged": staged, "created": created, "completed": completed, "failed": failed, "spawned": spawned}


@pytest.mark.asyncio
async def test_aggregates_one_domain_into_a_single_document(monkeypatch, tmp_path):
    async def crawl(url, content_filter=None, query=None):
        # Distinct body per page so we can prove both made it into the one doc.
        return {"url": url, "title": f"Page {url[-1]}", "markdown": f"body-of-{url[-1]}"}

    cap = _patch_common(monkeypatch, tmp_path, crawl)

    await main._run_web_import_task(
        task_id="web-1",
        urls=["https://x.com/about", "https://x.com/a", "https://x.com/b"],
        collection_id="col-1",
        content_filter="fit",
        query=None,
    )

    # Three pages on one domain → ONE aggregated PENDING document.
    assert len(cap["staged"]) == 1
    doc = cap["staged"][0]
    assert doc["collection_id"] == "col-1"
    assert doc["source"] == "crawl:x.com"
    # Named/titled by the DOMAIN, never a page title (regression: an homepage
    # whose crawl title fell back to "o.html" produced "o-html.md").
    assert doc["filename"] == "x.com.md"
    assert len([f for f in os.listdir(tmp_path) if f.endswith(".md")]) == 1

    # The single file is titled by the domain and contains every page's body +
    # per-page source lines.
    with open(doc["file_path"], encoding="utf-8") as f:
        content = f.read()
    assert content.startswith("# x.com\n")
    assert "body-of-t" in content  # /about
    assert "body-of-a" in content and "body-of-b" in content
    assert content.count("> Source: https://x.com/") == 3

    # The import task is COMPLETE (popup fires) — not failed — and reports pages
    # vs documents.
    assert cap["failed"] == {}
    assert cap["completed"]["tid"] == "web-1"
    result = cap["completed"]["result"]
    assert result["imported"] == 3 and result["documents"] == 1
    assert result["total"] == 3 and result["failed"] == 0
    assert result["processing"]["status"] == "queued"
    # succeeded[] carries no raw markdown (kept lean for the task result).
    assert all(set(s.keys()) == {"url", "title"} for s in result["succeeded"])

    # Processing handed to a separate batch_processing task.
    batch = [c for c in cap["created"] if c["task"].task_type == "batch_processing"]
    assert len(batch) == 1
    batch_task_id = batch[0]["task"].task_id
    assert result["processing"]["task_id"] == batch_task_id
    assert cap["spawned"] == [("BATCH", batch_task_id, 4, None)]


@pytest.mark.asyncio
async def test_groups_multiple_domains_into_one_document_each(monkeypatch, tmp_path):
    async def crawl(url, content_filter=None, query=None):
        return {"url": url, "title": "T", "markdown": "b"}

    cap = _patch_common(monkeypatch, tmp_path, crawl)

    await main._run_web_import_task(
        task_id="web-3",
        urls=["https://a.com/1", "https://a.com/2", "https://b.com/1"],
        collection_id=None,
        content_filter="fit",
        query=None,
    )

    # Two domains → two documents (a.com aggregates 2 pages, b.com 1 page).
    assert len(cap["staged"]) == 2
    assert {s["source"] for s in cap["staged"]} == {"crawl:a.com", "crawl:b.com"}
    result = cap["completed"]["result"]
    assert result["imported"] == 3 and result["documents"] == 2 and result["failed"] == 0


@pytest.mark.asyncio
async def test_all_urls_fail_marks_task_failed(monkeypatch, tmp_path):
    async def crawl(url, content_filter=None, query=None):
        raise crawl_client.CrawlUnavailableError("boom")

    cap = _patch_common(monkeypatch, tmp_path, crawl)

    await main._run_web_import_task(
        task_id="web-2",
        urls=["https://x.com/a"],
        collection_id=None,
        content_filter="fit",
        query=None,
    )

    assert cap["failed"]["tid"] == "web-2"
    assert cap["completed"] == {}
    assert cap["spawned"] == []  # no processing hand-off when nothing staged
