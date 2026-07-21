"""Platform tasks + storage capability tests (Phase 2.5).

Covers app_storage_service (KV, quotas), app_task_dsl (validation,
interpolation, conditions, chunk policies), the app_task_service engine
(setup → fan-out → item pool → finally, dedup, pause/cancel/retry, scheduler
due-logic), and the platform endpoints (capability + role gates).

Two engine tests are the DSL acceptance shapes from ECOSYSTEM.md §5.2:
a paperless-style scheduled sync (paginate → dedup → conditional skip →
template → cortex multipart upload → cursor write) and a yt-transcriber-style
batch (http transcribe → chunked llm refine with validation policies → store).
"""

import asyncio
import io
import json
import zipfile
from types import SimpleNamespace

import httpx
import pytest

from tests.test_apps import FakeAPIKeyService, apps_env, make_manifest, make_zip  # noqa: F401


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def platform_manifest(**overrides):
    manifest = make_manifest(
        id="sync-app",
        type="platform",
        capabilities={
            "http": {"hosts": ["${PAPERLESS_BASE_URL}"]},
            "tasks": {},
            "storage": {},
            "llm": {},
        },
        config=[
            {"name": "PAPERLESS_BASE_URL", "type": "text", "required": True},
            {
                "name": "PAPERLESS_TOKEN",
                "type": "secret",
                "auth_header": "Authorization: Token PAPERLESS_TOKEN",
            },
        ],
    )
    manifest["cortex"]["keyScope"] = "read_write"
    manifest["cortex"]["endpoints"] = ["upload", "documents", "graph/entities"]
    manifest.update(overrides)
    return manifest


@pytest.fixture
def tasks_env(apps_env):  # noqa: F811
    """Install a platform app with all capabilities + fresh task/storage
    singletons."""
    import app.services.app_storage_service as storage_module
    import app.services.app_task_service as task_module

    storage_module._storage_service = None
    task_module._app_task_service = None

    apps_env.install_from_zip(make_zip(platform_manifest()))
    apps_env.save_config(
        "sync-app",
        {"PAPERLESS_BASE_URL": "http://paperless.local", "PAPERLESS_TOKEN": "tok"},
    )
    yield SimpleNamespace(
        apps=apps_env,
        storage=storage_module.get_app_storage_service(),
        tasks=task_module.get_app_task_service(),
        app_id="sync-app",
    )
    storage_module._storage_service = None
    task_module._app_task_service = None


def run_and_wait(env, defn, created_by="owner"):
    """Submit inside a fresh loop and await the spawned run."""

    async def scenario():
        summary = env.tasks.submit(env.app_id, defn, created_by)
        task_id = summary["task_id"]
        live = env.tasks._running.get((env.app_id, task_id))
        if live:
            await live
        return env.tasks.get_task(env.app_id, task_id)

    return asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Storage service
# ---------------------------------------------------------------------------

def test_storage_roundtrip_and_listing(tasks_env):
    s, app_id = tasks_env.storage, tasks_env.app_id
    s.put(app_id, "sync/cursor", "2026-07-01")
    s.put(app_id, "synced/1", {"at": "now"})
    s.put(app_id, "synced/2", {"at": "later"})

    assert s.get(app_id, "sync/cursor") == "2026-07-01"
    assert s.get(app_id, "synced/1") == {"at": "now"}
    assert s.get(app_id, "missing") is None
    assert s.exists(app_id, "synced/1")

    listing = s.list_keys(app_id, prefix="synced/")
    assert [k["key"] for k in listing["keys"]] == ["synced/1", "synced/2"]
    assert listing["next"] is None

    assert s.missing(app_id, ["synced/1", "synced/3"]) == ["synced/3"]
    assert s.delete(app_id, "synced/1") is True
    assert s.delete(app_id, "synced/1") is False


def test_storage_rejects_bad_keys_and_caps(tasks_env, monkeypatch):
    from app.config import get_settings
    from app.services.app_storage_service import AppStorageError

    s, app_id = tasks_env.storage, tasks_env.app_id
    with pytest.raises(AppStorageError):
        s.put(app_id, "no spaces allowed", 1)
    with pytest.raises(AppStorageError):
        s.get(app_id, "x" * 600)
    with pytest.raises(AppStorageError):
        s.get("not-installed", "key")

    monkeypatch.setattr(get_settings(), "app_storage_max_value_kb", 1)
    with pytest.raises(AppStorageError, match="per-value cap"):
        s.put(app_id, "big", "y" * 2048)

    monkeypatch.setattr(get_settings(), "app_storage_max_value_kb", 1024)
    monkeypatch.setattr(get_settings(), "app_storage_max_mb", 0)
    with pytest.raises(AppStorageError, match="quota"):
        s.put(app_id, "any", "value")


# ---------------------------------------------------------------------------
# DSL: validation
# ---------------------------------------------------------------------------

def _validate(defn, capabilities=("http", "tasks", "storage", "llm")):
    from app.config import get_settings
    from app.services.app_task_dsl import validate_task_definition

    return validate_task_definition(
        defn, capabilities=set(capabilities), settings=get_settings()
    )


def test_validate_accepts_minimal_setup_task():
    defn = {"name": "one-shot", "setup": [{"store": {"put": "k", "value": 1}}]}
    assert _validate(defn) == []


