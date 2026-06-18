"""One-off probe: does Venice `disable_thinking` fix empty/slow chat answers?

For a couple of the models that returned empty answers in the benchmark, stream a
simple chat completion twice — plain vs `venice_parameters.disable_thinking=true`
— and report time-to-first-CONTENT-token, whether reasoning streamed in a
separate `reasoning_content` channel, and final answer length. Proves the
mechanism before changing production code. NOT part of the harness.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from run_bench import EnvFile, ENV_PATH  # noqa: E402

env = EnvFile.load(ENV_PATH)
BASE = (env.get("OPENAI_API_BASE") or "").rstrip("/")
KEY = env.get("OPENAI_API_KEY") or ""

QUESTION = ("Based on a product-information / ERP knowledge base, what is a PIM "
            "system and how does it relate to Microsoft Dynamics 365? Answer in 2-3 sentences.")

MODELS = ["google-gemma-4-26b-a4b-it", "qwen3-5-35b-a3b", "nvidia-nemotron-cascade-2-30b-a3b"]


async def probe(model: str, disable_thinking: bool) -> dict:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a concise assistant. Answer directly."},
            {"role": "user", "content": QUESTION},
        ],
        "stream": True,
        "max_tokens": 1200,
        "temperature": 0.3,
    }
    if disable_thinking:
        body["venice_parameters"] = {"disable_thinking": True}

    start = time.monotonic()
    first_content = None
    first_reasoning = None
    content = []
    reasoning_chars = 0
    async with httpx.AsyncClient(timeout=120) as cx:
        async with cx.stream("POST", f"{BASE}/chat/completions", json=body,
                             headers={"Authorization": f"Bearer {KEY}"}) as r:
            if r.status_code != 200:
                return {"model": model, "disable_thinking": disable_thinking,
                        "error": f"{r.status_code}: {(await r.aread())[:200]!r}"}
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    ev = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                delta = (ev.get("choices") or [{}])[0].get("delta", {}) or {}
                now = time.monotonic()
                rc = delta.get("reasoning_content")
                if rc:
                    reasoning_chars += len(rc)
                    if first_reasoning is None:
                        first_reasoning = now
                c = delta.get("content")
                if c:
                    if first_content is None:
                        first_content = now
                    content.append(c)
    end = time.monotonic()
    ans = "".join(content)
    return {
        "model": model,
        "disable_thinking": disable_thinking,
        "ttft_content_ms": int((first_content - start) * 1000) if first_content else None,
        "ttft_reasoning_ms": int((first_reasoning - start) * 1000) if first_reasoning else None,
        "reasoning_chars": reasoning_chars,
        "answer_chars": len(ans),
        "total_ms": int((end - start) * 1000),
        "answer_head": ans[:120].replace("\n", " "),
    }


async def main():
    for m in MODELS:
        for dt in (False, True):
            res = await probe(m, dt)
            tag = "disable_thinking" if dt else "plain          "
            if "error" in res:
                print(f"{m:40} {tag}  ERROR {res['error']}")
                continue
            print(f"{m:40} {tag}  ttft_content={str(res['ttft_content_ms']):>7}ms  "
                  f"reasoning_chars={res['reasoning_chars']:>6}  answer_chars={res['answer_chars']:>5}  "
                  f"total={res['total_ms']:>6}ms")
            print(f"      → {res['answer_head']!r}")


if __name__ == "__main__":
    asyncio.run(main())
