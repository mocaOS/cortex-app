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
from openai import AsyncOpenAI  # used in type annotations; clients built via factory

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
from app.services.reasoning_config import (
    apply_cache_control,
    safe_chat_completion,
    ReasoningMode,
)


def _chat_reasoning_mode(mode: str, settings) -> ReasoningMode:
    """Reasoning level for the chat/answer LLM calls.

    Speed/chat → DEFAULT_REASONING_MODE (default OFF → Venice disable_thinking,
    snappy first token). Deep-research (quality) → AUTO (provider default;
    hidden reasoning preserved). See config.default_reasoning_mode.
    """
    if mode == "speed":
        return ReasoningMode.parse(getattr(settings, "default_reasoning_mode", "off"))
    return ReasoningMode.AUTO
from app.services.prompt_security import (
    get_anti_injection_instruction,
    get_safe_refusal_message,
    validate_and_process_input,
    wrap_untrusted,
)
from app.services.llm_config import build_chat_params, make_async_openai_client, stream_usage_kwargs
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


def _needs_grounding_guard(
    fast_path: bool, result: ResearchResult, settings
) -> bool:
    """True when the pipeline should force one raw-question knowledge_search.

    Fires only when the loop performed ZERO searches and gathered ZERO sources
    (skill API responses land in `sources`, so a skill-answered question never
    triggers it). Searched-but-empty runs already had their retrieval chance.
    The memory fast-path intentionally answers without retrieval — exempt.
    """
    return (
        getattr(settings, "researcher_force_grounding", True)
        and not fast_path
        and result.search_count == 0
        and not result.sources
    )


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
    hint_entities: Optional[List[str]] = None,
) -> tuple:
    """
    Execute hybrid search for each query in parallel, then deduplicate and rerank.

    Uses the existing graph_search_async (hybrid RRF: vector + fulltext + graph traversal)
    and rerank_results_async (cross-encoder) infrastructure.

    ``hint_entities`` are entity names the researcher supplied on the tool call
    itself. When present the query-side entity-extraction LLM call is skipped
    entirely — the researcher just wrote the queries, so its own entity list is
    at least as good and costs zero extra latency.
    """
    queries = queries[:3]  # Cap at 3 queries

    # Batch the per-query helper calls when enabled: instead of every
    # graph_search_async extracting entities + embedding on its own (one LLM call
    # + one embedding call PER query), do ONE batched entity-extraction call and
    # ONE batched embedding call upfront, then hand each query its precomputed
    # results. Falls back to the per-query path on any failure or when disabled.
    per_query_entities: List[Optional[List[str]]] = [None] * len(queries)
    per_query_embeddings: List[Optional[List[float]]] = [None] * len(queries)
    _hints = [
        e.strip() for e in (hint_entities or []) if isinstance(e, str) and e.strip()
    ][:10]
    if settings.enable_batched_query_extraction and queries:
        try:
            embed_task = asyncio.to_thread(processor.embed_queries, list(queries))
            if _hints and getattr(settings, "researcher_tool_entity_hints", True):
                # Researcher-provided entities: no extraction round-trip needed.
                per_query_embeddings = await embed_task
                per_query_entities = [list(_hints) for _ in queries]
            elif processor.graph_extractor.is_available:
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


def _load_skill_state(settings) -> tuple:
    """Load the skill catalog + auto-activated skill state (sync, cached).

    Runs Neo4j queries / SKILL.md reads / secret decryption on cache misses, so
    callers must offload it with asyncio.to_thread — a bare call would pin the
    event loop and starve other in-flight requests (event-loop invariant).

    Returns (skill_service, skill_catalog, activated_skills).
    """
    if not getattr(settings, "enable_skills", False):
        return None, [], {}

    from app.services.skill_service import get_skill_service

    _skill_service = get_skill_service()
    skill_catalog = _skill_service.get_skill_catalog()
    activated_skills = {}
    if skill_catalog:
        logger.info(f"Skill catalog loaded: {len(skill_catalog)} skills available")
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
        if activated_skills:
            logger.info(
                f"Auto-activated {len(activated_skills)} skills: "
                f"{list(activated_skills.keys())}"
            )
    return _skill_service, skill_catalog, activated_skills