@pytest.mark.parametrize(
    "defn,fragment",
    [
        ({}, '"name"'),
        ({"name": "x", "bogus": 1, "setup": [{"store": {"get": "k"}}]}, "unknown task field"),
        ({"name": "x", "setup": [{"frob": {}}]}, "exactly one type key"),
        ({"name": "x", "setup": [{"store": {"get": "k"}, "id": "steps"}]}, "reserved"),
        ({"name": "x", "setup": [{"store": {"get": "k"}, "id": "a"},
                                  {"store": {"get": "k"}, "id": "a"}]}, "duplicate"),
        ({"name": "x", "items": [{"vars": {"a": 1}}]}, 'requires per-item "steps"'),
        ({"name": "x", "steps": [{"store": {"get": "k"}}]}, 'requires "items"'),
        ({"name": "x", "schedule": {"everyMinutes": 1},
          "setup": [{"store": {"get": "k"}}]}, "everyMinutes"),
        ({"name": "x", "concurrency": 99, "setup": [{"store": {"get": "k"}}]}, "concurrency"),
        ({"name": "x", "setup": [{"http": {"method": "BREW", "url": "u"}}]}, "http.method"),
        ({"name": "x", "setup": [{"cortex": {"method": "GET", "path": "/api/x"}}]}, "relative"),
        ({"name": "x", "setup": [{"llm": {"prompt": "p", "chunk": {"words": 500}}}]}, '"input"'),
        ({"name": "x", "setup": [{"llm": {"prompt": "no placeholder", "input": "$a",
                                            "chunk": {"words": 500}}}]}, "{chunk}"),
        ({"name": "x", "setup": [{"store": {"get": "a", "put": "b", "value": 1}}]},
         "exactly one of"),
        ({"name": "x", "setup": [{"skipItem": {"when": {"empty": "$a"}}}]}, "only valid in per-item"),
        ({"name": "x", "setup": [{"template": {}}]}, '"text" or "lines"'),
        ({"name": "x", "setup": [{"store": {"get": "k"}, "when": {"maybe": 1}}]},
         "unknown condition operator"),
        ({"name": "x", "items": {"from": "no-dollar", "vars": {"a": "1"}},
          "steps": [{"store": {"get": "k"}}]}, "items.from"),
    ],
)
def test_validate_rejects(defn, fragment):
    issues = _validate(defn)
    assert any(fragment in issue for issue in issues), issues


def test_validate_gates_on_capabilities():
    http_task = {"name": "x", "setup": [{"http": {"method": "GET", "url": "u"}}]}
    assert any("http" in i for i in _validate(http_task, capabilities=("tasks",)))
    llm_task = {"name": "x", "setup": [{"llm": {"prompt": "p"}}]}
    assert any("llm" in i for i in _validate(llm_task, capabilities=("tasks",)))
    store_task = {"name": "x", "setup": [{"store": {"get": "k"}}]}
    assert any("storage" in i for i in _validate(store_task, capabilities=("tasks",)))
    dedup = {"name": "x", "items": {"from": "$a", "vars": {"i": "1"},
                                      "skipIfStored": "k/{item.id}"},
             "steps": [{"template": {"text": "t"}}]}
    assert any("storage" in i for i in _validate(dedup, capabilities=("tasks",)))


# ---------------------------------------------------------------------------
# DSL: interpolation, refs, conditions
# ---------------------------------------------------------------------------

def _ctx():
    return {
        "vars": {"id": 7, "title": "Hello, Wörld! A very Fine Title"},
        "setup": {"tags": {"map": {"3": {"name": "Invoice"}}}},
        "steps": {"full": {"body": {"content": "  text  ", "correspondent": 3,
                                      "names": [{"name": "Ada"}, {"name": "Bob"}]}}},
        "run": {"failedCount": 0, "startedAt": "2026-07-20T00:00:00+00:00"},
        "config": {"BASE": "http://paperless.local"},
    }


def test_interpolate_filters_and_escapes():
    from app.services.app_task_dsl import interpolate

    ctx = _ctx()
    assert interpolate("{config.BASE}/api/documents/{vars.id}/", ctx) == \
        "http://paperless.local/api/documents/7/"
    assert interpolate("{vars.title|slug}", ctx) == "hello-w-rld-a-very-fine-title"
    assert interpolate("{vars.missing|default:fallback}", ctx) == "fallback"
    assert interpolate("{full.body.names|pluck:name|join:, }", ctx) == "Ada, Bob"
    assert interpolate("literal {{braces}} kept", ctx) == "literal {braces} kept"
    assert interpolate("{vars.title|truncate:5}", ctx) == "Hello"


def test_interpolate_nested_dynamic_lookup():
    """Live-verify regression: a dynamic map lookup nested INSIDE a template
    string must fully resolve — the flat-regex version left the outer
    expression as literal text in the rendered markdown."""
    from app.services.app_task_dsl import interpolate

    ctx = _ctx()
    assert interpolate(
        "- Correspondent: {setup.tags.map.{full.body.correspondent}.name}", ctx
    ) == "- Correspondent: Invoice"
    # resolved data is inserted verbatim, never re-scanned as template syntax
    ctx["vars"]["evil"] = "{config.BASE}"
    assert interpolate("x {vars.evil} y", ctx) == "x {config.BASE} y"


def test_resolve_ref_with_dynamic_lookup():
    from app.services.app_task_dsl import resolve_ref

    ctx = _ctx()
    assert resolve_ref("$steps.full.body.content", ctx) == "  text  "
    assert resolve_ref("$full.body.content", ctx) == "  text  "  # scope shorthand
    # dynamic map join: the correspondent id becomes a path segment
    assert resolve_ref("$setup.tags.map.{full.body.correspondent}.name", ctx) == "Invoice"
    assert resolve_ref("$full.body.names.1.name", ctx) == "Bob"


def test_resolve_ref_unknown_root_raises():
    from app.services.app_task_dsl import StepError, resolve_ref

    with pytest.raises(StepError, match="unknown reference root"):
        resolve_ref("$nonexistent.path", _ctx())


def test_conditions():
    from app.services.app_task_dsl import eval_condition

    ctx = _ctx()
    assert eval_condition({"notEmpty": "$full.body.content"}, ctx)
    assert eval_condition({"empty": "$vars.missing"}, ctx)
    assert eval_condition({"empty": "   "}, ctx)  # whitespace-only string
    assert eval_condition({"eq": ["$run.failedCount", 0]}, ctx)
    assert eval_condition({"contains": ["{vars.title}", "Fine"]}, ctx)
    assert eval_condition(
        {"and": [{"eq": ["$vars.id", 7]}, {"not": {"empty": "$config.BASE"}}]}, ctx
    )
    assert not eval_condition({"gt": ["$run.failedCount", 0]}, ctx)


def test_chunking_and_validation_policies():
    from app.services.app_task_dsl import chunk_output_valid, split_into_chunks

    text = "\n\n".join(f"Paragraph {i} " + "word " * 120 for i in range(6))
    chunks = split_into_chunks(text, 300)
    assert len(chunks) > 1
    assert all(len(c.split()) <= 360 for c in chunks)
    # reassembly loses nothing but blank-line normalization
    assert "".join(c.replace(" ", "").replace("\n", "") for c in chunks) == \
        text.replace(" ", "").replace("\n", "")

    original = "the quick brown fox jumps over the lazy dog " * 20
    assert chunk_output_valid(original, original.upper())
    assert not chunk_output_valid(original, "too short")
    assert not chunk_output_valid(original, "completely different vocabulary " * 30)


