"""Neo4j service for document, vector, and knowledge graph storage."""

from neo4j import GraphDatabase, AsyncGraphDatabase
from neo4j.exceptions import ServiceUnavailable
from typing import Optional, List
import logging
import numpy as np
from contextlib import asynccontextmanager

from app.config import get_settings
from app.models import (
    Document, DocumentChunk, DocumentMetadata, ProcessingStatus,
    Entity, Relationship, ExtractionResult
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
            
            logger.info("Neo4j schema initialized successfully (including GraphRAG indexes)")
    
    def store_document(self, doc_id: str, metadata: DocumentMetadata) -> str:
        """Store a document node in Neo4j."""
        with self.driver.session() as session:
            result = session.run("""
                MERGE (d:Document {id: $id})
                SET d.filename = $filename,
                    d.file_type = $file_type,
                    d.file_size = $file_size,
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
        """Get all documents from the knowledge base."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (d:Document)
                RETURN d.id as id,
                       d.filename as filename,
                       d.file_type as file_type,
                       d.file_size as file_size,
                       d.upload_date as upload_date,
                       d.chunk_count as chunk_count,
                       d.processing_status as processing_status,
                       d.error_message as error_message,
                       coalesce(d.progress_current, 0) as progress_current,
                       coalesce(d.progress_total, 0) as progress_total,
                       coalesce(d.progress_message, '') as progress_message
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
        Delete a document, its chunks, and orphaned entities.
        
        Entities are only deleted if they have no other connections to chunks
        from other documents. This keeps the Neo4j database clean.
        
        Returns:
            Dict with 'deleted' (bool), 'orphaned_entities_removed' (int)
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
            orphaned_count = 0
            if orphaned_entities:
                session.run("""
                    MATCH (e:Entity)
                    WHERE e.name IN $names
                    DETACH DELETE e
                """, names=orphaned_entities)
                orphaned_count = len(orphaned_entities)
                logger.info(f"Deleted {orphaned_count} orphaned entities for document {doc_id}")
            
            # Step 3: Delete document and its chunks
            result = session.run("""
                MATCH (d:Document {id: $id})
                OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:Chunk)
                DETACH DELETE d, c
                RETURN count(d) as deleted
            """, id=doc_id)
            
            deleted = result.single()["deleted"] > 0
            
            if deleted:
                logger.info(f"Deleted document {doc_id} with orphaned entity cleanup")
            
            return {
                "deleted": deleted,
                "orphaned_entities_removed": orphaned_count
            }
    
    def delete_documents(self, doc_ids: list[str]) -> dict:
        """
        Delete multiple documents, their chunks, and orphaned entities.
        
        Args:
            doc_ids: List of document IDs to delete
            
        Returns:
            Dict with 'deleted_count' (int), 'orphaned_entities_removed' (int)
        """
        total_deleted = 0
        total_orphaned = 0
        
        for doc_id in doc_ids:
            result = self.delete_document(doc_id)
            if result["deleted"]:
                total_deleted += 1
                total_orphaned += result["orphaned_entities_removed"]
        
        logger.info(f"Bulk deleted {total_deleted} documents, removed {total_orphaned} orphaned entities")
        
        return {
            "deleted_count": total_deleted,
            "orphaned_entities_removed": total_orphaned
        }
    
    def delete_all_documents(self) -> dict:
        """
        Delete all documents, chunks, and entities from the knowledge base.
        
        This is a destructive operation that clears the entire knowledge base.
        
        Returns:
            Dict with 'deleted_count' (int), 'entities_removed' (int)
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
            
            # Delete all entities (they will all be orphaned)
            session.run("MATCH (e:Entity) DETACH DELETE e")
            
            # Delete all chunks and documents
            session.run("""
                MATCH (d:Document)
                OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:Chunk)
                DETACH DELETE d, c
            """)
            
            logger.info(f"Deleted all documents: {doc_count} documents, {entity_count} entities")
            
            return {
                "deleted_count": doc_count,
                "entities_removed": entity_count
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
    
    def get_graph_visualization_data(self, limit: int = 100) -> dict:
        """
        Get data for visualizing the knowledge graph.
        
        Returns:
            Dict with 'nodes' and 'edges' for visualization
        """
        with self.driver.session() as session:
            # Get entities as nodes
            result = session.run("""
                MATCH (e:Entity)
                OPTIONAL MATCH (c:Chunk)-[:MENTIONS]->(e)
                WITH e, count(c) as mention_count
                RETURN e.name as id,
                       e.name as label,
                       e.type as type,
                       e.description as description,
                       mention_count
                ORDER BY mention_count DESC
                LIMIT $limit
            """, limit=limit)
            
            nodes = [dict(record) for record in result]
            node_ids = {n["id"] for n in nodes}
            
            # Get relationships as edges
            result = session.run("""
                MATCH (s:Entity)-[r]->(t:Entity)
                WHERE s.name IN $node_ids AND t.name IN $node_ids
                RETURN s.name as source,
                       t.name as target,
                       type(r) as type,
                       r.type as sub_type,
                       r.description as description
                LIMIT $limit
            """, node_ids=list(node_ids), limit=limit * 2)
            
            edges = []
            for record in result:
                edges.append({
                    "source": record["source"],
                    "target": record["target"],
                    "type": record["sub_type"] or record["type"],
                    "description": record["description"]
                })
            
            return {"nodes": nodes, "edges": edges}


# Singleton instance
_neo4j_service: Optional[Neo4jService] = None


def get_neo4j_service() -> Neo4jService:
    global _neo4j_service
    if _neo4j_service is None:
        _neo4j_service = Neo4jService()
    return _neo4j_service
