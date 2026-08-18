"""Document-deletion provenance hygiene + relationship document filter.

Deleting (or reprocessing) a document must scrub its id from
Entity.source_documents on surviving shared entities — otherwise entities
carry provenance pointers at documents that no longer exist (silent data
rot; see the upstream feedback report). And the persisted per-edge
attribution (r.source_document_id) must be queryable through
GET /api/graph/relationships.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.services.neo4j_service import Neo4jService

SCRUB_FRAGMENT = "SET e.source_documents = [x IN e.source_documents WHERE x <> $id]"


def _service_with_session():
    svc = Neo4jService()
    driver = MagicMock()
    session = MagicMock()
    driver.session.return_value.__enter__ = MagicMock(return_value=session)
    driver.session.return_value.__exit__ = MagicMock(return_value=False)
    svc._driver = driver
    return svc, session


def _all_queries(session):
    return [" ".join(call.args[0].split()) for call in session.run.call_args_list]


class TestDeleteProvenanceScrub:
    def test_delete_document_scrubs_source_documents(self):
        svc, session = _service_with_session()
        # One record dict serves every .single() in the cascade.
        session.run.return_value.single.return_value = {
            "orphaned_entities": [],
            "removed_count": 0,
            "deleted": 1,
        }

        result = svc.delete_document("doc-123")

        assert result["deleted"] is True
        scrubs = [q for q in _all_queries(session) if SCRUB_FRAGMENT in q]
        assert len(scrubs) == 1
        assert "$id IN coalesce(e.source_documents, [])" in scrubs[0]

    def test_delete_document_scrub_runs_before_document_removal(self):
        svc, session = _service_with_session()
        session.run.return_value.single.return_value = {
            "orphaned_entities": [],
            "removed_count": 0,
            "deleted": 1,
        }

        svc.delete_document("doc-123")

        queries = _all_queries(session)
        scrub_idx = next(i for i, q in enumerate(queries) if SCRUB_FRAGMENT in q)
        delete_idx = next(i for i, q in enumerate(queries) if "DETACH DELETE d, c" in q)
        assert scrub_idx < delete_idx

    def test_delete_document_chunks_scrubs_source_documents(self):
        svc, session = _service_with_session()
        session.run.return_value.single.return_value = {
            "orphaned_entities": [],
            "deleted": 0,
        }

        svc.delete_document_chunks("doc-123")

        scrubs = [q for q in _all_queries(session) if SCRUB_FRAGMENT in q]
        assert len(scrubs) == 1


class TestRelationshipsDocumentFilter:
    def _run(self, svc, session, **kwargs):
        session.run.return_value.single.return_value = {"total": 0}
        session.run.return_value.__iter__ = MagicMock(return_value=iter([]))
        return svc.list_relationships_paginated(**kwargs)

    def test_document_id_adds_where_clause_and_param(self):
        svc, session = _service_with_session()
        self._run(svc, session, document_id="doc-123")

        count_query = " ".join(session.run.call_args_list[0].args[0].split())
        data_query = " ".join(session.run.call_args_list[1].args[0].split())
        assert "r.source_document_id = $document_id" in count_query
        assert "r.source_document_id = $document_id" in data_query
        assert session.run.call_args_list[0].kwargs["document_id"] == "doc-123"

    def test_no_document_id_leaves_query_unfiltered(self):
        svc, session = _service_with_session()
        self._run(svc, session)

        count_query = " ".join(session.run.call_args_list[0].args[0].split())
        assert "source_document_id = $document_id" not in count_query
        assert "document_id" not in session.run.call_args_list[0].kwargs

    def test_rows_include_source_document_id(self):
        svc, session = _service_with_session()
        self._run(svc, session)

        data_query = " ".join(session.run.call_args_list[1].args[0].split())
        assert "r.source_document_id as source_document_id" in data_query
        assert data_query.rstrip().endswith(
            "RETURN source, target, rel_type as type, description, weight, source_document_id"
        )


class TestRelationshipsEndpointContract:
    def test_document_id_param_reaches_service(self, client, mock_neo4j):
        mock_neo4j.list_relationships_paginated.return_value = {
            "relationships": [],
            "total": 0,
        }

        resp = client.get("/api/graph/relationships?document_id=doc-123")

        assert resp.status_code == 200
        args = mock_neo4j.list_relationships_paginated.call_args.args
        # (skip, limit, search, rel_type, collection_filter, document_id)
        assert args[5] == "doc-123"
