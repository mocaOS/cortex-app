"""Neo4j service for document, vector, and knowledge graph storage.

Features:
- Community detection using graph algorithms
- Collection-level knowledge graphs
- Semantic entity resolution with embeddings
- Community summarization support
"""

from neo4j import GraphDatabase, AsyncGraphDatabase
from neo4j.exceptions import ServiceUnavailable, SessionExpired, TransientError
from typing import Optional, List, Tuple
from datetime import datetime, timedelta
import functools
import logging
import time
import numpy as np
from contextlib import asynccontextmanager
import uuid

from app.config import get_settings
from app.models import (
    Document, DocumentChunk, DocumentMetadata, ProcessingStatus,
    Entity, Relationship, ExtractionResult, Collection, Community
)

logger = logging.getLogger(__name__)

# Errors worth retrying: Neo4j restarting (compose OOM-restart is a designed-for
# event), a pooled connection killed mid-flight, or a transient server error
# (deadlock, leader switch). Auto-commit `session.run` gets NO driver-side retry
# — only managed transactions do — so idempotent service methods opt in via
# @retry_on_transient below.
_TRANSIENT_ERRORS = (ServiceUnavailable, SessionExpired, TransientError)


def retry_on_transient(fn):
    """Retry an idempotent Neo4jService method on transient driver errors.

    3 attempts with 0.5s/1.5s backoff (waits sized for a Neo4j restart to
    come back). ONLY apply to methods that are safe to re-run: reads, and
    writes that are pure SET/MERGE upserts. Never apply to CREATE-based
    writes or multi-step deletes.
    """

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        delay = 0.5
        for attempt in range(1, 4):
            try:
                return fn(self, *args, **kwargs)
            except _TRANSIENT_ERRORS as e:
                if attempt == 3:
                    raise
                logger.warning(
                    "Transient Neo4j error in %s (attempt %d/3): %s — retrying in %.1fs",
                    fn.__name__, attempt, e, delay,
                )
                time.sleep(delay)
                delay *= 3

    return wrapper


