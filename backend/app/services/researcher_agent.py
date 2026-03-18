"""
Agent-based research pipeline with researcher/writer separation.

The researcher agent uses OpenAI function-calling to iteratively gather information
from the knowledge base. Tool calls are executed against the existing hybrid search,
community search, and entity lookup infrastructure. The writer then synthesizes all
gathered context into a streamed answer.

Two modes:
- Speed (chat): 2 iterations, knowledge_search + done only
- Quality (deep research): up to 10 iterations, all tools including reasoning

Entry point: run_research_pipeline() — an async generator yielding SSE-compatible events.
"""

import json
import asyncio
import logging
from typing import AsyncGenerator, Literal, Optional, List
from dataclasses import dataclass, field

from openai import AsyncOpenAI

from app.models import ConversationMessage, GraphContext
from app.services.research_prompts import (
    get_researcher_prompt,
    get_writer_system_prompt,
    get_writer_user_prompt,
    get_tools_for_mode,
)
from app.services.prompt_security import get_anti_injection_instruction

logger = logging.getLogger(__name__)


# =============================================================================
# Data Structures
# =============================================================================


@dataclass
class ResearchResult:
    """Accumulated results from the researcher agent loop."""

    sources: list = field(default_factory=list)
    graph_context: dict = field(
        default_factory=lambda: {"entities": [], "relationships": [], "chunks": []}
    )
    communities: list = field(default_factory=list)
    summary: str = ""
    search_count: int = 0
    total_sources_considered: int = 0


# =============================================================================
# Context Merging & Deduplication
# =============================================================================


def _merge_graph_context(accumulated: dict, new_ctx: dict) -> None:
    """Merge new graph context into accumulated context (mutates accumulated)."""
    if not new_ctx:
        return

    existing_entity_names = {e.get("name") for e in accumulated.get("entities", [])}
    for entity in new_ctx.get("entities", []):
        if entity.get("name") not in existing_entity_names:
            accumulated["entities"].append(entity)
            existing_entity_names.add(entity.get("name"))

    existing_rels = {
        (r.get("source"), r.get("type"), r.get("target"))
        for r in accumulated.get("relationships", [])
    }
    for rel in new_ctx.get("relationships", []):
        key = (rel.get("source"), rel.get("type"), rel.get("target"))
        if key not in existing_rels:
            accumulated["relationships"].append(rel)
            existing_rels.add(key)

    existing_chunk_ids = {c.get("chunk_id") for c in accumulated.get("chunks", [])}
    for chunk in new_ctx.get("chunks", []):
        if chunk.get("chunk_id") and chunk.get("chunk_id") not in existing_chunk_ids:
            accumulated["chunks"].append(chunk)
            existing_chunk_ids.add(chunk.get("chunk_id"))


def _deduplicate_sources(sources: list) -> list:
    """Deduplicate sources by chunk_id, keeping highest-scored version."""
    seen = {}
    for s in sources:
        cid = s.get("chunk_id")
        if not cid:
            continue
        score = s.get("rerank_score", s.get("score", 0))
        existing_score = seen.get(cid, {}).get(
            "rerank_score", seen.get(cid, {}).get("score", 0)
        )
        if cid not in seen or score > existing_score:
            seen[cid] = s
    return sorted(
        seen.values(),
        key=lambda x: x.get("rerank_score", x.get("score", 0)),
        reverse=True,
    )


# =============================================================================
# Tool Result Formatting (for agent consumption)
# =============================================================================


def _format_search_results_for_agent(results: list, graph_ctx: dict) -> str:
    """Format search results as concise text for the agent to decide next steps."""
    if not results:
        return "No results found for these queries."

    output = f"Found {len(results)} relevant sources.\n\n"
    for i, r in enumerate(results[:10]):
        score = r.get("rerank_score", r.get("score", 0))
        filename = r.get("filename", "Unknown")
        content = r.get("content", "")[:300]
        output += f"[{i + 1}] {filename} (relevance: {score:.3f})\n"
        output += f"   {content}{'...' if len(r.get('content', '')) > 300 else ''}\n\n"

    entities = graph_ctx.get("entities", [])
    if entities:
        entity_names = [e.get("name", "") for e in entities[:10]]
        output += f"\nRelated entities found: {', '.join(entity_names)}\n"

    relationships = graph_ctx.get("relationships", [])
    if relationships:
        output += "Key relationships:\n"
        for rel in relationships[:5]:
            source = rel.get("source", "?")
            rtype = rel.get("type", "?")
            target = rel.get("target", "?")
            output += f"  - {source} --[{rtype}]--> {target}\n"

    return output


