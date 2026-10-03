#!/usr/bin/env python3
"""Standalone consumer-unit tests for qa/restore/backend-consumer.py.

Narrow scope: the consumer runner's own unit logic only (probe engine,
fixture expectations, delta allowlist classifier, chat receipt contract,
guard validations). No containers, no Neo4j, no HTTP server, no rehearsal or
oracle coordination — oracle cross-consistency is asserted from the oracle
module's constants only. Dependency-free (stdlib).

Run from the repository root:

    python3 qa/restore/test_backend_consumer_units.py
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bc = _load("backend_consumer", HERE / "backend-consumer.py")
oracle = _load("restore_oracle", HERE / "oracle.py")


class FixtureConsistency(unittest.TestCase):
    """The runner's frozen constants must match the oracle fixture source."""

    def test_document_metadata_matches_oracle(self):
        fx = bc.FX_DOCUMENTS[oracle.CITATION_SOURCE_ID]
        self.assertEqual(fx["collection_id"], oracle.SHARED_COLLECTION_ID)
        self.assertEqual(fx["filename"], "fixture-source.md")
        self.assertIn(oracle.CITATION_SOURCE_ID, bc.FX_ALL_DOCUMENTS)
        self.assertEqual(fx["file_path"],
                         f"uploads/{oracle.CITATION_SOURCE_ID}_"
                         f"fixture-source.md")
        self.assertIn(f"{oracle.CITATION_SOURCE_ID}-chunk-0",
                      bc.FX_FULLTEXT_CHUNK_IDS)

    def test_scope_negatives_match_oracle_fixture(self):
        self.assertIn("doc-crr-2", bc.FX_SCOPE_DENIED_DOCUMENTS)
        self.assertIn("col-crr-2", bc.FX_ALL_COLLECTIONS)
        self.assertNotIn("col-crr-2", bc.FX_SCOPED_VISIBLE_COLLECTIONS)

    def test_appstorage_rows_mirror_oracle_fixture(self):
        self.assertEqual(bc.FX_APP_STORAGE_TS, oracle.FIXED_TS)
        # Recorded rows of oracle.py gen-fixture (fixture app rehearsal-app);
        # every value must be a JSON document, as the KV store stores values
        # serialized.
        self.assertEqual(
            sorted(bc.FX_APP_STORAGE_ROWS),
            ["rehearsal/state", "sync/cursor", "transcripts/rehearsal-1"])
        for value in bc.FX_APP_STORAGE_ROWS.values():
            json.dumps(value)


class ProbeEngine(unittest.TestCase):
    """Wrong bodies / empty selections / non-raising checks must fail."""

    def _client(self, body=None, content=b"", ctype="application/json"):
        return bc._FakeClient({
            ("GET", "/x"): bc._FakeResponse(
                content=content, json_body=body, content_type=ctype)})

    def test_status_mismatch_fails(self):
        r = bc.probe(self._client({"ok": True}), "x", "GET", "/x",
                     expect_status=403)
        self.assertTrue(bc.probe_failed(r))

    def test_malformed_json_body_fails_check(self):
        r = bc.probe(self._client(content=b"not-json"), "x", "GET", "/x",
                     checks={"c": bc.docs_list_check(exact=["a"])})
        self.assertTrue(bc.probe_failed(r))

    def test_raising_check_fails_and_is_recorded(self):
        r = bc.probe(self._client({}), "x", "GET", "/x",
                     checks={"c": lambda b, resp: (_ for _ in ()).throw(
                         AssertionError("boom"))})
        self.assertEqual(r["checks_failed"], ["c: boom"])

    def test_right_scope_body_passes(self):
        body = {"documents": [{"id": "fx-source-0001"}], "total": 1}
        r = bc.probe(self._client(body), "x", "GET", "/x",
                     checks={"c": bc.docs_list_check(
                         absent=bc.FX_SCOPE_DENIED_DOCUMENTS,
                         exact=["fx-source-0001"])})
        self.assertFalse(bc.probe_failed(r), r["checks_failed"])

    def test_total_mismatch_fails(self):
        body = {"documents": [{"id": "fx-source-0001"}], "total": 99}
        r = bc.probe(self._client(body), "x", "GET", "/x",
                     checks={"c": bc.docs_list_check()})
        self.assertTrue(bc.probe_failed(r))

    def test_fixture_file_check_requires_exact_bytes_and_type(self):
        blob = b"fixture bytes"
        ok = bc.probe(self._client(
            content=blob, ctype="text/markdown"), "x", "GET", "/x",
            checks={"c": bc.fx_file_check(blob)})
        self.assertFalse(bc.probe_failed(ok), ok["checks_failed"])
        wrong_bytes = bc.probe(self._client(
            content=b"other", ctype="text/markdown"), "x", "GET", "/x",
            checks={"c": bc.fx_file_check(blob)})
        self.assertTrue(bc.probe_failed(wrong_bytes))
        wrong_type = bc.probe(self._client(
            content=blob, ctype="application/pdf"), "x", "GET", "/x",
            checks={"c": bc.fx_file_check(blob)})
        self.assertTrue(bc.probe_failed(wrong_type))


