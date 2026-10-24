"""LLM inference benchmark for AI Data Pilot — external-API level.

Measures what the app itself does not expose: token-level latency (TTFT, TPOT,
ITL), throughput under concurrency, and error rates — against the Z.ai
OpenAI-compatible API used by the app's ZaiProvider.

This is an EXTERNAL API benchmark, not a GPU/inference-runtime benchmark:
no claims are made about server-side GPU utilisation, KV cache or batching.
What is measured is the client-observed behaviour of the hosted endpoint.

Usage (from backend/):
    python scripts/llm_bench.py tokens --models zai:glm-4.6,zai:glm-5.3-flash
    python scripts/llm_bench.py tokens --scenario long
    python scripts/llm_bench.py load --model zai:glm-4.5-flash --levels 1,2,5,10
    python scripts/llm_bench.py retrieval

Metrics collected per streaming request (formulas):
    ttft_reasoning_s   — time to first chunk carrying delta.reasoning_content
    ttft_answer_s      — time to first chunk carrying delta.content
                         (for always-thinking models this includes the whole
                         reasoning phase; reported separately, never merged)
    total_s            — wall time from request start to stream end
    tpot_chunk_ms      — (total_s - ttft_answer_s) / (n_answer_chunks - 1)
                         per-answer-chunk time; chunk ≈ token for this API,
                         but see usage_source: exact when usage is returned
    itl_mean/p95_ms    — inter-arrival times between consecutive answer chunks
    completion_tok_s   — usage.completion_tokens / total_s  (whole request)
    tok_s_after_ttft   — usage.completion_tokens / (total_s - ttft_answer_s)

Load-test aggregation:
    throughput_rps     — completed_requests / wall_s      (completed only)
    throughput_tps     — sum(completion_tokens) / wall_s  (completed only)

Results → bench_results/<name>_<timestamp>.json (+ per-case CSV where useful).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import statistics
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BACKEND_DIR = Path(__file__).resolve().parent.parent
RESULTS_DIR = BACKEND_DIR / "bench_results"
ZAI_BASE_URL = "https://api.z.ai/api/paas/v4"

# Model id (app registry) → API model name. Mirrors app/llm/registry.py specs.
ZAI_MODELS = {
    "zai:glm-4.6": "glm-4.6",
    "zai:glm-4.5-flash": "glm-4.5-flash",
    "zai:glm-5.2": "glm-5.2",
    "zai:glm-5.3": "glm-5.3",
    "zai:glm-5.3-flash": "glm-5.3-flash",
}

SHORT_PROMPT = "Объясни в 4–5 предложениях, что такое выручка аналитической службы такси и из чего она складывается."
SHORT_MAX_TOKENS = 300


def long_prompt() -> str:
    """A ~2.6k-token analytics-style prompt: schema + repeated rows + question."""
    schema = (
        "Таблица fact_rides(ride_id INTEGER, user_id INTEGER, city_id INTEGER, "
        "ride_date DATE, ride_time TEXT, distance_km REAL, revenue_rub REAL, "
        "is_partner INTEGER, tariff TEXT, status TEXT)\n"
    )
    rows = "\n".join(
        f"ride {i}: user={1000 + i % 40}, city={i % 7}, date=2026-0{1 + i % 9}-1{i % 9}, "
        f"distance={5 + i % 25}.{i % 10} km, revenue={300 + i % 900}.{i % 10} RUB, "
        f"is_partner={i % 5 == 0}, tariff={'eco' if i % 3 else 'comfort'}, status=done"
        for i in range(120)
    )
    return (
        "Ниже схема таблицы и фрагмент данных поездок.\n" + schema + rows
        + "\n\nВопрос: одним предложением — какова средняя выручка на поездку в этом фрагменте?"
    )


def env_info() -> dict[str, str]:
    import platform
    from importlib import metadata
    out = {"python": platform.python_version(), "os": platform.platform()}
    for lib in ("openai", "httpx"):
        try:
            out[lib] = metadata.version(lib)
        except Exception:  # noqa: BLE001
            out[lib] = "unknown"
    return out


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


def get_zai_client(base_url: str | None = None, key_env: str = "ZAI_API_KEY"):
    from dotenv import dotenv_values
    from openai import AsyncOpenAI

    cfg = dotenv_values(BACKEND_DIR / ".env")
    url = base_url or ZAI_BASE_URL
    if url == ZAI_BASE_URL:
        key = cfg.get(key_env) or ""
    else:
        # Non-Z.ai endpoint (e.g. local ollama OpenAI-compat server): no key needed.
        key = cfg.get(key_env) or "local"
    if not key:
        raise SystemExit(f"{key_env} not set in backend/.env")
    return AsyncOpenAI(api_key=key, base_url=url, timeout=300.0)


def ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def save_json(name: str, payload: dict[str, Any]) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{name}_{ts()}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(int(len(ordered) * p / 100), len(ordered) - 1)
    return ordered[idx]


def agg(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean": round(statistics.mean(values), 3),
        "p50": round(pct(values, 50), 3),
        "p95": round(pct(values, 95), 3),
        "std": round(statistics.pstdev(values), 3) if len(values) > 1 else 0.0,
    }


# --------------------------------------------------------------------------- #
# Token-level latency: one streaming request, per-chunk timestamps
# --------------------------------------------------------------------------- #

class StreamRecord:
    __slots__ = ("t0", "t_first_reasoning", "t_first_answer", "t_last", "inter_answer",
                 "n_reason_chunks", "n_answer_chunks", "finish", "usage", "error",
                 "completion_text_len")

    def __init__(self) -> None:
        self.t0 = 0.0
        self.t_first_reasoning: float | None = None
        self.t_first_answer: float | None = None
        self.t_last: float | None = None
        self.inter_answer: list[float] = []
        self.n_reason_chunks = 0
        self.n_answer_chunks = 0
        self.finish: str | None = None
        self.usage: Any = None
        self.error: str | None = None
        self.completion_text_len = 0


async def stream_once(client, model: str, prompt: str, max_tokens: int,
                      disable_thinking: bool, allow_thinking_flag: bool = True) -> StreamRecord:
    rec = StreamRecord()
    extra: dict[str, Any] = {}
    if disable_thinking and allow_thinking_flag:
        extra["extra_body"] = {"thinking": {"type": "disabled"}}
    rec.t0 = time.perf_counter()
    prev_answer_t: float | None = None
    try:
        stream = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.1,
            stream=True,
            **extra,
        )
        async for chunk in stream:
            now = time.perf_counter()
            rec.t_last = now
            if getattr(chunk, "usage", None):
                rec.usage = chunk.usage
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                rec.finish = choice.finish_reason
            delta = choice.delta
            if delta is None:
                continue
            rc = getattr(delta, "reasoning_content", None)
            if rc:
                if rec.t_first_reasoning is None:
                    rec.t_first_reasoning = now - rec.t0
                rec.n_reason_chunks += 1
            c = delta.content
            if c:
                if rec.t_first_answer is None:
                    rec.t_first_answer = now - rec.t0
                rec.n_answer_chunks += 1
                rec.completion_text_len += len(c)
                if prev_answer_t is not None:
                    rec.inter_answer.append((now - prev_answer_t) * 1000)
                prev_answer_t = now
    except Exception as e:  # noqa: BLE001
        rec.error = f"{type(e).__name__}: {str(e)[:200]}"
    return rec


def rec_to_metrics(rec: StreamRecord) -> dict[str, Any]:
    total = (rec.t_last - rec.t0) if rec.t_last else 0.0
    out: dict[str, Any] = {
        "ok": rec.error is None and (rec.n_answer_chunks > 0 or rec.n_reason_chunks > 0),
        "answer_reached": rec.n_answer_chunks > 0,
        "total_s": round(total, 3),
        "ttft_reasoning_s": round(rec.t_first_reasoning, 3) if rec.t_first_reasoning is not None else None,
        "ttft_answer_s": round(rec.t_first_answer, 3) if rec.t_first_answer is not None else None,
        "n_reason_chunks": rec.n_reason_chunks,
        "n_answer_chunks": rec.n_answer_chunks,
        "finish_reason": rec.finish,
        "usage_prompt_tokens": rec.usage.prompt_tokens if rec.usage else None,
        "usage_completion_tokens": rec.usage.completion_tokens if rec.usage else None,
        "usage_source": "api" if rec.usage else "absent",
        "completion_text_len": rec.completion_text_len,
    }
    if rec.t_first_answer is not None and rec.n_answer_chunks > 1:
        out["tpot_chunk_ms"] = round((total - rec.t_first_answer) * 1000 / (rec.n_answer_chunks - 1), 2)
    if rec.inter_answer:
        out["itl_mean_ms"] = round(statistics.mean(rec.inter_answer), 2)
        out["itl_p95_ms"] = round(pct(rec.inter_answer, 95), 2)
    if rec.usage and rec.usage.completion_tokens and total > 0:
        # NOTE: on Z.ai completion_tokens INCLUDES reasoning tokens for thinking
        # models — this rate is "all generated tokens", not answer speed.
        out["completion_tok_per_s"] = round(rec.usage.completion_tokens / total, 1)
        if rec.t_first_answer is not None and total > rec.t_first_answer:
            out["tok_per_s_after_ttft"] = round(
                rec.usage.completion_tokens / (total - rec.t_first_answer), 1
            )
    if rec.t_first_answer is not None and rec.n_answer_chunks > 0 and total > rec.t_first_answer:
        # Answer-stream-only rate from client-observed chunks (≈ tokens, gateway
        # may merge several tokens per flush — see BENCHMARK.md).
        out["answer_chunk_per_s"] = round(rec.n_answer_chunks / (total - rec.t_first_answer), 1)
    if rec.error:
        out["error"] = rec.error
    return out


async def cmd_tokens(args: argparse.Namespace) -> None:
    client = get_zai_client(args.base_url)
    is_zai = (args.base_url or ZAI_BASE_URL) == ZAI_BASE_URL
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    prompt = long_prompt() if args.scenario == "long" else SHORT_PROMPT
    if args.scenario == "short":
        max_tokens = {"think_off": 300, "think_on": 1000}
    else:
        max_tokens = {"think_off": 700, "think_on": 2500}

    results: list[dict[str, Any]] = []
    for model_id in models:
        api_model = args.api_model or ZAI_MODELS.get(model_id, model_id.split(":", 1)[-1])
        for think_label, disable in (("think_off", True), ("think_on", False)):
            if args.think_mode != "both" and args.think_mode != think_label:
                continue
            if not is_zai and think_label == "think_on":
                continue  # thinking flags are Z.ai-specific; local runtimes tested once
            budget = max_tokens[think_label]
            runs = []
            for i in range(args.runs):
                rec = await stream_once(client, api_model, prompt, budget, disable,
                                        allow_thinking_flag=is_zai)
                m = rec_to_metrics(rec)
                m["run"] = i + 1
                runs.append(m)
                status = "ok" if m["ok"] else f"ERR {m.get('error', 'no tokens at all')}"
                print(f"  {model_id} [{think_label}] run{i + 1}: {status} "
                      f"ttft_ans={m['ttft_answer_s']}s tpot={m.get('tpot_chunk_ms')}ms "
                      f"total={m['total_s']}s c_tok={m.get('usage_completion_tokens')}",
                      flush=True)
            ok_runs = [r for r in runs if r["ok"]]
            summary: dict[str, Any] = {
                "model": model_id, "api_model": api_model, "think_mode": think_label,
                "scenario": args.scenario, "n_runs": args.runs,
                "n_ok": len(ok_runs),
            }
            if ok_runs:
                summary.update({
                    "total_s": agg([r["total_s"] for r in ok_runs]),
                    "ttft_reasoning_s": agg([r["ttft_reasoning_s"] for r in ok_runs if r["ttft_reasoning_s"] is not None]),
                    "ttft_answer_s": agg([r["ttft_answer_s"] for r in ok_runs if r["ttft_answer_s"] is not None]),
                    "tpot_chunk_ms": agg([r["tpot_chunk_ms"] for r in ok_runs if "tpot_chunk_ms" in r]),
                    "itl_mean_ms": agg([r["itl_mean_ms"] for r in ok_runs if "itl_mean_ms" in r]),
                    "usage_prompt_tokens": agg([r["usage_prompt_tokens"] for r in ok_runs if r["usage_prompt_tokens"]]),
                    "usage_completion_tokens": agg([r["usage_completion_tokens"] for r in ok_runs if r["usage_completion_tokens"]]),
                    "completion_tok_per_s": agg([r["completion_tok_per_s"] for r in ok_runs if "completion_tok_per_s" in r]),
                })
            results.append({"summary": summary, "runs": runs})
            print(f"  ⇒ {model_id} [{think_label}]: ttft_answer p50="
                  f"{summary.get('ttft_answer_s', {}).get('p50')}s "
                  f"tpot p50={summary.get('tpot_chunk_ms', {}).get('p50')}ms "
                  f"tok/s p50={summary.get('completion_tok_per_s', {}).get('p50')}")

    payload = {
        "tool": "llm_bench.py tokens",
        "level": "external_api_streaming" if is_zai else f"local_runtime_streaming:{args.base_url}",
        "endpoint": args.base_url or ZAI_BASE_URL,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "git_rev": git_rev(),
        "env": env_info(),
        "scenario": args.scenario,
        "prompt_sha256": sha256(prompt),
        "prompt_chars": len(prompt),
        "generation_params": {"temperature": 0.1, "max_tokens_by_mode": max_tokens,
                              "stream": True,
                              "note": "TPOT/ITL are client-observed chunk cadence of the hosted API; gateway batching can merge several tokens into one flush"},
        "results": results,
    }
    path = save_json(f"tokens_{args.scenario}", payload)
    print(f"\nSaved → {path}")


# --------------------------------------------------------------------------- #
# Load test: concurrency sweep
# --------------------------------------------------------------------------- #

async def load_worker(client, model: str, prompt: str, max_tokens: int,
                      disable_thinking: bool, sem: asyncio.Semaphore,
                      budget_s: float, allow_thinking_flag: bool = True) -> dict[str, Any]:
    async with sem:
        rec = await stream_once(client, model, prompt, max_tokens, disable_thinking,
                                allow_thinking_flag)
    total = (rec.t_last - rec.t0) if rec.t_last else budget_s
    err = rec.error or ""
    kind = "ok"
    if "429" in err or "rate" in err.lower():
        kind = "rate_limit"
    elif "Timeout" in err or rec.error and rec.t_last is None:
        kind = "timeout"
    elif rec.error:
        kind = "api_error"
    elif rec.n_answer_chunks == 0:
        kind = "empty_stream"
    return {
        "kind": kind,
        "total_s": round(total, 3),
        "ttft_answer_s": round(rec.t_first_answer, 3) if rec.t_first_answer is not None else None,
        "n_answer_chunks": rec.n_answer_chunks,
        "completion_tokens": rec.usage.completion_tokens if rec.usage else None,
        "error": err[:200] or None,
    }


async def cmd_load(args: argparse.Namespace) -> None:
    client = get_zai_client(args.base_url)
    is_zai = (args.base_url or ZAI_BASE_URL) == ZAI_BASE_URL
    model_id = args.model
    api_model = args.api_model or ZAI_MODELS.get(model_id, model_id.split(":", 1)[-1])
    prompt = long_prompt() if args.scenario == "long" else SHORT_PROMPT
    max_tokens = 700 if args.scenario == "long" else args.max_tokens
    disable = args.think_mode == "off" and is_zai

    # Which thinking modes the model supports (probe, not assumed).
    probe_off = await stream_once(client, api_model, "ping", 8, True, allow_thinking_flag=is_zai)
    can_disable = probe_off.error is None
    if disable and not can_disable:
        print(f"  NOTE: {model_id} cannot disable thinking ({probe_off.error}); running as-is.")
        disable = False

    print(f"Load test: {model_id} ({'thinking off' if disable else 'thinking on'}), "
          f"scenario={args.scenario}, max_tokens={max_tokens}")
    print(f"  warm-up: {args.warmup} sequential requests")
    for _ in range(args.warmup):
        r = await load_worker(client, api_model, prompt, max_tokens, disable,
                              asyncio.Semaphore(1), 300.0, allow_thinking_flag=is_zai)
        print(f"    warmup: {r['kind']} {r['total_s']}s")

    levels = [int(x) for x in args.levels.split(",")]
    level_results: list[dict[str, Any]] = []
    raw_requests: list[dict[str, Any]] = []
    rate_limited = False
    for level in levels:
        if rate_limited:
            print(f"  level {level}: SKIPPED (rate limit seen at a lower level)")
            level_results.append({"level": level, "skipped": "rate_limit_seen"})
            continue
        n = args.requests
        sem = asyncio.Semaphore(level)
        t0 = time.perf_counter()
        tasks = [load_worker(client, api_model, prompt, max_tokens, disable, sem, 300.0,
                             allow_thinking_flag=is_zai)
                 for _ in range(n)]
        reqs = await asyncio.gather(*tasks)
        wall = time.perf_counter() - t0
        for i, r in enumerate(reqs):
            raw_requests.append({"level": level, "req": i + 1, **r})

        done = [r for r in reqs if r["kind"] == "ok"]
        errs = [r for r in reqs if r["kind"] != "ok"]
        toks = [r["completion_tokens"] for r in done if r["completion_tokens"]]
        summary = {
            "level": level,
            "sent": n,
            "completed_ok": len(done),
            "errors": {k: sum(1 for r in errs if r["kind"] == k)
                       for k in {r["kind"] for r in errs}},
            "wall_s": round(wall, 2),
            "throughput_rps": round(len(done) / wall, 3) if done else 0.0,
            "throughput_tps": round(sum(toks) / wall, 1) if toks and done else None,
            "latency_total_s": agg([r["total_s"] for r in done]),
            "latency_p99_s": round(pct([r["total_s"] for r in done], 99), 3) if done else None,
            "ttft_answer_s": agg([r["ttft_answer_s"] for r in done if r["ttft_answer_s"] is not None]),
            "mean_completion_tokens": round(statistics.mean(toks), 1) if toks else None,
        }
        level_results.append(summary)
        print(f"  level {level:>2}: ok {len(done)}/{n} in {wall:.1f}s "
              f"rps={summary['throughput_rps']} tok/s={summary['throughput_tps']} "
              f"p50={summary['latency_total_s'].get('p50')}s "
              f"p95={summary['latency_total_s'].get('p95')}s errors={summary['errors']}")
        if any(r["kind"] == "rate_limit" for r in reqs):
            rate_limited = True
            print("  ⚠ rate limit observed — not escalating further levels.")

    payload = {
        "tool": "llm_bench.py load",
        "level": "external_api_concurrency" if is_zai else f"local_runtime_concurrency:{args.base_url}",
        "endpoint": args.base_url or ZAI_BASE_URL,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "git_rev": git_rev(),
        "env": env_info(),
        "model": model_id,
        "api_model": api_model,
        "thinking": "off" if disable else "on",
        "scenario": args.scenario,
        "prompt_sha256": sha256(prompt),
        "generation_params": {"temperature": 0.1, "max_tokens": max_tokens},
        "levels": level_results,
        "raw_requests": raw_requests,
    }
    path = save_json(f"load_{api_model}_{args.scenario}", payload)
    print(f"\nSaved → {path}")


# --------------------------------------------------------------------------- #
# Retrieval metrics (deterministic, no LLM): Recall@k, MRR@5 per mode
# --------------------------------------------------------------------------- #

def cmd_retrieval(args: argparse.Namespace) -> None:
    import importlib.util

    from app.core.docs_rag import retrieve

    spec = importlib.util.spec_from_file_location(
        "test_retrieval_quality",
        BACKEND_DIR / "tests" / "test_retrieval_quality.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    golden = mod.GOLDEN

    def rank_files(chunks: list[Any]) -> list[str]:
        files: list[str] = []
        for ch in chunks:
            f = ch.doc_id.split("#")[0]
            if f not in files:
                files.append(f)
        return files

    modes = {}
    for mode in ("bm25", "vector", "hybrid"):
        hits1 = hits3 = hits5 = 0
        rr5 = 0.0
        for question, expected in golden:
            chunks = retrieve(question, top_k=5, mode=mode)
            files = rank_files(chunks)
            if files[:1] and files[0] == expected:
                hits1 += 1
            if expected in files[:3]:
                hits3 += 1
            if expected in files[:5]:
                hits5 += 1
                rr5 += 1.0 / (files.index(expected) + 1)
        n = len(golden)
        modes[mode] = {
            "recall@1": round(hits1 / n, 3),
            "recall@3": round(hits3 / n, 3),
            "recall@5": round(hits5 / n, 3),
            "mrr@5": round(rr5 / n, 3),
        }
        print(f"  {mode:<7} recall@1={modes[mode]['recall@1']} "
              f"recall@3={modes[mode]['recall@3']} recall@5={modes[mode]['recall@5']} "
              f"mrr@5={modes[mode]['mrr@5']}")

    payload = {
        "tool": "llm_bench.py retrieval",
        "level": "deterministic_retrieval",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "git_rev": git_rev(),
        "golden_pairs": len(golden),
        "golden_source_sha256": sha256((BACKEND_DIR / "tests" / "test_retrieval_quality.py").read_text()),
        "grounding": "file-level (any chunk of the expected file counts)",
        "modes": modes,
    }
    path = save_json("retrieval", payload)
    print(f"\nSaved → {path}")


# --------------------------------------------------------------------------- #
# Context-length matrix: TTFT/total vs input size (1k..32k tokens)
# --------------------------------------------------------------------------- #

def padded_prompt(target_tokens: int) -> str:
    """Analytics-style prompt padded to ~target input tokens (~2.5 chars/token
    for RU text; actual size is recorded from usage.prompt_tokens)."""
    target_chars = int(target_tokens * 2.5)
    parts = ["Ниже фрагмент данных поездок такси.\n"]
    i = 0
    while sum(len(x) for x in parts) < target_chars:
        tariff = "eco" if i % 3 else "comfort"
        parts.append(
            f"ride {i}: user=1{i % 100}, city={i % 7}, date=2026-0{1 + i % 9}-1{i % 9}, "
            f"distance={5 + i % 25}.{i % 10} km, revenue={300 + i % 900}.{i % 10} RUB, "
            f"tariff={tariff}, status=done. "
        )
        i += 1
    parts.append("\n\nВопрос: одним предложением — сколько примерно строк в этом фрагменте?")
    return "".join(parts)


async def cmd_ctx(args: argparse.Namespace) -> None:
    client = get_zai_client(args.base_url)
    is_zai = (args.base_url or ZAI_BASE_URL) == ZAI_BASE_URL
    api_model = args.api_model or ZAI_MODELS.get(args.model, args.model.split(":", 1)[-1])
    sizes = [int(x) for x in args.sizes.split(",")]
    # GLM context windows (documented by provider): 4.x/5.x >= 128k — sizes up
    # to 32k are safe. For non-Z.ai endpoints the caller is responsible.
    results = []
    print(f"Context matrix: {args.model} (thinking off), sizes={sizes}, runs={args.runs}")
    for size in sizes:
        if size > 100_000:
            print(f"  {size}: SKIPPED — exceeds safe context budget")
            results.append({"target_tokens": size, "skipped": "context_budget"})
            continue
        prompt = padded_prompt(size)
        runs = []
        for i in range(args.runs):
            rec = await stream_once(client, api_model, prompt, 96, True,
                                    allow_thinking_flag=is_zai)
            m = rec_to_metrics(rec)
            m["run"] = i + 1
            runs.append(m)
            print(f"  {size:>6} tok run{i + 1}: {'ok' if m['ok'] else 'ERR ' + str(m.get('error'))[:60]} "
                  f"in_tok={m.get('usage_prompt_tokens')} ttft={m['ttft_answer_s']}s total={m['total_s']}s",
                  flush=True)
        ok = [r for r in runs if r["ok"] and r.get("usage_prompt_tokens")]
        entry = {
            "target_tokens": size,
            "prompt_chars": len(prompt),
            "n_ok": len(ok),
            "measured_prompt_tokens": agg([r["usage_prompt_tokens"] for r in ok]),
            "ttft_answer_s": agg([r["ttft_answer_s"] for r in ok if r["ttft_answer_s"] is not None]),
            "total_s": agg([r["total_s"] for r in ok]),
        }
        entry["derived_prefill_tok_per_s"] = (
            round(entry["measured_prompt_tokens"]["p50"] / entry["ttft_answer_s"]["p50"], 1)
            if ok and entry["ttft_answer_s"].get("p50") else None
        )
        results.append({"summary": entry, "runs": runs})
    payload = {
        "tool": "llm_bench.py ctx",
        "level": "external_api_prefill_vs_context",
        "note": "TTFT here = queue + prefill + first token; derived_prefill_tok_per_s is an "
                "UPPER BOUND on prefill speed (queue time included), client-observed",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "git_rev": git_rev(),
        "env": env_info(),
        "endpoint": args.base_url or ZAI_BASE_URL,
        "model": args.model,
        "api_model": api_model,
        "thinking": "off",
        "generation_params": {"temperature": 0.1, "max_tokens": 96, "stream": True},
        "results": results,
    }
    path = save_json(f"ctx_{api_model}", payload)
    print(f"\nSaved → {path}")


# --------------------------------------------------------------------------- #
# Prefix-cache black-box probe (mechanism NOT verifiable on external API)
# --------------------------------------------------------------------------- #

PREFIX_BASE = (
    "Ты — аналитик. Ниже схема аналитической базы RideGo.\n"
    "fact_rides(ride_id, user_id, city_id, ride_date, ride_time, distance_km, "
    "revenue_rub, is_partner, tariff, status); dim_city(city_id, city_name, "
    "region, country, is_inhouse); dim_user(user_id, city_id, registered_at); "
    "fact_subscriptions(sub_id, user_id, status, brand, cancel_reason).\n"
    + "Дополнительный контекст: тарифы eco/comfort/prev, партнёрские поездки is_partner=1 "
    "исключаются из финансовых метрик, выручка считается в рублях. " * 40
)


async def cmd_prefix(args: argparse.Namespace) -> None:
    """Compare TTFT with a REPEATED shared prefix vs UNIQUE fresh prefixes.

    This is a black-box client probe of the hosted endpoint: a TTFT drop on
    repeated prefixes is CONSISTENT WITH server-side prefix caching but does
    NOT prove it (queue placement, load balancers and other effects are not
    observable). Explicitly labeled as such in the artifact.
    """
    import random

    client = get_zai_client(args.base_url)
    is_zai = (args.base_url or ZAI_BASE_URL) == ZAI_BASE_URL
    api_model = args.api_model or ZAI_MODELS.get(args.model, args.model.split(":", 1)[-1])
    rng = random.Random(42)
    questions = [
        "Сколько поездок в базе?",
        "Какова средняя выручка?",
        "Сколько городов присутствия?",
        "Какая доля партнёрских поездок?",
        "Сколько пользователей зарегистрировано?",
    ]

    async def measure(prefix: str, q: str) -> dict[str, Any]:
        rec = await stream_once(client, api_model, prefix + "\n\nВопрос: " + q,
                                48, True, allow_thinking_flag=is_zai)
        m = rec_to_metrics(rec)
        m["question"] = q
        return m

    # Pass 1 and 2: SAME shared prefix, shuffled question order.
    same_runs: list[dict[str, Any]] = []
    for pass_i in (1, 2):
        order = questions[:]
        rng.shuffle(order)
        for q in order:
            m = await measure(PREFIX_BASE, q)
            m["pass"] = pass_i
            same_runs.append(m)
            print(f"  same-prefix pass{pass_i}: ttft={m['ttft_answer_s']}s in_tok={m.get('usage_prompt_tokens')} ({q[:24]})",
                  flush=True)

    # Pass 3: UNIQUE prefix per request (same length, random noise injected).
    fresh_runs: list[dict[str, Any]] = []
    for q in questions:
        noise = f"\nСессия анализа #{rng.randrange(16**8):08x}."
        m = await measure(PREFIX_BASE + noise, q)
        m["pass"] = 3
        fresh_runs.append(m)
        print(f"  fresh-prefix: ttft={m['ttft_answer_s']}s in_tok={m.get('usage_prompt_tokens')} ({q[:24]})",
              flush=True)

    def med_ttft(runs):
        vals = sorted(r["ttft_answer_s"] for r in runs if r["ttft_answer_s"] is not None)
        return round(statistics.median(vals), 3) if vals else None

    same_p2 = [r for r in same_runs if r["pass"] == 2]
    payload = {
        "tool": "llm_bench.py prefix",
        "level": "external_api_blackbox_probe",
        "caveat": "TTFT difference is consistent with prefix caching but the mechanism "
                  "CANNOT be verified on a hosted endpoint; not a self-inference metric",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "git_rev": git_rev(),
        "env": env_info(),
        "endpoint": args.base_url or ZAI_BASE_URL,
        "model": args.model,
        "api_model": api_model,
        "thinking": "off",
        "prefix_chars": len(PREFIX_BASE),
        "generation_params": {"temperature": 0.1, "max_tokens": 48, "stream": True},
        "summary": {
            "ttft_median_same_prefix_pass1": med_ttft([r for r in same_runs if r["pass"] == 1]),
            "ttft_median_same_prefix_pass2": med_ttft(same_p2),
            "ttft_median_fresh_prefix": med_ttft(fresh_runs),
            "mean_prompt_tokens": round(statistics.mean(
                [r["usage_prompt_tokens"] for r in same_runs + fresh_runs if r.get("usage_prompt_tokens")]), 1),
        },
        "raw": {"same_prefix": same_runs, "fresh_prefix": fresh_runs},
    }
    print("\n  ⇒ TTFT medians: same-prefix p1="
          f"{payload['summary']['ttft_median_same_prefix_pass1']}s p2={payload['summary']['ttft_median_same_prefix_pass2']}s "
          f"| fresh-prefix={payload['summary']['ttft_median_fresh_prefix']}s")
    path = save_json(f"prefix_{api_model}", payload)
    print(f"Saved → {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM inference benchmark (external API level)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_tokens = sub.add_parser("tokens", help="token-level TTFT/TPOT/ITL per model")
    p_tokens.add_argument("--models", default="zai:glm-4.6,zai:glm-5.2,zai:glm-5.3-flash")
    p_tokens.add_argument("--runs", type=int, default=3)
    p_tokens.add_argument("--scenario", choices=["short", "long"], default="short")
    p_tokens.add_argument("--think-mode", choices=["both", "think_off", "think_on"], default="both")
    p_tokens.add_argument("--base-url", default=None, help="override API endpoint (e.g. local ollama http://127.0.0.1:11434/v1)")
    p_tokens.add_argument("--api-model", default=None, help="override API model name")
    p_tokens.set_defaults(func=cmd_tokens)

    p_load = sub.add_parser("load", help="concurrency sweep")
    p_load.add_argument("--model", default="zai:glm-4.5-flash")
    p_load.add_argument("--levels", default="1,2,5,10")
    p_load.add_argument("--requests", type=int, default=10)
    p_load.add_argument("--warmup", type=int, default=2)
    p_load.add_argument("--scenario", choices=["short", "long"], default="short")
    p_load.add_argument("--max-tokens", type=int, default=200)
    p_load.add_argument("--think-mode", choices=["off", "on"], default="off")
    p_load.add_argument("--base-url", default=None, help="override API endpoint")
    p_load.add_argument("--api-model", default=None, help="override API model name")
    p_load.set_defaults(func=cmd_load)

    p_ret = sub.add_parser("retrieval", help="deterministic RAG retrieval metrics")
    p_ret.set_defaults(func=cmd_retrieval)

    p_ctx = sub.add_parser("ctx", help="context-length matrix (TTFT vs input size)")
    p_ctx.add_argument("--model", default="zai:glm-4.5-flash")
    p_ctx.add_argument("--sizes", default="1000,4000,8000,16000,32000")
    p_ctx.add_argument("--runs", type=int, default=2)
    p_ctx.add_argument("--base-url", default=None)
    p_ctx.add_argument("--api-model", default=None)
    p_ctx.set_defaults(func=cmd_ctx)

    p_pre = sub.add_parser("prefix", help="prefix-cache black-box probe (repeated vs fresh prefix)")
    p_pre.add_argument("--model", default="zai:glm-4.5-flash")
    p_pre.add_argument("--base-url", default=None)
    p_pre.add_argument("--api-model", default=None)
    p_pre.set_defaults(func=cmd_prefix)

    args = parser.parse_args()
    if args.cmd in ("tokens", "load", "ctx", "prefix"):
        asyncio.run(args.func(args))
    else:
        args.func(args)


if __name__ == "__main__":
    main()