def _format_communities_for_agent(communities: list) -> str:
    """Format community search results for the agent."""
    if not communities:
        return "No relevant communities found."

    output = f"Found {len(communities)} relevant communities.\n\n"
    for c in communities:
        name = c.get("name", "Unnamed")
        entity_count = c.get("entity_count", 0)
        summary = c.get("summary", "")[:400]
        output += f"- {name} ({entity_count} entities): {summary}\n\n"
    return output


def _format_entities_for_agent(entities: list) -> str:
    """Format entity lookup results for the agent."""
    if not entities:
        return "No entities found matching those names."

    output = f"Found {len(entities)} entities.\n\n"
    for e in entities:
        name = e.get("name", "Unknown")
        etype = e.get("type", "Unknown")
        desc = e.get("description", "")[:200]
        connections = e.get("connection_count", 0)
        output += f"- {name} ({etype}): {desc}\n  Connections: {connections}\n\n"
    return output


# =============================================================================
# Tool Execution
# =============================================================================


async def _execute_knowledge_search(
    queries: list,
    original_question: str,
    collection_id: Optional[str],
    processor,
    settings,
) -> tuple:
    """
    Execute hybrid search for each query in parallel, then deduplicate and rerank.

    Uses the existing graph_search_async (hybrid RRF: vector + fulltext + graph traversal)
    and rerank_results_async (cross-encoder) infrastructure.
    """
    queries = queries[:3]  # Cap at 3 queries

    # Execute all queries in parallel
    tasks = [
        processor.graph_search_async(
            q, top_k=5, use_hybrid_rrf=True, collection_id=collection_id
        )
        for q in queries
    ]
    search_results = await asyncio.gather(*tasks, return_exceptions=True)

    all_results = []
    merged_ctx = {"entities": [], "relationships": [], "chunks": []}

    for result in search_results:
        if isinstance(result, Exception):
            logger.warning(f"Knowledge search query failed: {result}")
            continue
        all_results.extend(result.get("results", []))
        _merge_graph_context(merged_ctx, result.get("graph_context", {}))

    # Rerank all results together against the original question
    if settings.enable_reranking and all_results:
        try:
            all_results = await processor.rerank_results_async(
                original_question, all_results, top_k=15
            )
        except Exception as e:
            logger.warning(f"Reranking failed, using raw scores: {e}")
            all_results = sorted(
                all_results, key=lambda x: x.get("score", 0), reverse=True
            )[:15]
    else:
        all_results = sorted(
            all_results, key=lambda x: x.get("score", 0), reverse=True
        )[:15]

    # Deduplicate by chunk_id
    unique = _deduplicate_sources(all_results)

    return unique, merged_ctx


# =============================================================================
# Researcher Agent Loop
# =============================================================================


