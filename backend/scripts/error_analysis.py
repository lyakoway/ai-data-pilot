"""Error taxonomy for Text-to-SQL failures — heuristic post-hoc classifier.

Reads existing sql_eval artifacts (v1/v2, all models), classifies each failed
case by comparing generated SQL against the reference SQL, and writes an
aggregated cross-model table + per-case CSV. No LLM calls: works purely on
artifacts, so it can be rerun freely.

Taxonomy (one primary label per failed case, priority order):
  infra          — client timeout / 429 / network (NOT model quality)
  no_sql         — model returned no SQL
  syntax/exec    — SQL failed to execute (DB error, guard rejection)
  wrong_table    — set of referenced tables != expected_tables
  wrong_join     — JOIN count/table set differs while tables otherwise match
  aggregation    — aggregate-function set differs (SUM/COUNT/AVG/MIN/MAX/ROUND)
  temporal       — reference is date-driven (strftime/date/ride_date) and the
                   generated date handling differs structurally
  filter         — filter literals differ (is_partner/status/country/tariff…)
  interpretation — structure matches on all axes above, results still differ
                   (semantics, ordering, rounding or question misreading)

It is a HEBURIISTIC: labels give direction for manual review, not proof.
A case labeled `interpretation` may hide any finer cause the heuristics
cannot see; examples are exported for manual verification.

Usage (from backend/):
    python scripts/error_analysis.py            # all artifacts, merged per model+set
"""
from __future__ import annotations

import csv
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm_bench import RESULTS_DIR, git_rev  # noqa: E402

AGG_RE = re.compile(r"\b(sum|count|avg|min|max|round|group_concat)\s*\(", re.I)
JOIN_RE = re.compile(r"\bjoin\s+([a-z_][a-z0-9_]*)", re.I)
FROM_RE = re.compile(r"\bfrom\s+([a-z_][a-z0-9_]*)", re.I)
DATE_RE = re.compile(r"strftime|date\s*\(|julianday|ride_date|registered_at|started_at|cancelled_at", re.I)
LIT_RE = re.compile(r"is_partner|status\s*=|country\s*=|vehicle_type\s*=|brand\s*=|is_inhouse|phone_country|tariff", re.I)


def tables_of(sql: str) -> set[str]:
    return {t.lower() for t in JOIN_RE.findall(sql or "")} | {
        t.lower() for t in FROM_RE.findall(sql or "")
    }


def aggs_of(sql: str) -> Counter:
    return Counter(a.lower() for a in AGG_RE.findall(sql or ""))


def is_infra(row: dict[str, Any]) -> bool:
    k = row.get("err_kind") or ""
    if k.startswith("infra") or k == "rate_limit":
        return True
    return str(row.get("error") or "").startswith("harness:")


def classify(row: dict[str, Any], expected_tables: list[str]) -> str:
    if is_infra(row):
        return "infra"
    if row.get("err_kind") == "no_sql" or (not row["executed"] and (row.get("error") or "").startswith("model returned no SQL")):
        return "no_sql"
    if not row["executed"]:
        return "syntax/exec"
    gen = row.get("generated_sql") or ""
    # reference is embedded in the case, not the artifact; recover expected
    # tables from the question id category is unavailable — use table diff vs expected_tables
    exp = {t.lower() for t in expected_tables}
    got = tables_of(gen)
    if exp and got and got != exp:
        # tolerate extra CTE sources: only flag when a real table differs
        if not exp.issubset(got) or not got.issubset(exp | {"_q"}):
            return "wrong_table"
    return ""  # fine-grained axes handled by the caller with the reference SQL


