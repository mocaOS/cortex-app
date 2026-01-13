"""Document processing service using Haystack with GraphRAG support."""

import os
import uuid
import logging
from pathlib import Path
from typing import Optional, List
import asyncio

from haystack import Document as HaystackDocument
from haystack.components.converters import (
    PyPDFToDocument,
    TextFileToDocument,
    MarkdownToDocument,
)
from haystack.components.preprocessors import DocumentSplitter

from app.config import get_settings
from app.models import (
    DocumentChunk,
    DocumentMetadata,
    ProcessingStatus,
    GraphContext,
)
from app.services.neo4j_service import get_neo4j_service
from app.services.graph_extractor import get_graph_extractor

logger = logging.getLogger(__name__)


class DocumentProcessor:
    """Process documents using Haystack components with GraphRAG extraction."""
    
    def __init__(self):
        self.settings = get_settings()
        self.neo4j = get_neo4j_service()
        self.graph_extractor = get_graph_extractor()
        
        # Initialize converters
        self.pdf_converter = PyPDFToDocument()
        self.text_converter = TextFileToDocument()
        self.md_converter = MarkdownToDocument()
        
        # Initialize splitter
        self.splitter = DocumentSplitter(
            split_by="word",
            split_length=self.settings.chunk_size,
            split_overlap=self.settings.chunk_overlap
        )
        
        # Initialize embedder based on configuration
        if self.settings.use_openai_embeddings and self.settings.openai_api_key:
            from haystack.components.embedders import OpenAIDocumentEmbedder
            from haystack.utils import Secret
            self.embedder = OpenAIDocumentEmbedder(
                api_key=Secret.from_token(self.settings.openai_api_key),
                api_base_url=self.settings.openai_api_base,
                model=self.settings.embedding_model,
            )
            logger.info(f"Using OpenAI embeddings: {self.settings.embedding_model}")
        else:
            from haystack.components.embedders import SentenceTransformersDocumentEmbedder
            self.embedder = SentenceTransformersDocumentEmbedder(
                model="sentence-transformers/all-MiniLM-L6-v2"
            )
            self.embedder.warm_up()
            logger.info("Using SentenceTransformers embeddings")
        
        logger.info(f"Document processor initialized (GraphRAG: {self.graph_extractor.is_available})")
    
    def _get_converter(self, file_type: str):
        """Get the appropriate converter for a file type."""
        converters = {
            ".pdf": self.pdf_converter,
            ".txt": self.text_converter,
            ".md": self.md_converter,
            ".markdown": self.md_converter,
        }
        return converters.get(file_type.lower())
    
    async def process_file(
        self, 
        file_path: str, 
        filename: str,
        file_size: int
    ) -> str:
        """Process a file and store it in the knowledge base."""
        doc_id = str(uuid.uuid4())
        file_type = Path(filename).suffix.lower()
        
        # Create document metadata
        metadata = DocumentMetadata(
            filename=filename,
            file_type=file_type,
            file_size=file_size,
            processing_status=ProcessingStatus.PENDING
        )
        
        # Store document node
        self.neo4j.store_document(doc_id, metadata)
        
        # Process in background
        asyncio.create_task(self._process_document(doc_id, file_path, file_type))
        
        return doc_id
    
    async def _process_document(
        self, 
        doc_id: str, 
        file_path: str,
        file_type: str
    ):
        """Background task to process a document with GraphRAG extraction."""
        total_entities = 0
        total_relationships = 0
        
        try:
            # Update status to processing
            self.neo4j.update_document_status(doc_id, ProcessingStatus.PROCESSING)
            
            # Convert file to Haystack document
            converter = self._get_converter(file_type)
            if not converter:
                raise ValueError(f"Unsupported file type: {file_type}")
            
            # Run conversion
            result = converter.run(sources=[Path(file_path)])
            documents = result.get("documents", [])
            
            if not documents:
                raise ValueError("No content extracted from file")
            
            # Split documents into chunks
            split_result = self.splitter.run(documents=documents)
            chunks = split_result.get("documents", [])
            
            # Generate embeddings
            embed_result = self.embedder.run(documents=chunks)
            embedded_chunks = embed_result.get("documents", [])
            
            # Store chunks in Neo4j
            chunk_ids = []
            for idx, chunk in enumerate(embedded_chunks):
                chunk_id = f"{doc_id}_chunk_{idx}"
                chunk_ids.append(chunk_id)
                chunk_model = DocumentChunk(
                    id=chunk_id,
                    document_id=doc_id,
                    content=chunk.content,
                    embedding=chunk.embedding,
                    chunk_index=idx,
                    metadata=chunk.meta
                )
                self.neo4j.store_chunk(chunk_model)
            
            logger.info(f"Document {doc_id}: stored {len(embedded_chunks)} chunks")
            
            # =================================================================
            # GraphRAG: Extract entities and relationships from chunks
            # Uses async extraction to avoid blocking the event loop
            # =================================================================
            if self.graph_extractor.is_available and self.settings.enable_graph_extraction:
                self.neo4j.update_document_status(doc_id, ProcessingStatus.EXTRACTING)
                logger.info(f"Document {doc_id}: starting graph extraction...")
                
                for idx, chunk in enumerate(embedded_chunks):
                    chunk_id = chunk_ids[idx]
                    
                    try:
                        # Extract entities and relationships (async to not block event loop)
                        extraction = await self.graph_extractor.extract_from_text_async(chunk.content)
                        
                        if extraction.entities or extraction.relationships:
                            # Store in Neo4j
                            counts = self.neo4j.store_graph_extraction(chunk_id, extraction)
                            total_entities += counts["entities"]
                            total_relationships += counts["relationships"]
                            
                            logger.debug(
                                f"Chunk {idx}: extracted {counts['entities']} entities, "
                                f"{counts['relationships']} relationships"
                            )
                    except Exception as e:
                        logger.warning(f"Graph extraction failed for chunk {chunk_id}: {e}")
                        # Continue processing other chunks
                
                logger.info(
                    f"Document {doc_id}: graph extraction complete - "
                    f"{total_entities} entities, {total_relationships} relationships"
                )
            
            # Update document status
            self.neo4j.update_document_status(
                doc_id, 
                ProcessingStatus.COMPLETED,
                chunk_count=len(embedded_chunks)
            )
            
            logger.info(
                f"Document {doc_id} processed successfully: "
                f"{len(embedded_chunks)} chunks, {total_entities} entities, "
                f"{total_relationships} relationships"
            )
            
        except Exception as e:
            logger.error(f"Error processing document {doc_id}: {e}")
            self.neo4j.update_document_status(
                doc_id,
                ProcessingStatus.FAILED,
                error_message=str(e)
            )
        finally:
            # Clean up uploaded file
            try:
                os.remove(file_path)
            except Exception:
                pass


