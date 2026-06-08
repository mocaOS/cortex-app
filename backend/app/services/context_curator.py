"""Multi-bucket conversation context curator.

Cortex's agent is stateless: the client carries an opaque ``conversation_memory``
blob and the backend curates a bounded context from it each turn (instead of the
legacy ``conversation_history[-N:]`` truncation), then returns an updated blob via a
``memory_update`` SSE event.

Phase 1 scope: the **transcript** bucket only — keep the most recent messages
verbatim and fold older ones into a rolling ``summary`` using a cheap fast model.
Later phases add ``source_ledger`` (citation continuity), ``facts``,
``open_questions`` and ``intent`` to the same blob without changing this contract.

Backward-compatible by construction: when ``memory`` is ``None`` (or the feature is
disabled), :func:`build_context` returns exactly the legacy truncation and
:func:`compact_memory` is never called, so behavior is byte-identical to today.

Blob shape (v1)::

    {
      "version": 1,
      "transcript": {
        "summary": "<rolling summary of messages older than the verbatim window>",
        "summarized_count": <int: # of leading conversation messages folded into summary>
      }
    }

Contract: when a client opts into memory it sends the **full** ``conversation_history``
each turn (cortex-chat already persists chats). ``summarized_count`` indexes into that
canonical history, so the verbatim/summarized split is unambiguous and lossless.
"""

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI

from app.models import ConversationMessage
from app.services.llm_config import get_llm_config, build_chat_params
from app.services.reasoning_config import build_reasoning_kwargs, ReasoningMode

logger = logging.getLogger(__name__)


def _utility_kwargs(base_url: str, model: str, max_tokens: int) -> dict:
    """Chat kwargs for the curator's internal utility calls (compaction,
    fast-path classifier). Reasoning is forced OFF so a GPT-5/o-series model
    doesn't spend the completion budget on reasoning tokens (which would leave
    these short outputs empty); token/temperature params are model-adapted.
    """
    kw = build_chat_params(model, temperature=0.2, max_tokens=max_tokens)
    try:
        kw.update(build_reasoning_kwargs(base_url, model, ReasoningMode.OFF))
    except Exception:  # noqa: BLE001 — reasoning kwargs are best-effort
        pass
    return kw

MEMORY_VERSION = 1

_GIST_CHARS = 200


def source_sid(source: Dict[str, Any]) -> str:
    """Conversation-stable id for a source.

    Derived from ``chunk_id`` when present (so the same chunk is the same id
    across every turn), else from filename + a content prefix. Deterministic, no
    state — the client and the ledger use it to preserve citation identity.
    """
    key = (source.get("chunk_id") or "").strip()
    if not key:
        filename = source.get("filename") or (source.get("metadata") or {}).get("filename") or ""
        key = f"{filename}|{(source.get('content') or '')[:_GIST_CHARS]}"
    return "s_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]

# How many ledger entries to surface in the curated context (most recent kept).
_LEDGER_DIGEST_N = 8
_MAX_FACTS = 12
_MAX_OPEN_QUESTIONS = 6