async def _run_researcher_loop(
    question: str,
    mode: Literal["speed", "quality"],
    conversation_history: List[ConversationMessage],
    collection_id: Optional[str],
    processor,
    neo4j_service,
    client: AsyncOpenAI,
    llm_config,
    settings,
) -> AsyncGenerator[dict, None]:
    """
    Run the researcher agent loop. Yields streaming events and a final result event.

    Events yielded:
    - {"type": "thinking", "content": "..."} — reasoning/status
    - {"type": "retrieval", "content": "..."} — search progress
    - {"type": "result", "data": ResearchResult} — final accumulated result
    """
    max_iterations = (
        settings.researcher_max_iterations_speed
        if mode == "speed"
        else settings.researcher_max_iterations_quality
    )
    tools = get_tools_for_mode(mode)

    # Build initial messages
    system_prompt = get_researcher_prompt(mode, 0, max_iterations)
    system_prompt += get_anti_injection_instruction(enabled=settings.prompt_security)

    messages = [{"role": "system", "content": system_prompt}]
    for msg in conversation_history[-settings.max_conversation_history :]:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": question})

    result = ResearchResult()
    communities_used_ids = []

    for iteration in range(max_iterations):
        # Update iteration count in system prompt
        messages[0]["content"] = get_researcher_prompt(
            mode, iteration, max_iterations
        ) + get_anti_injection_instruction(enabled=settings.prompt_security)

        try:
            response = await client.chat.completions.create(
                model=llm_config.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.2,
            )
        except Exception as e:
            logger.error(
                f"Researcher agent error on iteration {iteration + 1}: {e}"
            )
            yield {
                "type": "thinking",
                "content": "Research encountered an error, generating answer with available information...",
            }
            break

        assistant_message = response.choices[0].message

        if not assistant_message.tool_calls:
            # Model output text instead of calling tools — treat as implicit done
            messages.append(assistant_message)
            if assistant_message.content:
                # Use the text as the summary
                result.summary = assistant_message.content
            break

        # Add assistant message to conversation
        messages.append(assistant_message)

        # Sort tool calls: reasoning first, done last, search tools in middle
        sorted_calls = sorted(
            assistant_message.tool_calls,
            key=lambda tc: (
                0
                if tc.function.name == "reasoning"
                else 2
                if tc.function.name == "done"
                else 1
            ),
        )

        done_called = False

        for tool_call in sorted_calls:
            name = tool_call.function.name

            try:
                args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                logger.warning(
                    f"Invalid tool arguments for {name}: {tool_call.function.arguments}"
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps({"error": "Invalid JSON arguments"}),
                    }
                )
                continue

            if name == "done":
                result.summary = args.get("summary", "")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps({"status": "research_complete"}),
                    }
                )
                done_called = True

            elif name == "reasoning":
                thought = args.get("thought", "")
                if thought:
                    yield {"type": "thinking", "content": thought}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps({"status": "ok"}),
                    }
                )

            elif name == "knowledge_search":
                queries = args.get("queries", [])
                if queries:
                    yield {
                        "type": "thinking",
                        "content": f"Searching: {', '.join(q[:50] for q in queries[:3])}",
                    }

                    sources, graph_ctx = await _execute_knowledge_search(
                        queries, question, collection_id, processor, settings
                    )

                    result.sources.extend(sources)
                    result.total_sources_considered += len(sources)
                    result.search_count += 1
                    _merge_graph_context(result.graph_context, graph_ctx)

                    yield {
                        "type": "retrieval",
                        "content": f"Found {len(sources)} sources",
                    }

                    agent_text = _format_search_results_for_agent(sources, graph_ctx)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": agent_text,
                        }
                    )
                else:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": "No queries provided.",
                        }
                    )

            elif name == "community_search":
                query = args.get("query", "")
                if query and neo4j_service:
                    try:
                        communities = neo4j_service.search_communities_by_content(
                            query, limit=3
                        )
                    except Exception as e:
                        logger.warning(f"Community search failed: {e}")
                        communities = []

                    result.communities.extend(communities)
                    communities_used_ids.extend(
                        [c.get("id") for c in communities if c.get("id")]
                    )

                    yield {
                        "type": "retrieval",
                        "content": f"Found {len(communities)} relevant communities",
                    }

                    agent_text = _format_communities_for_agent(communities)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": agent_text,
                        }
                    )
                else:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": "No results.",
                        }
                    )

            elif name == "entity_lookup":
                names = args.get("names", [])
                if names and neo4j_service:
                    try:
                        entities = neo4j_service.find_entities_by_name(names[:5])
                    except Exception as e:
                        logger.warning(f"Entity lookup failed: {e}")
                        entities = []

                    yield {
                        "type": "retrieval",
                        "content": f"Found {len(entities)} entities",
                    }

                    agent_text = _format_entities_for_agent(entities)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": agent_text,
                        }
                    )
                else:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": "No entities found.",
                        }
                    )

            else:
                # Unknown tool
                logger.warning(f"Researcher called unknown tool: {name}")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(
                            {"error": f"Unknown tool: {name}"}
                        ),
                    }
                )

        if done_called:
            break

    # Yield final result
    yield {"type": "result", "data": result, "communities_used": communities_used_ids}


# =============================================================================
# Full Research Pipeline (Researcher → Writer)
# =============================================================================


