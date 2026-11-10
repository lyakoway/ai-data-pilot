# Demo scenario (~7 minutes, offline)

No API keys, no LLM calls — everything below runs on the deterministic demo
provider. Expected output is shown for each step.

```bash
./dev.sh          # or: cd backend && uvicorn app.main:app --port 8001
# UI: http://localhost:5173   API: http://localhost:8001/docs
```

## Part 1 — Atlas: Text-to-SQL with a visible execution trace (2 min)

1. Model = **Demo (offline)**, agent = Atlas. Ask: `Сколько всего поездок в базе?`
   - Expect: instant answer, live steps `Анализирую запрос → Получаю данные из БД →
     Формирую ответ`, status badge **demo** (honest — no LLM behind it).
2. Ask: `Топ-5 городов по выручке` → table + bar chart appear; Excel export link.
3. Ask something self-referential about a wrong table:
   `Средняя зарплата сотрудников по отделам`
   - Expect: honest refusal (`Модель не вернула SQL…`) — the agent does not
     invent data outside its schema.

## Part 2 — Doc: grounded RAG with citations (1.5 min)

1. Switch agent to Doc. Ask: `Какой TTL у кэша тарифов?`
   - Expect: answer citing `[1]` mapped to *Redis pricing cache*; sources panel.
2. Ask an adversarial close pair: `Какой TTL у антифрода серийников?`
   - Expect: the OTHER chunk (whitelist TTL ≠ 15-min pricing cache) — hybrid
     retrieval discriminates near-duplicates.
3. Ask: `Какой SLA поддержки у тарифа premium?`
   - Expect: abstention — "в доступной документации ответа нет" (not a
     hallucinated SLA).

## Part 3 — Tenant isolation (the P0 story, 2.5 min)

```bash
# Two workspaces, one secret each
curl -s -X POST localhost:8001/api/datasources/upload \
  -H "X-Workspace-Id: ws_a" -F "file=@a.csv"      # a.csv contains ALPHA_SECRET
curl -s -X POST localhost:8001/api/datasources/upload \
  -H "X-Workspace-Id: ws_b" -F "file=@b.csv"

# 1) metadata level: B cannot even see A's source
curl -s localhost:8001/api/datasources -H "X-Workspace-Id: ws_b"   # → no a.csv

# 2) document/RAG level: B's search does not find A's document text

# 3) SQL level (physical): even SQL *naming* A's table cannot read it
#    (per-tenant sqlite files — permanent regression test in the repo):
curl -s -X POST localhost:8001/api/chat -H "X-Workspace-Id: ws_b" \
  -H "Content-Type: application/json" \
  -d '{"message":"что в таблице <имя_таблицы_A>?","agent":"atlas","model":"mock"}'
# → honest error, rows: []

# 4) unknown workspace → fail-closed 403
curl -s localhost:8001/api/datasources -H "X-Workspace-Id: ws_hacker"  # → 403
```

Point the reviewer at `tests/test_tenancy.py` + `tests/test_storage.py` +
`tests/test_exports.py`: 30+ isolation tests, including the cross-tenant SQL
regression that **fails without the fix** (verified on the pre-fix commit).

## Part 4 — Evaluation loop as the differentiator (1 min)

```bash
cd backend
pytest -q                                   # 317 passed, no API keys needed
python scripts/eval_rag.py                  # chunk-level Recall/nDCG, deterministic
python scripts/compare_sql_evals.py --merge # 5 models, Wilson 95% CI
python scripts/error_analysis.py            # failure taxonomy from artifacts
```

Narrate: every README number traces to a `bench_results/*.json` artifact with
a manifest (git rev, model version, dataset hash) — nothing is hand-waved.

## Not shown (say it out loud)

GPU/self-hosted inference: never measured — blocked, documented with a
ready-to-run plan (docs/INFERENCE_BENCHMARK_GPU.md). Authentication: P1,
header-based dev mechanism today. Production users: none for this codebase.
