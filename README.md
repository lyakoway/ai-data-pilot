---
title: AI Data Pilot
emoji: 📊
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

English | [Русский](README.ru.md)

# AI Data Pilot

> Note: the Russian README is the older HF-demo edition and is intentionally
> not synchronized with this technical README — this file is the canonical
> engineering description.

Multi-agent analytics platform: natural-language questions → governed SQL,
deterministic Python analytics, grounded RAG answers. FastAPI + React/TS,
two agents (Atlas — Text-to-SQL, Doc — RAG), per-workspace data isolation,
a reproducible evaluation loop and LLM observability.

> **Live demo:** [lyakoway-ai-data-pilot.hf.space](https://lyakoway-ai-data-pilot.hf.space)
> (offline demo mode — no keys; may sleep on the free tier)

**[Architecture](ARCHITECTURE.md) · [Demo scenario](docs/DEMO.md) ·
[Trade-offs](docs/TRADEOFFS.md) · [Methodology](docs/EVALUATION.md) ·
[Benchmark results](docs/BENCHMARK_RESULTS.md) ·
[Error analysis](docs/ERROR_ANALYSIS.md) ·
[Observability](docs/OBSERVABILITY.md) ·
[GPU inference: blocked, documented](docs/INFERENCE_BENCHMARK_GPU.md)**

```
UI ─POST /api/chat/stream─▶ FastAPI ─┬─ Atlas: LLM plan → SQL Guard → per-ws engine
  (SSE steps, per-step latency)      │           ↳ self-correction ×2, ReAct ≤6 steps
                                      └─ Doc: hybrid retrieval (BM25+vector) →
                                              grounded answer, [n] citations, abstention
Tenant: X-Workspace-Id → TenantContext → workspace_id on every row,
        uploads/<ws>/, csv_db/<ws>.db (physical SQL isolation), exports/<ws>/
```

## Quick start

```bash
./dev.sh                    # UI :5173, API :8001 — offline demo works with no keys
cd backend && pytest -q     # 317 tests, no API keys, ~2.5 min
```

Golden-set evaluation (requires a provider key in `backend/.env`):

```bash
python scripts/eval_sql_full.py --model zai:glm-4.6          # SQL quality, Wilson CI
python scripts/eval_rag.py                                    # retrieval, deterministic
python scripts/compare_sql_evals.py --merge                   # 5-model table
python scripts/check_regression.py --baseline B.json --candidate N.json   # CI gate
```

## Measured results (every number traces to a manifest'ed artifact)

**SQL quality** — 50-case golden set (execution match; ambiguous cases
execution-only; n=44–45 checked per model; Wilson 95% CI):

| Model | Result Accuracy | CI | SQL-plan p50/p95 |
|---|---|---|---|
| GLM-4.5-flash (free) | **65.9%** / 73.3% after prompt fix (paired A/B) | 51.1–78.1 | 15.3 / 69 s |
| GLM-4.6 (default) | 40.0% | 27.0–54.5 | 13.7 / 22.9 s |
| GLM-5.2 | 37.8% | 25.1–52.4 | 5.7 / 12.0 s |

**RAG** — 24-case chunk-level set: hybrid retrieval Recall@1 **1.0** (bm25-only
0.95); answer faithfulness **0.75 → 1.0** and abstention 3/4 → 4/4 after the
grounding/abstention prompt contract (LLM-judge with calibration set; judge
shares the provider family — treat deltas as directional, absolutes as indicative).

**E2E latency** (full cycle, SSE): 16.5–33.5 s p50 by model; first step event
40–90 ms. Latency is LLM-bound (thinking phase alone adds 3–26 s to first
answer token — measured at the token level).

**Tenant isolation** — 30+ tests across metadata, files, SQL and exports;
the cross-tenant SQL regression **fails without the fix** (verified on the
pre-fix commit via git worktree): generated SQL naming a foreign workspace's
table gets `no such table`, not data. All external LLM metrics are
client-observed measurements of the Z.ai API — they say nothing about
self-hosted inference performance.

## Security & reliability contracts

- SQL Guard: SELECT/WITH-only, one statement, keyword blacklist
  (`load_extension`, `readfile`, `sqlite_master`… — found by writing bypass
  tests first), row cap 500, thread-pool timeouts 8/30 s.
- Tenant layer: fail-closed — missing workspace ⇒ refusal, never global
  access; foreign resource ids ⇒ 404 before any filesystem/DB access;
  path traversal rejected at a validated storage boundary.
- Observability: Langfuse adapter, redaction by default (sha256+length),
  `SafeObservation` — instrumentation can never break a request.

## Key decisions (ADRs, Russian originals + EN summaries)

[ADR-001](docs/ADR-001-atlas-doc-quality.md) prompt quality fixes via paired
A/B (incl. the LLM-judge instrument-bug post-mortem) ·
[ADR-002](docs/ADR-002-langgraph.md) LangGraph deferred with explicit
re-entry criteria ·
[ADR-003](docs/ADR-003-multitenancy.md) multi-tenancy → PostgreSQL RLS ·
[ADR-007](docs/ADR-007-per-tenant-csv-sqlite.md) per-tenant sqlite = physical
SQL isolation now, RLS later ·
[ADR-008](docs/ADR-008-exports-scoping.md) workspace-scoped exports +
delete_workspace ·
[all ADRs](docs/) (004–006: data isolation, background jobs, authn)

## What's not verified (honest list)

- **GPU / self-hosted inference** — never run: no NVIDIA GPU, no Docker, no
  cloud credentials to rent with. Blocked and documented with a ready
  unblock plan: [INFERENCE_BENCHMARK_GPU.md](docs/INFERENCE_BENCHMARK_GPU.md).
  No GPU numbers exist anywhere in this repo.
- **Prefix caching** on our own runtime — only a black-box probe on the
  external API (consistent with server-side caching, unprovable); the vLLM
  A/B experiment is scripted and waiting for hardware.
- **Concurrency above 64** parallel ingests — not tested (64 was clean).
- **Self-correction** — 0 repair rounds triggered across 250+ golden-set
  requests (generated SQL always executed first try), so the repair rate is
  unmeasurable on this set — stated, not claimed.
- **RAG judge calibration** — unstable between runs (1/3…3/3); paired deltas
  are the trustworthy signal, absolute faithfulness scores are not.
- **Authentication/billing** — not implemented (P1); the workspace header is
  a dev mechanism, production refuses it without authn.
- Production deployment of this codebase — none; the HF demo runs the
  offline mode.

## Stack

Python 3.13, FastAPI, SQLAlchemy 2, pydantic; React 19 + Vite; SQLite
(app metadata + per-workspace analytics) / PostgreSQL / ClickHouse sources;
Z.ai (default) / OpenAI / Anthropic / Ollama providers; pytest 317;
Docker multi-stage with an embedded demo PostgreSQL.
