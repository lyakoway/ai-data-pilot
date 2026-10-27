"""Unified evaluation pipeline — thin orchestration over existing tools.

Does NOT reimplement anything: each suite shells out to the existing scripts
(evaluate.py, eval_sql_full.py, eval_rag.py, rag_judge.py, e2e_bench.py,
llm_bench.py) and collects their artifacts under one run directory with a
single manifest (run id, git SHA, env, dataset versions and hashes, models,
parameters, eval-code version). Produces report.json + report.md.

Suites:
  deterministic   — eval_rag.py + pytest -q            (no API keys, fast)
  sql             — eval_sql_full.py --model --golden  (API key required)
  routing         — evaluate.py --suite routing        (API key required)
  rag-judge       — rag_judge.py                       (API key required)
  e2e             — e2e_bench.py --models ...          (API key required)
  all             — deterministic + sql + routing (+rag-judge if --with-judge)

Usage (from backend/):
    python scripts/run_eval.py --suite deterministic
    python scripts/run_eval.py --suite sql --model zai:glm-4.5-flash --golden tests/golden/sql_golden_v2.jsonl
    python scripts/run_eval.py --suite all --model zai:glm-4.5-flash --models-e2e mock,zai:glm-4.5-flash
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm_bench import RESULTS_DIR, env_info, git_rev, sha256  # noqa: E402

PY = sys.executable
SCRIPTS = BACKEND / "scripts"
GOLDEN_DIR = BACKEND / "tests" / "golden"


def run_cmd(cmd: list[str], timeout: int = 7200) -> dict[str, Any]:
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, cwd=BACKEND, capture_output=True, text=True, timeout=timeout)
    return {
        "cmd": [Path(c).name if i == 1 and str(c).endswith(".py") else c for i, c in enumerate(cmd)],
        "exit": proc.returncode,
        "wall_s": round(time.perf_counter() - t0, 1),
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-2000:],
    }


def new_artifacts(before: set[str]) -> list[str]:
    return sorted(str(p.relative_to(RESULTS_DIR)) for p in RESULTS_DIR.glob("*") if p.is_file() and str(p) not in before)


def grab_summary(paths: list[str]) -> dict[str, Any]:
    """Pull the summary block out of the artifacts a suite just produced."""
    out: dict[str, Any] = {}
    for rel in paths:
        p = RESULTS_DIR / rel
        if p.suffix != ".json":
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(d, dict) and "summary" in d and isinstance(d["summary"], dict):
            out[rel] = d["summary"]
        elif isinstance(d, dict) and "suites" in d:  # evaluate.py format
            out[rel] = {s.get("suite"): {k: v for k, v in s.items() if k != "details"}
                        for s in d["suites"]}
    return out


def golden_hash(name: str) -> str | None:
    p = GOLDEN_DIR / name
    return sha256(p.read_text(encoding="utf-8")) if p.exists() else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Unified evaluation pipeline")
    parser.add_argument("--suite", required=True,
                        choices=["deterministic", "sql", "routing", "rag-judge", "e2e", "all"])
    parser.add_argument("--model", default="zai:glm-4.5-flash", help="model for sql/routing/judge suites")
    parser.add_argument("--golden", default=str(GOLDEN_DIR / "sql_golden.jsonl"))
    parser.add_argument("--models-e2e", default="mock", help="comma list for e2e_bench")
    parser.add_argument("--with-judge", action="store_true", help="include rag-judge in 'all'")
    parser.add_argument("--runs-dir", default=str(RESULTS_DIR / "runs"))
    args = parser.parse_args()

    run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    run_dir = Path(args.runs_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "run_id": run_id,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "git_rev": git_rev(),
        "env": env_info(),
        "eval_code": ["scripts/run_eval.py", "scripts/eval_sql_full.py", "scripts/eval_rag.py",
                      "scripts/rag_judge.py", "scripts/e2e_bench.py", "scripts/evaluate.py"],
        "suite_requested": args.suite,
        "model": args.model,
        "datasets": {
            "sql_golden_v1": golden_hash("sql_golden.jsonl"),
            "sql_golden_v2": golden_hash("sql_golden_v2.jsonl"),
            "rag_retrieval_v2": golden_hash("rag_retrieval_golden_v2.jsonl"),
            "routing_golden": golden_hash("routing_golden.jsonl"),
        },
        "note": "raw per-suite artifacts are copied under this run dir; aggregates in report.json",
    }

    suites: list[str] = []
    if args.suite == "all":
        suites = ["deterministic-parts", "sql", "routing"]
        if args.with_judge:
            suites.append("rag-judge")
    else:
        suites = [args.suite]

    results: dict[str, Any] = {}
    for suite in suites:
        print(f"\n=== suite: {suite} ", flush=True)
        before = {str(p) for p in RESULTS_DIR.glob("*") if p.is_file()}
        if suite in ("deterministic", "deterministic-parts"):
            steps = {}
            steps["pytest"] = run_cmd([PY, "-m", "pytest", "-q"], timeout=1800)
            print(f"  pytest exit={steps['pytest']['exit']} wall={steps['pytest']['wall_s']}s", flush=True)
            steps["eval_rag"] = run_cmd([PY, str(SCRIPTS / "eval_rag.py")])
            print(f"  eval_rag exit={steps['eval_rag']['exit']}", flush=True)
            arts = new_artifacts(before)
            results["deterministic"] = {"steps": {k: {"exit": v["exit"], "wall_s": v["wall_s"]}
                                                  for k, v in steps.items()},
                                        "artifacts": arts, "summaries": grab_summary(arts)}
        elif suite == "sql":
            cmd = run_cmd([PY, str(SCRIPTS / "eval_sql_full.py"), "--model", args.model,
                           "--golden", args.golden])
            arts = new_artifacts(before)
            results["sql"] = {"steps": {"eval_sql_full": {"exit": cmd["exit"], "wall_s": cmd["wall_s"]}},
                              "artifacts": arts, "summaries": grab_summary(arts), "stdout_tail": cmd["stdout_tail"][-2500:]}
            print(f"  eval_sql_full exit={cmd['exit']} wall={cmd['wall_s']}s", flush=True)
        elif suite == "routing":
            cmd = run_cmd([PY, str(SCRIPTS / "evaluate.py"), "--suite", "routing", "--model", args.model])
            arts = new_artifacts(before)
            results["routing"] = {"steps": {"evaluate": {"exit": cmd["exit"], "wall_s": cmd["wall_s"]}},
                                  "artifacts": arts,
                                  "summaries": grab_summary(arts), "stdout_tail": cmd["stdout_tail"][-1500:]}
            print(f"  evaluate(routing) exit={cmd['exit']}", flush=True)
        elif suite == "rag-judge":
            cmd = run_cmd([PY, str(SCRIPTS / "rag_judge.py")])
            arts = new_artifacts(before)
            results["rag-judge"] = {"steps": {"rag_judge": {"exit": cmd["exit"], "wall_s": cmd["wall_s"]}},
                                    "artifacts": arts, "summaries": grab_summary(arts)}
            print(f"  rag_judge exit={cmd['exit']}", flush=True)
        elif suite == "e2e":
            cmd = run_cmd([PY, str(SCRIPTS / "e2e_bench.py"), "--models", args.models_e2e])
            arts = new_artifacts(before)
            results["e2e"] = {"steps": {"e2e_bench": {"exit": cmd["exit"], "wall_s": cmd["wall_s"]}},
                              "artifacts": arts, "summaries": grab_summary(arts)}
            print(f"  e2e_bench exit={cmd['exit']}", flush=True)
        # copy artifacts of this suite into the run dir (raw kept with the run)
        for rel in new_artifacts(before):
            src = RESULTS_DIR / rel
            dst = run_dir / src.name
            if not dst.exists():
                shutil.copy2(src, dst)

    (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "report.json").write_text(json.dumps({"manifest": manifest, "results": results},
                                                    ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"# Evaluation run {run_id}", "",
             f"- date: {manifest['generated_at']}  git: `{manifest['git_rev']}`",
             f"- suite(s): {', '.join(results)}  model: {args.model}", ""]
    for suite, r in results.items():
        lines.append(f"## {suite}")
        for step, info in r.get("steps", {}).items():
            mark = "OK" if info["exit"] == 0 else f"FAIL(exit={info['exit']})"
            lines.append(f"- {step}: {mark} ({info['wall_s']}s)")
        for art, summ in (r.get("summaries") or {}).items():
            key_metrics = {k: v for k, v in summ.items()
                           if isinstance(v, (int, float, str)) and k != "details"}
            lines.append(f"- `{art}`: `{json.dumps(key_metrics, ensure_ascii=False)[:400]}`")
        lines.append("")
    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nRun dir → {run_dir}")


if __name__ == "__main__":
    main()