class DeltaClassifier(unittest.TestCase):
    """Out-of-scope deltas fail; the derived allowlist accepts exactly its set."""

    @staticmethod
    def _diff(nodes_b=(), nodes_a=(), rels_b=(), rels_a=(),
              cons_b=(), cons_a=(), idx_b=(), idx_a=()):
        return bc.diff_capture(
            {"nodes": list(nodes_b), "rels": list(rels_b),
             "constraints": list(cons_b), "indexes": list(idx_b)},
            {"nodes": list(nodes_a), "rels": list(rels_a),
             "constraints": list(cons_a), "indexes": list(idx_a)})

    def test_unexpected_index_and_constraint_flagged(self):
        d = self._diff(
            cons_a=[{"name": "mystery", "type": "UNIQUENESS"}],
            idx_a=[{"name": "rogue_index", "type": "FULLTEXT"}])
        out = bc.classify_delta(d, ["k1"])
        v = " ".join(out["violations"])
        self.assertIn("mystery", v)
        self.assertIn("rogue_index", v)

    def test_allowed_startup_schema_accepted(self):
        cons = [{"name": n, "type": "UNIQUENESS"}
                for n in sorted(bc.STARTUP_CONSTRAINT_NAMES)[:2]]
        idx = [{"name": n, "type": "FULLTEXT"}
               for n in sorted(bc.STARTUP_INDEX_NAMES)[:2]]
        d = self._diff(cons_a=cons, idx_a=idx)
        out = bc.classify_delta(d, ["k1"])
        self.assertEqual(out["violations"], [])
        self.assertEqual(sorted(out["accepted"]["schema_constraints_ensured"]),
                         sorted(c["name"] for c in cons))

    def test_schema_definition_drift_flagged(self):
        row = {"name": "collection_id", "type": "UNIQUENESS"}
        d = self._diff(cons_b=[row],
                       cons_a=[dict(row, type="NODE_KEY")])
        out = bc.classify_delta(d, ["k1"])
        v = " ".join(out["violations"])
        self.assertIn("definition changed", v)
        self.assertIn("collection_id", v)

    def test_usage_log_overflow_flagged(self):
        a = [{"labels": ["APIKeyUsageLog"], "key": f"k1@2026-10-0{i}",
              "props": {"key_id": "k1", "date": f"2026-10-0{i}"}}
             for i in range(1, 6)]
        d = self._diff(nodes_a=a)
        out = bc.classify_delta(d, ["k1"])
        v = " ".join(out["violations"])
        self.assertIn("too many APIKeyUsageLog", v)

    def test_foreign_key_usage_log_flagged(self):
        d = self._diff(nodes_a=[
            {"labels": ["APIKeyUsageLog"], "key": "other-key@2026-10-01",
             "props": {"key_id": "other-key", "date": "2026-10-01"}}])
        out = bc.classify_delta(d, ["k1"])
        v = " ".join(out["violations"])
        self.assertIn("unexpected node added", v)

    def test_usage_log_allowed_changed_fields(self):
        b = [{"labels": ["APIKeyUsageLog"], "key": "k1@2026-10-01",
              "props": {"key_id": "k1", "date": "2026-10-01",
                        "request_count": 1}}]
        a = [{"labels": ["APIKeyUsageLog"], "key": "k1@2026-10-01",
              "props": {"key_id": "k1", "date": "2026-10-01",
                        "request_count": 2, "ep_documents": 3}}]
        out = bc.classify_delta(self._diff(nodes_b=b, nodes_a=a), ["k1"])
        self.assertEqual(out["violations"], [])

    def test_apikey_disallowed_field_flagged(self):
        b = [{"labels": ["APIKey"], "key": "k1",
              "props": {"key_hash": "h"}}]
        a = [{"labels": ["APIKey"], "key": "k1",
              "props": {"key_hash": "h", "price_per_query": 9.9}}]
        out = bc.classify_delta(self._diff(nodes_b=b, nodes_a=a), ["k1"])
        v = " ".join(out["violations"])
        self.assertIn("price_per_query", v)

    def test_removed_index_flagged_as_schema_loss(self):
        idx = [{"name": "chunk_content", "type": "FULLTEXT"}]
        d = self._diff(idx_b=idx)
        out = bc.classify_delta(d, ["k1"])
        v = " ".join(out["violations"])
        self.assertIn("index removed", v)
        self.assertIn("chunk_content", v)


