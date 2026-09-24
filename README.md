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

A multi-agent analytics platform that turns a natural-language question into
SQL, data analysis and a ready analytical result. Two specialized agents —
**Atlas** (Text-to-SQL, charts, Excel) and **Doc** (RAG over
docs and uploads) — plus a dual auto-router.

[![Demo](https://img.shields.io/badge/demo-lyakoway--ai--data--pilot.hf.space-ff9d00)](https://lyakoway-ai-data-pilot.hf.space/)
![backend](https://img.shields.io/badge/backend-FastAPI-009688)
![frontend](https://img.shields.io/badge/frontend-React%2019%20%2B%20Vite-61dafb)
![tests](https://img.shields.io/badge/tests-174%20passed-brightgreen)
![sources](https://img.shields.io/badge/sources-PostgreSQL%20·%20ClickHouse%20·%20CSV%20·%20Excel-6366f1)

<sub>On the free tier the demo Space may fall asleep — the first visit after
idle takes ~1 min. The vector model (fastembed) loads on Doc's
first search (~10 s).</sub>

**What shipped — three measured decisions:**

1. **Python counts** — trends, percentages, top-N and z-score run in a Python
   layer. The LLM writes prose. It never calculates business figures.
2. **SQL failure is a contract** — SQL Guard (SELECT-only) + two rewrites + an
   honest error. A failed query is never silently replaced with a fake result.
3. **Two agents, not one prompt** — Atlas for SQL, Doc for
   docs. Dual router — the decision is visible in the SSE trace.

Headline SQL quality on the public test pack (GLM-4.6): **~85% normalized
result correctness** (same numbers after dropping aliases, row order and number
format — not string exact-match). **~98% execution** only means the query ran.
**2h → 2min** report prep is from production experience, not this demo.

**Scale and validation:**

|               |                                     |
| ------------- | ----------------------------------- |
| **~15**       | analysts · internal pilot           |
| **~80**       | scenarios / week                    |
| **~85%**      | normalized SQL · held-out           |
| **2h → 2min** | report prep · production experience |
| **174**       | pytest tests                        |
| **Python**    | counts · LLM writes prose           |

<sub>Scale figures are from internal production experience. The demo and SQL
eval in this repo run on a public test dataset (RideGo ~21k rides).</sub>

## Key capabilities

- 🧭 **Dual auto-routing** — by agent (data → Atlas, docs → Doc)
  and by source (question → the right DB). Both routers: LLM classification +
  deterministic heuristic fallback. Manual switches remain available as an override.
- 👤 **Atlas**
  - **Agent Loop (ReAct)** — `database_query → calculate → analyze → create_chart → finish`. Prompt-based tool-calling works with every provider, including offline Demo.
  - **Execution trace (SSE)** — live steps: SQL, row_count, insights.
  - **Self-correction** — failed SQL comes back with the DB error. The agent rewrites (up to 2 rounds).
  - **Deterministic analytics** — Python computes the numbers. The LLM only interprets.
- 👩‍💻 **Doc**
  - **Hybrid search** — BM25-IDF (Russian stemming) + vector embeddings (fastembed, 50+ languages). Retrieval ablation lives on the RAG Chat case, not here.
  - **Uploads** — PDF, Word, Excel, CSV, TXT, MD. Excel is also a SQL table for Atlas.
  - **Inline citations `[1]`** and a document viewer (PDF page, DOCX, Excel table).
- 🗄️ **Sources** — PostgreSQL and ClickHouse (UI or env, schema introspection, dialect prompts), virtual **All uploads** with cross-file JOINs.
- ⚡ **Parameterized scenarios** — templates with `{period}`, `{group_by}`.
- 👍 **Feedback** — 👍/👎 analytics by agent.
- 🚦 **Status transparency** — every response carries an explicit `ok / demo / partial / error` status. No silent fallback.
- 🤖 **13 model configs** — OpenAI, Anthropic, Z.ai, Ollama + offline Demo. **GLM-4.6 is the default** (the ~85% SQL eval ran on it).

## Use cases

Data lives in databases and Excel, and getting a number usually means filing an
analyst ticket. SQL quality and latency here are measured on a public test
dataset.

1. **Self-service analytics for business** — a manager asks "revenue by region
   for 90 days" and gets a table with a chart — no analyst ticket, no queue.
   End-to-end latency is tens of seconds (LLM plan + answer), not a page-load.
2. **Root-cause analysis of metric drops** — "Why did revenue drop in July?" —
   the agent compares periods, computes the change, finds contributing factors
   via the agent loop and shows the analysis step by step.
3. **Analyzing uploaded Excel exports** — drag a data file into the window and
   ask questions about it: Atlas builds SQL over the auto-generated
   schema, Doc searches the content, cross-file JOINs work out
   of the box.
4. **A single entry point to heterogeneous databases** — PostgreSQL for
   transactions and ClickHouse for billion-row analytics under one interface,
   with the SQL dialect adapted automatically per source.

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
                              ├─ Agent router: data → Atlas, docs → Doc
                              ├─ Atlas: schema → SQL → guard → analytics → chart/xlsx
                              │    ↑ Agent Loop (ReAct): multi-step tool-calling
                              │    ↑ self-correction (2 retry rounds)
                              │    ↑ deterministic insights (Python, not LLM math)
                              │    ↑ execution trace streamed via SSE
                              ├─ Source router: question → the right DB
                              ├─ DataSources: RideGo | PostgreSQL | ClickHouse
                              │              | CSV/Excel (SQL + RAG) | All uploads (JOIN)
                              └─ Doc: hybrid RAG (BM25-IDF + vector fastembed)
                                        over built-in docs + uploaded files

App DB (SQLite): scenarios · datasource metadata · feedback · documents · chunks
Analytics DB:    RideGo (seeded) · uploaded CSV/Excel tables
Files:           data/uploads/ (originals for the document viewer)
```

**Storage:** scenarios, source metadata, feedback, documents and chunks live in
`app.db` (SQLite). Analytics: `ridego.db` (RideGo demo domain: `dim_city`,
`dim_user`, `fact_rides`, `fact_subscriptions`) and `csv_sources.db` (uploaded
tables). CSV/Excel enter **both** pipelines: a SQL table for Atlas and
text chunks for Doc. Source passwords stay server-side and never
return to the frontend.

## Engineering decisions

### Deterministic analytics

The LLM does not compute business metrics. Numerical work and anomaly detection
run in Python (`analytics.py`). Figures in the answer always come from the DB or
Python — never from generation.

### Bounded agent execution

The ReAct loop has a hard step limit (`MAX_LOOP_STEPS = 6`). SQL self-correction
is two repair rounds (`MAX_SQL_REPAIR_ROUNDS = 2`). Worst case is an honest
refusal, not a hung tool loop.

### System limits

| Mechanism                                   | Value                      |
| ------------------------------------------- | -------------------------- |
| SQL timeout: local sources                  | 8 s                        |
| SQL timeout: remote PostgreSQL / ClickHouse | 30 s                       |
| Row limit per query                         | 500 rows                   |
| Self-correction rounds                      | 2 (up to 3 attempts total) |
| Agent Loop: max steps                       | 6                          |
| Upload limit                                | 25 MB · 50,000 rows        |

<sub>Timeouts use a ThreadPoolExecutor with `future.result(timeout)` — a heavy
query never blocks the event loop. Remote databases get a larger budget:
cross-network connect plus handshake takes seconds.</sub>

### Why two agents

Data analysis and document search have different tools, limits and failure modes.
Routing to a specialized agent keeps each workflow bounded and measurable.

## Engineering findings

- **LLMs are unreliable at arithmetic.** Early versions produced plausible but
  incorrect percentages. Decision: all numerical computation moved into a
  deterministic Python layer.
- **Silent fallbacks destroy trust.** An answer without a mode badge looked
  like a real one. Decision: explicit `ok / demo / partial / error` statuses
  on every response.
- **Dirty Excel files are the norm.** A real upload broke on a merged header
  row and duplicate columns. Decision: resilient parsers that detect the header
  row, plus tests on dirty files.
- **Keyword search without stemming is useless for Russian.** «Затраты» did
  not match «расходы». Decision: Russian stemming for BM25 + a vector channel
  for semantics and multilinguality.
- **Routing saves trust, not steps.** A single universal prompt blurred the
  agent's role. Decision: two specialized agents + two-level routing with a
  visible decision in the trace.
- **Model pick is a quality/latency trade-off, not a default from a blog.** On
  the latency table (3 runs, one question) GLM-5.2 is ~16 s vs GLM-4.6 ~30 s.
  GLM-5.3-flash is faster still but weaker at SQL. GLM-4.6 stays the default
  because the ~85% held-out eval ran on it.

## Evaluation & Benchmarks

Figures below match
[the case page](https://lyakoway.vercel.app/portfolio/ai-data-pilot).
SQL eval is on the **public RideGo test pack**, not production databases.

**174 pytest tests:** Agent Loop, SQL guard, self-correction, retrieval
(Recall@1/5 · MRR for BM25 / Vector / Hybrid), numeric analytics contracts,
routing, sources. Isolated temp SQLite, no API keys required.

**Test coverage — 174 pytest tests:**

| Component                               | Tests | Covers                                                         |
| --------------------------------------- | ----- | -------------------------------------------------------------- |
| Agent Loop (ReAct)                      | 22    | tool calling, self-correction, step limit, fallback            |
| SQL guard                               | 18    | DML bans, multi-statement, timeouts, row limit                 |
| Analytics layer                         | 16    | trends, z-score threshold, top-N, RU/EN highlights             |
| Sources (CSV/Excel/PG/CH)               | 27    | parsers, introspection, name dedup, password masking           |
| Routers (agent + source)                | 26    | heuristic, LLM fallback, honest errors                         |
| Doc RAG + app.db            | 20    | steps, sources, citations, feedback stats                      |
| Parameterized scenarios                 | 10    | substitution, defaults, migration                              |
| Other (app_db, export)                  | 22    | CRUD, feedback, DB isolation                                   |
| Retrieval quality + analytics contracts | 13    | Recall@1/5, MRR (BM25/Vector/Hybrid), numeric golden contracts |

<sub>Tests run on isolated temp SQLite databases with fake providers — no API
keys required, full run ~50 s.</sub>

Harness: `backend/scripts/evaluate.py` — methodology in
[EVALUATION.md](backend/EVALUATION.md).

**SQL quality — held-out eval, live GLM-4.6 run:**

| Metric                                                                  | Result   |
| ----------------------------------------------------------------------- | -------- |
| **Normalized result correctness** (same numbers after canonicalization) | **~85%** |
| SQL Execution Accuracy (generated SQL ran — not the same as correct)    | ~98%     |

Headline quality is **~85% normalized**: same numbers after ignoring column
aliases, row order and number format. Strict string exact-match is not a
headline. ~98% only means the query executed.

**Default model: GLM-4.6** — the ~85% eval ran on it. Faster models are not
automatically better at SQL.

### Latency by model (live run)

| Model                        | Plan (LLM) | Execution (DB) | Answer (LLM) | Total   | SQL ok |
| ---------------------------- | ---------- | -------------- | ------------ | ------- | ------ |
| **GLM-4.6 (Z.ai) — default** | 13.5 s     | 12 ms          | 16.3 s       | ~29.8 s | 2/3    |
| GLM-5.2 (Z.ai)               | 7.0 s      | 6 ms           | 9.4 s        | ~16.4 s | 3/3    |
| GLM-5.3-flash (Z.ai)         | 7.0 s      | 8 ms           | 4.8 s        | ~11.9 s | 2/3    |
| GLM-5.3 (Z.ai)               | 13.8 s     | 11 ms          | 5.1 s        | ~19.0 s | 1/3    |

<sub>Medians of 3 runs of **one** question through the full cycle (plan → DB →
answer). External API latency varies. GLM-4.6 stays the default because the
~85% SQL eval ran on it. GLM-5.2 is faster on this sample. GLM-5.3-flash is
faster still but weaker at SQL. Script: `python scripts/latency_benchmark.py`
(from `backend/`).</sub>

Full cycle is **~16–30 s** depending on the model — provider floor on plan +
answer, not a sub-second dashboard. Streamed steps — local sources skip the
external handshake.

### Model registry (13 configs)

| Provider       | Models                                                                |
| -------------- | --------------------------------------------------------------------- |
| Demo (offline) | scripted scenario, no keys                                            |
| OpenAI         | GPT-4o, GPT-4o mini                                                   |
| Anthropic      | Claude Sonnet 5, Claude Opus 4.8                                      |
| Z.ai           | GLM-5.3-flash, GLM-5.3, GLM-5.2, **GLM-4.6 (default)**, GLM-4.5-flash |
| Ollama (local) | Llama 3.2 3B, Llama 3.1 8B, Mistral                                   |

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
- API: http://localhost:8001/docs (port 8001, so it does not clash with RAG Chat on 8000)

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
4. The image ships a built-in demo PostgreSQL (database `shop`, user `demo`,
   `127.0.0.1:5432`, localhost-only) — the **Connect PostgreSQL** preset works
   out of the box.

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
