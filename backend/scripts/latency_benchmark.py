"""Latency benchmark across models — замер по разным моделям, как в RAG Chat.

Прогоняет фиксированный golden-вопрос через каждую доступную модель и замеряет:
  - plan latency (LLM генерирует SQL)
  - execution latency (реальное выполнение запроса в БД)
  - answer latency (LLM оформляет текст)
  - total (end-to-end)

Usage (from backend/):
    python scripts/latency_benchmark.py                          # все доступные модели
    python scripts/latency_benchmark.py --models zai:glm-5.3-flash,zai:glm-4.6
    python scripts/latency_benchmark.py --runs 2                 # по 2 прогона на модель

Вывод: markdown-таблица (готова для README) + latency_benchmark.json.
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

from app.core.sql_guard import SqlExecutionError, SqlGuardError, SqlTimeoutError  # noqa: E402
from app.db.datasources import get_schema_catalog  # noqa: E402
from app.llm.base import ChatMessage  # noqa: E402
from app.llm.registry import get_provider, list_models  # noqa: E402
from app.agents.oleg import PLAN_SYSTEM, _extract_json  # noqa: E402

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "tests" / "golden"
OUT_JSON = Path(__file__).resolve().parent.parent / "latency_benchmark.json"
DEFAULT_QUESTION = "Сколько всего поездок в базе?"


def load_reference() -> str:
    """Reference SQL первого golden-кейса — для проверки корректности выполнения."""
    case = load_jsonl(GOLDEN_DIR / "sql_golden.jsonl")[0]
    return case["reference_sql"]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


async def bench_model(model_id: str, question: str, reference_sql: str, runs: int) -> dict[str, Any]:
    """Прогон одного вопроса через полный цикл Олега на одной модели, runs раз."""
    from app.db.datasources import RIDEGO_SOURCE_ID, get_schema_catalog, get_engine_for

    provider = get_provider(model_id)
    schema_catalog = get_schema_catalog("ridego")
    engine = get_engine_for("ridego")

    plan_ms: list[float] = []
    exec_ms: list[float] = []
    answer_ms: list[float] = []
    executed_ok = 0
    sql_match = 0

    for run_i in range(runs):
        # --- Plan (LLM генерирует SQL) ---
        t0 = time.perf_counter()
        raw = await provider.complete(
            PLAN_SYSTEM + schema_catalog,
            [ChatMessage("user", question)],
            lang="ru",
        )
        plan_ms.append((time.perf_counter() - t0) * 1000)
        plan = _extract_json(raw)

        # --- Execution (БД) ---
        if not plan or not plan.get("sql"):
            exec_ms.append(0.0)
            continue
        t1 = time.perf_counter()
        try:
            from sqlalchemy import text

            with engine.connect() as conn:
                result = conn.execute(text(f"SELECT * FROM ({plan['sql']}) AS _q LIMIT 500"))
                rows = result.fetchall()
            exec_ms.append((time.perf_counter() - t1) * 1000)
            executed_ok += 1
            # SQL match: результат совпал с эталонным запросом (по количеству строк)
            ref = conn.execute(text(f"SELECT * FROM ({reference_sql}) AS _q LIMIT 500"))
            if len(rows) == len(ref.fetchall()):
                sql_match += 1
        except Exception:  # noqa: BLE001
            exec_ms.append(0.0)

        # --- Answer (LLM оформляет текст) ---
        t2 = time.perf_counter()
        await provider.complete(
            "Ты — аналитик. Короткий ответ по данным в одном предложении.",
            [ChatMessage("user", f"Вопрос: {question}")],
            lang="ru",
        )
        answer_ms.append((time.perf_counter() - t2) * 1000)

    def med(values: list[float]) -> float:
        values = [v for v in values if v > 0]
        return round(statistics.median(values), 0) if values else 0.0

    return {
        "model": model_id,
        "provider": provider.provider,
        "runs": runs,
        "plan_p50_ms": med(plan_ms),
        "exec_p50_ms": med(exec_ms),
        "answer_p50_ms": med(answer_ms),
        "total_p50_ms": med(plan_ms) + med(exec_ms) + med(answer_ms),
        "executed_ok": executed_ok,
        "sql_match": sql_match,
    }


async def main_async(args: argparse.Namespace) -> list[dict[str, Any]]:
    all_models = list_models()
    available = [m["id"] for m in all_models if m["available"]]

    if args.models:
        wanted = [m.strip() for m in args.models.split(",")]
        models = [m for m in wanted if m in available or m == "mock"]
    else:
        models = available

    reference_sql = load_reference()
    print(f"Бенчмарк: {len(models)} моделей × {args.runs} прогонов × 1 вопрос\n")
    rows = []
    for model_id in models:
        print(f"  ▸ {model_id} …")
        t0 = time.perf_counter()
        try:
            from app.agents.oleg import PLAN_SYSTEM

            row = await bench_model(model_id, DEFAULT_QUESTION, reference_sql, args.runs)
            row["wall_s"] = round(time.perf_counter() - t0, 1)
            rows.append(row)
            print(f"    plan {row['plan_p50_ms']:.0f}ms · exec {row['exec_p50_ms']:.0f}ms · "
                  f"answer {row['answer_p50_ms']:.0f}ms · ok {row['executed_ok']}/{args.runs}")
        except Exception as e:  # noqa: BLE001 — модель может быть недоступна
            print(f"    ERROR: {str(e)[:80]}")
            rows.append({"model": model_id, "error": str(e)[:100]})

    return rows


def render_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| Модель | План (LLM) | Выполнение (БД) | Ответ (LLM) | Итого | SQL ok |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        if "error" in r:
            lines.append(f"| {r['model']} | — | — | — | — | недоступна |")
            continue
        lines.append(
            f"| {r['model']} | {r['plan_p50_ms']:.0f} ms | {r['exec_p50_ms']:.0f} ms | "
            f"{r['answer_p50_ms']:.0f} ms | "
            f"~{(r['plan_p50_ms'] + r['exec_p50_ms'] + r['answer_p50_ms']) / 1000:.1f} s | "
            f"{r['executed_ok']}/{r['runs']} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Latency benchmark across models")
    parser.add_argument("--models", default=None, help="через запятую, напр. zai:glm-4.6,zai:glm-5.3-flash")
    parser.add_argument("--runs", type=int, default=1, help="прогонов на модель")
    args = parser.parse_args()

    rows = asyncio.run(main_async(args))
    md = render_markdown(rows)

    print("\n" + "=" * 60)
    print("MARKDOWN")
    print("=" * 60)
    print(md)
    OUT_JSON.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nСохранено → {OUT_JSON}")


if __name__ == "__main__":
    main()
