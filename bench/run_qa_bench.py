#!/usr/bin/env python3
"""Q+A *chat* benchmark orchestrator — snappiness + quality, graph held fixed.

Unlike `run_bench.py` (which re-ingests a corpus per combo to score INGESTION),
this swaps ONLY the answer model (`OPENAI_MODEL`) against the already-ingested
graph and scores the CHAT experience: how fast the first token arrives, whether
the model overthinks itself into timeouts, and how good the answers are.

Per model:
  1. Rewrite .env: OPENAI_MODEL + OPENAI_MAX_CONTEXT (context pinned the same
     for every model); base_url/api_key stay on the operator's provider.
  2. `docker compose up -d backend --force-recreate` so it re-reads .env, wait
     healthy. (NO reset — the graph is never touched.)
  3. Stream a fixed question bank through POST /api/ask/stream (use_agentic=false)
     capturing TTFT / total / timeouts / output rate (qa_snappiness).
After all models: one judge call scores quality; a leaderboard report ranks
models on a speed×quality blend and flags overthinkers.

The .env is backed up at start (EnvSwapper) and restored on exit / Ctrl-C; the
live backend is recreated back onto the operator's model at the end. A BatchLock
prevents colliding with a running `run_bench.py` (both force-recreate backend).

Usage:
  python bench/run_qa_bench.py                 # all models in qa_models.yaml
  python bench/run_qa_bench.py --models minimax-m3,qwen3-5-35b-a3b
  python bench/run_qa_bench.py --dry-run       # print plan, no docker/LLM calls
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from cortex_client import CortexClient, CortexError  # noqa: E402
from run_bench import (  # noqa: E402
    EnvFile, EnvSwapper, BatchLock, BatchLockError,
    recreate_backend, ENV_PATH, LOGS_DIR,
)
from qa_evaluator import judge_answers  # noqa: E402
import qa_snappiness as qs  # noqa: E402

BENCH_DIR = Path(__file__).parent
QA_MODELS_YAML = BENCH_DIR / "qa_models.yaml"
QA_MODELS_EXAMPLE = BENCH_DIR / "qa_models.yaml.example"
LOCAL_BACKEND = "http://localhost:8000"


def ensure_qa_models_config() -> None:
    if not QA_MODELS_YAML.exists() and QA_MODELS_EXAMPLE.exists():
        shutil.copy2(QA_MODELS_EXAMPLE, QA_MODELS_YAML)
        print(f"[init] Created {QA_MODELS_YAML.name} from {QA_MODELS_EXAMPLE.name}",
              file=sys.stderr)


def load_models(args, backup_cfg: dict) -> tuple[list[dict], str]:
    """Resolve the model list. base_url/api_key default to the operator's .env."""
    base_url, api_key = backup_cfg["base_url"], backup_cfg["api_key"]

    if args.models:
        ids = [m.strip() for m in args.models.split(",") if m.strip()]
        ctx = args.context or 198000
        models = [
            {"model_id": i, "base_url": base_url, "api_key": api_key, "context": ctx}
            for i in ids
        ]
        return models, (args.baseline or ids[0])

    ensure_qa_models_config()
    data = yaml.safe_load(QA_MODELS_YAML.read_text(encoding="utf-8")) or {}
    default_ctx = args.context or int(data.get("context", 198000))
    models: list[dict] = []
    for entry in data.get("models", []):
        if isinstance(entry, str):
            entry = {"model_id": entry}
        if not entry.get("model_id"):
            continue
        models.append({
            "model_id": entry["model_id"],
            "base_url": entry.get("base_url", base_url),
            "api_key": entry.get("api_key", api_key),
            "context": int(entry.get("context", default_ctx)),
        })
    if not models:
        raise SystemExit(f"No models found in {QA_MODELS_YAML}")
    baseline = args.baseline or (
        models[0]["model_id"] if data.get("first_is_baseline", True) else ""
    )
    return models, baseline


def apply_model_to_env(model: dict, *, reasoning_mode: str) -> None:
    """Rewrite .env for one chat model. Only primary-tier vars + context."""
    env = EnvFile.load(ENV_PATH)
    env.set("OPENAI_MODEL", model["model_id"])
    env.set("OPENAI_API_BASE", model["base_url"])
    env.set("OPENAI_API_KEY", model["api_key"])
    env.set("OPENAI_MAX_CONTEXT", str(model["context"]))
    if reasoning_mode != "leave":
        env.set("DEFAULT_REASONING_MODE", reasoning_mode)
    env.write()


