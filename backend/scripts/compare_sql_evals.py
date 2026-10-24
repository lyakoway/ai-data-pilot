"""Unified model comparison for SQL eval artifacts.

Reads bench_results/sql_eval_*.json artifacts, optionally MERGES reruns of the
same (model, golden set) — later artifacts override earlier ones per case id,
with infra-failed cases (err_kind infra_*/rate_limit) only ever overridden BY a
real rerun, never overriding a real result — and prints/saves one comparison
table: model version, params, date, golden-set hash, n, infra failures,
Execution/Result Accuracy with Wilson 95% CI, Exact Match, latency.

Usage (from backend/):
    python scripts/compare_sql_evals.py                # each artifact separately
    python scripts/compare_sql_evals.py --merge        # merge reruns per model+set
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_sql_full import wilson_ci  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent
RESULTS = BACKEND_DIR / "bench_results"


def load_artifacts() -> list[dict[str, Any]]:
    out = []
    for f in sorted(RESULTS.glob("sql_eval_*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        d["_file"] = f.name
        out.append(d)
    return out


def set_tag(d: dict[str, Any]) -> str:
    return "v2" if "v2" in d["manifest"].get("golden_set_path", "") else "v1"


def is_infra(row: dict[str, Any]) -> bool:
    k = row.get("err_kind") or ""
    if k.startswith("infra") or k == "rate_limit":
        return True
    # legacy artifacts (stage 1, pre-2026-10-23) marked infra errors only in
    # the error string — no err_kind field was written
    return str(row.get("error") or "").startswith("harness:")


def summarize(model: str, tag: str, details: list[dict[str, Any]], metas: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(details)
    infra = sum(1 for r in details if is_infra(r))
    quality = [r for r in details if not is_infra(r)]
    executed = sum(1 for r in details if r["executed"])
    checked = [r for r in details if r["result_match"] is not None]
    matched = sum(1 for r in checked if r["result_match"])
    with_sql = [r for r in details if r["exact_match"] is not None]
    exact = sum(1 for r in details if r["exact_match"])
    lat = sorted(r["latency_ms"] for r in details if r["llm_calls"] > 0)
    lo, hi = wilson_ci(matched, len(checked))

    def p(pct: float) -> Any:
        if not lat:
            return None
        return lat[min(int(len(lat) * pct / 100), len(lat) - 1)]

    return {
        "model": model,
        "golden_set": tag,
        "model_reported": (metas[-1]["manifest"].get("model") or {}).get("reported"),
        "generated_at": metas[-1]["manifest"]["generated_at"],
        "git_rev": metas[-1]["manifest"]["git_rev"],
        "golden_sha": metas[-1]["manifest"]["golden_set"]["sha256"],
        "params": metas[-1]["manifest"]["generation_params"],
        "sources": [m["_file"] for m in metas],
        "n": n,
        "n_infra": infra,
        "execution_accuracy": round(executed / n * 100, 1) if n else 0,
        "execution_accuracy_excl_infra": round(
            sum(1 for r in quality if r["executed"]) / len(quality) * 100, 1
        ) if quality else None,
        "result_match": matched,
        "result_checked": len(checked),
        "result_accuracy": round(matched / len(checked) * 100, 1) if checked else None,
        "result_accuracy_wilson95": [lo, hi],
        "exact_match": round(exact / len(with_sql) * 100, 1) if with_sql else None,
        "latency_p50_ms": p(50),
        "latency_p95_ms": p(95),
    }


def merge_group(arts: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge artifacts of one (model, set): later runs override earlier by case id.
    A real (non-infra) rerun result replaces an infra failure; an infra failure
    never replaces a real result."""
    details: dict[str, dict[str, Any]] = {}
    for d in arts:  # artifacts sorted by filename == chronological
        for row in d["details"]:
            prev = details.get(row["id"])
            # latest REAL result wins; an infra failure never overrides a real one
            if prev is None or not (is_infra(row) and not is_infra(prev)):
                details[row["id"]] = row
    return summarize(arts[-1]["summary"]["model"], set_tag(arts[-1]),
                     list(details.values()), arts)


def render(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| Модель | Set | n | infra | Exec % | **Result % (m/c)** | Wilson95 | EM % | p50/p95 ms | Версия от API | Дата | Источник |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda x: (x["golden_set"], -(x["result_accuracy"] or 0))):
        lines.append(
            f"| {r['model']} | {r['golden_set']} | {r['n']} | {r['n_infra']} "
            f"| {r['execution_accuracy']} "
            f"| **{r['result_accuracy']}** ({r['result_match']}/{r['result_checked']}) "
            f"| {r['result_accuracy_wilson95'][0]}–{r['result_accuracy_wilson95'][1]} "
            f"| {r['exact_match']} | {r['latency_p50_ms']}/{r['latency_p95_ms']} "
            f"| {r['model_reported']} | {r['generated_at'][:16]} | {', '.join(r['sources'])} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare SQL eval artifacts")
    parser.add_argument("--merge", action="store_true", help="merge reruns per model+set")
    args = parser.parse_args()

    arts = load_artifacts()
    if not arts:
        raise SystemExit("no sql_eval_*.json artifacts found")

    rows: list[dict[str, Any]] = []
    if args.merge:
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for d in arts:
            groups.setdefault((d["summary"]["model"], set_tag(d)), []).append(d)
        rows = [merge_group(g) for g in groups.values()]
        label = "merged per model+set (infra failures replaced by real reruns)"
    else:
        rows = [summarize(d["summary"]["model"], set_tag(d), d["details"], [d]) for d in arts]
        label = "each artifact separately"

    import time
    print(f"# SQL eval comparison — {label}\n")
    print(render(rows))
    print(
        "\nОграничения: n=45–50 на модель — Wilson 95% CI широки (±10–13 п.п.); "
        "сравнение моделей на границах CI не значимо. Прогоны на внешнем API в разные "
        "дни/время; латентность информационная.\n"
        "Exec% включает инфра-сбои как незавершённые; excl_infra — только качественные "
        "попытки (см. JSON)."
    )
    out = RESULTS / f"sql_compare_{'merged_' if args.merge else ''}{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps({"mode": label, "rows": rows}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"Saved → {out}")


if __name__ == "__main__":
    main()