def classify_with_ref(row: dict[str, Any], ref_sql: str, expected_tables: list[str]) -> str:
    base = classify(row, expected_tables)
    if base:
        return base
    gen = row.get("generated_sql") or ""
    ref_joins = JOIN_RE.findall(ref_sql)
    gen_joins = JOIN_RE.findall(gen)
    if (len(ref_joins) != len(gen_joins)) or {j.lower() for j in ref_joins} != {j.lower() for j in gen_joins}:
        return "wrong_join"
    if aggs_of(gen) != aggs_of(ref_sql):
        return "aggregation"
    if DATE_RE.search(ref_sql):
        gen_dates = sorted(set(DATE_RE.findall(gen)))
        ref_dates = sorted(set(DATE_RE.findall(ref_sql)))
        if "ride_date" in " ".join(ref_dates) or "ride_date" in " ".join(gen_dates):
            # structural difference in date window handling
            gen_win = re.findall(r"(?:ride_date|registered_at)\s*(?:>=|<=|>|<|between)\s*[^,)\s]+", gen, re.I)
            ref_win = re.findall(r"(?:ride_date|registered_at)\s*(?:>=|<=|>|<|between)\s*[^,)\s]+", ref_sql, re.I)
            if len(gen_win) != len(ref_win):
                return "temporal"
        if set(d.lower() for d in gen_dates) != set(d.lower() for d in ref_dates):
            return "temporal"
    if Counter(LIT_RE.findall(gen)) != Counter(LIT_RE.findall(ref_sql)):
        return "filter"
    return "interpretation"


def load_cases() -> dict[str, dict[str, Any]]:
    cases = {}
    for name in ("sql_golden.jsonl", "sql_golden_v2.jsonl"):
        p = Path(__file__).resolve().parent.parent / "tests" / "golden" / name
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                c = json.loads(line)
                cases[c["id"]] = c
    return cases


def main() -> None:
    artifacts = sorted(RESULTS_DIR.glob("sql_eval_*.json"))
    if not artifacts:
        raise SystemExit("no sql_eval artifacts")
    cases = load_cases()

    # merge per (model, set): latest real result wins (same rule as compare_sql_evals)
    groups: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for f in artifacts:
        d = json.loads(f.read_text(encoding="utf-8"))
        model = d["summary"]["model"]
        set_tag = "v2" if "v2" in d["manifest"].get("golden_set_path", "") else "v1"
        g = groups.setdefault((model, set_tag), {})
        for row in d["details"]:
            prev = g.get(row["id"])
            row_infra = is_infra(row)
            if prev is None or not (row_infra and not is_infra(prev)):
                g[row["id"]] = row

    out_rows = []
    for (model, set_tag), details in sorted(groups.items()):
        for cid, row in details.items():
            case = cases.get(cid, {})
            # classify ONLY verified failures: result_match === False, or the
            # query did not execute. result_match None = execution-only case
            # (ambiguous) — correctness was never checked, must not be labeled.
            if row["executed"] and row.get("result_match") is not False:
                continue
            label = classify_with_ref(row, case.get("reference_sql", ""), case.get("expected_tables", []))
            out_rows.append({
                "model": model, "set": set_tag, "id": cid,
                "category": case.get("category"), "executed": row["executed"],
                "error_type": label,
                "question": (case.get("question") or "")[:80],
                "generated_sql": (row.get("generated_sql") or "")[:220],
                "error": (row.get("error") or "")[:100],
            })

    # aggregate
    agg: dict[tuple[str, str], Counter] = {}
    for r in out_rows:
        agg.setdefault((r["model"], r["set"]), Counter())[r["error_type"]] += 1

    print(f"Error taxonomy — {len(out_rows)} failed cases across {len(agg)} model+set groups\n")
    types = ["infra", "no_sql", "syntax/exec", "wrong_table", "wrong_join",
             "aggregation", "temporal", "filter", "interpretation"]
    print(f"{'model':<22}{'set':<5}" + "".join(f"{t:>15}" for t in types))
    for (model, set_tag) in sorted(agg):
        c = agg[(model, set_tag)]
        print(f"{model:<22}{set_tag:<5}" + "".join(f"{c.get(t, 0):>15}" for t in types))

    stamp = time.strftime("%Y%m%d_%H%M%S")
    csv_path = RESULTS_DIR / f"sql_error_taxonomy_{stamp}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)
    json_path = RESULTS_DIR / f"sql_error_taxonomy_{stamp}.json"
    json_path.write_text(json.dumps({
        "manifest": {"tool": "error_analysis.py", "git_rev": git_rev(),
                     "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                     "heuristic": "post-hoc SQL-structure diff; labels are directional, "
                                  "not proof — see docs/ERROR_ANALYSIS.md"},
        "aggregate": {f"{m}|{s}": dict(c) for (m, s), c in agg.items()},
        "details": out_rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved → {csv_path.name}\n       → {json_path.name}")


if __name__ == "__main__":
    main()