class Neo4jService:
    """Service for interacting with Neo4j database."""
    
    def __init__(self):
        self.settings = get_settings()
        self._driver = None
        # Tracks silent degradation of semantic entity dedup: every failed
        # vector-index lookup falls back to Levenshtein-only resolution.
        # Surfaced in get_stats() so operators can see it.
        self._vector_search_failures = 0
        self._vector_search_failure_warned = False
    
    @property
    def driver(self):
        if self._driver is None:
            self._driver = GraphDatabase.driver(
                self.settings.neo4j_uri,
                auth=(self.settings.neo4j_user, self.settings.neo4j_password),
                max_connection_pool_size=self.settings.neo4j_max_pool_size,
                connection_timeout=self.settings.neo4j_connection_timeout,
                connection_acquisition_timeout=(
                    self.settings.neo4j_connection_acquisition_timeout
                ),
                keep_alive=True,
            )
            logger.info(
                "Neo4j driver created (pool_size=%s, connection_timeout=%ss, "
                "acquisition_timeout=%ss)",
                self.settings.neo4j_max_pool_size,
                self.settings.neo4j_connection_timeout,
                self.settings.neo4j_connection_acquisition_timeout,
            )
        return self._driver
    
    def close(self):
        if self._driver:
            self._driver.close()
            self._driver = None
    
    def verify_connectivity(self) -> bool:
        """Check if Neo4j is reachable."""
        try:
            self.driver.verify_connectivity()
            return True
        except ServiceUnavailable:
            return False
    
    def initialize_schema(self):
        """Create indexes and constraints for the knowledge base and knowledge graph."""
        with self.driver.session() as session:
            # =================================================================
            # Document and Chunk constraints
            # =================================================================
            session.run("""
                CREATE CONSTRAINT document_id IF NOT EXISTS
                FOR (d:Document) REQUIRE d.id IS UNIQUE
            """)
            
            session.run("""
                CREATE CONSTRAINT chunk_id IF NOT EXISTS
                FOR (c:Chunk) REQUIRE c.id IS UNIQUE
            """)
            
            # =================================================================
            # Collection constraints
            # =================================================================
            try:
                session.run("""
                    CREATE CONSTRAINT collection_id IF NOT EXISTS
                    FOR (col:Collection) REQUIRE col.id IS UNIQUE
                """)
            except Exception as e:
                logger.warning(f"Collection constraint may already exist: {e}")
            
            # Create default collection
            try:
                session.run("""
                    MERGE (col:Collection {id: $id})
                    ON CREATE SET 
                        col.name = $name,
                        col.description = $description,
                        col.created_at = datetime()
                """, id="default", name="Default", description="Default collection for documents")
            except Exception as e:
                logger.warning(f"Could not create default collection: {e}")
            
            # =================================================================
            # Entity constraints and indexes for Knowledge Graph
            # =================================================================
            session.run("""
                CREATE CONSTRAINT entity_name IF NOT EXISTS
                FOR (e:Entity) REQUIRE e.name IS UNIQUE
            """)
            
            # Index on entity type for faster filtering
            try:
                session.run("""
                    CREATE INDEX entity_type IF NOT EXISTS
                    FOR (e:Entity) ON (e.type)
                """)
            except Exception as e:
                logger.warning(f"Entity type index may already exist: {e}")
            
            # Index on entity community_id for community queries
            try:
                session.run("""
                    CREATE INDEX entity_community IF NOT EXISTS
                    FOR (e:Entity) ON (e.community_id)
                """)
            except Exception as e:
                logger.warning(f"Entity community index may already exist: {e}")
            
            # =================================================================
            # Community constraints
            # =================================================================
            try:
                session.run("""
                    CREATE CONSTRAINT community_id IF NOT EXISTS
                    FOR (com:Community) REQUIRE com.id IS UNIQUE
                """)
            except Exception as e:
                logger.warning(f"Community constraint may already exist: {e}")
            
            # =================================================================
            # Vector indexes (with dimension mismatch detection)
            # =================================================================
            # Check existing vector indexes for dimension mismatches
            target_dim = self.settings.embedding_dimension
            try:
                idx_result = session.run(
                    "SHOW INDEXES YIELD name, type, options "
                    "WHERE type = 'VECTOR' AND name IN ['chunk_embedding', 'entity_embedding'] "
                    "RETURN name, options"
                )
                for idx_record in idx_result:
                    idx_name = idx_record["name"]
                    existing_dim = idx_record["options"].get("indexConfig", {}).get("vector.dimensions")
                    if existing_dim is not None and existing_dim != target_dim:
                        logger.warning(
                            f"Vector index '{idx_name}' has {existing_dim} dimensions but config expects {target_dim}. "
                            f"Dropping and recreating index."
                        )
                        session.run(f"DROP INDEX {idx_name}")
            except Exception as e:
                logger.warning(f"Could not check vector index dimensions: {e}")

            # Vector index for chunk embeddings
            try:
                session.run("""
                    CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS
                    FOR (c:Chunk)
                    ON c.embedding
                    OPTIONS {
                        indexConfig: {
                            `vector.dimensions`: $dimensions,
                            `vector.similarity_function`: 'cosine'
                        }
                    }
                """, dimensions=target_dim)
            except Exception as e:
                logger.warning(f"Chunk vector index may already exist: {e}")

            # Vector index for entity embeddings (semantic entity resolution)
            try:
                session.run("""
                    CREATE VECTOR INDEX entity_embedding IF NOT EXISTS
                    FOR (e:Entity)
                    ON e.embedding
                    OPTIONS {
                        indexConfig: {
                            `vector.dimensions`: $dimensions,
                            `vector.similarity_function`: 'cosine'
                        }
                    }
                """, dimensions=target_dim)
            except Exception as e:
                logger.warning(f"Entity vector index may already exist: {e}")
            
            # =================================================================
            # Full-text indexes
            # =================================================================
            # Full-text index for chunk content
            try:
                session.run("""
                    CREATE FULLTEXT INDEX chunk_content IF NOT EXISTS
                    FOR (c:Chunk) ON EACH [c.content]
                """)
            except Exception as e:
                logger.warning(f"Chunk fulltext index may already exist: {e}")
            
            # Full-text index for entity names (for fuzzy matching)
            try:
                session.run("""
                    CREATE FULLTEXT INDEX entity_name_fulltext IF NOT EXISTS
                    FOR (e:Entity) ON EACH [e.name, e.description]
                """)
            except Exception as e:
                logger.warning(f"Entity fulltext index may already exist: {e}")
            
            # Full-text index for community summaries
            try:
                session.run("""
                    CREATE FULLTEXT INDEX community_summary_fulltext IF NOT EXISTS
                    FOR (com:Community) ON EACH [com.summary, com.name]
                """)
            except Exception as e:
                logger.warning(f"Community fulltext index may already exist: {e}")
            
            # =================================================================
            # API Key constraints
            # =================================================================
            try:
                session.run("""
                    CREATE CONSTRAINT api_key_id IF NOT EXISTS
                    FOR (k:APIKey) REQUIRE k.id IS UNIQUE
                """)
            except Exception as e:
                logger.warning(f"APIKey constraint may already exist: {e}")
            
            # =================================================================
            # Skill constraints (agentskills.io)
            # =================================================================
            try:
                session.run("""
                    CREATE CONSTRAINT skill_id IF NOT EXISTS
                    FOR (s:Skill) REQUIRE s.skill_id IS UNIQUE
                """)
            except Exception as e:
                logger.warning(f"Skill constraint may already exist: {e}")

            # =================================================================
            # Git integration constraints & indexes
            # =================================================================
            try:
                session.run("""
                    CREATE CONSTRAINT git_connection_id IF NOT EXISTS
                    FOR (g:GitConnection) REQUIRE g.id IS UNIQUE
                """)
            except Exception as e:
                logger.warning(f"GitConnection constraint may already exist: {e}")
            try:
                # Keyed lookup for incremental sync: find the document for a
                # given (connection, repo path) without relying on filename dedup.
                session.run("""
                    CREATE INDEX git_document_path IF NOT EXISTS
                    FOR (d:Document) ON (d.git_connection_id, d.git_path)
                """)
            except Exception as e:
                logger.warning(f"git_document_path index may already exist: {e}")

            # =================================================================
            # Phase B checkpoint index (enable_phaseb_checkpointing)
            # =================================================================
            try:
                session.run("""
                    CREATE INDEX phaseb_checkpoint_key IF NOT EXISTS
                    FOR (p:PhaseBCheckpoint) ON (p.run_signature, p.batch_key)
                """)
            except Exception as e:
                logger.warning(f"PhaseBCheckpoint index may already exist: {e}")

            # =================================================================
            # Data migrations
            # =================================================================
            # Backfill source field on existing documents that don't have one
            try:
                result = session.run("""
                    MATCH (d:Document) WHERE d.source IS NULL
                    SET d.source = CASE
                        WHEN d.is_custom_input = true THEN 'custom_input'
                        ELSE 'upload'
                    END
                    RETURN count(d) as updated
                """)
                updated = result.single()["updated"]
                if updated > 0:
                    logger.info(f"Backfilled source field on {updated} existing documents")
            except Exception as e:
                logger.warning(f"Could not backfill document source field: {e}")

            # =================================================================
            # Vector index health check
            # =================================================================
            # A broken/missing vector index silently degrades semantic entity
            # dedup to Levenshtein-only — make that loud at startup.
            try:
                health = session.run(
                    "SHOW INDEXES YIELD name, type, state "
                    "WHERE type = 'VECTOR' AND name IN ['chunk_embedding', 'entity_embedding'] "
                    "RETURN name, state"
                )
                states = {r["name"]: r["state"] for r in health}
                for idx_name in ("chunk_embedding", "entity_embedding"):
                    state = states.get(idx_name)
                    if state is None:
                        logger.error(
                            f"Vector index '{idx_name}' is MISSING — semantic "
                            f"search/dedup will silently degrade. Check "
                            f"EMBEDDING_DIMENSION vs the index creation errors above."
                        )
                    elif state == "FAILED":
                        logger.error(
                            f"Vector index '{idx_name}' is in FAILED state — "
                            f"drop and recreate it (restart re-runs schema init)."
                        )
                    elif state != "ONLINE":
                        # POPULATING right after first creation is normal
                        logger.info(f"Vector index '{idx_name}' state: {state}")
            except Exception as e:
                logger.warning(f"Could not verify vector index health: {e}")

            logger.info("Neo4j schema initialized successfully (including Collections, Communities, GraphRAG indexes, APIKeys, Skills)")
    
    def store_document(self, doc_id: str, metadata: DocumentMetadata) -> str:
        """Store a document node in Neo4j."""
        with self.driver.session() as session:
            result = session.run("""
                MERGE (d:Document {id: $id})
                SET d.filename = $filename,
                    d.file_type = $file_type,
                    d.file_size = $file_size,
                    d.file_path = $file_path,
                    d.upload_date = $upload_date,
                    d.chunk_count = $chunk_count,
                    d.processing_status = $status,
                    d.error_message = $error_message,
                    d.progress_current = $progress_current,
                    d.progress_total = $progress_total,
                    d.progress_message = $progress_message,
                    d.source = $source,
                    d.git_connection_id = COALESCE($git_connection_id, d.git_connection_id),
                    d.git_path = COALESCE($git_path, d.git_path),
                    d.git_blob_sha = COALESCE($git_blob_sha, d.git_blob_sha),
                    d.git_commit_sha = COALESCE($git_commit_sha, d.git_commit_sha),
                    d.git_sync_status = COALESCE($git_sync_status, d.git_sync_status)
                RETURN d.id as id
            """,
                id=doc_id,
                filename=metadata.filename,
                file_type=metadata.file_type,
                file_size=metadata.file_size,
                file_path=metadata.file_path,
                upload_date=metadata.upload_date.isoformat(),
                chunk_count=metadata.chunk_count,
                status=metadata.processing_status.value,
                progress_current=metadata.progress_current,
                progress_total=metadata.progress_total,
                progress_message=metadata.progress_message,
                error_message=metadata.error_message,
                source=metadata.source,
                git_connection_id=metadata.git_connection_id,
                git_path=metadata.git_path,
                git_blob_sha=metadata.git_blob_sha,
                git_commit_sha=metadata.git_commit_sha,
                git_sync_status=metadata.git_sync_status,
            )
            return result.single()["id"]
    
    def store_chunk(self, chunk: DocumentChunk) -> str:
        """Store a document chunk with its embedding."""
        with self.driver.session() as session:
            # Convert embedding to list if it's a numpy array
            embedding = chunk.embedding
            if isinstance(embedding, np.ndarray):
                embedding = embedding.tolist()
            
            import hashlib as _hashlib
            content_hash = _hashlib.sha256(
                (chunk.content or "").encode("utf-8", errors="replace")
            ).hexdigest()[:32]

            result = session.run("""
                MATCH (d:Document {id: $document_id})
                MERGE (c:Chunk {id: $chunk_id})
                SET c.content = $content,
                    c.embedding = $embedding,
                    c.has_embedding = $has_embedding,
                    c.chunk_index = $chunk_index,
                    c.metadata = $metadata,
                    c.content_hash = $content_hash
                MERGE (d)-[:HAS_CHUNK]->(c)
                RETURN c.id as id
            """,
                document_id=chunk.document_id,
                chunk_id=chunk.id,
                content=chunk.content,
                embedding=embedding,
                # Boolean mirror of the (large) embedding vector so embedding
                # coverage can be checked without streaming vectors.
                has_embedding=embedding is not None,
                chunk_index=chunk.chunk_index,
                metadata=str(chunk.metadata),
                content_hash=content_hash,
            )
            return result.single()["id"]

    @retry_on_transient
    def set_document_fingerprint(
        self, doc_id: str, file_sha256: str, config_hash: str
    ) -> None:
        """Record the processed file + extraction-config identity on the
        Document (used by enable_reprocess_delta to skip unchanged reprocesses)."""
        with self.driver.session() as session:
            session.run("""
                MATCH (d:Document {id: $id})
                SET d.file_sha256 = $file_sha256,
                    d.reprocess_config_hash = $config_hash
            """, id=doc_id, file_sha256=file_sha256, config_hash=config_hash)

    @retry_on_transient
    def get_document_fingerprint(self, doc_id: str) -> Optional[dict]:
        """Return {file_sha256, config_hash, processing_status, entity_count,
        unembedded_chunk_count} or None.

        The degraded signals (entity_count == 0 / unembedded chunks) let the
        reprocess delta-skip force a real reprocess for a document that
        "completed" without a usable graph or embeddings — an unchanged
        file+config must not no-op a degraded doc."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (d:Document {id: $id})
                RETURN d.file_sha256 as file_sha256,
                       d.reprocess_config_hash as config_hash,
                       coalesce(d.processing_status, 'pending') as processing_status,
                       coalesce(d.entity_count, -1) as entity_count,
                       size([(d)-[:HAS_CHUNK]->(c:Chunk) WHERE c.has_embedding = false | 1]) as unembedded_chunk_count
            """, id=doc_id)
            record = result.single()
            return dict(record) if record else None
    
    @retry_on_transient
    def update_document_status(
        self,
        doc_id: str,
        status: ProcessingStatus,
        chunk_count: int = 0,
        error_message: Optional[str] = None,
        progress_message: str = "",
        entity_count: Optional[int] = None
    ):
        """Update the processing status of a document.

        `entity_count` is only SET when provided — callers pass it on
        successful completion when graph extraction actually ran, so a
        completed doc with entity_count=0 is a reliable "degraded" signal
        (extraction was expected but produced nothing). When extraction is
        disabled the field stays unset and the doc is never flagged.
        """
        query = """
                MATCH (d:Document {id: $id})
                SET d.processing_status = $status,
                    d.chunk_count = $chunk_count,
                    d.error_message = $error_message,
                    d.progress_message = $progress_message,
                    d.status_updated_at = datetime()
        """
        params = dict(
            id=doc_id,
            status=status.value,
            chunk_count=chunk_count,
            error_message=error_message,
            progress_message=progress_message,
        )
        if entity_count is not None:
            query += ", d.entity_count = $entity_count"
            params["entity_count"] = entity_count
        with self.driver.session() as session:
            session.run(query, **params)

    def set_document_injection_flag(
        self, doc_id: str, flagged: bool, reason: str = ""
    ) -> None:
        """Persist the ingestion prompt-injection scan result on a Document.

        Always writes both properties so a reprocess of a previously-flagged
        document that is now clean clears the flag.
        """
        with self.driver.session() as session:
            session.run(
                """
                MATCH (d:Document {id: $id})
                SET d.injection_flagged = $flagged,
                    d.injection_reason = $reason
                """,
                id=doc_id,
                flagged=bool(flagged),
                reason=reason or "",
            )

    @retry_on_transient
    def update_document_progress(
        self,
        doc_id: str,
        current: int,
        total: int,
        message: str
    ):
        """Update the processing progress of a document."""
        with self.driver.session() as session:
            session.run("""
                MATCH (d:Document {id: $id})
                SET d.progress_current = $current,
                    d.progress_total = $total,
                    d.progress_message = $message,
                    d.status_updated_at = datetime()
            """,
                id=doc_id,
                current=current,
                total=total,
                message=message
            )

    @retry_on_transient
    def update_image_progress(
        self,
        doc_id: str,
        current: int,
        total: int,
        message: str
    ):
        """Update the image analysis progress of a document."""
        with self.driver.session() as session:
            session.run("""
                MATCH (d:Document {id: $id})
                SET d.image_progress_current = $current,
                    d.image_progress_total = $total,
                    d.image_progress_message = $message
            """,
                id=doc_id,
                current=current,
                total=total,
                message=message
            )

    @retry_on_transient
    def reset_orphaned_processing_documents(self) -> list[str]:
        """Reset documents stranded mid-pipeline back to 'pending'.

        Document processing runs as in-process background tasks. When the
        backend restarts (every redeploy/upgrade in the per-tenant deploy
        model), any document in a transient state ('processing'/'extracting')
        is orphaned: no task is tracking it, so its spinner never resolves and
        it permanently keeps `/api/instance/status` reporting `safe_to_redeploy:
        false`. Run once at startup — at that point no processing can legitimately
        be in flight, so every transient-state doc is by definition orphaned.

        Resets them to 'pending' (the truthful "waiting to be processed" state)
        so they rejoin the normal pending queue and the operator can re-run
        processing, rather than leaving them stuck forever.

        Returns the list of reset document ids.
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (d:Document)
                WHERE d.processing_status IN ['processing', 'extracting']
                SET d.processing_status = 'pending',
                    d.progress_message = 'Reset to pending after a server restart interrupted processing — reprocess to retry',
                    d.error_message = null
                RETURN d.id AS id
            """)
            return [record["id"] for record in result]

    @retry_on_transient
    def reset_stranded_processing_documents(
        self, active_ids: list[str], stale_minutes: int = 30
    ) -> list[str]:
        """Periodic-safe variant of reset_orphaned_processing_documents.

        The startup reset assumes nothing can legitimately be in flight; this
        one runs hourly while the instance is live, so it only resets a
        transient-state document when BOTH hold: no in-process task/flag is
        tracking it (`active_ids`), and it hasn't written a status/progress
        heartbeat (`status_updated_at`) for `stale_minutes`. Catches the rare
        strand where the failure-path status write itself lost its Neo4j
        connection past the transient retries — without it, such a document
        spins forever and pins `safe_to_redeploy: false` until a restart.
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (d:Document)
                WHERE d.processing_status IN ['processing', 'extracting']
                  AND NOT d.id IN $active
                  AND (d.status_updated_at IS NULL
                       OR d.status_updated_at < datetime() - duration({minutes: $stale}))
                SET d.processing_status = 'pending',
                    d.progress_message = 'Reset to pending after processing stalled with no live task — reprocess to retry',
                    d.error_message = null,
                    d.status_updated_at = datetime()
                RETURN d.id AS id
            """, active=active_ids, stale=stale_minutes)
            return [record["id"] for record in result]

    def backfill_degraded_document_signals(self, include_entity_counts: bool = True) -> dict:
        """One-time, idempotent backfill of the degraded-document signals for
        data created before those signals existed.

        (a) `Chunk.has_embedding` — derived from `c.embedding IS NOT NULL` for
            chunks that predate the boolean, so embedding coverage can be
            checked without streaming vectors.
        (b) `Document.entity_count` — computed from the graph for completed
            documents that predate the field. Only run when graph extraction
            is enabled (`include_entity_counts`): with extraction off, 0
            entities is normal, not degraded, and the field must stay unset.

        Both updates run batched via `CALL {} IN TRANSACTIONS` on an
        auto-commit session (same memory-safety constraint as the batched
        deletes — a single transaction over every chunk would blow past the
        transaction memory cap on large knowledge bases). Idempotent: only
        NULL fields are touched, so re-running at every startup is a no-op.

        Returns {"chunks_backfilled": N, "documents_backfilled": M}.
        """
        summary = {"chunks_backfilled": 0, "documents_backfilled": 0}
        with self.driver.session() as session:
            pending_chunks = session.run("""
                MATCH (c:Chunk) WHERE c.has_embedding IS NULL
                RETURN count(c) as n
            """).single()["n"]
            if pending_chunks:
                session.run("""
                    MATCH (c:Chunk)
                    WHERE c.has_embedding IS NULL
                    CALL {
                        WITH c
                        SET c.has_embedding = c.embedding IS NOT NULL
                    } IN TRANSACTIONS OF 1000 ROWS
                """).consume()
                summary["chunks_backfilled"] = pending_chunks

            if include_entity_counts:
                pending_docs = session.run("""
                    MATCH (d:Document)
                    WHERE d.processing_status = 'completed' AND d.entity_count IS NULL
                    RETURN count(d) as n
                """).single()["n"]
                if pending_docs:
                    session.run("""
                        MATCH (d:Document)
                        WHERE d.processing_status = 'completed' AND d.entity_count IS NULL
                        CALL {
                            WITH d
                            OPTIONAL MATCH (d)-[:HAS_CHUNK]->(:Chunk)-[:MENTIONS]->(e:Entity)
                            WITH d, count(DISTINCT e) as ec
                            SET d.entity_count = ec
                        } IN TRANSACTIONS OF 1000 ROWS
                    """).consume()
                    summary["documents_backfilled"] = pending_docs
        return summary

    @retry_on_transient
    def refresh_chunk_count(self, doc_id: str) -> int:
        """Recount actual chunks from the graph and update the document's chunk_count."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (d:Document {id: $id})
                OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:Chunk)
                WITH d, count(c) as actual_count
                SET d.chunk_count = actual_count
                RETURN actual_count
            """, id=doc_id)
            record = result.single()
            return record["actual_count"] if record else 0

    # =========================================================================
    # Custom Input Methods (for manually added Q&A, text, markdown)
    # =========================================================================
    
    def set_custom_input_metadata(
        self,
        doc_id: str,
        input_type: str,
        raw_content: str,
        raw_answer: Optional[str] = None,
        topic_hint: Optional[str] = None
    ):
        """Store custom input metadata for later editing."""
        with self.driver.session() as session:
            session.run("""
                MATCH (d:Document {id: $id})
                SET d.is_custom_input = true,
                    d.custom_input_type = $input_type,
                    d.custom_raw_content = $raw_content,
                    d.custom_raw_answer = $raw_answer,
                    d.custom_topic_hint = $topic_hint
            """,
                id=doc_id,
                input_type=input_type,
                raw_content=raw_content,
                raw_answer=raw_answer,
                topic_hint=topic_hint
            )
    
    @retry_on_transient
    def get_custom_inputs(
        self,
        search: Optional[str] = None,
        limit: int = 50
    ) -> List[dict]:
        """Get all custom inputs with optional search."""
        with self.driver.session() as session:
            if search:
                # Search in filename, raw content, topic hint
                result = session.run("""
                    MATCH (d:Document)
                    WHERE d.is_custom_input = true
                    AND (
                        toLower(d.filename) CONTAINS toLower($search)
                        OR toLower(d.custom_raw_content) CONTAINS toLower($search)
                        OR toLower(d.custom_topic_hint) CONTAINS toLower($search)
                        OR toLower(d.custom_raw_answer) CONTAINS toLower($search)
                    )
                    OPTIONAL MATCH (c:Collection)-[:CONTAINS]->(d)
                    RETURN d.id as id,
                           coalesce(d.filename, '') as filename,
                           coalesce(d.custom_input_type, '') as input_type,
                           coalesce(d.custom_raw_content, '') as content,
                           coalesce(d.custom_raw_answer, '') as answer,
                           coalesce(d.custom_topic_hint, '') as topic_hint,
                           coalesce(d.upload_date, '') as created_at,
                           coalesce(d.processing_status, 'pending') as status,
                           c.id as collection_id,
                           c.name as collection_name
                    ORDER BY d.upload_date DESC
                    LIMIT $limit
                """, search=search, limit=limit)
            else:
                result = session.run("""
                    MATCH (d:Document)
                    WHERE d.is_custom_input = true
                    OPTIONAL MATCH (c:Collection)-[:CONTAINS]->(d)
                    RETURN d.id as id,
                           coalesce(d.filename, '') as filename,
                           coalesce(d.custom_input_type, '') as input_type,
                           coalesce(d.custom_raw_content, '') as content,
                           coalesce(d.custom_raw_answer, '') as answer,
                           coalesce(d.custom_topic_hint, '') as topic_hint,
                           coalesce(d.upload_date, '') as created_at,
                           coalesce(d.processing_status, 'pending') as status,
                           c.id as collection_id,
                           c.name as collection_name
                    ORDER BY d.upload_date DESC
                    LIMIT $limit
                """, limit=limit)
            
            return [dict(record) for record in result]
    
    @retry_on_transient
    def get_custom_input(self, doc_id: str) -> Optional[dict]:
        """Get a single custom input with full data for editing."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (d:Document {id: $id})
                WHERE d.is_custom_input = true
                OPTIONAL MATCH (c:Collection)-[:CONTAINS]->(d)
                RETURN d.id as id,
                       coalesce(d.filename, '') as filename,
                       coalesce(d.custom_input_type, '') as input_type,
                       coalesce(d.custom_raw_content, '') as content,
                       coalesce(d.custom_raw_answer, '') as answer,
                       coalesce(d.custom_topic_hint, '') as topic_hint,
                       coalesce(d.upload_date, '') as created_at,
                       coalesce(d.processing_status, 'pending') as status,
                       c.id as collection_id,
                       c.name as collection_name
            """, id=doc_id)
            
            record = result.single()
            return dict(record) if record else None
    
    @retry_on_transient
    def vector_search(
        self, 
        query_embedding: list[float], 
        top_k: int = 5,
        filters: Optional[dict] = None,
        collection_id: Optional[str] = None,
        allowed_collection_ids: Optional[List[str]] = None
    ) -> list[dict]:
        """Perform vector similarity search, optionally scoped to a collection or list of collections."""
        with self.driver.session() as session:
            # Build the query with optional filters
            filter_clause = ""
            if filters and "file_type" in filters:
                filter_clause = "AND d.file_type = $file_type"
            
            # Collection scoping: filter vector results to collection membership
            collection_clause = ""
            if collection_id:
                collection_clause = "MATCH (col:Collection {id: $collection_id})-[:CONTAINS]->(d)"
            elif allowed_collection_ids:
                collection_clause = "MATCH (col:Collection)-[:CONTAINS]->(d) WHERE col.id IN $allowed_collection_ids"
            
            result = session.run(f"""
                CALL db.index.vector.queryNodes('chunk_embedding', $top_k, $embedding)
                YIELD node as chunk, score
                MATCH (d:Document)-[:HAS_CHUNK]->(chunk)
                WHERE d.processing_status = 'completed' {filter_clause}
                {collection_clause}
                RETURN d.id as document_id,
                       d.filename as filename,
                       chunk.id as chunk_id,
                       chunk.content as content,
                       chunk.chunk_index as chunk_index,
                       score
                ORDER BY score DESC
                LIMIT $top_k
            """,
                embedding=query_embedding,
                top_k=top_k,
                file_type=filters.get("file_type") if filters else None,
                collection_id=collection_id,
                allowed_collection_ids=allowed_collection_ids
            )
            
            return [dict(record) for record in result]
    
    @retry_on_transient
    def get_all_documents(self) -> list[dict]:
        """Get all documents from the knowledge base with collection info."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (d:Document)
                OPTIONAL MATCH (col:Collection)-[:CONTAINS]->(d)
                RETURN d.id as id,
                       coalesce(d.filename, '') as filename,
                       coalesce(d.file_type, '') as file_type,
                       coalesce(d.file_size, 0) as file_size,
                       coalesce(d.file_path, '') as file_path,
                       coalesce(d.upload_date, '') as upload_date,
                       coalesce(d.chunk_count, 0) as chunk_count,
                       coalesce(d.processing_status, 'pending') as processing_status,
                       coalesce(d.error_message, '') as error_message,
                       coalesce(d.progress_current, 0) as progress_current,
                       coalesce(d.progress_total, 0) as progress_total,
                       coalesce(d.progress_message, '') as progress_message,
                       coalesce(d.image_progress_current, 0) as image_progress_current,
                       coalesce(d.image_progress_total, 0) as image_progress_total,
                       coalesce(d.image_progress_message, '') as image_progress_message,
                       col.id as collection_id,
                       col.name as collection_name,
                       coalesce(d.is_custom_input, false) as is_custom_input,
                       coalesce(d.custom_input_type, '') as custom_input_type,
                       coalesce(d.custom_topic_hint, '') as custom_topic_hint,
                       coalesce(d.source, 'upload') as source,
                       coalesce(d.entity_count, -1) as entity_count,
                       coalesce(d.injection_flagged, false) as injection_flagged,
                       coalesce(d.injection_reason, '') as injection_reason,
                       size([(d)-[:HAS_CHUNK]->(uc:Chunk) WHERE uc.has_embedding = false | 1]) as unembedded_chunk_count
                ORDER BY d.upload_date DESC
            """)
            return [dict(record) for record in result]
    
    @retry_on_transient
    def find_document_by_filename_and_size(self, filename: str, file_size: int) -> Optional[dict]:
        """Check if a document with the same filename and file size already exists."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (d:Document)
                WHERE d.filename = $filename AND d.file_size = $file_size
                RETURN d.id as id, d.filename as filename, d.file_size as file_size
                LIMIT 1
            """, filename=filename, file_size=file_size)
            record = result.single()
            return dict(record) if record else None

    @retry_on_transient
    def get_document(self, doc_id: str) -> Optional[dict]:
        """Get a single document by ID with collection info."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (d:Document {id: $id})
                WITH d, size([(d)-[:HAS_CHUNK]->(uc:Chunk) WHERE uc.has_embedding = false | 1]) as unembedded_chunk_count
                OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:Chunk)
                OPTIONAL MATCH (col:Collection)-[:CONTAINS]->(d)
                RETURN d.id as id,
                       coalesce(d.filename, '') as filename,
                       coalesce(d.file_type, '') as file_type,
                       coalesce(d.file_size, 0) as file_size,
                       coalesce(d.file_path, '') as file_path,
                       coalesce(d.upload_date, '') as upload_date,
                       coalesce(d.chunk_count, 0) as chunk_count,
                       coalesce(d.processing_status, 'pending') as processing_status,
                       coalesce(d.error_message, '') as error_message,
                       coalesce(d.progress_current, 0) as progress_current,
                       coalesce(d.progress_total, 0) as progress_total,
                       coalesce(d.progress_message, '') as progress_message,
                       coalesce(d.image_progress_current, 0) as image_progress_current,
                       coalesce(d.image_progress_total, 0) as image_progress_total,
                       coalesce(d.image_progress_message, '') as image_progress_message,
                       coalesce(d.source, 'upload') as source,
                       coalesce(d.entity_count, -1) as entity_count,
                       coalesce(d.injection_flagged, false) as injection_flagged,
                       coalesce(d.injection_reason, '') as injection_reason,
                       unembedded_chunk_count,
                       col.id as collection_id,
                       col.name as collection_name,
                       collect(c.id) as chunk_ids
            """, id=doc_id)
            
            record = result.single()
            return dict(record) if record else None
    
    @retry_on_transient
    def get_documents_file_paths(self, doc_ids: list) -> list:
        """Get file paths, filenames, and collection info for multiple documents by ID."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (d:Document)
                WHERE d.id IN $ids
                OPTIONAL MATCH (col:Collection)-[:CONTAINS]->(d)
                RETURN d.id as id,
                       coalesce(d.filename, '') as filename,
                       coalesce(d.file_path, '') as file_path,
                       col.id as collection_id
            """, ids=doc_ids)
            return [dict(record) for record in result]

    @retry_on_transient
    def get_document_content(self, doc_id: str) -> Optional[dict]:
        """
        Get a document with all its chunk content, ordered by chunk index.
        
        Returns dict with document metadata, collection_id, and full_content (concatenated chunks).
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (d:Document {id: $id})
                OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:Chunk)
                OPTIONAL MATCH (col:Collection)-[:CONTAINS]->(d)
                WITH d, col, c
                ORDER BY c.chunk_index
                WITH d, col, collect({
                    id: c.id,
                    content: c.content,
                    chunk_index: c.chunk_index
                }) as chunks
                RETURN d.id as id,
                       coalesce(d.filename, '') as filename,
                       coalesce(d.file_type, '') as file_type,
                       coalesce(d.file_size, 0) as file_size,
                       coalesce(d.upload_date, '') as upload_date,
                       coalesce(d.chunk_count, 0) as chunk_count,
                       col.id as collection_id,
                       chunks
            """, id=doc_id)
            
            record = result.single()
            if not record:
                return None
            
            doc = dict(record)
            chunks = doc.get("chunks", [])
            
            # Filter out null chunks (when document has no chunks yet)
            valid_chunks = [c for c in chunks if c.get("id") is not None]
            
            # Concatenate all chunk content
            full_content = "\n\n".join([
                c.get("content", "") for c in valid_chunks
            ])
            
            return {
                "id": doc["id"],
                "filename": doc["filename"],
                "file_type": doc["file_type"],
                "file_size": doc["file_size"],
                "upload_date": doc["upload_date"],
                "chunk_count": doc["chunk_count"],
                "collection_id": doc.get("collection_id"),
                "chunks": valid_chunks,
                "full_content": full_content
            }
    
    def delete_document_chunks(self, doc_id: str) -> dict:
        """
        Delete only the chunks and orphaned entities of a document, keeping the document node.
        
        Used for reprocessing documents without losing the document metadata.
        
        Returns:
            Dict with 'chunks_deleted' (int), 'orphaned_entities_removed' (int)
        """
        with self.driver.session() as session:
            # Step 0: Remove relationships this document contributed. Edges carry
            # source_document_id but survive entity-orphan cleanup when their
            # endpoints persist via other documents — clear them explicitly so a
            # reprocess does not leave stale relationships behind.
            session.run("""
                MATCH (:Entity)-[r]->(:Entity)
                WHERE r.source_document_id = $id
                DELETE r
            """, id=doc_id)

            # Step 1: Find entities that will become orphaned after deletion
            orphaned_result = session.run("""
                MATCH (d:Document {id: $id})-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(e:Entity)
                WITH e, collect(DISTINCT c) as doc_chunks

                // Check if entity is mentioned by chunks from OTHER documents
                OPTIONAL MATCH (other_chunk:Chunk)-[:MENTIONS]->(e)
                WHERE NOT other_chunk IN doc_chunks

                WITH e, doc_chunks, collect(other_chunk) as other_chunks
                WHERE size(other_chunks) = 0

                RETURN collect(e.name) as orphaned_entities
            """, id=doc_id)

            orphaned_record = orphaned_result.single()
            orphaned_entities = orphaned_record["orphaned_entities"] if orphaned_record else []

            # Step 2: Delete orphaned entities
            orphaned_count = 0
            if orphaned_entities:
                session.run("""
                    MATCH (e:Entity)
                    WHERE e.name IN $names
                    DETACH DELETE e
                """, names=orphaned_entities)
                orphaned_count = len(orphaned_entities)
                logger.info(f"Deleted {orphaned_count} orphaned entities for document {doc_id}")
            
            # Step 3: Delete only the chunks (not the document)
            result = session.run("""
                MATCH (d:Document {id: $id})-[:HAS_CHUNK]->(c:Chunk)
                WITH collect(c) as chunks
                UNWIND chunks as chunk
                DETACH DELETE chunk
                RETURN count(*) as deleted
            """, id=doc_id)
            
            chunks_deleted = result.single()["deleted"]
            
            # Reset document chunk count
            session.run("""
                MATCH (d:Document {id: $id})
                SET d.chunk_count = 0
            """, id=doc_id)
            
            logger.info(f"Deleted {chunks_deleted} chunks for document {doc_id}")
            
            return {
                "chunks_deleted": chunks_deleted,
                "orphaned_entities_removed": orphaned_count
            }
    
    def delete_document(self, doc_id: str) -> dict:
        """
        Delete a document, its chunks, orphaned entities, and orphaned communities.
        
        Entities are only deleted if they have no other connections to chunks
        from other documents. Communities are deleted if they have no remaining
        member entities. This keeps the Neo4j database clean.
        
        Returns:
            Dict with 'deleted' (bool), 'orphaned_entities_removed' (int), 
            'orphaned_communities_removed' (int)
        """
        with self.driver.session() as session:
            # Step 0: Remove relationships this document contributed (see note in
            # delete_document_chunks) so deletion never leaves stale edges whose
            # endpoint entities survive via other documents.
            session.run("""
                MATCH (:Entity)-[r]->(:Entity)
                WHERE r.source_document_id = $id
                DELETE r
            """, id=doc_id)

            # Step 1: Find entities that will become orphaned after deletion
            # These are entities ONLY mentioned by chunks of this document
            orphaned_result = session.run("""
                MATCH (d:Document {id: $id})-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(e:Entity)
                WITH e, collect(DISTINCT c) as doc_chunks

                // Check if entity is mentioned by chunks from OTHER documents
                OPTIONAL MATCH (other_chunk:Chunk)-[:MENTIONS]->(e)
                WHERE NOT other_chunk IN doc_chunks

                WITH e, doc_chunks, collect(other_chunk) as other_chunks
                WHERE size(other_chunks) = 0

                RETURN collect(e.name) as orphaned_entities
            """, id=doc_id)

            orphaned_record = orphaned_result.single()
            orphaned_entities = orphaned_record["orphaned_entities"] if orphaned_record else []

            # Step 2: Delete orphaned entities (DETACH DELETE removes their relationships too)
            orphaned_entity_count = 0
            if orphaned_entities:
                session.run("""
                    MATCH (e:Entity)
                    WHERE e.name IN $names
                    DETACH DELETE e
                """, names=orphaned_entities)
                orphaned_entity_count = len(orphaned_entities)
                logger.info(f"Deleted {orphaned_entity_count} orphaned entities for document {doc_id}")
            
            # Step 3: Delete orphaned communities (communities with no remaining members)
            orphaned_community_result = session.run("""
                MATCH (com:Community)
                WHERE NOT EXISTS { (com)-[:HAS_MEMBER]->(:Entity) }
                WITH collect(com.id) as orphaned_ids
                MATCH (com:Community)
                WHERE com.id IN orphaned_ids
                DETACH DELETE com
                RETURN size(orphaned_ids) as removed_count
            """)
            orphaned_community_record = orphaned_community_result.single()
            orphaned_community_count = orphaned_community_record["removed_count"] if orphaned_community_record else 0
            
            if orphaned_community_count > 0:
                logger.info(f"Deleted {orphaned_community_count} orphaned communities for document {doc_id}")
            
            # Step 4: Delete document and its chunks
            result = session.run("""
                MATCH (d:Document {id: $id})
                OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:Chunk)
                DETACH DELETE d, c
                RETURN count(d) as deleted
            """, id=doc_id)
            
            deleted = result.single()["deleted"] > 0
            
            if deleted:
                logger.info(f"Deleted document {doc_id} with orphaned entity/community cleanup")
            
            return {
                "deleted": deleted,
                "orphaned_entities_removed": orphaned_entity_count,
                "orphaned_communities_removed": orphaned_community_count
            }
    
    def delete_documents(self, doc_ids: list[str]) -> dict:
        """
        Delete multiple documents, their chunks, orphaned entities, and orphaned communities.
        
        Args:
            doc_ids: List of document IDs to delete
            
        Returns:
            Dict with 'deleted_count' (int), 'orphaned_entities_removed' (int),
            'orphaned_communities_removed' (int)
        """
        total_deleted = 0
        total_orphaned_entities = 0
        total_orphaned_communities = 0
        
        for doc_id in doc_ids:
            result = self.delete_document(doc_id)
            if result["deleted"]:
                total_deleted += 1
                total_orphaned_entities += result["orphaned_entities_removed"]
                total_orphaned_communities += result["orphaned_communities_removed"]
        
        logger.info(f"Bulk deleted {total_deleted} documents, removed {total_orphaned_entities} orphaned entities, {total_orphaned_communities} orphaned communities")
        
        return {
            "deleted_count": total_deleted,
            "orphaned_entities_removed": total_orphaned_entities,
            "orphaned_communities_removed": total_orphaned_communities
        }
    
    def delete_all_documents(self) -> dict:
        """
        Delete all documents, chunks, entities, and communities from the knowledge base.
        
        This is a destructive operation that clears the entire knowledge base.
        
        Returns:
            Dict with 'deleted_count' (int), 'entities_removed' (int), 'communities_removed' (int)
        """
        with self.driver.session() as session:
            # Get counts before deletion
            count_result = session.run("""
                MATCH (d:Document)
                RETURN count(d) as doc_count
            """)
            doc_count = count_result.single()["doc_count"]
            
            entity_result = session.run("""
                MATCH (e:Entity)
                RETURN count(e) as entity_count
            """)
            entity_count = entity_result.single()["entity_count"]
            
            community_result = session.run("""
                MATCH (com:Community)
                RETURN count(com) as community_count
            """)
            community_count = community_result.single()["community_count"]
            
            # Batched deletes: a single DETACH DELETE over the whole graph
            # exceeds dbms.memory.transaction.total.max on large knowledge
            # bases. CALL {} IN TRANSACTIONS requires an auto-commit query
            # (session.run), not an explicit transaction.

            # Delete all communities
            session.run("""
                MATCH (com:Community)
                CALL { WITH com DETACH DELETE com } IN TRANSACTIONS OF 10000 ROWS
            """)

            # Delete all entities (they will all be orphaned)
            session.run("""
                MATCH (e:Entity)
                CALL { WITH e DETACH DELETE e } IN TRANSACTIONS OF 10000 ROWS
            """)

            # Delete all chunks (small batches: chunks carry embedding vectors)
            session.run("""
                MATCH (c:Chunk)
                CALL { WITH c DETACH DELETE c } IN TRANSACTIONS OF 2000 ROWS
            """)

            # Delete all documents
            session.run("""
                MATCH (d:Document)
                CALL { WITH d DETACH DELETE d } IN TRANSACTIONS OF 10000 ROWS
            """)
            
            logger.info(f"Deleted all documents: {doc_count} documents, {entity_count} entities, {community_count} communities")
            
            return {
                "deleted_count": doc_count,
                "entities_removed": entity_count,
                "communities_removed": community_count
            }
    
    def cleanup_orphaned_entities(self) -> int:
        """
        Find and delete all orphaned entities in the database.
        
        An orphaned entity is one that is not mentioned by any chunk.
        This is useful for cleaning up after previous deletions or data inconsistencies.
        
        Returns:
            Number of orphaned entities deleted
        """
        with self.driver.session() as session:
            # Find entities not connected to any chunk
            result = session.run("""
                MATCH (e:Entity)
                WHERE NOT EXISTS { MATCH (:Chunk)-[:MENTIONS]->(e) }
                WITH collect(e) as orphans
                UNWIND orphans as orphan
                DETACH DELETE orphan
                RETURN count(*) as deleted
            """)
            
            deleted_count = result.single()["deleted"]
            
            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} orphaned entities")
            
            return deleted_count
    
    def cleanup_orphaned_communities(self) -> int:
        """
        Find and delete all orphaned communities in the database.
        
        An orphaned community is one that has no member entities (via HAS_MEMBER relationship).
        This is useful for cleaning up after entity deletions or data inconsistencies.
        
        Returns:
            Number of orphaned communities deleted
        """
        with self.driver.session() as session:
            # Find communities not connected to any entity
            result = session.run("""
                MATCH (com:Community)
                WHERE NOT EXISTS { (com)-[:HAS_MEMBER]->(:Entity) }
                WITH collect(com) as orphans
                UNWIND orphans as orphan
                DETACH DELETE orphan
                RETURN count(*) as deleted
            """)
            
            deleted_count = result.single()["deleted"]
            
            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} orphaned communities")
            
            return deleted_count
    
    # =========================================================================
    # GraphRAG: Entity and Relationship Storage
    # =========================================================================
    
    def store_entity(self, entity: Entity, chunk_id: str = None, document_id: str = None) -> str:
        """
        Store an entity with optional chunk and document provenance.
        Uses MERGE to avoid duplicates. Links to chunk via MENTIONS if chunk_id provided.
        """
        with self.driver.session() as session:
            result = session.run("""
                MERGE (e:Entity {name: $name})
                ON CREATE SET
                    e.type = $type,
                    e.description = $description,
                    e.created_at = datetime(),
                    e.source_documents = CASE WHEN $doc_id IS NOT NULL THEN [$doc_id] ELSE [] END,
                    e.extraction_count = 1,
                    e.last_extracted_at = datetime()
                ON MATCH SET
                    e.type = CASE WHEN e.type IS NULL OR e.type = '' THEN $type ELSE e.type END,
                    e.description = CASE WHEN size(e.description) < size($description) THEN $description ELSE e.description END,
                    e.source_documents = CASE
                        WHEN $doc_id IS NOT NULL AND NOT $doc_id IN coalesce(e.source_documents, [])
                        THEN coalesce(e.source_documents, []) + $doc_id
                        ELSE coalesce(e.source_documents, [])
                    END,
                    e.extraction_count = coalesce(e.extraction_count, 0) + 1,
                    e.last_extracted_at = datetime()
                WITH e
                OPTIONAL MATCH (c:Chunk {id: $chunk_id})
                WHERE $chunk_id IS NOT NULL
                FOREACH (_ IN CASE WHEN c IS NOT NULL THEN [1] ELSE [] END |
                    MERGE (c)-[:MENTIONS]->(e)
                )
                RETURN e.name as name
            """,
                name=entity.name,
                type=entity.type,
                description=entity.description,
                chunk_id=chunk_id,
                doc_id=document_id,
            )
            record = result.single()
            return record["name"] if record else entity.name

    def update_entity(self, entity_name: str, new_name: str = None, new_description: str = None) -> dict:
        """
        Update an entity's name and/or description.
        If name changes, the old name is added to aliases and the fulltext index auto-updates.
        All graph edges remain intact since Neo4j edges connect to nodes, not name strings.
        Returns the updated entity data.
        """
        if new_name is not None and new_name.strip() == "":
            raise ValueError("Entity name cannot be empty")

        with self.driver.session() as session:
            # If renaming, check that the new name doesn't already exist
            if new_name and new_name != entity_name:
                existing = session.run(
                    "MATCH (e:Entity {name: $name}) RETURN e.name as name",
                    name=new_name,
                ).single()
                if existing:
                    raise ValueError(f"An entity named '{new_name}' already exists")

            # Build dynamic SET clause
            set_parts = []
            params = {"entity_name": entity_name}

            if new_name and new_name != entity_name:
                set_parts.append("e.name = $new_name")
                set_parts.append("e.aliases = apoc.coll.toSet(coalesce(e.aliases, []) + $old_name)")
                params["new_name"] = new_name
                params["old_name"] = entity_name

            if new_description is not None:
                set_parts.append("e.description = $new_description")
                params["new_description"] = new_description

            if not set_parts:
                raise ValueError("No updates provided")

            query = f"""
                MATCH (e:Entity {{name: $entity_name}})
                SET {', '.join(set_parts)}
                RETURN e.name as name, e.type as type, e.description as description,
                       e.aliases as aliases
            """
            result = session.run(query, **params)
            record = result.single()
            if not record:
                raise ValueError(f"Entity '{entity_name}' not found")

            return {
                "name": record["name"],
                "type": record["type"],
                "description": record["description"],
                "aliases": record["aliases"] or [],
            }

    def get_text_chunks_for_document(self, document_id: str) -> List[dict]:
        """Get all text chunks (non-image) for a document with their IDs and content."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (d:Document {id: $doc_id})-[:HAS_CHUNK]->(c:Chunk)
                WHERE c.chunk_index < 1000
                RETURN c.id as id, c.content as content
                ORDER BY c.chunk_index
            """, doc_id=document_id)
            return [dict(record) for record in result]

    @retry_on_transient
    def link_entity_to_chunk(self, entity_name: str, chunk_id: str) -> bool:
        """Create (Chunk)-[:MENTIONS]->(Entity) relationship."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (c:Chunk {id: $chunk_id})
                MATCH (e:Entity {name: $entity_name})
                MERGE (c)-[:MENTIONS]->(e)
                RETURN e.name as name
            """,
                chunk_id=chunk_id,
                entity_name=entity_name,
            )
            return result.single() is not None
    
    def store_relationship(
        self,
        relationship: Relationship,
        source_document_id: str = None,
        extraction_method: str = "per_document",
    ) -> bool:
        """
        Store a relationship between two entities.
        Creates a dynamic relationship type with weight and provenance.
        """
        # Skip self-referential relationships
        if relationship.source.strip().lower() == relationship.target.strip().lower():
            return False
        with self.driver.session() as session:
            # Use APOC if available, otherwise use a workaround
            try:
                result = session.run("""
                    MATCH (s:Entity {name: $source})
                    MATCH (t:Entity {name: $target})
                    CALL apoc.merge.relationship(s, $rel_type, {}, {description: $description, weight: $weight, confidence: $confidence}, t) YIELD rel
                    SET rel.extracted_at = datetime(),
                        rel.extraction_method = $extraction_method,
                        rel.source_document_id = $source_doc_id
                    RETURN type(rel) as rel_type
                """,
                    source=relationship.source,
                    target=relationship.target,
                    rel_type=relationship.relationship_type,
                    description=relationship.description,
                    weight=relationship.weight,
                    confidence=relationship.confidence,
                    extraction_method=extraction_method,
                    source_doc_id=source_document_id,
                )
                return result.single() is not None
            except Exception as e:
                # Fallback without APOC - use RELATED_TO with type property
                logger.debug(f"APOC not available, using fallback: {e}")
                result = session.run("""
                    MATCH (s:Entity {name: $source})
                    MATCH (t:Entity {name: $target})
                    MERGE (s)-[r:RELATED_TO {type: $rel_type}]->(t)
                    SET r.description = $description, r.weight = $weight,
                        r.confidence = $confidence,
                        r.extracted_at = datetime(),
                        r.extraction_method = $extraction_method,
                        r.source_document_id = $source_doc_id
                    RETURN type(r) as rel_type
                """,
                    source=relationship.source,
                    target=relationship.target,
                    rel_type=relationship.relationship_type,
                    description=relationship.description,
                    weight=relationship.weight,
                    confidence=relationship.confidence,
                    extraction_method=extraction_method,
                    source_doc_id=source_document_id,
                )
                return result.single() is not None
    
    def store_graph_extraction(
        self,
        chunk_id: str,
        extraction: ExtractionResult,
        source_document_id: str = None,
        extraction_method: str = "per_document",
        entity_embeddings: Optional[List[Optional[List[float]]]] = None,
    ) -> dict:
        """
        Store all entities and relationships from an extraction result.

        When `entity_embeddings` is provided (aligned with `extraction.entities`)
        AND `enable_semantic_entity_resolution` is true, each non-None embedding
        routes through `store_entity_with_embedding` (embedding-first dedup with
        Levenshtein fallback). Otherwise entities fall back to fuzzy-only
        `store_entity_with_resolution`. This keeps the image pipeline in sync
        with the per-document text-entity path.

        Returns:
            Dict with counts of stored entities and relationships
        """
        entity_count = 0
        relationship_count = 0

        use_semantic = self.settings.enable_semantic_entity_resolution

        for idx, entity in enumerate(extraction.entities):
            try:
                embedding = (
                    entity_embeddings[idx]
                    if entity_embeddings is not None and idx < len(entity_embeddings)
                    else None
                )
                if embedding and use_semantic:
                    self.store_entity_with_embedding(
                        entity,
                        chunk_id=chunk_id,
                        document_id=source_document_id,
                        embedding=embedding,
                    )
                else:
                    self.store_entity_with_resolution(
                        entity,
                        chunk_id=chunk_id,
                        document_id=source_document_id,
                        similarity_threshold=0.85,
                    )
                entity_count += 1
            except Exception as e:
                logger.warning(f"Failed to store entity {entity.name}: {e}")

        # Then store relationships
        for relationship in extraction.relationships:
            try:
                if self.store_relationship(
                    relationship,
                    source_document_id=source_document_id,
                    extraction_method=extraction_method,
                ):
                    relationship_count += 1
            except Exception as e:
                logger.warning(f"Failed to store relationship {relationship.source} -> {relationship.target}: {e}")
        
        return {"entities": entity_count, "relationships": relationship_count}

    # =========================================================================
    # Phase B: Collection-Level Entity/Relationship Queries
    # =========================================================================

    def get_all_entities_for_document(self, document_id: str) -> List[dict]:
        """Get all entities linked to a document's chunks."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (d:Document {id: $doc_id})-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(e:Entity)
                RETURN DISTINCT e.name as name, e.type as type, e.description as description,
                       e.community_id as community_id, count(DISTINCT c) as mention_count
                ORDER BY mention_count DESC
            """, doc_id=document_id)
            return [dict(record) for record in result]

    def get_all_entities_for_collection(
        self,
        collection_id: Optional[str] = None,
        limit: int = 0,
    ) -> List[dict]:
        """Get all entities in a collection (or globally if collection_id is None).

        Args:
            collection_id: Scope to a specific collection (None = global)
            limit: Max entities to return (0 = no limit, returns all)
        """
        with self.driver.session() as session:
            limit_clause = f"LIMIT {limit}" if limit > 0 else ""
            if collection_id:
                result = session.run(f"""
                    MATCH (col:Collection {{id: $col_id}})-[:CONTAINS]->(d:Document)
                          -[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(e:Entity)
                    RETURN DISTINCT e.name as name, e.type as type,
                           e.description as description,
                           e.community_id as community_id,
                           count(DISTINCT d) as document_count
                    ORDER BY document_count DESC
                    {limit_clause}
                """, col_id=collection_id)
            else:
                result = session.run(f"""
                    MATCH (e:Entity)
                    OPTIONAL MATCH (e)<-[:MENTIONS]-(c:Chunk)<-[:HAS_CHUNK]-(d:Document)
                    RETURN DISTINCT e.name as name, e.type as type,
                           e.description as description,
                           e.community_id as community_id,
                           count(DISTINCT d) as document_count
                    ORDER BY document_count DESC
                    {limit_clause}
                """)
            return [dict(record) for record in result]

    def get_existing_relationships_for_entities(
        self,
        entity_names: List[str],
        max_per_entity: int = 0,
    ) -> List[dict]:
        """Get existing relationships between the given entities.

        Args:
            entity_names: Entity names to query.
            max_per_entity: If > 0, return at most this many relationships per
                source entity (highest-weight first). 0 = no cap.
        """
        if not entity_names:
            return []
        with self.driver.session() as session:
            if max_per_entity > 0:
                # Cap per source entity to prevent hub entities from dominating
                result = session.run("""
                    MATCH (s:Entity)-[r]->(t:Entity)
                    WHERE s.name IN $names AND t.name IN $names
                    WITH s.name as source, t.name as target,
                         coalesce(r.type, type(r)) as type,
                         r.description as description,
                         coalesce(r.weight, 5.0) as weight
                    ORDER BY source, weight DESC
                    WITH source, collect({
                        target: target, type: type,
                        description: description, weight: weight
                    }) as rels
                    WITH source, rels[0..$max_per] as capped_rels
                    UNWIND capped_rels as rel
                    RETURN source, rel.target as target, rel.type as type,
                           rel.description as description, rel.weight as weight
                """, names=entity_names, max_per=max_per_entity)
                # Deduplicate: a relationship A->B may appear via source=A and source=B
                seen = set()
                relationships = []
                for record in result:
                    key = (record["source"], record["target"], record["type"])
                    if key not in seen:
                        seen.add(key)
                        relationships.append(dict(record))
                return relationships
            else:
                result = session.run("""
                    MATCH (s:Entity)-[r]->(t:Entity)
                    WHERE s.name IN $names AND t.name IN $names
                    RETURN s.name as source, t.name as target,
                           coalesce(r.type, type(r)) as type,
                           r.description as description, r.weight as weight
                """, names=entity_names)
                return [dict(record) for record in result]

    def get_entity_degree_map(self, entity_names: List[str]) -> dict:
        """Return {entity_name: relationship_count} for given entities."""
        if not entity_names:
            return {}
        degree_map: dict[str, int] = {}
        batch_size = 500
        for i in range(0, len(entity_names), batch_size):
            batch = entity_names[i:i + batch_size]
            with self.driver.session() as session:
                result = session.run("""
                    MATCH (e:Entity)
                    WHERE e.name IN $names
                    OPTIONAL MATCH (e)-[r]-(:Entity)
                    RETURN e.name as name, count(r) as degree
                """, names=batch)
                for record in result:
                    degree_map[record["name"]] = record["degree"]
        return degree_map

    def create_cooccurrence_relationships(self, min_shared_chunks: int = 2) -> int:
        """Create RELATED_TO relationships between entities that co-occur in chunks.

        Based on FastGraphRAG approach: entities sharing text units are likely related.
        Only creates relationships that don't already exist (MERGE).

        Args:
            min_shared_chunks: Minimum shared chunks to create a relationship.

        Returns:
            Number of relationships created.
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (e1:Entity)<-[:MENTIONS]-(c:Chunk)-[:MENTIONS]->(e2:Entity)
                WHERE id(e1) < id(e2)
                WITH e1, e2, count(DISTINCT c) as shared_chunks
                WHERE shared_chunks >= $min_shared
                MERGE (e1)-[r:RELATED_TO]->(e2)
                ON CREATE SET r.weight = CASE
                    WHEN shared_chunks >= 5 THEN 7.0
                    WHEN shared_chunks >= 3 THEN 5.0
                    ELSE 3.0
                END,
                r.description = 'Co-occurrence: entities appear together in ' + toString(shared_chunks) + ' text chunks',
                r.extraction_method = 'co_occurrence',
                r.extracted_at = datetime()
                RETURN count(r) as created
            """, min_shared=min_shared_chunks)
            record = result.single()
            created = record["created"] if record else 0
            logger.info(f"Co-occurrence seeding: created {created} relationships (min_shared={min_shared_chunks})")
            return created

    def get_entities_without_relationships(self, limit: int = 500) -> List[dict]:
        """Get entities that have no relationships (prime targets for Phase B analysis)."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (e:Entity)
                WHERE NOT EXISTS { (e)-[]-(:Entity) }
                RETURN e.name as name, e.type as type, e.description as description
                LIMIT $limit
            """, limit=limit)
            return [dict(record) for record in result]

    # =========================================================================
    # GraphRAG: Graph Traversal and Retrieval
    # =========================================================================
    
    @retry_on_transient
    def find_entities_by_name(
        self,
        names: List[str],
        allowed_collection_ids: Optional[List[str]] = None
    ) -> List[dict]:
        """
        Find entities by their names (case-insensitive fuzzy match).
        Appends wildcard for prefix matching (e.g. "pol" finds "Polygon").
        Optionally scoped to entities from the given collections.
        """
        if not names:
            return []

        with self.driver.session() as session:
            # Use fulltext search with wildcard prefix matching
            # "pol" -> "pol*" so Lucene matches "polygon", "policy", etc.
            terms = []
            for name in names:
                sanitized = name.replace('"', '\\"').replace('~', '\\~').replace('*', '').strip()
                if sanitized:
                    terms.append(f"{sanitized}*")
            search_query = " OR ".join(terms) if terms else " OR ".join(names)
            try:
                if allowed_collection_ids:
                    result = session.run("""
                        CALL db.index.fulltext.queryNodes('entity_name_fulltext', $search_query)
                        YIELD node, score
                        WHERE EXISTS {
                            MATCH (col:Collection)-[:CONTAINS]->(d:Document)-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(node)
                            WHERE col.id IN $allowed_collection_ids
                        }
                        OPTIONAL MATCH (node)-[r]-()
                        WITH node, score, count(r) as connection_count
                        RETURN node.name as name,
                               node.type as type,
                               node.description as description,
                               score,
                               connection_count
                        ORDER BY connection_count DESC, score DESC
                        LIMIT 20
                    """, search_query=search_query, allowed_collection_ids=allowed_collection_ids)
                else:
                    result = session.run("""
                    CALL db.index.fulltext.queryNodes('entity_name_fulltext', $search_query)
                    YIELD node, score
                    OPTIONAL MATCH (node)-[r]-()
                    WITH node, score, count(r) as connection_count
                    RETURN node.name as name,
                           node.type as type,
                           node.description as description,
                           score,
                           connection_count
                    ORDER BY connection_count DESC, score DESC
                    LIMIT 20
                """, search_query=search_query)
                return [dict(record) for record in result]
            except Exception as e:
                logger.warning(f"Fulltext search failed, using exact match: {e}")
                # Fallback to exact match
                result = session.run("""
                    MATCH (e:Entity)
                    WHERE e.name IN $names
                    OPTIONAL MATCH (e)-[r]-()
                    WITH e, count(r) as connection_count
                    RETURN e.name as name,
                           e.type as type,
                           e.description as description,
                           1.0 as score,
                           connection_count
                    ORDER BY connection_count DESC
                """, names=names)
                return [dict(record) for record in result]
    
    @retry_on_transient
    def traverse_from_entities(
        self,
        entity_names: List[str],
        max_hops: int = 2,
        limit: int = 50,
        collection_id: Optional[str] = None,
        entity_paths_only: bool = False,
        allowed_collection_ids: Optional[List[str]] = None
    ) -> dict:
        """
        Traverse the graph from given entities to find related context.
        Optionally scoped to a specific collection or list of collections.

        Args:
            entity_paths_only: If True, only follow paths where ALL intermediate
                nodes are Entity nodes (excludes paths through Chunks/Documents).
                Use for graph visualization; leave False for RAG context.

        Returns:
            Dict with 'entities', 'relationships', and 'chunks'
        """
        if not entity_names:
            return {"entities": [], "relationships": [], "chunks": []}

        with self.driver.session() as session:
            # Find related entities and relationships within max_hops
            # Note: max_hops must be injected as literal - Neo4j doesn't allow parameters in variable-length patterns

            # Collection scoping for chunks
            collection_clause = ""
            if collection_id:
                collection_clause = "MATCH (col:Collection {id: $collection_id})-[:CONTAINS]->(d)"
            elif allowed_collection_ids:
                collection_clause = "MATCH (col:Collection)-[:CONTAINS]->(d) WHERE col.id IN $allowed_collection_ids"

            # Optionally constrain traversal to Entity-only paths
            path_filter = "WHERE ALL(n IN nodes(path) WHERE n:Entity)" if entity_paths_only else ""

            result = session.run(f"""
                MATCH (start:Entity)
                WHERE start.name IN $entity_names
                CALL {{
                    WITH start
                    MATCH path = (start)-[r*1..{int(max_hops)}]-(related:Entity)
                    {path_filter}
                    RETURN related, relationships(path) as rels
                    LIMIT $limit
                }}
                WITH start, collect(DISTINCT related) as related_entities, 
                     collect(rels) as all_rels
                
                // Get chunks that mention these entities
                OPTIONAL MATCH (c:Chunk)-[:MENTIONS]->(e:Entity)
                WHERE e.name IN $entity_names OR e IN related_entities
                OPTIONAL MATCH (d:Document)-[:HAS_CHUNK]->(c)
                {collection_clause}
                
                RETURN start.name as start_entity,
                       start.type as start_type,
                       start.description as start_description,
                       [e IN related_entities | {{name: e.name, type: e.type, description: e.description}}] as related,
                       collect(DISTINCT {{
                           chunk_id: c.id, 
                           content: c.content, 
                           document_id: d.id,
                           filename: d.filename
                       }}) as chunks
            """, 
                entity_names=entity_names, 
                limit=limit,
                collection_id=collection_id,
                allowed_collection_ids=allowed_collection_ids
            )
            
            entities = []
            relationships = []
            chunks = []
            seen_chunks = set()
            
            for record in result:
                # Add start entity
                entities.append({
                    "name": record["start_entity"],
                    "type": record["start_type"],
                    "description": record["start_description"]
                })
                
                # Add related entities
                for related in record["related"] or []:
                    if related["name"] not in [e["name"] for e in entities]:
                        entities.append(related)
                
                # Add chunks (deduplicated)
                for chunk in record["chunks"] or []:
                    if chunk.get("chunk_id") and chunk["chunk_id"] not in seen_chunks:
                        seen_chunks.add(chunk["chunk_id"])
                        chunks.append(chunk)
            
            # Get relationships between found entities
            entity_name_list = [e["name"] for e in entities]
            if len(entity_name_list) > 1:
                rel_result = session.run("""
                    MATCH (s:Entity)-[r]->(t:Entity)
                    WHERE s.name IN $names AND t.name IN $names
                    RETURN s.name as source,
                           t.name as target,
                           type(r) as relationship_type,
                           r.description as description,
                           r.type as sub_type
                """, names=entity_name_list)
                
                for record in rel_result:
                    relationships.append({
                        "source": record["source"],
                        "target": record["target"],
                        "type": record["sub_type"] or record["relationship_type"],
                        "description": record["description"]
                    })
            
            return {
                "entities": entities[:20],  # Limit entities
                "relationships": relationships[:30],  # Limit relationships
                "chunks": chunks[:10]  # Limit chunks
            }
    
    @retry_on_transient
    def fulltext_search(
        self,
        query_text: str,
        top_k: int = 10,
        collection_id: Optional[str] = None,
        allowed_collection_ids: Optional[List[str]] = None
    ) -> List[dict]:
        """
        Perform full-text keyword search on chunk content, optionally scoped to a collection or list of collections.
        """
        with self.driver.session() as session:
            try:
                # Escape special characters for Lucene query
                escaped_query = query_text.replace('"', '\\"').replace('~', '\\~')
                
                # Collection scoping
                collection_clause = ""
                if collection_id:
                    collection_clause = "MATCH (col:Collection {id: $collection_id})-[:CONTAINS]->(d)"
                elif allowed_collection_ids:
                    collection_clause = "MATCH (col:Collection)-[:CONTAINS]->(d) WHERE col.id IN $allowed_collection_ids"
                
                result = session.run(f"""
                    CALL db.index.fulltext.queryNodes('chunk_content', $search_text)
                    YIELD node as chunk, score
                    MATCH (d:Document)-[:HAS_CHUNK]->(chunk)
                    WHERE d.processing_status = 'completed'
                    {collection_clause}
                    RETURN d.id as document_id,
                           d.filename as filename,
                           chunk.id as chunk_id,
                           chunk.content as content,
                           chunk.chunk_index as chunk_index,
                           score
                    ORDER BY score DESC
                    LIMIT $top_k
                """, search_text=escaped_query, top_k=top_k, collection_id=collection_id,
                    allowed_collection_ids=allowed_collection_ids)
                
                return [dict(record) for record in result]
            except Exception as e:
                logger.warning(f"Fulltext search failed: {e}")
                return []
    
    @retry_on_transient
    def metadata_search(
        self,
        query_text: str,
        top_k: int = 10,
        collection_id: Optional[str] = None,
        allowed_collection_ids: Optional[List[str]] = None
    ) -> List[dict]:
        """
        Search documents by filename, topic hint, or raw content (for custom inputs).
        Returns chunks from matching documents with high relevance score.
        Optionally scoped to a specific collection or list of collections.
        """
        with self.driver.session() as session:
            try:
                search_lower = query_text.lower().strip()

                # Collection scoping
                collection_clause = ""
                if collection_id:
                    collection_clause = "MATCH (col:Collection {id: $collection_id})-[:CONTAINS]->(d)"
                elif allowed_collection_ids:
                    collection_clause = "MATCH (col:Collection)-[:CONTAINS]->(d) WHERE col.id IN $allowed_collection_ids"
                
                # Search in document metadata
                result = session.run(f"""
                    MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)
                    WHERE d.processing_status = 'completed'
                    AND (
                        toLower(d.filename) CONTAINS $search_term
                        OR toLower(d.custom_topic_hint) CONTAINS $search_term
                        OR (d.is_custom_input = true AND toLower(d.custom_raw_content) CONTAINS $search_term)
                    )
                    {collection_clause}
                    WITH d, c, 
                         CASE 
                             WHEN toLower(d.filename) CONTAINS $search_term THEN 3.0
                             WHEN toLower(d.custom_topic_hint) CONTAINS $search_term THEN 2.5
                             ELSE 2.0
                         END as relevance_score
                    RETURN d.id as document_id,
                           d.filename as filename,
                           c.id as chunk_id,
                           c.content as content,
                           c.chunk_index as chunk_index,
                           relevance_score as score
                    ORDER BY relevance_score DESC, c.chunk_index ASC
                    LIMIT $top_k
                """, search_term=search_lower, top_k=top_k,
                    collection_id=collection_id,
                    allowed_collection_ids=allowed_collection_ids)
                
                return [dict(record) for record in result]
            except Exception as e:
                logger.warning(f"Metadata search failed: {e}")
                return []
    
    @retry_on_transient
    def simple_hybrid_search(
        self,
        query_embedding: List[float],
        query_text: str,
        top_k: int = 10,
        vector_weight: float = 0.5,
        keyword_weight: float = 0.3,
        metadata_weight: float = 0.2,
        collection_id: Optional[str] = None,
        allowed_collection_ids: Optional[List[str]] = None
    ) -> List[dict]:
        """
        Simple hybrid search combining vector + keyword + metadata search.
        Uses RRF to merge results from all three sources.
        Optionally scoped to a specific collection or list of collections.
        """
        # 1. Vector search (semantic similarity)
        vector_results = self.vector_search(query_embedding, top_k * 2,
                                            collection_id=collection_id,
                                            allowed_collection_ids=allowed_collection_ids)
        
        # 2. Keyword/full-text search (content matching)
        keyword_results = self.fulltext_search(query_text, top_k * 2,
                                               collection_id=collection_id,
                                               allowed_collection_ids=allowed_collection_ids)
        
        # 3. Metadata search (filename, topic hint)
        metadata_results = self.metadata_search(query_text, top_k * 2,
                                                collection_id=collection_id,
                                                allowed_collection_ids=allowed_collection_ids)
        
        # 4. Combine using RRF
        combined = self._reciprocal_rank_fusion(
            [vector_results, keyword_results, metadata_results],
            [vector_weight, keyword_weight, metadata_weight]
        )
        
        return combined[:top_k]
    
    def _reciprocal_rank_fusion(
        self,
        result_lists: List[List[dict]],
        weights: List[float],
        k: int = 60
    ) -> List[dict]:
        """
        Apply Reciprocal Rank Fusion to combine multiple ranked lists.
        
        RRF score = sum(weight_i / (k + rank_i)) for each list
        """
        scores = {}
        all_results = {}
        
        for list_idx, (results, weight) in enumerate(zip(result_lists, weights)):
            for rank, result in enumerate(results):
                chunk_id = result.get("chunk_id", "")
                if not chunk_id:
                    continue
                
                # Calculate RRF score for this result in this list
                rrf_score = weight / (k + rank + 1)
                
                if chunk_id not in scores:
                    scores[chunk_id] = 0
                    all_results[chunk_id] = result
                
                scores[chunk_id] += rrf_score
        
        # Sort by combined RRF score
        sorted_chunks = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        
        # Build final results with combined scores
        final_results = []
        for chunk_id, rrf_score in sorted_chunks:
            result = all_results[chunk_id].copy()
            result["score"] = rrf_score
            result["rrf_score"] = rrf_score
            final_results.append(result)
        
        return final_results
    
    @retry_on_transient
    def hybrid_search_rrf(
        self,
        query_embedding: List[float],
        query_text: str,
        entity_names: List[str],
        top_k: int = 5,
        max_hops: int = 2,
        vector_weight: float = 0.5,
        keyword_weight: float = 0.3,
        graph_weight: float = 0.2,
        collection_id: Optional[str] = None,
        allowed_collection_ids: Optional[List[str]] = None
    ) -> dict:
        """
        Perform hybrid search with Reciprocal Rank Fusion.
        Combines: vector similarity + full-text keyword + graph traversal
        Optionally scoped to a specific collection or list of collections.
        
        Returns:
            Dict with 'results' (RRF-fused) and 'graph_context'
        """
        # 1. Vector search
        vector_results = self.vector_search(query_embedding, top_k * 3, collection_id=collection_id,
                                            allowed_collection_ids=allowed_collection_ids)
        
        # 2. Keyword/full-text search
        keyword_results = self.fulltext_search(query_text, top_k * 3, collection_id=collection_id,
                                               allowed_collection_ids=allowed_collection_ids)
        
        # 3. Graph traversal for context
        graph_context = self.traverse_from_entities(entity_names, max_hops, collection_id=collection_id,
                                                    allowed_collection_ids=allowed_collection_ids)
        
        # 4. Get chunks from graph context
        graph_chunks = graph_context.get("chunks", [])
        # Convert graph chunks to same format as vector results
        graph_chunk_results = []
        for chunk in graph_chunks:
            if chunk.get("chunk_id"):
                graph_chunk_results.append({
                    "document_id": chunk.get("document_id", ""),
                    "filename": chunk.get("filename", ""),
                    "chunk_id": chunk.get("chunk_id", ""),
                    "content": chunk.get("content", ""),
                    "chunk_index": 0,
                    "score": 1.0  # Default score for graph results
                })
        
        # 5. Apply RRF fusion
        result_lists = [vector_results, keyword_results, graph_chunk_results]
        weights = [vector_weight, keyword_weight, graph_weight]
        
        fused_results = self._reciprocal_rank_fusion(result_lists, weights)
        
        return {
            "results": fused_results[:top_k],
            "graph_context": graph_context,
            "vector_count": len(vector_results),
            "keyword_count": len(keyword_results),
            "graph_chunk_count": len(graph_chunk_results)
        }
    
    @retry_on_transient
    def hybrid_search(
        self,
        query_embedding: List[float],
        entity_names: List[str],
        top_k: int = 5,
        max_hops: int = 2
    ) -> dict:
        """
        Perform hybrid search combining vector similarity and graph traversal.
        (Legacy method - use hybrid_search_rrf for better results)
        
        Returns:
            Dict with 'vector_results' and 'graph_context'
        """
        # Vector search
        vector_results = self.vector_search(query_embedding, top_k)
        
        # Graph traversal
        graph_context = self.traverse_from_entities(entity_names, max_hops)
        
        return {
            "vector_results": vector_results,
            "graph_context": graph_context
        }
    
    # =========================================================================
    # Entity Resolution with Fuzzy Matching
    # =========================================================================
    
    def find_similar_entities(
        self,
        entity_name: str,
        threshold: float = 0.85
    ) -> List[dict]:
        """
        Find entities with similar names using Levenshtein distance.

        With `entity_dedup_prefilter` enabled, candidates come from the
        entity fulltext index (top 50) and only those are Levenshtein-scored —
        O(50) instead of a full Entity label scan per lookup. Falls back to
        the full scan if the fulltext query fails.
        """
        if self.settings.entity_dedup_prefilter:
            candidates = self._find_similar_entities_prefiltered(
                entity_name, threshold
            )
            if candidates is not None:
                return candidates
        with self.driver.session() as session:
            try:
                # Use APOC for string similarity if available
                result = session.run("""
                    MATCH (e:Entity)
                    WITH e, apoc.text.levenshteinSimilarity(toLower(e.name), toLower($name)) as similarity
                    WHERE similarity >= $threshold
                    RETURN e.name as name, e.type as type, e.description as description, similarity
                    ORDER BY similarity DESC
                    LIMIT 5
                """, name=entity_name, threshold=threshold)
                return [dict(record) for record in result]
            except Exception as e:
                logger.debug(f"APOC similarity not available, using exact match: {e}")
                # Fallback: exact match only
                result = session.run("""
                    MATCH (e:Entity)
                    WHERE toLower(e.name) = toLower($name)
                    RETURN e.name as name, e.type as type, e.description as description, 1.0 as similarity
                """, name=entity_name)
                return [dict(record) for record in result]

    def _find_similar_entities_prefiltered(
        self,
        entity_name: str,
        threshold: float,
    ) -> Optional[List[dict]]:
        """Fulltext-prefiltered Levenshtein lookup.

        Returns None when the fulltext path errored (caller falls back to the
        full scan). An empty list is a real "no candidates" result.
        """
        # Lucene query: escape special chars, then OR the name terms so
        # multi-word entities still match on partial term hits.
        sanitized = "".join(
            c if c.isalnum() else " " for c in entity_name
        ).strip()
        if not sanitized:
            return None
        query = " OR ".join(sanitized.split())
        with self.driver.session() as session:
            try:
                result = session.run("""
                    CALL db.index.fulltext.queryNodes(
                        'entity_name_fulltext', $query, {limit: 50}
                    )
                    YIELD node
                    WITH node,
                         apoc.text.levenshteinSimilarity(
                             toLower(node.name), toLower($name)
                         ) as similarity
                    WHERE similarity >= $threshold
                    RETURN node.name as name, node.type as type,
                           node.description as description, similarity
                    ORDER BY similarity DESC
                    LIMIT 5
                """, query=query, name=entity_name, threshold=threshold)
                return [dict(record) for record in result]
            except Exception as e:
                logger.debug(
                    f"Fulltext dedup prefilter failed, falling back to full "
                    f"scan: {e}"
                )
                return None
    
    @retry_on_transient
    def get_chunk_context_for_entities(
        self,
        entity_names: List[str],
        max_chunks: int = 10,
        max_content_length: int = 500,
        token_budget: int = 0,
    ) -> str:
        """Retrieve the most relevant chunk text for a set of entities.

        Prioritizes chunks that mention the most entities in the batch
        (co-mention chunks), as these are most likely to contain
        relationship-relevant context.

        Args:
            entity_names: Entity names to find context for
            max_chunks: Max number of chunks to return (used when token_budget=0)
            max_content_length: Max chars per chunk (used when token_budget=0)
            token_budget: If > 0, dynamically fill chunks up to this token count,
                          overriding max_chunks and max_content_length.
        """
        if not entity_names:
            return ""

        # When token budget is provided, fetch more chunks and fill to budget
        fetch_limit = max_chunks if token_budget <= 0 else min(200, max(50, token_budget // 20))
        content_limit = max_content_length if token_budget <= 0 else 2000

        with self.driver.session() as session:
            # Fetch chunks with the entities they mention (from our batch).
            # We collect entity names per chunk so we can diversify selection
            # instead of always picking chunks dominated by hub entities.
            result = session.run("""
                MATCH (c:Chunk)-[:MENTIONS]->(e:Entity)
                WHERE e.name IN $entity_names
                WITH c, collect(DISTINCT e.name) as mentioned_entities, count(DISTINCT e) as mention_count
                ORDER BY mention_count DESC
                LIMIT $max_chunks
                RETURN c.content as content, mention_count, mentioned_entities
            """, entity_names=entity_names, max_chunks=fetch_limit)

            # Collect all candidate chunks
            candidate_chunks = []
            for record in result:
                content = (record["content"] or "")[:content_limit]
                if content:
                    candidate_chunks.append({
                        "content": content,
                        "mention_count": record["mention_count"],
                        "mentioned_entities": set(record["mentioned_entities"]),
                    })

            # Greedy selection: pick chunks that maximize entity coverage diversity.
            # At each step, pick the chunk covering the most uncovered entities.
            # This prevents hub-entity chunks from monopolizing the context.
            chunks = []
            total_chars = 0
            char_budget = token_budget * 3 if token_budget > 0 else 0
            covered_entities: set = set()
            used_indices: set = set()

            while len(used_indices) < len(candidate_chunks):
                best_idx = -1
                best_new_coverage = -1
                best_mention_count = -1

                for i, c in enumerate(candidate_chunks):
                    if i in used_indices:
                        continue
                    new_coverage = len(c["mentioned_entities"] - covered_entities)
                    # Prefer chunks covering new entities; break ties by mention_count
                    if (new_coverage > best_new_coverage or
                        (new_coverage == best_new_coverage and c["mention_count"] > best_mention_count)):
                        best_idx = i
                        best_new_coverage = new_coverage
                        best_mention_count = c["mention_count"]

                if best_idx < 0:
                    break

                content = candidate_chunks[best_idx]["content"]
                if char_budget > 0:
                    if total_chars + len(content) > char_budget:
                        remaining = char_budget - total_chars
                        if remaining >= 200:
                            chunks.append(content[:remaining])
                        break
                    total_chars += len(content)

                chunks.append(content)
                used_indices.add(best_idx)
                covered_entities.update(candidate_chunks[best_idx]["mentioned_entities"])

            return "\n---\n".join(chunks) if chunks else ""

    def get_entity_co_occurrence(
        self,
        entity_names: List[str],
    ) -> dict:
        """Get chunk co-occurrence map for entities.

        Returns a dict mapping entity name -> set of chunk IDs where it appears.
        Used for co-occurrence-based batching in relationship analysis.
        """
        if not entity_names:
            return {}

        co_occurrence: dict[str, set] = {}
        # Process in batches to avoid huge Cypher parameter lists
        batch_size = 500
        for i in range(0, len(entity_names), batch_size):
            batch = entity_names[i:i + batch_size]
            with self.driver.session() as session:
                result = session.run("""
                    MATCH (c:Chunk)-[:MENTIONS]->(e:Entity)
                    WHERE e.name IN $names
                    RETURN e.name as name, collect(DISTINCT c.id) as chunk_ids
                """, names=batch)
                for record in result:
                    name = record["name"]
                    chunk_ids = record["chunk_ids"] or []
                    co_occurrence[name] = set(chunk_ids)

        return co_occurrence

    def get_entities_missing_embedding(self, entity_names: List[str]) -> List[dict]:
        """Return {name, type, description} for entities without a usable embedding.

        Used by targeted Phase B discovery to backfill vectors before the
        kNN candidate scan (entities ingested before semantic resolution was
        enabled, or whose embedding call failed at ingest time). An embedding
        whose dimension doesn't match the configured index is treated as
        missing — after an embedding-model switch, entities merged into from
        the old graph keep stale vectors that the recreated index can't serve.
        """
        if not entity_names:
            return []
        missing: List[dict] = []
        batch_size = 500
        for i in range(0, len(entity_names), batch_size):
            batch = entity_names[i:i + batch_size]
            with self.driver.session() as session:
                result = session.run("""
                    MATCH (e:Entity)
                    WHERE e.name IN $names
                      AND (e.embedding IS NULL OR size(e.embedding) <> $dim)
                    RETURN e.name as name, e.type as type, e.description as description
                """, names=batch, dim=self.settings.embedding_dimension)
                missing.extend(dict(record) for record in result)
        return missing

    def set_entity_embeddings_bulk(self, rows: List[dict]) -> int:
        """Set `embedding` on entities in bulk. rows: [{name, embedding}]."""
        if not rows:
            return 0
        updated = 0
        batch_size = 200  # embedding vectors are large — keep transactions bounded
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            with self.driver.session() as session:
                result = session.run("""
                    UNWIND $rows AS row
                    MATCH (e:Entity {name: row.name})
                    SET e.embedding = row.embedding
                    RETURN count(e) as cnt
                """, rows=batch)
                record = result.single()
                updated += record["cnt"] if record else 0
        return updated

    def get_knn_candidate_pairs(
        self,
        entity_names: List[str],
        k: int = 8,
        min_similarity: float = 0.80,
    ) -> List[tuple]:
        """Vector-index kNN candidate pairs for targeted relationship discovery.

        For each entity (with an embedding), queries the `entity_embedding`
        vector index for its k nearest neighbors and keeps pairs that are not
        already connected by any relationship.

        Returns:
            List of (source_name, target_name, index_score) tuples,
            deduplicated across directions.
        """
        if not entity_names:
            return []
        pairs: List[tuple] = []
        seen: set = set()
        failed_batches = 0
        batch_size = 250
        for i in range(0, len(entity_names), batch_size):
            batch = entity_names[i:i + batch_size]
            try:
                with self.driver.session() as session:
                    # size() guard: a stale wrong-dimension embedding (from a
                    # model switch) makes queryNodes throw and would otherwise
                    # kill the whole kNN phase for one bad vector.
                    result = session.run("""
                        MATCH (e:Entity)
                        WHERE e.name IN $names AND e.embedding IS NOT NULL
                          AND size(e.embedding) = $dim
                        CALL db.index.vector.queryNodes('entity_embedding', $k, e.embedding)
                        YIELD node, score
                        WHERE node:Entity AND node.name <> e.name
                          AND score >= $min_sim
                          AND NOT (e)-[]-(node)
                        RETURN e.name as source, node.name as target, score
                    """, names=batch, k=k + 1, min_sim=min_similarity,
                        dim=self.settings.embedding_dimension)
                    for record in result:
                        key = tuple(sorted((record["source"].lower(), record["target"].lower())))
                        if key not in seen:
                            seen.add(key)
                            pairs.append((record["source"], record["target"], record["score"]))
            except Exception as e:
                failed_batches += 1
                logger.warning(
                    f"kNN candidate batch {i // batch_size + 1} failed "
                    f"({len(batch)} entities) — skipping batch: {e}"
                )
        if failed_batches:
            logger.warning(
                f"kNN candidate scan: {failed_batches} batch(es) skipped; "
                f"co-mention candidates still cover those entities"
            )
        return pairs

    def get_doc_cooccurrence_pairs(
        self,
        entity_names: List[str],
        min_shared_docs: int = 2,
        doc_freq_cap: int = 30,
        max_pairs_per_batch: int = 5000,
    ) -> List[tuple]:
        """Document co-mention candidate pairs (no LLM).

        Finds unconnected entity pairs mentioned together in at least
        `min_shared_docs` distinct documents. Entities appearing in more than
        `doc_freq_cap` documents are skipped as anchors (hub guard — they
        co-occur with everything and would dominate both the query cost and
        the candidate budget).

        Returns:
            List of (source_name, target_name, shared_doc_count) tuples.
        """
        if not entity_names or min_shared_docs <= 0:
            return []
        pairs: List[tuple] = []
        seen: set = set()
        batch_size = 200
        for i in range(0, len(entity_names), batch_size):
            batch = entity_names[i:i + batch_size]
            with self.driver.session() as session:
                result = session.run("""
                    MATCH (e1:Entity) WHERE e1.name IN $names
                    MATCH (e1)<-[:MENTIONS]-(:Chunk)<-[:HAS_CHUNK]-(d:Document)
                    WITH e1, collect(DISTINCT d) as docs
                    WHERE size(docs) >= $min_shared AND size(docs) <= $freq_cap
                    UNWIND docs as d
                    MATCH (d)-[:HAS_CHUNK]->(:Chunk)-[:MENTIONS]->(e2:Entity)
                    WHERE e1.name < e2.name
                    WITH e1, e2, count(DISTINCT d) as shared
                    WHERE shared >= $min_shared AND NOT (e1)-[]-(e2)
                    RETURN e1.name as source, e2.name as target, shared
                    ORDER BY shared DESC
                    LIMIT $max_pairs
                """, names=batch, min_shared=min_shared_docs,
                    freq_cap=doc_freq_cap, max_pairs=max_pairs_per_batch)
                for record in result:
                    key = tuple(sorted((record["source"].lower(), record["target"].lower())))
                    if key not in seen:
                        seen.add(key)
                        pairs.append((record["source"], record["target"], record["shared"]))
        return pairs

    def get_relationship_count(self) -> int:
        """Get total count of Entity-Entity relationships."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (s:Entity)-[r]->(t:Entity)
                RETURN count(r) as cnt
            """)
            record = result.single()
            return record["cnt"] if record else 0

    def store_entity_with_resolution(
        self,
        entity: Entity,
        chunk_id: str = None,
        document_id: str = None,
        similarity_threshold: float = 0.85
    ) -> str:
        """
        Store entity with fuzzy deduplication.
        Merges with existing similar entities if found.
        """
        # First, check for similar existing entities
        similar = self.find_similar_entities(entity.name, similarity_threshold)

        if similar and similar[0]["similarity"] >= similarity_threshold:
            # Merge into existing entity
            canonical_name = similar[0]["name"]

            # Add alias if names are different
            if canonical_name.lower() != entity.name.lower():
                self._add_entity_alias(canonical_name, entity.name)

            # Link to chunk if provided
            if chunk_id:
                with self.driver.session() as session:
                    session.run("""
                        MATCH (e:Entity {name: $name})
                        MATCH (c:Chunk {id: $chunk_id})
                        MERGE (c)-[:MENTIONS]->(e)
                    """, name=canonical_name, chunk_id=chunk_id)

            # Update document provenance if provided
            if document_id:
                with self.driver.session() as session:
                    session.run("""
                        MATCH (e:Entity {name: $name})
                        SET e.source_documents = CASE
                            WHEN NOT $doc_id IN coalesce(e.source_documents, [])
                            THEN coalesce(e.source_documents, []) + $doc_id
                            ELSE coalesce(e.source_documents, [])
                        END,
                        e.extraction_count = coalesce(e.extraction_count, 0) + 1,
                        e.last_extracted_at = datetime()
                    """, name=canonical_name, doc_id=document_id)

            return canonical_name

        # No similar entity, create new
        return self.store_entity(entity, chunk_id=chunk_id, document_id=document_id)
    
    def _add_entity_alias(self, canonical_name: str, alias: str):
        """Add an alias for an entity."""
        with self.driver.session() as session:
            try:
                session.run("""
                    MATCH (e:Entity {name: $canonical})
                    SET e.aliases = CASE 
                        WHEN e.aliases IS NULL THEN [$alias]
                        WHEN NOT $alias IN e.aliases THEN e.aliases + $alias
                        ELSE e.aliases
                    END
                """, canonical=canonical_name, alias=alias)
            except Exception as e:
                logger.debug(f"Failed to add entity alias: {e}")
    
    @retry_on_transient
    def get_graph_visualization_data(
        self,
        limit: int = 100,
        include_neighbors: bool = True,
        allowed_collection_ids: Optional[List[str]] = None
    ) -> dict:
        """
        Get data for visualizing the knowledge graph.
        
        This method fetches entities and ALL their relationships in both directions,
        optionally expanding to include neighbor entities to show more graph structure.
        
        Args:
            limit: Maximum number of core entities to fetch (based on mention count).
                   Use 0 or negative to fetch ALL entities.
            include_neighbors: If True, expands entity set to include 1-hop neighbors
            allowed_collection_ids: If provided, scope entities to those mentioned in 
                   documents from these collections (4-hop pattern)
            
        Returns:
            Dict with 'nodes', 'edges', and metadata for visualization
        """
        with self.driver.session() as session:
            # Step 1: Get top entities by mention count (core entities)
            # If limit <= 0, fetch all entities (no limit)
            fetch_all = limit <= 0
            
            # Collection scoping: 4-hop pattern for entity filtering
            collection_match = ""
            collection_where = ""
            if allowed_collection_ids:
                collection_match = """
                    MATCH (col:Collection)-[:CONTAINS]->(d:Document)-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(e)
                    WHERE col.id IN $allowed_collection_ids
                    WITH DISTINCT e
                """
            
            if fetch_all:
                # No LIMIT clause - fetch all entities
                if allowed_collection_ids:
                    result = session.run(f"""
                        {collection_match}
                        OPTIONAL MATCH (c2:Chunk)-[:MENTIONS]->(e)
                        WITH e, count(c2) as mention_count
                        OPTIONAL MATCH (e)-[r]-(:Entity)
                        WITH e, mention_count, count(r) as degree
                        RETURN e.name as id,
                               e.name as label,
                               e.type as type,
                               e.description as description,
                               e.community_id as community_id,
                               mention_count
                        ORDER BY mention_count DESC
                    """, allowed_collection_ids=allowed_collection_ids)
                else:
                    result = session.run("""
                        MATCH (e:Entity)
                        OPTIONAL MATCH (c:Chunk)-[:MENTIONS]->(e)
                        WITH e, count(c) as mention_count
                        OPTIONAL MATCH (e)-[r]-(:Entity)
                        WITH e, mention_count, count(r) as degree
                        RETURN e.name as id,
                               e.name as label,
                               e.type as type,
                               e.description as description,
                               e.community_id as community_id,
                               mention_count
                        ORDER BY mention_count DESC
                    """)
            else:
                # Diversity score: penalize high-degree hubs so the default
                # view shows a diverse set of entities, not just the most connected ones.
                if allowed_collection_ids:
                    result = session.run(f"""
                        {collection_match}
                        OPTIONAL MATCH (c2:Chunk)-[:MENTIONS]->(e)
                        WITH e, count(c2) as mention_count
                        OPTIONAL MATCH (e)-[r]-(:Entity)
                        WITH e, mention_count, count(r) as degree
                        WITH e, mention_count, degree,
                             CASE WHEN degree = 0 THEN mention_count * 1.0
                                  ELSE mention_count * 1.0 / (1.0 + log(1.0 + toFloat(degree)))
                             END as diversity_score
                        RETURN e.name as id,
                               e.name as label,
                               e.type as type,
                               e.description as description,
                               e.community_id as community_id,
                               mention_count
                        ORDER BY diversity_score DESC
                        LIMIT $limit
                    """, limit=limit, allowed_collection_ids=allowed_collection_ids)
                else:
                    result = session.run("""
                        MATCH (e:Entity)
                        OPTIONAL MATCH (c:Chunk)-[:MENTIONS]->(e)
                        WITH e, count(c) as mention_count
                        OPTIONAL MATCH (e)-[r]-(:Entity)
                        WITH e, mention_count, count(r) as degree
                        WITH e, mention_count, degree,
                             CASE WHEN degree = 0 THEN mention_count * 1.0
                                  ELSE mention_count * 1.0 / (1.0 + log(1.0 + toFloat(degree)))
                             END as diversity_score
                        RETURN e.name as id,
                               e.name as label,
                               e.type as type,
                               e.description as description,
                               e.community_id as community_id,
                               mention_count
                        ORDER BY diversity_score DESC
                        LIMIT $limit
                    """, limit=limit)
            
            core_nodes = [dict(record) for record in result]
            core_node_ids = {n["id"] for n in core_nodes}
            
            if not core_nodes:
                return {"nodes": [], "edges": [], "stats": {"total_entities": 0, "total_relationships": 0}}
            
            # Step 2: Get ALL relationships involving core entities (both directions)
            # Key improvement - fetch relationships where
            # either source OR target is in our entity set
            edge_limit = None if fetch_all else limit * 5
            
            if fetch_all:
                # No edge limit - fetch all relationships
                rel_result = session.run("""
                    MATCH (s:Entity)-[r]->(t:Entity)
                    WHERE s.name IN $node_ids OR t.name IN $node_ids
                    RETURN s.name as source,
                           t.name as target,
                           type(r) as rel_type,
                           r.type as sub_type,
                           r.description as description,
                           coalesce(r.weight, 5.0) as weight
                    ORDER BY weight DESC
                """, node_ids=list(core_node_ids))
            else:
                rel_result = session.run("""
                    MATCH (s:Entity)-[r]->(t:Entity)
                    WHERE s.name IN $node_ids OR t.name IN $node_ids
                    RETURN s.name as source,
                           t.name as target,
                           type(r) as rel_type,
                           r.type as sub_type,
                           r.description as description,
                           coalesce(r.weight, 5.0) as weight
                    ORDER BY weight DESC
                    LIMIT $edge_limit
                """, node_ids=list(core_node_ids), edge_limit=edge_limit)
            
            edges = []
            neighbor_ids = set()
            
            for record in rel_result:
                source = record["source"]
                target = record["target"]
                
                # Track neighbors (entities connected but not in core set)
                if source not in core_node_ids:
                    neighbor_ids.add(source)
                if target not in core_node_ids:
                    neighbor_ids.add(target)
                
                edges.append({
                    "source": source,
                    "target": target,
                    "type": record["sub_type"] or record["rel_type"],
                    "description": record["description"],
                    "weight": record["weight"]
                })
            
            # Step 3: Optionally fetch neighbor entity details for complete graph
            all_nodes = list(core_nodes)
            
            if include_neighbors and neighbor_ids:
                # For fetch_all mode, include all neighbors; otherwise limit to prevent explosion
                if fetch_all:
                    neighbor_list = list(neighbor_ids)
                else:
                    neighbor_limit = min(len(neighbor_ids), max(limit // 2, 50))
                    neighbor_list = list(neighbor_ids)[:neighbor_limit]
                
                neighbor_result = session.run("""
                    MATCH (e:Entity)
                    WHERE e.name IN $names
                    OPTIONAL MATCH (c:Chunk)-[:MENTIONS]->(e)
                    WITH e, count(c) as mention_count
                    RETURN e.name as id,
                           e.name as label,
                           e.type as type,
                           e.description as description,
                           e.community_id as community_id,
                           mention_count
                """, names=neighbor_list)
                
                for record in neighbor_result:
                    all_nodes.append(dict(record))
            
            # Step 4: Filter edges to only include those between final node set
            all_node_ids = {n["id"] for n in all_nodes}
            filtered_edges = [
                e for e in edges 
                if e["source"] in all_node_ids and e["target"] in all_node_ids
            ]
            
            # Get total counts for stats
            stats_result = session.run("""
                MATCH (e:Entity) 
                WITH count(e) as entity_count
                OPTIONAL MATCH (:Entity)-[r]->(:Entity)
                WHERE type(r) <> 'MENTIONS'
                RETURN entity_count, count(r) as rel_count
            """)
            stats_record = stats_result.single()
            
            return {
                "nodes": all_nodes,
                "edges": filtered_edges,
                "stats": {
                    "displayed_entities": len(all_nodes),
                    "displayed_relationships": len(filtered_edges),
                    "total_entities": stats_record["entity_count"] if stats_record else 0,
                    "total_relationships": stats_record["rel_count"] if stats_record else 0,
                    "neighbor_entities_included": len(neighbor_ids) if include_neighbors else 0
                }
            }
    
    # =========================================================================
    # Entity Merge & Deduplication
    # =========================================================================

    def get_entity_descriptions(self, entity_names: List[str]) -> dict:
        """Get the name, type, and description for a list of entity names."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (e:Entity)
                WHERE e.name IN $names
                RETURN e.name as name, e.type as type, coalesce(e.description, '') as description
            """, names=entity_names)
            return {r["name"]: {"type": r["type"], "description": r["description"]} for r in result}

    def merge_entities(self, canonical_name: str, merge_names: List[str], merged_description: Optional[str] = None) -> dict:
        """
        Merge multiple entities into one canonical entity.

        For each entity being merged:
        1. Retarget all inbound relationships to point to canonical
        2. Retarget all outbound relationships to point from canonical
        3. Deduplicate relationships (same source+target+type -> keep highest weight)
        4. Transfer MENTIONS links (Chunk->Entity) to canonical
        5. Add merged entity names as aliases on canonical
        6. Merge source_documents lists
        7. Set merged_description if provided, otherwise keep the longer description
        8. Delete the merged entity

        Returns: { canonical, merged, relationships_retargeted, aliases_added, chunks_relinked }
        """
        if not merge_names:
            return {"canonical": canonical_name, "merged": [], "relationships_retargeted": 0, "aliases_added": 0, "chunks_relinked": 0}

        total_rels_retargeted = 0
        total_aliases_added = 0
        total_chunks_relinked = 0
        merged_successfully = []

        with self.driver.session() as session:
            # Verify canonical exists
            check = session.run(
                "MATCH (e:Entity {name: $name}) RETURN e.name as name",
                name=canonical_name
            )
            if not check.single():
                raise ValueError(f"Canonical entity '{canonical_name}' not found")

            # Collect pre-merge data for history before entities are deleted
            all_names = [canonical_name] + [n for n in merge_names if n != canonical_name]
            pre_merge_result = session.run("""
                MATCH (e:Entity)
                WHERE e.name IN $names
                OPTIONAL MATCH (c:Chunk)-[:MENTIONS]->(e)
                WITH e, count(DISTINCT c) as mentions
                OPTIONAL MATCH (e)-[r]-(:Entity)
                RETURN e.name as name, e.type as type,
                       coalesce(e.description, '') as description,
                       mentions, count(DISTINCT r) as rels
            """, names=all_names)
            pre_merge_data = {r["name"]: dict(r) for r in pre_merge_result}

            for merge_name in merge_names:
                if merge_name == canonical_name:
                    continue

                # Check if merge entity exists
                check = session.run(
                    "MATCH (e:Entity {name: $name}) RETURN e.name as name",
                    name=merge_name
                )
                if not check.single():
                    logger.warning(f"Entity '{merge_name}' not found, skipping")
                    continue

                # 1. Retarget inbound relationships (source -> old becomes source -> canonical)
                result = session.run("""
                    MATCH (source:Entity)-[r]->(old:Entity {name: $merge_name})
                    WHERE source.name <> $canonical_name
                      AND NOT type(r) = 'MENTIONS'
                    WITH source, r, type(r) as rel_type, properties(r) as rel_props
                    WITH source, r, rel_type, rel_props,
                         coalesce(rel_props.weight, 5.0) as rw,
                         coalesce(rel_props.description, '') as rd
                    DELETE r
                    WITH source, rel_type, rw, rd
                    MATCH (canon:Entity {name: $canonical_name})
                    // Skip self-referencing
                    WHERE source.name <> canon.name
                    MERGE (source)-[nr:RELATED_TO]->(canon)
                    ON CREATE SET nr.type = rel_type, nr.weight = rw, nr.description = rd
                    ON MATCH SET nr.weight = CASE WHEN rw > nr.weight THEN rw ELSE nr.weight END,
                                 nr.description = CASE WHEN size(rd) > size(coalesce(nr.description, '')) THEN rd ELSE nr.description END
                    RETURN count(*) as retargeted
                """, merge_name=merge_name, canonical_name=canonical_name)
                record = result.single()
                inbound = record["retargeted"] if record else 0

                # 2. Retarget outbound relationships (old -> target becomes canonical -> target)
                result = session.run("""
                    MATCH (old:Entity {name: $merge_name})-[r]->(target:Entity)
                    WHERE target.name <> $canonical_name
                      AND NOT type(r) = 'MENTIONS'
                    WITH target, r, type(r) as rel_type, properties(r) as rel_props
                    WITH target, r, rel_type, rel_props,
                         coalesce(rel_props.weight, 5.0) as rw,
                         coalesce(rel_props.description, '') as rd
                    DELETE r
                    WITH target, rel_type, rw, rd
                    MATCH (canon:Entity {name: $canonical_name})
                    WHERE target.name <> canon.name
                    MERGE (canon)-[nr:RELATED_TO]->(target)
                    ON CREATE SET nr.type = rel_type, nr.weight = rw, nr.description = rd
                    ON MATCH SET nr.weight = CASE WHEN rw > nr.weight THEN rw ELSE nr.weight END,
                                 nr.description = CASE WHEN size(rd) > size(coalesce(nr.description, '')) THEN rd ELSE nr.description END
                    RETURN count(*) as retargeted
                """, merge_name=merge_name, canonical_name=canonical_name)
                record = result.single()
                outbound = record["retargeted"] if record else 0

                total_rels_retargeted += inbound + outbound

                # 3. Transfer chunk MENTIONS links
                result = session.run("""
                    MATCH (c:Chunk)-[m:MENTIONS]->(old:Entity {name: $merge_name})
                    WITH c, m
                    MATCH (canon:Entity {name: $canonical_name})
                    MERGE (c)-[:MENTIONS]->(canon)
                    DELETE m
                    RETURN count(*) as relinked
                """, merge_name=merge_name, canonical_name=canonical_name)
                record = result.single()
                chunks_relinked = record["relinked"] if record else 0
                total_chunks_relinked += chunks_relinked

                # 4. Transfer aliases and metadata, then delete
                # On the first merge iteration, apply the LLM-generated description if provided
                desc_clause = (
                    "canon.description = $merged_description"
                    if merged_description and merge_name == merge_names[0]
                    else """canon.description = CASE
                            WHEN size(coalesce(old.description, '')) > size(coalesce(canon.description, ''))
                            THEN old.description ELSE canon.description
                        END"""
                )
                result = session.run(f"""
                    MATCH (old:Entity {{name: $merge_name}}), (canon:Entity {{name: $canonical_name}})
                    SET canon.aliases = apoc.coll.toSet(
                            coalesce(canon.aliases, []) + $merge_name + coalesce(old.aliases, [])
                        ),
                        canon.source_documents = apoc.coll.toSet(
                            coalesce(canon.source_documents, []) + coalesce(old.source_documents, [])
                        ),
                        {desc_clause},
                        canon.extraction_count = coalesce(canon.extraction_count, 0) + coalesce(old.extraction_count, 0)
                    // Clear community_id since graph topology changed
                    REMOVE canon.community_id
                    DETACH DELETE old
                    RETURN 1 as done
                """, merge_name=merge_name, canonical_name=canonical_name,
                     merged_description=merged_description or "")
                record = result.single()
                if record:
                    total_aliases_added += 1
                    merged_successfully.append(merge_name)

        if merged_successfully:
            from datetime import timezone
            now_iso = datetime.now(timezone.utc).isoformat()
            self.set_meta("last_entity_merge_at", now_iso)

            # Store merge history record
            self._store_merge_history(
                canonical_name=canonical_name,
                merged_names=merged_successfully,
                entity_data=pre_merge_data,
                rels_retargeted=total_rels_retargeted,
                chunks_relinked=total_chunks_relinked,
                merged_description=merged_description,
                timestamp=now_iso,
            )

        logger.info(f"Merged {len(merged_successfully)} entities into '{canonical_name}': "
                     f"{total_rels_retargeted} rels retargeted, {total_chunks_relinked} chunks relinked")

        return {
            "canonical": canonical_name,
            "merged": merged_successfully,
            "relationships_retargeted": total_rels_retargeted,
            "aliases_added": total_aliases_added,
            "chunks_relinked": total_chunks_relinked,
        }

    def _store_merge_history(
        self,
        canonical_name: str,
        merged_names: List[str],
        entity_data: dict,
        rels_retargeted: int,
        chunks_relinked: int,
        merged_description: Optional[str],
        timestamp: str,
    ):
        """Store a merge history record as a MergeHistory node."""
        import json as _json
        merge_id = f"merge_{uuid.uuid4().hex[:12]}"

        # Build entity snapshots from pre-merge data
        entities = []
        for name in [canonical_name] + merged_names:
            data = entity_data.get(name, {})
            entities.append({
                "name": name,
                "type": data.get("type", ""),
                "description": data.get("description", ""),
                "mention_count": data.get("mentions", 0),
                "relationship_count": data.get("rels", 0),
                "is_canonical": name == canonical_name,
            })

        with self.driver.session() as session:
            session.run("""
                CREATE (h:MergeHistory {
                    id: $id,
                    canonical_name: $canonical,
                    merged_names: $merged,
                    merged_count: $count,
                    relationships_retargeted: $rels,
                    chunks_relinked: $chunks,
                    merged_description: $desc,
                    entities_snapshot: $snapshot,
                    merged_at: $ts
                })
            """,
                id=merge_id,
                canonical=canonical_name,
                merged=merged_names,
                count=len(merged_names),
                rels=rels_retargeted,
                chunks=chunks_relinked,
                desc=merged_description or "",
                snapshot=_json.dumps(entities),
                ts=timestamp,
            )

    def get_merge_history(self, limit: int = 50) -> List[dict]:
        """Get merge history records, most recent first."""
        import json as _json
        with self.driver.session() as session:
            result = session.run("""
                MATCH (h:MergeHistory)
                RETURN h.id as id,
                       h.canonical_name as canonical_name,
                       h.merged_names as merged_names,
                       h.merged_count as merged_count,
                       h.relationships_retargeted as relationships_retargeted,
                       h.chunks_relinked as chunks_relinked,
                       h.merged_description as merged_description,
                       h.entities_snapshot as entities_snapshot,
                       h.merged_at as merged_at
                ORDER BY h.merged_at DESC
                LIMIT $limit
            """, limit=limit)
            records = []
            for r in result:
                entry = dict(r)
                # Parse JSON snapshot
                try:
                    entry["entities_snapshot"] = _json.loads(entry["entities_snapshot"])
                except Exception:
                    entry["entities_snapshot"] = []
                records.append(entry)
            return records

    def suggest_duplicate_entities(
        self,
        threshold: float = 0.75,
        limit: int = 100,
        allowed_collection_ids: Optional[List[str]] = None
    ) -> List[dict]:
        """
        Find candidate duplicate entities using multiple similarity strategies.

        Uses rapidfuzz's cdist for batch C-level comparison (handles 7000+ entities
        efficiently vs the O(n²) Python loop).

        Strategies:
        - ratio: catches typos ("Colborn" vs "Colbornne")
        - token_sort_ratio: catches word reordering ("Bell Colborn" vs "Colborn Bell")
        - partial_ratio: only for same-type entities with reasonable length ratio,
          catches name variants ("Colborn" vs "Colborn Bell" when both are Person)

        partial_ratio is restricted to same-type + length ratio >= 0.5 to prevent
        brand-name pollution ("Google" matching "Google Pay", "Google Chrome", etc.)

        Uses star clustering instead of BFS to prevent transitive chain explosions:
        each group is centered on one canonical entity, and only entities directly
        similar to it are included.

        Returns grouped candidates with suggested canonical (most connected entity).
        """
        from rapidfuzz import fuzz
        from rapidfuzz.process import cdist
        import numpy as np

        # Fetch all entities with their connectivity stats (optionally scoped to collections)
        with self.driver.session() as session:
            if allowed_collection_ids:
                result = session.run("""
                    MATCH (col:Collection)-[:CONTAINS]->(d:Document)-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(e:Entity)
                    WHERE col.id IN $allowed_collection_ids
                    WITH DISTINCT e
                    OPTIONAL MATCH (c2:Chunk)-[:MENTIONS]->(e)
                    WITH e, count(DISTINCT c2) as mention_count
                    OPTIONAL MATCH (e)-[r]-(:Entity)
                    RETURN e.name as name, e.type as type,
                           e.description as description,
                           mention_count,
                           count(DISTINCT r) as relationship_count
                """, allowed_collection_ids=allowed_collection_ids)
            else:
                result = session.run("""
                    MATCH (e:Entity)
                    OPTIONAL MATCH (c:Chunk)-[:MENTIONS]->(e)
                    WITH e, count(DISTINCT c) as mention_count
                    OPTIONAL MATCH (e)-[r]-(:Entity)
                    RETURN e.name as name, e.type as type,
                           e.description as description,
                           mention_count,
                           count(DISTINCT r) as relationship_count
                """)
            all_entities = [dict(record) for record in result]

        if len(all_entities) < 2:
            return []

        # Build entity info lookup
        entity_info = {}
        for e in all_entities:
            entity_info[e['name']] = {
                'name': e['name'],
                'type': e['type'] or '',
                'description': e['description'] or '',
                'mention_count': e['mention_count'],
                'relationship_count': e['relationship_count'],
            }

        names = [e['name'] for e in all_entities]
        names_lower = [n.lower() for n in names]
        types = [e['type'] or '' for e in all_entities]
        n = len(names)

        score_cutoff = threshold * 100  # rapidfuzz uses 0-100 scale

        # Use half of available CPU cores to avoid saturating the system.
        # In Docker, len(os.sched_getaffinity(0)) respects cgroup CPU limits
        # while os.cpu_count() returns the host's total cores.
        import os
        try:
            available = len(os.sched_getaffinity(0))
        except AttributeError:
            available = os.cpu_count() or 2
        half_cores = max(1, available // 2)

        # Batch compute similarity matrices in C (much faster than Python pairwise loop)
        best_matrix = cdist(names_lower, names_lower, scorer=fuzz.ratio,
                            score_cutoff=score_cutoff, dtype=np.float32, workers=half_cores)
        token_sort_matrix = cdist(names_lower, names_lower, scorer=fuzz.token_sort_ratio,
                                  score_cutoff=score_cutoff, dtype=np.float32, workers=half_cores)
        np.maximum(best_matrix, token_sort_matrix, out=best_matrix)
        del token_sort_matrix

        # partial_ratio: only within same-type groups with length ratio gating
        type_groups = {}
        for idx, t in enumerate(types):
            if t:
                type_groups.setdefault(t, []).append(idx)

        for entity_type, indices in type_groups.items():
            if len(indices) < 2:
                continue

            group_names = [names_lower[i] for i in indices]
            partial_scores = cdist(group_names, group_names, scorer=fuzz.partial_ratio,
                                   score_cutoff=score_cutoff, dtype=np.float32, workers=half_cores)

            # Length ratio gating
            min_len_ratio = 0.35 if entity_type == 'Person' else 0.5
            group_lens = np.array([len(names_lower[i]) for i in indices], dtype=np.float32)
            len_min = np.minimum.outer(group_lens, group_lens)
            len_max = np.maximum.outer(group_lens, group_lens)
            len_ratios = np.divide(len_min, len_max, out=np.zeros_like(len_min), where=len_max > 0)
            partial_scores[len_ratios < min_len_ratio] = 0

            # For Person entities: only allow partial_ratio when the shorter
            # name is a word-level prefix of the longer name.
            # This keeps "Colborn" ↔ "Colborn Bell" (legitimate short→full name)
            # while blocking "Andy" ↔ "Andreas Gysin" or "David Young" ↔ "David Hockney"
            # (shared first name, different people).
            if entity_type == 'Person':
                # Build a mask: only keep partial_ratio for pairs where
                # the shorter name is a strict word-prefix of the longer.
                # Vectorized: suppress when both have same word count (covers
                # most false positives), then only check the remaining sparse
                # pairs with different word counts.
                group_norm = [names_lower[i].replace('-', ' ').split() for i in indices]
                word_counts = np.array([len(w) for w in group_norm], dtype=np.int32)

                # Same word count → never a prefix → suppress partial_ratio
                wc_eq = np.equal.outer(word_counts, word_counts)
                partial_scores[wc_eq] = 0

                # For remaining nonzero pairs (different word counts), check
                # if shorter name's words match the start of longer name's words.
                # This is sparse — most pairs are already zeroed above.
                gi_arr, gj_arr = np.where(np.triu(partial_scores, k=1) > 0)
                for idx in range(len(gi_arr)):
                    gi, gj = int(gi_arr[idx]), int(gj_arr[idx])
                    words_a, words_b = group_norm[gi], group_norm[gj]
                    short_w, long_w = (words_a, words_b) if len(words_a) <= len(words_b) else (words_b, words_a)
                    is_prefix = True
                    for k, sw in enumerate(short_w):
                        if k < len(long_w) and fuzz.ratio(sw, long_w[k]) >= 80:
                            continue
                        is_prefix = False
                        break
                    if not is_prefix:
                        partial_scores[gi, gj] = 0
                        partial_scores[gj, gi] = 0

            # Merge improvements into best_matrix via numpy indexing
            idx_arr = np.array(indices)
            current = best_matrix[np.ix_(idx_arr, idx_arr)]
            best_matrix[np.ix_(idx_arr, idx_arr)] = np.maximum(current, partial_scores)

        # Extract matching pairs from upper triangle
        upper = np.triu(best_matrix, k=1)
        del best_matrix
        rows, cols = np.where(upper > 0)

        name_lens = [len(nl) for nl in names_lower]
        direct_matches = {}

        for idx in range(len(rows)):
            i, j = int(rows[idx]), int(cols[idx])
            score = float(upper[i, j]) / 100.0

            len_a, len_b = name_lens[i], name_lens[j]

            # Skip both very short (<=2 chars) — too many false positives
            if len_a <= 2 and len_b <= 2:
                continue

            # Higher threshold for short names
            effective_threshold = threshold
            if min(len_a, len_b) <= 3:
                effective_threshold = max(threshold, 0.85)

            if score >= effective_threshold:
                a_name = names[i]
                b_name = names[j]
                direct_matches.setdefault(a_name, []).append((b_name, score))
                direct_matches.setdefault(b_name, []).append((a_name, score))

        del upper

        if not direct_matches:
            return []

        # Star clustering: greedily form groups around the most-connected entities.
        # Each group is centered on one canonical — only its direct matches join.
        # No transitive chains: "Google" ~ "Google Pay" won't pull in "Chrome".
        assigned = set()
        groups = []

        # Sort candidates: entities with the most direct matches first,
        # then by connectivity (relationships + mentions) as tiebreaker
        candidates = sorted(
            direct_matches.keys(),
            key=lambda name: (
                len(direct_matches[name]),
                entity_info[name]['relationship_count'],
                entity_info[name]['mention_count'],
            ),
            reverse=True,
        )

        # Identify single-word Person names — these must not be star centers
        # because a bare first name like "Andrea" would pull all "Andrea X"
        # into one group. They can still be members of other groups.
        single_word_persons = set()
        for name in direct_matches:
            info = entity_info[name]
            if info['type'] == 'Person' and len(name.split()) == 1:
                single_word_persons.add(name)

        for canonical_candidate in candidates:
            if canonical_candidate in assigned:
                continue

            # Don't let single-word Person names be star centers
            if canonical_candidate in single_word_persons:
                continue

            # Gather unassigned entities directly similar to this one
            members = []
            max_sim = 0.0
            for other_name, score in direct_matches[canonical_candidate]:
                if other_name not in assigned:
                    members.append(other_name)
                    max_sim = max(max_sim, score)

            if not members:
                continue

            # The group is: canonical_candidate + its direct matches
            group_names = [canonical_candidate] + members
            group_entities = [entity_info[name] for name in group_names]

            # Pick the most connected entity as the actual canonical
            group_entities.sort(
                key=lambda e: (e['relationship_count'], e['mention_count']),
                reverse=True,
            )
            canonical = group_entities[0]['name']

            groups.append({
                'suggested_canonical': canonical,
                'entities': group_entities,
                'similarity': round(max_sim, 3),
                'method': 'name',
            })

            # Mark all group members as assigned
            for name in group_names:
                assigned.add(name)

        # Sort: Person groups first (highest dedup accuracy), then by similarity
        TYPE_PRIORITY = {'Person': 0, 'Organization': 1}
        def _group_sort_key(g):
            types = {e['type'] for e in g['entities'] if e['type']}
            best_type = min((TYPE_PRIORITY.get(t, 99) for t in types), default=99)
            return (best_type, -g['similarity'])
        groups.sort(key=_group_sort_key)
        return groups[:limit]

    @retry_on_transient
    def get_entity_relationships(
        self,
        entity_name: str,
        max_depth: int = 2,
        limit: int = 50,
        allowed_collection_ids: Optional[List[str]] = None
    ) -> dict:
        """
        Get an entity and all its relationships up to max_depth hops.
        
        This enables focused graph exploration from a specific entity.
        
        Args:
            entity_name: The entity to start from
            max_depth: Maximum relationship hops to traverse (1-3)
            limit: Maximum number of relationships to return
            allowed_collection_ids: If provided, scope to entities from these collections
            
        Returns:
            Dict with 'entity', 'related_entities', 'relationships'
        """
        max_depth = min(max(1, max_depth), 3)  # Clamp between 1-3
        
        with self.driver.session() as session:
            # Get the central entity — verify it is accessible in the allowed collections
            if allowed_collection_ids:
                entity_result = session.run("""
                    MATCH (e:Entity {name: $name})
                    WHERE EXISTS {
                        MATCH (col:Collection)-[:CONTAINS]->(d:Document)-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(e)
                        WHERE col.id IN $allowed_collection_ids
                    }
                    OPTIONAL MATCH (c2:Chunk)-[:MENTIONS]->(e)
                    WITH e, count(c2) as mention_count
                    RETURN e.name as name,
                           e.type as type,
                           e.description as description,
                           e.community_id as community_id,
                           mention_count
                """, name=entity_name, allowed_collection_ids=allowed_collection_ids)
            else:
                entity_result = session.run("""
                    MATCH (e:Entity {name: $name})
                    OPTIONAL MATCH (c:Chunk)-[:MENTIONS]->(e)
                    WITH e, count(c) as mention_count
                    RETURN e.name as name,
                           e.type as type,
                           e.description as description,
                           e.community_id as community_id,
                           mention_count
                """, name=entity_name)
            
            entity_record = entity_result.single()
            if not entity_record:
                return {"entity": None, "related_entities": [], "relationships": []}
            
            entity = dict(entity_record)
            
            # Traverse relationships up to max_depth
            # Using a parameterized depth requires string interpolation (safe since we clamp the value)
            # For collection-scoped keys, filter related entities to the allowed collections
            if allowed_collection_ids:
                traverse_result = session.run(f"""
                    MATCH (start:Entity {{name: $name}})
                    CALL {{
                        WITH start
                        MATCH path = (start)-[r*1..{max_depth}]-(related:Entity)
                        WHERE ALL(n IN nodes(path) WHERE n:Entity)
                        AND EXISTS {{
                            MATCH (col:Collection)-[:CONTAINS]->(d:Document)-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(related)
                            WHERE col.id IN $allowed_collection_ids
                        }}
                        RETURN DISTINCT related,
                               [rel IN relationships(path) | {{
                                   source: startNode(rel).name,
                                   target: endNode(rel).name,
                                   type: coalesce(rel.type, type(rel)),
                                   description: rel.description,
                                   weight: coalesce(rel.weight, 5.0)
                               }}] as path_rels
                        LIMIT $limit
                    }}
                    RETURN related.name as name,
                           related.type as type,
                           related.description as description,
                           related.community_id as community_id,
                           path_rels
                """, name=entity_name, limit=limit, allowed_collection_ids=allowed_collection_ids)
            else:
                traverse_result = session.run(f"""
                    MATCH (start:Entity {{name: $name}})
                    CALL {{
                        WITH start
                        MATCH path = (start)-[r*1..{max_depth}]-(related:Entity)
                        WHERE ALL(n IN nodes(path) WHERE n:Entity)
                        RETURN DISTINCT related,
                               [rel IN relationships(path) | {{
                                   source: startNode(rel).name,
                                   target: endNode(rel).name,
                                   type: coalesce(rel.type, type(rel)),
                                   description: rel.description,
                                   weight: coalesce(rel.weight, 5.0)
                               }}] as path_rels
                        LIMIT $limit
                    }}
                    RETURN related.name as name,
                           related.type as type,
                           related.description as description,
                           related.community_id as community_id,
                           path_rels
                """, name=entity_name, limit=limit)
            
            related_entities = []
            all_relationships = []
            seen_rels = set()
            
            for record in traverse_result:
                related_entities.append({
                    "name": record["name"],
                    "type": record["type"],
                    "description": record["description"],
                    "community_id": record["community_id"]
                })
                
                # Collect unique relationships
                for rel in record["path_rels"]:
                    rel_key = (rel["source"], rel["target"], rel["type"])
                    if rel_key not in seen_rels:
                        seen_rels.add(rel_key)
                        all_relationships.append(rel)
            
            return {
                "entity": entity,
                "related_entities": related_entities,
                "relationships": all_relationships
            }
    
    @retry_on_transient
    def get_graph_subgraph(
        self,
        entity_names: List[str],
        include_connections: bool = True,
        allowed_collection_ids: Optional[List[str]] = None
    ) -> dict:
        """
        Get a subgraph containing specified entities and their interconnections.
        
        Method for focused graph visualization of specific entities.
        
        Args:
            entity_names: List of entity names to include
            include_connections: If True, also include entities that connect the given entities
            allowed_collection_ids: If provided, scope entities to those from these collections
            
        Returns:
            Dict with 'nodes' and 'edges' for the subgraph
        """
        if not entity_names:
            return {"nodes": [], "edges": []}
        
        with self.driver.session() as session:
            if include_connections:
                if allowed_collection_ids:
                    result = session.run("""
                        // Get selected entities (scoped to allowed collections)
                        MATCH (e:Entity)
                        WHERE e.name IN $names
                        AND EXISTS {
                            MATCH (col:Collection)-[:CONTAINS]->(d:Document)-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(e)
                            WHERE col.id IN $allowed_collection_ids
                        }
                        
                        // Get their direct neighbors (also scoped)
                        OPTIONAL MATCH (e)-[]-(neighbor:Entity)
                        WHERE EXISTS {
                            MATCH (col2:Collection)-[:CONTAINS]->(d2:Document)-[:HAS_CHUNK]->(c2:Chunk)-[:MENTIONS]->(neighbor)
                            WHERE col2.id IN $allowed_collection_ids
                        }
                        
                        WITH collect(DISTINCT e) + collect(DISTINCT neighbor) as all_nodes
                        UNWIND all_nodes as n
                        WITH DISTINCT n
                        WHERE n IS NOT NULL
                        
                        OPTIONAL MATCH (c3:Chunk)-[:MENTIONS]->(n)
                        WITH n, count(c3) as mention_count
                        RETURN n.name as id,
                               n.name as label,
                               n.type as type,
                               n.description as description,
                               n.community_id as community_id,
                               mention_count
                    """, names=entity_names, allowed_collection_ids=allowed_collection_ids)
                else:
                    # Get selected entities and all their direct neighbors (1 hop)
                    # This gives a focused subgraph centered on the selected entities
                    result = session.run("""
                        // Get selected entities
                        MATCH (e:Entity)
                        WHERE e.name IN $names
                        
                        // Get their direct neighbors
                        OPTIONAL MATCH (e)-[]-(neighbor:Entity)
                        
                        // Combine into a single set of nodes
                        WITH collect(DISTINCT e) + collect(DISTINCT neighbor) as all_nodes
                        UNWIND all_nodes as n
                        WITH DISTINCT n
                        WHERE n IS NOT NULL
                        
                        // Get mention counts
                        OPTIONAL MATCH (c:Chunk)-[:MENTIONS]->(n)
                        WITH n, count(c) as mention_count
                        RETURN n.name as id,
                               n.name as label,
                               n.type as type,
                               n.description as description,
                               n.community_id as community_id,
                               mention_count
                    """, names=entity_names)
            else:
                if allowed_collection_ids:
                    result = session.run("""
                        MATCH (e:Entity)
                        WHERE e.name IN $names
                        AND EXISTS {
                            MATCH (col:Collection)-[:CONTAINS]->(d:Document)-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(e)
                            WHERE col.id IN $allowed_collection_ids
                        }
                        OPTIONAL MATCH (c2:Chunk)-[:MENTIONS]->(e)
                        WITH e, count(c2) as mention_count
                        RETURN e.name as id,
                               e.name as label,
                               e.type as type,
                               e.description as description,
                               e.community_id as community_id,
                               mention_count
                    """, names=entity_names, allowed_collection_ids=allowed_collection_ids)
                else:
                    # Just get the specified entities
                    result = session.run("""
                        MATCH (e:Entity)
                        WHERE e.name IN $names
                        OPTIONAL MATCH (c:Chunk)-[:MENTIONS]->(e)
                        WITH e, count(c) as mention_count
                        RETURN e.name as id,
                               e.name as label,
                               e.type as type,
                               e.description as description,
                               e.community_id as community_id,
                               mention_count
                    """, names=entity_names)
            
            nodes = [dict(record) for record in result]
            node_ids = {n["id"] for n in nodes}
            
            # Get relationships between nodes in the subgraph
            edge_result = session.run("""
                MATCH (s:Entity)-[r]->(t:Entity)
                WHERE s.name IN $node_ids AND t.name IN $node_ids
                RETURN s.name as source,
                       t.name as target,
                       type(r) as rel_type,
                       r.type as sub_type,
                       r.description as description,
                       coalesce(r.weight, 5.0) as weight
            """, node_ids=list(node_ids))
            
            edges = []
            for record in edge_result:
                edges.append({
                    "source": record["source"],
                    "target": record["target"],
                    "type": record["sub_type"] or record["rel_type"],
                    "description": record["description"],
                    "weight": record["weight"]
                })
            
            return {"nodes": nodes, "edges": edges}
    
    # =========================================================================
    # Collection Management
    # =========================================================================
    
    def create_collection(self, name: str, description: Optional[str] = None) -> dict:
        """Create a new collection for organizing documents."""
        collection_id = str(uuid.uuid4())
        
        with self.driver.session() as session:
            result = session.run("""
                CREATE (col:Collection {
                    id: $id,
                    name: $name,
                    description: $description,
                    created_at: datetime()
                })
                RETURN col.id as id, col.name as name, col.description as description
            """, id=collection_id, name=name, description=description)
            
            record = result.single()
            logger.info(f"Created collection: {name} ({collection_id})")
            return dict(record) if record else None
    
    @retry_on_transient
    def get_collection(self, collection_id: str) -> Optional[dict]:
        """Get a collection by ID with stats."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (col:Collection {id: $id})
                OPTIONAL MATCH (col)-[:CONTAINS]->(d:Document)
                OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(e:Entity)
                RETURN col.id as id,
                       col.name as name,
                       col.description as description,
                       col.created_at as created_at,
                       count(DISTINCT d) as document_count,
                       count(DISTINCT e) as entity_count
            """, id=collection_id)
            
            record = result.single()
            return dict(record) if record else None
    
    @retry_on_transient
    def list_collections(self) -> List[dict]:
        """List all collections with stats."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (col:Collection)
                OPTIONAL MATCH (col)-[:CONTAINS]->(d:Document)
                OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(e:Entity)
                RETURN col.id as id,
                       col.name as name,
                       col.description as description,
                       col.created_at as created_at,
                       count(DISTINCT d) as document_count,
                       count(DISTINCT e) as entity_count
                ORDER BY col.created_at DESC
            """)
            return [dict(record) for record in result]
    
    def update_collection(self, collection_id: str, name: str = None, description: str = None) -> dict:
        """Update a collection's name and/or description."""
        with self.driver.session() as session:
            set_clauses = []
            params = {"id": collection_id}
            if name is not None:
                set_clauses.append("col.name = $name")
                params["name"] = name
            if description is not None:
                set_clauses.append("col.description = $description")
                params["description"] = description
            if not set_clauses:
                return self.get_collection(collection_id)

            result = session.run(f"""
                MATCH (col:Collection {{id: $id}})
                SET {', '.join(set_clauses)}
                RETURN col.id as id, col.name as name, col.description as description,
                       col.created_at as created_at
            """, **params)
            record = result.single()
            if not record:
                return None
            return dict(record)

    def delete_collection(self, collection_id: str) -> dict:
        """
        Delete a collection and move all its documents to the default collection.
        
        Documents are preserved and can be deleted individually from the default
        collection if needed, which properly cleans up chunks and orphaned entities.
        
        Returns:
            Dict with 'deleted' (bool), 'documents_moved' (int)
        """
        with self.driver.session() as session:
            # First, move all documents to the default collection
            move_result = session.run("""
                MATCH (col:Collection {id: $id})-[:CONTAINS]->(d:Document)
                MATCH (default_col:Collection {id: 'default'})
                // Remove from current collection
                MATCH (col)-[r:CONTAINS]->(d)
                DELETE r
                // Add to default collection
                MERGE (default_col)-[:CONTAINS]->(d)
                SET d.collection_id = 'default'
                RETURN count(d) as moved_count
            """, id=collection_id)
            
            move_record = move_result.single()
            documents_moved = move_record["moved_count"] if move_record else 0
            
            # Then delete the collection itself
            delete_result = session.run("""
                MATCH (col:Collection {id: $id})
                DETACH DELETE col
                RETURN 1 as deleted
            """, id=collection_id)
            
            delete_record = delete_result.single()
            deleted = delete_record is not None
            
            if deleted:
                logger.info(f"Deleted collection {collection_id}, moved {documents_moved} documents to default collection")
            
            return {"deleted": deleted, "documents_moved": documents_moved}
    
    def add_document_to_collection(self, document_id: str, collection_id: str) -> bool:
        """Add a document to a collection."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (col:Collection {id: $collection_id})
                MATCH (d:Document {id: $document_id})
                MERGE (col)-[:CONTAINS]->(d)
                SET d.collection_id = $collection_id
                RETURN d.id as id
            """, collection_id=collection_id, document_id=document_id)
            
            record = result.single()
            return record is not None
    
    def move_document_to_collection(self, document_id: str, target_collection_id: str) -> bool:
        """
        Move a document to a different collection.
        Removes from current collection (if any) and adds to the target collection.
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (d:Document {id: $document_id})
                MATCH (target:Collection {id: $target_collection_id})
                // Remove from any existing collection
                OPTIONAL MATCH (old:Collection)-[r:CONTAINS]->(d)
                DELETE r
                // Add to new collection
                MERGE (target)-[:CONTAINS]->(d)
                SET d.collection_id = $target_collection_id
                RETURN d.id as id
            """, document_id=document_id, target_collection_id=target_collection_id)
            
            record = result.single()
            return record is not None
    
    def move_documents_to_collection(self, document_ids: list[str], target_collection_id: str) -> dict:
        """
        Move multiple documents to a collection.
        Returns count of successful moves.
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (target:Collection {id: $target_collection_id})
                UNWIND $document_ids as doc_id
                MATCH (d:Document {id: doc_id})
                // Remove from any existing collection
                OPTIONAL MATCH (old:Collection)-[r:CONTAINS]->(d)
                DELETE r
                // Add to new collection
                MERGE (target)-[:CONTAINS]->(d)
                SET d.collection_id = $target_collection_id
                RETURN count(d) as moved_count
            """, document_ids=document_ids, target_collection_id=target_collection_id)
            
            record = result.single()
            return {"moved_count": record["moved_count"] if record else 0}
    
    def remove_document_from_collection(self, document_id: str) -> bool:
        """Remove a document from its current collection (move to default/no collection)."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (d:Document {id: $document_id})
                OPTIONAL MATCH (col:Collection)-[r:CONTAINS]->(d)
                DELETE r
                REMOVE d.collection_id
                RETURN d.id as id
            """, document_id=document_id)
            
            record = result.single()
            return record is not None
    
    @retry_on_transient
    def get_collection_entities(self, collection_id: str, limit: int = 100) -> List[dict]:
        """Get entities belonging to a collection."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (col:Collection {id: $collection_id})-[:CONTAINS]->(d:Document)
                MATCH (d)-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(e:Entity)
                WITH e, count(DISTINCT c) as mention_count
                RETURN e.name as name,
                       e.type as type,
                       e.description as description,
                       e.community_id as community_id,
                       mention_count
                ORDER BY mention_count DESC
                LIMIT $limit
            """, collection_id=collection_id, limit=limit)
            return [dict(record) for record in result]
    
    # =========================================================================
    # Community Detection
    # =========================================================================
    
    def detect_communities(self, min_size: int = 3, collection_id: Optional[str] = None) -> List[dict]:
        """
        Detect communities of related entities using connected components.
        
        For production, Neo4j GDS (Graph Data Science) with Louvain algorithm is recommended.
        This fallback uses connected components via relationship traversal.
        
        Returns:
            List of communities with their entities
        """
        with self.driver.session() as session:
            # Try using Neo4j GDS Louvain if available
            try:
                # Check if GDS is available
                session.run("CALL gds.version()")
                
                # Use GDS Louvain for better community detection
                return self._detect_communities_gds(session, min_size, collection_id)
            except Exception as e:
                logger.debug(f"GDS not available, using fallback community detection: {e}")
                # Fallback to simple connected components
                return self._detect_communities_fallback(session, min_size, collection_id)
    
    def _detect_communities_gds(
        self, 
        session, 
        min_size: int, 
        collection_id: Optional[str]
    ) -> List[dict]:
        """Detect communities using Neo4j GDS Louvain algorithm."""
        graph_name = f"entity_graph_{uuid.uuid4().hex[:8]}"
        
        try:
            # Create a temporary graph projection with:
            # 1. Direct entity-to-entity relationships (undirected, weighted)
            # 2. Co-mention edges: entities sharing a chunk get an implicit connection (weight 2.0)
            #    This helps community detection when direct relationships are sparse.
            if collection_id:
                # Collection-scoped graph
                session.run("""
                    CALL gds.graph.project.cypher(
                        $graph_name,
                        'MATCH (col:Collection {id: $col_id})-[:CONTAINS]->(d:Document)-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(e:Entity) RETURN id(e) as id',
                        'MATCH (e1:Entity)-[r]->(e2:Entity) WHERE type(r) <> "MENTIONS" RETURN id(e1) as source, id(e2) as target, coalesce(r.weight, 5.0) as weight UNION MATCH (e1:Entity)<-[r]-(e2:Entity) WHERE type(r) <> "MENTIONS" RETURN id(e1) as source, id(e2) as target, coalesce(r.weight, 5.0) as weight UNION MATCH (c:Chunk)-[:MENTIONS]->(e1:Entity), (c)-[:MENTIONS]->(e2:Entity) WHERE id(e1) < id(e2) RETURN id(e1) as source, id(e2) as target, 2.0 as weight',
                        {parameters: {col_id: $col_id}}
                    )
                """, graph_name=graph_name, col_id=collection_id)
            else:
                # Global graph - entity relationships + co-mention edges
                session.run("""
                    CALL gds.graph.project.cypher(
                        $graph_name,
                        'MATCH (e:Entity) RETURN id(e) as id',
                        'MATCH (e1:Entity)-[r]->(e2:Entity) WHERE type(r) <> "MENTIONS" RETURN id(e1) as source, id(e2) as target, coalesce(r.weight, 5.0) as weight UNION MATCH (e1:Entity)<-[r]-(e2:Entity) WHERE type(r) <> "MENTIONS" RETURN id(e1) as source, id(e2) as target, coalesce(r.weight, 5.0) as weight UNION MATCH (c:Chunk)-[:MENTIONS]->(e1:Entity), (c)-[:MENTIONS]->(e2:Entity) WHERE id(e1) < id(e2) RETURN id(e1) as source, id(e2) as target, 2.0 as weight'
                    )
                """, graph_name=graph_name)

            # Try Leiden first (better for GraphRAG), fall back to Louvain
            try:
                result = session.run("""
                    CALL gds.leiden.stream($graph_name, {
                        relationshipWeightProperty: 'weight'
                    })
                    YIELD nodeId, communityId
                    WITH gds.util.asNode(nodeId) as entity, communityId
                    WITH communityId,
                         collect({
                             name: entity.name,
                             type: entity.type,
                             description: entity.description
                         }) as members
                    WHERE size(members) >= $min_size
                    RETURN communityId as id,
                           members,
                           size(members) as entity_count
                    ORDER BY entity_count DESC
                """, graph_name=graph_name, min_size=min_size)
                logger.info("Community detection: using Leiden algorithm")
            except Exception as leiden_err:
                logger.info(f"Leiden not available ({leiden_err}), falling back to Louvain")
                result = session.run("""
                    CALL gds.louvain.stream($graph_name, {
                        relationshipWeightProperty: 'weight'
                    })
                    YIELD nodeId, communityId
                    WITH gds.util.asNode(nodeId) as entity, communityId
                    WITH communityId,
                         collect({
                             name: entity.name,
                             type: entity.type,
                             description: entity.description
                         }) as members
                    WHERE size(members) >= $min_size
                    RETURN communityId as id,
                           members,
                           size(members) as entity_count
                    ORDER BY entity_count DESC
                """, graph_name=graph_name, min_size=min_size)
            
            communities = []
            for record in result:
                communities.append({
                    "id": record["id"],
                    "entities": record["members"],
                    "entity_count": record["entity_count"]
                })
            
            return communities
            
        finally:
            # Clean up the projected graph
            try:
                session.run("CALL gds.graph.drop($graph_name, false)", graph_name=graph_name)
            except Exception:
                pass
    
    def _detect_communities_fallback(
        self, 
        session, 
        min_size: int, 
        collection_id: Optional[str]
    ) -> List[dict]:
        """Fallback community detection using connected components via BFS."""
        # Get all entities and their relationships
        if collection_id:
            result = session.run("""
                MATCH (col:Collection {id: $col_id})-[:CONTAINS]->(d:Document)
                MATCH (d)-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(e:Entity)
                WITH collect(DISTINCT e.name) as entity_names
                MATCH (e1:Entity)-[r]-(e2:Entity)
                WHERE e1.name IN entity_names AND e2.name IN entity_names
                  AND type(r) <> 'MENTIONS'
                RETURN DISTINCT e1.name as source, e2.name as target
            """, col_id=collection_id)
        else:
            result = session.run("""
                MATCH (e1:Entity)-[r]-(e2:Entity)
                WHERE type(r) <> 'MENTIONS'
                RETURN DISTINCT e1.name as source, e2.name as target
            """)
        
        # Build adjacency list
        adjacency = {}
        for record in result:
            source, target = record["source"], record["target"]
            if source not in adjacency:
                adjacency[source] = set()
            if target not in adjacency:
                adjacency[target] = set()
            adjacency[source].add(target)
            adjacency[target].add(source)
        
        # Find connected components using BFS
        visited = set()
        communities = []
        community_id = 0
        
        for entity in adjacency:
            if entity in visited:
                continue
            
            # BFS to find all connected entities
            component = []
            queue = [entity]
            while queue:
                current = queue.pop(0)
                if current in visited:
                    continue
                visited.add(current)
                component.append(current)
                queue.extend([n for n in adjacency.get(current, []) if n not in visited])
            
            if len(component) >= min_size:
                communities.append({
                    "id": community_id,
                    "entities": component,
                    "entity_count": len(component)
                })
                community_id += 1
        
        # Fetch entity details for each community
        for community in communities:
            entity_names = community["entities"]
            detail_result = session.run("""
                MATCH (e:Entity)
                WHERE e.name IN $names
                RETURN e.name as name, e.type as type, e.description as description
            """, names=entity_names)
            
            community["entities"] = [dict(r) for r in detail_result]
        
        return sorted(communities, key=lambda c: c["entity_count"], reverse=True)
    
    def store_community(self, community_id: int, entities: List[str], summary: Optional[str] = None, name: Optional[str] = None) -> bool:
        """Store a detected community and link its entities."""
        with self.driver.session() as session:
            # Create or update community node
            session.run("""
                MERGE (com:Community {id: $id})
                SET com.summary = $summary,
                    com.name = $name,
                    com.entity_count = $entity_count,
                    com.updated_at = datetime()
            """, id=community_id, summary=summary, name=name, entity_count=len(entities))
            
            # Link entities to community and update their community_id
            session.run("""
                MATCH (com:Community {id: $community_id})
                MATCH (e:Entity)
                WHERE e.name IN $entity_names
                MERGE (com)-[:HAS_MEMBER]->(e)
                SET e.community_id = $community_id
            """, community_id=community_id, entity_names=entities)
            
            return True
    
    @retry_on_transient
    def get_community(
        self,
        community_id: int,
        allowed_collection_ids: Optional[List[str]] = None
    ) -> Optional[dict]:
        """Get a community with its entities and relationships.
        
        Args:
            allowed_collection_ids: If provided, the community is only returned if it has
                at least one member entity from these collections. Entity list is also
                filtered to those accessible in the allowed collections.
        """
        with self.driver.session() as session:
            if allowed_collection_ids:
                # Verify the community has at least one accessible member entity
                result = session.run("""
                    MATCH (com:Community {id: $id})
                    WHERE EXISTS {
                        MATCH (com)-[:HAS_MEMBER]->(member:Entity)
                        WHERE EXISTS {
                            MATCH (col:Collection)-[:CONTAINS]->(d:Document)-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(member)
                            WHERE col.id IN $allowed_collection_ids
                        }
                    }
                    OPTIONAL MATCH (com)-[:HAS_MEMBER]->(e:Entity)
                    WHERE EXISTS {
                        MATCH (col2:Collection)-[:CONTAINS]->(d2:Document)-[:HAS_CHUNK]->(c2:Chunk)-[:MENTIONS]->(e)
                        WHERE col2.id IN $allowed_collection_ids
                    }
                    WITH com, collect({name: e.name, type: e.type, description: e.description}) as entities
                    RETURN com.id as id,
                           com.name as name,
                           com.summary as summary,
                           com.entity_count as entity_count,
                           entities
                """, id=community_id, allowed_collection_ids=allowed_collection_ids)
            else:
                result = session.run("""
                    MATCH (com:Community {id: $id})
                    OPTIONAL MATCH (com)-[:HAS_MEMBER]->(e:Entity)
                    WITH com, collect({name: e.name, type: e.type, description: e.description}) as entities
                    RETURN com.id as id,
                           com.name as name,
                           com.summary as summary,
                           com.entity_count as entity_count,
                           entities
                """, id=community_id)
            
            record = result.single()
            if not record:
                return None
            
            community = dict(record)
            
            # Get key relationships within the community
            rel_result = session.run("""
                MATCH (e1:Entity {community_id: $id})-[r]->(e2:Entity {community_id: $id})
                WHERE type(r) <> 'MENTIONS'
                RETURN e1.name as source, e2.name as target, type(r) as type, r.description as description
                LIMIT 20
            """, id=community_id)
            
            community["key_relationships"] = [dict(r) for r in rel_result]
            
            return community
    
    @retry_on_transient
    def list_communities(self, limit: int = 50) -> List[dict]:
        """List all stored communities."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (com:Community)
                OPTIONAL MATCH (com)-[:HAS_MEMBER]->(e:Entity)
                WITH com, count(e) as member_count, 
                     collect(e.name)[0..5] as sample_entities
                RETURN com.id as id,
                       com.name as name,
                       com.summary as summary,
                       member_count as entity_count,
                       sample_entities
                ORDER BY member_count DESC
                LIMIT $limit
            """, limit=limit)
            return [dict(record) for record in result]

    @retry_on_transient
    def list_entities_paginated(
        self,
        skip: int = 0,
        limit: int = 50,
        search: str = None,
        entity_type: str = None,
        allowed_collection_ids: Optional[List[str]] = None
    ) -> dict:
        """List entities with server-side pagination, search, and filtering.
        
        Args:
            allowed_collection_ids: If provided, scope to entities from these collections (4-hop pattern)
        """
        with self.driver.session() as session:
            # Build WHERE clauses
            where_parts = []
            params = {"skip": skip, "limit": limit}

            if entity_type:
                where_parts.append("e.type = $entity_type")
                params["entity_type"] = entity_type

            if search:
                where_parts.append("(toLower(e.name) CONTAINS toLower($search) OR toLower(e.description) CONTAINS toLower($search))")
                params["search"] = search

            where_clause = "WHERE " + " AND ".join(where_parts) if where_parts else ""
            
            # Collection scoping: 4-hop pattern
            collection_match = ""
            if allowed_collection_ids:
                collection_match = """
                    MATCH (col:Collection)-[:CONTAINS]->(d:Document)-[:HAS_CHUNK]->(chunk:Chunk)-[:MENTIONS]->(e)
                    WHERE col.id IN $allowed_collection_ids
                    WITH DISTINCT e
                """
                params["allowed_collection_ids"] = allowed_collection_ids

            # Get total count
            if allowed_collection_ids:
                count_query = f"""
                    {collection_match}
                    {where_clause.replace('WHERE', 'WHERE' if not collection_match else 'AND' if where_parts else '')}
                    RETURN count(DISTINCT e) as total
                """
                # Fix WHERE/AND clause
                if where_parts:
                    count_query = f"""
                        {collection_match}
                        WHERE {' AND '.join(where_parts)}
                        RETURN count(DISTINCT e) as total
                    """
                else:
                    count_query = f"""
                        {collection_match}
                        RETURN count(DISTINCT e) as total
                    """
            else:
                count_query = f"""
                    MATCH (e:Entity)
                    {where_clause}
                    RETURN count(e) as total
                """
            total = session.run(count_query, **params).single()["total"]

            # Get paginated results with sorting: name matches first when searching
            if search:
                order_clause = """
                    ORDER BY
                        CASE WHEN toLower(e.name) CONTAINS toLower($search) THEN 0 ELSE 1 END,
                        mention_count DESC
                """
            else:
                order_clause = "ORDER BY mention_count DESC"

            if allowed_collection_ids:
                if where_parts:
                    data_query = f"""
                        {collection_match}
                        WHERE {' AND '.join(where_parts)}
                        OPTIONAL MATCH (c:Chunk)-[:MENTIONS]->(e)
                        WITH e, count(c) as mention_count
                        {order_clause}
                        SKIP $skip
                        LIMIT $limit
                        RETURN e.name as name, e.type as type, e.description as description,
                               mention_count
                    """
                else:
                    data_query = f"""
                        {collection_match}
                        OPTIONAL MATCH (c:Chunk)-[:MENTIONS]->(e)
                        WITH e, count(c) as mention_count
                        {order_clause}
                        SKIP $skip
                        LIMIT $limit
                        RETURN e.name as name, e.type as type, e.description as description,
                               mention_count
                    """
            else:
                data_query = f"""
                    MATCH (e:Entity)
                    {where_clause}
                    OPTIONAL MATCH (c:Chunk)-[:MENTIONS]->(e)
                    WITH e, count(c) as mention_count
                    {order_clause}
                    SKIP $skip
                    LIMIT $limit
                    RETURN e.name as name, e.type as type, e.description as description,
                           mention_count
                """
            entities = [dict(record) for record in session.run(data_query, **params)]

            return {"entities": entities, "total": total}

    @retry_on_transient
    def list_relationships_paginated(
        self,
        skip: int = 0,
        limit: int = 50,
        search: str = None,
        rel_type: str = None,
        allowed_collection_ids: Optional[List[str]] = None
    ) -> dict:
        """List relationships with server-side pagination, search, and filtering.
        
        Args:
            allowed_collection_ids: If provided, scope to relationships where at least one
                endpoint entity is from these collections (4-hop pattern).
        """
        with self.driver.session() as session:
            params = {"skip": skip, "limit": limit}

            # Build WHERE clauses
            where_parts = ["type(r) <> 'MENTIONS'", "type(r) <> 'HAS_MEMBER'", "type(r) <> 'FROM_DOCUMENT'", "type(r) <> 'CO_MENTION'"]

            if rel_type:
                where_parts.append("type(r) = $rel_type")
                params["rel_type"] = rel_type

            if search:
                where_parts.append("(toLower(s.name) CONTAINS toLower($search) OR toLower(t.name) CONTAINS toLower($search) OR toLower(coalesce(r.description, '')) CONTAINS toLower($search))")
                params["search"] = search

            where_clause = "WHERE " + " AND ".join(where_parts)

            # Collection scoping: pre-collect allowed entity names using 4-hop pattern
            if allowed_collection_ids:
                params["allowed_collection_ids"] = allowed_collection_ids
                collection_filter_clause = """
                    AND (EXISTS {
                        MATCH (col:Collection)-[:CONTAINS]->(d:Document)-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(s)
                        WHERE col.id IN $allowed_collection_ids
                    } OR EXISTS {
                        MATCH (col2:Collection)-[:CONTAINS]->(d2:Document)-[:HAS_CHUNK]->(c2:Chunk)-[:MENTIONS]->(t)
                        WHERE col2.id IN $allowed_collection_ids
                    })
                """
            else:
                collection_filter_clause = ""

            # Get total count
            count_query = f"""
                MATCH (s:Entity)-[r]->(t:Entity)
                {where_clause}
                {collection_filter_clause}
                RETURN count(r) as total
            """
            total = session.run(count_query, **params).single()["total"]

            # Get paginated results
            if search:
                order_clause = """
                    ORDER BY
                        CASE WHEN toLower(source) CONTAINS toLower($search) OR toLower(target) CONTAINS toLower($search) THEN 0 ELSE 1 END,
                        coalesce(weight, 0) DESC
                """
            else:
                order_clause = "ORDER BY coalesce(weight, 0) DESC"

            data_query = f"""
                MATCH (s:Entity)-[r]->(t:Entity)
                {where_clause}
                {collection_filter_clause}
                WITH s.name as source, t.name as target, type(r) as rel_type,
                     r.description as description, r.weight as weight
                {order_clause}
                SKIP $skip
                LIMIT $limit
                RETURN source, target, rel_type as type, description, weight
            """
            relationships = [dict(record) for record in session.run(data_query, **params)]

            return {"relationships": relationships, "total": total}

    @retry_on_transient
    def list_communities_paginated(
        self,
        skip: int = 0,
        limit: int = 50,
        search: str = None,
        allowed_collection_ids: Optional[List[str]] = None
    ) -> dict:
        """List communities with server-side pagination and search.
        
        Args:
            allowed_collection_ids: If provided, only return communities that have at least
                one member entity from these collections (5-hop pattern).
        """
        with self.driver.session() as session:
            params = {"skip": skip, "limit": limit}

            # Collection scoping: include only communities whose member entities
            # are reachable from the allowed collections (5-hop pattern)
            if allowed_collection_ids:
                params["allowed_collection_ids"] = allowed_collection_ids
                collection_filter = """
                    AND EXISTS {
                        MATCH (com)-[:HAS_MEMBER]->(member:Entity)
                        WHERE EXISTS {
                            MATCH (col:Collection)-[:CONTAINS]->(d:Document)-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(member)
                            WHERE col.id IN $allowed_collection_ids
                        }
                    }
                """
            else:
                collection_filter = ""

            if search:
                # For communities, we need to search in name, summary, and member names
                # Use a WITH clause to collect member info first, then filter
                count_query = f"""
                    MATCH (com:Community)
                    OPTIONAL MATCH (com)-[:HAS_MEMBER]->(e:Entity)
                    WITH com, count(e) as member_count, collect(e.name) as all_entity_names
                    WHERE (toLower(coalesce(com.name, '')) CONTAINS toLower($search)
                       OR toLower(coalesce(com.summary, '')) CONTAINS toLower($search)
                       OR any(n IN all_entity_names WHERE toLower(n) CONTAINS toLower($search)))
                    {collection_filter}
                    RETURN count(com) as total
                """
                params["search"] = search
                total = session.run(count_query, **params).single()["total"]

                data_query = f"""
                    MATCH (com:Community)
                    OPTIONAL MATCH (com)-[:HAS_MEMBER]->(e:Entity)
                    WITH com, count(e) as member_count,
                         collect(e.name)[0..5] as sample_entities,
                         collect(e.name) as all_entity_names
                    WHERE (toLower(coalesce(com.name, '')) CONTAINS toLower($search)
                       OR toLower(coalesce(com.summary, '')) CONTAINS toLower($search)
                       OR any(n IN all_entity_names WHERE toLower(n) CONTAINS toLower($search)))
                    {collection_filter}
                    ORDER BY
                        CASE WHEN toLower(coalesce(com.name, '')) CONTAINS toLower($search) THEN 0 ELSE 1 END,
                        member_count DESC
                    SKIP $skip
                    LIMIT $limit
                    RETURN com.id as id, com.name as name, com.summary as summary,
                           member_count as entity_count, sample_entities
                """
            else:
                count_query = f"""
                    MATCH (com:Community)
                    WHERE 1=1
                    {collection_filter}
                    RETURN count(com) as total
                """
                total = session.run(count_query, **params).single()["total"]

                data_query = f"""
                    MATCH (com:Community)
                    WHERE 1=1
                    {collection_filter}
                    OPTIONAL MATCH (com)-[:HAS_MEMBER]->(e:Entity)
                    WITH com, count(e) as member_count,
                         collect(e.name)[0..5] as sample_entities
                    ORDER BY member_count DESC
                    SKIP $skip
                    LIMIT $limit
                    RETURN com.id as id, com.name as name, com.summary as summary,
                           member_count as entity_count, sample_entities
                """

            communities = [dict(record) for record in session.run(data_query, **params)]

            return {"communities": communities, "total": total}

    @retry_on_transient
    def get_entity_types(self, allowed_collection_ids: Optional[List[str]] = None) -> List[str]:
        """Get all distinct entity types, optionally scoped to collections."""
        with self.driver.session() as session:
            if allowed_collection_ids:
                result = session.run("""
                    MATCH (col:Collection)-[:CONTAINS]->(d:Document)-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(e:Entity)
                    WHERE col.id IN $allowed_collection_ids
                    RETURN DISTINCT e.type as type
                    ORDER BY type
                """, allowed_collection_ids=allowed_collection_ids)
            else:
                result = session.run("""
                    MATCH (e:Entity)
                    RETURN DISTINCT e.type as type
                    ORDER BY type
                """)
            return [record["type"] for record in result if record["type"]]

    @retry_on_transient
    def get_relationship_types(self, allowed_collection_ids: Optional[List[str]] = None) -> List[str]:
        """Get all distinct relationship types (excluding internal types), optionally scoped to collections."""
        with self.driver.session() as session:
            if allowed_collection_ids:
                result = session.run("""
                    MATCH (col:Collection)-[:CONTAINS]->(d:Document)-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(s:Entity)
                    WHERE col.id IN $allowed_collection_ids
                    WITH DISTINCT s
                    MATCH (s)-[r]->(t:Entity)
                    WHERE type(r) <> 'MENTIONS' AND type(r) <> 'HAS_MEMBER'
                      AND type(r) <> 'FROM_DOCUMENT' AND type(r) <> 'CO_MENTION'
                    RETURN DISTINCT type(r) as type
                    ORDER BY type
                """, allowed_collection_ids=allowed_collection_ids)
            else:
                result = session.run("""
                    MATCH (s:Entity)-[r]->(t:Entity)
                    WHERE type(r) <> 'MENTIONS' AND type(r) <> 'HAS_MEMBER'
                      AND type(r) <> 'FROM_DOCUMENT' AND type(r) <> 'CO_MENTION'
                    RETURN DISTINCT type(r) as type
                    ORDER BY type
                """)
            return [record["type"] for record in result if record["type"]]

    @retry_on_transient
    def get_community_relationships(self, community_id: int, limit: int = 30) -> List[dict]:
        """Get relationships within a community."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (e1:Entity {community_id: $id})-[r]->(e2:Entity {community_id: $id})
                WHERE type(r) <> 'MENTIONS'
                RETURN e1.name as source,
                       e2.name as target,
                       type(r) as type,
                       r.description as description,
                       r.weight as weight
                ORDER BY r.weight DESC
                LIMIT $limit
            """, id=community_id, limit=limit)
            return [dict(record) for record in result]
    
    def delete_community(self, community_id: int) -> dict:
        """Delete a single community and unlink its member entities.

        Removes the Community node, its HAS_MEMBER relationships, and clears
        community_id from member entities.  Entities themselves are NOT deleted.

        Returns:
            Dict with deleted community id and count of unlinked entities.
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (com:Community {id: $id})
                OPTIONAL MATCH (com)-[:HAS_MEMBER]->(e:Entity)
                WITH com, collect(e) as members
                FOREACH (e IN members | SET e.community_id = null)
                WITH com, size(members) as unlinked
                DETACH DELETE com
                RETURN unlinked
            """, id=community_id)
            record = result.single()
            if record is None:
                return {"deleted": False, "community_id": community_id, "entities_unlinked": 0}
            logger.info(f"Deleted community {community_id}, unlinked {record['unlinked']} entities")
            return {"deleted": True, "community_id": community_id, "entities_unlinked": record["unlinked"]}

    def delete_all_communities(self) -> dict:
        """Delete ALL communities and unlink all member entities.

        Returns:
            Dict with count of deleted communities and unlinked entities.
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (com:Community)
                OPTIONAL MATCH (com)-[:HAS_MEMBER]->(e:Entity)
                WITH com, collect(e) as members
                FOREACH (e IN members | SET e.community_id = null)
                WITH com, size(members) as unlinked
                DETACH DELETE com
                RETURN count(com) as deleted, sum(unlinked) as total_unlinked
            """)
            record = result.single()
            deleted = record["deleted"] if record else 0
            unlinked = record["total_unlinked"] if record else 0
            logger.info(f"Deleted {deleted} communities, unlinked {unlinked} entities")
            return {"communities_deleted": deleted, "entities_unlinked": unlinked}

    def delete_all_relationships(self) -> dict:
        """Delete ALL relationships between entities (excluding MENTIONS from chunks).

        Batched via CALL {} IN TRANSACTIONS: a single-transaction delete (or
        the old collect()+FOREACH form) exceeds dbms.memory.transaction.total.max
        on large graphs (50k+ relationships → MemoryPoolOutOfMemoryError).

        Returns:
            Dict with count of deleted relationships.
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (:Entity)-[r]->(:Entity)
                WHERE type(r) <> 'MENTIONS'
                CALL {
                    WITH r
                    DELETE r
                } IN TRANSACTIONS OF 10000 ROWS
                RETURN count(*) as deleted
            """)
            record = result.single()
            deleted = record["deleted"] if record else 0
            logger.info(f"Deleted {deleted} relationships")
            return {"relationships_deleted": deleted}

    def delete_batch_relationships(self) -> dict:
        """Delete only batch-analysis relationships, preserving per-chunk relationships from Step 1.

        Returns:
            Dict with count of deleted relationships.
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (:Entity)-[r]->(:Entity)
                WHERE type(r) <> 'MENTIONS'
                AND coalesce(r.extraction_method, 'batch') <> 'per_chunk'
                CALL {
                    WITH r
                    DELETE r
                } IN TRANSACTIONS OF 10000 ROWS
                RETURN count(*) as deleted
            """)
            record = result.single()
            deleted = record["deleted"] if record else 0
            logger.info(f"Deleted {deleted} batch relationships (preserved per-chunk)")
            return {"relationships_deleted": deleted}

    def delete_all_entities(self) -> dict:
        """Delete ALL entities and their MENTIONS relationships from chunks.

        Batched via CALL {} IN TRANSACTIONS: a single-transaction DETACH
        DELETE over a large graph (29k entities / 50k+ relationships) exceeds
        dbms.memory.transaction.total.max (observed MemoryPoolOutOfMemoryError
        at 1.4 GiB on the 2026-07-03 rebuild graph).

        Returns:
            Dict with count of deleted entities.
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (e:Entity)
                CALL {
                    WITH e
                    DETACH DELETE e
                } IN TRANSACTIONS OF 5000 ROWS
                RETURN count(*) as deleted
            """)
            record = result.single()
            deleted = record["deleted"] if record else 0
            logger.info(f"Deleted {deleted} entities")
            return {"entities_deleted": deleted}

    def delete_all_merge_history(self) -> int:
        """Delete ALL MergeHistory nodes (deduplication audit trail).

        Returns:
            Number of MergeHistory nodes deleted.
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (h:MergeHistory)
                DETACH DELETE h
                RETURN count(h) as deleted
            """)
            record = result.single()
            deleted = record["deleted"] if record else 0
            logger.info(f"Deleted {deleted} merge history records")
            return deleted

    def delete_all_system_meta(self) -> int:
        """Delete ALL SystemMeta nodes (staleness timestamps, etc.).

        Returns:
            Number of SystemMeta nodes deleted.
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (m:SystemMeta)
                DETACH DELETE m
                RETURN count(m) as deleted
            """)
            record = result.single()
            deleted = record["deleted"] if record else 0
            logger.info(f"Deleted {deleted} system meta records")
            return deleted

    def search_communities_by_content(
        self,
        query: str,
        limit: int = 5,
        allowed_collection_ids: Optional[List[str]] = None
    ) -> List[dict]:
        """Search communities by their summary content.
        
        Args:
            allowed_collection_ids: If provided, only return communities with at least one
                member entity from these collections.
        """
        with self.driver.session() as session:
            try:
                if allowed_collection_ids:
                    result = session.run("""
                        CALL db.index.fulltext.queryNodes('community_summary_fulltext', $search_query)
                        YIELD node, score
                        WHERE EXISTS {
                            MATCH (node)-[:HAS_MEMBER]->(member:Entity)
                            WHERE EXISTS {
                                MATCH (col:Collection)-[:CONTAINS]->(d:Document)-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(member)
                                WHERE col.id IN $allowed_collection_ids
                            }
                        }
                        RETURN node.id as id,
                               node.name as name,
                               node.summary as summary,
                               node.entity_count as entity_count,
                               score
                        ORDER BY score DESC
                        LIMIT $limit
                    """, search_query=query, limit=limit, allowed_collection_ids=allowed_collection_ids)
                else:
                    result = session.run("""
                        CALL db.index.fulltext.queryNodes('community_summary_fulltext', $search_query)
                        YIELD node, score
                        RETURN node.id as id,
                               node.name as name,
                               node.summary as summary,
                               node.entity_count as entity_count,
                               score
                        ORDER BY score DESC
                        LIMIT $limit
                    """, search_query=query, limit=limit)
                return [dict(record) for record in result]
            except Exception as e:
                logger.warning(f"Community search failed: {e}")
                return []
    
    # =========================================================================
    # Semantic Entity Resolution
    # =========================================================================
    
    def find_similar_entities_by_embedding(
        self,
        entity_embedding: List[float],
        threshold: float = 0.85,
        limit: int = 5
    ) -> List[dict]:
        """Find entities with similar embeddings for semantic deduplication."""
        with self.driver.session() as session:
            try:
                result = session.run("""
                    CALL db.index.vector.queryNodes('entity_embedding', $limit, $embedding)
                    YIELD node, score
                    WHERE score >= $threshold
                    RETURN node.name as name,
                           node.type as type,
                           node.description as description,
                           node.community_id as community_id,
                           score as similarity
                    ORDER BY score DESC
                """, embedding=entity_embedding, threshold=threshold, limit=limit)
                return [dict(record) for record in result]
            except Exception as e:
                self._vector_search_failures += 1
                if not self._vector_search_failure_warned:
                    self._vector_search_failure_warned = True
                    logger.warning(
                        f"Entity embedding search failed — semantic dedup is "
                        f"degrading to Levenshtein-only (this is logged once; "
                        f"see vector_search_failures in /api/stats): {e}"
                    )
                else:
                    logger.debug(f"Entity embedding search failed: {e}")
                return []
    
    def store_entity_with_embedding(
        self,
        entity: Entity,
        chunk_id: str = None,
        document_id: str = None,
        embedding: Optional[List[float]] = None
    ) -> Tuple[str, bool]:
        """
        Store entity with embedding for semantic resolution.

        Returns:
            Tuple of (entity_name, is_new_entity)
        """
        with self.driver.session() as session:
            # Convert embedding to list if numpy array
            if embedding is not None and hasattr(embedding, 'tolist'):
                embedding = embedding.tolist()

            # Check for existing similar entities by embedding
            if embedding and self.settings.enable_semantic_entity_resolution:
                similar = self.find_similar_entities_by_embedding(
                    embedding,
                    threshold=self.settings.entity_similarity_threshold
                )

                if similar:
                    # Merge into existing entity
                    canonical_name = similar[0]["name"]

                    # Add as alias if names are different
                    if canonical_name.lower() != entity.name.lower():
                        self._add_entity_alias(canonical_name, entity.name)

                    # Link to chunk if provided
                    if chunk_id:
                        session.run("""
                            MATCH (e:Entity {name: $name})
                            MATCH (c:Chunk {id: $chunk_id})
                            MERGE (c)-[:MENTIONS]->(e)
                        """, name=canonical_name, chunk_id=chunk_id)

                    # Update document provenance
                    if document_id:
                        session.run("""
                            MATCH (e:Entity {name: $name})
                            SET e.source_documents = CASE
                                WHEN NOT $doc_id IN coalesce(e.source_documents, [])
                                THEN coalesce(e.source_documents, []) + $doc_id
                                ELSE coalesce(e.source_documents, [])
                            END,
                            e.extraction_count = coalesce(e.extraction_count, 0) + 1,
                            e.last_extracted_at = datetime()
                        """, name=canonical_name, doc_id=document_id)

                    logger.debug(f"Merged entity '{entity.name}' into '{canonical_name}' (similarity: {similar[0]['similarity']:.3f})")
                    return (canonical_name, False)

            # Also check Levenshtein as backup (catches typo variants)
            lev_similar = self.find_similar_entities(entity.name, threshold=0.85)
            if lev_similar and lev_similar[0]["similarity"] >= 0.85:
                canonical_name = lev_similar[0]["name"]
                if canonical_name.lower() != entity.name.lower():
                    self._add_entity_alias(canonical_name, entity.name)
                if chunk_id:
                    session.run("""
                        MATCH (e:Entity {name: $name})
                        MATCH (c:Chunk {id: $chunk_id})
                        MERGE (c)-[:MENTIONS]->(e)
                    """, name=canonical_name, chunk_id=chunk_id)
                # Update document provenance
                if document_id:
                    session.run("""
                        MATCH (e:Entity {name: $name})
                        SET e.source_documents = CASE
                            WHEN NOT $doc_id IN coalesce(e.source_documents, [])
                            THEN coalesce(e.source_documents, []) + $doc_id
                            ELSE coalesce(e.source_documents, [])
                        END,
                        e.extraction_count = coalesce(e.extraction_count, 0) + 1,
                        e.last_extracted_at = datetime()
                    """, name=canonical_name, doc_id=document_id)
                return (canonical_name, False)

            # Create new entity with embedding
            if chunk_id:
                result = session.run("""
                    MERGE (e:Entity {name: $name})
                    ON CREATE SET
                        e.type = $type,
                        e.description = $description,
                        e.embedding = $embedding,
                        e.source_documents = CASE WHEN $doc_id IS NOT NULL THEN [$doc_id] ELSE [] END,
                        e.extraction_count = 1,
                        e.created_at = datetime(),
                        e.last_extracted_at = datetime()
                    ON MATCH SET
                        e.type = CASE WHEN e.type IS NULL OR e.type = '' THEN $type ELSE e.type END,
                        e.description = CASE WHEN size(coalesce(e.description, '')) < size($description) THEN $description ELSE e.description END,
                        e.embedding = CASE WHEN e.embedding IS NULL OR size(e.embedding) <> size($embedding) THEN $embedding ELSE e.embedding END,
                        e.source_documents = CASE
                            WHEN $doc_id IS NOT NULL AND NOT $doc_id IN coalesce(e.source_documents, [])
                            THEN coalesce(e.source_documents, []) + $doc_id
                            ELSE coalesce(e.source_documents, [])
                        END,
                        e.extraction_count = coalesce(e.extraction_count, 0) + 1,
                        e.last_extracted_at = datetime()
                    WITH e
                    MATCH (c:Chunk {id: $chunk_id})
                    MERGE (c)-[:MENTIONS]->(e)
                    RETURN e.name as name
                """,
                    name=entity.name,
                    type=entity.type,
                    description=entity.description,
                    embedding=embedding,
                    chunk_id=chunk_id,
                    doc_id=document_id,
                )
            else:
                result = session.run("""
                    MERGE (e:Entity {name: $name})
                    ON CREATE SET
                        e.type = $type,
                        e.description = $description,
                        e.embedding = $embedding,
                        e.source_documents = CASE WHEN $doc_id IS NOT NULL THEN [$doc_id] ELSE [] END,
                        e.extraction_count = 1,
                        e.created_at = datetime(),
                        e.last_extracted_at = datetime()
                    ON MATCH SET
                        e.type = CASE WHEN e.type IS NULL OR e.type = '' THEN $type ELSE e.type END,
                        e.description = CASE WHEN size(coalesce(e.description, '')) < size($description) THEN $description ELSE e.description END,
                        e.embedding = CASE WHEN e.embedding IS NULL OR size(e.embedding) <> size($embedding) THEN $embedding ELSE e.embedding END,
                        e.source_documents = CASE
                            WHEN $doc_id IS NOT NULL AND NOT $doc_id IN coalesce(e.source_documents, [])
                            THEN coalesce(e.source_documents, []) + $doc_id
                            ELSE coalesce(e.source_documents, [])
                        END,
                        e.extraction_count = coalesce(e.extraction_count, 0) + 1,
                        e.last_extracted_at = datetime()
                    RETURN e.name as name
                """,
                    name=entity.name,
                    type=entity.type,
                    description=entity.description,
                    embedding=embedding,
                    doc_id=document_id,
                )

            record = result.single()
            return (record["name"] if record else entity.name, True)

    # =========================================================================
    # Batched KG writes (enable_batched_kg_writes)
    # =========================================================================
    # These reproduce the per-item semantics of store_entity_with_embedding /
    # store_entity_with_resolution / link_entity_to_chunk / store_relationship
    # in a handful of UNWIND round trips. The per-item methods stay the
    # default path; test_batched_writes.py asserts the parity contract.

    def resolve_entities_batch_by_embedding(
        self,
        rows: List[Tuple[int, List[float]]],
        threshold: float,
    ) -> dict:
        """Vector-index resolution for many embeddings in one round trip.

        Args:
            rows: (index, embedding) pairs.
        Returns:
            {index: {"name": canonical, "similarity": score}} — indices
            without a match above threshold are absent.
        """
        if not rows:
            return {}
        payload = [{"idx": i, "embedding": emb} for i, emb in rows]
        with self.driver.session() as session:
            try:
                result = session.run("""
                    UNWIND $rows AS row
                    CALL {
                        WITH row
                        CALL db.index.vector.queryNodes('entity_embedding', 5, row.embedding)
                        YIELD node, score
                        WITH node, score
                        WHERE score >= $threshold
                        RETURN node.name AS name, score
                        ORDER BY score DESC
                        LIMIT 1
                    }
                    RETURN row.idx AS idx, name, score AS similarity
                """, rows=payload, threshold=threshold)
                return {
                    r["idx"]: {"name": r["name"], "similarity": r["similarity"]}
                    for r in result
                }
            except Exception as e:
                self._vector_search_failures += 1
                logger.warning(
                    f"Batched entity embedding resolution failed — falling "
                    f"back to Levenshtein for this batch: {e}"
                )
                return {}

    def resolve_entities_batch_by_name(
        self,
        rows: List[Tuple[int, str]],
        threshold: float = 0.85,
    ) -> dict:
        """Levenshtein resolution for many names in one round trip.

        One full Entity scan serves the whole batch (vs one scan per entity
        on the per-item path). With entity_dedup_prefilter, each name only
        scores its top-50 fulltext candidates instead.
        """
        if not rows:
            return {}
        if self.settings.entity_dedup_prefilter:
            resolved = self._resolve_batch_by_name_prefiltered(rows, threshold)
            if resolved is not None:
                return resolved
        payload = [{"idx": i, "name": name} for i, name in rows]
        with self.driver.session() as session:
            try:
                result = session.run("""
                    UNWIND $rows AS row
                    CALL {
                        WITH row
                        MATCH (e:Entity)
                        WITH e, apoc.text.levenshteinSimilarity(
                            toLower(e.name), toLower(row.name)
                        ) AS similarity
                        WHERE similarity >= $threshold
                        RETURN e.name AS name, similarity
                        ORDER BY similarity DESC
                        LIMIT 1
                    }
                    RETURN row.idx AS idx, name, similarity
                """, rows=payload, threshold=threshold)
                return {
                    r["idx"]: {"name": r["name"], "similarity": r["similarity"]}
                    for r in result
                }
            except Exception as e:
                logger.warning(
                    f"Batched Levenshtein resolution failed (APOC missing?) — "
                    f"treating batch as unresolved: {e}"
                )
                return {}

    def _resolve_batch_by_name_prefiltered(
        self,
        rows: List[Tuple[int, str]],
        threshold: float,
    ) -> Optional[dict]:
        """Fulltext-prefiltered variant of resolve_entities_batch_by_name.

        Returns None on error so the caller falls back to the full scan.
        """
        payload = []
        for i, name in rows:
            sanitized = "".join(
                c if c.isalnum() else " " for c in name
            ).strip()
            if not sanitized:
                continue
            payload.append({
                "idx": i,
                "name": name,
                "query": " OR ".join(sanitized.split()),
            })
        if not payload:
            return {}
        with self.driver.session() as session:
            try:
                result = session.run("""
                    UNWIND $rows AS row
                    CALL {
                        WITH row
                        CALL db.index.fulltext.queryNodes(
                            'entity_name_fulltext', row.query, {limit: 50}
                        )
                        YIELD node
                        WITH node, apoc.text.levenshteinSimilarity(
                            toLower(node.name), toLower(row.name)
                        ) AS similarity
                        WHERE similarity >= $threshold
                        RETURN node.name AS name, similarity
                        ORDER BY similarity DESC
                        LIMIT 1
                    }
                    RETURN row.idx AS idx, name, similarity
                """, rows=payload, threshold=threshold)
                return {
                    r["idx"]: {"name": r["name"], "similarity": r["similarity"]}
                    for r in result
                }
            except Exception as e:
                logger.debug(
                    f"Prefiltered batch resolution failed, falling back to "
                    f"full scan: {e}"
                )
                return None

    def store_entities_batch(self, rows: List[dict]) -> int:
        """Create/merge many entities in one UNWIND call.

        Each row: {name, type, description, embedding (nullable), doc_id}.
        SET semantics are identical to store_entity_with_embedding's MERGE
        (existing type kept, longer description wins, first embedding wins,
        provenance appended, extraction_count incremented).
        """
        if not rows:
            return 0
        with self.driver.session() as session:
            result = session.run("""
                UNWIND $rows AS row
                MERGE (e:Entity {name: row.name})
                ON CREATE SET
                    e.type = row.type,
                    e.description = row.description,
                    e.embedding = row.embedding,
                    e.source_documents = CASE WHEN row.doc_id IS NOT NULL THEN [row.doc_id] ELSE [] END,
                    e.extraction_count = 1,
                    e.created_at = datetime(),
                    e.last_extracted_at = datetime()
                ON MATCH SET
                    e.type = CASE WHEN e.type IS NULL OR e.type = '' THEN row.type ELSE e.type END,
                    e.description = CASE WHEN size(coalesce(e.description, '')) < size(row.description) THEN row.description ELSE e.description END,
                    e.embedding = CASE WHEN e.embedding IS NULL OR (row.embedding IS NOT NULL AND size(e.embedding) <> size(row.embedding)) THEN row.embedding ELSE e.embedding END,
                    e.source_documents = CASE
                        WHEN row.doc_id IS NOT NULL AND NOT row.doc_id IN coalesce(e.source_documents, [])
                        THEN coalesce(e.source_documents, []) + row.doc_id
                        ELSE coalesce(e.source_documents, [])
                    END,
                    e.extraction_count = coalesce(e.extraction_count, 0) + 1,
                    e.last_extracted_at = datetime()
                RETURN count(e) AS stored
            """, rows=rows)
            record = result.single()
            return record["stored"] if record else 0

    def apply_entity_merges_batch(self, rows: List[dict]) -> int:
        """Apply many entity merges (alias + provenance) in one UNWIND call.

        Each row: {canonical, alias (nullable), doc_id (nullable)}. Mirrors
        the merge branch of store_entity_with_embedding: alias appended when
        different, document provenance appended, extraction_count += 1.
        """
        if not rows:
            return 0
        with self.driver.session() as session:
            result = session.run("""
                UNWIND $rows AS row
                MATCH (e:Entity {name: row.canonical})
                SET e.aliases = CASE
                        WHEN row.alias IS NULL THEN e.aliases
                        WHEN e.aliases IS NULL THEN [row.alias]
                        WHEN NOT row.alias IN e.aliases THEN e.aliases + row.alias
                        ELSE e.aliases
                    END,
                    e.source_documents = CASE
                        WHEN row.doc_id IS NOT NULL AND NOT row.doc_id IN coalesce(e.source_documents, [])
                        THEN coalesce(e.source_documents, []) + row.doc_id
                        ELSE coalesce(e.source_documents, [])
                    END,
                    e.extraction_count = coalesce(e.extraction_count, 0) + 1,
                    e.last_extracted_at = datetime()
                RETURN count(e) AS merged
            """, rows=rows)
            record = result.single()
            return record["merged"] if record else 0

    def link_entities_to_chunks_batch(
        self,
        pairs: List[dict],
        tx_size: int = 1000,
    ) -> int:
        """MERGE many (Chunk)-[:MENTIONS]->(Entity) links in UNWIND batches.

        Each pair: {chunk_id, entity_name}. Like link_entity_to_chunk, pairs
        whose chunk or entity doesn't exist are silently skipped.
        """
        if not pairs:
            return 0
        linked = 0
        with self.driver.session() as session:
            for start in range(0, len(pairs), tx_size):
                batch = pairs[start:start + tx_size]
                result = session.run("""
                    UNWIND $pairs AS p
                    MATCH (c:Chunk {id: p.chunk_id})
                    MATCH (e:Entity {name: p.entity_name})
                    MERGE (c)-[:MENTIONS]->(e)
                    RETURN count(*) AS linked
                """, pairs=batch)
                record = result.single()
                linked += record["linked"] if record else 0
        return linked

    def store_relationships_batch(
        self,
        relationships: List[Relationship],
        source_document_id: str = None,
        extraction_method: str = "per_document",
    ) -> int:
        """Store many relationships in one UNWIND + apoc.merge call.

        Self-referential relationships are skipped (as in store_relationship).
        Falls back to the per-item path when APOC is unavailable.
        Returns the number of relationships actually stored (relationships
        whose endpoints don't exist are skipped, matching the per-item path).
        """
        rows = [
            {
                "source": r.source,
                "target": r.target,
                "rel_type": r.relationship_type,
                "description": r.description,
                "weight": r.weight,
                "confidence": r.confidence,
            }
            for r in relationships
            if r.source.strip().lower() != r.target.strip().lower()
        ]
        if not rows:
            return 0
        with self.driver.session() as session:
            try:
                result = session.run("""
                    UNWIND $rows AS row
                    MATCH (s:Entity {name: row.source})
                    MATCH (t:Entity {name: row.target})
                    CALL apoc.merge.relationship(
                        s, row.rel_type, {},
                        {description: row.description, weight: row.weight,
                         confidence: row.confidence}, t
                    ) YIELD rel
                    SET rel.extracted_at = datetime(),
                        rel.extraction_method = $extraction_method,
                        rel.source_document_id = $source_doc_id
                    RETURN count(rel) AS stored
                """,
                    rows=rows,
                    extraction_method=extraction_method,
                    source_doc_id=source_document_id,
                )
                record = result.single()
                return record["stored"] if record else 0
            except Exception as e:
                logger.debug(
                    f"Batched relationship store failed (APOC missing?), "
                    f"falling back to per-item: {e}"
                )
        stored = 0
        for rel in relationships:
            try:
                if self.store_relationship(
                    rel,
                    source_document_id=source_document_id,
                    extraction_method=extraction_method,
                ):
                    stored += 1
            except Exception as e:
                logger.warning(f"Failed to store relationship in fallback: {e}")
        return stored

    # =========================================================================
    # Phase B checkpoints (enable_phaseb_checkpointing)
    # =========================================================================
    # A crash/redeploy mid-analysis no longer re-pays every batch's Phase 1 +
    # Phase 2 LLM cost: completed batches are skipped on resume and Phase 1
    # candidate pairs are reused across rounds.

    def get_phaseb_checkpoint(
        self, run_signature: str, batch_key: str
    ) -> Optional[dict]:
        """Return {round, phase2_done, candidates} for a batch, or None."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (p:PhaseBCheckpoint {run_signature: $sig, batch_key: $key})
                RETURN p.round as round, p.phase2_done as phase2_done,
                       p.candidates_json as candidates_json
            """, sig=run_signature, key=batch_key)
            record = result.single()
            if not record:
                return None
            import json as _json
            candidates = None
            if record["candidates_json"]:
                try:
                    candidates = [
                        tuple(pair) for pair in _json.loads(record["candidates_json"])
                    ]
                except (ValueError, TypeError):
                    candidates = None
            return {
                "round": record["round"],
                "phase2_done": bool(record["phase2_done"]),
                "candidates": candidates,
            }

    def upsert_phaseb_checkpoint(
        self,
        run_signature: str,
        batch_key: str,
        round_num: int,
        candidates: Optional[list] = None,
        phase2_done: Optional[bool] = None,
    ) -> None:
        """Create/update a batch checkpoint. Only provided fields are set."""
        import json as _json
        sets = ["p.round = $round", "p.updated_at = datetime()"]
        params = {"sig": run_signature, "key": batch_key, "round": round_num}
        if candidates is not None:
            sets.append("p.candidates_json = $candidates_json")
            params["candidates_json"] = _json.dumps(
                [list(pair) for pair in candidates]
            )
        if phase2_done is not None:
            sets.append("p.phase2_done = $phase2_done")
            params["phase2_done"] = phase2_done
        with self.driver.session() as session:
            session.run(f"""
                MERGE (p:PhaseBCheckpoint {{run_signature: $sig, batch_key: $key}})
                SET {", ".join(sets)}
            """, **params)

    def clear_phaseb_checkpoints(self, run_signature: Optional[str] = None) -> int:
        """Delete checkpoints — all of them, or those NOT matching a signature
        when one is given (stale runs)."""
        with self.driver.session() as session:
            if run_signature:
                result = session.run("""
                    MATCH (p:PhaseBCheckpoint)
                    WHERE p.run_signature <> $sig
                    DETACH DELETE p
                    RETURN count(p) as deleted
                """, sig=run_signature)
            else:
                result = session.run("""
                    MATCH (p:PhaseBCheckpoint)
                    DETACH DELETE p
                    RETURN count(p) as deleted
                """)
            record = result.single()
            return record["deleted"] if record else 0

    @retry_on_transient
    def get_stats(self, allowed_collection_ids: Optional[List[str]] = None) -> dict:
        """Get knowledge base and knowledge graph statistics.

        Args:
            allowed_collection_ids: If provided, scope document/entity/community counts
                to only those accessible from these collections.
        """
        with self.driver.session() as session:
            if allowed_collection_ids:
                # Scoped stats: counts only for the allowed collections
                result = session.run("""
                    MATCH (col:Collection)-[:CONTAINS]->(d:Document)
                    WHERE col.id IN $allowed_collection_ids
                    WITH DISTINCT d
                    WITH count(d) as doc_count,
                         sum(coalesce(d.file_size, 0)) as total_size

                    OPTIONAL MATCH (col2:Collection)-[:CONTAINS]->(d2:Document)-[:HAS_CHUNK]->(c:Chunk)
                    WHERE col2.id IN $allowed_collection_ids
                    WITH doc_count, total_size, count(c) as chunk_count

                    OPTIONAL MATCH (col3:Collection)-[:CONTAINS]->(d3:Document)-[:HAS_CHUNK]->(c3:Chunk)-[:MENTIONS]->(e:Entity)
                    WHERE col3.id IN $allowed_collection_ids
                    WITH doc_count, total_size, chunk_count, count(DISTINCT e) as entity_count

                    OPTIONAL MATCH (:Entity)-[r]->(:Entity)
                    WITH doc_count, total_size, chunk_count, entity_count,
                         count(r) as relationship_count,
                         count(CASE WHEN r.extraction_method = 'per_chunk' THEN 1 END) as per_chunk_rel_count

                    OPTIONAL MATCH (com:Community)
                    WITH doc_count, total_size, chunk_count, entity_count, relationship_count, per_chunk_rel_count, count(com) as community_count

                    OPTIONAL MATCH (col4:Collection)
                    WITH doc_count, total_size, chunk_count, entity_count, relationship_count, per_chunk_rel_count, community_count, count(col4) as collection_count

                    RETURN doc_count as document_count,
                           chunk_count,
                           total_size,
                           entity_count,
                           relationship_count,
                           community_count,
                           collection_count,
                           0 as pending_count,
                           doc_count as completed_count,
                           0 as failed_count,
                           0 as processing_count,
                           per_chunk_rel_count
                """, allowed_collection_ids=allowed_collection_ids)
            else:
                # NOTE: total_size MUST be aggregated before any OPTIONAL MATCH that
                # fans rows out (e.g. HAS_CHUNK), or sum(d.file_size) gets multiplied
                # by the per-doc chunk count and yields nonsense totals (~70× inflation
                # for typical corpora).
                result = session.run("""
                MATCH (d:Document)
                WITH count(d) as doc_count, sum(coalesce(d.file_size, 0)) as total_size

                OPTIONAL MATCH (:Document)-[:HAS_CHUNK]->(c:Chunk)
                WITH doc_count, total_size, count(c) as chunk_count

                OPTIONAL MATCH (e:Entity)
                WITH doc_count, chunk_count, total_size, count(e) as entity_count

                OPTIONAL MATCH (:Entity)-[r]->(:Entity)
                WITH doc_count, chunk_count, total_size, entity_count,
                     count(r) as relationship_count,
                     count(CASE WHEN r.extraction_method = 'per_chunk' THEN 1 END) as per_chunk_rel_count

                OPTIONAL MATCH (com:Community)
                WITH doc_count, chunk_count, total_size, entity_count, relationship_count, per_chunk_rel_count, count(com) as community_count

                OPTIONAL MATCH (col:Collection)
                WITH doc_count, chunk_count, total_size, entity_count, relationship_count, per_chunk_rel_count, community_count, count(col) as collection_count

                OPTIONAL MATCH (pending:Document)
                WHERE coalesce(pending.processing_status, 'pending') = 'pending'
                WITH doc_count, chunk_count, total_size, entity_count, relationship_count, per_chunk_rel_count, community_count, collection_count, count(pending) as pending_count

                OPTIONAL MATCH (completed:Document)
                WHERE completed.processing_status = 'completed'
                WITH doc_count, chunk_count, total_size, entity_count, relationship_count, per_chunk_rel_count, community_count, collection_count, pending_count, count(completed) as completed_count

                OPTIONAL MATCH (failed:Document)
                WHERE failed.processing_status = 'failed'
                WITH doc_count, chunk_count, total_size, entity_count, relationship_count, per_chunk_rel_count, community_count, collection_count, pending_count, completed_count, count(failed) as failed_count

                OPTIONAL MATCH (proc:Document)
                WHERE proc.processing_status IN ['processing', 'extracting']
                WITH doc_count, chunk_count, total_size, entity_count, relationship_count, per_chunk_rel_count, community_count, collection_count, pending_count, completed_count, failed_count, count(proc) as processing_count

                RETURN doc_count as document_count,
                       chunk_count,
                       total_size,
                       entity_count,
                       relationship_count,
                       community_count,
                       collection_count,
                       pending_count,
                       completed_count,
                       failed_count,
                       processing_count,
                       per_chunk_rel_count
            """)

            record = result.single()
            doc_count = record["document_count"]
            chunk_count = record["chunk_count"]
            completed_count = record["completed_count"]
            entity_count = record["entity_count"]

            # Get entity type breakdown
            entity_type_counts = {}
            avg_entity_mentions = 0.0
            if entity_count > 0:
                type_result = session.run("""
                    MATCH (e:Entity)
                    RETURN e.type as entity_type, count(e) as count, avg(coalesce(e.mention_count, 0)) as avg_mentions
                    ORDER BY count DESC
                """)
                total_mentions = 0.0
                total_entities = 0
                for type_record in type_result:
                    etype = type_record["entity_type"] or "Unknown"
                    ecount = type_record["count"]
                    entity_type_counts[etype] = ecount
                    total_mentions += type_record["avg_mentions"] * ecount
                    total_entities += ecount
                if total_entities > 0:
                    avg_entity_mentions = round(total_mentions / total_entities, 1)

            avg_chunks = round(chunk_count / completed_count, 1) if completed_count > 0 else 0.0

            return {
                "document_count": doc_count,
                "chunk_count": chunk_count,
                "total_size": record["total_size"] or 0,
                "entity_count": entity_count,
                "relationship_count": record["relationship_count"],
                "per_chunk_relationship_count": record["per_chunk_rel_count"],
                "community_count": record["community_count"],
                "collection_count": record["collection_count"],
                "pending_count": record["pending_count"],
                "completed_count": completed_count,
                "failed_count": record["failed_count"],
                "processing_count": record["processing_count"],
                "avg_chunks_per_doc": avg_chunks,
                "entity_type_counts": entity_type_counts,
                "avg_entity_mentions": avg_entity_mentions,
                "last_relationship_analysis_at": self._get_or_seed_analysis_timestamp(record["relationship_count"]),
                "last_community_detection_at": self._get_or_seed_detection_timestamp(record["community_count"]),
                "last_entity_merge_at": self._get_meta("last_entity_merge_at"),
                "vector_search_failures": self._vector_search_failures,
                "entity_relationship_ratio": round(
                    record["relationship_count"] / entity_count, 2
                ) if entity_count > 0 else 0.0,
                "relationship_target_ratio": get_settings().relationship_target_ratio,
            }

    def _get_or_seed_analysis_timestamp(self, relationship_count: int) -> str | None:
        """Get the last relationship analysis timestamp, seeding it if relationships exist but no timestamp."""
        ts = self._get_meta("last_relationship_analysis_at")
        if ts is None and relationship_count > 0:
            # Relationships exist from before timestamp tracking was added.
            # Seed with epoch so all existing completed docs are flagged as
            # needing re-analysis. Once the user re-analyzes, the real
            # timestamp replaces this.
            ts = "2000-01-01T00:00:00+00:00"
            self.set_meta("last_relationship_analysis_at", ts)
        return ts

    def _get_or_seed_detection_timestamp(self, community_count: int) -> str | None:
        """Get the last community detection timestamp, seeding it if communities exist but no timestamp."""
        ts = self._get_meta("last_community_detection_at")
        if ts is None and community_count > 0:
            ts = "2000-01-01T00:00:00+00:00"
            self.set_meta("last_community_detection_at", ts)
        return ts

    def set_meta(self, key: str, value: str) -> None:
        """Store a metadata value on a SystemMeta node."""
        with self.driver.session() as session:
            session.run(
                "MERGE (m:SystemMeta {key: $key}) SET m.value = $value",
                key=key, value=value
            )

    def _get_meta(self, key: str) -> str | None:
        """Retrieve a metadata value from a SystemMeta node."""
        with self.driver.session() as session:
            result = session.run(
                "MATCH (m:SystemMeta {key: $key}) RETURN m.value as value",
                key=key
            )
            record = result.single()
            return record["value"] if record else None

    # ------------------------------------------------------------------
    # Runtime settings (admin-editable overrides over env defaults).
    # Persisted as SystemMeta under a "setting:" namespace so they are
    # distinct from internal metadata (staleness timestamps, etc.).
    # ------------------------------------------------------------------
    def set_runtime_setting(self, key: str, value: bool) -> None:
        """Persist a boolean runtime-setting override."""
        self.set_meta(f"setting:{key}", "true" if value else "false")

    def get_runtime_setting(self, key: str, default: bool) -> bool:
        """Read a boolean runtime-setting override; return `default` if unset."""
        raw = self._get_meta(f"setting:{key}")
        if raw is None:
            return default
        return raw == "true"

    # =========================================================================
    # API Key Management
    # =========================================================================
    
    def create_api_key(
        self,
        key_id: str,
        name: str,
        key_prefix: str,
        key_hash: str,
        permissions: List[str],
        created_by: str = "admin",
        collection_scope: str = "all",
        allowed_collections: Optional[List[str]] = None
    ) -> Optional[dict]:
        """Create a new API key in the database.
        
        Args:
            key_id: Unique identifier for the key
            name: Human-readable name
            key_prefix: First 12 chars of key for identification
            key_hash: SHA-256 hash of the key
            permissions: List of permission strings ('read', 'manage')
            created_by: Who created this key
            collection_scope: 'all' for unrestricted, 'restricted' for collection-specific
            allowed_collections: List of collection IDs when scope is 'restricted'
        """
        allowed_collections = allowed_collections or []
        
        with self.driver.session() as session:
            # Create the API key node
            result = session.run("""
                CREATE (k:APIKey {
                    id: $id,
                    name: $name,
                    key_prefix: $key_prefix,
                    key_hash: $key_hash,
                    permissions: $permissions,
                    is_active: true,
                    created_at: datetime(),
                    created_by: $created_by,
                    collection_scope: $collection_scope
                })
                WITH k
                // Create HAS_ACCESS_TO relationships for restricted keys
                CALL {
                    WITH k
                    UNWIND CASE WHEN size($allowed_collections) > 0 THEN $allowed_collections ELSE [null] END AS coll_id
                    WITH k, coll_id
                    WHERE coll_id IS NOT NULL
                    MATCH (c:Collection {id: coll_id})
                    CREATE (k)-[:HAS_ACCESS_TO]->(c)
                    RETURN count(*) as created_relations
                }
                // Return the key with collection info
                OPTIONAL MATCH (k)-[:HAS_ACCESS_TO]->(c:Collection)
                RETURN k.id as id,
                       k.name as name,
                       k.key_prefix as key_prefix,
                       k.permissions as permissions,
                       k.is_active as is_active,
                       k.created_at as created_at,
                       k.created_by as created_by,
                       k.collection_scope as collection_scope,
                       collect(DISTINCT c.id) as allowed_collections,
                       collect(DISTINCT c.name) as allowed_collection_names
            """,
                id=key_id,
                name=name,
                key_prefix=key_prefix,
                key_hash=key_hash,
                permissions=permissions,
                created_by=created_by,
                collection_scope=collection_scope,
                allowed_collections=allowed_collections
            )
            
            record = result.single()
            if record:
                logger.info(f"Created API key: {name} ({key_id}) with scope: {collection_scope}")
                return dict(record)
            return None
    
    def ensure_admin_key_exists(self, admin_key_prefix: str = "admin") -> dict:
        """
        Ensure the admin API key has a record in Neo4j for usage tracking.
        
        The admin key is validated against an environment variable, not a stored hash,
        but we still need a record to track usage statistics.
        
        Args:
            admin_key_prefix: Prefix to identify the admin key
            
        Returns:
            The admin key record (created or existing)
        """
        with self.driver.session() as session:
            # Try to find existing admin key record
            result = session.run("""
                MATCH (k:APIKey {id: 'admin'})
                RETURN k.id as id,
                       coalesce(k.name, '') as name,
                       coalesce(k.key_prefix, '') as key_prefix,
                       coalesce(k.permissions, []) as permissions,
                       coalesce(k.is_active, true) as is_active,
                       coalesce(k.created_at, '') as created_at,
                       coalesce(k.created_by, '') as created_by,
                       coalesce(k.total_requests, 0) as total_requests
            """)
            
            record = result.single()
            if record:
                logger.debug("Admin API key record already exists")
                return dict(record)
            
            # Create admin key record (no hash stored - validated against env var)
            result = session.run("""
                CREATE (k:APIKey {
                    id: 'admin',
                    name: 'Admin API Key',
                    key_prefix: $key_prefix,
                    key_hash: 'ENV_VAR_AUTH',
                    permissions: ['read', 'manage'],
                    is_active: true,
                    created_at: datetime(),
                    created_by: 'system',
                    is_admin_key: true,
                    total_requests: 0,
                    error_count: 0
                })
                RETURN k.id as id,
                       k.name as name,
                       k.key_prefix as key_prefix,
                       k.permissions as permissions,
                       k.is_active as is_active,
                       k.created_at as created_at,
                       k.created_by as created_by
            """, key_prefix=admin_key_prefix)
            
            record = result.single()
            if record:
                logger.info("Created admin API key record for usage tracking")
                return dict(record)
            
            return {}
    
    def get_api_key_by_id(self, key_id: str) -> Optional[dict]:
        """Get an API key by its ID."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (k:APIKey {id: $id})
                OPTIONAL MATCH (k)-[:HAS_ACCESS_TO]->(c:Collection)
                RETURN k.id as id,
                       coalesce(k.name, '') as name,
                       coalesce(k.key_prefix, '') as key_prefix,
                       k.key_hash as key_hash,
                       coalesce(k.permissions, []) as permissions,
                       coalesce(k.is_active, true) as is_active,
                       coalesce(k.created_at, '') as created_at,
                       k.last_used_at as last_used_at,
                       coalesce(k.created_by, '') as created_by,
                       coalesce(k.collection_scope, 'all') as collection_scope,
                       collect(DISTINCT c.id) as allowed_collections,
                       collect(DISTINCT c.name) as allowed_collection_names
            """, id=key_id)
            
            record = result.single()
            if record:
                data = dict(record)
                # Filter out None values from collections
                data["allowed_collections"] = [c for c in data.get("allowed_collections", []) if c is not None]
                data["allowed_collection_names"] = [c for c in data.get("allowed_collection_names", []) if c is not None]
                return data
            return None
    
    @retry_on_transient
    def get_api_key_by_prefix(self, key_prefix: str) -> List[dict]:
        """Get API keys by their prefix (for validation lookup)."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (k:APIKey)
                WHERE k.key_prefix = $prefix AND coalesce(k.is_active, true) = true
                OPTIONAL MATCH (k)-[:HAS_ACCESS_TO]->(c:Collection)
                WITH k, collect(DISTINCT c.id) as coll_ids, collect(DISTINCT c.name) as coll_names
                RETURN k.id as id,
                       coalesce(k.name, '') as name,
                       k.key_prefix as key_prefix,
                       k.key_hash as key_hash,
                       coalesce(k.permissions, []) as permissions,
                       coalesce(k.is_active, true) as is_active,
                       coalesce(k.created_at, '') as created_at,
                       k.last_used_at as last_used_at,
                       coalesce(k.created_by, '') as created_by,
                       coalesce(k.collection_scope, 'all') as collection_scope,
                       coll_ids as allowed_collections,
                       coll_names as allowed_collection_names
            """, prefix=key_prefix)
            
            keys = []
            for record in result:
                data = dict(record)
                # Filter out None values from collections
                data["allowed_collections"] = [c for c in data.get("allowed_collections", []) if c is not None]
                data["allowed_collection_names"] = [c for c in data.get("allowed_collection_names", []) if c is not None]
                keys.append(data)
            return keys
    
    def list_api_keys(self) -> List[dict]:
        """List all API keys (without the hash)."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (k:APIKey)
                OPTIONAL MATCH (k)-[:HAS_ACCESS_TO]->(c:Collection)
                WITH k, collect(DISTINCT c.id) as coll_ids, collect(DISTINCT c.name) as coll_names
                RETURN k.id as id,
                       coalesce(k.name, '') as name,
                       coalesce(k.key_prefix, '') as key_prefix,
                       coalesce(k.permissions, []) as permissions,
                       coalesce(k.is_active, true) as is_active,
                       coalesce(k.created_at, '') as created_at,
                       k.last_used_at as last_used_at,
                       coalesce(k.created_by, '') as created_by,
                       coalesce(k.collection_scope, 'all') as collection_scope,
                       coll_ids as allowed_collections,
                       coll_names as allowed_collection_names
                ORDER BY k.created_at DESC
            """)
            
            keys = []
            for record in result:
                data = dict(record)
                # Filter out None values from collections
                data["allowed_collections"] = [c for c in data.get("allowed_collections", []) if c is not None]
                data["allowed_collection_names"] = [c for c in data.get("allowed_collection_names", []) if c is not None]
                keys.append(data)
            return keys
    
    def update_api_key(
        self,
        key_id: str,
        name: Optional[str] = None,
        permissions: Optional[List[str]] = None,
        is_active: Optional[bool] = None,
        collection_scope: Optional[str] = None,
        allowed_collections: Optional[List[str]] = None
    ) -> Optional[dict]:
        """Update an API key's properties.
        
        Args:
            key_id: The API key ID to update
            name: New name (optional)
            permissions: New permissions list (optional)
            is_active: New active status (optional)
            collection_scope: New collection scope - 'all' or 'restricted' (optional)
            allowed_collections: New list of allowed collection IDs (optional)
        """
        with self.driver.session() as session:
            # Build dynamic SET clause
            set_clauses = []
            params = {"id": key_id}
            
            if name is not None:
                set_clauses.append("k.name = $name")
                params["name"] = name
            if permissions is not None:
                set_clauses.append("k.permissions = $permissions")
                params["permissions"] = permissions
            if is_active is not None:
                set_clauses.append("k.is_active = $is_active")
                params["is_active"] = is_active
            if collection_scope is not None:
                set_clauses.append("k.collection_scope = $collection_scope")
                params["collection_scope"] = collection_scope
            
            # Handle collection relationships update
            if allowed_collections is not None:
                params["allowed_collections"] = allowed_collections
                
                # Delete existing HAS_ACCESS_TO relationships and create new ones
                session.run("""
                    MATCH (k:APIKey {id: $id})-[r:HAS_ACCESS_TO]->()
                    DELETE r
                """, id=key_id)
                
                if allowed_collections:
                    session.run("""
                        MATCH (k:APIKey {id: $id})
                        UNWIND $allowed_collections AS coll_id
                        MATCH (c:Collection {id: coll_id})
                        CREATE (k)-[:HAS_ACCESS_TO]->(c)
                    """, id=key_id, allowed_collections=allowed_collections)
            
            # Apply property updates if any
            if set_clauses:
                set_clause = ", ".join(set_clauses)
                session.run(f"""
                    MATCH (k:APIKey {{id: $id}})
                    SET {set_clause}
                """, **params)
            
            # Return updated key data
            return self.get_api_key_by_id(key_id)
    
    def delete_api_key(self, key_id: str) -> bool:
        """Delete an API key and all its relationships."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (k:APIKey {id: $id})
                DETACH DELETE k
                RETURN 1 as deleted
            """, id=key_id)
            
            record = result.single()
            if record:
                logger.info(f"Deleted API key: {key_id}")
                return True
            return False
    
    @retry_on_transient
    def update_api_key_last_used(self, key_id: str) -> None:
        """Update the last_used_at timestamp for an API key."""
        with self.driver.session() as session:
            session.run("""
                MATCH (k:APIKey {id: $id})
                SET k.last_used_at = datetime()
            """, id=key_id)
    
    # =========================================================================
    # API Key Usage Tracking
    # =========================================================================
    
    def record_api_key_usage(
        self,
        key_id: str,
        endpoint_category: str,
        is_error: bool = False,
        error_message: Optional[str] = None
    ) -> None:
        """
        Record a single API request for usage tracking.
        
        This method:
        1. Increments the total request counter on the APIKey node
        2. Updates/creates the daily usage log node
        3. Tracks endpoint-specific counts
        4. Records errors if applicable
        
        Args:
            key_id: The API key ID
            endpoint_category: Category of endpoint (ask, search, upload, documents, graph, other)
            is_error: Whether this request resulted in an error
            error_message: Error message if is_error is True
        """
        today = datetime.utcnow().strftime("%Y-%m-%d")
        
        with self.driver.session() as session:
            # Update API key stats and daily usage in a single transaction
            session.run("""
                MATCH (k:APIKey {id: $key_id})
                
                // Update total requests on the key
                SET k.total_requests = COALESCE(k.total_requests, 0) + 1,
                    k.last_used_at = datetime()
                
                // Handle error tracking
                WITH k
                CALL {
                    WITH k
                    WHERE $is_error = true
                    SET k.error_count = COALESCE(k.error_count, 0) + 1,
                        k.last_error_at = datetime(),
                        k.last_error_message = $error_message
                    RETURN 1 as dummy
                    UNION ALL
                    WITH k
                    WHERE $is_error = false
                    RETURN 0 as dummy
                }
                
                // Get or create daily usage log
                WITH k
                MERGE (log:APIKeyUsageLog {key_id: $key_id, date: $today})
                ON CREATE SET 
                    log.request_count = 1,
                    log.error_count = CASE WHEN $is_error THEN 1 ELSE 0 END,
                    log.endpoint_counts = {},
                    log.created_at = datetime()
                ON MATCH SET 
                    log.request_count = log.request_count + 1,
                    log.error_count = log.error_count + CASE WHEN $is_error THEN 1 ELSE 0 END
                
                // Create relationship if not exists
                MERGE (k)-[:HAS_USAGE]->(log)
                
                // Update endpoint counts in the log
                WITH log
                SET log.endpoint_counts = CASE 
                    WHEN log.endpoint_counts IS NULL THEN {`$endpoint_category`: 1}
                    ELSE apoc.map.setKey(
                        log.endpoint_counts, 
                        $endpoint_category, 
                        COALESCE(log.endpoint_counts[$endpoint_category], 0) + 1
                    )
                END
            """, 
                key_id=key_id, 
                today=today, 
                endpoint_category=endpoint_category,
                is_error=is_error,
                error_message=error_message
            )
    
    def record_api_key_usage_simple(
        self,
        key_id: str,
        endpoint_category: str,
        is_error: bool = False,
        error_message: Optional[str] = None
    ) -> None:
        """
        Record API usage without APOC dependency (simpler version).
        Uses multiple queries instead of complex APOC functions.
        """
        today = datetime.utcnow().strftime("%Y-%m-%d")
        
        with self.driver.session() as session:
            # Update API key counters
            if is_error:
                session.run("""
                    MATCH (k:APIKey {id: $key_id})
                    SET k.total_requests = COALESCE(k.total_requests, 0) + 1,
                        k.error_count = COALESCE(k.error_count, 0) + 1,
                        k.last_used_at = datetime(),
                        k.last_error_at = datetime(),
                        k.last_error_message = $error_message
                """, key_id=key_id, error_message=error_message)
            else:
                session.run("""
                    MATCH (k:APIKey {id: $key_id})
                    SET k.total_requests = COALESCE(k.total_requests, 0) + 1,
                        k.last_used_at = datetime()
                """, key_id=key_id)
            
            # Get or create daily usage log and update counts
            session.run("""
                MATCH (k:APIKey {id: $key_id})
                MERGE (log:APIKeyUsageLog {key_id: $key_id, date: $today})
                ON CREATE SET 
                    log.request_count = 0,
                    log.error_count = 0,
                    log.created_at = datetime()
                MERGE (k)-[:HAS_USAGE]->(log)
                SET log.request_count = log.request_count + 1,
                    log.error_count = log.error_count + CASE WHEN $is_error THEN 1 ELSE 0 END
            """, key_id=key_id, today=today, is_error=is_error)
            
            # Update endpoint counts using a property naming convention
            endpoint_prop = f"ep_{endpoint_category}"
            session.run(f"""
                MATCH (log:APIKeyUsageLog {{key_id: $key_id, date: $today}})
                SET log.{endpoint_prop} = COALESCE(log.{endpoint_prop}, 0) + 1
            """, key_id=key_id, today=today)
    
    def get_api_key_stats(self, key_id: str) -> Optional[dict]:
        """
        Get comprehensive usage statistics for an API key.
        
        Returns stats including:
        - Total requests all time
        - Requests today/this week/this month
        - Error counts
        - Endpoint breakdown
        """
        today = datetime.utcnow()
        today_str = today.strftime("%Y-%m-%d")
        
        # Calculate date boundaries
        week_start = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
        month_start = today.strftime("%Y-%m-01")
        
        with self.driver.session() as session:
            result = session.run("""
                MATCH (k:APIKey {id: $key_id})
                
                // Get requests today
                OPTIONAL MATCH (k)-[:HAS_USAGE]->(today_log:APIKeyUsageLog {date: $today_str})
                
                // Get requests this week
                OPTIONAL MATCH (k)-[:HAS_USAGE]->(week_log:APIKeyUsageLog)
                WHERE week_log.date >= $week_start
                
                // Get requests this month
                OPTIONAL MATCH (k)-[:HAS_USAGE]->(month_log:APIKeyUsageLog)
                WHERE month_log.date >= $month_start
                
                // Get all logs for endpoint breakdown
                OPTIONAL MATCH (k)-[:HAS_USAGE]->(all_log:APIKeyUsageLog)
                
                RETURN k.id as id,
                       coalesce(k.name, '') as name,
                       COALESCE(k.total_requests, 0) as total_requests,
                       COALESCE(k.error_count, 0) as error_count,
                       k.last_error_at as last_error_at,
                       coalesce(k.last_error_message, '') as last_error_message,
                       COALESCE(today_log.request_count, 0) as requests_today,
                       COALESCE(SUM(week_log.request_count), 0) as requests_this_week,
                       COALESCE(SUM(month_log.request_count), 0) as requests_this_month,
                       COLLECT(DISTINCT all_log) as all_logs
            """, 
                key_id=key_id,
                today_str=today_str,
                week_start=week_start,
                month_start=month_start
            )
            
            record = result.single()
            if not record or record["id"] is None:
                return None
            
            # Aggregate endpoint breakdown from logs
            endpoint_breakdown = {}
            for log in record["all_logs"]:
                if log:
                    log_dict = dict(log)
                    for key, value in log_dict.items():
                        if key.startswith("ep_") and isinstance(value, (int, float)):
                            endpoint = key[3:]  # Remove "ep_" prefix
                            endpoint_breakdown[endpoint] = endpoint_breakdown.get(endpoint, 0) + int(value)
            
            return {
                "total_requests": record["total_requests"],
                "requests_today": record["requests_today"],
                "requests_this_week": int(record["requests_this_week"]) if record["requests_this_week"] else 0,
                "requests_this_month": int(record["requests_this_month"]) if record["requests_this_month"] else 0,
                "error_count": record["error_count"],
                "last_error_at": record["last_error_at"],
                "last_error_message": record["last_error_message"],
                "endpoint_breakdown": endpoint_breakdown
            }
    
    def get_api_key_usage_history(self, key_id: str, days: int = 30) -> List[dict]:
        """
        Get daily usage history for an API key.
        
        Args:
            key_id: The API key ID
            days: Number of days of history to retrieve
            
        Returns:
            List of {date, requests, errors} for each day
        """
        start_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        with self.driver.session() as session:
            result = session.run("""
                MATCH (k:APIKey {id: $key_id})-[:HAS_USAGE]->(log:APIKeyUsageLog)
                WHERE log.date >= $start_date
                RETURN log.date as date,
                       log.request_count as requests,
                       log.error_count as errors
                ORDER BY log.date ASC
            """, key_id=key_id, start_date=start_date)
            
            return [
                {
                    "date": record["date"],
                    "requests": record["requests"] or 0,
                    "errors": record["errors"] or 0
                }
                for record in result
            ]
    
    def get_admin_stats_overview(self) -> dict:
        """
        Get aggregated statistics across all API keys.
        """
        today = datetime.utcnow()
        today_str = today.strftime("%Y-%m-%d")
        week_start = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
        month_start = today.strftime("%Y-%m-01")
        
        with self.driver.session() as session:
            result = session.run("""
                MATCH (k:APIKey)
                
                // Get today's logs
                OPTIONAL MATCH (k)-[:HAS_USAGE]->(today_log:APIKeyUsageLog {date: $today_str})
                
                // Get week's logs
                OPTIONAL MATCH (k)-[:HAS_USAGE]->(week_log:APIKeyUsageLog)
                WHERE week_log.date >= $week_start
                
                // Get month's logs
                OPTIONAL MATCH (k)-[:HAS_USAGE]->(month_log:APIKeyUsageLog)
                WHERE month_log.date >= $month_start
                
                // Get all logs for endpoint breakdown
                OPTIONAL MATCH (k)-[:HAS_USAGE]->(all_log:APIKeyUsageLog)
                
                WITH k, 
                     COALESCE(SUM(today_log.request_count), 0) as today_requests,
                     COALESCE(SUM(week_log.request_count), 0) as week_requests,
                     COALESCE(SUM(month_log.request_count), 0) as month_requests,
                     COLLECT(DISTINCT all_log) as logs
                
                RETURN COUNT(k) as total_keys,
                       SUM(CASE WHEN k.is_active THEN 1 ELSE 0 END) as active_keys,
                       SUM(COALESCE(k.total_requests, 0)) as total_requests_all_time,
                       SUM(today_requests) as total_requests_today,
                       SUM(week_requests) as total_requests_this_week,
                       SUM(month_requests) as total_requests_this_month,
                       SUM(COALESCE(k.error_count, 0)) as total_errors,
                       COLLECT({name: k.name, requests: k.total_requests}) as keys_with_requests,
                       COLLECT(logs) as all_logs
            """, 
                today_str=today_str,
                week_start=week_start,
                month_start=month_start
            )
            
            record = result.single()
            if not record:
                return {
                    "total_keys": 0,
                    "active_keys": 0,
                    "total_requests_all_time": 0,
                    "total_requests_today": 0,
                    "total_requests_this_week": 0,
                    "total_requests_this_month": 0,
                    "total_errors": 0,
                    "most_active_key": None,
                    "endpoint_breakdown": {}
                }
            
            # Find most active key
            most_active_key = None
            max_requests = 0
            for key_info in record["keys_with_requests"]:
                if key_info and key_info.get("requests", 0) and key_info["requests"] > max_requests:
                    max_requests = key_info["requests"]
                    most_active_key = key_info.get("name")
            
            # Aggregate endpoint breakdown
            endpoint_breakdown = {}
            for log_list in record["all_logs"]:
                if log_list:
                    for log in log_list:
                        if log:
                            log_dict = dict(log)
                            for key, value in log_dict.items():
                                if key.startswith("ep_") and isinstance(value, (int, float)):
                                    endpoint = key[3:]
                                    endpoint_breakdown[endpoint] = endpoint_breakdown.get(endpoint, 0) + int(value)
            
            return {
                "total_keys": record["total_keys"] or 0,
                "active_keys": int(record["active_keys"]) if record["active_keys"] else 0,
                "total_requests_all_time": int(record["total_requests_all_time"]) if record["total_requests_all_time"] else 0,
                "total_requests_today": int(record["total_requests_today"]) if record["total_requests_today"] else 0,
                "total_requests_this_week": int(record["total_requests_this_week"]) if record["total_requests_this_week"] else 0,
                "total_requests_this_month": int(record["total_requests_this_month"]) if record["total_requests_this_month"] else 0,
                "total_errors": int(record["total_errors"]) if record["total_errors"] else 0,
                "most_active_key": most_active_key,
                "endpoint_breakdown": endpoint_breakdown
            }

    def increment_llm_completions(self, date_str: str, by_kind: dict) -> None:
        """Add completion counts to the LLMUsageDay node for ``date_str``.

        One node per UTC day; ``by_kind`` maps usage kinds ("query",
        "processing", "other") to increments. Written by the usage_meter
        flusher in coalesced batches, never per completion.
        """
        total = sum(by_kind.values())
        if total <= 0:
            return
        with self.driver.session() as session:
            # NB: kwargs can't be named `query` — that collides with the
            # driver's own Session.run(query, ...) first argument.
            session.run(
                """
                MERGE (u:LLMUsageDay {date: $date})
                SET u.completions = COALESCE(u.completions, 0) + $total,
                    u.completions_query = COALESCE(u.completions_query, 0) + $query_n,
                    u.completions_processing = COALESCE(u.completions_processing, 0) + $processing_n
                """,
                date=date_str,
                total=total,
                query_n=int(by_kind.get("query", 0)),
                processing_n=int(by_kind.get("processing", 0)),
            )

    def get_llm_completion_count_this_month(self) -> dict:
        """LLM completion counts for the current UTC calendar month.

        Used to enforce MAX_QUERIES_PER_MONTH, which is denominated in internal
        LLM completions (unit-based quota). Returns totals plus the
        query/processing attribution for the frontend usage meter.
        """
        month_start = datetime.utcnow().strftime("%Y-%m-01")
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (u:LLMUsageDay)
                WHERE u.date >= $month_start
                RETURN COALESCE(SUM(u.completions), 0) AS total,
                       COALESCE(SUM(u.completions_query), 0) AS query,
                       COALESCE(SUM(u.completions_processing), 0) AS processing
                """,
                month_start=month_start,
            )
            record = result.single()
            if not record:
                return {"total": 0, "query": 0, "processing": 0}
            return {
                "total": int(record["total"] or 0),
                "query": int(record["query"] or 0),
                "processing": int(record["processing"] or 0),
            }

    def list_api_keys_with_stats(self) -> List[dict]:
        """
        List all API keys with their usage statistics.
        """
        today = datetime.utcnow()
        today_str = today.strftime("%Y-%m-%d")
        week_start = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
        month_start = today.strftime("%Y-%m-01")
        
        with self.driver.session() as session:
            result = session.run("""
                MATCH (k:APIKey)
                
                // Get collection access info
                OPTIONAL MATCH (k)-[:HAS_ACCESS_TO]->(c:Collection)
                WITH k, collect(DISTINCT c.id) as coll_ids, collect(DISTINCT c.name) as coll_names
                
                // Get today's log
                OPTIONAL MATCH (k)-[:HAS_USAGE]->(today_log:APIKeyUsageLog {date: $today_str})
                
                // Get week's logs
                OPTIONAL MATCH (k)-[:HAS_USAGE]->(week_log:APIKeyUsageLog)
                WHERE week_log.date >= $week_start
                
                // Get month's logs  
                OPTIONAL MATCH (k)-[:HAS_USAGE]->(month_log:APIKeyUsageLog)
                WHERE month_log.date >= $month_start
                
                // Get all logs for endpoint breakdown
                OPTIONAL MATCH (k)-[:HAS_USAGE]->(all_log:APIKeyUsageLog)
                
                WITH k, coll_ids, coll_names,
                     COALESCE(today_log.request_count, 0) as requests_today,
                     COLLECT(DISTINCT week_log) as week_logs,
                     COLLECT(DISTINCT month_log) as month_logs,
                     COLLECT(DISTINCT all_log) as all_logs
                
                RETURN k.id as id,
                       k.name as name,
                       k.key_prefix as key_prefix,
                       k.permissions as permissions,
                       k.is_active as is_active,
                       k.created_at as created_at,
                       k.last_used_at as last_used_at,
                       k.created_by as created_by,
                       coalesce(k.collection_scope, 'all') as collection_scope,
                       coll_ids as allowed_collections,
                       coll_names as allowed_collection_names,
                       COALESCE(k.total_requests, 0) as total_requests,
                       COALESCE(k.error_count, 0) as error_count,
                       k.last_error_at as last_error_at,
                       k.last_error_message as last_error_message,
                       requests_today,
                       REDUCE(s = 0, log IN week_logs | s + COALESCE(log.request_count, 0)) as requests_this_week,
                       REDUCE(s = 0, log IN month_logs | s + COALESCE(log.request_count, 0)) as requests_this_month,
                       all_logs
                ORDER BY k.created_at DESC
            """,
                today_str=today_str,
                week_start=week_start,
                month_start=month_start
            )
            
            keys = []
            for record in result:
                # Aggregate endpoint breakdown
                endpoint_breakdown = {}
                for log in record["all_logs"]:
                    if log:
                        log_dict = dict(log)
                        for key, value in log_dict.items():
                            if key.startswith("ep_") and isinstance(value, (int, float)):
                                endpoint = key[3:]
                                endpoint_breakdown[endpoint] = endpoint_breakdown.get(endpoint, 0) + int(value)
                
                # Filter out None values from collections
                allowed_collections = [c for c in record.get("allowed_collections", []) if c is not None]
                allowed_collection_names = [c for c in record.get("allowed_collection_names", []) if c is not None]
                
                keys.append({
                    "id": record["id"],
                    "name": record["name"],
                    "key_prefix": record["key_prefix"],
                    "permissions": record["permissions"] or [],
                    "is_active": record["is_active"],
                    "created_at": record["created_at"],
                    "last_used_at": record["last_used_at"],
                    "created_by": record["created_by"],
                    "collection_scope": record["collection_scope"],
                    "allowed_collections": allowed_collections,
                    "allowed_collection_names": allowed_collection_names,
                    "stats": {
                        "total_requests": record["total_requests"],
                        "requests_today": record["requests_today"],
                        "requests_this_week": record["requests_this_week"] or 0,
                        "requests_this_month": record["requests_this_month"] or 0,
                        "error_count": record["error_count"],
                        "last_error_at": record["last_error_at"],
                        "last_error_message": record["last_error_message"],
                        "endpoint_breakdown": endpoint_breakdown
                    }
                })
            
            return keys
    
    # =========================================================================
    # System Reset Operations
    # =========================================================================
    
    def delete_all_collections(self) -> int:
        """
        Delete all non-default collections.
        
        Documents in deleted collections are moved to the default collection first.
        
        Returns:
            Number of collections deleted
        """
        with self.driver.session() as session:
            # First, move all documents from non-default collections to default
            session.run("""
                MATCH (col:Collection)-[:CONTAINS]->(d:Document)
                WHERE col.id <> 'default'
                MATCH (default_col:Collection {id: 'default'})
                MERGE (default_col)-[:CONTAINS]->(d)
                SET d.collection_id = 'default'
                WITH col, d
                MATCH (col)-[r:CONTAINS]->(d)
                DELETE r
            """)
            
            # Then delete all non-default collections
            result = session.run("""
                MATCH (col:Collection)
                WHERE col.id <> 'default'
                WITH collect(col) as collections
                UNWIND collections as col
                DETACH DELETE col
                RETURN count(*) as deleted
            """)
            
            deleted_count = result.single()["deleted"]
            logger.info(f"Deleted {deleted_count} non-default collections")
            return deleted_count
    
    def delete_all_api_keys(self) -> int:
        """
        Delete all API keys from the system.
        
        WARNING: This will remove all API keys, requiring new ones to be created.
        
        Returns:
            Number of API keys deleted
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (k:APIKey)
                WITH collect(k) as keys
                UNWIND keys as k
                DELETE k
                RETURN count(*) as deleted
            """)
            
            deleted_count = result.single()["deleted"]
            logger.info(f"Deleted {deleted_count} API keys")
            return deleted_count

    # =========================================================================
    # Library Export Methods
    # =========================================================================

    def export_all_documents(self) -> list:
        """Get all Document nodes with all properties for export."""
        with self.driver.session() as session:
            result = session.run("MATCH (d:Document) RETURN d{.*} as doc")
            return [record["doc"] for record in result]

    def export_all_chunks_batched(self, batch_size: int = 500, skip: int = 0) -> list:
        """Get chunks in batches with their parent document ID."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)
                RETURN c{.*} as chunk, d.id as document_id
                ORDER BY c.id
                SKIP $skip LIMIT $batch_size
            """, skip=skip, batch_size=batch_size)
            return [{"chunk": record["chunk"], "document_id": record["document_id"]} for record in result]

    def export_chunk_count(self) -> int:
        """Get total chunk count for batched export progress tracking."""
        with self.driver.session() as session:
            result = session.run("MATCH (:Document)-[:HAS_CHUNK]->(c:Chunk) RETURN count(c) as cnt")
            return result.single()["cnt"]

    def export_all_entities(self) -> list:
        """Get all Entity nodes with all properties for export."""
        with self.driver.session() as session:
            result = session.run("MATCH (e:Entity) RETURN e{.*} as entity")
            return [record["entity"] for record in result]

    def export_entity_count(self) -> int:
        """Get total entity count for batched export progress tracking."""
        with self.driver.session() as session:
            result = session.run("MATCH (e:Entity) RETURN count(e) as cnt")
            return result.single()["cnt"]

    def export_all_entities_batched(self, batch_size: int = 500, skip: int = 0) -> list:
        """Get entities in batches (includes embeddings) for streaming export.

        Entity name is the unique key, so ORDER BY e.name gives a stable
        pagination order across SKIP/LIMIT windows.
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (e:Entity)
                RETURN e{.*} as entity
                ORDER BY e.name
                SKIP $skip LIMIT $batch_size
            """, skip=skip, batch_size=batch_size)
            return [record["entity"] for record in result]

    def export_all_entity_relationships(self) -> list:
        """Get all Entity-Entity relationships with type and properties."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (s:Entity)-[r]->(t:Entity)
                RETURN s.name as source, t.name as target, type(r) as rel_type,
                       r.description as description, r.weight as weight,
                       r.confidence as confidence,
                       r.extraction_method as extraction_method,
                       r.source_document_id as source_document_id,
                       r.extracted_at as extracted_at
            """)
            return [dict(record) for record in result]

    def export_relationship_count(self) -> int:
        """Get total Entity-Entity relationship count for batched export progress."""
        with self.driver.session() as session:
            result = session.run("MATCH (:Entity)-[r]->(:Entity) RETURN count(r) as cnt")
            return result.single()["cnt"]

    def export_all_entity_relationships_batched(self, batch_size: int = 500, skip: int = 0) -> list:
        """Get Entity-Entity relationships in batches for streaming export.

        Ordered by elementId(r) — unique and stable within a read snapshot — so
        parallel edges between the same pair can't be skipped or duplicated across
        SKIP/LIMIT windows.
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (s:Entity)-[r]->(t:Entity)
                RETURN s.name as source, t.name as target, type(r) as rel_type,
                       r.description as description, r.weight as weight,
                       r.confidence as confidence,
                       r.extraction_method as extraction_method,
                       r.source_document_id as source_document_id,
                       r.extracted_at as extracted_at
                ORDER BY elementId(r)
                SKIP $skip LIMIT $batch_size
            """, skip=skip, batch_size=batch_size)
            return [dict(record) for record in result]

    def export_all_communities(self) -> list:
        """Get all Community nodes with all properties."""
        with self.driver.session() as session:
            result = session.run("MATCH (com:Community) RETURN com{.*} as community")
            return [record["community"] for record in result]

    def export_community_members(self) -> list:
        """Get all Community→Entity HAS_MEMBER edges."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (com:Community)-[:HAS_MEMBER]->(e:Entity)
                RETURN com.id as community_id, e.name as entity_name
            """)
            return [dict(record) for record in result]

    def export_all_collections(self) -> list:
        """Get all Collection nodes with all properties."""
        with self.driver.session() as session:
            result = session.run("MATCH (col:Collection) RETURN col{.*} as collection")
            return [record["collection"] for record in result]

    def export_collection_members(self) -> list:
        """Get all Collection→Document CONTAINS edges."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (col:Collection)-[:CONTAINS]->(d:Document)
                RETURN col.id as collection_id, d.id as document_id
            """)
            return [dict(record) for record in result]

    def export_all_chunk_mentions(self) -> list:
        """Get all Chunk→Entity MENTIONS edges."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (c:Chunk)-[:MENTIONS]->(e:Entity)
                RETURN c.id as chunk_id, e.name as entity_name
            """)
            return [dict(record) for record in result]

    def export_all_merge_history(self) -> list:
        """Get all MergeHistory nodes."""
        with self.driver.session() as session:
            result = session.run("MATCH (h:MergeHistory) RETURN h{.*} as history")
            return [record["history"] for record in result]

    def export_all_system_meta(self) -> list:
        """Get all SystemMeta nodes."""
        with self.driver.session() as session:
            result = session.run("MATCH (m:SystemMeta) RETURN m.key as key, m.value as value")
            return [dict(record) for record in result]

    # =========================================================================
    # Library Import Methods
    # =========================================================================

    def import_documents_batch(self, documents: list) -> int:
        """Bulk create Document nodes. Returns count created."""
        with self.driver.session() as session:
            result = session.run("""
                UNWIND $docs as doc
                CREATE (d:Document)
                SET d = doc
                RETURN count(d) as cnt
            """, docs=documents)
            return result.single()["cnt"]

    def import_chunks_batch(self, chunks: list) -> int:
        """Bulk create Chunk nodes and HAS_CHUNK edges. Each item must have 'chunk' and 'document_id'."""
        with self.driver.session() as session:
            result = session.run("""
                UNWIND $items as item
                MATCH (d:Document {id: item.document_id})
                CREATE (c:Chunk)
                SET c = item.chunk
                CREATE (d)-[:HAS_CHUNK]->(c)
                RETURN count(c) as cnt
            """, items=chunks)
            return result.single()["cnt"]

    def import_entities_batch(self, entities: list) -> int:
        """Bulk create Entity nodes. Returns count created."""
        with self.driver.session() as session:
            result = session.run("""
                UNWIND $entities as entity
                CREATE (e:Entity)
                SET e = entity
                RETURN count(e) as cnt
            """, entities=entities)
            return result.single()["cnt"]

    def import_chunk_mentions_batch(self, mentions: list) -> int:
        """Bulk create Chunk→Entity MENTIONS edges."""
        with self.driver.session() as session:
            result = session.run("""
                UNWIND $mentions as m
                MATCH (c:Chunk {id: m.chunk_id})
                MATCH (e:Entity {name: m.entity_name})
                CREATE (c)-[:MENTIONS]->(e)
                RETURN count(*) as cnt
            """, mentions=mentions)
            return result.single()["cnt"]

    def import_relationship(self, source: str, target: str, rel_type: str, props: dict) -> bool:
        """Create a single Entity-Entity relationship with dynamic type via APOC."""
        with self.driver.session() as session:
            try:
                result = session.run("""
                    MATCH (s:Entity {name: $source})
                    MATCH (t:Entity {name: $target})
                    CALL apoc.merge.relationship(s, $rel_type, {},
                        {description: $description, weight: $weight, confidence: $confidence,
                         extraction_method: $extraction_method, source_document_id: $source_doc_id,
                         extracted_at: $extracted_at}, t) YIELD rel
                    RETURN type(rel) as rel_type
                """,
                    source=source, target=target, rel_type=rel_type,
                    description=props.get("description", ""),
                    weight=props.get("weight", 5.0),
                    confidence=props.get("confidence"),
                    extraction_method=props.get("extraction_method", ""),
                    source_doc_id=props.get("source_document_id"),
                    extracted_at=props.get("extracted_at"),
                )
                return result.single() is not None
            except Exception:
                result = session.run("""
                    MATCH (s:Entity {name: $source})
                    MATCH (t:Entity {name: $target})
                    CREATE (s)-[r:RELATED_TO]->(t)
                    SET r.type = $rel_type, r.description = $description,
                        r.weight = $weight, r.confidence = $confidence,
                        r.extraction_method = $extraction_method,
                        r.source_document_id = $source_doc_id,
                        r.extracted_at = $extracted_at
                    RETURN type(r) as rel_type
                """,
                    source=source, target=target, rel_type=rel_type,
                    description=props.get("description", ""),
                    weight=props.get("weight", 5.0),
                    confidence=props.get("confidence"),
                    extraction_method=props.get("extraction_method", ""),
                    source_doc_id=props.get("source_document_id"),
                    extracted_at=props.get("extracted_at"),
                )
                return result.single() is not None

    def import_communities_batch(self, communities: list) -> int:
        """Bulk create Community nodes."""
        with self.driver.session() as session:
            result = session.run("""
                UNWIND $communities as com
                CREATE (c:Community)
                SET c = com
                RETURN count(c) as cnt
            """, communities=communities)
            return result.single()["cnt"]

    def import_community_members_batch(self, members: list) -> int:
        """Bulk create Community→Entity HAS_MEMBER edges and set entity.community_id."""
        with self.driver.session() as session:
            result = session.run("""
                UNWIND $members as m
                MATCH (com:Community {id: m.community_id})
                MATCH (e:Entity {name: m.entity_name})
                CREATE (com)-[:HAS_MEMBER]->(e)
                SET e.community_id = m.community_id
                RETURN count(*) as cnt
            """, members=members)
            return result.single()["cnt"]

    def import_collections_batch(self, collections: list) -> int:
        """Bulk create Collection nodes."""
        with self.driver.session() as session:
            result = session.run("""
                UNWIND $collections as col
                MERGE (c:Collection {id: col.id})
                SET c = col
                RETURN count(c) as cnt
            """, collections=collections)
            return result.single()["cnt"]

    def import_collection_members_batch(self, members: list) -> int:
        """Bulk create Collection→Document CONTAINS edges."""
        with self.driver.session() as session:
            result = session.run("""
                UNWIND $members as m
                MATCH (col:Collection {id: m.collection_id})
                MATCH (d:Document {id: m.document_id})
                CREATE (col)-[:CONTAINS]->(d)
                RETURN count(*) as cnt
            """, members=members)
            return result.single()["cnt"]

    def import_merge_history_batch(self, histories: list) -> int:
        """Bulk create MergeHistory nodes."""
        with self.driver.session() as session:
            result = session.run("""
                UNWIND $histories as h
                CREATE (m:MergeHistory)
                SET m = h
                RETURN count(m) as cnt
            """, histories=histories)
            return result.single()["cnt"]

    def import_system_meta_batch(self, metas: list) -> int:
        """Bulk create SystemMeta nodes."""
        with self.driver.session() as session:
            result = session.run("""
                UNWIND $metas as m
                MERGE (s:SystemMeta {key: m.key})
                SET s.value = m.value
                RETURN count(s) as cnt
            """, metas=metas)
            return result.single()["cnt"]

    def delete_all_skills(self) -> int:
        """Delete ALL Skill nodes.

        Returns:
            Number of Skill nodes deleted.
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (s:Skill)
                DETACH DELETE s
                RETURN count(s) as deleted
            """)
            record = result.single()
            deleted = record["deleted"] if record else 0
            logger.info(f"Deleted {deleted} skill records")
            return deleted

    def export_all_skills(self) -> list:
        """Get all Skill nodes for export."""
        with self.driver.session() as session:
            result = session.run("MATCH (s:Skill) RETURN s{.*} as skill")
            return [record["skill"] for record in result]

    def import_skills_batch(self, skills: list) -> int:
        """Bulk create Skill nodes. Returns count created."""
        with self.driver.session() as session:
            result = session.run("""
                UNWIND $skills as sk
                MERGE (s:Skill {skill_id: sk.skill_id})
                SET s = sk
                RETURN count(s) as cnt
            """, skills=skills)
            return result.single()["cnt"]

    # =================================================================
    # Agent Skills CRUD (agentskills.io)
    # =================================================================

    def upsert_skill(self, props: dict) -> dict:
        """Create or update a Skill node, preserving enabled state on update."""
        with self.driver.session() as session:
            result = session.run("""
                MERGE (s:Skill {skill_id: $skill_id})
                ON CREATE SET
                    s.name = $name,
                    s.description = $description,
                    s.version = $version,
                    s.author = $author,
                    s.license = $license,
                    s.source = $source,
                    s.source_url = $source_url,
                    s.skill_type = $skill_type,
                    s.enabled = $enabled,
                    s.installed_at = $installed_at,
                    s.directory_path = $directory_path,
                    s.tool_names = $tool_names
                ON MATCH SET
                    s.name = $name,
                    s.description = $description,
                    s.version = $version,
                    s.author = $author,
                    s.license = $license,
                    s.skill_type = $skill_type,
                    s.directory_path = $directory_path,
                    s.tool_names = $tool_names
                RETURN s
            """, **props)
            record = result.single()
            return dict(record["s"]) if record else {}

    def get_all_skills(self) -> list:
        """Get all Skill nodes."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (s:Skill)
                RETURN s
                ORDER BY s.installed_at DESC
            """)
            return [dict(record["s"]) for record in result]

    def get_skill(self, skill_id: str) -> Optional[dict]:
        """Get a single Skill node by ID."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (s:Skill {skill_id: $skill_id})
                RETURN s
            """, skill_id=skill_id)
            record = result.single()
            return dict(record["s"]) if record else None

    def get_enabled_skills(self) -> list:
        """Get all enabled Skill nodes."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (s:Skill {enabled: true})
                RETURN s
                ORDER BY s.installed_at ASC
            """)
            return [dict(record["s"]) for record in result]

    def update_skill(self, skill_id: str, props: dict) -> Optional[dict]:
        """Update a Skill node's properties."""
        set_clauses = ", ".join(f"s.{k} = ${k}" for k in props.keys())
        with self.driver.session() as session:
            result = session.run(f"""
                MATCH (s:Skill {{skill_id: $skill_id}})
                SET {set_clauses}
                RETURN s
            """, skill_id=skill_id, **props)
            record = result.single()
            return dict(record["s"]) if record else None

    def delete_skill(self, skill_id: str) -> bool:
        """Delete a Skill node."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (s:Skill {skill_id: $skill_id})
                DELETE s
                RETURN count(s) as cnt
            """, skill_id=skill_id)
            return result.single()["cnt"] > 0

    # =========================================================================
    # Git Integration — connections & document provenance
    # =========================================================================

    def create_git_connection(self, props: dict) -> dict:
        """Create a GitConnection node. `props` must include `id`."""
        with self.driver.session() as session:
            result = session.run("""
                CREATE (g:GitConnection {id: $id})
                SET g += $props
                RETURN g
            """, id=props["id"], props=props)
            record = result.single()
            return dict(record["g"]) if record else {}

    def get_git_connection(self, connection_id: str) -> Optional[dict]:
        """Get a single GitConnection (includes the raw PAT for internal use)."""
        with self.driver.session() as session:
            result = session.run(
                "MATCH (g:GitConnection {id: $id}) RETURN g",
                id=connection_id,
            )
            record = result.single()
            return dict(record["g"]) if record else None

    def list_git_connections(self) -> list:
        """List all GitConnection nodes."""
        with self.driver.session() as session:
            result = session.run(
                "MATCH (g:GitConnection) RETURN g ORDER BY g.created_at DESC"
            )
            return [dict(record["g"]) for record in result]

    def update_git_connection(self, connection_id: str, props: dict) -> Optional[dict]:
        """Update a GitConnection's properties (only keys present in `props`)."""
        if not props:
            return self.get_git_connection(connection_id)
        with self.driver.session() as session:
            result = session.run("""
                MATCH (g:GitConnection {id: $id})
                SET g += $props
                RETURN g
            """, id=connection_id, props=props)
            record = result.single()
            return dict(record["g"]) if record else None

    def delete_git_connection(self, connection_id: str) -> bool:
        """Delete a GitConnection node (does not touch its documents)."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (g:GitConnection {id: $id})
                DETACH DELETE g
                RETURN count(g) as cnt
            """, id=connection_id)
            return result.single()["cnt"] > 0

    def set_git_connection_sync_state(self, connection_id: str, **state) -> None:
        """Update sync bookkeeping (last_synced_sha/at, next_sync_due, sync_status, ...)."""
        if not state:
            return
        with self.driver.session() as session:
            session.run("""
                MATCH (g:GitConnection {id: $id})
                SET g += $state
            """, id=connection_id, state=state)

    def find_git_document(self, connection_id: str, git_path: str) -> Optional[dict]:
        """Find the document for a (connection, repo path) pair — the sync key."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (d:Document {git_connection_id: $cid, git_path: $path})
                RETURN d.id as id, d.git_blob_sha as git_blob_sha,
                       d.filename as filename, d.git_sync_status as git_sync_status
                LIMIT 1
            """, cid=connection_id, path=git_path)
            record = result.single()
            return dict(record) if record else None

    def set_document_git_provenance(
        self, doc_id: str, *, blob_sha: str = None, commit_sha: str = None,
        sync_status: str = None,
    ) -> None:
        """Update git provenance on an existing document (after reprocess/sync)."""
        with self.driver.session() as session:
            session.run("""
                MATCH (d:Document {id: $id})
                SET d.git_blob_sha = COALESCE($blob_sha, d.git_blob_sha),
                    d.git_commit_sha = COALESCE($commit_sha, d.git_commit_sha),
                    d.git_sync_status = COALESCE($sync_status, d.git_sync_status)
            """, id=doc_id, blob_sha=blob_sha, commit_sha=commit_sha, sync_status=sync_status)

    def mark_git_document_orphaned(self, doc_id: str) -> None:
        """Flag a document whose source file was removed from the repo, for user review."""
        with self.driver.session() as session:
            session.run(
                "MATCH (d:Document {id: $id}) SET d.git_sync_status = 'orphaned'",
                id=doc_id,
            )

    def remap_git_document(self, doc_id: str, new_path: str, new_filename: str) -> None:
        """Update a document's repo path/filename after a rename, clearing orphaned state."""
        with self.driver.session() as session:
            session.run("""
                MATCH (d:Document {id: $id})
                SET d.git_path = $new_path,
                    d.filename = $new_filename,
                    d.git_sync_status = 'synced'
            """, id=doc_id, new_path=new_path, new_filename=new_filename)

    def list_documents_for_git_connection(self, connection_id: str) -> list:
        """List all documents ingested from a given git connection."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (d:Document {git_connection_id: $cid})
                RETURN d.id as id, d.git_path as git_path, d.filename as filename,
                       d.git_sync_status as git_sync_status
            """, cid=connection_id)
            return [dict(record) for record in result]

    def list_orphaned_git_documents(self, connection_id: str) -> list:
        """List documents from a connection whose source file was removed (flagged for review)."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (d:Document {git_connection_id: $cid, git_sync_status: 'orphaned'})
                RETURN d.id as id, d.filename as filename, d.git_path as git_path
                ORDER BY d.git_path
            """, cid=connection_id)
            return [dict(record) for record in result]

    def delete_relationships_by_source_document(self, doc_id: str) -> int:
        """Delete entity-to-entity relationships whose provenance is this document.

        Fills the gap where reprocess/delete only removed relationships when an
        endpoint entity became fully orphaned — leaving stale edges otherwise.
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (:Entity)-[r]->(:Entity)
                WHERE r.source_document_id = $doc_id
                DELETE r
                RETURN count(r) as cnt
            """, doc_id=doc_id)
            record = result.single()
            return record["cnt"] if record else 0

    # =========================================================================
    # Background task records (persistence for main.py's in-memory task store)
    # =========================================================================
    # The live store stays in process memory; these records are a write-through
    # shadow so a restart doesn't turn every in-flight task id into a 404.
    # `result` is stored as a JSON string (Neo4j properties can't nest maps).

    @retry_on_transient
    def upsert_task_records(self, records: list[dict]) -> int:
        """Batch-upsert task snapshots. Each dict is flat (see main._serialize_task)."""
        if not records:
            return 0
        with self.driver.session() as session:
            result = session.run("""
                UNWIND $records AS rec
                MERGE (t:TaskRecord {task_id: rec.task_id})
                SET t += rec
                RETURN count(t) as cnt
            """, records=records)
            record = result.single()
            return record["cnt"] if record else 0

    @retry_on_transient
    def get_task_record(self, task_id: str) -> Optional[dict]:
        with self.driver.session() as session:
            result = session.run("""
                MATCH (t:TaskRecord {task_id: $task_id})
                RETURN t
            """, task_id=task_id)
            record = result.single()
            return dict(record["t"]) if record else None

    def fail_interrupted_task_records(self) -> int:
        """Mark persisted pending/running tasks as failed (startup reconcile).

        Tasks run as in-process coroutines — anything not terminal at startup
        can never make progress again. Failing the record turns the UI's
        eternal poll into an actionable error.
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (t:TaskRecord)
                WHERE t.status IN ['pending', 'running']
                SET t.status = 'failed',
                    t.error = 'Interrupted by server restart',
                    t.message = 'Failed: interrupted by server restart',
                    t.completed_at = $now
                RETURN count(t) as cnt
            """, now=datetime.utcnow().isoformat())
            record = result.single()
            return record["cnt"] if record else 0

    def delete_task_record(self, task_id: str) -> None:
        with self.driver.session() as session:
            session.run(
                "MATCH (t:TaskRecord {task_id: $task_id}) DELETE t",
                task_id=task_id,
            )

    def prune_task_records(self, max_age_days: int = 7) -> int:
        """Delete terminal task records older than the retention window."""
        cutoff = (datetime.utcnow() - timedelta(days=max_age_days)).isoformat()
        with self.driver.session() as session:
            result = session.run("""
                MATCH (t:TaskRecord)
                WHERE t.status IN ['completed', 'failed']
                  AND coalesce(t.completed_at, t.started_at, '') < $cutoff
                DELETE t
                RETURN count(t) as cnt
            """, cutoff=cutoff)
            record = result.single()
            return record["cnt"] if record else 0


# Singleton instance
_neo4j_service: Optional[Neo4jService] = None


def get_neo4j_service() -> Neo4jService:
    global _neo4j_service
    if _neo4j_service is None:
        _neo4j_service = Neo4jService()
    return _neo4j_service
