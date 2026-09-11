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

# 📊 AI Data Pilot — multi-agent analytics

> **Live demo:** [lyakoway-ai-data-pilot.hf.space](https://lyakoway-ai-data-pilot.hf.space/)

Natural-language analytics: SQL, a chart and an explanation. Two specialized
agents — **Data Agent** (Text-to-SQL, charts, Excel) and **Knowledge Agent**
(RAG over docs and uploads) — plus a dual auto-router. In the Russian UI they
are named Олег and Ксюша.

This repository is an independently built **personal demo** on a test dataset
(RideGo ~21k rides). It is the same problem class as a production multi-agent
analytics platform — **not** that system's source code or production databases.
Published SQL figures below match the case page
[lyakoway.vercel.app/portfolio/ai-data-pilot](https://lyakoway.vercel.app/portfolio/ai-data-pilot).
Production databases stay under NDA.

[![Demo](https://img.shields.io/badge/demo-lyakoway--ai--data--pilot.hf.space-ff9d00)](https://lyakoway-ai-data-pilot.hf.space/)
![backend](https://img.shields.io/badge/backend-FastAPI-009688)
![frontend](https://img.shields.io/badge/frontend-React%2019%20%2B%20Vite-61dafb)
![tests](https://img.shields.io/badge/tests-174%20passed-brightgreen)
![sources](https://img.shields.io/badge/sources-PostgreSQL%20·%20ClickHouse%20·%20CSV%20·%20Excel-6366f1)

<sub>On the free tier the demo Space may fall asleep — the first visit after
idle takes ~1 min. The vector model (fastembed) loads on the Knowledge Agent's
first search (~10 s).</sub>

**What shipped — three measured decisions:**

1. **Python counts** — trends, percentages, top-N and z-score run in a Python
   layer. The LLM writes prose; it never calculates business figures.
2. **SQL failure is a contract** — SQL Guard (SELECT-only) + two rewrites + an
   honest error. A failed query is never silently replaced with a fake result.
3. **Two agents, not one prompt** — Data Agent for SQL, Knowledge Agent for
   docs, dual router; the decision is visible in the SSE trace.

Headline SQL quality on the public test pack (GLM-4.6): **~85% normalized
result correctness** (same numbers after dropping aliases, row order and number
format — not string exact-match). **~98% execution** only means the query ran.
**2h → 2min** report prep is MTS production, not this demo.

## Key capabilities

- 🧭 **Dual auto-routing** — by agent (data → Data Agent, docs → Knowledge Agent)
  and by source (question → the right DB). Both routers: LLM classification +
  deterministic heuristic fallback. Manual switches remain as override.
- 👤 **Data Agent**
  - **Agent Loop (ReAct)** — `database_query → calculate → analyze → create_chart → finish`. Prompt-based tool-calling works with every provider, including offline Demo.
  - **Execution trace (SSE)** — live steps; SQL, row_count, insights.
  - **Self-correction** — failed SQL comes back with the DB error; the agent rewrites (up to 2 rounds).
  - **Deterministic analytics** — Python computes the numbers; the LLM only interprets.
- 👩‍💻 **Knowledge Agent**
  - **Hybrid search** — BM25-IDF + vector embeddings (fastembed, 50+ languages). Retrieval ablation lives on the RAG Chat case, not here.
  - **Uploads** — PDF, Word, Excel, CSV, TXT, MD; Excel is also a SQL table for the Data Agent.
  - **Inline citations `[1]`** and a document viewer (PDF page, DOCX, Excel table).
- 🗄️ **Sources** — PostgreSQL and ClickHouse (UI or env, schema introspection, dialect prompts); virtual **All uploads** with cross-file JOINs.
- ⚡ **Parameterized scenarios** — templates with `{period}`, `{group_by}`.
- 👍 **Feedback** — 👍/👎 analytics by agent.
- 🤖 **13 model configs** — OpenAI, Anthropic, Z.ai, Ollama + offline Demo. **GLM-4.6 is the default** (the ~85% SQL eval ran on it).

## Engineering approach

Probabilistic LLM reasoning is separated from deterministic application logic:

- **LLM** — intent, routing, SQL generation, tool choice, prose
- **Python** — numbers, trends, top-N, anomalies, result validation
- **SQL Guard** — validate generated SQL before execution
- **Self-correction** — failed SQL is returned with the DB error, limited retries
- **Observability** — every agent step streams over SSE

End-to-end latency is **tens of seconds** (LLM plan + answer), not a page-load.
Streamed steps make the wait inspectable.

## Architecture

```
[React dashboard] ──/api──▶ [FastAPI]
                              ├─ Agent router: data → Data Agent, docs → Knowledge Agent
                              ├─ Data Agent: schema → SQL → guard → analytics → chart/xlsx
                              │    ↑ Agent Loop (ReAct): multi-step tool-calling
                              │    ↑ self-correction (2 retry rounds)
                              │    ↑ deterministic insights (Python, not LLM math)
                              │    ↑ execution trace streamed via SSE
                              ├─ Source router: question → the right DB
                              ├─ DataSources: RideGo | PostgreSQL | ClickHouse
                              │              | CSV/Excel (SQL + RAG) | All uploads (JOIN)
                              └─ Knowledge Agent: hybrid RAG (BM25-IDF + vector fastembed)
                                        over built-in docs + uploaded files

App DB (SQLite): scenarios · datasource metadata · feedback · documents · chunks
Analytics DB:    RideGo (seeded) · uploaded CSV/Excel tables
Files:           data/uploads/ (originals for the document viewer)
```

**Storage:** scenarios, source metadata, feedback, documents and chunks live in
`app.db` (SQLite). Analytics: `ridego.db` (RideGo demo domain: `dim_city`,
`dim_user`, `fact_rides`, `fact_subscriptions`) and `csv_sources.db` (uploaded
tables). CSV/Excel enter **both** pipelines: a SQL table for the Data Agent and
text chunks for the Knowledge Agent. Source passwords stay server-side and never
return to the frontend.

## Engineering decisions

### Deterministic analytics

The LLM does not compute business metrics. Numerical work and anomaly detection
run in Python (`analytics.py`). Figures in the answer always come from the DB or
Python — never from generation.

### Bounded agent execution

The ReAct loop has a hard step limit (`MAX_LOOP_STEPS = 6`); SQL self-correction
is two repair rounds (`MAX_SQL_REPAIR_ROUNDS = 2`). Worst case is an honest
refusal, not a hung tool loop.

### Why two agents

Data analysis and document search have different tools, limits and failure modes.
Routing to a specialized agent keeps each workflow bounded and measurable.

### LLM vs deterministic code

| Responsibility | Implementation |
|---|---|
| Intent | LLM |
| Agent routing | LLM classification + deterministic heuristic (fallback) |
| Source routing | LLM over schemas + heuristic (fallback) |
| SQL generation | LLM |
| SQL validation | Python (SQL Guard: read-only, one statement, forbidden keywords) |
| Execution limits | Python: timeout 8 s (30 s for remote PostgreSQL), max 500 rows |
| SQL execution | SQLite / PostgreSQL / ClickHouse |
| Numbers (trends, top-N, percentages) | Python |
| Anomaly detection | Python (z-score) |
| Charts | Python prepares spec + data; React / Recharts renders |
| Document search | Python: BM25-IDF + vector embeddings (fastembed) |
| Final answer text | LLM |

**The LLM is not used where ordinary code is more reliable.**

## Evaluation & Benchmarks

Figures below match
[the case page](https://lyakoway.vercel.app/portfolio/ai-data-pilot).
SQL eval is on the **public RideGo test pack**, not MTS production databases.

**174 pytest tests:** Agent Loop, SQL guard, self-correction, retrieval
(Recall@1/5 · MRR for BM25 / Vector / Hybrid), numeric analytics contracts,
routing, sources. Isolated temp SQLite, no API keys required.

Harness: `backend/scripts/evaluate.py` — methodology in
[EVALUATION.md](backend/EVALUATION.md).

**SQL quality — held-out eval, live GLM-4.6 run:**

| Metric | Result |
|---|---|
| **Normalized result correctness** (same numbers after canonicalization) | **~85%** |
| SQL Execution Accuracy (generated SQL ran — not the same as correct) | ~98% |

Headline quality is **~85% normalized**: same numbers after ignoring column
aliases, row order and number format. Strict string exact-match is not a
headline. ~98% only means the query executed.

**Default model: GLM-4.6** — the ~85% eval ran on it. Faster models are not
automatically better at SQL.

### Latency by model (live run)

| Model | Plan (LLM) | Execution (DB) | Answer (LLM) | Total | SQL ok |
|---|---|---|---|---|---|
| **GLM-4.6 (Z.ai) — default** | 13.5 s | 12 ms | 16.3 s | ~29.8 s | 2/3 |
| GLM-5.2 (Z.ai) | 7.0 s | 6 ms | 9.4 s | ~16.4 s | 3/3 |
| GLM-5.3-flash (Z.ai) | 7.0 s | 8 ms | 4.8 s | ~11.9 s | 2/3 |
| GLM-5.3 (Z.ai) | 13.8 s | 11 ms | 5.1 s | ~19.0 s | 1/3 |

<sub>Medians of 3 runs of **one** question through the full cycle (plan → DB →
answer). External API latency varies. GLM-4.6 stays the default because the
~85% SQL eval ran on it. GLM-5.2 is faster on this sample; GLM-5.3-flash is
faster still but weaker SQL. Script: `python scripts/latency_benchmark.py`
(from `backend/`).</sub>

Full cycle is **~16–30 s** depending on the model — provider floor on plan +
answer, not a sub-second dashboard. Streamed steps; local sources skip the
external handshake.

### Model registry (13 configs)

| Provider | Models |
|---|---|
| Demo (offline) | scripted scenario, no keys |
| OpenAI | GPT-4o, GPT-4o mini |
| Anthropic | Claude Sonnet 5, Claude Opus 4.8 |
| Z.ai | GLM-5.3-flash, GLM-5.3, GLM-5.2, **GLM-4.6 (default)**, GLM-4.5-flash |
| Ollama (local) | Llama 3.2 3B, Llama 3.1 8B, Mistral |

## Known Limitations

- **Prompt injection via documents.** The Knowledge Agent accepts arbitrary
  files; their contents enter the LLM context. No injection sanitization on the
  RAG side. SQL is covered by the guard (read-only + limits).
- **Eval sets are small.** Routing and retrieval golden sets are author-written
  and do not replace the SQL headline (~85% normalized on GLM-4.6). Retrieval
  pytest (n=8 pairs) currently scores 1.0 on every mode — it does not
  discriminate BM25 vs vector vs hybrid.
- **Self-correction is unit-tested, not quantified end-to-end** on the headline
  SQL run (failed-SQL repair rate is not a published metric).
- **Single-turn.** No conversation memory; each request is independent.
- **Cost is not a headline metric.** Latency is measured per model; tokens /
  cost per query are not.

## Tech Stack

- **Backend:** Python, FastAPI, SQLAlchemy 2 (SQLite / PostgreSQL / ClickHouse), fastembed, openpyxl
- **Frontend:** React 19, Vite, Recharts, xlsx, docx-preview
- **LLM:** OpenAI / Anthropic / Z.ai / Ollama — prompt-based tool-calling, Demo soft-fallback
- **Quality:** pytest + pytest-asyncio, golden-set evaluation harness
- **Infra:** Docker (multi-stage: frontend build → FastAPI static), docker compose

## Quick Start

```bash
chmod +x dev.sh
./dev.sh
```

- UI: http://localhost:5173
- API: http://localhost:8001/docs  (port 8001, so it does not clash with RAG Chat on 8000)

**Demo (offline)** works without keys.

## Configuration

`backend/.env` ← from `backend/.env.example`:

```env
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
ZAI_API_KEY=
OLLAMA_BASE_URL=http://localhost:11434
DEMO_SCALE=small   # or full for denser data
```

**Test PostgreSQL** (local): `docker compose -f docker-compose.test.yml up -d`
→ `demo:demo@localhost:5433/shop` or env `POSTGRES_URL`.

## Testing

```bash
cd backend
pip install -r requirements.txt   # includes pytest, pytest-asyncio
pytest -v
```

Coverage: analytics layer (`analytics.py`), SQL guard (`sql_guard.py`), Data
Agent self-correction loop, retrieval quality, routing and sources. Isolated
temp SQLite — no API keys.

Golden-set evaluation (provider key required):

```bash
cd backend
python scripts/evaluate.py --suite all --model zai:glm-4.6
python scripts/latency_benchmark.py --models zai:glm-4.6 --runs 3
```

## Deployment

**Hugging Face Spaces:**

1. Space → **Docker**, port **7860** (see YAML at the top of this README).
2. Secrets (optional): `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `ZAI_API_KEY`.
3. Root `Dockerfile` builds the frontend and serves it from FastAPI.

Local image check:

```bash
docker build -t ai-data-pilot .
docker run --rm -p 7860:7860 ai-data-pilot
# → http://localhost:7860
```

**Docker Compose (dev split):**

```bash
docker compose up --build
```
