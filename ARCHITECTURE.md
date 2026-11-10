# AI Data Pilot — Architecture

Multi-agent analytics platform: natural-language questions become governed
SQL, deterministic Python analytics and grounded RAG answers. Two agents
(Atlas — Text-to-SQL, Doc — RAG), FastAPI backend, React frontend, per-tenant
data isolation, full evaluation loop and LLM observability.

```
                                          ┌────────────────────────┐
   React UI ──POST /api/chat(/stream)──▶  │ FastAPI                │
                                          │  require_tenant (ws)   │
                                          │  CORS allowlist (P0.1) │
                                          └───┬───────────────┬────┘
                                              │               │
                                     ┌────────▼──────┐ ┌──────▼────────┐
                                     │ Atlas (SQL)   │ │ Doc (RAG)     │
                                     │ planner LLM   │ │ hybrid search │
                                     │ SQL Guard     │ │ BM25+vector   │
                                     │ self-corr ×2  │ │ grounded ans. │
                                     │ ReAct loop ≤6 │ │ citations [n] │
                                     └───┬───────┬───┘ └──────┬────────┘
                                         │       │            │
              ┌──────────────────────────▼──┐ ┌──▼────────────▼───────────┐
              │ Tenant data (app.db)        │ │ Per-tenant analytics      │
              │ workspaces/datasources/     │ │ data/csv_db/<ws>.db (P0.4)│
              │ documents/chunks/scenarios/ │ │ + uploads/<ws>/ (P0.3)    │
              │ feedback — workspace_id     │ │ + exports/<ws>/ (P1.1)    │
              └─────────────────────────────┘ └───────────────────────────┘
                                         │
              external DBs: PostgreSQL / ClickHouse (user-registered)
              LLM providers: Z.ai (default) / OpenAI / Anthropic / Ollama
              observability: Langfuse adapter (opt-in, fail-safe)
```

## Components

| Component | Location | Notes |
|---|---|---|
| Atlas agent | `backend/app/agents/atlas.py` | linear pipeline for simple questions; ReAct loop (≤6 steps) for complex; SQL self-correction ≤2 rounds (guard errors never retried) |
| Doc agent | `backend/app/agents/doc.py` | retrieval top-4 → grounded answer with mandatory [n] citations; abstention contract |
| SQL Guard | `backend/app/core/sql_guard.py` | SELECT/WITH-only, keyword blacklist (incl. `load_extension`/`readfile`/`sqlite_master`), row cap 500, thread-pool timeout 8/30 s |
| Tenant layer | `backend/app/tenancy.py` | `TenantContext` from `X-Workspace-Id` (dev) — fail-closed: no workspace ⇒ refusal, never global access |
| Storage boundary | `backend/app/core/storage.py` | ALL upload/export paths built only from validated components (doc-id 12-hex, ws-id regex, ext whitelist) |
| Data layer | `backend/app/db/` | app metadata (SQLite → PostgreSQL per ADR-003) + per-workspace analytics files |
| Observability | `backend/app/observability/adapter.py` | only module importing langfuse; `SafeObservation` wrapper — instrumentation can never break a request; redaction by default (sha256+length) |
| Evaluation | `backend/scripts/` | golden sets (SQL v1/v2, RAG 24-case chunk-level, routing), Wilson-CI compare, regression gate, error taxonomy |

## Request flow (Atlas, simple question)

1. `require_tenant` resolves workspace (unknown ⇒ 403; prod without header ⇒ 403).
2. Router picks agent (LLM + heuristic fallback); source router picks datasource — **ownership-checked**: a foreign `datasource_id` in the request body yields an honest error with zero rows, the agent never runs.
3. Planner LLM → JSON plan (SQL, tables, chart intent) → SQL Guard → execution in the workspace's own sqlite/postgres/clickhouse engine.
4. Execution error → LLM repair (≤2). Guard/timeout errors → honest refusal, never retried.
5. Deterministic Python analytics compute all numbers; LLM only writes prose.
6. SSE streams steps (`planner → database_query → answer`) with per-step durations; `done` carries the full contract.

## Multi-tenant model (P0.2–P0.4, P1.1)

| Layer | Mechanism | Verified by |
|---|---|---|
| API | `require_tenant` on every tenant route | 403 tests |
| Metadata | `workspace_id` on all tenant tables; ownership-checked reads | cross-tenant 404 tests |
| Uploads | `uploads/<ws>/` + validated path components | traversal + foreign-file tests |
| Analytics SQL | **physical**: `csv_db/<ws>.db` per workspace — a foreign table name cannot even resolve (`no such table`) | permanent regression test (fails pre-fix, verified via git worktree) |
| Exports | `exports/<ws>/`, name-as-id | cross-tenant regression |
| Lifecycle | `delete_workspace` CLI: files → metadata in one transaction; idempotent | partial-failure resume tests |

Isolation is currently **application-level + per-file physical**; PostgreSQL
RLS (ADR-003) is the accepted end-state.

## Failure boundaries

- LLM provider down/429 → transport retries (SDK, bounded, Retry-After), then honest `status=error` with empty rows — no fabricated data, ever.
- Langfuse down → silent no-op (`SafeObservation`); user path unaffected.
- Foreign resource id → 404/403 before any filesystem or DB access.
- Runaway SQL → guard timeout (8 s local / 30 s remote) on a worker thread; event loop never blocks.
- Complex-question loop exhaustion (6 steps) → explicit refusal.

## Known limits (honest)

Authentication is not implemented yet (ADR-006, P1a — tenant context is
header-based dev mechanism); datasource credentials are stored plaintext
until P0 encryption; app metadata still on SQLite until the PostgreSQL
migration; GPU/self-hosted inference never measured (blocked — see
docs/INFERENCE_BENCHMARK_GPU.md).
