"""Unit tests for startup recovery of documents orphaned mid-processing.

`Neo4jService.reset_orphaned_processing_documents` runs once at startup to rescue
documents stranded in a transient state ('processing'/'extracting') by a prior
shutdown — otherwise their spinner never resolves and `/api/instance/status`
reports `safe_to_redeploy: false` forever. The method is thin (one Cypher write),
so we instantiate the service via object.__new__ and mock the driver to assert
the contract: it targets only transient states, resets to 'pending', and returns
the affected ids.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from app.services.neo4j_service import Neo4jService


@pytest.fixture
def svc_with_session():
    svc = object.__new__(Neo4jService)
    session = MagicMock()

    @contextmanager
    def _session_cm():
        yield session

    driver = MagicMock()
    driver.session.side_effect = lambda *a, **k: _session_cm()
    svc._driver = driver  # back the read-only `driver` property
    return svc, session


def test_resets_and_returns_ids(svc_with_session):
    svc, session = svc_with_session
    session.run.return_value = [{"id": "doc-1"}, {"id": "doc-2"}]

    out = svc.reset_orphaned_processing_documents()

    assert out == ["doc-1", "doc-2"]
    # The query must scope to transient states and set status to pending.
    cypher = session.run.call_args.args[0]
    assert "'processing'" in cypher and "'extracting'" in cypher
    assert "'pending'" in cypher
    # Must not touch terminal states.
    assert "'completed'" not in cypher and "'failed'" not in cypher


def test_no_orphans_returns_empty(svc_with_session):
    svc, session = svc_with_session
    session.run.return_value = []
    assert svc.reset_orphaned_processing_documents() == []