class QueryProcessor:
    """Process queries for semantic search and GraphRAG."""
    
    def __init__(self):
        self.settings = get_settings()
        self.neo4j = get_neo4j_service()
        self.graph_extractor = get_graph_extractor()
        
        # Initialize text embedder based on configuration
        if self.settings.use_openai_embeddings and self.settings.openai_api_key:
            from haystack.components.embedders import OpenAITextEmbedder
            from haystack.utils import Secret
            self.text_embedder = OpenAITextEmbedder(
                api_key=Secret.from_token(self.settings.openai_api_key),
                api_base_url=self.settings.openai_api_base,
                model=self.settings.embedding_model,
            )
            logger.info(f"Using OpenAI text embeddings: {self.settings.embedding_model}")
        else:
            from haystack.components.embedders import SentenceTransformersTextEmbedder
            self.text_embedder = SentenceTransformersTextEmbedder(
                model="sentence-transformers/all-MiniLM-L6-v2"
            )
            self.text_embedder.warm_up()
            logger.info("Using SentenceTransformers text embeddings")
        
        logger.info("Query processor initialized (GraphRAG enabled)")
    
    def embed_query(self, query: str) -> list[float]:
        """Generate embedding for a query."""
        result = self.text_embedder.run(text=query)
        return result["embedding"]
    
    def search(
        self, 
        query: str, 
        top_k: int = 5,
        filters: Optional[dict] = None
    ) -> list[dict]:
        """Perform semantic search."""
        # Generate query embedding
        query_embedding = self.embed_query(query)
        
        # Search in Neo4j
        results = self.neo4j.vector_search(
            query_embedding=query_embedding,
            top_k=top_k,
            filters=filters
        )
        
        return results
    
    async def graph_search_async(
        self,
        query: str,
        top_k: int = 5,
        max_hops: int = 2
    ) -> dict:
        """
        Perform hybrid search combining vector similarity and graph traversal.
        Uses async extraction to avoid blocking the event loop.
        
        Returns:
            Dict with 'vector_results' and 'graph_context'
        """
        # Generate query embedding
        query_embedding = self.embed_query(query)
        
        # Extract entities from the query (async to not block event loop)
        query_entities = []
        if self.graph_extractor.is_available:
            query_entities = await self.graph_extractor.extract_entities_from_query_async(query)
        
        # Perform hybrid search
        return self.neo4j.hybrid_search(
            query_embedding=query_embedding,
            entity_names=query_entities,
            top_k=top_k,
            max_hops=max_hops
        )
    
    async def rag_query(
        self, 
        question: str, 
        top_k: int = 5,
        use_graph: bool = True,
        max_hops: int = 2
    ) -> dict:
        """Answer a question using GraphRAG (vector search + knowledge graph)."""
        
        graph_context = None
        
        if use_graph and self.graph_extractor.is_available:
            # Use hybrid search (vector + graph) - async to not block event loop
            hybrid_results = await self.graph_search_async(question, top_k=top_k, max_hops=max_hops)
            results = hybrid_results["vector_results"]
            graph_data = hybrid_results["graph_context"]
            
            # Build graph context object
            if graph_data["entities"] or graph_data["relationships"]:
                graph_context = GraphContext(
                    entities=graph_data["entities"],
                    relationships=graph_data["relationships"],
                    chunks=graph_data["chunks"]
                )
        else:
            # Fall back to vector-only search
            results = self.search(question, top_k=top_k)
        
        if not results and (not graph_context or not graph_context.entities):
            return {
                "question": question,
                "answer": "I couldn't find any relevant information in the knowledge base.",
                "sources": [],
                "graph_context": None
            }
        
        # Build context from vector search results
        vector_context = "\n\n".join([
            f"[Source: {r['filename']}]\n{r['content']}"
            for r in results
        ]) if results else ""
        
        # Build context from graph (entities and relationships)
        graph_context_str = ""
        if graph_context and graph_context.entities:
            entity_info = "\n".join([
                f"- {e['name']} ({e.get('type', 'Unknown')}): {e.get('description', '')}"
                for e in graph_context.entities[:10]
            ])
            graph_context_str += f"\n\n=== Related Entities ===\n{entity_info}"
        
        if graph_context and graph_context.relationships:
            rel_info = "\n".join([
                f"- {r['source']} --[{r['type']}]--> {r['target']}"
                for r in graph_context.relationships[:15]
            ])
            graph_context_str += f"\n\n=== Entity Relationships ===\n{rel_info}"
        
        # Combine contexts
        full_context = vector_context + graph_context_str
        
        # Check if OpenAI is configured
        if not self.settings.openai_api_key:
            return {
                "question": question,
                "answer": f"Based on the knowledge base, here is the relevant information:\n\n{full_context}",
                "sources": results,
                "graph_context": graph_context.model_dump() if graph_context else None
            }
        
        # Use OpenAI for generation with enhanced context
        try:
            from openai import OpenAI
            
            client = OpenAI(
                api_key=self.settings.openai_api_key,
                base_url=self.settings.openai_api_base,
            )
            
            system_prompt = """You are a helpful assistant that answers questions based on the provided context.
Use information from both the document excerpts and the knowledge graph (entities and relationships) to provide comprehensive answers.
If the knowledge graph provides relevant entity information or relationships, incorporate that into your answer.
Be concise and accurate. Only use information from the context to answer."""
            
            prompt = f"""Answer the question based on the provided context.

=== Document Context ===
{vector_context if vector_context else "No document excerpts available."}
{graph_context_str if graph_context_str else ""}

Question: {question}

Answer:"""
            
            response = client.chat.completions.create(
                model=self.settings.openai_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=700
            )
            
            answer = response.choices[0].message.content
            
            return {
                "question": question,
                "answer": answer,
                "sources": results,
                "graph_context": graph_context.model_dump() if graph_context else None
            }
            
        except Exception as e:
            logger.error(f"Error in GraphRAG query: {e}")
            return {
                "question": question,
                "answer": f"Error generating answer: {str(e)}. Here is the relevant context:\n\n{full_context}",
                "sources": results,
                "graph_context": graph_context.model_dump() if graph_context else None
            }


# Singleton instances
_document_processor: Optional[DocumentProcessor] = None
_query_processor: Optional[QueryProcessor] = None


def get_document_processor() -> DocumentProcessor:
    global _document_processor
    if _document_processor is None:
        _document_processor = DocumentProcessor()
    return _document_processor


def get_query_processor() -> QueryProcessor:
    global _query_processor
    if _query_processor is None:
        _query_processor = QueryProcessor()
    return _query_processor