def _write_results(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


async def judge_all(qa_by_run: dict, bank: list, cfg: dict) -> dict:
    """Judge each model in its OWN call (10 answers), then merge.

    One giant call scoring all model×question tuples overflows the judge's
    max_tokens and returns truncated JSON — zeroing every model's quality. Per
    model the output stays small, and one bad response only loses that model.
    """
    merged: dict = {"by_run": {}}
    for model_id, modes in qa_by_run.items():
        print(f"[judge] {model_id}…", file=sys.stderr)
        try:
            out = await judge_answers({model_id: modes}, bank, cfg, timeout_s=600)
            merged["by_run"].update(out.get("by_run", {}))
        except Exception as exc:  # noqa: BLE001 — never lose latency data
            print(f"[judge] {model_id} failed ({str(exc)[:160]}); quality stays 0.",
                  file=sys.stderr)
    return merged


def _print_leaderboard(rows: list[dict]) -> None:
    ranked = sorted(rows, key=lambda r: r["combined_score"], reverse=True)
    print("\n  rank  model                                      score  ttft_p50  total_p50  q/5  flag",
          file=sys.stderr)
    for i, r in enumerate(ranked, 1):
        print(f"  {i:>4}  {r['model']:42s} {r['combined_score']:>6}  "
              f"{r['ttft_p50_ms']:>7}ms  {r['total_p50_ms']:>8}ms  "
              f"{r['quality_overall']:>4}  {'OVERTHINK' if r['overthinking'] else ''}",
              file=sys.stderr)


async def rejudge(args) -> int:
    """Re-run the quality judge against a saved results JSON — no model re-runs.

    Reuses the saved per-model latency rows + raw answers; only recomputes the
    quality scores (per-model), re-scores the blend, and rewrites the report.
    """
    path = Path(args.rejudge)
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data["raw"]
    rows = data["models"]
    batch_id = data["batch_id"]
    budget_s = data.get("budget_s", 30.0)

    bank_path = LOGS_DIR / f"qa-chat-bank-{batch_id}.json"
    if bank_path.exists():
        bank = json.loads(bank_path.read_text(encoding="utf-8"))
    else:  # reconstruct from any model's answer records
        sample = next(iter(raw.values()))["speed"]
        bank = [{"id": a["question_id"], "question": a["question"],
                 "type": a.get("type", "")} for a in sample]

    env = EnvFile.load(ENV_PATH)
    cfg = {"model": env.get("OPENAI_MODEL") or "",
           "base_url": env.get("OPENAI_API_BASE") or "",
           "api_key": env.get("OPENAI_API_KEY") or ""}
    print(f"[rejudge] judging {len(rows)} models with `{cfg['model']}`…", file=sys.stderr)

    qa_by_run = {m: {"speed": v.get("speed", []), "quality": []} for m, v in raw.items()}
    judge_out = await judge_all(qa_by_run, bank, cfg)
    qs.apply_quality(rows, judge_out)
    qs.score_and_flag(rows, budget_s=budget_s)

    report = qs.build_report_md(
        rows, batch_id=batch_id, baseline_model=data.get("baseline", ""),
        budget_s=budget_s, hard_cap_s=data.get("hard_cap_s", 90),
        questions_count=len(bank), reasoning_mode=data.get("reasoning_mode", "leave"),
    )
    report_path = LOGS_DIR / f"qa-chat-report-{batch_id}.md"
    report_path.write_text(report, encoding="utf-8")
    data["models"] = rows
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"[rejudge] report → {report_path}", file=sys.stderr)
    _print_leaderboard(rows)
    return 0


