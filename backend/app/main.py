"""MOCA Knowledge Base - FastAPI Backend."""

import os
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
import aiofiles
import json

from app.config import get_settings
from app.models import (
    UploadResponse,
    SearchRequest,
    SearchResponse,
    SearchResult,
    RAGRequest,
    RAGResponse,
    HealthResponse,
    ProcessingStatus,
    GraphStatsResponse,
    GraphContext,
    ConversationMessage,
    ReprocessRequest,
    DeleteRequest,
)
from app.services.neo4j_service import get_neo4j_service
from app.services.document_processor import get_document_processor, get_query_processor
from app.services.graph_extractor import get_graph_extractor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    settings = get_settings()
    
    # Create upload directory
    os.makedirs(settings.upload_dir, exist_ok=True)
    
    # Initialize Neo4j
    neo4j = get_neo4j_service()
    try:
        neo4j.initialize_schema()
        logger.info("Neo4j schema initialized")
    except Exception as e:
        logger.warning(f"Could not initialize Neo4j schema: {e}")
    
    # Warm up processors
    try:
        get_document_processor()
        get_query_processor()
        logger.info("Processors initialized")
    except Exception as e:
        logger.warning(f"Could not initialize processors: {e}")
    
    yield
    
    # Cleanup
    neo4j.close()
    logger.info("Application shutdown complete")


app = FastAPI(
    title="MOCA Knowledge Base",
    description="A Neo4j + Haystack powered GraphRAG knowledge base with entity extraction, knowledge graph construction, and semantic search",
    version="2.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    neo4j = get_neo4j_service()
    connected = neo4j.verify_connectivity()
    
    return HealthResponse(
        status="healthy" if connected else "degraded",
        neo4j_connected=connected,
        version="1.0.0"
    )


@app.get("/api/stats", response_model=GraphStatsResponse)
async def get_stats():
    """Get knowledge base and knowledge graph statistics."""
    try:
        neo4j = get_neo4j_service()
        stats = neo4j.get_stats()
        return GraphStatsResponse(
            document_count=stats["document_count"],
            chunk_count=stats["chunk_count"],
            entity_count=stats.get("entity_count", 0),
            relationship_count=stats.get("relationship_count", 0),
            total_size=stats["total_size"]
        )
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/upload", response_model=UploadResponse)
async def upload_file(file: UploadFile = File(...)):
    """Upload a file to the knowledge base."""
    settings = get_settings()
    
    # Validate file extension
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in settings.allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"File type {file_ext} not supported. Allowed: {settings.allowed_extensions}"
        )
    
    # Read file content
    content = await file.read()
    file_size = len(content)
    
    # Validate file size
    max_size = settings.max_file_size_mb * 1024 * 1024
    if file_size > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.max_file_size_mb}MB"
        )
    
    # Save file temporarily
    import uuid
    temp_filename = f"{uuid.uuid4()}{file_ext}"
    file_path = os.path.join(settings.upload_dir, temp_filename)
    
    async with aiofiles.open(file_path, 'wb') as f:
        await f.write(content)
    
    # Process file
    try:
        processor = get_document_processor()
        doc_id = await processor.process_file(file_path, file.filename, file_size)
        
        return UploadResponse(
            document_id=doc_id,
            filename=file.filename,
            status=ProcessingStatus.PROCESSING,
            message="File uploaded and processing started"
        )
    except Exception as e:
        logger.error(f"Error processing file: {e}")
        # Clean up
        try:
            os.remove(file_path)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/documents")
async def list_documents():
    """List all documents in the knowledge base."""
    try:
        neo4j = get_neo4j_service()
        documents = neo4j.get_all_documents()
        return {"documents": documents, "total": len(documents)}
    except Exception as e:
        logger.error(f"Error listing documents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/documents/{document_id}")