class ChatReceiptContract(unittest.TestCase):
    """Real-shaped raw chat JSON; exact required ids; hard rejects."""

    BASE = "http://127.0.0.1:9"

    @staticmethod
    def _receipt(**over):
        r = {
            "ok": True,
            "failures": [],
            "upstream": {"mode": "isolated-backend",
                         "url": "http://127.0.0.1:9"},
            "networkTripwire": {"clean": True, "blockedAttempts": []},
            "cleanup": {"stateDirUnchanged": True},
            "checks": [
                {"id": "proxy-isolated-backend-collections-200-scoped",
                 "ok": True},
                {"id": "proxy-isolated-backend-citation-content-exact",
                 "ok": True},
                {"id": "login-wrong-password-401", "ok": True},
            ],
        }
        r.update(over)
        return r

    def _rejects(self, mutate):
        bad = self._receipt()
        mutate(bad)
        failures = bc.validate_chat_receipt(bad, self.BASE)
        self.assertTrue(failures, "wrong chat receipt was accepted")
        return failures

    def test_valid_receipt_accepted(self):
        self.assertEqual(bc.validate_chat_receipt(self._receipt(), self.BASE),
                         [])

    def test_old_prefix_id_no_longer_matches(self):
        def mutate(r):
            r["checks"][0] = {"id": "proxy-restored-group-key-forwarded",
                              "ok": True}
        failures = self._rejects(mutate)
        self.assertTrue(any("missing" in f for f in failures))

    def test_wrong_url_rejected(self):
        self._rejects(lambda r: r["upstream"].__setitem__(
            "url", "http://127.0.0.1:8"))

    def test_wrong_mode_rejected(self):
        self._rejects(lambda r: r["upstream"].__setitem__(
            "mode", "loopback-sink"))

    def test_required_false_and_report_only_rejected(self):
        self._rejects(lambda r: r["checks"][0].__setitem__("ok", False))
        self._rejects(lambda r: r["checks"][0].__setitem__("reportOnly",
                                                           True))

    def test_missing_required_check_rejected(self):
        self._rejects(lambda r: r["checks"].pop(1))

    def test_legacy_file_route_id_rejected(self):
        # The chat proxy only allows /api/documents/{id}/content; the legacy
        # citation-file id asserted a forbidden/404 file route and is not an
        # acceptable substitute.
        def mutate(r):
            r["checks"][1] = {"id": "proxy-isolated-backend-citation-file-exact",
                              "ok": True}
        failures = self._rejects(mutate)
        self.assertTrue(any("missing" in f or "citation-content" in f
                            for f in failures))

    def test_network_tripwire_events_rejected(self):
        self._rejects(lambda r: r["networkTripwire"].__setitem__(
            "clean", False))
        self._rejects(lambda r: r["networkTripwire"].__setitem__(
            "blockedAttempts", [{"peer": "10.0.0.9"}]))

    def test_source_state_changed_rejected(self):
        self._rejects(lambda r: r["cleanup"].__setitem__(
            "stateDirUnchanged", False))

    def test_ok_and_failures_rejected(self):
        self._rejects(lambda r: r.__setitem__("ok", False))
        self._rejects(lambda r: r.__setitem__("failures", ["x"]))

    def test_empty_check_selection_rejected(self):
        self._rejects(lambda r: r.__setitem__("checks", []))

    def test_non_object_rejected(self):
        self.assertTrue(bc.validate_chat_receipt([1, 2], self.BASE))


