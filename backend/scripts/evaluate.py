"""LLM evaluation harness for AI Data Pilot — manual run with a real model.

Measures what deterministic pytest cannot: quality of LLM-generated SQL,
routing decisions, self-correction and end-to-end latency.

Usage (from backend/):
    python scripts/evaluate.py --suite all --model zai:glm-4.6
    python scripts/evaluate.py --suite sql --model openai:gpt-4o-mini --limit 10

Suites:
    sql      — SQL Execution/Result Accuracy, Self-Correction Rate, Task
               Completion, p50/p95 latency (tests/golden/sql_golden.jsonl)
    routing  — Agent Routing Accuracy (tests/golden/routing_golden.jsonl)

Requires a working model (API key / running Ollama). Demo provider is allowed
for plumbing checks but produces meaningless SQL numbers.
Results are written to evaluation_results.json (gitignored).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.oleg import MAX_SQL_REPAIR_ROUNDS, _generate_plan, _repair_plan  # noqa: E402
from app.core.sql_guard import SqlExecutionError, SqlGuardError, SqlTimeoutError  # noqa: E402
from app.db.datasources import get_engine_for, get_schema_catalog  # noqa: E402
from app.llm.base import ChatMessage  # noqa: E402
from app.llm.registry import get_provider  # noqa: E402

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "tests" / "golden"
RESULTS_PATH = Path(__file__).resolve().parent.parent / "evaluation_results.json"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def normalize_rows(rows: list[list[Any]]) -> set[tuple[str, ...]]:
    """Rows → set of string tuples: order- and float-format-insensitive matching."""
    out: set[tuple[str, ...]] = set()
    for row in rows:
        cells = []
        for v in row:
            if v is None:
                cells.append("")
            elif isinstance(v, float):
                cells.append(f"{round(v, 2):g}")
            else:
                cells.append(str(v))
        out.add(tuple(cells))
    return out


async def run_sql_case(
    provider, question: str, lang: str, schema_catalog: str, engine, datasource_id: str, allow_mock: bool
) -> dict[str, Any]:
    """Mini linear pipeline: plan → execute → self-correction (mirrors Oleg)."""
    t0 = time.perf_counter()
    plan = await _generate_plan(provider, question, lang, schema_catalog, allow_mock=allow_mock,
                                datasource_id=datasource_id)
    attempts = 0
    executed = False
    error: str | None = None
    rows: list[list[Any]] = []
    columns: list[str] = []

    if plan and plan.get("sql"):
        for attempt in range(MAX_SQL_REPAIR_ROUNDS + 1):
            attempts += 1
            try:
                result = run_and_normalize(engine, plan["sql"])
                columns, rows = result
                executed = True
                error = None
                break
            except SqlGuardError as e:
                error = f"guard: {e}"
                break  # never retried — by design
            except SqlTimeoutError as e:
                error = f"timeout: {e}"
                break
            except SqlExecutionError as e:
                error = str(e)[:200]
                if attempt >= MAX_SQL_REPAIR_ROUNDS:
                    break
                repaired = await _repair_plan(
                    provider, question, plan["sql"], error, lang, schema_catalog
                )
                if repaired and repaired.get("sql") and repaired["sql"] != plan["sql"]:
                    plan = repaired
                    continue
                break

    return {
        "executed": executed,
        "attempts": attempts,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
        "error": error,
        "columns": columns,
        "rows": rows,
        "tables_used": (plan or {}).get("tables_used") or [],
    }


def run_and_normalize(engine, sql: str) -> tuple[list[str], list[list[Any]]]:
    from app.core.sql_guard import sanitize_sql

    safe = sanitize_sql(sql)
    with engine.connect() as conn:
        from sqlalchemy import text

        result = conn.execute(text(safe))
        columns = list(result.keys())
        rows = [list(r) for r in result.fetchall()]
    return columns, rows


async def eval_sql_suite(
    model_id: str, limit: int | None,
) -> dict[str, Any]:
    from app.db.datasources import ALL_UPLOADS_ID, RIDEGO_SOURCE_ID, get_schema_catalog, ingest_csv, get_source_meta, delete_source

    provider = get_provider(model_id)
    if provider.provider == "mock":
        print("WARNING: mock provider — SQL numbers are a plumbing check only.\n")
    cases = load_jsonl(GOLDEN_DIR / "sql_golden.jsonl")
    if limit:
        cases = cases[:limit]

    # --- Cross-source preparation: deterministic CSV sources, created once ---
    created_sources: list[str] = []
    markers: dict[str, str] = {}
    needs_cross = any(c.get("prepare") for c in cases)
    if needs_cross:
        preps: dict[str, str] = {}
        for c in cases:
            for prep in c.get("prepare", []):
                if prep["name"] not in preps:
                    meta = ingest_csv(prep["name"] + ".csv", prep["csv"])
                    preps[prep["name"]] = meta["table_name"]
                    created_sources.append(meta["id"])
        markers = {f"{{{{{name}}}}}": table for name, table in preps.items()}

    engines = {
        RIDEGO_SOURCE_ID: (get_schema_catalog(RIDEGO_SOURCE_ID), get_engine_for(RIDEGO_SOURCE_ID)),
        ALL_UPLOADS_ID: (get_schema_catalog(ALL_UPLOADS_ID), get_engine_for(ALL_UPLOADS_ID)),
    }

    results: list[dict[str, Any]] = []
    for i, case in enumerate(cases, 1):
        try:
            case_ds = ALL_UPLOADS_ID if case.get("prepare") else RIDEGO_SOURCE_ID
            case_schema, case_engine = engines[case_ds]

            ref_sql = case["reference_sql"]
            for marker, table in markers.items():
                ref_sql = ref_sql.replace(marker, table)
            check = case.get("check", "result")

            expected_cols, expected_rows = run_and_normalize(case_engine, ref_sql)
            expected_set = normalize_rows(expected_rows)

            run = await run_sql_case(
                provider, case["question"], "ru", case_schema, case_engine,
                case_ds, allow_mock=(provider.provider == "mock"),
            )
            actual_set = normalize_rows(run["rows"])
            executed = run["executed"]

            if check == "result":
                result_match: bool | None = executed and actual_set == expected_set
            else:
                result_match = None  # execution-only (ambiguous cases)

            results.append({
                "id": case["id"],
                "category": case["category"],
                "question": case["question"],
                "executed": executed,
                "result_match": result_match,
                "attempts": run["attempts"],
                "latency_ms": run["latency_ms"],
                "schema_match": sorted(set(t.lower() for t in run["tables_used"]))
                == sorted(t.lower() for t in case["expected_tables"]),
                "error": (run["error"] or "")[:160] if not executed else None,
                "_expected_rows": len(expected_set),
                "_actual_rows": len(actual_set),
            })
            status = "EXEC" if executed else "FAIL"
            mark = ("MATCH" if result_match else "diff ") if result_match is not None else "exec "
            print(f"  [{i:>2}/{len(cases)}] {status} {mark} attempts={run['attempts']} "
                  f"{run['latency_ms']:>5}ms  {case['id']} {case['question'][:40]}")
        except Exception as e:  # noqa: BLE001 — a broken case must not kill the suite
            results.append({
                "id": case.get("id", "?"), "category": case.get("category", "?"),
                "question": case.get("question", "?"), "executed": False,
                "result_match": False, "attempts": 0, "latency_ms": 0,
                "schema_match": False, "error": f"harness error: {e}"[:160],
            })
            print(f"  [{i:>2}/{len(cases)}] FAIL harness: {e}"[:120])

    executed_n = sum(1 for r in results if r["executed"])
    matched = [r for r in results if r["result_match"] is not None]
    matched_n = sum(1 for r in matched if r["result_match"])
    repaired = [r for r in results if r["attempts"] > 1]
    repaired_ok = sum(1 for r in repaired if r["executed"])
    latencies = sorted(r["latency_ms"] for r in results)

    def pct(n: int, d: int) -> float:
        return round(n / d * 100, 1) if d else 0.0

    def pctl(p: float) -> float:
        if not latencies:
            return 0.0
        idx = min(int(len(latencies) * p / 100), len(latencies) - 1)
        return latencies[idx]

    # --- Cleanup cross-source fixtures created for this run ---
    for sid in created_sources:
        try:
            delete_source(sid)
        except Exception:  # noqa: BLE001
            pass

    return {
        "suite": "sql",
        "model": model_id,
        "n": len(results),
        "sql_execution_accuracy": pct(executed_n, len(results)),
        "result_accuracy": pct(matched_n, len(matched)),
        "self_correction_rate": pct(repaired_ok, len(repaired)) if repaired else None,
        "repaired_cases": len(repaired),
        "task_completion_rate": pct(executed_n, len(results)),
        "schema_accuracy": pct(sum(1 for r in results if r["schema_match"]), len(results)),
        "latency_p50_ms": pctl(50),
        "latency_p95_ms": pctl(95),
        "details": results,
    }


async def eval_routing_suite(model_id: str, limit: int | None) -> dict[str, Any]:
    from app.agents.router import route_agent

    provider = get_provider(model_id)
    if provider.provider == "mock":
        print("NOTE: mock provider — routing uses the deterministic heuristic.\n")
    cases = load_jsonl(GOLDEN_DIR / "routing_golden.jsonl")
    if limit:
        cases = cases[:limit]

    results: list[dict[str, Any]] = []
    for i, case in enumerate(cases, 1):
        t0 = time.perf_counter()
        got = await route_agent(case["question"], model_id=model_id)
        results.append({
            "id": case["id"],
            "question": case["question"],
            "expected": case["expected_agent"],
            "got": got,
            "match": got == case["expected_agent"],
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        })
        mark = "OK " if results[-1]["match"] else "MISS"
        print(f"  [{i:>2}/{len(cases)}] {mark} {got:<8} {case['question'][:50]}")

    correct = sum(1 for r in results if r["match"])
    accuracy = round(correct / len(results) * 100, 1) if results else 0.0
    return {
        "suite": "routing",
        "model": model_id,
        "n": len(results),
        "routing_accuracy": accuracy,
        "details": results,
    }


def print_report(suites: list[dict[str, Any]]) -> None:
    print("\n" + "=" * 64)
    print("EVALUATION REPORT")
    print("=" * 64)
    for s in suites:
        print(f"\n— {s['suite']} suite (model: {s['model']}, n={s['n']})")
        if s["suite"] == "sql":
            print(f"  SQL Execution Accuracy : {s['sql_execution_accuracy']}%")
            print(f"  Result Accuracy        : {s['result_accuracy']}%")
            if s.get("self_correction_rate") is not None:
                print(f"  Self-Correction Rate   : {s['self_correction_rate']}% "
                      f"({s['repaired_cases']} repaired cases)")
            print(f"  Task Completion Rate   : {s['task_completion_rate']}%")
            print(f"  Latency p50 / p95      : {s['latency_p50_ms']}ms / {s['latency_p95_ms']}ms")
        else:
            print(f"  Agent Routing Accuracy : {s['routing_accuracy']}%")
            misses = [d for d in s["details"] if not d["match"]]
            if misses:
                print("  Misses:")
                for m in misses:
                    print(f"    ✗ {m['question'][:50]} → {m['got']} (expected {m['expected']})")


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM evaluation harness")
    parser.add_argument("--suite", choices=["sql", "routing", "all"], default="all")
    parser.add_argument("--model", default="zai:glm-4.6")
    parser.add_argument("--limit", type=int, default=None, help="cap cases per suite (debug)")
    args = parser.parse_args()

    async def run() -> list[dict[str, Any]]:
        suites = []
        if args.suite in ("sql", "all"):
            print(f"SQL suite: {args.model}")
            suites.append(await eval_sql_suite(args.model, args.limit))
        if args.suite in ("routing", "all"):
            print(f"Routing suite: {args.model}")
            suites.append(await eval_routing_suite(args.model, args.limit))
        return suites

    suites = asyncio.run(run())
    print_report(suites)
    RESULTS_PATH.write_text(
        json.dumps({"model": args.model, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "suites": suites}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nResults saved → {RESULTS_PATH}")


if __name__ == "__main__":
    main()
