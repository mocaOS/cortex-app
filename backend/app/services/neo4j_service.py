"""Neo4j service for document, vector, and knowledge graph storage.

Enhanced with R2R-style features:
- Community detection using graph algorithms
- Collection-level knowledge graphs
- Semantic entity resolution with embeddings
- Community summarization support
"""

from neo4j import GraphDatabase, AsyncGraphDatabase
from neo4j.exceptions import ServiceUnavailable
from typing import Optional, List, Tuple
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
            # Collection constraints (R2R-style)
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
            # Community constraints (R2R-style)
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
            
            logger.info("Neo4j schema initialized successfully (including Collections, Communities, GraphRAG indexes)")
    
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
    
    def vector_search(
        self, 
        query_embedding: list[float], 
        top_k: int = 5,
        filters: Optional[dict] = None
    ) -> list[dict]:
        """Perform vector similarity search."""
        with self.driver.session() as session:
            # Build the query with optional filters
            filter_clause = ""
            if filters and "file_type" in filters:
                filter_clause = "AND d.file_type = $file_type"
            
            result = session.run(f"""
                CALL db.index.vector.queryNodes('chunk_embedding', $top_k, $embedding)
                YIELD node as chunk, score
                MATCH (d:Document)-[:HAS_CHUNK]->(chunk)
                WHERE d.processing_status = 'completed' {filter_clause}
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
                file_type=filters.get("file_type") if filters else None
            )
            
            return [dict(record) for record in result]
    
    def get_all_documents(self) -> list[dict]:
        """Get all documents from the knowledge base with collection info."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (d:Document)
                OPTIONAL MATCH (col:Collection)-[:CONTAINS]->(d)
                RETURN d.id as id,
                       d.filename as filename,
                       d.file_type as file_type,
                       d.file_size as file_size,
                       d.file_path as file_path,
                       d.upload_date as upload_date,
                       d.chunk_count as chunk_count,
                       d.processing_status as processing_status,
                       d.error_message as error_message,
                       coalesce(d.progress_current, 0) as progress_current,
                       coalesce(d.progress_total, 0) as progress_total,
                       coalesce(d.progress_message, '') as progress_message,
                       col.id as collection_id,
                       col.name as collection_name
                ORDER BY d.upload_date DESC
            """)
            return [dict(record) for record in result]
    
    def get_document(self, doc_id: str) -> Optional[dict]:
        """Get a single document by ID."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (d:Document {id: $id})
                OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:Chunk)
                RETURN d.id as id,
                       d.filename as filename,
                       d.file_type as file_type,
                       d.file_size as file_size,
                       d.file_path as file_path,
                       d.upload_date as upload_date,
                       d.chunk_count as chunk_count,
                       d.processing_status as processing_status,
                       d.error_message as error_message,
                       coalesce(d.progress_current, 0) as progress_current,
                       coalesce(d.progress_total, 0) as progress_total,
                       coalesce(d.progress_message, '') as progress_message,
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
                       d.filename as filename,
                       d.file_type as file_type,
                       d.file_size as file_size,
                       d.upload_date as upload_date,
                       d.chunk_count as chunk_count,
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
    
    def store_entity(self, entity: Entity, chunk_id: str) -> str:
        """
        Store an entity and link it to the chunk where it was found.
        Uses MERGE to avoid duplicates.
        """
        with self.driver.session() as session:
            result = session.run("""
                MERGE (e:Entity {name: $name})
                ON CREATE SET 
                    e.type = $type,
                    e.description = $description,
                    e.created_at = datetime()
                ON MATCH SET
                    e.type = CASE WHEN e.type IS NULL OR e.type = '' THEN $type ELSE e.type END,
                    e.description = CASE WHEN size(e.description) < size($description) THEN $description ELSE e.description END
                WITH e
                MATCH (c:Chunk {id: $chunk_id})
                MERGE (c)-[:MENTIONS]->(e)
                RETURN e.name as name
            """,
                name=entity.name,
                type=entity.type,
                description=entity.description,
                chunk_id=chunk_id
            )
            record = result.single()
            return record["name"] if record else entity.name
    
    def store_relationship(self, relationship: Relationship) -> bool:
        """
        Store a relationship between two entities.
        Creates a dynamic relationship type with weight.
        """
        with self.driver.session() as session:
            # Use APOC if available, otherwise use a workaround
            try:
                result = session.run("""
                    MATCH (s:Entity {name: $source})
                    MATCH (t:Entity {name: $target})
                    CALL apoc.merge.relationship(s, $rel_type, {}, {description: $description, weight: $weight}, t) YIELD rel
                    RETURN type(rel) as rel_type
                """,
                    source=relationship.source,
                    target=relationship.target,
                    rel_type=relationship.relationship_type,
                    description=relationship.description,
                    weight=relationship.weight
                )
                return result.single() is not None
            except Exception as e:
                # Fallback without APOC - use RELATED_TO with type property
                logger.debug(f"APOC not available, using fallback: {e}")
                result = session.run("""
                    MATCH (s:Entity {name: $source})
                    MATCH (t:Entity {name: $target})
                    MERGE (s)-[r:RELATED_TO {type: $rel_type}]->(t)
                    SET r.description = $description, r.weight = $weight
                    RETURN type(r) as rel_type
                """,
                    source=relationship.source,
                    target=relationship.target,
                    rel_type=relationship.relationship_type,
                    description=relationship.description,
                    weight=relationship.weight
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
    # GraphRAG: Graph Traversal and Retrieval
    # =========================================================================
    
    def find_entities_by_name(self, names: List[str]) -> List[dict]:
        """
        Find entities by their names (case-insensitive fuzzy match).
        """
        if not names:
            return []
        
        with self.driver.session() as session:
            # Use fulltext search for fuzzy matching
            search_query = " OR ".join(names)
            try:
                result = session.run("""
                    CALL db.index.fulltext.queryNodes('entity_name_fulltext', $search_query)
                    YIELD node, score
                    RETURN node.name as name,
                           node.type as type,
                           node.description as description,
                           score
                    ORDER BY score DESC
                    LIMIT 20
                """, search_query=search_query)
                return [dict(record) for record in result]
            except Exception as e:
                logger.warning(f"Fulltext search failed, using exact match: {e}")
                # Fallback to exact match
                result = session.run("""
                    MATCH (e:Entity)
                    WHERE e.name IN $names
                    RETURN e.name as name,
                           e.type as type,
                           e.description as description,
                           1.0 as score
                """, names=names)
                return [dict(record) for record in result]
    
    def traverse_from_entities(
        self,
        entity_names: List[str],
        max_hops: int = 2,
        limit: int = 50
    ) -> dict:
        """
        Traverse the graph from given entities to find related context.
        
        Returns:
            Dict with 'entities', 'relationships', and 'chunks'
        """
        if not entity_names:
            return {"entities": [], "relationships": [], "chunks": []}
        
        with self.driver.session() as session:
            # Find related entities and relationships within max_hops
            # Note: max_hops must be injected as literal - Neo4j doesn't allow parameters in variable-length patterns
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
                limit=limit
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
        top_k: int = 10
    ) -> List[dict]:
        """
        Perform full-text keyword search on chunk content.
        """
        with self.driver.session() as session:
            try:
                # Escape special characters for Lucene query
                escaped_query = query_text.replace('"', '\\"').replace('~', '\\~')
                
                result = session.run("""
                    CALL db.index.fulltext.queryNodes('chunk_content', $search_text)
                    YIELD node as chunk, score
                    MATCH (d:Document)-[:HAS_CHUNK]->(chunk)
                    WHERE d.processing_status = 'completed'
                    RETURN d.id as document_id,
                           d.filename as filename,
                           chunk.id as chunk_id,
                           chunk.content as content,
                           chunk.chunk_index as chunk_index,
                           score
                    ORDER BY score DESC
                    LIMIT $top_k
                """, search_text=escaped_query, top_k=top_k)
                
                return [dict(record) for record in result]
            except Exception as e:
                logger.warning(f"Fulltext search failed: {e}")
                return []
    
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
        graph_weight: float = 0.2
    ) -> dict:
        """
        Perform hybrid search with Reciprocal Rank Fusion (R2R-style).
        Combines: vector similarity + full-text keyword + graph traversal
        
        Returns:
            Dict with 'results' (RRF-fused) and 'graph_context'
        """
        # 1. Vector search
        vector_results = self.vector_search(query_embedding, top_k * 3)
        
        # 2. Keyword/full-text search
        keyword_results = self.fulltext_search(query_text, top_k * 3)
        
        # 3. Graph traversal for context
        graph_context = self.traverse_from_entities(entity_names, max_hops)
        
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
    
    def store_entity_with_resolution(
        self,
        entity: Entity,
        chunk_id: str,
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
            
            # Link to chunk
            with self.driver.session() as session:
                session.run("""
                    MATCH (e:Entity {name: $name})
                    MATCH (c:Chunk {id: $chunk_id})
                    MERGE (c)-[:MENTIONS]->(e)
                """, name=canonical_name, chunk_id=chunk_id)
            
            return canonical_name
        
        # No similar entity, create new
        return self.store_entity(entity, chunk_id)
    
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
                WITH count(DISTINCT d) as doc_count, count(c) as chunk_count, sum(d.file_size) as total_size
                
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
        Get data for visualizing the knowledge graph (R2R-style enhanced).
        
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
            # This is the key R2R-style improvement - fetch relationships where
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
                OPTIONAL MATCH ()-[r:RELATED_TO]->()
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
    
    def get_entity_relationships(self, entity_name: str, max_depth: int = 2, limit: int = 50) -> dict:
        """
        Get an entity and all its relationships up to max_depth hops (R2R-style).
        
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
        
        R2R-style method for focused graph visualization of specific entities.
        
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
                # Find paths between specified entities (up to 2 hops)
                result = session.run("""
                    MATCH (e:Entity)
                    WHERE e.name IN $names
                    WITH collect(e) as entities
                    UNWIND entities as e1
                    UNWIND entities as e2
                    WHERE e1 <> e2
                    OPTIONAL MATCH path = shortestPath((e1)-[*1..2]-(e2))
                    WITH entities, collect(path) as paths
                    UNWIND paths as p
                    UNWIND nodes(p) as n
                    WITH DISTINCT n
                    WHERE n:Entity
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
    # Collection Management (R2R-style)
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
    # Community Detection (R2R-style)
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
            # Create a temporary graph projection
            if collection_id:
                # Collection-scoped graph
                session.run("""
                    CALL gds.graph.project.cypher(
                        $graph_name,
                        'MATCH (col:Collection {id: $col_id})-[:CONTAINS]->(d:Document)-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(e:Entity) RETURN id(e) as id',
                        'MATCH (e1:Entity)-[r:RELATED_TO]->(e2:Entity) RETURN id(e1) as source, id(e2) as target',
                        {parameters: {col_id: $col_id}}
                    )
                """, graph_name=graph_name, col_id=collection_id)
            else:
                # Global graph
                session.run("""
                    CALL gds.graph.project(
                        $graph_name,
                        'Entity',
                        {RELATED_TO: {orientation: 'UNDIRECTED'}}
                    )
                """, graph_name=graph_name)
            
            # Run Louvain community detection
            result = session.run("""
                CALL gds.louvain.stream($graph_name)
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
                MATCH (e1:Entity)-[:RELATED_TO]-(e2:Entity)
                WHERE e1.name IN entity_names AND e2.name IN entity_names
                RETURN e1.name as source, e2.name as target
            """, col_id=collection_id)
        else:
            result = session.run("""
                MATCH (e1:Entity)-[:RELATED_TO]-(e2:Entity)
                RETURN e1.name as source, e2.name as target
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
                MATCH (e1:Entity {community_id: $id})-[r:RELATED_TO]->(e2:Entity {community_id: $id})
                RETURN e1.name as source, e2.name as target, r.type as type, r.description as description
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
                MATCH (e1:Entity {community_id: $id})-[r:RELATED_TO]->(e2:Entity {community_id: $id})
                RETURN e1.name as source,
                       e2.name as target,
                       r.type as type,
                       r.description as description,
                       r.weight as weight
                ORDER BY r.weight DESC
                LIMIT $limit
            """, id=community_id, limit=limit)
            return [dict(record) for record in result]
    
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
    # Semantic Entity Resolution (R2R-style)
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
                WITH count(DISTINCT d) as doc_count, count(c) as chunk_count, sum(d.file_size) as total_size
                
                OPTIONAL MATCH (e:Entity)
                WITH doc_count, chunk_count, total_size, count(e) as entity_count
                
                OPTIONAL MATCH ()-[r:RELATED_TO]->()
                WITH doc_count, chunk_count, total_size, entity_count, count(r) as relationship_count
                
                OPTIONAL MATCH (com:Community)
                WITH doc_count, chunk_count, total_size, entity_count, relationship_count, count(com) as community_count
                
                OPTIONAL MATCH (col:Collection)
                WITH doc_count, chunk_count, total_size, entity_count, relationship_count, community_count, count(col) as collection_count
                
                OPTIONAL MATCH (pending:Document {processing_status: 'pending'})
                RETURN doc_count as document_count,
                       chunk_count,
                       total_size,
                       entity_count,
                       relationship_count,
                       community_count,
                       collection_count,
                       count(pending) as pending_count
            """)
            
            record = result.single()
            return {
                "document_count": record["document_count"],
                "chunk_count": record["chunk_count"],
                "total_size": record["total_size"] or 0,
                "entity_count": record["entity_count"],
                "relationship_count": record["relationship_count"],
                "community_count": record["community_count"],
                "collection_count": record["collection_count"],
                "pending_count": record["pending_count"]
            }


# Singleton instance
_neo4j_service: Optional[Neo4jService] = None


def get_neo4j_service() -> Neo4jService:
    global _neo4j_service
    if _neo4j_service is None:
        _neo4j_service = Neo4jService()
    return _neo4j_service