class ChatExecutionEnvelope(unittest.TestCase):
    """run_chat_consumer parses ONE JSON, records the raw receipt, and the
    execution envelope stays separate (skipped without a node runtime)."""

    BASE = "http://127.0.0.1:9"

    def _script(self, tmp, body):
        path = tmp / "fake-chat-consumer.mjs"
        path.write_text(
            "process.stdout.write(JSON.stringify(%s));"
            % json.dumps(body))
        return path

    def test_happy_path_envelope_and_raw(self):
        if not shutil_which("node"):
            self.skipTest("node runtime unavailable")
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            script = self._script(tmp, {
                "ok": True, "failures": [],
                "upstream": {"mode": "isolated-backend", "url": self.BASE},
                "networkTripwire": {"clean": True, "blockedAttempts": []},
                "cleanup": {"stateDirUnchanged": True},
                "checks": [
                    {"id": "proxy-isolated-backend-collections-200-scoped",
                     "ok": True},
                    {"id": "proxy-isolated-backend-citation-content-exact",
                     "ok": True},
                ],
            })
            raw, execution, failures = bc.run_chat_consumer(
                script, tmp / "chat", self.BASE, "fx-admin", tmp / "blob.md",
                _mkdir(tmp / "work"), 30.0)
            self.assertEqual(failures, [])
            self.assertEqual(raw["ok"], True)
            self.assertEqual(execution["exit"], 0)
            self.assertIn("--state-dir", execution["command"])
            self.assertIn("node_interpreter", execution)

    def test_failing_script_yields_contract_failures_and_raw(self):
        if not shutil_which("node"):
            self.skipTest("node runtime unavailable")
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            script = self._script(tmp, {"ok": False, "failures": ["boom"]})
            raw, execution, failures = bc.run_chat_consumer(
                script, tmp / "chat", self.BASE, "fx-admin", tmp / "blob.md",
                _mkdir(tmp / "work"), 30.0)
            self.assertIsNotNone(raw)
            self.assertTrue(failures)
            self.assertEqual(execution["exit"], 0)


def shutil_which(name):
    import shutil
    return shutil.which(name)


def _mkdir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


class FakeIso:
    def iso_format(self):
        return "2026-10-01T00:00:00Z"


class JsonSafety(unittest.TestCase):
    """Neo4j driver temporals must serialize (ISO) instead of TypeError."""

    def test_iso_format_object(self):
        self.assertEqual(bc._jsonable({"created_at": FakeIso()}),
                         {"created_at": "2026-10-01T00:00:00Z"})

    def test_nested_structures(self):
        value = {"rels": [{"rp": {"when": FakeIso()}}]}
        self.assertEqual(
            bc._jsonable(value),
            {"rels": [{"rp": {"when": "2026-10-01T00:00:00Z"}}]})

    def test_fallback_str(self):
        class Weird:
            def __str__(self):
                return "PT2S"
        self.assertEqual(bc._jsonable(Weird()), "PT2S")


