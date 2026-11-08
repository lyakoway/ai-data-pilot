# ADR-005: Background jobs и workflow persistence

Статус: Accepted (Stage 6, 2026-11)

## Context

Сегодня длинные операции выполняются в HTTP-запросе: upload-документа —
parse→chunk→embed синхронно (G9); агентный цикл до 75 s держит воркер.
Для SaaS нужны: статус/прогресс, отмена, retry, промежуточный результат,
восстановление после сбоя воркера. LangGraph-решение отложено (ADR-002) —
durable-execution нужен уже на P1, реализуем собственно.

## Decision

**Двухуровневая модель: jobs (очередь) + steps (персистентный прогресс).**

1. Таблица `jobs(id, workspace_id, type, payload, status, priority,
   idempotency_key, created_by, heartbeat_at, result, error)` в Postgres.
   Статусы: `queued → running → succeeded | failed | cancelled`.
   Воркер — отдельный процесс (тот же кодбейз, `python -m app.jobs.worker`),
   `SELECT … FOR UPDATE SKIP LOCKED` — очередь без нового инфраструктурного
   компонента.
2. `job_steps(job_id, step_no, name, status, detail_json)` — прогресс
   шагов: ровно те же имена, что SSE-шаги агентов (`planner`,
   `database_query`, …) — переиспользуем существующую семантику.
3. **Отмена:** флаг `cancel_requested` — воркер проверяет между шагами
   (у нас шаги короткие, кроме LLM-вызовов; LLM-вызов прерывается по
   cancel-таску asyncio).
4. **Retry:** повторный запуск job'а = НОВЫЙ job со ссылкой на
   `retries_of`; шаги со статусом `succeeded` при retry пропускаются
   (idempotent steps: embed-чанки уже записаны → пропускаем по
   `detail_json.last_chunk_id`). Идемпотентность приёма — `idempotency_key`.
5. **Восстановление:** heartbeat + reclaim зависших (`running` без
   heartbeat > TTL → `queued`, шаги `running` → `failed_step`,
   перезапуск с последнего succeeded-шага).
6. **Прогресс клиенту:** SSE `GET /api/v1/jobs/{id}/events` (тот же
   контракт событий, что чат) или поллинг статуса.

Первые job-типы (P1): `document_ingest` (parse→chunk→embed), 
`scenario_batch_run`, `xlsx_export`.

## Alternatives

- **Celery/RQ + Redis:** зрелые, но добавляют инфраструктуру (Redis) до
  реальной необходимости; SKIP LOCKED-очередь на Postgres покрывает
  ожидаемую нагрузку (десятки джобов/мин).
- **LangGraph + PostgresSaver сейчас:** см. ADR-002 — отложено за
  критериями входа; наш step-журнал фактически даёт тот же resume-грануляр,
  которого требует продукт сегодня.

## Consequences

- Джобы workspace-scoped с самого начала (`workspace_id` NOT NULL + RLS) —
  tenant-контекст восстанавливается из job-строки, не из воркера.
- Воркер использует те же observability-спаны (корневой trace типа job).
- Лимиты (ADR-лимиты): `max_running_jobs_per_workspace` — проверка при
  постановке.

## Risks

- Долгий LLM-шаг без heartbeat-потока — mitigation: heartbeat обновляется
  отдельной задачей-таймером, не в шаге.
- Дублирование шагов при retry — правило «шаг идемпотентен или
  перезапускаем с чистого состояния» фиксируется в типе джоба.

## Migration strategy

P1: схема+воркер+`document_ingest` (вынос из HTTP), API статуса; P2:
scenario_batch; LangGraph-PoC — только на фоне ADR-002-критериев.