async def get_document(document_id: str):
    """Get a specific document."""
    try:
        neo4j = get_neo4j_service()
        document = neo4j.get_document(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        return document
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting document: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/documents/{document_id}")
async def delete_document(document_id: str):
    """Delete a document and clean up orphaned entities from the knowledge base."""
    try:
        neo4j = get_neo4j_service()
        result = neo4j.delete_document(document_id)
        if not result["deleted"]:
            raise HTTPException(status_code=404, detail="Document not found")
        
        return {
            "message": "Document deleted successfully",
            "orphaned_entities_removed": result["orphaned_entities_removed"]
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting document: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/documents/delete")
async def delete_documents(request: DeleteRequest):
    """
    Delete multiple documents from the knowledge base.
    
    This endpoint deletes the specified documents and cleans up any orphaned entities.
    """
    try:
        neo4j = get_neo4j_service()
        result = neo4j.delete_documents(request.document_ids)
        
        return {
            "message": f"Successfully deleted {result['deleted_count']} document(s)",
            "deleted_count": result["deleted_count"],
            "orphaned_entities_removed": result["orphaned_entities_removed"]
        }
    except Exception as e:
        logger.error(f"Error deleting documents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/documents")
async def delete_all_documents():
    """
    Delete all documents from the knowledge base.
    
    WARNING: This is a destructive operation that removes all documents, chunks, and entities.
    """
    try:
        neo4j = get_neo4j_service()
        result = neo4j.delete_all_documents()
        
        return {
            "message": f"Successfully deleted all {result['deleted_count']} document(s)",
            "deleted_count": result["deleted_count"],
            "entities_removed": result["entities_removed"]
        }
    except Exception as e:
        logger.error(f"Error deleting all documents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/documents/{document_id}/reprocess")
async def reprocess_document(document_id: str, file: UploadFile = File(...)):
    """
    Reprocess a single document by re-uploading the file.
    
    This deletes existing chunks and entities, then reprocesses the file.
    """
    settings = get_settings()
    neo4j = get_neo4j_service()
    
    # Check document exists
    document = neo4j.get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Validate file extension
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in settings.allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"File type {file_ext} not supported. Allowed: {settings.allowed_extensions}"
        )
    
    # Read file content
    content = await file.read()
    file_size = len(content)
    
    # Validate file size
    max_size = settings.max_file_size_mb * 1024 * 1024
    if file_size > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.max_file_size_mb}MB"
        )
    
    # Save file temporarily
    import uuid
    temp_filename = f"{uuid.uuid4()}{file_ext}"
    file_path = os.path.join(settings.upload_dir, temp_filename)
    
    async with aiofiles.open(file_path, 'wb') as f:
        await f.write(content)
    
    try:
        processor = get_document_processor()
        await processor.reprocess_document_from_file(document_id, file_path, file_ext)
        
        return {
            "document_id": document_id,
            "filename": file.filename,
            "status": ProcessingStatus.PROCESSING,
            "message": "Reprocessing started"
        }
    except Exception as e:
        logger.error(f"Error reprocessing document: {e}")
        try:
            os.remove(file_path)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/documents/reprocess")
async def reprocess_documents(request: ReprocessRequest):
    """
    Mark multiple documents for reprocessing.
    
    Since original files are not stored, this resets documents to 'pending' status
    and clears their chunks. Documents need to be re-uploaded to complete reprocessing.
    
    Returns a list of document IDs that were successfully queued for reprocessing.
    """
    try:
        neo4j = get_neo4j_service()
        processor = get_document_processor()
        
        results = []
        for doc_id in request.document_ids:
            try:
                doc = neo4j.get_document(doc_id)
                if doc:
                    await processor.reprocess_document(doc_id)
                    results.append({
                        "document_id": doc_id,
                        "status": "queued",
                        "message": "Document chunks cleared, ready for re-upload"
                    })
                else:
                    results.append({
                        "document_id": doc_id,
                        "status": "error",
                        "message": "Document not found"
                    })
            except Exception as e:
                results.append({
                    "document_id": doc_id,
                    "status": "error",
                    "message": str(e)
                })
        
        return {
            "results": results,
            "total_queued": len([r for r in results if r["status"] == "queued"])
        }
    except Exception as e:
        logger.error(f"Error reprocessing documents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/cleanup/orphaned-entities")
async def cleanup_orphaned_entities():
    """
    Clean up orphaned entities from the knowledge graph.
    
    Orphaned entities are those not connected to any document chunk.
    This can happen from previous deletions or data inconsistencies.
    """
    try:
        neo4j = get_neo4j_service()
        deleted_count = neo4j.cleanup_orphaned_entities()
        return {
            "message": "Cleanup completed",
            "orphaned_entities_removed": deleted_count
        }
    except Exception as e:
        logger.error(f"Error cleaning up orphaned entities: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/search", response_model=SearchResponse)