async def run(args) -> int:
    batch_id = args.batch_id or datetime.now(timezone.utc).strftime("qa_%Y-%m-%d_%H-%M")

    # EnvSwapper backs up .env immediately and restores on exit / signal.
    swapper = EnvSwapper(ENV_PATH, batch_id)
    backup_cfg = swapper.primary_model_config()
    if not (backup_cfg["base_url"] and backup_cfg["api_key"] and backup_cfg["model"]):
        raise SystemExit("OPENAI_MODEL / OPENAI_API_BASE / OPENAI_API_KEY must be set in .env")
    admin_key = swapper.admin_key()
    models, baseline = load_models(args, backup_cfg)

    print(f"[plan] batch={batch_id}  models={len(models)}  questions={args.count}  "
          f"budget={args.budget}s  hard_cap={args.hard_cap}s  reasoning={args.reasoning_mode}",
          file=sys.stderr)
    for m in models:
        tag = "  (baseline)" if m["model_id"] == baseline else ""
        print(f"  - {m['model_id']:42s} ctx={m['context']:>7,d}{tag}", file=sys.stderr)

    if args.dry_run:
        print("[dry-run] no docker / no LLM calls. Exiting.", file=sys.stderr)
        return 0

    lock = BatchLock(batch_id)
    lock.acquire()

    cx = CortexClient(LOCAL_BACKEND, admin_key=admin_key, timeout=args.hard_cap + 60)
    rows: list[dict] = []
    qa_by_run: dict[str, dict] = {}
    results_path = LOGS_DIR / f"qa-chat-results-{batch_id}.json"

    try:
        # Preflight: there must be a graph to query.
        st = await cx.stats()
        ent = st.get("entity_count", 0) or 0
        if ent <= 0:
            raise SystemExit(
                f"Graph is empty (entity_count={ent}). Ingest a corpus before "
                f"running the Q+A chat benchmark."
            )
        print(f"[graph] {st.get('document_count')} docs · {ent} entities · "
              f"{st.get('relationship_count')} relationships · "
              f"{st.get('community_count')} communities", file=sys.stderr)

        # Fixed question bank (generated once from the graph, cached, reused).
        bank_path = LOGS_DIR / f"qa-chat-bank-{batch_id}.json"
        bank = await qs.generate_question_bank_from_graph(
            cx, backup_cfg, count=args.count, cache_path=bank_path,
        )

        for idx, m in enumerate(models, 1):
            print(f"\n[model {idx}/{len(models)}] {m['model_id']} "
                  f"(ctx={m['context']})", file=sys.stderr)
            apply_model_to_env(m, reasoning_mode=args.reasoning_mode)
            if args.no_recreate:
                await cx.wait_until_ready(timeout_s=30)
            else:
                recreate_backend()
                await cx.wait_until_ready(timeout_s=180)

            answers = await qs.run_snappiness_set(
                cx, bank, budget_s=args.budget, hard_cap_s=args.hard_cap,
                top_k=args.top_k,
            )
            rows.append(qs.aggregate_model(m["model_id"], m["context"], answers))
            qa_by_run[m["model_id"]] = {"speed": answers, "quality": []}
            # Persist incrementally so a crash/Ctrl-C keeps captured data.
            _write_results(results_path, {
                "batch_id": batch_id, "baseline": baseline,
                "budget_s": args.budget, "hard_cap_s": args.hard_cap,
                "reasoning_mode": args.reasoning_mode,
                "models": rows, "raw": qa_by_run,
            })

        # Quality judging — per model (operator's baseline model as judge).
        if not args.skip_judge:
            print("\n[judge] scoring answers…", file=sys.stderr)
            judge_out = await judge_all(qa_by_run, bank, backup_cfg)
            qs.apply_quality(rows, judge_out)

        qs.score_and_flag(rows, budget_s=args.budget)

        report = qs.build_report_md(
            rows, batch_id=batch_id, baseline_model=baseline,
            budget_s=args.budget, hard_cap_s=args.hard_cap,
            questions_count=len(bank), reasoning_mode=args.reasoning_mode,
        )
        report_path = LOGS_DIR / f"qa-chat-report-{batch_id}.md"
        report_path.write_text(report, encoding="utf-8")
        _write_results(results_path, {
            "batch_id": batch_id, "baseline": baseline,
            "budget_s": args.budget, "hard_cap_s": args.hard_cap,
            "reasoning_mode": args.reasoning_mode,
            "models": rows, "raw": qa_by_run,
        })

        print(f"\n[done] report → {report_path}", file=sys.stderr)
        print(f"[done] results → {results_path}", file=sys.stderr)
        _print_leaderboard(rows)
    finally:
        await cx.aclose()
        swapper.restore()
        if not args.no_recreate:
            print("[env] recreating backend back onto operator's model…", file=sys.stderr)
            try:
                recreate_backend()
            except Exception as exc:  # noqa: BLE001
                print(f"[env] WARNING: could not recreate backend: {exc}", file=sys.stderr)
        lock.release()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--models", help="Comma-separated model ids (overrides qa_models.yaml)")
    p.add_argument("--baseline", help="Model id to mark as the current/prod baseline")
    p.add_argument("--context", type=int, default=0,
                    help="Pin OPENAI_MAX_CONTEXT for every model (default: from yaml / 198000)")
    p.add_argument("--count", type=int, default=12, help="Question bank size (default 12)")
    p.add_argument("--budget", type=float, default=60.0,
                    help="Snappy-chat budget in seconds; slower-but-complete = over_budget (default 60)")
    p.add_argument("--hard-cap", type=float, default=180.0, dest="hard_cap",
                    help="Per-question transport read timeout in seconds (default 180)")
    p.add_argument("--top-k", type=int, default=5, dest="top_k", help="Retrieval top_k (default 5)")
    p.add_argument("--reasoning-mode", default="leave",
                    choices=["leave", "off", "auto", "on"],
                    help="Set DEFAULT_REASONING_MODE for all models, or leave .env as-is (default)")
    p.add_argument("--skip-judge", action="store_true", help="Skip the quality judge call")
    p.add_argument("--no-recreate", action="store_true",
                    help="Don't docker-recreate between models (testing the wiring only)")
    p.add_argument("--batch-id", help="Override batch id (default qa_<utc-timestamp>)")
    p.add_argument("--dry-run", action="store_true", help="Print the plan and exit")
    p.add_argument("--rejudge", metavar="RESULTS_JSON",
                    help="Re-run the quality judge against a saved results JSON "
                         "(no model re-runs) and rewrite the report")
    args = p.parse_args()
    try:
        if args.rejudge:
            return asyncio.run(rejudge(args))
        return asyncio.run(run(args))
    except BatchLockError as exc:
        print(f"[batch] aborted — {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("[batch] interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