def _merge_activation_state(activated_skills: dict) -> tuple:
    """Merge per-skill activation payloads into (instructions, tool_defs, tool_map)."""
    activated_instructions = "\n\n".join(
        s["instructions"] for s in activated_skills.values() if s["instructions"]
    )
    activated_tool_defs = [
        td for s in activated_skills.values() for td in s["tool_defs"]
    ]
    activated_tool_map = {}
    for s in activated_skills.values():
        activated_tool_map.update(s["tool_map"])
    return activated_instructions, activated_tool_defs, activated_tool_map


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

    # Skill activation state (on-demand pattern). Loaded off the event loop —
    # cache misses hit Neo4j + the filesystem + Fernet decryption.
    _skill_service = None
    skill_catalog = []                # compact name+desc for system prompt
    activated_skills = {}             # skill_id → {instructions, tool_defs, tool_map, config}
    activated_instructions = ""       # concatenated active skill instruction bodies
    activated_tool_defs = []          # merged tool defs from all activated skills
    activated_tool_map = {}           # merged {namespaced_name: (skill_id, original_name)}

    try:
        _skill_service, skill_catalog, activated_skills = await asyncio.to_thread(
            _load_skill_state, settings
        )
        activated_instructions, activated_tool_defs, activated_tool_map = (
            _merge_activation_state(activated_skills)
        )
    except Exception as e:
        logger.warning(f"Failed to load skill catalog: {e}")

    # Git integration: load the primary connection (if any) so the git_repo tool
    # is available. Writes are gated server-side on the connection's access_level.
    git_connection = None
    if getattr(settings, "enable_git_integration", False):
        try:
            from app.services.neo4j_service import get_neo4j_service as _get_neo4j
            # Sync neo4j driver — offload so it doesn't block the event loop.
            _conns = await asyncio.to_thread(_get_neo4j().list_git_connections)
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
    _has_skills = bool(skill_catalog)
    # MAX_SKILL_INSTRUCTIONS_TOKENS budget, approximated at ~4 chars/token.
    _skill_budget_chars = (
        max(0, getattr(settings, "max_skill_instructions_tokens", 4000)) * 4 or None
    )
    system_prompt = (
        (
            get_researcher_prompt_static(mode, max_iterations, has_skills=_has_skills)
            if stable_prompt
            else get_researcher_prompt(mode, 0, max_iterations, has_skills=_has_skills)
        )
        + build_skill_catalog_block(skill_catalog)
        + build_activated_skills_block(activated_instructions, max_chars=_skill_budget_chars)
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
    # True once any side-effecting / researcher-only tool ran (http_request,
    # git_repo, legacy skill tools). Their outputs live in `messages`, which
    # the writer never sees — so speed early-write must not fire after them.
    _side_effect_called = False
    # Per-run dedup of identical knowledge_search calls: normalized queries →
    # formatted tool text. A repeat returns instantly with a nudge to try a
    # different angle instead of paying the full retrieval pipeline again.
    _search_cache: dict = {}

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
                get_researcher_prompt(
                    mode, iteration, max_iterations, has_skills=bool(skill_catalog)
                )
                + build_skill_catalog_block(skill_catalog)
                + build_activated_skills_block(
                    activated_instructions, max_chars=_skill_budget_chars
                )
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
            response = await safe_chat_completion(
                client.chat.completions.create,
                base_url=llm_config.base_url,
                model=llm_config.model,
                reasoning_mode=_chat_reasoning_mode(mode, settings),
                overrides=settings.parsed_reasoning_overrides,
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

        # Precompute read-only tool calls concurrently. knowledge_search /
        # community_search / entity_lookup are independent reads — running
        # them serially costs (N-1) full retrieval pipelines of wall-clock per
        # iteration, and quality mode is explicitly prompted to issue several
        # searches per turn. Side-effecting tools (http_request, git_repo,
        # skill tools) keep their sequential execution below; message order
        # and per-tool_call_id replies are preserved either way.
        _precomputed: dict = {}
        if getattr(settings, "researcher_parallel_tool_calls", True):
            _ro_specs = []
            for _tc in sorted_calls:
                if _tc.function.name not in (
                    "knowledge_search", "community_search", "entity_lookup"
                ):
                    continue
                try:
                    _tc_args = json.loads(_tc.function.arguments)
                except json.JSONDecodeError:
                    continue
                _ro_specs.append((_tc, _tc_args))

            if len(_ro_specs) >= 2:

                async def _exec_read_only(name: str, tc_args: dict):
                    if name == "knowledge_search":
                        queries = [q for q in (tc_args.get("queries") or []) if q][:3]
                        if not queries:
                            return None
                        _key = tuple(sorted(q.strip().lower() for q in queries))
                        if _key in _search_cache:
                            return None  # branch below serves the cached text
                        return await _execute_knowledge_search(
                            queries, question, collection_id, processor, settings,
                            allowed_collection_ids=allowed_collection_ids,
                            hint_entities=tc_args.get("entities"),
                        )
                    if name == "community_search":
                        query = tc_args.get("query", "")
                        if not (query and neo4j_service):
                            return None
                        return await asyncio.to_thread(
                            neo4j_service.search_communities_by_content,
                            query, limit=3,
                        )
                    names = tc_args.get("names", [])
                    if not (names and neo4j_service):
                        return None
                    return await asyncio.to_thread(
                        neo4j_service.find_entities_by_name, names[:5]
                    )

                yield {
                    "type": "thinking",
                    "content": f"Running {len(_ro_specs)} lookups in parallel...",
                }
                _ro_results = await asyncio.gather(
                    *(_exec_read_only(t.function.name, a) for t, a in _ro_specs),
                    return_exceptions=True,
                )
                for (_tc, _a), _res in zip(_ro_specs, _ro_results):
                    if isinstance(_res, Exception):
                        logger.warning(
                            f"Parallel {_tc.function.name} failed: {_res}"
                        )
                    elif _res is not None:
                        _precomputed[_tc.id] = _res

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
                queries = [q for q in (args.get("queries") or []) if q][:3]
                if queries:
                    _dedup_key = tuple(sorted(q.strip().lower() for q in queries))
                    _cached_text = (
                        _search_cache.get(_dedup_key)
                        if getattr(settings, "researcher_search_dedup", True)
                        else None
                    )
                    if _cached_text is not None:
                        # Exact repeat within this run — skip the retrieval
                        # pipeline and steer the model toward new ground.
                        logger.info(
                            f"knowledge_search dedup hit: {list(queries)}"
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "content": (
                                    "You already searched these exact queries in "
                                    "this session — the same results are repeated "
                                    "below. Try a DIFFERENT angle, different "
                                    "keywords, or another tool instead.\n\n"
                                    + _cached_text
                                ),
                            }
                        )
                        continue

                    if settings.stream_reasoning_steps:
                        yield {
                            "type": "status",
                            "status": {"stage": "searching", "message": "Searching the knowledge base"},
                        }
                    yield {
                        "type": "thinking",
                        "content": f"Searching: {', '.join(q[:50] for q in queries[:3])}",
                    }

                    if tool_call.id in _precomputed:
                        sources, graph_ctx = _precomputed[tool_call.id]
                    else:
                        sources, graph_ctx = await _execute_knowledge_search(
                            queries, question, collection_id, processor, settings,
                            allowed_collection_ids=allowed_collection_ids,
                            hint_entities=args.get("entities"),
                        )

                    result.sources.extend(sources)
                    result.total_sources_considered += len(sources)
                    result.search_count += 1
                    _merge_graph_context(result.graph_context, graph_ctx)

                    yield {
                        "type": "retrieval",
                        "content": f"Found {len(sources)} sources",
                    }

                    agent_text = wrap_untrusted(
                        _format_search_results_for_agent(sources, graph_ctx),
                        source="knowledge base search",
                        enabled=settings.prompt_security,
                    )
                    _search_cache[_dedup_key] = agent_text
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
                        if tool_call.id in _precomputed:
                            communities = _precomputed[tool_call.id]
                        else:
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
                        if tool_call.id in _precomputed:
                            entities = _precomputed[tool_call.id]
                        else:
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
                # Output lives only in `messages` (researcher context), so the
                # speed early-write shortcut must not fire after this tool.
                _side_effect_called = True
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

                # SSRF guard: the URL is LLM-chosen (steerable via prompt
                # injection in ingested content), so validate the target — and
                # every redirect hop — before connecting. Hosts an operator has
                # explicitly allowlisted (or marked TLS-insecure, i.e. already
                # trusted) bypass the check; private targets are blocked unless
                # SKILL_HTTP_ALLOW_PRIVATE is set.
                from app.services.ssrf_guard import async_request_hook, SSRFError
                _ssrf_allowlist = _insecure_hosts | {
                    h.strip().lower()
                    for h in (getattr(settings, "skill_http_allowed_hosts", "") or "").split(",")
                    if h.strip()
                }
                _ssrf_hook = async_request_hook(
                    allow_private=getattr(settings, "skill_http_allow_private", False),
                    allowlist=_ssrf_allowlist,
                )
                try:
                    async with httpx.AsyncClient(
                        timeout=_http_timeout,
                        follow_redirects=True,
                        verify=_verify_tls,
                        event_hooks={"request": [_ssrf_hook]},
                    ) as http_client:
                        resp = await http_client.request(
                            method,
                            url,
                            headers=headers,
                            content=body.encode() if body else None,
                        )
                        resp.raise_for_status()
                        response_text = _truncate_response(resp.text)
                except SSRFError as e:
                    response_text = f"Error: request blocked ({e})"
                    _req_error_label = f"API call blocked (SSRF guard): {method} {url}"
                    logger.warning(f"http_request blocked by SSRF guard: {method} {url} → {e}")
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
                    # External HTTP response — fence it as untrusted data so an
                    # injected page/API body can't steer the agent. The copy
                    # stored as a writer source (below) is fenced later by
                    # get_writer_user_prompt.
                    "content": wrap_untrusted(
                        response_text,
                        source=f"HTTP {method} {url}",
                        enabled=settings.prompt_security,
                    ),
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
                _side_effect_called = True
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
                        # External skill/API output — fence as untrusted data.
                        "content": wrap_untrusted(
                            tool_result[:4000],
                            source=f"skill {original_name}",
                            enabled=settings.prompt_security,
                        ),
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

        # Speed early-write: once a search iteration produced sources (and no
        # side-effecting tool ran, whose researcher-only output the writer
        # would lose), asking the model "are you done?" costs one more full
        # LLM round-trip whose `done` summary the speed writer prompt never
        # reads — break straight to the writer instead.
        if (
            mode == "speed"
            and getattr(settings, "researcher_speed_early_write", True)
            and result.sources
            and not _http_request_called
            and not _side_effect_called
        ):
            logger.info(
                "Speed early-write: %d sources after iteration %d; skipping "
                "the researcher confirmation round-trip",
                len(result.sources), iteration + 1,
            )
            break

    # Yield final result
    yield {"type": "result", "data": result, "communities_used": communities_used_ids}


# =============================================================================
# Citation marker hygiene (stream-safe)
# =============================================================================

# A complete citation marker, optionally preceded by one space (the space is
# dropped along with the marker, mirroring the frontend's defensive strip).
_CITE_MARKER_RE = re.compile(r"\s?\[src_(\d+)\]")

# A trailing fragment that could still grow into a [src_N] marker. While the
# buffer ends in one of these we must hold it back, because the next token may
# complete (or invalidate) the marker. Trailing whitespace is also held so the
# optional space that precedes a stripped marker is removed with it, even when
# the space and the "[" land in different tokens.
_CITE_PARTIAL_RE = re.compile(r"\s*\[(?:s(?:r(?:c(?:_\d*)?)?)?)?$|\s+$")


class _CitationStripper:
    """Stream-safe filter enforcing the [src_N] ↔ sources invariant.

    Every [src_N] that survives in the answer text is guaranteed to map to an
    emitted source (1-based). Markers with N < 1 or N > ``max_index`` — which
    includes the no-sources case (``max_index == 0``) — are stripped together
    with one optional leading space. This stops the writer LLM from rendering
    orphaned/hallucinated citations as literal `[src_1]` text when retrieval
    returned nothing or the model over-numbers.

    Tokens are buffered only across a potential partial marker, so latency is
    unaffected for ordinary text.
    """

    def __init__(self, max_index: int):
        self.max_index = max(0, max_index)
        self._buf = ""

    def _resolve(self, match: "re.Match[str]") -> str:
        n = int(match.group(1))
        return match.group(0) if 1 <= n <= self.max_index else ""

    def feed(self, token: str) -> str:
        self._buf += token
        # Hold back a trailing fragment that might still become a marker.
        partial = _CITE_PARTIAL_RE.search(self._buf)
        if partial:
            head, self._buf = self._buf[: partial.start()], self._buf[partial.start() :]
        else:
            head, self._buf = self._buf, ""
        return _CITE_MARKER_RE.sub(self._resolve, head)

    def flush(self) -> str:
        out = _CITE_MARKER_RE.sub(self._resolve, self._buf)
        self._buf = ""
        return out


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

    # Input gate: block prompt-injection attempts before any retrieval or LLM
    # work. agent_rag_stream delegates straight here without its own check, so
    # this is the only guard on that path (other entry points validate too —
    # a redundant block simply returns the refusal before doing work).
    _security_enabled = bool(getattr(settings, "prompt_security", True))
    _processed_question, _was_blocked, _reason = validate_and_process_input(
        question, strict_mode=True, enabled=_security_enabled
    )
    if _was_blocked:
        logger.warning(f"Blocked potential prompt injection in research pipeline: {_reason}")
        yield {"content": get_safe_refusal_message()}
        yield {"done": True}
        return
    question = _processed_question

    # Query-time prompt-guard classifier (shared cortex-helper): a second,
    # model-based gate after the regex heuristic. Fail-open + URL/toggle-gated.
    from app.services.prompt_guard_client import guard_user_question

    _guard_blocked, _guard_reason = await guard_user_question(
        question, settings, neo4j_service
    )
    if _guard_blocked:
        logger.warning(f"Prompt-guard blocked question in research pipeline: {_guard_reason}")
        yield {"content": get_safe_refusal_message()}
        yield {"done": True}
        return

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

    client = make_async_openai_client(
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

    # Grounding guard: the model can (stochastically) end the loop without ever
    # searching — explicit `done` on iteration 1, or prose instead of tool calls
    # — which yields an ungrounded zero-source answer on a knowledge-base
    # product. Run one knowledge_search with the raw question before writing.
    if _needs_grounding_guard(fast_path, research_result, settings):
        logger.info(
            "Grounding guard: loop ended with 0 searches — "
            "searching the raw question before the writer"
        )
        if settings.stream_reasoning_steps:
            yield {"status": {"stage": "searching", "message": "Searching the knowledge base"}}
        try:
            guard_sources, guard_ctx = await _execute_knowledge_search(
                [question], question, collection_id, processor, settings,
                allowed_collection_ids=allowed_collection_ids,
            )
            research_result.sources.extend(guard_sources)
            research_result.total_sources_considered += len(guard_sources)
            research_result.search_count += 1
            _merge_graph_context(research_result.graph_context, guard_ctx)
            yield {"retrieval": f"Found {len(guard_sources)} sources"}
        except Exception as e:
            logger.warning(f"Grounding guard search failed (writing without): {e}")

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
        # Skill API responses can be up to 32KB — the researcher needs the full
        # data, but the writer copy is capped so one response doesn't dominate
        # (and pay for) the entire answer-stage prefill.
        _src_type = (r.get("metadata") or {}).get("source_type", "")
        if _src_type.startswith("skill_api") and len(content) > 8000:
            content = (
                content[:8000]
                + "\n[response truncated for the answer stage — the full data "
                "was already analyzed during research]"
            )
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
        secure=settings.prompt_security,
    )

    writer_messages = [{"role": "system", "content": writer_system}]

    # Include the curated conversation context for multi-turn continuity.
    for msg in curated_history:
        writer_messages.append({"role": msg.role, "content": msg.content})

    # When retrieval produced no grounded sources, instruct the writer not to
    # cite at all — otherwise it tends to emit learned [src_N] formatting that
    # has nothing to point at. The stream filter below is the hard guarantee;
    # this just avoids wasting tokens on citations we'd only strip.
    if not source_events:
        writer_user += (
            "\n\n(No reference sources are available — do NOT use [src_N] "
            "citation markers anywhere in your answer.)"
        )

    writer_messages.append({"role": "user", "content": writer_user})

    try:
        # Writer composes the final answer from already-gathered context — it
        # never needs hidden reasoning, so suppress it for a snappy first token
        # (mode-aware: quality stays AUTO). See _chat_reasoning_mode.
        stream = await safe_chat_completion(
            client.chat.completions.create,
            base_url=llm_config.base_url,
            model=llm_config.model,
            reasoning_mode=_chat_reasoning_mode(mode, settings),
            overrides=settings.parsed_reasoning_overrides,
            messages=writer_messages,
            stream=True,
            **stream_usage_kwargs(),
            **build_chat_params(
                llm_config.model, temperature=0.3, max_tokens=writer_max_tokens
            ),
        )

        # Strip any [src_N] whose N has no matching emitted source (orphaned /
        # over-numbered / no-sources-at-all), preserving the per-turn contract.
        answer_parts: List[str] = []
        citation_filter = _CitationStripper(max_index=len(source_events))
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                clean = citation_filter.feed(chunk.choices[0].delta.content)
                if clean:
                    answer_parts.append(clean)
                    yield {"content": clean}
        tail = citation_filter.flush()
        if tail:
            answer_parts.append(tail)
            yield {"content": tail}

    except Exception as e:
        logger.error(f"Writer streaming error: {e}")
        answer_parts = []
        yield {
            "content": "I encountered an error generating the response. Please try again."
        }

    # Update the client-carried memory blob AFTER streaming (zero added latency on
    # the answer path). Only when the client opted in by sending a blob.
    _done_event = {"done": True, "communities_used": list(set(communities_used))}
    _memory_enabled = conversation_memory is not None and getattr(
        settings, "enable_conversation_memory", True
    )
    # Compaction is a full LLM call (1-4s). Emitting `done` first lets the UI
    # finalize the turn as soon as the last answer token lands; pending_memory
    # tells clients one more frame (memory_update) follows before stream end.
    # EMIT_DONE_BEFORE_MEMORY=false restores the legacy order for clients that
    # stop consuming at `done`.
    _done_first = _memory_enabled and getattr(settings, "emit_done_before_memory", True)
    if _done_first:
        yield {**_done_event, "pending_memory": True}
    if _memory_enabled:
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

    if not _done_first:
        yield _done_event


# =============================================================================
# HTTP Request Helper
# =============================================================================


def _truncate_response(text: str, max_chars: int = 32000) -> str:
    """Truncate an HTTP response intelligently, labeling the cut explicitly.

    For JSON responses with arrays, slim down each item by truncating long
    string values (descriptions etc.) to keep ALL items within budget.
    Falls back to keeping complete items, then plain truncation. The result
    always carries a trailer stating data was dropped — without it the model
    confidently answers from cut data and never paginates.
    """
    if len(text) <= max_chars:
        return text
    truncated = _truncate_response_body(text, max_chars)
    return truncated + (
        f"\n[NOTE: response truncated — the original was {len(text)} chars "
        f"and only part is shown. Do not assume this is everything; request "
        f"fewer items per call or use pagination parameters "
        f"(e.g. ?limit=, ?page=) to retrieve the rest.]"
    )


def _truncate_response_body(text: str, max_chars: int) -> str:
    """Core slimming logic for :func:`_truncate_response` (no trailer)."""
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