async def search(request: SearchRequest):
    """Perform semantic search on the knowledge base."""
    try:
        processor = get_query_processor()
        results = processor.search(
            query=request.query,
            top_k=request.top_k,
            filters=request.filters
        )
        
        search_results = [
            SearchResult(
                document_id=r["document_id"],
                chunk_id=r["chunk_id"],
                content=r["content"],
                score=r["score"],
                metadata={"filename": r["filename"], "chunk_index": r["chunk_index"]}
            )
            for r in results
        ]
        
        return SearchResponse(
            query=request.query,
            results=search_results,
            total_results=len(search_results)
        )
    except Exception as e:
        logger.error(f"Error in search: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/ask", response_model=RAGResponse)
async def ask_question(request: RAGRequest):
    """
    Ask a question using enhanced GraphRAG.
    
    Features:
    - Hybrid search with RRF (vector + keyword + graph)
    - Cross-encoder re-ranking for precision
    - Conversation memory for context
    - Agentic multi-step reasoning (optional)
    """
    try:
        processor = get_query_processor()
        
        # Convert conversation history if provided
        conversation_history = None
        if request.conversation_history:
            conversation_history = request.conversation_history
        
        result = await processor.rag_query(
            question=request.question,
            top_k=request.top_k,
            use_graph=request.use_graph,
            max_hops=request.max_hops,
            conversation_history=conversation_history,
            use_reranking=request.use_reranking,
            use_agentic=request.use_agentic
        )
        
        sources = [
            SearchResult(
                document_id=r["document_id"],
                chunk_id=r["chunk_id"],
                content=r["content"],
                score=r.get("rerank_score", r.get("score", 0)),
                metadata={
                    "filename": r["filename"], 
                    "chunk_index": r.get("chunk_index", 0),
                    "rerank_score": r.get("rerank_score")
                }
            )
            for r in result["sources"]
        ]
        
        # Build graph context if available
        graph_context = None
        if result.get("graph_context"):
            graph_context = GraphContext(**result["graph_context"])
        
        return RAGResponse(
            question=result["question"],
            answer=result["answer"],
            sources=sources,
            graph_context=graph_context,
            reranked=result.get("reranked", False),
            reasoning_steps=result.get("reasoning_steps")
        )
    except Exception as e:
        logger.error(f"Error in GraphRAG query: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/ask/stream")