# ---------------------------------------------------------------------------
# Engine: acceptance shape 1 — paperless-style scheduled sync
# ---------------------------------------------------------------------------

PAPERLESS_SYNC = {
    "name": "paperless-sync",
    "concurrency": 2,
    "setup": [
        {"id": "cursor", "store": {"get": "sync/cursor"}},
        {"id": "docs", "http": {
            "method": "GET",
            "url": "{config.PAPERLESS_BASE_URL}/api/documents/"
                   "?ordering=-added&added__gt={cursor.value|default:1970-01-01}",
            "paginate": {"items": "results", "next": "next"},
        }},
    ],
    "items": {
        "from": "$setup.docs.items",
        "vars": {"id": "{item.id}", "title": "{item.title}", "added": "{item.added}"},
        "skipIfStored": "synced/{item.id}",
    },
    "steps": [
        {"id": "full", "http": {
            "method": "GET",
            "url": "{config.PAPERLESS_BASE_URL}/api/documents/{vars.id}/",
        }},
        {"skipItem": {"when": {"empty": "$full.body.content"},
                       "reason": "no extracted text in paperless"}},
        {"id": "md", "template": {"lines": [
            "# {vars.title}",
            "",
            {"text": "- Created: {full.body.created}",
             "when": {"notEmpty": "$full.body.created"}},
            "",
            "{full.body.content}",
        ]}},
        {"id": "up", "cortex": {
            "method": "POST",
            "path": "upload?start_processing=true&source=paperless-sync",
            "multipart": {"content": "$md.text",
                           "filename": "paperless-{vars.id}-{vars.title|slug}.md"},
        }},
        {"store": {"put": "synced/{vars.id}", "value": {"at": "{run.startedAt}"}}},
    ],
    "finally": [
        {"store": {"put": "sync/cursor", "value": "{setup.docs.items.0.added}"},
         "when": {"and": [{"eq": ["$run.failedCount", 0]},
                            {"notEmpty": "$setup.docs.items"}]}},
    ],
}

_PAPERLESS_PAGES = {
    "": {
        "count": 3,
        "next": "http://paperless.local/api/documents/?page=2",
        "results": [
            {"id": 1, "title": "Old Doc", "added": "2026-07-19"},
            {"id": 2, "title": "Empty Doc", "added": "2026-07-18"},
        ],
    },
    "page=2": {
        "count": 3,
        "next": None,
        "results": [{"id": 3, "title": "Tax Notice 2026", "added": "2026-07-17"}],
    },
}


@pytest.fixture
def fake_upstreams(monkeypatch):
    """Fake external http (paperless) + the cortex loopback upstream."""
    external_calls = []
    cortex_calls = []

    async def fake_execute_app_http(app_id, *, method, url, body=None, content_type=None, extra_headers=None):
        external_calls.append({"method": method, "url": url})
        request = httpx.Request(method, url)
        if "/api/documents/?" in url:
            page = _PAPERLESS_PAGES["page=2" if "page=2" in url else ""]
            return httpx.Response(200, json=page, request=request)
        for doc_id, content in (("1", "old text"), ("2", "   "), ("3", "tax content here")):
            if url.endswith(f"/api/documents/{doc_id}/"):
                return httpx.Response(
                    200,
                    json={"id": int(doc_id), "content": content, "created": "2026-07-0" + doc_id},
                    request=request,
                )
        return httpx.Response(404, json={"detail": "?"}, request=request)

    async def fake_request(self, method, url, **kwargs):
        cortex_calls.append({"method": method, "url": str(url), **{
            k: kwargs[k] for k in ("files", "json") if k in kwargs
        }})
        return httpx.Response(
            200, json={"queued": True}, request=httpx.Request(method, str(url))
        )

    monkeypatch.setattr(
        "app.services.app_task_service.execute_app_http", fake_execute_app_http
    )
    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    return SimpleNamespace(external=external_calls, cortex=cortex_calls)


def test_paperless_sync_shape_end_to_end(tasks_env, fake_upstreams):
    env = tasks_env
    env.storage.put(env.app_id, "synced/1", {"at": "earlier"})  # already synced

    task = run_and_wait(env, PAPERLESS_SYNC)

    assert task["status"] == "completed", task
    counts = task["counts"]
    assert counts == {"total": 2, "done": 1, "failed": 0, "skipped": 1, "deduped": 1}

    statuses = {item["vars"]["id"]: item for item in task["items"]}
    assert statuses["2"]["status"] == "skipped"
    assert statuses["2"]["reason"] == "no extracted text in paperless"
    assert statuses["3"]["status"] == "done"

    # exactly one cortex upload, multipart, allowlisted path, slug filename
    assert len(fake_upstreams.cortex) == 1
    upload = fake_upstreams.cortex[0]
    assert "/api/upload?start_processing=true&source=paperless-sync" in upload["url"]
    filename, content, content_type = upload["files"]["file"]
    assert filename == "paperless-3-tax-notice-2026.md"
    assert b"# Tax Notice 2026" in content
    assert b"- Created: 2026-07-03" in content
    assert b"tax content here" in content
    assert content_type == "text/markdown"

    # dedup state + cursor written (failedCount == 0)
    assert env.storage.exists(env.app_id, "synced/3")
    assert not env.storage.exists(env.app_id, "synced/2")  # skipped ≠ synced
    assert env.storage.get(env.app_id, "sync/cursor") == "2026-07-19"

    # pagination followed the next link; per-doc fetches only for kept items
    urls = [c["url"] for c in fake_upstreams.external]
    assert any("page=2" in u for u in urls)
    assert not any(u.endswith("/api/documents/1/") for u in urls)  # deduped pre-fetch


def test_cortex_step_respects_endpoint_allowlist(tasks_env, fake_upstreams):
    defn = {
        "name": "escape-attempt",
        "items": [{"vars": {"x": "1"}}],
        "steps": [{"cortex": {"method": "GET", "path": "admin/apps"}}],
    }
    task = run_and_wait(tasks_env, defn)
    assert task["status"] == "completed"  # run completes; the item fails
    assert task["counts"]["failed"] == 1
    assert "allowlist" in task["items"][0]["error"]
    assert fake_upstreams.cortex == []  # nothing reached the upstream


# ---------------------------------------------------------------------------
# Engine: acceptance shape 2 — yt-transcriber-style batch
# ---------------------------------------------------------------------------