class FixtureStrictness(unittest.TestCase):
    """No generic pass-as-full: non-fixture expectations are BLOCKED."""

    def _args(self, tmp, **over):
        keys_path = tmp / "keys.json"
        keys_path.write_text(json.dumps([
            {"id": bc.FX_READ_KEY_ID, "plaintext": bc.FX_READ_KEY_PLAINTEXT,
             "role": "scoped", "collection": bc.FX_SHARED_COLLECTION_ID},
            {"id": bc.FX_MANAGE_KEY_ID,
             "plaintext": bc.FX_MANAGE_KEY_PLAINTEXT, "role": "read"},
        ]))
        root = tmp / "root"
        (root / "uploads").mkdir(parents=True, exist_ok=True)
        (root / "custom_inputs").mkdir(parents=True, exist_ok=True)
        (root / bc.FX_DOCUMENTS["fx-source-0001"]["file_path"]) \
            .write_text("fixture source text\n")
        args = type("Args", (), {})()
        args.run_id = "unit-fixture"
        args.bolt_url = "bolt://127.0.0.1:1"
        args.backend_dir = str(REPO_ROOT / "backend")
        args.files_root = str(root)
        args.keys_json = str(keys_path)
        args.expect_document_id = "fx-source-0001"
        args.expect_collection_id = bc.FX_SHARED_COLLECTION_ID
        args.app_storage_id = None
        args.chat_consumer = None
        args.out = str(tmp / "out")
        args.neo4j_user = "neo4j"
        args.neo4j_password = "fx-unit"
        args.admin_key = "fx-synthetic-admin-key-unit"
        args.port = 0
        args.startup_timeout = 5.0
        args.keep_scratch = True
        args.chat_timeout = 10.0
        args.chat_state_dir = None
        args.chat_app_node = None
        args.fulltext_term = None
        for k, v in over.items():
            setattr(args, k, v)
        return args

    def test_non_fixture_document_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(bc.Blocked):
                bc.run(self._args(Path(td),
                                  expect_document_id="doc-generic-9"), {})

    def test_non_fixture_collection_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(bc.Blocked):
                bc.run(self._args(Path(td),
                                  expect_collection_id="col-generic-9"), {})

    def test_non_fixture_app_storage_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(bc.Blocked):
                bc.run(self._args(Path(td), app_storage_id="other-app"), {})

    def test_wrong_key_material_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            args = self._args(tmp)
            (tmp / "badkeys.json").write_text(json.dumps([
                {"id": bc.FX_READ_KEY_ID, "plaintext": "wrong-plaintext",
                 "role": "scoped", "collection": bc.FX_SHARED_COLLECTION_ID},
                {"id": bc.FX_MANAGE_KEY_ID,
                 "plaintext": bc.FX_MANAGE_KEY_PLAINTEXT, "role": "read"},
            ]))
            args.keys_json = str(tmp / "badkeys.json")
            with self.assertRaises(bc.Blocked):
                bc.run(args, {})

    def test_fixture_identity_accepted_up_to_dead_bolt(self):
        import os
        try:
            import neo4j  # noqa: F401
        except ImportError:
            self.skipTest("neo4j driver unavailable in this interpreter")
        with tempfile.TemporaryDirectory() as td:
            receipt = {}
            old_home = os.environ.get("CORTEX_BACKEND_CONSUMER_HOME")
            os.environ["CORTEX_BACKEND_CONSUMER_HOME"] = td
            try:
                with self.assertRaises(bc.RunFailure) as ctx:
                    bc.run(self._args(Path(td)), receipt)
            finally:
                if old_home is None:
                    os.environ.pop("CORTEX_BACKEND_CONSUMER_HOME", None)
                else:
                    os.environ["CORTEX_BACKEND_CONSUMER_HOME"] = old_home
                for stale in (Path(td) / "cortex-backend-consumer") \
                        .glob("unit-fixture-*"):
                    import shutil
                    shutil.rmtree(stale, ignore_errors=True)
            self.assertIn("graph capture", str(ctx.exception))
            self.assertIn("fixture_keys", receipt)
            self.assertNotIn("note", receipt)


class ParseSingleJson(unittest.TestCase):
    def test_array_ok(self):
        obj, err = bc.parse_single_json('[1, 2]\n')
        self.assertIsNone(err)
        self.assertEqual(obj, [1, 2])

    def test_garbage_rejected(self):
        _, err = bc.parse_single_json('{"a":}')
        self.assertTrue(err)

    def test_two_objects_rejected(self):
        _, err = bc.parse_single_json('{}{}')
        self.assertTrue(err)