async def run_research_pipeline(
    question: str,
    mode: Literal["speed", "quality"],
    conversation_history: Optional[List[ConversationMessage]] = None,
    collection_id: Optional[str] = None,
    processor=None,
    neo4j_service=None,
    llm_config=None,
    settings=None,
) -> AsyncGenerator[dict, None]:
    """
    Full research pipeline: researcher agent loop → writer stream.

    Yields SSE-compatible events matching the existing frontend contract:
    - {"thinking": "..."} — reasoning/status updates
    - {"retrieval": "..."} — search progress
    - {"sources": [...]} — accumulated sources for display
    - {"graph_context": {...}} — merged graph context
    - {"retrieval_stats": {...}} — research summary stats
    - {"content": "..."} — streamed answer tokens
    - {"done": True, "communities_used": [...]} — completion signal
    """
    conversation_history = conversation_history or []

    writer_max_tokens = (
        settings.writer_max_tokens_speed
        if mode == "speed"
        else settings.writer_max_tokens_quality
    )

    client = AsyncOpenAI(
        api_key=llm_config.api_key,
        base_url=llm_config.base_url,
    )

    yield {"thinking": "Starting research..."}

    # =========================================================================
    # Phase 1: Researcher Agent Loop
    # =========================================================================

    research_result = ResearchResult()
    communities_used = []

    async for event in _run_researcher_loop(
        question=question,
        mode=mode,
        conversation_history=conversation_history,
        collection_id=collection_id,
        processor=processor,
        neo4j_service=neo4j_service,
        client=client,
        llm_config=llm_config,
        settings=settings,
    ):
        if event["type"] == "result":
            research_result = event["data"]
            communities_used = event.get("communities_used", [])
        elif event["type"] == "thinking":
            yield {"thinking": event["content"]}
        elif event["type"] == "retrieval":
            yield {"retrieval": event["content"]}

    # =========================================================================
    # Phase 2: Prepare Context for Writer
    # =========================================================================

    # Deduplicate accumulated sources
    unique_sources = _deduplicate_sources(research_result.sources)

    # Cap sources for writer context
    max_writer_sources = 15 if mode == "quality" else 8
    writer_sources = unique_sources[:max_writer_sources]

    # Format sources with reference IDs for the writer
    formatted_sources = ""
    for idx, r in enumerate(writer_sources):
        ref_id = f"src_{idx + 1}"
        score = r.get("rerank_score", r.get("score", 0))
        filename = r.get("filename", "Unknown")
        content = r.get("content", "")
        formatted_sources += (
            f"\n[{ref_id}] Source: {filename} (relevance: {score:.3f})\n{content}\n"
        )

    # Build graph context string
    graph_context_str = ""
    entities = research_result.graph_context.get("entities", [])
    relationships = research_result.graph_context.get("relationships", [])

    max_entities = 15 if mode == "quality" else 10
    if entities:
        entity_info = "\n".join(
            [
                f"- {e.get('name', '?')} ({e.get('type', 'Unknown')}): {e.get('description', '')}"
                for e in entities[:max_entities]
            ]
        )
        graph_context_str += f"\n\n=== Related Entities ===\n{entity_info}"

    max_rels = 20 if mode == "quality" else 15
    if relationships:
        rel_info = "\n".join(
            [
                f"- {r.get('source', '?')} --[{r.get('type', '?')}]--> {r.get('target', '?')}"
                for r in relationships[:max_rels]
            ]
        )
        graph_context_str += f"\n\n=== Entity Relationships ===\n{rel_info}"

    # Add community summaries
    if research_result.communities:
        seen_ids = set()
        unique_communities = []
        for c in research_result.communities:
            cid = c.get("id")
            if cid not in seen_ids:
                seen_ids.add(cid)
                unique_communities.append(c)

        if unique_communities:
            community_info = "\n".join(
                [
                    f"- {c.get('name', 'Unnamed')}: {c.get('summary', '')}"
                    for c in unique_communities[:5]
                ]
            )
            graph_context_str += (
                f"\n\n=== Relevant Knowledge Communities ===\n{community_info}"
            )

    # Emit sources to frontend
    source_events = [
        {
            "document_id": r.get("document_id", ""),
            "chunk_id": r.get("chunk_id", ""),
            "content": r.get("content", ""),
            "score": r.get("rerank_score", r.get("score", 0)),
            "metadata": {"filename": r.get("filename", "")},
        }
        for r in writer_sources
    ]
    yield {"sources": source_events}

    # Emit graph context
    graph_context_obj = GraphContext(
        entities=entities[:15],
        relationships=relationships[:20],
        communities=research_result.communities[:5],
    )
    yield {"graph_context": graph_context_obj.model_dump()}

    # Emit retrieval stats
    yield {
        "retrieval_stats": {
            "total_sources_considered": research_result.total_sources_considered,
            "unique_sources": len(unique_sources),
            "search_calls": research_result.search_count,
            "communities_used": len(set(communities_used)),
        }
    }

    # =========================================================================
    # Phase 3: Writer Generates Answer
    # =========================================================================

    anti_injection = get_anti_injection_instruction(enabled=settings.prompt_security)
    writer_system = get_writer_system_prompt(mode, anti_injection)

    has_history = len(conversation_history) > 0
    writer_user = get_writer_user_prompt(
        mode=mode,
        formatted_sources=formatted_sources,
        graph_context_str=graph_context_str,
        question=question,
        researcher_summary=research_result.summary,
        has_history=has_history,
    )

    writer_messages = [{"role": "system", "content": writer_system}]

    # Include conversation history for multi-turn context
    if conversation_history:
        for msg in conversation_history[-settings.max_conversation_history :]:
            writer_messages.append({"role": msg.role, "content": msg.content})

    writer_messages.append({"role": "user", "content": writer_user})

    try:
        stream = await client.chat.completions.create(
            model=llm_config.model,
            messages=writer_messages,
            temperature=0.3,
            max_tokens=writer_max_tokens,
            stream=True,
        )

        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield {"content": chunk.choices[0].delta.content}

    except Exception as e:
        logger.error(f"Writer streaming error: {e}")
        yield {
            "content": "I encountered an error generating the response. Please try again."
        }

    yield {"done": True, "communities_used": list(set(communities_used))}
