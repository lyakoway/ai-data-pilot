"""End-to-end application latency benchmark — AI Data Pilot /api/chat/stream.

Spawns a real uvicorn server (in-process app untouched) and drives the same
SSE endpoint the React UI consumes. Measures, per request:

  - time_to_first_event_s — HTTP request start → first SSE frame received.
    NOTE: this is the first *step* event, NOT a model TTFT: the app streams
    agent steps (planner → database_query → answer), not tokens.
  - step durations as reported by the app itself (step.duration_ms)
  - total_s — request start → SSE ``done`` event
  - llm_steps — steps that call the model (planner, answer, agent)
  - sql_repairs — database_query steps after the first (self-correction)
  - status — from the done payload (ok / partial / demo / error)
  - numeric_grounding — heuristic: every number in the answer text must
    appear in the returned rows or insights highlights (labeled heuristic)

Usage (from backend/):
    python scripts/e2e_bench.py --models zai:glm-4.6 --runs 3
    python scripts/e2e_bench.py --models mock,zai:glm-4.6,zai:glm-5.2,zai:glm-5.3-flash
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm_bench import git_rev, save_json  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Simple, single-SQL questions (linear path) — comparable with the old
# latency_benchmark.py runs. One complex question exercises the ReAct loop.
QUESTIONS = [
    ("simple", "Сколько всего поездок в базе?"),
    ("simple", "Какова общая выручка за весь период?"),
    ("complex", "Почему выручка упала в последнем месяце по сравнению с предыдущим?"),
]

_NUM_RE = re.compile(r"-?\d[\d \u00a0.,]*")


def _numbers_in(text: str) -> set[str]:
    """Normalize RU/EN number formats: strip spaces, unify decimal separator to '.'."""
    out = set()
    for m in _NUM_RE.finditer(text or ""):
        raw = m.group(0).replace(" ", "").replace("\u00a0", "")
        # RU '1,5' vs EN '1,500': treat comma as decimal only when followed by 1-2 digits at end
        if re.fullmatch(r"-?\d+,\d{1,2}", raw):
            raw = raw.replace(",", ".")
        else:
            raw = raw.replace(",", "")
        out.add(raw)
    return out


def numeric_grounding(answer: str, rows: list[list[Any]], highlights: list[str]) -> bool | None:
    """True if every number mentioned in the answer occurs in the data.
    Heuristic: substring containment after whitespace normalization; years and
    small ordinals excluded via min length 2 digits. None when no numbers."""
    src = " | ".join(
        str(v) for row in rows for v in row
    ) + " | " + " | ".join(highlights or [])
    src_n = _numbers_in(src)
    ans_n = {x for x in _numbers_in(answer) if len(x.replace(",", "").replace(".", "")) >= 2}
    if not ans_n:
        return None
    ok = True
    for a in ans_n:
        found = any(a == s or (len(a) >= 3 and a.rstrip(",.") in s) or (len(s) >= 3 and s.rstrip(",.") in a)
                   for s in src_n)
        if not found:
            ok = False
    return ok


async def wait_server(client: httpx.AsyncClient, timeout_s: float = 60.0) -> None:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout_s:
        try:
            r = await client.get("/api/health")
            if r.status_code == 200:
                return
        except Exception:  # noqa: BLE001
            await asyncio.sleep(0.5)
    raise SystemExit("server did not become ready in time")


async def one_request(client: httpx.AsyncClient, model: str, question: str,
                      timeout_s: float) -> dict[str, Any]:
    t0 = time.perf_counter()
    t_first_event: float | None = None
    steps: list[dict[str, Any]] = []
    done_payload: dict[str, Any] | None = None
    error_event: dict[str, Any] | None = None
    try:
        async with client.stream(
            "POST", "/api/chat/stream",
            json={"message": question, "agent": "atlas", "model": model, "lang": "ru",
                  "datasource_id": "ridego"},
        ) as resp:
            event_name = ""
            data_buf: list[str] = []
            async for line in resp.aiter_lines():
                if line.startswith("event: "):
                    event_name = line[7:].strip()
                elif line.startswith("data: ") and line[6:].strip():
                    data_buf.append(line[6:])
                elif not line.strip() and data_buf:
                    payload = json.loads("".join(data_buf))
                    data_buf = []
                    if t_first_event is None:
                        t_first_event = time.perf_counter() - t0
                    if event_name == "step":
                        steps.append(payload)
                    elif event_name == "done":
                        done_payload = payload
                    elif event_name == "error":
                        error_event = payload
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:150]}",
                "total_s": round(time.perf_counter() - t0, 2)}

    total = time.perf_counter() - t0
    # SSE sends each step twice (running → done/error); dedupe by step id.
    final_steps: dict[str, dict[str, Any]] = {}
    for s in steps:
        final_steps[s.get("id") or len(final_steps)] = s
    dbq = [s for s in final_steps.values() if s.get("tool") == "database_query"]
    result: dict[str, Any] = {
        "ok": done_payload is not None and error_event is None,
        "total_s": round(total, 3),
        "time_to_first_event_s": round(t_first_event, 3) if t_first_event is not None else None,
        "n_steps": len(final_steps),
        "llm_steps": sum(1 for s in final_steps.values() if s.get("tool") in ("planner", "answer", "agent", "router", "source_router")),
        "sql_repairs": max(len(dbq) - 1, 0),
        "status": (done_payload or {}).get("status"),
        "step_durations_ms": {s.get("tool"): s.get("duration_ms") for s in steps if s.get("duration_ms") is not None},
        "error": (error_event or {}).get("message") if error_event else None,
    }
    if done_payload:
        highlights = ((done_payload.get("insights") or {}).get("highlights")) or []
        result["numeric_grounding"] = numeric_grounding(
            done_payload.get("answer") or "", done_payload.get("rows") or [], highlights
        )
    return result


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    port = args.port
    proc = subprocess.Popen(
        [str(BACKEND_DIR / ".venv" / "bin" / "uvicorn"), "app.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=BACKEND_DIR,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    results: list[dict[str, Any]] = []
    base_url = f"http://127.0.0.1:{port}"
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=args.timeout) as client:
            await wait_server(client)
            print(f"server up on :{port}")
            for model in [m.strip() for m in args.models.split(",") if m.strip()]:
                for qkind, question in QUESTIONS:
                    kind_runs: list[dict[str, Any]] = []
                    for run_i in range(args.runs):
                        r = await one_request(client, model, question, args.timeout)
                        r.update({"model": model, "question_kind": qkind, "run": run_i + 1})
                        kind_runs.append(r)
                        results.append(r)
                        print(f"  {model} [{qkind}] run{run_i + 1}: "
                              f"{'ok' if r['ok'] else 'FAIL'} total={r['total_s']}s "
                              f"first_event={r.get('time_to_first_event_s')}s "
                              f"steps={r.get('n_steps')} repairs={r.get('sql_repairs')} "
                              f"status={r.get('status')}", flush=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # noqa: BLE001
            proc.kill()

    # aggregate per (model, question_kind)
    agg_rows = []
    for model in {r["model"] for r in results}:
        for qkind in {r["question_kind"] for r in results if r["model"] == model}:
            sub = [r for r in results if r["model"] == model and r["question_kind"] == qkind]
            ok = [r for r in sub if r["ok"]]
            def _p(vals: list[Any], p: float) -> Any:
                nums = sorted(v for v in vals if v is not None)
                return nums[min(int(len(nums) * p / 100), len(nums) - 1)] if nums else None
            grounding = [r["numeric_grounding"] for r in ok if r.get("numeric_grounding") is not None]
            agg_rows.append({
                "model": model, "question_kind": qkind, "n": len(sub), "n_ok": len(ok),
                "total_s_p50": _p([r["total_s"] for r in ok], 50),
                "total_s_mean": round(statistics.mean([r["total_s"] for r in ok]), 2) if ok else None,
                "first_event_s_p50": _p([r["time_to_first_event_s"] for r in ok], 50),
                "sql_repairs_total": sum(r.get("sql_repairs", 0) for r in sub),
                "numeric_grounding_ok_share": round(
                    sum(1 for g in grounding if g) / len(grounding) * 100, 1
                ) if grounding else None,
                "statuses": sorted({str(r.get("status")) for r in sub}),
            })
    return {
        "tool": "e2e_bench.py",
        "level": "application_sse",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "git_rev": git_rev(),
        "server": "uvicorn (local subprocess), single worker",
        "note": ("time_to_first_event_s is the first agent-step SSE frame, NOT a model "
                 "TTFT — the application streams steps, not tokens"),
        "questions": [{"kind": k, "text": q} for k, q in QUESTIONS],
        "runs_per_question": args.runs,
        "aggregate": agg_rows,
        "details": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="E2E SSE latency benchmark")
    parser.add_argument("--models", default="mock,zai:glm-4.6,zai:glm-5.2,zai:glm-5.3-flash")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--port", type=int, default=8017)
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args()
    payload = asyncio.run(main_async(args))
    print("\n" + "=" * 64)
    print("E2E aggregate (p50 total_s | p50 first_event_s | repairs | grounding)")
    for a in payload["aggregate"]:
        print(f"  {a['model']:<22} {a['question_kind']:<8} ok {a['n_ok']}/{a['n']} "
              f"total p50={a['total_s_p50']}s first_event p50={a['first_event_s_p50']}s "
              f"repairs={a['sql_repairs_total']} grounding={a['numeric_grounding_ok_share']}%")
    path = save_json("e2e_sse", payload)
    print(f"\nSaved → {path}")


if __name__ == "__main__":
    main()
