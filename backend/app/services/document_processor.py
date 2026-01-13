"""Document processing service using Haystack with GraphRAG support.

Enhanced with R2R-style features:
- Hybrid search with RRF
- Conversation memory
- Re-ranking with cross-encoder
- Agentic multi-step RAG
- Enhanced chunking
"""

import os
import uuid
import logging
from pathlib import Path
from typing import Optional, List
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json

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
    ConversationMessage,
)
from app.services.neo4j_service import get_neo4j_service
from app.services.graph_extractor import get_graph_extractor

logger = logging.getLogger(__name__)

# Thread pool for re-ranking (cross-encoder can be slow)
_rerank_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="reranker")


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
        
        # Initialize splitter based on configuration
        # Sentence-based splitting preserves semantic units better
        if self.settings.chunk_by == "sentence":
            self.splitter = DocumentSplitter(
                split_by="sentence",
                split_length=self.settings.sentences_per_chunk,
                split_overlap=1  # 1 sentence overlap for context continuity
            )
            logger.info(f"Using sentence-based chunking: {self.settings.sentences_per_chunk} sentences per chunk")
        else:
            self.splitter = DocumentSplitter(
                split_by="word",
                split_length=self.settings.chunk_size,
                split_overlap=self.settings.chunk_overlap
            )
            logger.info(f"Using word-based chunking: {self.settings.chunk_size} words per chunk")
        
        # Initialize embedder based on configuration
        if self.settings.use_openai_embeddings and self.settings.openai_api_key:
            from haystack.components.embedders import OpenAIDocumentEmbedder
            from haystack.utils import Secret
            self.embedder = OpenAIDocumentEmbedder(
                api_key=Secret.from_token(self.settings.openai_api_key),
                api_base_url=self.settings.openai_api_base,
                model=self.settings.embedding_model,
                dimensions=self.settings.embedding_dimension,
            )
            logger.info(f"Using OpenAI embeddings: {self.settings.embedding_model} (dim={self.settings.embedding_dimension})")
        else:
            from haystack.components.embedders import SentenceTransformersDocumentEmbedder
            self.embedder = SentenceTransformersDocumentEmbedder(
                model="sentence-transformers/all-MiniLM-L6-v2"
            )
            self.embedder.warm_up()
            logger.info("Using SentenceTransformers embeddings")
        
        logger.info(f"Document processor initialized (GraphRAG: {self.graph_extractor.is_available})")
    
    async def reprocess_document(self, doc_id: str) -> bool:
        """
        Reprocess an existing document by deleting its chunks and re-extracting.
        
        Returns True if reprocessing started successfully.
        """
        # Get document info
        doc_info = self.neo4j.get_document(doc_id)
        if not doc_info:
            raise ValueError(f"Document {doc_id} not found")
        
        filename = doc_info["filename"]
        file_type = doc_info["file_type"]
        
        # Get the upload directory
        settings = get_settings()
        
        # Check if original file still exists (it shouldn't, but just in case)
        # We need to re-read from chunks if they exist, otherwise this won't work
        # For now, we need the file to be re-uploaded or we use the stored content
        
        # Delete existing chunks and entities
        cleanup_result = self.neo4j.delete_document_chunks(doc_id)
        logger.info(
            f"Cleaned up document {doc_id}: "
            f"{cleanup_result['chunks_deleted']} chunks, "
            f"{cleanup_result['orphaned_entities_removed']} orphaned entities"
        )
        
        # Get stored chunk content to rebuild (if any chunks existed)
        # Since we just deleted them, we need another approach
        # The best approach is to require the file to be available
        # For reprocessing, we'll need the file path to be provided or stored
        
        # For now, we'll mark this as needing file re-upload
        # A better approach would be to store file content in object storage
        
        # Update status to pending for reprocessing
        self.neo4j.update_document_status(
            doc_id, 
            ProcessingStatus.PENDING,
            progress_message="Ready for reprocessing - please re-upload the file"
        )
        
        return True
    
    async def reprocess_document_from_file(
        self, 
        doc_id: str,
        file_path: str,
        file_type: str
    ) -> bool:
        """
        Reprocess a document from an existing file.
        
        This deletes existing chunks/entities and reprocesses the file.
        """
        # Delete existing chunks and entities
        cleanup_result = self.neo4j.delete_document_chunks(doc_id)
        logger.info(
            f"Cleaned up document {doc_id}: "
            f"{cleanup_result['chunks_deleted']} chunks, "
            f"{cleanup_result['orphaned_entities_removed']} orphaned entities"
        )
        
        # Process in background (same as new document)
        asyncio.create_task(self._process_document(doc_id, file_path, file_type))
        
        return True
    
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
            self.neo4j.update_document_status(
                doc_id, ProcessingStatus.PROCESSING,
                progress_message="Starting document processing..."
            )
            self.neo4j.update_document_progress(doc_id, 0, 100, "Converting document...")
            
            # Convert file to Haystack document
            converter = self._get_converter(file_type)
            if not converter:
                raise ValueError(f"Unsupported file type: {file_type}")
            
            # Run conversion
            result = converter.run(sources=[Path(file_path)])
            documents = result.get("documents", [])
            
            if not documents:
                raise ValueError("No content extracted from file")
            
            self.neo4j.update_document_progress(doc_id, 10, 100, "Splitting into chunks...")
            
            # Split documents into chunks
            split_result = self.splitter.run(documents=documents)
            chunks = split_result.get("documents", [])
            
            self.neo4j.update_document_progress(doc_id, 15, 100, f"Generating embeddings for {len(chunks)} chunks...")
            
            # Generate embeddings
            embed_result = self.embedder.run(documents=chunks)
            embedded_chunks = embed_result.get("documents", [])
            
            self.neo4j.update_document_progress(doc_id, 25, 100, "Storing chunks in database...")
            
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
                
                # Update progress for chunk storage (25-35%)
                storage_progress = 25 + int((idx + 1) / len(embedded_chunks) * 10)
                if idx % 5 == 0 or idx == len(embedded_chunks) - 1:  # Update every 5 chunks
                    self.neo4j.update_document_progress(
                        doc_id, storage_progress, 100, 
                        f"Stored chunk {idx + 1}/{len(embedded_chunks)}"
                    )
            
            logger.info(f"Document {doc_id}: stored {len(embedded_chunks)} chunks")
            
            # =================================================================
            # GraphRAG: Extract entities and relationships from chunks
            # Uses R2R-style extraction with document summary for context
            # Processes multiple chunks concurrently based on concurrent_extractions setting
            # =================================================================
            if self.graph_extractor.is_available and self.settings.enable_graph_extraction:
                self.neo4j.update_document_status(
                    doc_id, ProcessingStatus.EXTRACTING,
                    progress_message="Extracting knowledge graph..."
                )
                self.neo4j.update_document_progress(doc_id, 35, 100, "Generating document summary...")
                logger.info(f"Document {doc_id}: starting graph extraction...")
                
                # Generate document summary for context (R2R-style)
                # Combine all chunk content for summary (limited to first ~5000 chars)
                full_text = " ".join([c.content for c in embedded_chunks])[:5000]
                document_summary = await self.graph_extractor.generate_document_summary_async(full_text)
                
                if document_summary:
                    logger.info(f"Document {doc_id}: generated summary for extraction context")
                
                self.neo4j.update_document_progress(doc_id, 40, 100, "Extracting entities and relationships...")
                
                # Use semaphore for concurrent extraction (controlled by config)
                concurrent_limit = self.settings.concurrent_extractions
                semaphore = asyncio.Semaphore(concurrent_limit)
                extraction_results = {}  # Store results by index
                completed_count = 0
                
                async def extract_chunk(idx: int, chunk, chunk_id: str):
                    """Extract from a single chunk with semaphore control."""
                    nonlocal completed_count
                    async with semaphore:
                        try:
                            extraction = await self.graph_extractor.extract_from_text_async(
                                chunk.content, 
                                document_summary=document_summary
                            )
                            extraction_results[idx] = (chunk_id, extraction)
                        except Exception as e:
                            logger.warning(f"Graph extraction failed for chunk {chunk_id}: {e}")
                            extraction_results[idx] = (chunk_id, None)
                        
                        # Update progress
                        completed_count += 1
                        extraction_progress = 40 + int(completed_count / len(embedded_chunks) * 55)
                        self.neo4j.update_document_progress(
                            doc_id, extraction_progress, 100,
                            f"Extracted {completed_count}/{len(embedded_chunks)} chunks ({total_entities} entities found)"
                        )
                
                # Create extraction tasks for all chunks
                extraction_tasks = [
                    extract_chunk(idx, chunk, chunk_ids[idx])
                    for idx, chunk in enumerate(embedded_chunks)
                ]
                
                logger.info(
                    f"Document {doc_id}: processing {len(extraction_tasks)} chunks "
                    f"with concurrency limit of {concurrent_limit}"
                )
                
                # Run all extractions concurrently (limited by semaphore)
                await asyncio.gather(*extraction_tasks)
                
                # Store results in order
                for idx in sorted(extraction_results.keys()):
                    chunk_id, extraction = extraction_results[idx]
                    if extraction and (extraction.entities or extraction.relationships):
                        counts = self.neo4j.store_graph_extraction(chunk_id, extraction)
                        total_entities += counts["entities"]
                        total_relationships += counts["relationships"]
                        
                        logger.debug(
                            f"Chunk {idx}: extracted {counts['entities']} entities, "
                            f"{counts['relationships']} relationships"
                        )
                
                logger.info(
                    f"Document {doc_id}: graph extraction complete - "
                    f"{total_entities} entities, {total_relationships} relationships"
                )
            
            self.neo4j.update_document_progress(doc_id, 100, 100, "Processing complete!")
            
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
    """Process queries for semantic search and GraphRAG with R2R-style enhancements."""
    
    def __init__(self):
        self.settings = get_settings()
        self.neo4j = get_neo4j_service()
        self.graph_extractor = get_graph_extractor()
        self._reranker = None  # Lazy load cross-encoder
        
        # Initialize text embedder based on configuration
        if self.settings.use_openai_embeddings and self.settings.openai_api_key:
            from haystack.components.embedders import OpenAITextEmbedder
            from haystack.utils import Secret
            self.text_embedder = OpenAITextEmbedder(
                api_key=Secret.from_token(self.settings.openai_api_key),
                api_base_url=self.settings.openai_api_base,
                model=self.settings.embedding_model,
                dimensions=self.settings.embedding_dimension,
            )
            logger.info(f"Using OpenAI text embeddings: {self.settings.embedding_model} (dim={self.settings.embedding_dimension})")
        else:
            from haystack.components.embedders import SentenceTransformersTextEmbedder
            self.text_embedder = SentenceTransformersTextEmbedder(
                model="sentence-transformers/all-MiniLM-L6-v2"
            )
            self.text_embedder.warm_up()
            logger.info("Using SentenceTransformers text embeddings")
        
        logger.info("Query processor initialized (GraphRAG + Reranking + Agentic RAG enabled)")
    
    @property
    def reranker(self):
        """Lazy load cross-encoder for re-ranking."""
        if self._reranker is None and self.settings.enable_reranking:
            try:
                from sentence_transformers import CrossEncoder
                self._reranker = CrossEncoder(self.settings.reranking_model)
                logger.info(f"Loaded cross-encoder: {self.settings.reranking_model}")
            except Exception as e:
                logger.warning(f"Failed to load cross-encoder, disabling reranking: {e}")
                self._reranker = False  # Mark as unavailable
        return self._reranker if self._reranker else None
    
    def rerank_results(
        self,
        query: str,
        results: List[dict],
        top_k: int = 5
    ) -> List[dict]:
        """
        Re-rank results using cross-encoder for better precision.
        
        Cross-encoders score query-document pairs directly,
        providing more accurate relevance scores than bi-encoders.
        """
        if not results or not self.reranker:
            return results[:top_k]
        
        try:
            # Create query-content pairs
            pairs = [(query, r.get("content", "")) for r in results]
            
            # Score with cross-encoder
            scores = self.reranker.predict(pairs)
            
            # Add rerank scores to results
            for i, score in enumerate(scores):
                results[i]["rerank_score"] = float(score)
            
            # Sort by rerank score
            reranked = sorted(results, key=lambda x: x.get("rerank_score", 0), reverse=True)
            
            logger.debug(f"Reranked {len(results)} results")
            return reranked[:top_k]
            
        except Exception as e:
            logger.warning(f"Reranking failed: {e}")
            return results[:top_k]
    
    async def rerank_results_async(
        self,
        query: str,
        results: List[dict],
        top_k: int = 5
    ) -> List[dict]:
        """Async version of rerank_results."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            _rerank_executor,
            lambda: self.rerank_results(query, results, top_k)
        )
    
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
        max_hops: int = 2,
        use_hybrid_rrf: bool = True
    ) -> dict:
        """
        Perform hybrid search combining vector similarity, keyword search, and graph traversal.
        Uses Reciprocal Rank Fusion (RRF) for better results.
        
        Returns:
            Dict with 'results', 'graph_context', and search metadata
        """
        # Generate query embedding
        query_embedding = self.embed_query(query)
        
        # Extract entities from the query (async to not block event loop)
        query_entities = []
        if self.graph_extractor.is_available:
            query_entities = await self.graph_extractor.extract_entities_from_query_async(query)
        
        # Use hybrid search with RRF if enabled
        if use_hybrid_rrf and self.settings.enable_hybrid_search:
            hybrid_result = self.neo4j.hybrid_search_rrf(
                query_embedding=query_embedding,
                query_text=query,
                entity_names=query_entities,
                top_k=top_k,
                max_hops=max_hops,
                vector_weight=self.settings.vector_weight,
                keyword_weight=self.settings.keyword_weight,
                graph_weight=self.settings.graph_weight
            )
            return {
                "results": hybrid_result["results"],
                "graph_context": hybrid_result["graph_context"],
                "search_method": "hybrid_rrf",
                "vector_count": hybrid_result.get("vector_count", 0),
                "keyword_count": hybrid_result.get("keyword_count", 0),
                "graph_chunk_count": hybrid_result.get("graph_chunk_count", 0)
            }
        else:
            # Legacy hybrid search
            result = self.neo4j.hybrid_search(
                query_embedding=query_embedding,
                entity_names=query_entities,
                top_k=top_k,
                max_hops=max_hops
            )
            return {
                "results": result["vector_results"],
                "graph_context": result["graph_context"],
                "search_method": "vector_graph"
            }
    
    async def rag_query(
        self, 
        question: str, 
        top_k: int = 5,
        use_graph: bool = True,
        max_hops: int = 2,
        conversation_history: Optional[List[ConversationMessage]] = None,
        use_reranking: bool = True,
        use_agentic: bool = False
    ) -> dict:
        """
        Answer a question using enhanced GraphRAG with R2R-style features.
        
        Features:
        - Hybrid search with RRF (vector + keyword + graph)
        - Cross-encoder re-ranking for precision
        - Conversation memory for context
        - Agentic multi-step reasoning for complex questions
        - Enhanced prompts for better answers
        """
        
        # If agentic mode is requested, use multi-step reasoning
        if use_agentic and self.settings.enable_agentic_rag:
            return await self._agentic_rag_query(
                question=question,
                top_k=top_k,
                max_hops=max_hops,
                conversation_history=conversation_history
            )
        
        graph_context = None
        search_metadata = {}
        
        if use_graph and self.graph_extractor.is_available:
            # Use hybrid search with RRF
            search_result = await self.graph_search_async(
                question, 
                top_k=top_k * 2,  # Get more for reranking
                max_hops=max_hops,
                use_hybrid_rrf=self.settings.enable_hybrid_search
            )
            results = search_result["results"]
            graph_data = search_result["graph_context"]
            search_metadata = {
                "search_method": search_result.get("search_method", "unknown"),
                "vector_count": search_result.get("vector_count", 0),
                "keyword_count": search_result.get("keyword_count", 0),
                "graph_chunk_count": search_result.get("graph_chunk_count", 0)
            }
            
            # Build graph context object
            if graph_data["entities"] or graph_data["relationships"]:
                graph_context = GraphContext(
                    entities=graph_data["entities"],
                    relationships=graph_data["relationships"],
                    chunks=graph_data["chunks"]
                )
        else:
            # Fall back to vector-only search
            results = self.search(question, top_k=top_k * 2)
            search_metadata = {"search_method": "vector_only"}
        
        # Apply re-ranking if enabled
        reranked = False
        if use_reranking and self.settings.enable_reranking and results:
            results = await self.rerank_results_async(question, results, top_k)
            reranked = True
        else:
            results = results[:top_k]
        
        if not results and (not graph_context or not graph_context.entities):
            return {
                "question": question,
                "answer": "I couldn't find any relevant information in the knowledge base.",
                "sources": [],
                "graph_context": None,
                "reranked": False,
                "reasoning_steps": None
            }
        
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
        
        # Check if OpenAI is configured
        if not self.settings.openai_api_key:
            full_context = "\n\n".join([
                f"[Source: {r['filename']}]\n{r['content']}"
                for r in results
            ]) + graph_context_str
            return {
                "question": question,
                "answer": f"Based on the knowledge base, here is the relevant information:\n\n{full_context}",
                "sources": results,
                "graph_context": graph_context.model_dump() if graph_context else None,
                "reranked": reranked,
                "reasoning_steps": None
            }
        
        # Generate answer with enhanced prompts
        try:
            from openai import OpenAI
            
            client = OpenAI(
                api_key=self.settings.openai_api_key,
                base_url=self.settings.openai_api_base,
            )
            
            # Enhanced R2R-style system prompt
            system_prompt = """You are an expert research assistant. Answer based ONLY on the provided context.