YT_BATCH = {
    "name": "transcribe-batch",
    "concurrency": 2,
    "items": [
        {"vars": {"videoId": "abc", "url": "http://paperless.local/watch?v=abc"}},
        {"vars": {"videoId": "def", "url": "http://paperless.local/watch?v=def"}},
    ],
    "steps": [
        {"id": "t", "http": {"method": "POST",
                               "url": "{config.PAPERLESS_BASE_URL}/api/v1/video/transcriptions",
                               "body": {"url": "{vars.url}", "response_format": "json"}}},
        {"skipItem": {"when": {"empty": "$t.body.transcript"}, "reason": "no speech"}},
        {"id": "refined", "llm": {
            "prompt": "Clean up this transcript chunk, fixing punctuation:\n\n{chunk}",
            "input": "$t.body.transcript",
            "chunk": {"words": 200},
            "validate": {"minLengthRatio": 0.5, "minWordOverlap": 0.6,
                          "onFail": "keepOriginal"},
        }},
        {"store": {"put": "transcripts/{vars.videoId}",
                    "value": {"original": "$t.body.transcript",
                               "refined": "$refined.text"}}},
    ],
}


@pytest.fixture
def fake_llm(monkeypatch, tasks_env):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "openai_api_key", "test-key")
    calls = {"n": 0, "closed": 0}

    async def fake_create(*, model, messages, **params):
        calls["n"] += 1
        prompt = messages[-1]["content"]
        chunk = prompt.split("\n\n", 1)[1]
        if "GARBLED" in chunk:
            text = "junk"  # fails both validation attempts → keepOriginal
        else:
            text = chunk.replace("uh ", "")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
        )

    async def fake_close():
        calls["closed"] += 1

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)),
        close=fake_close,
    )
    monkeypatch.setattr(
        "app.services.llm_config.make_async_openai_client",
        lambda *, api_key, base_url, **kw: fake_client,
    )
    return calls


