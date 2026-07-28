#!/usr/bin/env python3
"""Seed a Langfuse project's model price catalog so generations get USD cost.

Langfuse prices a generation by regex-matching the recorded model name to a
model definition (input/output price per token). Venice/OpenRouter models are
not in Langfuse's built-in catalog, so without this seed cost shows $0 even
though token usage is tracked. This script POSTs the definitions in
``langfuse-models.json`` (USD per 1M tokens) to a project's Models API, dividing
by 1e6 to Langfuse's per-token unit.

Idempotent: skips a model when a non-managed definition with the same name +
prices already exists; otherwise creates it. Safe to re-run after editing the
catalog, and reusable against ANY project (point the keys at a tenant project to
backfill it).

Usage:
  # Uses LANGFUSE_BASE_URL / LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY from env
  # (or a .env file via --env-file). Project-scoped keys → seeds THAT project.
  python seed_langfuse_models.py
  python seed_langfuse_models.py --env-file ../../.env
  python seed_langfuse_models.py --base-url https://lf.example.com \
      --public-key pk-lf-... --secret-key sk-lf-... --catalog ./langfuse-models.json
  python seed_langfuse_models.py --dry-run
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path

DEFAULT_CATALOG = Path(__file__).with_name("langfuse-models.json")


def _load_env_file(path: str) -> None:
    """Minimal .env loader (KEY=VALUE, ignores comments/quotes)."""
    p = Path(path)
    if not p.exists():
        sys.exit(f"--env-file not found: {path}")
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _auth_header(public_key: str, secret_key: str) -> str:
    raw = f"{public_key}:{secret_key}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def _request(method: str, url: str, auth: str, body: dict | None = None) -> tuple[int, dict | list | None]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", auth)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = resp.read().decode()
            return resp.status, (json.loads(payload) if payload else None)
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:300]}


def _existing_models(base: str, auth: str) -> dict[str, dict]:
    """Map modelName -> definition for project-custom (non-managed) models."""
    out: dict[str, dict] = {}
    page = 1
    while True:
        status, data = _request("GET", f"{base}/api/public/models?limit=100&page={page}", auth)
        if status != 200 or not isinstance(data, dict):
            break
        rows = data.get("data", [])
        for m in rows:
            if not m.get("isLangfuseManaged"):
                out[m.get("modelName")] = m
        meta = data.get("meta", {})
        if page >= meta.get("totalPages", 1) or not rows:
            break
        page += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed Langfuse model price catalog.")
    ap.add_argument("--base-url", default=os.environ.get("LANGFUSE_BASE_URL", ""))
    ap.add_argument("--public-key", default=os.environ.get("LANGFUSE_PUBLIC_KEY", ""))
    ap.add_argument("--secret-key", default=os.environ.get("LANGFUSE_SECRET_KEY", ""))
    ap.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    ap.add_argument("--env-file", help="Load LANGFUSE_* from this .env first")
    ap.add_argument("--dry-run", action="store_true", help="Show what would change, POST nothing")
    args = ap.parse_args()

    if args.env_file:
        _load_env_file(args.env_file)
        args.base_url = args.base_url or os.environ.get("LANGFUSE_BASE_URL", "")
        args.public_key = args.public_key or os.environ.get("LANGFUSE_PUBLIC_KEY", "")
        args.secret_key = args.secret_key or os.environ.get("LANGFUSE_SECRET_KEY", "")

    if not (args.base_url and args.public_key and args.secret_key):
        sys.exit("Missing LANGFUSE_BASE_URL / LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY "
                 "(set env, --env-file, or pass flags).")

    base = args.base_url.rstrip("/")
    auth = _auth_header(args.public_key, args.secret_key)
    catalog = json.loads(Path(args.catalog).read_text())
    unit = catalog.get("unit", "TOKENS")
    models = catalog.get("models", [])

    existing = _existing_models(base, auth)
    created = skipped = failed = 0

    for entry in models:
        name = entry["model"]
        input_price = round(entry["input_per_1m"] / 1_000_000, 15)
        output_price = round(entry["output_per_1m"] / 1_000_000, 15)
        # Default: match the model name exactly (case-insensitive). An entry may
        # supply "match_pattern" to override — e.g. to tolerate an OpenRouter
        # ":nitro"/":floor" variant suffix on the recorded model name.
        pattern = entry.get("match_pattern") or f"(?i)^({re.escape(name)})$"

        prev = existing.get(name)
        if prev and abs((prev.get("inputPrice") or 0) - input_price) < 1e-18 \
                and abs((prev.get("outputPrice") or 0) - output_price) < 1e-18:
            print(f"  = skip  {name} (already priced {input_price}/{output_price})")
            skipped += 1
            continue

        if args.dry_run:
            verb = "update" if prev else "create"
            print(f"  ~ {verb} {name} -> in={input_price} out={output_price}")
            created += 1
            continue

        status, resp = _request("POST", f"{base}/api/public/models", auth, {
            "modelName": name,
            "matchPattern": pattern,
            "unit": unit,
            "inputPrice": input_price,
            "outputPrice": output_price,
        })
        if status == 200:
            print(f"  + create {name} -> in={input_price} out={output_price}")
            created += 1
        else:
            print(f"  ! FAIL  {name}: {status} {resp}")
            failed += 1

    print(f"\nDone: {created} created/updated, {skipped} unchanged, {failed} failed "
          f"({len(models)} in catalog) -> {base}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