class Guards(unittest.TestCase):
    def test_run_id_guard(self):
        for bad in ("../x", "sub/dir", "a b", "..", ".x", "x\n"):
            with self.assertRaises(bc.Blocked):
                bc.validate_run_id(bad)

    def test_bolt_guard(self):
        bc.validate_bolt_url("bolt://127.0.0.1:1")
        bc.validate_bolt_url("bolt://localhost:1")
        for bad in ("bolt://0.0.0.0:1", "bolt://example.com:1",
                    "file:///tmp"):
            with self.assertRaises(bc.Blocked):
                bc.validate_bolt_url(bad)

    def test_files_root_guard(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "fixture-root"
            (root / "uploads").mkdir(parents=True)
            (root / "custom_inputs").mkdir()
            bc.validate_files_root(root, REPO_ROOT)
            bare = Path(td) / "bare"
            bare.mkdir()
            with self.assertRaises(bc.Blocked):
                bc.validate_files_root(bare, REPO_ROOT)

    def test_run_blocked_on_bad_run_id(self):
        args = type("Args", (), {})()
        args.run_id = "../traversal"
        receipt = {}
        with self.assertRaises(bc.Blocked):
            bc.run(args, receipt)
        self.assertEqual(receipt, {})


class ImmutableRows(unittest.TestCase):
    def test_reads_fixture_shape(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "storage.sqlite"
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT,"
                         " size INTEGER, updated_at TEXT)")
            conn.execute("INSERT INTO kv VALUES ('a', '{}', 2, 't')")
            conn.commit()
            conn.close()
            rows = bc._immutable_rows(db)
            self.assertEqual(rows, {"a": {"value": "{}", "size": 2,
                                          "updated_at": "t"}})


class ServerEnv(unittest.TestCase):
    """build_server_env with the DEFAULT CLI (no --backend-dir): the resolved
    absolute backend path must land in PYTHONPATH; every env value is a str;
    an unresolved required value fails loudly (never silently discarded)."""

    def _args(self, backend_dir=None):
        args = type("Args", (), {})()
        args.bolt_url = "bolt://127.0.0.1:1"
        args.neo4j_user = "neo4j"
        args.neo4j_password = "fx-unit"
        args.admin_key = "fx-synthetic-admin-key-unit"
        args.run_id = "unit-env"
        args.backend_dir = backend_dir  # parse_args default
        return args

    def test_default_cli_resolved_backend_path(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            scratch = Path(td)
            resolved = str(REPO_ROOT / "backend")
            env = bc.build_server_env(self._args(), scratch,
                                      "http://127.0.0.1:9/v1", resolved)
            self.assertEqual(env["PYTHONPATH"], resolved)
            self.assertTrue(Path(env["PYTHONPATH"]).is_absolute())
            self.assertEqual(
                env["PYTHONPATH"], str(Path(env["PYTHONPATH"]).resolve())
                if Path(env["PYTHONPATH"]).exists() else env["PYTHONPATH"])

    def test_all_values_are_strings(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            env = bc.build_server_env(
                self._args(), Path(td), "http://127.0.0.1:9/v1",
                str(REPO_ROOT / "backend"))
            bad = {k: v for k, v in env.items()
                   if v is None or not isinstance(v, str)}
            self.assertEqual(bad, {}, "non-string env values would "
                                      "TypeError in subprocess.Popen")

    def test_none_backend_dir_raises_not_discarded(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(bc.Blocked):
                bc.build_server_env(self._args(), Path(td),
                                    "http://127.0.0.1:9/v1", None)

    def test_no_ambient_secret_inheritance(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            env = bc.build_server_env(
                self._args(), Path(td), "http://127.0.0.1:9/v1",
                str(REPO_ROOT / "backend"))
            self.assertEqual(env["OPENAI_API_KEY"],
                             bc.PLACEHOLDER_PROVIDER_KEY)
            self.assertEqual(env["NEO4J_PASSWORD"], "fx-unit")
            self.assertEqual(env["ADMIN_API_KEY"],
                             "fx-synthetic-admin-key-unit")
            self.assertEqual(env.get("LANGFUSE_SECRET_KEY"), "")
            self.assertEqual(env.get("SENTRY_DSN"), "")


class CleanupFacts(unittest.TestCase):
    """Receipt cleanup must carry facts, not implied boot."""

    def test_dead_bolt_receipt_reports_not_spawned(self):
        try:
            import neo4j  # noqa: F401
        except ImportError:
            self.skipTest("neo4j driver unavailable in this interpreter")
        import os
        with tempfile.TemporaryDirectory() as td:
            receipt = {}
            old_home = os.environ.get("CORTEX_BACKEND_CONSUMER_HOME")
            os.environ["CORTEX_BACKEND_CONSUMER_HOME"] = td
            try:
                with self.assertRaises(bc.RunFailure):
                    bc.run(FixtureStrictness._args(FixtureStrictness,
                                                   Path(td)), receipt)
            finally:
                if old_home is None:
                    os.environ.pop("CORTEX_BACKEND_CONSUMER_HOME", None)
                else:
                    os.environ["CORTEX_BACKEND_CONSUMER_HOME"] = old_home
            self.assertFalse(receipt["cleanup"]["server_spawned"])
            self.assertFalse(receipt["cleanup"]["server_stopped"])
            self.assertTrue(receipt["failures"])
            self.assertIn("pre-boot", receipt.get("phase", ""))

    def test_unexpected_exception_path_writes_failures(self):
        # A run whose pre-boot capture raises an UNEXPECTED (non-Blocked,
        # non-RunFailure) exception must produce nonempty receipt failures
        # with the actual diagnostic and the correct phase, before cleanup.
        import os
        from unittest import mock
        with tempfile.TemporaryDirectory() as td:
            receipt = {}
            old_home = os.environ.get("CORTEX_BACKEND_CONSUMER_HOME")
            os.environ["CORTEX_BACKEND_CONSUMER_HOME"] = td
            try:
                with mock.patch.object(
                        bc, "safe_capture",
                        side_effect=RuntimeError("boom-diagnostic")):
                    bc.run(FixtureStrictness._args(FixtureStrictness,
                                                   Path(td)), receipt)
            finally:
                if old_home is None:
                    os.environ.pop("CORTEX_BACKEND_CONSUMER_HOME", None)
                else:
                    os.environ["CORTEX_BACKEND_CONSUMER_HOME"] = old_home
            self.assertTrue(receipt["failures"])
            self.assertIn("boom-diagnostic", receipt["failures"][0])
            self.assertIn("unexpected", receipt["failures"][0])
            self.assertIn("pre-boot-capture", receipt.get("phase", ""))
            self.assertFalse(receipt["cleanup"]["server_spawned"])


class FreshBootstrapDelta(unittest.TestCase):
    """Fresh-boot classes: constraint-backing RANGE pairing (exact, no name
    catchall) and the entity_count bootstrap backfill (independent expected
    counts, absent-before only)."""

    @staticmethod
    def _pre():
        return {
            "nodes": [{"labels": ["Document"], "key": "fx-source-0001",
                       "props": {"processing_status": "completed",
                                 "filename": "fixture-source.md"}}],
            "rels": [
                {"la": ["Document"], "ka": "fx-source-0001", "rt": "HAS_CHUNK",
                 "rp": {}, "lb": ["Chunk"], "kb": "fx-source-0001-chunk-0"},
                {"la": ["Chunk"], "ka": "fx-source-0001-chunk-0",
                 "rt": "MENTIONS", "rp": {}, "lb": ["Entity"],
                 "kb": "Rehearsal Alpha"}],
            "constraints": [], "indexes": []}

    def test_independent_expected_count(self):
        self.assertEqual(bc.expected_entity_counts(self._pre()),
                         {"fx-source-0001": 1})

    def _after(self):
        after = json.loads(json.dumps(self._pre()))
        after["nodes"][0]["props"]["entity_count"] = 1
        after["constraints"] = [
            {"name": "collection_id", "type": "UNIQUENESS",
             "entityType": "NODE", "labelsOrTypes": ["Collection"],
             "properties": ["id"]}]
        after["indexes"] = [
            {"name": "collection_id", "type": "RANGE", "entityType": "NODE",
             "labelsOrTypes": ["Collection"], "properties": ["id"],
             "indexProvider": "native-btree"}]
        return after

    def test_healthy_fresh_bootstrap_accepted(self):
        exp = bc.expected_entity_counts(self._pre())
        out = bc.classify_delta(
            bc.diff_capture(self._pre(), self._after()), ["k1"],
            expected_entity_counts=exp)
        self.assertEqual(out["violations"], [])
        self.assertEqual(out["accepted"]["entity_count_backfilled"],
                         {"fx-source-0001": 1})
        self.assertEqual(out["accepted"]["constraint_backing_indexes"],
                         ["collection_id"])

    def test_unpaired_wrong_range_index_denied(self):
        after = self._after()
        after["indexes"].append(
            {"name": "collection_id", "type": "RANGE", "entityType": "NODE",
             "labelsOrTypes": ["Document"], "properties": ["id"],
             "indexProvider": "native-btree"})
        out = bc.classify_delta(
            bc.diff_capture(self._pre(), after), ["k1"],
            expected_entity_counts=bc.expected_entity_counts(self._pre()))
        self.assertTrue(any("collection_id" in v for v in out["violations"]))

    def test_wrong_derived_count_denied(self):
        after = self._after()
        after["nodes"][0]["props"]["entity_count"] = 5
        out = bc.classify_delta(
            bc.diff_capture(self._pre(), after), ["k1"],
            expected_entity_counts=bc.expected_entity_counts(self._pre()))
        self.assertTrue(any("entity_count" in v for v in out["violations"]))

    def test_arbitrary_document_field_denied(self):
        after = self._after()
        after["nodes"][0]["props"]["filename"] = "tampered.md"
        out = bc.classify_delta(
            bc.diff_capture(self._pre(), after), ["k1"],
            expected_entity_counts=bc.expected_entity_counts(self._pre()))
        self.assertTrue(any("tampered" in v or "filename" in v
                            for v in out["violations"]))

    def test_preexisting_wrong_count_mutation_denied(self):
        pre = self._pre()
        pre["nodes"][0]["props"]["entity_count"] = 99
        out = bc.classify_delta(
            bc.diff_capture(pre, self._after()),
            ["k1"], expected_entity_counts={"fx-source-0001": 1})
        self.assertTrue(out["violations"],
                        "pre-existing entity_count mutation passed as "
                        "bootstrap backfill")

    def test_warm_graph_no_entity_count_allowance(self):
        # On a warm graph (entity_count already present pre-boot) the
        # bootstrap allowance is inactive: any Document change fails.
        pre = self._pre()
        pre["nodes"][0]["props"]["entity_count"] = 1
        after = json.loads(json.dumps(pre))
        after["nodes"][0]["props"]["entity_count"] = 7
        out = bc.classify_delta(bc.diff_capture(pre, after), ["k1"],
                                expected_entity_counts={})
        self.assertTrue(any("Document" in v for v in out["violations"]))


class ChatHookPreconditions(unittest.TestCase):
    """Owned work-dir + TMPDIR + capacity gate before the locked install."""

    def test_capacity_gate_blocks_before_node_execution(self):
        if not shutil_which("node"):
            self.skipTest("node runtime unavailable")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            script = tmp / "fake.mjs"
            script.write_text("process.stdout.write(JSON.stringify({}))")
            raw, execution, failures = bc.run_chat_consumer(
                script, tmp / "chat", "http://127.0.0.1:9", "fx-admin",
                tmp / "blob.md", tmp / "work", 30.0,
                chat_work_dir=tmp / "chat-work",
                chat_tmpdir=tmp / "tmp",
                min_free_mb=10**9)  # impossibly large -> blocked
            self.assertIsNone(raw)
            self.assertEqual(execution["exit"], "capacity-blocked")
            self.assertTrue(any("capacity gate" in f for f in failures))
            self.assertIn("free_mb_before", execution)
            self.assertIn("work_dir", execution)

    def test_work_dir_passed_and_tmpdir_set(self):
        if not shutil_which("node"):
            self.skipTest("node runtime unavailable")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            script = tmp / "fake.mjs"
            receipt = {
                "ok": True, "failures": [],
                "upstream": {"mode": "isolated-backend",
                             "url": "http://127.0.0.1:9"},
                "networkTripwire": {"clean": True, "blockedAttempts": []},
                "cleanup": {"stateDirUnchanged": True},
                "checks": [
                    {"id": "proxy-isolated-backend-collections-200-scoped",
                     "ok": True},
                    {"id": "proxy-isolated-backend-citation-content-exact",
                     "ok": True},
                ],
                "argv": None,
            }
            script.write_text(
                "process.stdout.write(JSON.stringify({...%s, "
                "argv: process.argv}));" % json.dumps(receipt))
            (tmp / "work").mkdir(parents=True, exist_ok=True)
            raw, execution, failures = bc.run_chat_consumer(
                script, tmp / "chat", "http://127.0.0.1:9", "fx-admin",
                tmp / "blob.md", tmp / "work", 30.0,
                chat_work_dir=tmp / "chat-work", chat_tmpdir=tmp / "tmp",
                min_free_mb=1)
            self.assertEqual(failures, [])
            self.assertIn("--work-dir", execution["command"])
            self.assertIn(str(tmp / "chat-work"), execution["command"])
            self.assertIn("--work-dir", raw["argv"])
            self.assertTrue((tmp / "chat-work").is_dir())
            self.assertEqual(execution["tmpdir"], str(tmp / "tmp"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
