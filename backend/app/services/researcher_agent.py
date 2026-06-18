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
import os
import re
import time
import uuid
from typing import AsyncGenerator, Literal, Optional, List
from dataclasses import dataclass, field

import httpx
from openai import AsyncOpenAI

from app.models import ConversationMessage, GraphContext
from app.services.research_prompts import (
    get_researcher_prompt,
    get_researcher_prompt_static,
    get_writer_system_prompt,
    get_writer_user_prompt,
    get_tools_for_mode,
    get_tools_with_skill_activation,
    build_skill_catalog_block,
    build_activated_skills_block,
)
from app.services.reasoning_config import apply_cache_control
from app.services.prompt_security import get_anti_injection_instruction
from app.services.llm_config import build_chat_params
from app.services.context_curator import (
    build_context,
    clamp_memory_blob,
    compact_memory,
    source_sid,
    is_memory_answerable,
    rehydrate_graph_context,
)

logger = logging.getLogger(__name__)


async def _empty_aiter():
    """An async iterator that yields nothing (used to skip the researcher loop on
    the memory fast-path while keeping the shared event-handling block)."""
    return
    yield  # pragma: no cover — makes this an async generator


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
    # Human-readable descriptions of skill API calls (http_request) that failed,
    # so the writer can explicitly tell the user an attempted action did not
    # succeed instead of silently glossing over it.
    failed_actions: list = field(default_factory=list)


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
    """Deduplicate sources by chunk_id, keeping highest-scored version.

    Sources without chunk_id (e.g. skill API responses) are always kept.
    """
    seen = {}
    no_id = []
    for s in sources:
        cid = s.get("chunk_id")
        if not cid:
            no_id.append(s)
            continue
        score = s.get("rerank_score", s.get("score", 0))
        existing_score = seen.get(cid, {}).get(
            "rerank_score", seen.get(cid, {}).get("score", 0)
        )
        if cid not in seen or score > existing_score:
            seen[cid] = s
    deduped = sorted(
        seen.values(),
        key=lambda x: x.get("rerank_score", x.get("score", 0)),
        reverse=True,
    )
    # Skill API sources go first (highest priority)
    return no_id + deduped


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
    allowed_collection_ids: Optional[List[str]] = None,
) -> tuple:
    """
    Execute hybrid search for each query in parallel, then deduplicate and rerank.

    Uses the existing graph_search_async (hybrid RRF: vector + fulltext + graph traversal)
    and rerank_results_async (cross-encoder) infrastructure.
    """
    queries = queries[:3]  # Cap at 3 queries

    # Batch the per-query helper calls when enabled: instead of every
    # graph_search_async extracting entities + embedding on its own (one LLM call
    # + one embedding call PER query), do ONE batched entity-extraction call and
    # ONE batched embedding call upfront, then hand each query its precomputed
    # results. Falls back to the per-query path on any failure or when disabled.
    per_query_entities: List[Optional[List[str]]] = [None] * len(queries)
    per_query_embeddings: List[Optional[List[float]]] = [None] * len(queries)
    if settings.enable_batched_query_extraction and queries:
        try:
            embed_task = asyncio.to_thread(processor.embed_queries, list(queries))
            if processor.graph_extractor.is_available:
                per_query_entities, per_query_embeddings = await asyncio.gather(
                    processor.graph_extractor.extract_entities_from_queries_async(
                        list(queries)
                    ),
                    embed_task,
                )
            else:
                per_query_embeddings = await embed_task
                per_query_entities = [[] for _ in queries]
        except Exception as e:
            logger.warning(
                f"Batched query pre-processing failed ({e}); falling back to per-query"
            )
            per_query_entities = [None] * len(queries)
            per_query_embeddings = [None] * len(queries)

    # Execute all queries in parallel
    tasks = [
        processor.graph_search_async(
            q, top_k=5, use_hybrid_rrf=True, collection_id=collection_id,
            allowed_collection_ids=allowed_collection_ids,
            precomputed_entities=per_query_entities[i],
            precomputed_embedding=per_query_embeddings[i],
        )
        for i, q in enumerate(queries)
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
    rerank_top_k = getattr(settings, "rerank_top_k", 15)
    if settings.enable_reranking and all_results:
        try:
            all_results = await processor.rerank_results_async(
                original_question, all_results, top_k=rerank_top_k
            )
        except Exception as e:
            logger.warning(f"Reranking failed, using raw scores: {e}")
            all_results = sorted(
                all_results, key=lambda x: x.get("score", 0), reverse=True
            )[:rerank_top_k]
    else:
        all_results = sorted(
            all_results, key=lambda x: x.get("score", 0), reverse=True
        )[:rerank_top_k]

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
    allowed_collection_ids: Optional[List[str]] = None,
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

    # Skill activation state (on-demand pattern)
    _skill_service = None
    skill_catalog = []                # compact name+desc for system prompt
    activated_skills = {}             # skill_id → {instructions, tool_defs, tool_map, config}
    activated_instructions = ""       # concatenated active skill instruction bodies
    activated_tool_defs = []          # merged tool defs from all activated skills
    activated_tool_map = {}           # merged {namespaced_name: (skill_id, original_name)}

    if getattr(settings, "enable_skills", False):
        try:
            from app.services.skill_service import get_skill_service
            _skill_service = get_skill_service()
            skill_catalog = _skill_service.get_skill_catalog()
            if skill_catalog:
                logger.info(
                    f"Skill catalog loaded: {len(skill_catalog)} skills available"
                )
                # Auto-activate all enabled skills so the model sees their
                # instructions and can call http_request directly
                for skill_entry in skill_catalog:
                    sid = skill_entry["skill_id"]
                    try:
                        instr, t_defs, t_map, skill_config = (
                            _skill_service.load_skill_for_activation(sid)
                        )
                        config_schema = _skill_service.get_skill_config_schema(sid) or []
                        base_url = _skill_service.get_skill_base_url(sid)
                        activated_skills[sid] = {
                            "instructions": instr,
                            "tool_defs": t_defs,
                            "tool_map": t_map,
                            "config": skill_config,
                            "config_schema": config_schema,
                            "base_url": base_url,
                        }
                    except Exception as e:
                        logger.warning(f"Failed to auto-activate skill '{sid}': {e}")
                # Build merged activation state
                activated_instructions = "\n\n".join(
                    s["instructions"]
                    for s in activated_skills.values()
                    if s["instructions"]
                )
                activated_tool_defs = [
                    td
                    for s in activated_skills.values()
                    for td in s["tool_defs"]
                ]
                activated_tool_map = {}
                for s in activated_skills.values():
                    activated_tool_map.update(s["tool_map"])
                if activated_skills:
                    logger.info(
                        f"Auto-activated {len(activated_skills)} skills: "
                        f"{list(activated_skills.keys())}"
                    )
        except Exception as e:
            logger.warning(f"Failed to load skill catalog: {e}")

    # Git integration: load the primary connection (if any) so the git_repo tool
    # is available. Writes are gated server-side on the connection's access_level.
    git_connection = None
    if getattr(settings, "enable_git_integration", False):
        try:
            from app.services.neo4j_service import get_neo4j_service as _get_neo4j
            _conns = _get_neo4j().list_git_connections()
            if _conns:
                # Prefer a read/write connection so write actions are possible.
                git_connection = next(
                    (c for c in _conns if c.get("access_level") == "read_write"),
                    _conns[0],
                )
        except Exception as e:
            logger.warning(f"Failed to load git connections: {e}")

    # Build tools list (rebuilt each iteration to include newly activated skill tools)
    tools = get_tools_with_skill_activation(
        mode,
        has_skills=bool(skill_catalog),
        activated_skill_tools=activated_tool_defs or None,
        has_git=bool(git_connection),
    )

    # Build initial messages. In stable-prompt mode (default) the system
    # prompt is iteration-free and built exactly once — byte-stable across
    # the loop, so provider prefix caches hit from iteration 2 on.
    stable_prompt = getattr(settings, "researcher_stable_prompt", True)
    system_prompt = (
        (
            get_researcher_prompt_static(mode, max_iterations)
            if stable_prompt
            else get_researcher_prompt(mode, 0, max_iterations)
        )
        + build_skill_catalog_block(skill_catalog)
        + build_activated_skills_block(activated_instructions)
        + get_anti_injection_instruction(enabled=settings.prompt_security)
    )

    messages = [{"role": "system", "content": system_prompt}]
    # conversation_history is already curated/bounded by the caller
    # (run_research_pipeline -> context_curator.build_context).
    for msg in conversation_history:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": question})

    result = ResearchResult()
    communities_used_ids = []
    _http_request_called = False

    # Wall-clock budget: on expiry we stop gathering and let the writer
    # synthesize from accumulated results (same path as iteration exhaustion).
    _wall_clock_budget = getattr(settings, "researcher_wall_clock_seconds", 0)
    _deadline = (
        time.monotonic() + _wall_clock_budget if _wall_clock_budget > 0 else None
    )

    for iteration in range(max_iterations):
        if _deadline is not None and time.monotonic() >= _deadline:
            logger.info(
                f"Researcher loop hit wall-clock budget "
                f"({_wall_clock_budget}s) at iteration {iteration + 1}; "
                f"synthesizing from gathered results"
            )
            yield {
                "type": "thinking",
                "content": "Time budget reached — synthesizing the answer from what I've gathered...",
            }
            break
        if stable_prompt:
            # The system prefix never changes; the iteration counter rides as
            # a trailing system note so the prefix stays cache-hot. The note
            # is rebuilt per call (not appended to `messages`), keeping the
            # persistent list append-only.
            call_messages = messages + [{
                "role": "system",
                "content": f"Iteration {iteration + 1} of {max_iterations}.",
            }]
        else:
            # Legacy: rebuild system prompt + tools each iteration
            messages[0]["content"] = (
                get_researcher_prompt(mode, iteration, max_iterations)
                + build_skill_catalog_block(skill_catalog)
                + build_activated_skills_block(activated_instructions)
                + get_anti_injection_instruction(enabled=settings.prompt_security)
            )
            tools = get_tools_with_skill_activation(
                mode,
                has_skills=bool(skill_catalog),
                activated_skill_tools=activated_tool_defs or None,
                has_git=bool(git_connection),
            )
            call_messages = messages

        if getattr(settings, "enable_prompt_cache_control", False):
            call_messages = apply_cache_control(
                call_messages, llm_config.base_url, llm_config.model
            )

        try:
            response = await client.chat.completions.create(
                model=llm_config.model,
                messages=call_messages,
                tools=tools,
                tool_choice="auto",
                **build_chat_params(llm_config.model, temperature=0.2),
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
            if iteration == 0 and bool(skill_catalog):
                # First iteration with skills active but model didn't call tools.
                # Try to force a tool call. tool_choice="required" works on some
                # providers but 500s on others (e.g. Venice/minimax) — when it
                # fails or yields no tool call, fall back to a corrective system
                # nudge + tool_choice="auto", which is supported everywhere.
                logger.warning(
                    "Model skipped tools on iteration 1 with skills active; forcing a tool call..."
                )
                nudge = {
                    "role": "system",
                    "content": (
                        "You replied with prose but did not call a tool. If the user's "
                        "request maps to an active skill (for example, creating a ticket), "
                        "you MUST call the appropriate tool now (e.g. http_request) using "
                        "the exact url and body described in the skill instructions. "
                        "Do not reply with prose."
                    ),
                }
                retry_msg = None
                # 1) Best-effort: tool_choice="required" (some providers support it).
                try:
                    response = await client.chat.completions.create(
                        model=llm_config.model,
                        messages=call_messages,
                        tools=tools,
                        tool_choice="required",
                        **build_chat_params(llm_config.model, temperature=0.2),
                    )
                    retry_msg = response.choices[0].message
                except Exception as e:
                    logger.warning(
                        f"tool_choice=required unsupported/failed ({e}); "
                        "falling back to nudge + tool_choice=auto"
                    )
                # 2) Fallback: corrective nudge + tool_choice="auto".
                if retry_msg is None or not retry_msg.tool_calls:
                    try:
                        response = await client.chat.completions.create(
                            model=llm_config.model,
                            messages=call_messages + [nudge],
                            tools=tools,
                            tool_choice="auto",
                            **build_chat_params(llm_config.model, temperature=0.2),
                        )
                        retry_msg = response.choices[0].message
                    except Exception as e:
                        logger.warning(f"nudge + auto retry failed: {e}")
                if retry_msg is not None:
                    assistant_message = retry_msg
            elif not assistant_message.content:
                # Empty response — retry once (model flakiness)
                logger.warning(f"Empty LLM response on iteration {iteration + 1}, retrying...")
                yield {
                    "type": "thinking",
                    "content": "Retrying...",
                }
                try:
                    response = await client.chat.completions.create(
                        model=llm_config.model,
                        messages=call_messages,
                        tools=tools,
                        tool_choice="auto",
                        **build_chat_params(llm_config.model, temperature=0.2),
                    )
                    assistant_message = response.choices[0].message
                except Exception:
                    pass

            if not assistant_message.tool_calls:
                # Model output text instead of calling tools — treat as implicit done
                logger.info(f"No tool calls on iteration {iteration + 1}, ending loop (content={bool(assistant_message.content)})")
                messages.append(assistant_message)
                if assistant_message.content:
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
                    if settings.stream_reasoning_steps:
                        yield {
                            "type": "status",
                            "status": {"stage": "searching", "message": "Searching the knowledge base"},
                        }
                    yield {
                        "type": "thinking",
                        "content": f"Searching: {', '.join(q[:50] for q in queries[:3])}",
                    }

                    sources, graph_ctx = await _execute_knowledge_search(
                        queries, question, collection_id, processor, settings,
                        allowed_collection_ids=allowed_collection_ids
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
                        # Sync neo4j driver — offload so it doesn't block the
                        # event loop and starve other in-flight requests.
                        communities = await asyncio.to_thread(
                            neo4j_service.search_communities_by_content,
                            query,
                            limit=3,
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
                        # Sync neo4j driver — offload so it doesn't block the
                        # event loop and starve other in-flight requests.
                        entities = await asyncio.to_thread(
                            neo4j_service.find_entities_by_name, names[:5]
                        )
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

            elif name == "git_repo":
                git_action = (args.get("action") or "").strip()
                if not git_connection:
                    response_text = "Error: no git repository is connected."
                else:
                    from app.services.git_providers import get_provider, GitProviderError
                    from app.services.crypto_service import get_crypto_service, CryptoError
                    owner = git_connection["repo_owner"]
                    repo = git_connection["repo_name"]
                    try:
                        git_pat = get_crypto_service().decrypt(git_connection["pat"])
                    except CryptoError:
                        git_pat = None
                    provider = git_pat and get_provider(
                        git_connection["vendor"], git_pat,
                        git_connection.get("base_url"),
                    )
                    base_branch = (
                        git_connection.get("branch")
                        or git_connection.get("default_branch")
                        or "main"
                    )
                    write_actions = {"propose_change", "comment"}
                    if not provider:
                        response_text = (
                            "Error: git credentials cannot be decrypted "
                            "(encryption key changed or removed); an admin must "
                            "re-enter the PAT for this connection."
                        )
                    elif git_action in write_actions and git_connection.get("access_level") != "read_write":
                        # Hard, server-side enforcement of the read-only toggle.
                        response_text = (
                            "Error: this repository is connected read-only; "
                            "write actions are not permitted."
                        )
                    else:
                        yield {"type": "thinking", "content": f"git_repo: {git_action} on {owner}/{repo}"}
                        try:
                            if git_action == "read_file":
                                file_path = (args.get("path") or "").strip()
                                # Server-side guard: repo-relative paths only —
                                # rejects '..' segments so an LLM-chosen path
                                # can't walk the provider API URL.
                                if not file_path or file_path.startswith("/") or ".." in file_path.split("/"):
                                    response_text = "Error: invalid path — must be a relative path inside the repository."
                                else:
                                    content = await provider.get_file_content(
                                        owner, repo, file_path, base_branch,
                                    )
                                    response_text = _truncate_response(content)
                            elif git_action == "propose_change":
                                files = [
                                    (f["path"], f.get("content", ""))
                                    for f in (args.get("files") or [])
                                    if f.get("path")
                                ]
                                if not files:
                                    response_text = "Error: no files provided for propose_change."
                                else:
                                    # Invariant: always a fresh branch + PR, never a
                                    # direct push to the default branch.
                                    new_branch = f"cortex/agent-{uuid.uuid4().hex[:8]}"
                                    await provider.create_branch(owner, repo, new_branch, base_branch)
                                    await provider.commit_files(
                                        owner, repo, new_branch, files,
                                        args.get("commit_message") or "Update via Cortex agent",
                                    )
                                    pr = await provider.open_pull_request(
                                        owner, repo, new_branch, base_branch,
                                        args.get("title") or "Cortex agent change",
                                        args.get("body") or "",
                                    )
                                    response_text = (
                                        f"Opened pull request: {pr.url} "
                                        f"(branch {new_branch} → {base_branch})"
                                    )
                            elif git_action == "comment":
                                pr_number = args.get("pr_number")
                                if pr_number is None:
                                    response_text = "Error: pr_number is required for comment."
                                else:
                                    res = await provider.comment(
                                        owner, repo, int(pr_number), args.get("body") or "",
                                    )
                                    response_text = (
                                        f"Comment added to #{pr_number}"
                                        + (f": {res.url}" if res.url else "")
                                    )
                            else:
                                response_text = f"Error: unknown git action '{git_action}'."
                        except GitProviderError as e:
                            response_text = f"Error: {str(e)[:500]}"
                        except Exception as e:
                            response_text = f"Error: {str(e)[:500]}"
                            logger.warning(f"git_repo {git_action} failed: {e}")

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": response_text,
                })

            elif name == "http_request":
                # Build merged config from all activated skills
                _merged_configs = {}
                for _s in activated_skills.values():
                    _merged_configs.update(_s.get("config", {}))

                method = args.get("method", "GET").upper()
                url = _substitute_variables(args.get("url", ""), _merged_configs)
                body = args.get("body")
                if body:
                    body = _substitute_variables(body, _merged_configs)

                # Build headers server-side from config schemas, scoped by hostname.
                # The LLM never provides headers — auth is fully automatic.
                #
                # Two skills that both set `Authorization` will overwrite each other
                # unless we constrain which skill's credentials apply to which host.
                # Hostnames are derived from whatever the skill already knows: the
                # LLM-extracted `base_url` for skills with hardcoded URLs, or any
                # URL-shaped config value (e.g. *_BASE_URL) for skills where the user
                # supplies the host. No extra UI input required.
                from urllib.parse import urlparse
                req_host = (urlparse(url).hostname or "").lower()

                def _skill_hosts(skill_data) -> set:
                    hosts = set()
                    bu = skill_data.get("base_url")
                    if bu:
                        h = (urlparse(bu).hostname or "").lower()
                        if h:
                            hosts.add(h)
                    for v in (skill_data.get("config") or {}).values():
                        if isinstance(v, str) and v.startswith(("http://", "https://")):
                            h = (urlparse(v).hostname or "").lower()
                            if h:
                                hosts.add(h)
                    return hosts

                # Prefer skills whose known hostnames include the request host.
                # Fall back to skills with no hostname info so freshly installed skills
                # (LLM analyzer didn't extract a URL, no URL config var) still work.
                matching_skills = [
                    s for s in activated_skills.values() if req_host in _skill_hosts(s)
                ]
                if matching_skills:
                    auth_sources = matching_skills
                else:
                    auth_sources = [
                        s for s in activated_skills.values() if not _skill_hosts(s)
                    ]
                    if len(auth_sources) > 1:
                        logger.warning(
                            "http_request: %d activated skills lack any URL hint "
                            "(no base_url, no URL config value) — auth headers may "
                            "collide on shared header names (e.g. Authorization) "
                            "when sent to %s.",
                            len(auth_sources), req_host,
                        )

                headers = {}
                for skill_data in auth_sources:
                    for var_def in (skill_data.get("config_schema") or []):
                        auth_tmpl = var_def.get("auth_header", "")
                        var_name = var_def.get("name", "")
                        config = skill_data.get("config", {})
                        if auth_tmpl and var_name and var_name in config and ": " in auth_tmpl:
                            hdr_name, hdr_val = auth_tmpl.split(": ", 1)
                            headers[hdr_name] = hdr_val.replace(var_name, config[var_name])

                # Rails-backed APIs (e.g. Zammad) reject untyped bodies with 422.
                if body and "Content-Type" not in headers:
                    headers["Content-Type"] = "application/json"

                _has_auth = any(h.lower() == "authorization" for h in headers)
                logger.info(
                    f"http_request: {method} {url} | "
                    f"auth={'yes' if _has_auth else 'none'} | "
                    f"ct={headers.get('Content-Type', 'none')} | "
                    f"body_len={len(body) if body else 0}"
                )

                # Surface the common "model guessed the wrong host" failure: no auth
                # was attached even though an active skill knows a real host. Usually
                # means the model wrote a literal/hallucinated URL instead of the
                # ${BASE_URL} placeholder, so the request host matched no skill.
                if not _has_auth:
                    _known_hosts = sorted(
                        {h for s in activated_skills.values() for h in _skill_hosts(s)}
                    )
                    if _known_hosts:
                        logger.warning(
                            "http_request: auth=none for host '%s' but active skills "
                            "have configured host(s) %s — the model likely wrote a "
                            "literal/guessed URL instead of the ${BASE_URL} placeholder. "
                            "No credentials were attached.",
                            req_host, _known_hosts,
                        )

                yield {
                    "type": "thinking",
                    "content": f"Calling {method} {url}",
                }

                # TLS verification is on by default. A self-hosted skill API with a
                # self-signed cert can be allowlisted per-host via SKILL_HTTP_INSECURE_HOSTS.
                _insecure_hosts = {
                    h.strip().lower()
                    for h in (getattr(settings, "skill_http_insecure_hosts", "") or "").split(",")
                    if h.strip()
                }
                _verify_tls = req_host not in _insecure_hosts
                if not _verify_tls:
                    logger.warning(
                        "http_request: TLS verification DISABLED for host '%s' "
                        "(listed in SKILL_HTTP_INSECURE_HOSTS).", req_host,
                    )

                # Short, user-facing failure label (set in the except handlers).
                # None means the call succeeded.
                _req_error_label = None
                _http_timeout = getattr(settings, "skill_http_timeout", 15)
                try:
                    async with httpx.AsyncClient(
                        timeout=_http_timeout,
                        follow_redirects=True,
                        verify=_verify_tls,
                    ) as http_client:
                        resp = await http_client.request(
                            method,
                            url,
                            headers=headers,
                            content=body.encode() if body else None,
                        )
                        resp.raise_for_status()
                        response_text = _truncate_response(resp.text)
                except httpx.TimeoutException:
                    response_text = (
                        f"Error: HTTP request timed out ({_http_timeout}s)"
                    )
                    _req_error_label = (
                        f"API call timed out: {method} {url} ({_http_timeout}s)"
                    )
                    logger.warning(f"http_request timed out: {method} {url}")
                except httpx.HTTPStatusError as e:
                    response_text = (
                        f"Error: HTTP {e.response.status_code} — "
                        f"{e.response.text[:500]}"
                    )
                    _req_error_label = (
                        f"API call failed: {method} {url} → HTTP {e.response.status_code}"
                    )
                    logger.warning(
                        f"http_request failed: {method} {url} → "
                        f"{e.response.status_code} | "
                        f"req_body={(body or '')[:300]} | "
                        f"resp_body={e.response.text[:300]}"
                    )
                except Exception as e:
                    response_text = f"Error: {str(e)[:500]}"
                    _req_error_label = f"API call failed: {method} {url}"
                    logger.warning(f"http_request exception: {method} {url} → {e}")

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": response_text,
                })

                _http_request_called = True

                # Surface a failed call to the user in real time, mirroring the
                # skill_tool channel used for successful tool activity.
                if _req_error_label:
                    yield {
                        "type": "skill_tool",
                        "content": _req_error_label,
                        "skill_name": "http_request",
                        "is_error": True,
                    }

                # Store API response as a source so the writer has context.
                # Use top-level keys matching the format the writer expects.
                if not response_text.startswith("Error:"):
                    result.sources.append({
                        "content": response_text,
                        "score": 1.0,
                        "filename": f"Skill API: {url}",
                        "metadata": {
                            "filename": f"Skill API: {url}",
                            "source_type": "skill_api",
                        },
                    })
                else:
                    # Record the failure so the writer cannot narrate a failed
                    # action (e.g. a ticket POST) as if it succeeded. score=0 so
                    # it never outranks real evidence.
                    result.sources.append({
                        "content": (
                            f"NOTE: This skill API call FAILED and returned no "
                            f"data. {response_text}"
                        ),
                        "score": 0.0,
                        "filename": f"Skill API (FAILED): {url}",
                        "metadata": {
                            "filename": f"Skill API (FAILED): {url}",
                            "source_type": "skill_api_error",
                        },
                    })
                    # Surfaced to the writer as an explicit instruction (not just
                    # a low-score source the model may ignore or deflect on).
                    result.failed_actions.append(
                        f"{method} {url} — {response_text}"
                    )

            elif name == "activate_skill" and _skill_service:
                skill_name = args.get("name", "")
                if skill_name in activated_skills:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": f"Skill '{skill_name}' is already active.",
                    })
                else:
                    try:
                        instr, t_defs, t_map, skill_config = (
                            _skill_service.load_skill_for_activation(skill_name)
                        )
                        config_schema = _skill_service.get_skill_config_schema(skill_name) or []
                        base_url = _skill_service.get_skill_base_url(skill_name)
                        activated_skills[skill_name] = {
                            "instructions": instr,
                            "tool_defs": t_defs,
                            "tool_map": t_map,
                            "config": skill_config,
                            "config_schema": config_schema,
                            "base_url": base_url,
                        }
                        # Rebuild merged activation state
                        activated_instructions = "\n\n".join(
                            s["instructions"]
                            for s in activated_skills.values()
                            if s["instructions"]
                        )
                        activated_tool_defs = [
                            td
                            for s in activated_skills.values()
                            for td in s["tool_defs"]
                        ]
                        activated_tool_map = {}
                        for s in activated_skills.values():
                            activated_tool_map.update(s["tool_map"])

                        yield {
                            "type": "skill_tool",
                            "content": f"Activating skill: {skill_name}",
                            "skill_name": skill_name,
                        }

                        tool_names_str = (
                            ", ".join(
                                t["function"]["name"] for t in t_defs
                            )
                            or "none (instruction-only)"
                        )
                        auth_note = ""
                        if skill_config:
                            auth_note = (
                                " Authentication is pre-configured — "
                                "API tokens and credentials are automatically "
                                "injected into http_request calls. Just call "
                                "the API endpoints described in the skill "
                                "instructions directly, no need to provide "
                                "tokens or API keys yourself."
                            )
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": (
                                f"Activated skill '{skill_name}'. "
                                f"Tools now available: {tool_names_str}. "
                                f"Instructions loaded into context."
                                f"{auth_note}"
                            ),
                        })
                    except Exception as e:
                        logger.warning(f"Skill activation failed: {e}")
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": f"Failed to activate skill '{skill_name}': {str(e)}",
                        })

            elif name == "list_skills":
                catalog_text = "\n".join(
                    f"- {s['name']}: {s['description']}"
                    f" [type: {s['skill_type']}]"
                    + (" (ACTIVE)" if s["skill_id"] in activated_skills else "")
                    for s in skill_catalog
                ) if skill_catalog else "No skills available."
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": f"Available skills:\n{catalog_text}",
                })

            elif name in activated_tool_map and _skill_service:
                # Activated skill tool call
                skill_id, original_name = activated_tool_map[name]
                yield {
                    "type": "skill_tool",
                    "content": f"Running skill tool: {original_name}",
                    "skill_name": skill_id,
                }
                try:
                    tool_result = await _skill_service.execute_skill_tool(
                        skill_id, original_name, args
                    )
                except Exception as e:
                    logger.warning(f"Skill tool {name} failed: {e}")
                    tool_result = f"Error: {str(e)}"

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_result[:4000],
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
    allowed_collection_ids: Optional[List[str]] = None,
    processor=None,
    neo4j_service=None,
    llm_config=None,
    settings=None,
    conversation_memory: Optional[dict] = None,
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
    # Bound the client-carried memory blob before anything trusts it (a buggy
    # or malicious client can inflate it without limit).
    conversation_memory = clamp_memory_blob(conversation_memory, settings)
    # Curate a bounded context from the client-carried memory blob (if any). When
    # no blob is present this returns the legacy [-max_conversation_history:] slice,
    # so downstream behavior is unchanged.
    curated_history = build_context(conversation_history, conversation_memory, settings)

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
    if settings.stream_reasoning_steps:
        yield {"status": {"stage": "analyzing", "message": "Analyzing your question"}}

    # =========================================================================
    # Phase 1: Researcher Agent Loop (or memory fast-path)
    # =========================================================================

    research_result = ResearchResult()
    communities_used = []

    # Fast-path: a follow-up answerable from memory alone (e.g. "summarize that",
    # "why?", "in German") skips the researcher loop and its retrieval entirely.
    # Graph grounding is rehydrated from the blob's stored kg_context.
    fast_path = conversation_memory is not None and await is_memory_answerable(
        question, conversation_memory, settings
    )

    if fast_path:
        yield {"thinking": "Answering from conversation memory (no new retrieval)."}
        _rg = rehydrate_graph_context(conversation_memory)
        research_result.graph_context = _rg
        research_result.communities = _rg.get("communities", [])

    async for event in _run_researcher_loop(
        question=question,
        mode=mode,
        conversation_history=curated_history,
        collection_id=collection_id,
        allowed_collection_ids=allowed_collection_ids,
        processor=processor,
        neo4j_service=neo4j_service,
        client=client,
        llm_config=llm_config,
        settings=settings,
    ) if not fast_path else _empty_aiter():
        if event["type"] == "result":
            research_result = event["data"]
            communities_used = event.get("communities_used", [])
        elif event["type"] == "thinking":
            yield {"thinking": event["content"]}
        elif event["type"] == "retrieval":
            yield {"retrieval": event["content"]}
        elif event["type"] == "status":
            yield {"status": event["status"]}
        elif event["type"] == "skill_tool":
            yield {
                "skill_tool": event["content"],
                "skill_name": event.get("skill_name", ""),
                "is_error": event.get("is_error", False),
            }

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

    # Emit sources to frontend. Each carries a conversation-stable `sid` so
    # citations keep identity across turns (additive field; existing frontends
    # ignore it). Per-turn [src_N] numbering for the writer is unchanged.
    source_events = [
        {
            "document_id": r.get("document_id", ""),
            "chunk_id": r.get("chunk_id", ""),
            "content": r.get("content", ""),
            "score": r.get("rerank_score", r.get("score", 0)),
            "metadata": {"filename": r.get("filename", "")},
            "sid": source_sid(r),
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

    if settings.stream_reasoning_steps:
        yield {"status": {"stage": "generating", "message": "Writing the answer"}}

    anti_injection = get_anti_injection_instruction(enabled=settings.prompt_security)
    writer_system = get_writer_system_prompt(mode, anti_injection)

    has_history = len(curated_history) > 0
    writer_user = get_writer_user_prompt(
        mode=mode,
        formatted_sources=formatted_sources,
        graph_context_str=graph_context_str,
        question=question,
        researcher_summary=research_result.summary,
        has_history=has_history,
        failed_actions=research_result.failed_actions,
    )

    writer_messages = [{"role": "system", "content": writer_system}]

    # Include the curated conversation context for multi-turn continuity.
    for msg in curated_history:
        writer_messages.append({"role": msg.role, "content": msg.content})

    writer_messages.append({"role": "user", "content": writer_user})

    try:
        stream = await client.chat.completions.create(
            model=llm_config.model,
            messages=writer_messages,
            stream=True,
            **build_chat_params(
                llm_config.model, temperature=0.3, max_tokens=writer_max_tokens
            ),
        )

        answer_parts: List[str] = []
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                token = chunk.choices[0].delta.content
                answer_parts.append(token)
                yield {"content": token}

    except Exception as e:
        logger.error(f"Writer streaming error: {e}")
        answer_parts = []
        yield {
            "content": "I encountered an error generating the response. Please try again."
        }

    # Update the client-carried memory blob AFTER streaming (zero added latency on
    # the answer path). Only when the client opted in by sending a blob.
    if conversation_memory is not None and getattr(
        settings, "enable_conversation_memory", True
    ):
        try:
            updated_memory = await compact_memory(
                memory=conversation_memory,
                conversation_history=conversation_history,
                question=question,
                answer="".join(answer_parts),
                settings=settings,
                sources=source_events,
                kg_context={
                    "entities": research_result.graph_context.get("entities", []),
                    "communities": research_result.communities,
                },
            )
            yield {"memory_update": updated_memory}
        except Exception as e:  # noqa: BLE001 — never break the turn on memory work
            logger.warning(f"memory_update emission skipped: {e}")

    yield {"done": True, "communities_used": list(set(communities_used))}


# =============================================================================
# HTTP Request Helper
# =============================================================================


def _truncate_response(text: str, max_chars: int = 32000) -> str:
    """Truncate an HTTP response intelligently.

    For JSON responses with arrays, slim down each item by truncating long
    string values (descriptions etc.) to keep ALL items within budget.
    Falls back to keeping complete items, then plain truncation.
    """
    if len(text) <= max_chars:
        return text

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text[:max_chars]

    # Find the array to slim down
    items = None
    array_key = None
    if isinstance(data, dict):
        for key in ("data", "results", "items", "entries"):
            if key in data and isinstance(data[key], list):
                items = data[key]
                array_key = key
                break
    elif isinstance(data, list):
        items = data

    if items is None:
        return text[:max_chars]

    # Strategy 1: Progressively slim items to fit ALL within budget
    # Start aggressive: short strings, flatten nested objects, drop large arrays
    for max_str_len in (80, 40):
        slimmed = []
        for item in items:
            if isinstance(item, dict):
                slim = {}
                for k, v in item.items():
                    if isinstance(v, str) and len(v) > max_str_len:
                        slim[k] = v[:max_str_len] + "..."
                    elif isinstance(v, dict):
                        # Flatten nested objects to just key fields
                        if "name" in v:
                            slim[k] = v.get("name")
                        elif "type" in v:
                            slim[k] = v
                        else:
                            slim[k] = str(v)[:max_str_len]
                    elif isinstance(v, list):
                        if len(v) == 0:
                            continue  # drop empty arrays
                        # Keep only names/ids from list items
                        compact = []
                        for li in v[:5]:
                            if isinstance(li, dict) and "name" in li:
                                compact.append(li["name"])
                            elif isinstance(li, dict) and "id" in li:
                                compact.append(li["id"])
                            else:
                                compact.append(li)
                        slim[k] = compact
                    else:
                        slim[k] = v
                slimmed.append(slim)
            else:
                slimmed.append(item)

        if array_key:
            data[array_key] = slimmed
            test_result = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        else:
            test_result = json.dumps(slimmed, ensure_ascii=False, separators=(",", ":"))

        if len(test_result) <= max_chars:
            return test_result

    if array_key:
        data[array_key] = slimmed
        result = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    else:
        result = json.dumps(slimmed, ensure_ascii=False, separators=(",", ":"))

    if len(result) <= max_chars:
        return result

    # Strategy 2: Keep complete original items that fit
    kept = []
    budget = max_chars - 200
    for item in items:
        item_str = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        if budget - len(item_str) < 0 and kept:
            break
        budget -= len(item_str)
        kept.append(item)

    if array_key:
        data[array_key] = kept
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(kept, ensure_ascii=False, separators=(",", ":"))[:max_chars]


def _substitute_variables(value: str, skill_configs: dict = None) -> str:
    """Substitute variable placeholders in a string.

    Handles two patterns:
    1. ${VARIABLE_NAME} — explicit placeholder syntax
    2. Bare VARIABLE_NAME — when the LLM writes the variable name literally
       (e.g. "Bearer API_TOKEN" instead of "Bearer ${API_TOKEN}")

    Resolution order for each variable:
    1. Skill config values (from config.json, set via the setup wizard)
    2. SKILL_* environment variables (backward compatibility)
    """
    configs = skill_configs or {}
    replaced = set()

    # Pass 1: Replace ${VAR} patterns
    def _placeholder_replacer(match):
        var_name = match.group(1)
        if var_name in configs:
            replaced.add(var_name)
            return configs[var_name]
        if var_name.startswith("SKILL_"):
            return os.environ.get(var_name, "")
        return match.group(0)

    result = re.sub(r"\$\{([A-Z_][A-Z0-9_]*)\}", _placeholder_replacer, value)

    # Pass 2: Replace bare config key names that appear as standalone words
    # (e.g. the model wrote `ZAMMAD_GROUP_NAME` instead of `${ZAMMAD_GROUP_NAME}`).
    # Only for all-uppercase keys (avoids false positives) and only for keys NOT
    # already handled in pass 1. We must NOT skip a key just because its value
    # coincidentally appears elsewhere in the text — that would leave the bare
    # placeholder un-substituted (e.g. group value "Users" appearing in the body).
    for key, val in configs.items():
        if key in replaced:
            continue
        if key == key.upper() and len(key) >= 3 and re.search(r"\b" + re.escape(key) + r"\b", result):
            result = re.sub(r"\b" + re.escape(key) + r"\b", val, result)

    return result
