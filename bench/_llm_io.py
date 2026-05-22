"""Shared helpers for calling OpenAI-compatible /chat/completions endpoints.

Used by `llm_review.py` (end-of-batch review) and `qa_evaluator.py` (question
generation + judging). Both call the operator's primary model with the same
robustness measures, so the wire-level concerns live here.

What this module provides:

- `chat_completion(...)` — POST to /chat/completions with httpx, retry once
  without `response_format` if the server rejects it (some providers 400),
  return the raw `content` string from `choices[0].message`.

- `strip_thinking_and_fences(text)` — remove `<think>...</think>` blocks and
  a wrapping ```json ... ``` fence. Reasoning models often emit both despite
  prompt instructions; without stripping, JSON parsing fails.

- `parse_json_response(text)` — convenience wrapper that strips then parses.
  Raises RuntimeError with a 500-char tail on malformed input so the caller
  can log something useful.
"""

from __future__ import annotations

import json
import re
from typing import Optional

import httpx


_THINK_BLOCK_RE = re.compile(r"<think>[\s\S]*?</think>\s*", re.IGNORECASE)


def strip_thinking_and_fences(text: str) -> str:
    """Remove `<think>...</think>` reasoning blocks AND a wrapping ```json fence."""
    cleaned = _THINK_BLOCK_RE.sub("", text).strip()
    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        if first_nl >= 0:
            cleaned = cleaned[first_nl + 1:]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
    return cleaned.strip()


def parse_json_response(text: str) -> dict:
    """Strip think-blocks + fences, then `json.loads`. Raises RuntimeError on failure."""
    cleaned = strip_thinking_and_fences(text)
    if not cleaned:
        raise RuntimeError(
            "Empty content after stripping <think> blocks. "
            f"Raw (first 500 chars): {text[:500]}"
        )
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Response was not valid JSON ({exc}). "
            f"Cleaned (first 500 chars): {cleaned[:500]}"
        ) from exc


async def chat_completion(
    messages: list[dict],
    *,
    api_key: str,
    base_url: str,
    model: str,
    max_tokens: int = 4000,
    temperature: float = 0.3,
    timeout_s: float = 600.0,
    want_json: bool = True,
) -> str:
    """POST messages to {base_url}/chat/completions; return content string.

    If `want_json=True`, sends `response_format: {"type": "json_object"}` and
    retries once without it if the server returns 400 (some providers don't
    recognise the field).

    Raises RuntimeError on any non-2xx status or malformed envelope.
    """
    if not (api_key and base_url and model):
        raise RuntimeError(
            "chat_completion needs api_key, base_url, and model "
            "(all three must be non-empty)."
        )

    body: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if want_json:
        body["response_format"] = {"type": "json_object"}

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async def _post(payload: dict) -> httpx.Response:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            return await client.post(url, json=payload, headers=headers)

    try:
        r = await _post(body)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"chat_completion request failed: {exc}") from exc

    if r.status_code == 400 and "response_format" in body:
        body.pop("response_format", None)
        try:
            r = await _post(body)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"chat_completion retry failed: {exc}") from exc

    if r.status_code != 200:
        raise RuntimeError(
            f"chat_completion HTTP {r.status_code} from {url}: {r.text[:500]}"
        )

    try:
        payload = r.json()
        content: Optional[str] = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"chat_completion response malformed: {r.text[:500]}"
        ) from exc

    return content or ""
