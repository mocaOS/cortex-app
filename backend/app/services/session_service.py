"""Server-side sessions — the backend keeps conversation state per session_id.

The client-carried memory contract (send `conversation_memory`, replay the
`memory_update` frame plus the FULL history every turn) stays the stateless
default. Sessions are the opt-in alternative (`ENABLE_SESSIONS`): create one,
pass `session_id` on ask, and the backend loads/persists history + the curated
blob itself — clients drop to "pass a string", wire cost stops growing with
conversation length, and conversations survive whichever machine the client
ran on.

Doctrine:
- A session belongs to the API key that created it; other keys see 404.
- `session_id` and client-carried state are mutually exclusive on a request
  (400 `session_conflict`) — one source of truth.
- History is capped at SESSION_MAX_TURNS messages; trimming drops the OLDEST
  messages and decrements the memory blob's `transcript.summarized_count` by
  the same amount, preserving the curator's index into the canonical history
  (trimmed messages are exactly the ones already folded into the rolling
  summary once summarized_count >= trimmed).
- Sessions are instance-operational data: excluded from library export,
  TTL-swept by hourly maintenance (SESSION_TTL_DAYS idle), quota-capped per
  key (SESSION_MAX_PER_KEY).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Hard byte ceilings — a session must never grow unbounded even within the
# turn cap (pathological single messages). Matches clamp_memory_blob's spirit.
MAX_HISTORY_BYTES = 1_000_000
MAX_MEMORY_BYTES = 128_000


def sessions_enabled() -> bool:
    from app.config import get_settings

    return bool(getattr(get_settings(), "enable_sessions", False))


def parse_session_state(row: Dict[str, Any]) -> Tuple[List[dict], dict]:
    """(history, memory) from a stored session row — tolerant of corruption."""
    try:
        history = json.loads(row.get("history") or "[]")
        if not isinstance(history, list):
            history = []
    except (ValueError, TypeError):
        history = []
    try:
        memory = json.loads(row.get("memory") or "{}")
        if not isinstance(memory, dict):
            memory = {}
    except (ValueError, TypeError):
        memory = {}
    return history, memory


def trim_history(
    history: List[dict], memory: dict, max_turns: int
) -> Tuple[List[dict], dict]:
    """Cap history at max_turns messages, keeping the curator index coherent.

    Drops the oldest messages and decrements transcript.summarized_count by
    the number trimmed (floored at 0). Returns (history, memory) — memory is
    copied only when adjusted.
    """
    if max_turns <= 0 or len(history) <= max_turns:
        return history, memory
    trimmed = len(history) - max_turns
    history = history[trimmed:]
    transcript = memory.get("transcript")
    if isinstance(transcript, dict) and "summarized_count" in transcript:
        memory = {**memory, "transcript": {
            **transcript,
            "summarized_count": max(0, int(transcript.get("summarized_count") or 0) - trimmed),
        }}
    return history, memory


def serialize_state(history: List[dict], memory: dict) -> Tuple[str, str]:
    """JSON-encode with byte ceilings; oversize history drops oldest turns."""
    memory_json = json.dumps(memory)
    if len(memory_json) > MAX_MEMORY_BYTES:
        logger.warning("Session memory blob over ceiling — dropping blob")
        memory_json = "{}"
    history_json = json.dumps(history)
    while len(history_json) > MAX_HISTORY_BYTES and len(history) > 2:
        history = history[2:]  # drop oldest exchange
        history_json = json.dumps(history)
    return history_json, memory_json


def build_turn_state(
    prior_history: List[dict],
    prior_memory: dict,
    question: str,
    answer: str,
    updated_memory: Optional[dict],
    max_turns: int,
) -> Tuple[str, str, int]:
    """Assemble the post-turn state → (history_json, memory_json, turn_count)."""
    history = prior_history + [
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer},
    ]
    memory = updated_memory if isinstance(updated_memory, dict) else prior_memory
    history, memory = trim_history(history, memory, max_turns)
    history_json, memory_json = serialize_state(history, memory)
    return history_json, memory_json, len(history)