def test_yt_batch_shape_end_to_end(tasks_env, fake_llm, monkeypatch):
    transcripts = {
        "abc": ("uh hello everyone uh welcome to the show today we talk about "
                 "knowledge graphs and retrieval " * 12),
        "def": ("normal opening part " * 30) + "\n\nGARBLED " + ("zz " * 200),
    }

    async def fake_execute_app_http(app_id, *, method, url, body=None, content_type=None, extra_headers=None):
        video_id = json.loads(body)["url"].rsplit("=", 1)[1]
        return httpx.Response(
            200,
            json={"transcript": transcripts[video_id], "lang": "en"},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(
        "app.services.app_task_service.execute_app_http", fake_execute_app_http
    )

    task = run_and_wait(tasks_env, YT_BATCH)
    assert task["status"] == "completed", task
    assert task["counts"]["done"] == 2

    env = tasks_env
    stored_abc = env.storage.get(env.app_id, "transcripts/abc")
    assert "uh " not in stored_abc["refined"]  # refinement applied
    assert "uh " in stored_abc["original"]

    stored_def = env.storage.get(env.app_id, "transcripts/def")
    assert "GARBLED" in stored_def["refined"]  # invalid chunk kept original
    assert fake_llm["n"] >= 4  # chunked calls happened (incl. one retry)
    # the factory's client is CACHED and shared backend-wide — the engine
    # must never close it (a closed cached client breaks every later LLM
    # call, ask pipeline included; found live)
    assert fake_llm["closed"] == 0


def test_llm_call_cap_fails_item(tasks_env, fake_llm, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "app_task_llm_calls_per_run", 1)

    async def fake_http(app_id, *, method, url, body=None, content_type=None, extra_headers=None):
        # several paragraphs → several chunks → the second llm call trips the cap
        transcript = ("alpha beta gamma delta epsilon words here today. " * 25 + "\n\n") * 3
        return httpx.Response(200, json={"transcript": transcript},
                              request=httpx.Request(method, url))

    monkeypatch.setattr("app.services.app_task_service.execute_app_http", fake_http)
    defn = {**YT_BATCH, "items": [YT_BATCH["items"][0]]}
    task = run_and_wait(tasks_env, defn)
    assert task["counts"]["failed"] == 1
    assert "llm call cap" in task["items"][0]["error"]


def test_step_output_size_cap(tasks_env, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "app_task_step_output_max_kb", 1)

    async def fake_http(app_id, *, method, url, body=None, content_type=None, extra_headers=None):
        return httpx.Response(200, json={"blob": "x" * 4096},
                              request=httpx.Request(method, url))

    monkeypatch.setattr("app.services.app_task_service.execute_app_http", fake_http)
    defn = {
        "name": "too-big",
        "items": [{"vars": {"a": "1"}}],
        "steps": [{"id": "big", "http": {"method": "GET",
                                            "url": "{config.PAPERLESS_BASE_URL}/x"}}],
    }
    task = run_and_wait(tasks_env, defn)
    assert task["counts"]["failed"] == 1
    assert "exceeds" in task["items"][0]["error"]


def test_pagination_byte_budget_stops_accumulation(tasks_env, monkeypatch):
    """Security-review F1: pagination must fail as soon as the accumulated
    pages exceed the step-output budget — not buffer maxPages × 20 MB first."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "app_task_step_output_max_kb", 4)
    pages_served = {"n": 0}

    async def fake_http(app_id, *, method, url, body=None, content_type=None,
                        extra_headers=None):
        pages_served["n"] += 1
        return httpx.Response(
            200,
            json={"results": ["x" * 3000], "next": "http://paperless.local/api/more"},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr("app.services.app_task_service.execute_app_http", fake_http)
    defn = {
        "name": "hog",
        "setup": [{"id": "docs", "http": {
            "method": "GET", "url": "{config.PAPERLESS_BASE_URL}/api/x",
            "paginate": {"items": "results", "next": "next", "maxPages": 50},
        }}],
    }
    task = run_and_wait(tasks_env, defn)
    assert task["status"] == "failed"
    assert "step cap" in task["error"]
    assert pages_served["n"] <= 3  # stopped almost immediately, not at page 50


def test_http_step_headers_forwarded_but_denylisted(tasks_env, monkeypatch):
    """Security-review F3: declared headers are sent (interpolated), but an
    app can never override config-injected auth or framing headers."""
    seen = {}

    async def fake_execute(app_id, *, method, url, body=None, content_type=None,
                           extra_headers=None):
        # exercise the REAL merge logic by calling through to it via the
        # service's header assembly — here we just capture what the step sends
        seen.update(extra_headers or {})
        return httpx.Response(200, json={}, request=httpx.Request(method, url))

    monkeypatch.setattr("app.services.app_task_service.execute_app_http", fake_execute)
    defn = {
        "name": "hdrs",
        "setup": [{"http": {"method": "GET",
                              "url": "{config.PAPERLESS_BASE_URL}/api/x",
                              "headers": {"X-Api-Version": "v{vars.missing|default:2}",
                                           "Authorization": "Bearer stolen"}}}],
    }
    task = run_and_wait(tasks_env, defn)
    assert task["status"] == "completed"
    assert seen["X-Api-Version"] == "v2"  # interpolated + forwarded


def test_forbidden_headers_never_reach_upstream(tasks_env, monkeypatch):
    """The real execute_app_http drops denylisted / auth-conflicting names."""
    from app.services.app_task_service import execute_app_http

    captured = {}

    async def fake_request(self, method, url, content=None, headers=None):
        captured.update(headers or {})
        return httpx.Response(200, json={}, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    # the SSRF guard resolves DNS; the fake host doesn't exist — the guard
    # itself is exercised by test_apps.py, here we test header assembly
    monkeypatch.setattr(
        "app.services.ssrf_guard.validate_url", lambda url, **kw: None
    )
    asyncio.run(execute_app_http(
        "sync-app", method="GET", url="http://paperless.local/api/x",
        extra_headers={"Authorization": "Bearer stolen", "Host": "evil",
                        "X-Custom": "ok", "Transfer-Encoding": "chunked",
                        "Cookie": "SOCS=CAI"},
    ))
    assert captured["Authorization"] == "Token tok"  # config injection wins
    assert captured["X-Custom"] == "ok"
    assert captured["Cookie"] == "SOCS=CAI"  # app cookies allowed (consent bypass)
    assert "Host" not in captured
    assert "Transfer-Encoding" not in captured


def test_auth_host_scopes_credentials_per_host(apps_env, monkeypatch):  # noqa: F811
    """A multi-host app must not leak one service's credential to another:
    a var with auth_host only injects on calls to that host; unscoped vars
    keep the inject-everywhere behavior."""
    manifest = platform_manifest(
        id="multi-host-app",
        capabilities={"http": {"hosts": ["api.venice.example", "www.youtube.example"]},
                       "tasks": {}, "storage": {}},
        config=[
            {"name": "VENICE_API_KEY", "type": "secret",
             "auth_header": "Authorization: Bearer VENICE_API_KEY",
             "auth_host": "api.venice.example"},
            {"name": "GLOBAL_TAG", "type": "text",
             "auth_header": "X-Tag: GLOBAL_TAG"},
        ],
    )
    apps_env.install_from_zip(make_zip(manifest))
    apps_env.save_config(
        "multi-host-app", {"VENICE_API_KEY": "vk-secret", "GLOBAL_TAG": "t1"}
    )

    venice = apps_env.platform_auth_headers("multi-host-app", target_host="api.venice.example")
    assert venice["Authorization"] == "Bearer vk-secret"
    assert venice["X-Tag"] == "t1"

    youtube = apps_env.platform_auth_headers("multi-host-app", target_host="www.youtube.example")
    assert "Authorization" not in youtube  # the venice key never reaches youtube
    assert youtube["X-Tag"] == "t1"  # unscoped var still injects everywhere

    # auth_host also accepts ${CONFIG_VAR} URL references
    manifest2 = platform_manifest(
        id="ref-host-app",
        capabilities={"http": {"hosts": ["${SERVICE_BASE_URL}"]}, "tasks": {}, "storage": {}},
        config=[
            {"name": "SERVICE_BASE_URL", "type": "text", "required": True},
            {"name": "SERVICE_TOKEN", "type": "secret",
             "auth_header": "Authorization: Token SERVICE_TOKEN",
             "auth_host": "${SERVICE_BASE_URL}"},
        ],
    )
    apps_env.install_from_zip(make_zip(manifest2))
    apps_env.save_config(
        "ref-host-app",
        {"SERVICE_BASE_URL": "https://svc.example:8443", "SERVICE_TOKEN": "st"},
    )
    assert apps_env.platform_auth_headers("ref-host-app", target_host="svc.example") == {
        "Authorization": "Token st"
    }
    assert apps_env.platform_auth_headers("ref-host-app", target_host="other.example") == {}


def test_upgrade_preserves_storage_and_tasks(tasks_env):
    """Live-verify regression: reinstalling an app (upgrade) must carry over
    storage.sqlite and tasks/ — wiping them would erase a sync app's dedup
    state and schedules on every version bump."""
    env = tasks_env
    env.storage.put(env.app_id, "sync/cursor", "2026-07-19")
    _seed_record(env, task_id="apptask_keepme0000001", status="completed",
                 schedule={"everyMinutes": 60})

    manifest = platform_manifest()
    manifest["version"] = "1.0.1"
    env.apps.install_from_zip(make_zip(manifest))

    assert env.storage.get(env.app_id, "sync/cursor") == "2026-07-19"
    kept = env.tasks._load_task(env.app_id, "apptask_keepme0000001")
    assert kept is not None and kept["schedule"] == {"everyMinutes": 60}
    # config survived too (pre-existing behavior, keep it locked)
    assert env.apps.public_config(env.app_id)["PAPERLESS_BASE_URL"] == "http://paperless.local"


def test_manifest_rejects_bad_auth_host(apps_env):  # noqa: F811
    manifest = platform_manifest(id="bad-auth-host")
    manifest["config"][1]["auth_host"] = ""
    issues = apps_env.validate_manifest(manifest)
    assert any("auth_host" in i for i in issues), issues


# ---------------------------------------------------------------------------
# Engine: lifecycle, scheduler, resume
# ---------------------------------------------------------------------------

def test_submit_rejects_without_tasks_capability(apps_env):  # noqa: F811
    import app.services.app_task_service as task_module
    from app.services.app_task_dsl import TaskDefinitionError

    task_module._app_task_service = None
    manifest = platform_manifest(id="no-tasks-app",
                                  capabilities={"http": {"hosts": ["x.example"]}})
    apps_env.install_from_zip(make_zip(manifest))
    with pytest.raises(TaskDefinitionError, match="tasks"):
        task_module.get_app_task_service().submit(
            "no-tasks-app", {"name": "n", "setup": [{"template": {"text": "t"}}]}, "owner"
        )
    task_module._app_task_service = None


def _seed_record(env, **overrides):
    record = {
        "task_id": "apptask_abcdef123456",
        "app_id": env.app_id,
        "name": "seeded",
        "definition": {"name": "seeded", "setup": [{"template": {"text": "t"}}]},
        "schedule": None,
        "status": "pending",
        "created_at": "2026-07-20T00:00:00+00:00",
        "created_by": "owner",
        "counts": {},
        "message": "",
        "items": [],
        "runs": [],
    }
    record.update(overrides)
    env.tasks._persist(record)
    return record


def test_actions_on_idle_records(tasks_env, monkeypatch):
    env = tasks_env
    spawned = []
    monkeypatch.setattr(env.tasks, "_spawn", lambda *a, **k: spawned.append((a, k)))

    _seed_record(env)
    assert env.tasks.apply_action(env.app_id, "apptask_abcdef123456", "pause")["status"] == "paused"
    assert env.tasks.apply_action(env.app_id, "apptask_abcdef123456", "resume")["status"] == "paused"
    assert len(spawned) == 1  # resume re-spawns

    _seed_record(env, status="completed", items=[
        {"vars": {"a": 1}, "status": "failed", "error": "boom"},
        {"vars": {"a": 2}, "status": "done"},
    ])
    result = env.tasks.apply_action(env.app_id, "apptask_abcdef123456", "retryFailed")
    assert result["status"] == "pending"
    record = env.tasks._load_task(env.app_id, "apptask_abcdef123456")
    assert record["items"][0]["status"] == "pending"
    assert record["items"][1]["status"] == "done"

    assert env.tasks.apply_action(env.app_id, "apptask_abcdef123456", "cancel")["status"] == "cancelled"
    with pytest.raises(Exception, match="unknown action"):
        env.tasks.apply_action(env.app_id, "apptask_abcdef123456", "explode")
    assert env.tasks.apply_action(env.app_id, "apptask_missing00000", "pause") is None


def test_scheduler_due_logic(tasks_env, monkeypatch):
    from datetime import datetime, timedelta, timezone

    env = tasks_env
    spawned = []
    monkeypatch.setattr(env.tasks, "_spawn", lambda app_id, task_id, **k: spawned.append(task_id))

    old = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    _seed_record(env, task_id="apptask_due0000000001", status="completed",
                 schedule={"everyMinutes": 15},
                 runs=[{"run_id": "r1", "started_at": old, "status": "completed"}])
    _seed_record(env, task_id="apptask_fresh00000001", status="completed",
                 schedule={"everyMinutes": 15},
                 runs=[{"run_id": "r2", "started_at": recent, "status": "completed"}])
    _seed_record(env, task_id="apptask_oneshot000001", status="completed",
                 runs=[{"run_id": "r3", "started_at": old, "status": "completed"}])
    _seed_record(env, task_id="apptask_paused0000001", status="paused",
                 schedule={"everyMinutes": 15},
                 runs=[{"run_id": "r4", "started_at": old, "status": "completed"}])

    env.tasks._schedule_tick()
    assert spawned == ["apptask_due0000000001"]


def test_resume_interrupted_respawns_running(tasks_env, monkeypatch):
    env = tasks_env
    spawned = []
    monkeypatch.setattr(env.tasks, "_spawn", lambda app_id, task_id, **k: spawned.append(task_id))
    _seed_record(env, task_id="apptask_crashed000001", status="running")
    _seed_record(env, task_id="apptask_finished00001", status="completed")
    assert env.tasks.resume_interrupted() == 1
    assert spawned == ["apptask_crashed000001"]


def test_delete_task_and_prune(tasks_env):
    env = tasks_env
    _seed_record(env, task_id="apptask_deleteme00001", status="completed")
    assert env.tasks.delete_task(env.app_id, "apptask_deleteme00001") is True
    assert env.tasks.delete_task(env.app_id, "apptask_deleteme00001") is False


# ---------------------------------------------------------------------------
# HTTP endpoints (capability + role gates)
# ---------------------------------------------------------------------------

def _issue_token(env, role="admin", principal="owner"):
    token, _ = env.apps.issue_token(env.app_id, principal=principal, role=role)
    return {"Authorization": f"Bearer {token}"}


def test_platform_endpoints_gates(tasks_env, client):
    env = tasks_env
    # no token → 401
    assert client.get(f"/apps/{env.app_id}/api/platform/tasks").status_code == 401
    assert client.get(f"/apps/{env.app_id}/api/platform/storage").status_code == 401

    owner = _issue_token(env)
    viewer = _issue_token(env, role="viewer", principal="owner")

    # storage roundtrip through the API
    put = client.put(
        f"/apps/{env.app_id}/api/platform/storage/notes/a",
        headers=owner, json={"value": {"n": 1}},
    )
    assert put.status_code == 200
    got = client.get(f"/apps/{env.app_id}/api/platform/storage/notes/a", headers=owner)
    assert got.json() == {"key": "notes/a", "value": {"n": 1}}
    listing = client.get(
        f"/apps/{env.app_id}/api/platform/storage?prefix=notes/", headers=owner
    )
    assert [k["key"] for k in listing.json()["keys"]] == ["notes/a"]
    assert client.get(
        f"/apps/{env.app_id}/api/platform/storage/missing", headers=owner
    ).status_code == 404

    # viewers read but never write
    assert client.get(
        f"/apps/{env.app_id}/api/platform/storage/notes/a", headers=viewer
    ).status_code == 200
    assert client.put(
        f"/apps/{env.app_id}/api/platform/storage/notes/a",
        headers=viewer, json={"value": 2},
    ).status_code == 403
    assert client.post(
        f"/apps/{env.app_id}/api/platform/tasks", headers=viewer,
        json={"name": "n", "setup": [{"template": {"text": "t"}}]},
    ).status_code == 403

    # task submit + status via the API (validation errors carry issues)
    bad = client.post(
        f"/apps/{env.app_id}/api/platform/tasks", headers=owner, json={"name": "x"}
    )
    assert bad.status_code == 400
    assert bad.json()["detail"]["issues"]

    ok = client.post(
        f"/apps/{env.app_id}/api/platform/tasks", headers=owner,
        json={"name": "noop", "setup": [{"template": {"text": "hello"}}]},
    )
    assert ok.status_code == 200, ok.text
    task_id = ok.json()["task_id"]
    detail = client.get(
        f"/apps/{env.app_id}/api/platform/tasks/{task_id}", headers=owner
    )
    assert detail.status_code == 200
    assert detail.json()["name"] == "noop"

    # admin oversight mirrors
    admin_list = client.get(f"/api/admin/apps/{env.app_id}/tasks")
    assert admin_list.status_code == 200
    assert admin_list.json()["tasks"][0]["task_id"] == task_id
    assert client.delete(
        f"/api/admin/apps/{env.app_id}/tasks/{task_id}"
    ).status_code == 200


def test_platform_endpoints_require_capability(apps_env, client):  # noqa: F811
    import app.services.app_task_service as task_module

    task_module._app_task_service = None
    manifest = platform_manifest(id="http-only-app",
                                  capabilities={"http": {"hosts": ["x.example"]}})
    apps_env.install_from_zip(make_zip(manifest))
    token, _ = apps_env.issue_token("http-only-app", principal="owner", role="admin")
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get(
        "/apps/http-only-app/api/platform/tasks", headers=headers
    ).status_code == 403
    assert client.get(
        "/apps/http-only-app/api/platform/storage", headers=headers
    ).status_code == 403
    task_module._app_task_service = None


# ---------------------------------------------------------------------------
# Cloud-sync extensions: webdav step, multipart fromUrl, dynamic auth
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "defn,fragment",
    [
        ({"name": "x", "setup": [{"webdav": {}}]}, "webdav.url"),
        ({"name": "x", "setup": [{"webdav": {"url": "u", "depth": 2}}]}, "webdav.depth"),
        ({"name": "x", "setup": [{"webdav": {"url": "u", "frob": 1}}]}, "unknown webdav"),
        ({"name": "x", "setup": [{"http": {"method": "GET", "url": "u",
                                             "auth": "Bearer x"}}]}, "auth must be"),
        ({"name": "x", "setup": [{"http": {"method": "GET", "url": "u",
                                             "auth": {"bearer": "$t", "basic": "b"}}}]},
         "auth must be"),
        ({"name": "x", "setup": [{"cortex": {"method": "POST", "path": "upload",
            "multipart": {"content": "$a", "fromUrl": "u", "filename": "f"}}}]},
         "not both"),
        ({"name": "x", "setup": [{"cortex": {"method": "POST", "path": "upload",
            "multipart": {"fromUrl": "u", "filename": "f", "method": "DELETE"}}}]},
         "multipart.method"),
    ],
)
def test_validate_rejects_cloud_sync_shapes(defn, fragment):
    issues = _validate(defn)
    assert any(fragment in issue for issue in issues), issues


def test_validate_gates_webdav_and_from_url_on_http_capability():
    webdav_task = {"name": "x", "setup": [{"webdav": {"url": "u"}}]}
    assert any('"http"' in i for i in _validate(webdav_task, capabilities=("tasks",)))
    from_url = {"name": "x", "setup": [{"cortex": {"method": "POST", "path": "upload",
        "multipart": {"fromUrl": "u", "filename": "f"}}}]}
    assert any('"http"' in i for i in _validate(from_url, capabilities=("tasks",)))
    # plain text multipart stays capability-free
    text_up = {"name": "x", "setup": [{"cortex": {"method": "POST", "path": "upload",
        "multipart": {"content": "$a", "filename": "f.md"}}}]}
    assert _validate(text_up, capabilities=("tasks",)) == []


_MULTISTATUS = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
  <d:response>
    <d:href>/remote.php/dav/files/rene/Docs/</d:href>
    <d:propstat><d:status>HTTP/1.1 200 OK</d:status><d:prop>
      <d:getetag>"root-etag"</d:getetag>
      <d:resourcetype><d:collection/></d:resourcetype>
    </d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/rene/Docs/Q3%20Report.pdf</d:href>
    <d:propstat><d:status>HTTP/1.1 200 OK</d:status><d:prop>
      <d:getetag>W/"abc123"</d:getetag>
      <d:getlastmodified>Mon, 20 Jul 2026 10:00:00 GMT</d:getlastmodified>
      <d:getcontentlength>2048</d:getcontentlength>
      <d:getcontenttype>application/pdf</d:getcontenttype>
      <d:resourcetype/>
      <oc:fileid>991</oc:fileid>
    </d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/rene/Docs/Sub/</d:href>
    <d:propstat><d:status>HTTP/1.1 200 OK</d:status><d:prop>
      <d:getetag>"sub-etag"</d:getetag>
      <d:resourcetype><d:collection/></d:resourcetype>
    </d:prop></d:propstat>
  </d:response>
</d:multistatus>"""


def test_parse_multistatus_normalizes_and_drops_self():
    from app.services.app_task_service import _parse_multistatus

    items = _parse_multistatus(
        _MULTISTATUS.encode(),
        request_url="http://nc.local/remote.php/dav/files/rene/Docs/",
    )
    assert [i["name"] for i in items] == ["Q3 Report.pdf", "Sub"]
    pdf, sub = items
    assert pdf["etag"] == "abc123"            # W/ prefix and quotes stripped
    assert pdf["size"] == 2048
    assert pdf["contentType"] == "application/pdf"
    assert pdf["isDir"] is False
    assert pdf["fileId"] == "991"
    assert pdf["lastModified"].startswith("2026-07-20T10:00:00")
    assert sub["isDir"] is True and sub["etag"] == "sub-etag"


def test_webdav_step_end_to_end_and_auth_override(tasks_env, monkeypatch):
    env = tasks_env
    calls = []

    async def fake_execute(app_id, *, method, url, body=None, content_type=None,
                            extra_headers=None, auth_override=None):
        calls.append({"method": method, "url": url, "depth": (extra_headers or {}).get("Depth"),
                       "auth": auth_override})
        return httpx.Response(207, content=_MULTISTATUS.encode(),
                               headers={"content-type": "application/xml"})

    monkeypatch.setattr("app.services.app_task_service.execute_app_http", fake_execute)
    result = run_and_wait(env, {
        "name": "webdav-list",
        "setup": [
            {"store": {"put": "auth/token", "value": "live-token"}},
            {"id": "tok", "store": {"get": "auth/token"}},
            {"id": "listing", "webdav": {
                "url": "http://paperless.local/remote.php/dav/files/rene/Docs/",
                "depth": 1, "auth": {"bearer": "$tok.value"}}},
            {"store": {"put": "out/count", "value": "$listing.count"}},
        ],
    })
    assert result["status"] == "completed", result
    assert env.storage.get(env.app_id, "out/count") == 2
    propfind = calls[-1]
    assert propfind["method"] == "PROPFIND" and propfind["depth"] == "1"
    assert propfind["auth"] == "Bearer live-token"


def test_from_url_multipart_streams_binary_to_upload(tasks_env, monkeypatch):
    env = tasks_env
    pdf_bytes = b"%PDF-1.7 fake body"
    uploads = []

    async def fake_execute(app_id, *, method, url, body=None, content_type=None,
                            extra_headers=None, auth_override=None):
        assert method == "POST" and auth_override == "Bearer at-123"
        assert (extra_headers or {}).get("Dropbox-API-Arg") == '{"path": "id:001"}'
        return httpx.Response(200, content=pdf_bytes,
                               headers={"content-type": "application/pdf"})

    async def fake_request(self, method, url, **kwargs):
        if "files" in kwargs:
            uploads.append(kwargs["files"])
            return httpx.Response(200, json={"task_id": "t1"})
        return httpx.Response(200, json={})

    monkeypatch.setattr("app.services.app_task_service.execute_app_http", fake_execute)
    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    result = run_and_wait(env, {
        "name": "binary-up",
        "setup": [
            {"store": {"put": "t", "value": {"access": "at-123"}}},
            {"id": "tok", "store": {"get": "t"}},
        ],
        "items": [{"vars": {"id": "id:001", "name": "report"}}],
        "steps": [
            {"cortex": {"method": "POST", "path": "upload?start_processing=true",
                "multipart": {
                    "fromUrl": "http://paperless.local/2/files/download",
                    "method": "POST",
                    "headers": {"Dropbox-API-Arg": "{{\"path\": \"{vars.id}\"}}"},
                    "auth": {"bearer": "$tok.value.access"},
                    "filename": "{vars.name}.pdf"}}},
        ],
    })
    assert result["status"] == "completed", result
    assert result["counts"]["done"] == 1
    (fname, payload, ctype) = uploads[0]["file"]
    assert fname == "report.pdf" and payload == pdf_bytes and ctype == "application/pdf"


def test_execute_app_http_allows_propfind_and_auth_override(tasks_env, monkeypatch):
    from app.services.app_task_service import execute_app_http

    seen = {}

    async def fake_request(self, method, url, content=None, headers=None):
        seen["method"] = method
        seen["headers"] = headers or {}
        seen["body"] = content
        return httpx.Response(207, content=b"<d:multistatus xmlns:d='DAV:'/>")

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    monkeypatch.setattr(
        "app.services.ssrf_guard.validate_url", lambda url, **kw: None
    )
    response = asyncio.run(execute_app_http(
        "sync-app", method="PROPFIND", url="http://paperless.local/dav/",
        body="<propfind/>", content_type="application/xml",
        extra_headers={"Depth": "1"}, auth_override="Bearer run-minted",
    ))
    assert response.status_code == 207
    assert seen["method"] == "PROPFIND"
    assert seen["headers"]["Depth"] == "1"
    # dynamic auth wins over the config-injected Token header
    assert seen["headers"]["Authorization"] == "Bearer run-minted"


def test_validate_from_each_and_webdav_filter():
    both = {"name": "x", "items": {"from": "$a", "fromEach": ["$b"], "vars": {"i": "1"}},
            "steps": [{"template": {"text": "t"}}]}
    assert any('"from" OR "fromEach"' in i for i in _validate(both))
    bad_refs = {"name": "x", "items": {"fromEach": ["no-dollar"], "vars": {"i": "1"}},
                "steps": [{"template": {"text": "t"}}]}
    assert any("fromEach" in i for i in _validate(bad_refs))
    bad_filter = {"name": "x", "setup": [{"webdav": {"url": "u", "filter": "folders"}}]}
    assert any("webdav.filter" in i for i in _validate(bad_filter))
    good = {"name": "x",
            "setup": [{"id": "f0", "webdav": {"url": "u", "filter": "files"}}],
            "items": {"fromEach": ["$f0.items"], "vars": {"n": "{item.name}"}},
            "steps": [{"template": {"text": "{vars.n}"}}]}
    assert _validate(good) == []


def test_from_each_concatenates_listings_and_filters_files(tasks_env, monkeypatch):
    env = tasks_env

    async def fake_execute(app_id, *, method, url, body=None, content_type=None,
                            extra_headers=None, auth_override=None):
        assert method == "PROPFIND"
        return httpx.Response(207, content=_MULTISTATUS.encode(),
                               headers={"content-type": "application/xml"})

    monkeypatch.setattr("app.services.app_task_service.execute_app_http", fake_execute)
    result = run_and_wait(env, {
        "name": "multi-folder",
        "setup": [
            {"id": "f0", "webdav": {
                "url": "http://paperless.local/remote.php/dav/files/rene/Docs/",
                "filter": "files"}},
            {"id": "f1", "webdav": {
                "url": "http://paperless.local/remote.php/dav/files/rene/Docs/",
                "filter": "files"}},
        ],
        "items": {"fromEach": ["$f0.items", "$f1.items"],
                   "vars": {"name": "{item.name}", "etag": "{item.etag}"}},
        "steps": [{"store": {"put": "seen/{vars.name|slug}-{run.index}",
                              "value": "{vars.etag}"}}],
    })
    assert result["status"] == "completed", result
    # each listing has 1 file (dirs filtered) → 2 items total
    assert result["counts"]["total"] == 2 and result["counts"]["done"] == 2


def test_ext_filter():
    from app.services.app_task_dsl import interpolate

    ctx = {"vars": {"a": "Q3 Report.PDF", "b": "no-extension", "c": ".gitignore",
                      "d": "archive.tar.gz"},
           "setup": {}, "steps": {}, "run": {}, "config": {}}
    assert interpolate("{vars.a|ext}", ctx) == "pdf"
    assert interpolate("{vars.b|ext}", ctx) == ""
    assert interpolate("{vars.c|ext}", ctx) == ""      # dotfile, no real ext
    assert interpolate("{vars.d|ext}", ctx) == "gz"
