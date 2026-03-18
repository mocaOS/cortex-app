"""Neo4j service for document, vector, and knowledge graph storage.

Features:
- Community detection using graph algorithms
- Collection-level knowledge graphs
- Semantic entity resolution with embeddings
- Community summarization support
"""

from neo4j import GraphDatabase, AsyncGraphDatabase
from neo4j.exceptions import ServiceUnavailable
from typing import Optional, List, Tuple
from datetime import datetime, timedelta
import logging
import numpy as np
from contextlib import asynccontextmanager
import uuid

from app.config import get_settings
from app.models import (
    Document, DocumentChunk, DocumentMetadata, ProcessingStatus,
    Entity, Relationship, ExtractionResult, Collection, Community
)

logger = logging.getLogger(__name__)


class Neo4jService:
    """Service for interacting with Neo4j database."""
    
    def __init__(self):
        self.settings = get_settings()
        self._driver = None
    
    @property
    def driver(self):
        if self._driver is None:
            self._driver = GraphDatabase.driver(
                self.settings.neo4j_uri,
                auth=(self.settings.neo4j_user, self.settings.neo4j_password)
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
            # Vector indexes
            # =================================================================
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
                """, dimensions=self.settings.embedding_dimension)
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
                """, dimensions=self.settings.embedding_dimension)
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
            
            logger.info("Neo4j schema initialized successfully (including Collections, Communities, GraphRAG indexes, APIKeys)")
    
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
                    d.progress_message = $progress_message
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
                error_message=metadata.error_message
            )
            return result.single()["id"]
    
    def store_chunk(self, chunk: DocumentChunk) -> str:
        """Store a document chunk with its embedding."""
        with self.driver.session() as session:
            # Convert embedding to list if it's a numpy array
            embedding = chunk.embedding
            if isinstance(embedding, np.ndarray):
                embedding = embedding.tolist()
            
            result = session.run("""
                MATCH (d:Document {id: $document_id})
                MERGE (c:Chunk {id: $chunk_id})
                SET c.content = $content,
                    c.embedding = $embedding,
                    c.chunk_index = $chunk_index,
                    c.metadata = $metadata
                MERGE (d)-[:HAS_CHUNK]->(c)
                RETURN c.id as id
            """,
                document_id=chunk.document_id,
                chunk_id=chunk.id,
                content=chunk.content,
                embedding=embedding,
                chunk_index=chunk.chunk_index,
                metadata=str(chunk.metadata)
            )
            return result.single()["id"]
    
    def update_document_status(
        self, 
        doc_id: str, 
        status: ProcessingStatus, 
        chunk_count: int = 0,
        error_message: Optional[str] = None,
        progress_message: str = ""
    ):
        """Update the processing status of a document."""
        with self.driver.session() as session:
            session.run("""
                MATCH (d:Document {id: $id})
                SET d.processing_status = $status,
                    d.chunk_count = $chunk_count,
                    d.error_message = $error_message,
                    d.progress_message = $progress_message
            """,
                id=doc_id,
                status=status.value,
                chunk_count=chunk_count,
                error_message=error_message,
                progress_message=progress_message
            )
    
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
                    d.progress_message = $message
            """,
                id=doc_id,
                current=current,
                total=total,
                message=message
            )

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
    
    def vector_search(
        self, 
        query_embedding: list[float], 
        top_k: int = 5,
        filters: Optional[dict] = None,
        collection_id: Optional[str] = None
    ) -> list[dict]:
        """Perform vector similarity search, optionally scoped to a collection."""
        with self.driver.session() as session:
            # Build the query with optional filters
            filter_clause = ""
            if filters and "file_type" in filters:
                filter_clause = "AND d.file_type = $file_type"
            
            # Collection scoping: post-filter vector results to collection membership
            collection_clause = ""
            if collection_id:
                collection_clause = "MATCH (col:Collection {id: $collection_id})-[:CONTAINS]->(d)"
            
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
                collection_id=collection_id
            )
            
            return [dict(record) for record in result]
    
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
                       coalesce(d.custom_topic_hint, '') as custom_topic_hint
                ORDER BY d.upload_date DESC
            """)
            return [dict(record) for record in result]
    
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

    def get_document(self, doc_id: str) -> Optional[dict]:
        """Get a single document by ID."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (d:Document {id: $id})
                OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:Chunk)
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
                       collect(c.id) as chunk_ids
            """, id=doc_id)
            
            record = result.single()
            return dict(record) if record else None
    
    def get_document_content(self, doc_id: str) -> Optional[dict]:
        """
        Get a document with all its chunk content, ordered by chunk index.
        
        Returns dict with document metadata and full_content (concatenated chunks).
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (d:Document {id: $id})
                OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:Chunk)
                WITH d, c
                ORDER BY c.chunk_index
                WITH d, collect({
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
            
            # Delete all communities
            session.run("MATCH (com:Community) DETACH DELETE com")
            
            # Delete all entities (they will all be orphaned)
            session.run("MATCH (e:Entity) DETACH DELETE e")
            
            # Delete all chunks and documents
            session.run("""
                MATCH (d:Document)
                OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:Chunk)
                DETACH DELETE d, c
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
        with self.driver.session() as session:
            # Use APOC if available, otherwise use a workaround
            try:
                result = session.run("""
                    MATCH (s:Entity {name: $source})
                    MATCH (t:Entity {name: $target})
                    CALL apoc.merge.relationship(s, $rel_type, {}, {description: $description, weight: $weight}, t) YIELD rel
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
                    extraction_method=extraction_method,
                    source_doc_id=source_document_id,
                )
                return result.single() is not None
    
    def store_graph_extraction(self, chunk_id: str, extraction: ExtractionResult) -> dict:
        """
        Store all entities and relationships from an extraction result.
        
        Returns:
            Dict with counts of stored entities and relationships
        """
        entity_count = 0
        relationship_count = 0
        
        # Store entities first
        for entity in extraction.entities:
            try:
                self.store_entity(entity, chunk_id)
                entity_count += 1
            except Exception as e:
                logger.warning(f"Failed to store entity {entity.name}: {e}")
        
        # Then store relationships
        for relationship in extraction.relationships:
            try:
                if self.store_relationship(relationship):
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
        limit: int = 5000,
    ) -> List[dict]:
        """Get all entities in a collection (or globally if collection_id is None)."""
        with self.driver.session() as session:
            if collection_id:
                result = session.run("""
                    MATCH (col:Collection {id: $col_id})-[:CONTAINS]->(d:Document)
                          -[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(e:Entity)
                    RETURN DISTINCT e.name as name, e.type as type,
                           e.description as description,
                           e.community_id as community_id,
                           count(DISTINCT d) as document_count
                    ORDER BY document_count DESC
                    LIMIT $limit
                """, col_id=collection_id, limit=limit)
            else:
                result = session.run("""
                    MATCH (e:Entity)
                    OPTIONAL MATCH (e)<-[:MENTIONS]-(c:Chunk)<-[:HAS_CHUNK]-(d:Document)
                    RETURN DISTINCT e.name as name, e.type as type,
                           e.description as description,
                           e.community_id as community_id,
                           count(DISTINCT d) as document_count
                    ORDER BY document_count DESC
                    LIMIT $limit
                """, limit=limit)
            return [dict(record) for record in result]

    def get_existing_relationships_for_entities(
        self,
        entity_names: List[str],
        limit: int = 500,
    ) -> List[dict]:
        """Get existing relationships between the given entities."""
        if not entity_names:
            return []
        with self.driver.session() as session:
            result = session.run("""
                MATCH (s:Entity)-[r]->(t:Entity)
                WHERE s.name IN $names AND t.name IN $names
                RETURN s.name as source, t.name as target,
                       coalesce(r.type, type(r)) as type,
                       r.description as description, r.weight as weight
                LIMIT $limit
            """, names=entity_names, limit=limit)
            return [dict(record) for record in result]

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
    
    def find_entities_by_name(self, names: List[str]) -> List[dict]:
        """
        Find entities by their names (case-insensitive fuzzy match).
        Appends wildcard for prefix matching (e.g. "pol" finds "Polygon").
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
    
    def traverse_from_entities(
        self,
        entity_names: List[str],
        max_hops: int = 2,
        limit: int = 50,
        collection_id: Optional[str] = None
    ) -> dict:
        """
        Traverse the graph from given entities to find related context.
        Optionally scoped to a specific collection.
        
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
            
            result = session.run(f"""
                MATCH (start:Entity)
                WHERE start.name IN $entity_names
                CALL {{
                    WITH start
                    MATCH path = (start)-[r*1..{int(max_hops)}]-(related:Entity)
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
                collection_id=collection_id
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
    
    def fulltext_search(
        self,
        query_text: str,
        top_k: int = 10,
        collection_id: Optional[str] = None
    ) -> List[dict]:
        """
        Perform full-text keyword search on chunk content, optionally scoped to a collection.
        """
        with self.driver.session() as session:
            try:
                # Escape special characters for Lucene query
                escaped_query = query_text.replace('"', '\\"').replace('~', '\\~')
                
                # Collection scoping
                collection_clause = ""
                if collection_id:
                    collection_clause = "MATCH (col:Collection {id: $collection_id})-[:CONTAINS]->(d)"
                
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
                """, search_text=escaped_query, top_k=top_k, collection_id=collection_id)
                
                return [dict(record) for record in result]
            except Exception as e:
                logger.warning(f"Fulltext search failed: {e}")
                return []
    
    def metadata_search(
        self,
        query_text: str,
        top_k: int = 10
    ) -> List[dict]:
        """
        Search documents by filename, topic hint, or raw content (for custom inputs).
        Returns chunks from matching documents with high relevance score.
        """
        with self.driver.session() as session:
            try:
                search_lower = query_text.lower().strip()
                
                # Search in document metadata
                result = session.run("""
                    MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)
                    WHERE d.processing_status = 'completed'
                    AND (
                        toLower(d.filename) CONTAINS $search_term
                        OR toLower(d.custom_topic_hint) CONTAINS $search_term
                        OR (d.is_custom_input = true AND toLower(d.custom_raw_content) CONTAINS $search_term)
                    )
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
                """, search_term=search_lower, top_k=top_k)
                
                return [dict(record) for record in result]
            except Exception as e:
                logger.warning(f"Metadata search failed: {e}")
                return []
    
    def simple_hybrid_search(
        self,
        query_embedding: List[float],
        query_text: str,
        top_k: int = 10,
        vector_weight: float = 0.5,
        keyword_weight: float = 0.3,
        metadata_weight: float = 0.2
    ) -> List[dict]:
        """
        Simple hybrid search combining vector + keyword + metadata search.
        Uses RRF to merge results from all three sources.
        """
        # 1. Vector search (semantic similarity)
        vector_results = self.vector_search(query_embedding, top_k * 2)
        
        # 2. Keyword/full-text search (content matching)
        keyword_results = self.fulltext_search(query_text, top_k * 2)
        
        # 3. Metadata search (filename, topic hint)
        metadata_results = self.metadata_search(query_text, top_k * 2)
        
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
        collection_id: Optional[str] = None
    ) -> dict:
        """
        Perform hybrid search with Reciprocal Rank Fusion.
        Combines: vector similarity + full-text keyword + graph traversal
        Optionally scoped to a specific collection.
        
        Returns:
            Dict with 'results' (RRF-fused) and 'graph_context'
        """
        # 1. Vector search
        vector_results = self.vector_search(query_embedding, top_k * 3, collection_id=collection_id)
        
        # 2. Keyword/full-text search
        keyword_results = self.fulltext_search(query_text, top_k * 3, collection_id=collection_id)
        
        # 3. Graph traversal for context
        graph_context = self.traverse_from_entities(entity_names, max_hops, collection_id=collection_id)
        
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
        """
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
    
    def get_chunk_context_for_entities(
        self,
        entity_names: List[str],
        max_chunks: int = 10,
        max_content_length: int = 500,
    ) -> str:
        """Retrieve the most relevant chunk text for a set of entities.

        Prioritizes chunks that mention the most entities in the batch
        (co-mention chunks), as these are most likely to contain
        relationship-relevant context.
        """
        if not entity_names:
            return ""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (c:Chunk)-[:MENTIONS]->(e:Entity)
                WHERE e.name IN $entity_names
                WITH c, count(DISTINCT e) as mention_count
                ORDER BY mention_count DESC
                LIMIT $max_chunks
                RETURN c.content as content, mention_count
            """, entity_names=entity_names, max_chunks=max_chunks)

            chunks = []
            for record in result:
                content = (record["content"] or "")[:max_content_length]
                if content:
                    chunks.append(content)

            return "\n---\n".join(chunks) if chunks else ""

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
    
    def get_stats(self) -> dict:
        """Get knowledge base and knowledge graph statistics."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (d:Document)
                OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:Chunk)
                WITH count(DISTINCT d) as doc_count, count(c) as chunk_count, sum(coalesce(d.file_size, 0)) as total_size
                
                OPTIONAL MATCH (e:Entity)
                WITH doc_count, chunk_count, total_size, count(e) as entity_count
                
                OPTIONAL MATCH ()-[r:RELATED_TO]->()
                RETURN doc_count as document_count,
                       chunk_count,
                       total_size,
                       entity_count,
                       count(r) as relationship_count
            """)
            
            record = result.single()
            return {
                "document_count": record["document_count"],
                "chunk_count": record["chunk_count"],
                "total_size": record["total_size"] or 0,
                "entity_count": record["entity_count"],
                "relationship_count": record["relationship_count"]
            }
    
    def get_graph_visualization_data(self, limit: int = 100, include_neighbors: bool = True) -> dict:
        """
        Get data for visualizing the knowledge graph.
        
        This method fetches entities and ALL their relationships in both directions,
        optionally expanding to include neighbor entities to show more graph structure.
        
        Args:
            limit: Maximum number of core entities to fetch (based on mention count).
                   Use 0 or negative to fetch ALL entities.
            include_neighbors: If True, expands entity set to include 1-hop neighbors
            
        Returns:
            Dict with 'nodes', 'edges', and metadata for visualization
        """
        with self.driver.session() as session:
            # Step 1: Get top entities by mention count (core entities)
            # If limit <= 0, fetch all entities (no limit)
            fetch_all = limit <= 0
            
            if fetch_all:
                # No LIMIT clause - fetch all entities
                result = session.run("""
                    MATCH (e:Entity)
                    OPTIONAL MATCH (c:Chunk)-[:MENTIONS]->(e)
                    WITH e, count(c) as mention_count
                    RETURN e.name as id,
                           e.name as label,
                           e.type as type,
                           e.description as description,
                           e.community_id as community_id,
                           mention_count
                    ORDER BY mention_count DESC
                """)
            else:
                result = session.run("""
                    MATCH (e:Entity)
                    OPTIONAL MATCH (c:Chunk)-[:MENTIONS]->(e)
                    WITH e, count(c) as mention_count
                    RETURN e.name as id,
                           e.name as label,
                           e.type as type,
                           e.description as description,
                           e.community_id as community_id,
                           mention_count
                    ORDER BY mention_count DESC
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

    def suggest_duplicate_entities(self, threshold: float = 0.75, limit: int = 100) -> List[dict]:
        """
        Find candidate duplicate entities using multiple similarity strategies.

        Fetches all entities from Neo4j and compares in Python using rapidfuzz:
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

        # Fetch all entities with their connectivity stats
        with self.driver.session() as session:
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

        # Pre-compute lowercased names and types
        names = [e['name'] for e in all_entities]
        names_lower = [n.lower() for n in names]
        types = [e['type'] or '' for e in all_entities]
        n = len(names)

        # Compute pairwise similarity — store as {name: {other_name: score}}
        direct_matches = {}  # name -> [(other_name, score)]

        for i in range(n):
            for j in range(i + 1, n):
                a_lower = names_lower[i]
                b_lower = names_lower[j]

                # Skip if both are very short (<=2 chars) — too many false positives
                if len(a_lower) <= 2 and len(b_lower) <= 2:
                    continue

                # Core metrics: ratio (typos) and token_sort_ratio (reordering)
                score_ratio = fuzz.ratio(a_lower, b_lower)
                score_token_sort = fuzz.token_sort_ratio(a_lower, b_lower)
                best_score = max(score_ratio, score_token_sort)

                # partial_ratio only for same-type entities with reasonable
                # length ratio — prevents "Google" matching "Google Chrome"
                # More lenient for Person type (first name → full name is common)
                same_type = types[i] and types[j] and types[i] == types[j]
                len_ratio = min(len(a_lower), len(b_lower)) / max(len(a_lower), len(b_lower))
                is_person = same_type and types[i] == 'Person'
                min_len_ratio = 0.35 if is_person else 0.5
                if same_type and len_ratio >= min_len_ratio:
                    score_partial = fuzz.partial_ratio(a_lower, b_lower)
                    best_score = max(best_score, score_partial)

                best_score = best_score / 100.0

                # Higher threshold for short names to reduce noise
                effective_threshold = threshold
                if min(len(a_lower), len(b_lower)) <= 3:
                    effective_threshold = max(threshold, 0.85)

                if best_score >= effective_threshold:
                    a_name = names[i]
                    b_name = names[j]
                    if a_name not in direct_matches:
                        direct_matches[a_name] = []
                    if b_name not in direct_matches:
                        direct_matches[b_name] = []
                    direct_matches[a_name].append((b_name, best_score))
                    direct_matches[b_name].append((a_name, best_score))

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

        for canonical_candidate in candidates:
            if canonical_candidate in assigned:
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

    def get_entity_relationships(self, entity_name: str, max_depth: int = 2, limit: int = 50) -> dict:
        """
        Get an entity and all its relationships up to max_depth hops.
        
        This enables focused graph exploration from a specific entity.
        
        Args:
            entity_name: The entity to start from
            max_depth: Maximum relationship hops to traverse (1-3)
            limit: Maximum number of relationships to return
            
        Returns:
            Dict with 'entity', 'related_entities', 'relationships'
        """
        max_depth = min(max(1, max_depth), 3)  # Clamp between 1-3
        
        with self.driver.session() as session:
            # Get the central entity
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
            traverse_result = session.run(f"""
                MATCH (start:Entity {{name: $name}})
                CALL {{
                    WITH start
                    MATCH path = (start)-[r*1..{max_depth}]-(related:Entity)
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
    
    def get_graph_subgraph(self, entity_names: List[str], include_connections: bool = True) -> dict:
        """
        Get a subgraph containing specified entities and their interconnections.
        
        Method for focused graph visualization of specific entities.
        
        Args:
            entity_names: List of entity names to include
            include_connections: If True, also include entities that connect the given entities
            
        Returns:
            Dict with 'nodes' and 'edges' for the subgraph
        """
        if not entity_names:
            return {"nodes": [], "edges": []}
        
        with self.driver.session() as session:
            if include_connections:
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
    
    def get_community(self, community_id: int) -> Optional[dict]:
        """Get a community with its entities and relationships."""
        with self.driver.session() as session:
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

        Returns:
            Dict with count of deleted relationships.
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (:Entity)-[r]->(:Entity)
                WHERE type(r) <> 'MENTIONS'
                WITH count(r) as total, collect(r) as rels
                FOREACH (r IN rels | DELETE r)
                RETURN total as deleted
            """)
            record = result.single()
            deleted = record["deleted"] if record else 0
            logger.info(f"Deleted {deleted} relationships")
            return {"relationships_deleted": deleted}

    def search_communities_by_content(self, query: str, limit: int = 5) -> List[dict]:
        """Search communities by their summary content."""
        with self.driver.session() as session:
            try:
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
                logger.debug(f"Entity embedding search failed (index may not exist): {e}")
                return []
    
    def store_entity_with_embedding(
        self,
        entity: Entity,
        chunk_id: str,
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
                    
                    # Link to chunk
                    session.run("""
                        MATCH (e:Entity {name: $name})
                        MATCH (c:Chunk {id: $chunk_id})
                        MERGE (c)-[:MENTIONS]->(e)
                    """, name=canonical_name, chunk_id=chunk_id)
                    
                    logger.debug(f"Merged entity '{entity.name}' into '{canonical_name}' (similarity: {similar[0]['similarity']:.3f})")
                    return (canonical_name, False)
            
            # Create new entity with embedding
            result = session.run("""
                MERGE (e:Entity {name: $name})
                ON CREATE SET 
                    e.type = $type,
                    e.description = $description,
                    e.embedding = $embedding,
                    e.created_at = datetime()
                ON MATCH SET
                    e.type = CASE WHEN e.type IS NULL OR e.type = '' THEN $type ELSE e.type END,
                    e.description = CASE WHEN size(coalesce(e.description, '')) < size($description) THEN $description ELSE e.description END,
                    e.embedding = CASE WHEN e.embedding IS NULL THEN $embedding ELSE e.embedding END
                WITH e
                MATCH (c:Chunk {id: $chunk_id})
                MERGE (c)-[:MENTIONS]->(e)
                RETURN e.name as name
            """,
                name=entity.name,
                type=entity.type,
                description=entity.description,
                embedding=embedding,
                chunk_id=chunk_id
            )
            
            record = result.single()
            return (record["name"] if record else entity.name, True)
    
    def get_stats(self) -> dict:
        """Get knowledge base and knowledge graph statistics."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (d:Document)
                OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:Chunk)
                WITH count(DISTINCT d) as doc_count, count(c) as chunk_count, sum(coalesce(d.file_size, 0)) as total_size

                OPTIONAL MATCH (e:Entity)
                WITH doc_count, chunk_count, total_size, count(e) as entity_count

                OPTIONAL MATCH (:Entity)-[r]->(:Entity)
                WHERE type(r) <> 'MENTIONS'
                WITH doc_count, chunk_count, total_size, entity_count, count(r) as relationship_count

                OPTIONAL MATCH (com:Community)
                WITH doc_count, chunk_count, total_size, entity_count, relationship_count, count(com) as community_count

                OPTIONAL MATCH (col:Collection)
                WITH doc_count, chunk_count, total_size, entity_count, relationship_count, community_count, count(col) as collection_count

                OPTIONAL MATCH (pending:Document)
                WHERE coalesce(pending.processing_status, 'pending') = 'pending'
                WITH doc_count, chunk_count, total_size, entity_count, relationship_count, community_count, collection_count, count(pending) as pending_count

                OPTIONAL MATCH (completed:Document)
                WHERE completed.processing_status = 'completed'
                WITH doc_count, chunk_count, total_size, entity_count, relationship_count, community_count, collection_count, pending_count, count(completed) as completed_count

                OPTIONAL MATCH (failed:Document)
                WHERE failed.processing_status = 'failed'
                WITH doc_count, chunk_count, total_size, entity_count, relationship_count, community_count, collection_count, pending_count, completed_count, count(failed) as failed_count

                OPTIONAL MATCH (proc:Document)
                WHERE proc.processing_status IN ['processing', 'extracting']
                WITH doc_count, chunk_count, total_size, entity_count, relationship_count, community_count, collection_count, pending_count, completed_count, failed_count, count(proc) as processing_count

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
                       processing_count
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
        created_by: str = "admin"
    ) -> Optional[dict]:
        """Create a new API key in the database."""
        with self.driver.session() as session:
            result = session.run("""
                CREATE (k:APIKey {
                    id: $id,
                    name: $name,
                    key_prefix: $key_prefix,
                    key_hash: $key_hash,
                    permissions: $permissions,
                    is_active: true,
                    created_at: datetime(),
                    created_by: $created_by
                })
                RETURN k.id as id,
                       k.name as name,
                       k.key_prefix as key_prefix,
                       k.permissions as permissions,
                       k.is_active as is_active,
                       k.created_at as created_at,
                       k.created_by as created_by
            """,
                id=key_id,
                name=name,
                key_prefix=key_prefix,
                key_hash=key_hash,
                permissions=permissions,
                created_by=created_by
            )
            
            record = result.single()
            if record:
                logger.info(f"Created API key: {name} ({key_id})")
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
                RETURN k.id as id,
                       coalesce(k.name, '') as name,
                       coalesce(k.key_prefix, '') as key_prefix,
                       k.key_hash as key_hash,
                       coalesce(k.permissions, []) as permissions,
                       coalesce(k.is_active, true) as is_active,
                       coalesce(k.created_at, '') as created_at,
                       k.last_used_at as last_used_at,
                       coalesce(k.created_by, '') as created_by
            """, id=key_id)
            
            record = result.single()
            return dict(record) if record else None
    
    def get_api_key_by_prefix(self, key_prefix: str) -> List[dict]:
        """Get API keys by their prefix (for validation lookup)."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (k:APIKey)
                WHERE k.key_prefix = $prefix AND coalesce(k.is_active, true) = true
                RETURN k.id as id,
                       coalesce(k.name, '') as name,
                       k.key_prefix as key_prefix,
                       k.key_hash as key_hash,
                       coalesce(k.permissions, []) as permissions,
                       coalesce(k.is_active, true) as is_active,
                       coalesce(k.created_at, '') as created_at,
                       k.last_used_at as last_used_at,
                       coalesce(k.created_by, '') as created_by
            """, prefix=key_prefix)
            
            return [dict(record) for record in result]
    
    def list_api_keys(self) -> List[dict]:
        """List all API keys (without the hash)."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (k:APIKey)
                RETURN k.id as id,
                       coalesce(k.name, '') as name,
                       coalesce(k.key_prefix, '') as key_prefix,
                       coalesce(k.permissions, []) as permissions,
                       coalesce(k.is_active, true) as is_active,
                       coalesce(k.created_at, '') as created_at,
                       k.last_used_at as last_used_at,
                       coalesce(k.created_by, '') as created_by
                ORDER BY k.created_at DESC
            """)
            
            return [dict(record) for record in result]
    
    def update_api_key(
        self,
        key_id: str,
        name: Optional[str] = None,
        permissions: Optional[List[str]] = None,
        is_active: Optional[bool] = None
    ) -> Optional[dict]:
        """Update an API key's properties."""
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
            
            if not set_clauses:
                return self.get_api_key_by_id(key_id)
            
            set_clause = ", ".join(set_clauses)
            
            result = session.run(f"""
                MATCH (k:APIKey {{id: $id}})
                SET {set_clause}
                RETURN k.id as id,
                       coalesce(k.name, '') as name,
                       coalesce(k.key_prefix, '') as key_prefix,
                       coalesce(k.permissions, []) as permissions,
                       coalesce(k.is_active, true) as is_active,
                       coalesce(k.created_at, '') as created_at,
                       k.last_used_at as last_used_at,
                       coalesce(k.created_by, '') as created_by
            """, **params)
            
            record = result.single()
            if record:
                logger.info(f"Updated API key: {key_id}")
                return dict(record)
            return None
    
    def delete_api_key(self, key_id: str) -> bool:
        """Delete an API key."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (k:APIKey {id: $id})
                DELETE k
                RETURN 1 as deleted
            """, id=key_id)
            
            record = result.single()
            if record:
                logger.info(f"Deleted API key: {key_id}")
                return True
            return False
    
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
                
                WITH k,
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
                
                keys.append({
                    "id": record["id"],
                    "name": record["name"],
                    "key_prefix": record["key_prefix"],
                    "permissions": record["permissions"] or [],
                    "is_active": record["is_active"],
                    "created_at": record["created_at"],
                    "last_used_at": record["last_used_at"],
                    "created_by": record["created_by"],
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


# Singleton instance
_neo4j_service: Optional[Neo4jService] = None


def get_neo4j_service() -> Neo4jService:
    global _neo4j_service
    if _neo4j_service is None:
        _neo4j_service = Neo4jService()
    return _neo4j_service
