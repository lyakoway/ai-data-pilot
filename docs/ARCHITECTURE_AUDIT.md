# ARCHITECTURE AUDIT — Stage 6 (2026-11)

Аудит выполнен по коду на коммите `febe843` (post-stage-5); каждый факт
ссылается на файл/строку. Оценки Latency — из измеренных артефактов
`backend/bench_results/` (этапы 1–4).

## 1. Текущее состояние

### Orchestration (Atlas)
- **Линейный путь** для «простых» вопросов: `run_atlas_streaming` →
  planner → SQL (+≤2 repair rounds) → analytics → chart → answer
  (`app/agents/atlas.py`).
- **ReAct-цикл** для «сложных»: regex-классификатор `_COMPLEX_RE` решает,
  простой или сложный вопрос; `_run_loop` — до 6 итераций
  (`MAX_LOOP_STEPS=6`, `tools.py:28`), одна JSON-декларация инструмента на
  ход, parse-retry ×1 (этап 4).
- Состояние — локальные переменные корутины; **нет персистентности**:
  обрыв соединения = потеря работы, повторить нельзя.
- Измеренное: E2E simple 16.5–33.5 s (p50); complex на free-модели
  исчерпывает бюджет шагов (не `finish`) — проблема бюджета, не оркестрации.

### Orchestration (Doc)
Линейный: retrieval (top-4) → generation; контракты grounding/abstention/
citations закреплены тестами (этап 4). Цикла нет — LangGraph здесь нечего
заменять.

### Retries/ошибки (слоями, измерено тестами)
1. транспорт: SDK-ретраи (3, с Retry-After, timeout 300/10 s) — этап 2;
2. parse-retry обрезанного JSON ×1 — этап 4;
3. SQL self-correction ≤2 раундов (guard-ошибки не ретраятся by design);
4. статусы `ok/demo/partial/error` + честные сообщения.

### SSE
Очередь событий `step` (каждый шаг дважды: running→done/error) + `done`
(`app/api/routes/chat.py`). Контракт запинен тестами (этап 5). Стриминг
шагов, не токенов.

### Observability
Langfuse-адаптер (`app/observability/adapter.py`): корневой trace,
спаны planner/sql/answer/loop/retrieval, redaction по умолчанию, no-op при
отключении. Не блокирует путь (SDK-батчи); shutdown-flush в lifespan.

### Evaluation-контур
Golden sets v1/v2 + `eval_sql_full.py` **импортирует внутренности Atlas**
(`_generate_plan`, `_repair_plan`, `MAX_SQL_REPAIR_ROUNDS` —
`scripts/eval_sql_full.py:41`) — жёсткая связка: любой рефакторинг агентов
требует сохранения этих швов. Regression-гейт на Wilson-CI; 249 тестов.

## 2. Подтверждённые ограничения для SaaS (главное)

| # | Факт | Где | SaaS-риск |
|---|---|---|---|
| G1 | ~~**CORS `allow_origins + ["*"]`**~~ **ЗАКРЫТО (Stage 7, P0.1)** | `app/main.py` | wildcard убран; явный allowlist; production-валидация fail-fast; 16 тестов (`tests/test_cors.py`) |
| G2 | **app.db = SQLite** (файл) | `app/config.py:32` | нет RLS, один writer, нет конкурентных тенантов |
| G3 | **Пароли datasource в открытом виде**, интерполируются в DSN | `app/db/datasources.py:529,688,712` | утечка БД = утечка credentials клиентов |
| G4 | **RAG-корпус глобальный**: `get_chunks_for_search` берёт ВСЕ чанки без фильтра владельца | `app/db/app_db.py:475-481` | прямой cross-tenant leak документов |
| G5 | **Аутентификации нет** — единое пространство; datasource id доступен любому | вся API-слой | нет изоляции по построению |
| G6 | **Rate limiting нет** (наш 429-опыт: free-эндпоинт падает при конкурентности 5) | grep — middleware отсутствует | один тенант исчерпывает общий лимит провайдера |
| G7 | **История запросов не сохраняется** (нет chat_history) | `app/db/app_db.py` | продуктовая фича SaaS отсутствует |
| G8 | Квоты только глобальные (upload 25 MB/50k строк) | `documents.py:18` | нет per-tenant лимитов |
| G9 | Upload-пайплайн (parse→chunk→embed) синхронный в запросе | `documents.py` | длинные задачи блокируют воркер |

## 3. Что уже хорошо (не трогать)

SQL Guard (усилен этапом 3, 13 векторов пин-тестами); layered retries;
SSE-контракт; observability-слой; evaluation-контур с manifest и гейтом;
детерминированная аналитика в Python; изоляция тестов (249, без внешних
вызовов).