def _transcript(memory: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Safely extract the transcript bucket from a (possibly malformed) blob."""
    if not isinstance(memory, dict):
        return {}
    t = memory.get("transcript")
    return t if isinstance(t, dict) else {}


def _str_list(value: Any, cap: int) -> List[str]:
    """Coerce a bucket value into a capped list of non-empty strings."""
    if not isinstance(value, list):
        return []
    out = [str(v).strip() for v in value if str(v).strip()]
    return out[:cap]


def render_memory_block(memory: Optional[Dict[str, Any]]) -> str:
    """Render the semantic buckets + ledger digest + summary as one text block.

    Used both as injected conversation context (build_context) and as the basis
    for the fast-path answerability check. Returns "" when there is nothing useful.
    """
    if not isinstance(memory, dict):
        return ""
    parts: List[str] = []

    intent = (memory.get("intent") or "").strip() if isinstance(memory.get("intent"), str) else ""
    if intent:
        parts.append(f"User intent / preferences: {intent}")

    facts = _str_list(memory.get("facts"), _MAX_FACTS)
    if facts:
        parts.append("Established facts:\n" + "\n".join(f"- {f}" for f in facts))

    open_qs = _str_list(memory.get("open_questions"), _MAX_OPEN_QUESTIONS)
    if open_qs:
        parts.append("Open questions:\n" + "\n".join(f"- {q}" for q in open_qs))

    ledger = memory.get("source_ledger")
    if isinstance(ledger, list) and ledger:
        recent = [e for e in ledger if isinstance(e, dict)][-_LEDGER_DIGEST_N:]
        lines = [
            f"- [{e.get('sid', '?')}] {e.get('filename', 'source')}: {(e.get('gist') or '')[:120]}"
            for e in recent
        ]
        parts.append("Previously cited sources (reuse instead of re-fetching):\n" + "\n".join(lines))

    summary = (_transcript(memory).get("summary") or "").strip()
    if summary:
        parts.append(f"Summary of earlier conversation:\n{summary}")

    return "\n\n".join(parts).strip()


def build_context(
    conversation_history: Optional[List[ConversationMessage]],
    memory: Optional[Dict[str, Any]],
    settings,
) -> List[ConversationMessage]:
    """Assemble the bounded message list to inject as conversation context.

    Returns a list of :class:`ConversationMessage`. The caller appends the new
    user question after these.

    - No memory / disabled  -> legacy ``conversation_history[-max_conversation_history:]``.
    - Memory present        -> a leading memory block (intent + facts + open questions
      + ledger digest + rolling summary) + the verbatim tail not yet folded in.
    """
    history = list(conversation_history or [])

    if not memory or not getattr(settings, "enable_conversation_memory", True):
        n = settings.max_conversation_history
        return history[-n:] if n > 0 else history

    summarized_count = _transcript(memory).get("summarized_count", 0)
    if not isinstance(summarized_count, int) or summarized_count < 0:
        summarized_count = 0
    # Never index past the history we were actually given.
    summarized_count = min(summarized_count, len(history))

    messages: List[ConversationMessage] = []
    block = render_memory_block(memory)
    if block:
        messages.append(
            ConversationMessage(role="user", content="[Conversation memory]\n" + block)
        )
    # Everything from summarized_count onward is kept verbatim (no gap, no overlap
    # with the summary).
    messages.extend(history[summarized_count:])
    return messages


def _normalize(msg: Any) -> Optional[Dict[str, str]]:
    """Coerce a ConversationMessage or dict into a {role, content} dict."""
    if isinstance(msg, ConversationMessage):
        return {"role": msg.role, "content": msg.content}
    if isinstance(msg, dict) and "role" in msg and "content" in msg:
        return {"role": str(msg["role"]), "content": str(msg["content"])}
    return None


def _parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Best-effort extract a JSON object from an LLM response (strips fences/prose)."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if t.count("```") >= 2 else t.strip("`")
        if t.lstrip().startswith("json"):
            t = t.lstrip()[4:]
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        obj = json.loads(t[start : end + 1])
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


async def _update_buckets(
    prior: Dict[str, Any],
    aged_out: List[Dict[str, str]],
    question: str,
    answer: str,
    client: AsyncOpenAI,
    model: str,
    base_url: str,
) -> Dict[str, Any]:
    """One structured fast-LLM call that updates summary/facts/open_questions/intent.

    - ``summary`` only grows by folding ``aged_out`` messages (empty => unchanged).
    - ``facts``/``open_questions``/``intent`` are (re)derived from the latest exchange
      merged with their prior values.

    Returns a dict with keys summary, facts, open_questions, intent. Raises on a hard
    LLM error (caller keeps prior buckets); tolerates malformed JSON by falling back
    to prior values field-by-field.
    """
    aged = "\n".join(f"{m['role']}: {m['content']}" for m in aged_out) or "(none)"
    prior_facts = _str_list(prior.get("facts"), _MAX_FACTS)
    prior_oq = _str_list(prior.get("open_questions"), _MAX_OPEN_QUESTIONS)
    prior_intent = prior.get("intent") if isinstance(prior.get("intent"), str) else ""
    prior_summary = (prior.get("summary") or "").strip()

    system = (
        "You maintain compact long-term memory for an ongoing conversation between a "
        "user and an AI assistant. Return ONLY a JSON object with keys: "
        '"summary" (string), "facts" (string[]), "open_questions" (string[]), '
        '"intent" (string).\n'
        "Rules:\n"
        "- summary: if AGED-OUT MESSAGES is '(none)', return EARLIER SUMMARY verbatim; "
        "otherwise merge them into a concise narrative (<=150 words, third person).\n"
        f"- facts: merge EARLIER FACTS with durable facts/decisions/constraints from the "
        f"LATEST EXCHANGE; dedupe; keep the {_MAX_FACTS} most important; short bullets.\n"
        f"- open_questions: unresolved threads; keep <= {_MAX_OPEN_QUESTIONS}; drop ones now answered.\n"
        "- intent: one sentence capturing the user's goal and preferred answer "
        "style/language; update only if clearer. No prose outside the JSON."
    )
    user = (
        f"EARLIER SUMMARY:\n{prior_summary or '(none)'}\n\n"
        f"EARLIER FACTS:\n{json.dumps(prior_facts, ensure_ascii=False)}\n\n"
        f"EARLIER OPEN QUESTIONS:\n{json.dumps(prior_oq, ensure_ascii=False)}\n\n"
        f"EARLIER INTENT:\n{prior_intent or '(none)'}\n\n"
        f"AGED-OUT MESSAGES (fold into summary):\n{aged}\n\n"
        f"LATEST EXCHANGE:\nuser: {question}\nassistant: {answer}\n\nJSON:"
    )
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        **_utility_kwargs(base_url, model, 900),
    )
    parsed = _parse_json_object(resp.choices[0].message.content or "") or {}
    return {
        "summary": (parsed.get("summary") or prior_summary).strip()
        if isinstance(parsed.get("summary"), str)
        else prior_summary,
        "facts": _str_list(parsed.get("facts"), _MAX_FACTS) or prior_facts,
        "open_questions": _str_list(parsed.get("open_questions"), _MAX_OPEN_QUESTIONS) or prior_oq,
        "intent": (parsed.get("intent") or prior_intent).strip()
        if isinstance(parsed.get("intent"), str)
        else prior_intent,
    }


async def is_memory_answerable(
    question: str,
    memory: Optional[Dict[str, Any]],
    settings,
) -> bool:
    """Fast classifier: can this turn be answered from memory alone (skip retrieval)?

    Gated by ``enable_memory_fast_path`` and the presence of non-trivial memory.
    Conservative: any error or uncertainty returns False (do the normal retrieval).
    """
    if not getattr(settings, "enable_memory_fast_path", True):
        return False
    block = render_memory_block(memory)
    if not block:
        return False
    try:
        llm_config = get_llm_config(fast_mode=True)
        model = settings.conversation_memory_compaction_model or llm_config.model
        client = AsyncOpenAI(api_key=llm_config.api_key, base_url=llm_config.base_url)
        system = (
            "You decide whether a user's new message can be fully answered using ONLY "
            "the provided conversation memory, with NO new document/database search. "
            "Examples answerable from memory: 'summarize that', 'why?', 'translate to "
            "German', 'rephrase', referring back to something already discussed. "
            "Examples NOT answerable: any request for new facts, details, or sources not "
            "already present. Reply with exactly 'yes' or 'no'."
        )
        user = f"CONVERSATION MEMORY:\n{block}\n\nNEW MESSAGE:\n{question}\n\nAnswerable from memory only?"
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            **_utility_kwargs(llm_config.base_url, model, 200),
        )
        verdict = (resp.choices[0].message.content or "").strip().lower()
        return verdict.startswith("y")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Memory fast-path classifier failed, defaulting to retrieval: {e}")
        return False


def rehydrate_graph_context(memory: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Reconstruct a graph_context dict from the blob's stored kg_context.

    Used on fast-path turns (retrieval skipped) so the writer keeps the graph
    grounding from earlier turns without re-querying Neo4j.
    """
    empty = {"entities": [], "relationships": [], "communities": []}
    if not isinstance(memory, dict):
        return empty
    kg = memory.get("kg_context")
    if not isinstance(kg, dict):
        return empty
    return {
        "entities": kg.get("entities") if isinstance(kg.get("entities"), list) else [],
        "relationships": kg.get("relationships") if isinstance(kg.get("relationships"), list) else [],
        "communities": kg.get("communities") if isinstance(kg.get("communities"), list) else [],
    }


def _merge_kg_context(base: Dict[str, Any], kg_context: Optional[Dict[str, Any]]) -> None:
    """Store a capped snapshot of the turn's graph context for later rehydration."""
    if not isinstance(kg_context, dict):
        return
    ents = kg_context.get("entities") if isinstance(kg_context.get("entities"), list) else []
    comms = kg_context.get("communities") if isinstance(kg_context.get("communities"), list) else []
    if not ents and not comms:
        return  # nothing useful this turn — keep any prior kg_context
    base["kg_context"] = {
        "entities": ents[:12],
        "communities": comms[:5],
    }


def _merge_source_ledger(
    base: Dict[str, Any],
    sources: Optional[List[Dict[str, Any]]],
    settings,
) -> None:
    """Accumulate this turn's emitted sources into the source_ledger bucket.

    Deduped by ``sid`` (so a chunk cited across many turns has one ledger entry),
    capped to the most recent ``conversation_memory_max_ledger`` entries. Each entry
    is compact — ``{sid, filename, gist, score}`` — so the blob stays small. KG
    anchors (entity/community ids) are layered on in a later phase.
    """
    if not sources:
        return
    ledger = base.get("source_ledger")
    if not isinstance(ledger, list):
        ledger = []
    known = {e.get("sid") for e in ledger if isinstance(e, dict)}
    for s in sources:
        sid = s.get("sid")
        if not sid or sid in known:
            continue
        meta = s.get("metadata") or {}
        ledger.append({
            "sid": sid,
            "filename": meta.get("filename") or s.get("filename", ""),
            "gist": (s.get("content") or "")[:_GIST_CHARS],
            "score": s.get("score", 0),
        })
        known.add(sid)
    cap = getattr(settings, "conversation_memory_max_ledger", 50)
    if cap and len(ledger) > cap:
        ledger = ledger[-cap:]
    base["source_ledger"] = ledger


async def compact_memory(
    memory: Optional[Dict[str, Any]],
    conversation_history: Optional[List[ConversationMessage]],
    question: str,
    answer: str,
    settings,
    sources: Optional[List[Dict[str, Any]]] = None,
    kg_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return an updated memory blob after a completed turn.

    One structured fast-LLM call updates the transcript summary (folding aged-out
    messages) and the semantic buckets (facts / open_questions / intent) from the
    latest exchange; the source_ledger and kg_context are merged in deterministically.
    Runs *after* the answer has streamed, so it adds no user-visible latency. Degrades
    gracefully: on any LLM error the prior buckets are kept (no message lost — aged-out
    folding simply retries next turn).
    """
    base: Dict[str, Any] = dict(memory) if isinstance(memory, dict) else {}

    # Canonical post-turn transcript = what the client sent + this exchange. The
    # client will hold the same list next turn, so indices stay stable.
    post_turn: List[Dict[str, str]] = []
    for m in (conversation_history or []):
        nm = _normalize(m)
        if nm:
            post_turn.append(nm)
    post_turn.append({"role": "user", "content": question})
    post_turn.append({"role": "assistant", "content": answer})

    prev = _transcript(base)
    summary = (prev.get("summary") or "").strip()
    summarized_count = prev.get("summarized_count", 0)
    if not isinstance(summarized_count, int) or summarized_count < 0:
        summarized_count = 0
    summarized_count = min(summarized_count, len(post_turn))

    window = max(0, settings.conversation_memory_window)
    target = max(0, len(post_turn) - window)
    aged_out = post_turn[summarized_count:target] if target > summarized_count else []

    # Carry priors forward by default (graceful degradation on LLM failure).
    facts = _str_list(base.get("facts"), _MAX_FACTS)
    open_questions = _str_list(base.get("open_questions"), _MAX_OPEN_QUESTIONS)
    intent = base.get("intent") if isinstance(base.get("intent"), str) else ""

    try:
        llm_config = get_llm_config(fast_mode=True)
        model = settings.conversation_memory_compaction_model or llm_config.model
        client = AsyncOpenAI(api_key=llm_config.api_key, base_url=llm_config.base_url)
        updated = await _update_buckets(
            prior={
                "summary": summary,
                "facts": facts,
                "open_questions": open_questions,
                "intent": intent,
            },
            aged_out=aged_out,
            question=question,
            answer=answer,
            client=client,
            model=model,
            base_url=llm_config.base_url,
        )
        summary = updated["summary"]
        facts = updated["facts"]
        open_questions = updated["open_questions"]
        intent = updated["intent"]
        if aged_out:
            summarized_count = target
    except Exception as e:  # noqa: BLE001 — never let compaction break the turn
        logger.warning(f"Memory compaction failed, keeping prior buckets: {e}")

    base["version"] = MEMORY_VERSION
    base["transcript"] = {"summary": summary, "summarized_count": summarized_count}
    base["facts"] = facts
    base["open_questions"] = open_questions
    base["intent"] = intent
    _merge_source_ledger(base, sources, settings)
    _merge_kg_context(base, kg_context)
    return base