Guidelines:
1. Synthesize information from multiple sources into a coherent answer
2. Use knowledge graph relationships to understand entity connections
3. Cite sources inline: [src_1], [src_2] for document references
4. If entities from the graph provide relevant context, mention them
5. If the context doesn't contain enough information, say so explicitly
6. Structure longer answers with clear sections when appropriate
7. Be precise and avoid hallucination - only state what the sources support

Response Quality:
- Prefer specific facts over vague generalizations
- Connect related concepts using the relationship data when helpful
- If multiple sources conflict, acknowledge the discrepancy"""
            
            # Format sources with reference IDs
            formatted_sources = ""
            if results:
                for idx, r in enumerate(results):
                    ref_id = f"src_{idx+1}"
                    rerank_info = f" (relevance: {r.get('rerank_score', r.get('score', 0)):.3f})" if reranked else ""
                    formatted_sources += f"\n[{ref_id}] Source: {r['filename']}{rerank_info}\n{r['content']}\n"
            
            # Build the prompt
            prompt = f"""Answer the question based on the provided context. Use reference IDs like [src_1], [src_2] to cite your sources.

=== Document Context ===
{formatted_sources if formatted_sources else "No document excerpts available."}
{graph_context_str if graph_context_str else ""}

### Question:
{question}

### Answer:"""
            
            # Build messages with conversation history
            messages = [{"role": "system", "content": system_prompt}]
            
            # Add conversation history for context
            if conversation_history:
                max_history = self.settings.max_conversation_history
                for msg in conversation_history[-max_history:]:
                    messages.append({
                        "role": msg.role,
                        "content": msg.content
                    })
            
            messages.append({"role": "user", "content": prompt})
            
            response = client.chat.completions.create(
                model=self.settings.openai_model,
                messages=messages,
                temperature=0.3,
                max_tokens=1200  # Increased for more complete answers
            )
            
            answer = response.choices[0].message.content
            
            return {
                "question": question,
                "answer": answer,
                "sources": results,
                "graph_context": graph_context.model_dump() if graph_context else None,
                "reranked": reranked,
                "reasoning_steps": None,
                **search_metadata
            }
            
        except Exception as e:
            logger.error(f"Error in GraphRAG query: {e}")
            full_context = "\n\n".join([
                f"[Source: {r['filename']}]\n{r['content']}"
                for r in results
            ]) + graph_context_str
            return {
                "question": question,
                "answer": f"Error generating answer: {str(e)}. Here is the relevant context:\n\n{full_context}",
                "sources": results,
                "graph_context": graph_context.model_dump() if graph_context else None,
                "reranked": reranked,
                "reasoning_steps": None
            }
    
    async def _agentic_rag_query(
        self,
        question: str,
        top_k: int = 5,
        max_hops: int = 2,
        conversation_history: Optional[List[ConversationMessage]] = None
    ) -> dict:
        """
        Agentic multi-step RAG for complex questions.
        
        Inspired by R2R's Deep Research API:
        1. Break down complex questions into sub-questions
        2. Iteratively retrieve information
        3. Synthesize and identify gaps
        4. Generate comprehensive answer
        """
        from openai import OpenAI
        
        if not self.settings.openai_api_key:
            # Fall back to regular RAG if no LLM
            return await self.rag_query(
                question=question,
                top_k=top_k,
                use_graph=True,
                max_hops=max_hops,
                conversation_history=conversation_history,
                use_agentic=False
            )
        
        client = OpenAI(
            api_key=self.settings.openai_api_key,
            base_url=self.settings.openai_api_base,
        )
        
        reasoning_steps = []
        all_results = []
        all_graph_contexts = []
        
        # Step 1: Decompose the question into sub-questions
        reasoning_steps.append("Analyzing question complexity...")
        
        decompose_response = client.chat.completions.create(
            model=self.settings.openai_model,
            messages=[
                {"role": "system", "content": """You help break down complex questions into simpler sub-questions.
Output a JSON array of sub-questions that together would answer the main question.
If the question is simple, just return a single-element array with the original question.
Maximum 3 sub-questions. Format: {"sub_questions": ["q1", "q2", ...]}"""},
                {"role": "user", "content": f"Break down this question: {question}"}
            ],
            temperature=0.2,
            max_tokens=300
        )
        
        try:
            decompose_text = decompose_response.choices[0].message.content
            # Extract JSON from response
            import re
            json_match = re.search(r'\{[^{}]*"sub_questions"[^{}]*\}', decompose_text, re.DOTALL)
            if json_match:
                sub_questions = json.loads(json_match.group())["sub_questions"]
            else:
                sub_questions = [question]
        except Exception as e:
            logger.warning(f"Failed to decompose question: {e}")
            sub_questions = [question]
        
        reasoning_steps.append(f"Identified {len(sub_questions)} research areas")
        
        # Step 2: Research each sub-question
        for i, sub_q in enumerate(sub_questions[:self.settings.max_agentic_steps]):
            reasoning_steps.append(f"Researching: {sub_q[:50]}...")
            
            search_result = await self.graph_search_async(
                sub_q,
                top_k=top_k,
                max_hops=max_hops,
                use_hybrid_rrf=True
            )
            
            # Re-rank results
            if self.settings.enable_reranking and search_result["results"]:
                reranked_results = await self.rerank_results_async(
                    sub_q, 
                    search_result["results"], 
                    top_k
                )
                all_results.extend(reranked_results)
            else:
                all_results.extend(search_result["results"][:top_k])
            
            if search_result["graph_context"]:
                all_graph_contexts.append(search_result["graph_context"])
        
        # Deduplicate results by chunk_id
        seen_chunks = set()
        unique_results = []
        for r in all_results:
            chunk_id = r.get("chunk_id", "")
            if chunk_id and chunk_id not in seen_chunks:
                seen_chunks.add(chunk_id)
                unique_results.append(r)
        
        # Sort by score and take top results
        unique_results.sort(key=lambda x: x.get("rerank_score", x.get("score", 0)), reverse=True)
        final_results = unique_results[:top_k * 2]
        
        reasoning_steps.append(f"Gathered {len(final_results)} unique sources")
        
        # Merge graph contexts
        merged_entities = {}
        merged_relationships = []
        for gc in all_graph_contexts:
            for entity in gc.get("entities", []):
                name = entity.get("name", "")
                if name and name not in merged_entities:
                    merged_entities[name] = entity
            for rel in gc.get("relationships", []):
                merged_relationships.append(rel)
        
        graph_context = GraphContext(
            entities=list(merged_entities.values())[:15],
            relationships=merged_relationships[:20],
            chunks=[]
        ) if merged_entities else None
        
        # Step 3: Generate comprehensive answer
        reasoning_steps.append("Synthesizing final answer...")
        
        # Build context
        formatted_sources = ""
        for idx, r in enumerate(final_results):
            ref_id = f"src_{idx+1}"
            formatted_sources += f"\n[{ref_id}] Source: {r['filename']}\n{r['content']}\n"
        
        graph_context_str = ""
        if graph_context and graph_context.entities:
            entity_info = "\n".join([
                f"- {e['name']} ({e.get('type', 'Unknown')}): {e.get('description', '')}"
                for e in graph_context.entities
            ])
            graph_context_str += f"\n\n=== Related Entities ===\n{entity_info}"
        
        if graph_context and graph_context.relationships:
            rel_info = "\n".join([
                f"- {r['source']} --[{r['type']}]--> {r['target']}"
                for r in graph_context.relationships
            ])
            graph_context_str += f"\n\n=== Entity Relationships ===\n{rel_info}"
        
        # Enhanced system prompt for agentic mode
        system_prompt = """You are an expert research assistant that provides comprehensive, well-structured answers.

You have access to information gathered through multiple research steps. Your task is to synthesize this information into a complete, authoritative answer.

Guidelines:
1. Provide a comprehensive answer that addresses all aspects of the question
2. Organize complex answers with clear structure (sections, bullet points)
3. Cite sources using reference IDs: [src_1], [src_2], etc.
4. Highlight key findings and insights
5. Note any limitations or gaps in the available information
6. Connect related concepts using the entity relationships provided
7. Be precise and avoid making claims not supported by the sources"""
        
        # Build messages with conversation history
        messages = [{"role": "system", "content": system_prompt}]
        
        if conversation_history:
            max_history = self.settings.max_conversation_history
            for msg in conversation_history[-max_history:]:
                messages.append({
                    "role": msg.role,
                    "content": msg.content
                })
        
        prompt = f"""Based on comprehensive research, provide a detailed answer to this question.

=== Research Context ===
{formatted_sources if formatted_sources else "No document excerpts available."}
{graph_context_str if graph_context_str else ""}

### Question:
{question}

### Comprehensive Answer:"""
        
        messages.append({"role": "user", "content": prompt})
        
        response = client.chat.completions.create(
            model=self.settings.openai_model,
            messages=messages,
            temperature=0.3,
            max_tokens=2000  # Longer for comprehensive answers
        )
        
        answer = response.choices[0].message.content
        reasoning_steps.append("Answer generated successfully")
        
        return {
            "question": question,
            "answer": answer,
            "sources": final_results,
            "graph_context": graph_context.model_dump() if graph_context else None,
            "reranked": True,
            "reasoning_steps": reasoning_steps,
            "search_method": "agentic_rag",
            "sub_questions": sub_questions
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
