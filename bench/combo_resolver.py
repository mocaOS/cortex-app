"""Load models.yaml + combos.yaml, resolve combo refs to full model dicts.

Each combo has three tier refs (primary / extraction / relationship) that name
model entries in models.yaml. After resolution, each tier carries the full
model dict (model_id, base_url, api_key, context_length, ...).

API key resolution:
  - If the model entry has `api_key`, that literal is used.
  - Else, `api_key_env` names an env var. Try the shell environment first, then
    fall back to the live .env at repo root.
  - Empty / missing key → ResolverError.

Failure mode: every error in this module fires BEFORE the orchestrator touches
.env or the docker container — so a misconfigured combo fails fast and leaves
the environment untouched.
"""

from __future__ import annotations

import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Optional

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
BENCH_DIR = REPO_ROOT / "bench"
ENV_PATH = REPO_ROOT / ".env"
DEFAULT_MODELS = BENCH_DIR / "models.yaml"
DEFAULT_COMBOS = BENCH_DIR / "combos.yaml"


class ResolverError(RuntimeError):
    """Raised when a combo / model definition is malformed or unresolvable."""


# ---------------------------------------------------------------------------
# .env loader (for api_key_env fallback)
# ---------------------------------------------------------------------------

def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def resolve_api_key(model: dict, env_file_values: dict[str, str]) -> str:
    """Return the literal API key for a model entry."""
    if model.get("api_key"):
        return str(model["api_key"])
    env_name = model.get("api_key_env")
    if not env_name:
        raise ResolverError(
            f"Model {model.get('model_id')!r} has no api_key or api_key_env."
        )
    shell_val = os.environ.get(env_name)
    if shell_val:
        return shell_val
    file_val = env_file_values.get(env_name)
    if file_val:
        return file_val
    raise ResolverError(
        f"Model {model.get('model_id')!r} requires env var {env_name!r}, "
        f"but it's missing from both the shell environment and {ENV_PATH}."
    )


# ---------------------------------------------------------------------------
# Model + combo loading
# ---------------------------------------------------------------------------

REQUIRED_MODEL_FIELDS = ("model_id", "base_url", "context_length")


def _validate_model(model_key: str, model: dict) -> None:
    for field in REQUIRED_MODEL_FIELDS:
        if field not in model or model[field] in (None, ""):
            raise ResolverError(
                f"Model {model_key!r} is missing required field {field!r}."
            )
    if not (model.get("api_key") or model.get("api_key_env")):
        raise ResolverError(
            f"Model {model_key!r} must define either api_key or api_key_env."
        )
    if not isinstance(model["context_length"], int) or model["context_length"] <= 0:
        raise ResolverError(
            f"Model {model_key!r} context_length must be a positive int, "
            f"got {model['context_length']!r}."
        )
    if not re.match(r"^https?://", str(model["base_url"])):
        raise ResolverError(
            f"Model {model_key!r} base_url must be http(s)://…, got {model['base_url']!r}."
        )


def load_models(path: Path = DEFAULT_MODELS) -> dict[str, dict]:
    if not path.exists():
        raise ResolverError(f"models.yaml not found at {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    raw = data.get("models", {})
    if not isinstance(raw, dict):
        raise ResolverError("models.yaml must have a top-level 'models:' mapping.")
    for key, entry in raw.items():
        if not isinstance(entry, dict):
            raise ResolverError(f"Model {key!r} must be a mapping, got {type(entry)}.")
        _validate_model(key, entry)
    return raw


def load_combos(path: Path = DEFAULT_COMBOS) -> list[dict]:
    if not path.exists():
        raise ResolverError(f"combos.yaml not found at {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    combos = data.get("combos", [])
    if not isinstance(combos, list) or not combos:
        raise ResolverError("combos.yaml must have a non-empty 'combos:' list.")
    seen_ids: set[str] = set()
    for combo in combos:
        if not isinstance(combo, dict):
            raise ResolverError(f"Combo must be a mapping, got {type(combo)}.")
        cid = combo.get("id")
        if not cid:
            raise ResolverError(f"Combo missing 'id': {combo!r}")
        if cid in seen_ids:
            raise ResolverError(f"Duplicate combo id {cid!r}.")
        seen_ids.add(cid)
        for tier in ("primary", "extraction", "relationship"):
            if tier not in combo or not combo[tier]:
                raise ResolverError(
                    f"Combo {cid!r} missing tier {tier!r}. Each combo needs "
                    "primary / extraction / relationship."
                )
    return combos


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def resolve_combo(
    combo: dict,
    models: dict[str, dict],
    env_file_values: dict[str, str],
) -> dict:
    """Replace each tier's model-id string with the full model dict (incl. key).

    Returns a NEW dict so the input is not mutated. The returned dict has the
    same shape as the input combo, but each of `primary`/`extraction`/`relationship`
    is now a full model dict with an additional `api_key` literal resolved.
    """
    out = deepcopy(combo)
    for tier in ("primary", "extraction", "relationship"):
        model_key = combo[tier]
        if model_key not in models:
            raise ResolverError(
                f"Combo {combo['id']!r} references unknown model {model_key!r} "
                f"at tier {tier!r}. Known: {sorted(models.keys())}"
            )
        resolved = deepcopy(models[model_key])
        resolved["api_key"] = resolve_api_key(resolved, env_file_values)
        resolved["_registry_key"] = model_key  # provenance for the run record
        out[tier] = resolved
    return out


def load_all(
    *,
    models_path: Path = DEFAULT_MODELS,
    combos_path: Path = DEFAULT_COMBOS,
    only: Optional[str] = None,
) -> list[dict]:
    """Convenience: load + validate + resolve everything.

    If `only` is given, returns only combos whose id contains the substring.

    Raises ResolverError on any malformed YAML, unknown model ref, or missing
    API key.
    """
    models = load_models(models_path)
    combos = load_combos(combos_path)
    if only:
        combos = [c for c in combos if only in c["id"]]
        if not combos:
            raise ResolverError(f"--only {only!r} matched no combos.")
    env_file_values = _load_env_file(ENV_PATH)
    return [resolve_combo(c, models, env_file_values) for c in combos]


# ---------------------------------------------------------------------------
# CLI entry-point: `python combo_resolver.py` — quick validation
# ---------------------------------------------------------------------------

def _redact(key: str) -> str:
    if not key:
        return "(empty)"
    return f"{key[:4]}…{key[-4:]}" if len(key) > 12 else "…"


if __name__ == "__main__":
    import sys

    try:
        resolved = load_all()
    except ResolverError as e:
        print(f"ResolverError: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(resolved)} combo(s). Resolved configs:\n")
    for combo in resolved:
        print(f"=== {combo['id']} ===")
        for tier in ("primary", "extraction", "relationship"):
            m = combo[tier]
            print(
                f"  {tier:13s}  {m['model_id']:42s}"
                f"  base={m['base_url']}"
                f"  key={_redact(m['api_key'])}"
                f"  ctx={m['context_length']:>7,d}"
            )
        for k in ("extraction_reasoning_mode", "relationship_reasoning_mode", "default_reasoning_mode"):
            if k in combo:
                print(f"  override     {k}={combo[k]}")
        print()
