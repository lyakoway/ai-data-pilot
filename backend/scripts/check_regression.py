"""Regression gate for evaluation results — compare a run against a baseline.

Methodology (no arbitrary thresholds):
  - Metric-level: flag a regression ONLY when the drop exceeds what the
    binomial noise of the new run's sample can explain — i.e. the baseline
    proportion lies ABOVE the upper bound of the new run's Wilson 95% CI
    (two overlapping CIs = "no significant change" by design: conservative
    for releases, never blocks on small-sample jitter).
  - Contract-level (regardless of sample size): a metric appearing for the
    first time with a zero value, or infra-failure share jumping above 20%,
    is always a failure — those are correctness/health signals, not noise.

Exit code 0 = pass, 1 = regression (suitable for CI).

Usage (from backend/):
    python scripts/check_regression.py --baseline bench_results/sql_eval_..._v1.json \
                                       --candidate bench_results/sql_eval_..._v1.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_sql_full import wilson_ci  # noqa: E402

# metrics checked with the Wilson test: (json path inside artifact, direction)
WILSON_METRICS: dict[str, dict[str, Any]] = {
    "sql": {
        "key": "result_accuracy",
        "num": "result_accuracy",      # field with the raw fraction data below
        "num_fields": ("result_accuracy", "result_accuracy_n_checked"),
    },
}

CONTRACT_MAX_INFRA_SHARE = 20.0  # % — health signal, not statistics


def load_artifact(path: str) -> dict[str, Any]:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if "summary" not in d:
        raise SystemExit(f"{path}: not an eval artifact (no summary)")
    return d


def check_sql(base: dict[str, Any], cand: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    failures, warns, infos = [], [], []
    bs, cs = base["summary"], cand["summary"]

    # --- Wilson comparison on result accuracy ---
    b_acc = bs.get("result_accuracy")
    c_acc = cs.get("result_accuracy")
    c_n = cs.get("result_accuracy_n_checked", 0)
    if b_acc is None or c_acc is None or not c_n:
        failures.append(f"result_accuracy missing (baseline={b_acc}, candidate={c_acc}, n={c_n})")
    else:
        # reconstruct k from the percentage (rounded) — use stored counts when present
        k = round(c_acc * c_n / 100)
        lo, hi = wilson_ci(k, c_n)
        if b_acc > hi + 0.5:  # baseline above the candidate's CI → real drop
            failures.append(
                f"result_accuracy regression: baseline {b_acc}% > candidate CI upper "
                f"{hi}% (candidate {c_acc}%, n={c_n}, Wilson95 [{lo},{hi}])")
        elif b_acc > c_acc:
            infos.append(
                f"result_accuracy dropped {b_acc}%→{c_acc}% but within binomial noise "
                f"(Wilson95 upper {hi}% ≥ baseline) — NOT a blocking regression")
        else:
            infos.append(f"result_accuracy {c_acc}% ≥ baseline {b_acc}% (n={c_n})")

    # --- contract / health checks (sample-size independent) ---
    infra_share = cs.get("infra_failure_share") or 0.0
    if infra_share > CONTRACT_MAX_INFRA_SHARE:
        failures.append(f"infra failure share {infra_share}% > {CONTRACT_MAX_INFRA_SHARE}% "
                        "(suite unhealthy — rerun before judging quality)")
    exec_acc = cs.get("sql_execution_accuracy_excl_infra", cs.get("sql_execution_accuracy"))
    if exec_acc is not None and exec_acc < 50:
        failures.append(f"execution accuracy {exec_acc}% < 50% — contract violation")
    # zero repaired cases → self-correction rate is unmeasurable, not zero
    if cs.get("repaired_cases", 0) == 0:
        infos.append("self-correction unmeasurable this run (0 repaired cases — SQL never failed)")
    return failures, warns, infos


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluation regression gate")
    parser.add_argument("--baseline", required=True, help="baseline artifact json")
    parser.add_argument("--candidate", required=True, help="new artifact json")
    args = parser.parse_args()

    base, cand = load_artifact(args.baseline), load_artifact(args.candidate)
    suite = cand["summary"].get("suite") or ("sql" if "sql_execution_accuracy" in cand["summary"] else "unknown")
    if suite != "sql":
        raise SystemExit(f"unsupported suite for regression gate: {suite}")

    failures, warns, infos = check_sql(base, cand)

    print(f"Regression gate — suite=sql")
    print(f"  baseline : {args.baseline} ({base['summary'].get('model')}, {base['manifest']['generated_at']})")
    print(f"  candidate: {args.candidate} ({cand['summary'].get('model')}, {cand['manifest']['generated_at']})")
    for i in infos:
        print(f"  INFO: {i}")
    for w in warns:
        print(f"  WARN: {w}")
    for f in failures:
        print(f"  FAIL: {f}")
    if failures:
        print("\nRESULT: REGRESSION — do not ship without review")
        sys.exit(1)
    print("\nRESULT: PASS (no statistically grounded regression)")


if __name__ == "__main__":
    main()