async def ask_question_stream(request: RAGRequest):
    """
    Stream the RAG response for better UX.
    
    Returns Server-Sent Events (SSE) with:
    - content: Streamed answer tokens
    - sources: Retrieved sources (at end)
    - graph_context: Graph context (at end)
    - done: Completion signal
    """
    settings = get_settings()
    
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=400, 
            detail="OpenAI API key required for streaming"
        )
    
    async def generate():
        try:
            from openai import AsyncOpenAI
            
            processor = get_query_processor()
            
            # First, do the retrieval (non-streaming part)
            conversation_history = request.conversation_history
            
            graph_context = None
            
            if request.use_graph:
                search_result = await processor.graph_search_async(
                    request.question,
                    top_k=request.top_k * 2,
                    max_hops=request.max_hops,
                    use_hybrid_rrf=settings.enable_hybrid_search
                )
                results = search_result["results"]
                graph_data = search_result["graph_context"]
                
                if graph_data["entities"] or graph_data["relationships"]:
                    graph_context = GraphContext(
                        entities=graph_data["entities"],
                        relationships=graph_data["relationships"],
                        chunks=graph_data["chunks"]
                    )
            else:
                results = processor.search(request.question, top_k=request.top_k * 2)
            
            # Re-rank if enabled
            if request.use_reranking and settings.enable_reranking and results:
                results = await processor.rerank_results_async(
                    request.question, results, request.top_k
                )
            else:
                results = results[:request.top_k]
            
            # Send sources first
            sources = [
                {
                    "document_id": r["document_id"],
                    "chunk_id": r["chunk_id"],
                    "content": r["content"],
                    "score": r.get("rerank_score", r.get("score", 0)),
                    "metadata": {"filename": r["filename"]}
                }
                for r in results
            ]
            yield f"data: {json.dumps({'sources': sources})}\n\n"
            
            # Send graph context
            if graph_context:
                yield f"data: {json.dumps({'graph_context': graph_context.model_dump()})}\n\n"
            
            # Build context for generation
            formatted_sources = ""
            for idx, r in enumerate(results):
                ref_id = f"src_{idx+1}"
                formatted_sources += f"\n[{ref_id}] Source: {r['filename']}\n{r['content']}\n"
            
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
            
            system_prompt = """You are an expert research assistant. Answer based ONLY on the provided context.

Guidelines:
1. Synthesize information from multiple sources into a coherent answer
2. Cite sources inline: [src_1], [src_2] for document references
3. Be precise and avoid hallucination - only state what the sources support
4. If the context doesn't contain enough information, say so explicitly"""
            
            prompt = f"""Answer the question based on the provided context. Cite your sources.

=== Document Context ===
{formatted_sources if formatted_sources else "No documents available."}
{graph_context_str}

### Question:
{request.question}

### Answer:"""
            
            # Build messages with conversation history
            messages = [{"role": "system", "content": system_prompt}]
            
            if conversation_history:
                max_history = settings.max_conversation_history
                for msg in conversation_history[-max_history:]:
                    messages.append({
                        "role": msg.role,
                        "content": msg.content
                    })
            
            messages.append({"role": "user", "content": prompt})
            
            # Stream the response using async client
            client = AsyncOpenAI(
                api_key=settings.openai_api_key,
                base_url=settings.openai_api_base,
            )
            
            stream = await client.chat.completions.create(
                model=settings.openai_model,
                messages=messages,
                temperature=0.3,
                max_tokens=1200,
                stream=True
            )
            
            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    yield f"data: {json.dumps({'content': content})}\n\n"
            
            yield f"data: {json.dumps({'done': True})}\n\n"
            
        except Exception as e:
            logger.error(f"Error in streaming RAG: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


# =============================================================================
# GraphRAG Endpoints
# =============================================================================

@app.get("/api/graph/visualization")
async def get_graph_visualization(limit: int = Query(default=100, ge=10, le=500)):
    """Get knowledge graph data for visualization."""
    try:
        neo4j = get_neo4j_service()
        data = neo4j.get_graph_visualization_data(limit=limit)
        return data
    except Exception as e:
        logger.error(f"Error getting graph visualization: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/entities")
async def list_entities(
    entity_type: Optional[str] = Query(default=None, description="Filter by entity type"),
    limit: int = Query(default=50, ge=1, le=200)
):
    """List entities in the knowledge graph."""
    try:
        neo4j = get_neo4j_service()
        with neo4j.driver.session() as session:
            if entity_type:
                result = session.run("""
                    MATCH (e:Entity {type: $type})
                    OPTIONAL MATCH (c:Chunk)-[:MENTIONS]->(e)
                    RETURN e.name as name, e.type as type, e.description as description,
                           count(c) as mention_count
                    ORDER BY mention_count DESC
                    LIMIT $limit
                """, type=entity_type, limit=limit)
            else:
                result = session.run("""
                    MATCH (e:Entity)
                    OPTIONAL MATCH (c:Chunk)-[:MENTIONS]->(e)
                    RETURN e.name as name, e.type as type, e.description as description,
                           count(c) as mention_count
                    ORDER BY mention_count DESC
                    LIMIT $limit
                """, limit=limit)
            
            entities = [dict(record) for record in result]
            return {"entities": entities, "total": len(entities)}
    except Exception as e:
        logger.error(f"Error listing entities: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/entity/{entity_name}")
async def get_entity_details(entity_name: str, max_hops: int = Query(default=2, ge=1, le=3)):
    """Get details about a specific entity and its relationships."""
    try:
        neo4j = get_neo4j_service()
        context = neo4j.traverse_from_entities([entity_name], max_hops=max_hops)
        
        if not context["entities"]:
            raise HTTPException(status_code=404, detail="Entity not found")
        
        return context
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting entity details: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/search")
async def search_entities(query: str = Query(..., min_length=1)):
    """Search for entities by name."""
    try:
        neo4j = get_neo4j_service()
        results = neo4j.find_entities_by_name([query])
        return {"query": query, "results": results}
    except Exception as e:
        logger.error(f"Error searching entities: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/status")
async def get_graph_status():
    """Get GraphRAG system status."""
    try:
        settings = get_settings()
        extractor = get_graph_extractor()
        neo4j = get_neo4j_service()
        stats = neo4j.get_stats()
        
        return {
            "graph_extraction_enabled": settings.enable_graph_extraction,
            "llm_available": extractor.is_available,
            "model": settings.openai_model if extractor.is_available else None,
            "entity_count": stats.get("entity_count", 0),
            "relationship_count": stats.get("relationship_count", 0),
        }
    except Exception as e:
        logger.error(f"Error getting graph status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
