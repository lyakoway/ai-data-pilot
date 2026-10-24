"""Extended Text-to-SQL evaluation — quality metrics beyond scripts/evaluate.py.

Reuses the same golden set (tests/golden/sql_golden.jsonl, 50 cases) and the
same mini-pipeline (plan → execute → self-correction, via the very same
`_generate_plan` / `_repair_plan` functions the Atlas agent uses) as
evaluate.py, so result_accuracy / sql_execution_accuracy stay directly
comparable with earlier artifacts. Adds:

  - Exact Match SQL (normalized string equality — harsh, reported for
    completeness, not used as the headline)
  - share of timeouts / guard rejections / no-SQL refusals
  - LLM calls per task and SQL repair statistics
  - per-category breakdown
  - a reproducibility manifest (model version as reported by the API, prompt
    hash, golden-set hash, git rev, generation params)

Usage (from backend/):
    python scripts/eval_sql_full.py --model zai:glm-4.6
    python scripts/eval_sql_full.py --model zai:glm-5.2 --limit 10
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import evaluate as base  # noqa: E402

from app.agents.atlas import (  # noqa: E402
    MAX_SQL_REPAIR_ROUNDS,
    PLAN_SYSTEM,
    _dialect_note,
    _generate_plan,
    _repair_plan,
)
from app.core.sql_guard import SqlExecutionError, SqlGuardError, SqlTimeoutError  # noqa: E402
from app.llm.registry import get_provider  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = BACKEND_DIR / "bench_results"


def git_rev() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True,
            text=True, cwd=BACKEND_DIR.parent, timeout=5,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def normalize_sql(sql: str) -> str:
    """Normalization for Exact Match: lowercase, collapse whitespace, strip ';'.
    Deliberately minimal — aliases/format differences still count as mismatch."""
    return re.sub(r"\s+", " ", sql.strip().rstrip(";").lower())


def classify_harness_error(err: str) -> str:
    """Classify infra-level failures so they never masquerade as model quality.
    Model-quality failure kinds: no_sql / guard / timeout / execution.
    Infra kinds: infra_timeout (client-side SDK timeout), rate_limit (HTTP 429),
    infra_error (anything else raised outside the pipeline)."""
    e = (err or "").lower()
    if "timed out" in e or "timeout" in e:
        return "infra_timeout"
    if "429" in e or "rate limit" in e:
        return "rate_limit"
    return "infra_error"


def _lib_version(name: str) -> str:
    try:
        from importlib import metadata
        return metadata.version(name)
    except Exception:  # noqa: BLE001
        return "unknown"


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (k successes of n)."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * (p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5 / denom
    return (round(max(0.0, centre - half) * 100, 1), round(min(1.0, centre + half) * 100, 1))


def pct(n: int, d: int) -> float:
    return round(n / d * 100, 1) if d else 0.0


def pctl(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * p / 100), len(ordered) - 1)]


async def probe_model_version(model_id: str) -> dict[str, Any]:
    """One-token completion to record the model version the API reports."""
    from dotenv import dotenv_values
    from openai import AsyncOpenAI

    cfg = dotenv_values(BACKEND_DIR / ".env")
    key = cfg.get("ZAI_API_KEY") or ""
    if not key or not model_id.startswith("zai:"):
        return {"model_id": model_id, "reported": None}
    client = AsyncOpenAI(api_key=key, base_url="https://api.z.ai/api/paas/v4", timeout=60.0)
    api_model = model_id.split(":", 1)[1]
    try:
        r = await client.chat.completions.create(
            model=api_model, messages=[{"role": "user", "content": "ping"}],
            max_tokens=4, extra_body={"thinking": {"type": "disabled"}},
        )
        return {"model_id": model_id, "reported": r.model}
    except Exception:  # noqa: BLE001 — model may always think; retry without the flag
        try:
            r = await client.chat.completions.create(
                model=api_model, messages=[{"role": "user", "content": "ping"}], max_tokens=8,
            )
            return {"model_id": model_id, "reported": r.model}
        except Exception as e:  # noqa: BLE001
            return {"model_id": model_id, "reported": None, "probe_error": str(e)[:120]}


async def run_case(provider, question: str, schema: str, engine, datasource_id: str) -> dict[str, Any]:
    """Mirror of evaluate.run_sql_case that also records the generated SQL and
    the exact number of LLM calls. Same retry semantics as the Atlas agent."""
    t0 = time.perf_counter()
    llm_calls = 1
    plan = await _generate_plan(provider, question, "ru", schema, allow_mock=False,
                                datasource_id=datasource_id)
    attempts = 0
    executed = False
    error: str | None = None
    err_kind: str | None = None
    rows: list[list[Any]] = []
    columns: list[str] = []
    final_sql: str | None = None
    timeout = guard = no_sql = False

    if not plan or not plan.get("sql"):
        no_sql = True
        return {
            "executed": False, "attempts": 0, "llm_calls": llm_calls,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "error": "model returned no SQL", "err_kind": "no_sql",
            "columns": [], "rows": [], "final_sql": None,
            "tables_used": [], "timeout": False, "guard": False, "no_sql": True,
        }

    for attempt in range(MAX_SQL_REPAIR_ROUNDS + 1):
        attempts += 1
        final_sql = plan["sql"]
        try:
            columns, rows = base.run_and_normalize(engine, plan["sql"])
            executed = True
            error = None
            break
        except SqlGuardError as e:
            error, guard = f"guard: {e}", True
            break  # never retried — by design
        except SqlTimeoutError as e:
            error, timeout = f"timeout: {e}", True
            break
        except SqlExecutionError as e:
            error = str(e)[:200]
            if attempt >= MAX_SQL_REPAIR_ROUNDS:
                break
            llm_calls += 1
            repaired = await _repair_plan(provider, question, plan["sql"], error, "ru", schema)
            if repaired and repaired.get("sql") and repaired["sql"] != plan["sql"]:
                plan = repaired
                continue
            break

    if timeout:
        err_kind = "timeout"
    elif guard:
        err_kind = "guard"
    elif not executed:
        err_kind = "execution" if attempts else "no_sql"

    return {
        "executed": executed, "attempts": attempts, "llm_calls": llm_calls,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
        "error": error, "err_kind": err_kind,
        "columns": columns, "rows": rows, "final_sql": final_sql,
        "tables_used": (plan or {}).get("tables_used") or [],
        "timeout": timeout, "guard": guard, "no_sql": no_sql,
    }


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    provider = get_provider(args.model)
    if provider.provider == "mock":
        raise SystemExit(
            f"Provider for {args.model!r} resolved to mock (no API key?) — "
            "SQL numbers would be meaningless. Aborting."
        )

    from app.db.datasources import (
        ALL_UPLOADS_ID,
        RIDEGO_SOURCE_ID,
        delete_source,
        get_engine_for,
        get_schema_catalog,
        ingest_csv,
    )

    golden_path = Path(args.golden)
    cases = base.load_jsonl(golden_path)
    if args.only:
        wanted = {c.strip() for c in args.only.split(",") if c.strip()}
        cases = [c for c in cases if c["id"] in wanted]
    elif args.limit:
        cases = cases[: args.limit]

    # Cross-source fixtures (identical to evaluate.py).
    created: list[str] = []
    markers: dict[str, str] = {}
    if any(c.get("prepare") for c in cases):
        preps: dict[str, str] = {}
        for c in cases:
            for prep in c.get("prepare", []):
                if prep["name"] not in preps:
                    meta = ingest_csv(prep["name"] + ".csv", prep["csv"])
                    preps[prep["name"]] = meta["table_name"]
                    created.append(meta["id"])
        markers = {f"{{{{{n}}}}}": t for n, t in preps.items()}

    engines = {
        RIDEGO_SOURCE_ID: (get_schema_catalog(RIDEGO_SOURCE_ID), get_engine_for(RIDEGO_SOURCE_ID)),
        ALL_UPLOADS_ID: (get_schema_catalog(ALL_UPLOADS_ID), get_engine_for(ALL_UPLOADS_ID)),
    }

    rows_out: list[dict[str, Any]] = []
    t_suite = time.perf_counter()
    for i, case in enumerate(cases, 1):
        ds = ALL_UPLOADS_ID if case.get("prepare") else RIDEGO_SOURCE_ID
        schema, engine = engines[ds]
        ref_sql = case["reference_sql"]
        for marker, table in markers.items():
            ref_sql = ref_sql.replace(marker, table)

        row: dict[str, Any] = {
            "id": case["id"], "category": case["category"], "question": case["question"],
            "executed": False, "result_match": None, "exact_match": None,
            "llm_calls": 0, "attempts": 0, "repaired": False,
            "err_kind": None, "latency_ms": 0, "error": None, "generated_sql": None,
        }
        try:
            expected_cols, expected_rows = base.run_and_normalize(engine, ref_sql)
            expected_set = base.normalize_rows(expected_rows)
            run = await run_case(provider, case["question"], schema, engine, ds)
            actual_set = base.normalize_rows(run["rows"])
            row.update({
                "executed": run["executed"],
                "attempts": run["attempts"],
                "repaired": run["attempts"] > 1,
                "llm_calls": run["llm_calls"],
                "latency_ms": run["latency_ms"],
                "error": (run["error"] or "")[:160] or None,
                "err_kind": run["err_kind"],
                "generated_sql": (run["final_sql"] or "")[:500] or None,
            })
            if case.get("check", "result") == "result":
                row["result_match"] = run["executed"] and actual_set == expected_set
            if run["final_sql"]:
                row["exact_match"] = normalize_sql(run["final_sql"]) == normalize_sql(ref_sql)
            rows_out.append(row)
            mark = ("MATCH" if row["result_match"] else "diff ") if row["result_match"] is not None else "exec "
            print(f"  [{i:>2}/{len(cases)}] {'EXEC' if run['executed'] else 'FAIL'} {mark} "
                  f"calls={run['llm_calls']} {run['latency_ms']:>5}ms {case['id']}", flush=True)
        except Exception as e:  # noqa: BLE001 — infra failures (SDK timeout, 429, network)
            row["error"] = f"harness: {str(e)[:160]}"
            row["err_kind"] = classify_harness_error(str(e))
            rows_out.append(row)
            print(f"  [{i:>2}/{len(cases)}] INFRA [{row['err_kind']}] {case['id']}: {str(e)[:80]}", flush=True)

    for sid in created:
        try:
            delete_source(sid)
        except Exception:  # noqa: BLE001
            pass
    suite_wall_s = round(time.perf_counter() - t_suite, 1)

    # ------- aggregate -------
    # Infra failures (client timeouts / 429 / network) are counted separately
    # from model-quality failures and NEVER silently folded into them.
    n = len(rows_out)
    infra = [r for r in rows_out if (r["err_kind"] or "").startswith("infra") or r["err_kind"] == "rate_limit"]
    quality_rows = [r for r in rows_out if r not in infra]
    executed = [r for r in rows_out if r["executed"]]
    checked = [r for r in rows_out if r["result_match"] is not None]
    matched = [r for r in checked if r["result_match"]]
    with_sql = [r for r in rows_out if r["exact_match"] is not None]
    exact = [r for r in rows_out if r["exact_match"]]
    repaired = [r for r in rows_out if r["repaired"]]
    repaired_ok = [r for r in repaired if r["executed"]]
    attempted = [r for r in rows_out if r["llm_calls"] > 0]
    lat = [r["latency_ms"] for r in rows_out if r["llm_calls"] > 0]

    by_cat: dict[str, dict[str, Any]] = {}
    for cat in sorted({r["category"] for r in rows_out}):
        sub = [r for r in rows_out if r["category"] == cat]
        sub_checked = [r for r in sub if r["result_match"] is not None]
        sub_matched = sum(1 for r in sub_checked if r["result_match"])
        by_cat[cat] = {
            "n": len(sub),
            "n_executed": sum(1 for r in sub if r["executed"]),
            "n_result_checked": len(sub_checked),
            "n_result_match": sub_matched,
            "result_accuracy": pct(sub_matched, len(sub_checked)) if sub_checked else None,
            "n_infra": sum(1 for r in sub if r in infra),
        }

    version = await probe_model_version(args.model)
    schema_ridego = get_schema_catalog(RIDEGO_SOURCE_ID)
    lo, hi = wilson_ci(len(matched), len(checked))
    manifest = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "git_rev": git_rev(),
        "env": {
            "python": sys.version.split()[0],
            "openai_lib": _lib_version("openai"),
            "httpx_lib": _lib_version("httpx"),
        },
        "golden_set_path": str(args.golden),
        "model": version,
        "generation_params": {
            "temperature_complete": 0.1,
            "max_tokens": "provider default (not set by app)",
            "thinking_mode": "provider default (GLM-5.x think by default; the app does not disable thinking)",
            "note": "params are hardcoded in app/llm/providers.py — unchanged for this eval",
        },
        "prompt": {
            "plan_system_sha256": sha256(PLAN_SYSTEM.format(dialect_note=_dialect_note("ridego"))),
            "schema_catalog_sha256": sha256(schema_ridego),
        },
        "golden_set": {
            "path": str(Path(args.golden).relative_to(BACKEND_DIR)) if str(args.golden).startswith(str(BACKEND_DIR)) else str(args.golden),
            "n_cases": n,
            "sha256": sha256(Path(args.golden).read_text()),
        },
    }

    summary = {
        "model": args.model,
        "n": n,
        "n_infra_failures": len(infra),
        "infra_failure_share": pct(len(infra), n),
        "infra_kinds": {k: sum(1 for r in infra if r["err_kind"] == k) for k in {r["err_kind"] for r in infra}},
        "sql_execution_accuracy": pct(len(executed), n),
        "sql_execution_accuracy_excl_infra": pct(
            sum(1 for r in quality_rows if r["executed"]), len(quality_rows)
        ) if quality_rows else None,
        "result_accuracy": pct(len(matched), len(checked)),
        "result_accuracy_n_checked": len(checked),
        "result_accuracy_wilson95": [lo, hi],
        "exact_match_sql": pct(len(exact), len(with_sql)),
        "exact_match_n_with_sql": len(with_sql),
        "self_correction_rate": pct(len(repaired_ok), len(repaired)) if repaired else None,
        "repaired_cases": len(repaired),
        "avg_llm_calls_per_attempted_task": round(
            statistics.mean([r["llm_calls"] for r in attempted]), 2
        ) if attempted else 0,
        "timeout_share": pct(sum(1 for r in quality_rows if r["err_kind"] == "timeout"), n),
        "guard_reject_share": pct(sum(1 for r in quality_rows if r["err_kind"] == "guard"), n),
        "no_sql_share": pct(sum(1 for r in quality_rows if r["err_kind"] == "no_sql"), n),
        "task_completion_rate": pct(len(executed), n),
        "latency_ms": {
            "p50": pctl(lat, 50), "p95": pctl(lat, 95),
            "p99": pctl(lat, 99) if len(lat) >= 20 else None,
            "mean": round(statistics.mean(lat)) if lat else 0,
            "n_attempted": len(lat),
        },
        "by_category": by_cat,
        "suite_wall_s": suite_wall_s,
        "notes": [
            "p50/p95/p99 — nearest-rank (lower); n<20 → p99 omitted",
            "completion latency includes thinking phase (app path does not disable it)",
        ],
    }
    return {"summary": summary, "manifest": manifest, "details": rows_out}


def main() -> None:
    parser = argparse.ArgumentParser(description="Extended SQL evaluation")
    parser.add_argument("--model", default="zai:glm-4.6")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--only", default=None, help="comma-separated case ids to (re)run, e.g. sql_009,sql_010")
    parser.add_argument("--golden", default=str(base.GOLDEN_DIR / "sql_golden.jsonl"), help="golden set jsonl (v1 default; v2 = sql_golden_v2.jsonl)")
    args = parser.parse_args()

    result = asyncio.run(main_async(args))
    s = result["summary"]
    print("\n" + "=" * 64)
    print(f"SQL EVAL — {s['model']} (n={s['n']}, golden={result['manifest']['golden_set']['path']})")
    print("=" * 64)
    print(f"  SQL Execution Accuracy : {s['sql_execution_accuracy']}%"
          + (f"  (excl. infra: {s['sql_execution_accuracy_excl_infra']}%)" if s["n_infra_failures"] else ""))
    print(f"  Result Accuracy        : {s['result_accuracy']}%  (checked n={s['result_accuracy_n_checked']}, "
          f"Wilson95 {s['result_accuracy_wilson95'][0]}–{s['result_accuracy_wilson95'][1]}%)")
    print(f"  Exact Match SQL        : {s['exact_match_sql']}%  (n with SQL={s['exact_match_n_with_sql']})")
    print(f"  Self-Correction Rate   : {s['self_correction_rate']}%  ({s['repaired_cases']} repaired)")
    print(f"  avg LLM calls/task     : {s['avg_llm_calls_per_attempted_task']} (attempted only)")
    if s["n_infra_failures"]:
        print(f"  INFRA FAILURES         : {s['n_infra_failures']} ({s['infra_kinds']}) — "
              "excluded from quality shares; rerun with --only before headline numbers")
    print(f"  timeouts {s['timeout_share']}% | guard {s['guard_reject_share']}% | no-SQL {s['no_sql_share']}%")
    print(f"  latency p50/p95/p99 ms : {s['latency_ms']['p50']}/{s['latency_ms']['p95']}/{s['latency_ms']['p99']}"
          f" (n attempted={s['latency_ms']['n_attempted']})")
    print("  by category (n | exec | result match/checked):")
    for cat, m in s["by_category"].items():
        print(f"    {cat:<14} n={m['n']:>2} exec={m['n_executed']:>2} "
              f"result={m['n_result_match']}/{m['n_result_checked']}"
              + (f" ({m['result_accuracy']}%)" if m['result_accuracy'] is not None else "")
              + (f" infra={m['n_infra']}" if m['n_infra'] else ""))

    OUT_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    model_tag = args.model.replace(":", "_")
    set_tag = "v2" if "v2" in str(args.golden) else "v1"
    json_path = OUT_DIR / f"sql_eval_{model_tag}_{set_tag}_{stamp}.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_path = OUT_DIR / f"sql_eval_{model_tag}_{set_tag}_{stamp}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(result["details"][0].keys()))
        writer.writeheader()
        writer.writerows(result["details"])
    print(f"\nSaved → {json_path}\n       → {csv_path}")


if __name__ == "__main__":
    main()
