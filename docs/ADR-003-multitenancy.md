# ADR-003: SaaS multi-tenancy model

## EN summary

Tenant→Organization→Workspace hierarchy; shared schema + PostgreSQL
Row-Level Security as the target model (fail-closed default-deny policies,
workspace id set per transaction). Schema-per-tenant and "WHERE-clause only"
rejected (migration/pool explosion; one forgotten clause = leak). SQLite
application-level filtering is the interim step (P0.2), PG migration is the
accepted end state.


Статус: Accepted (Stage 6, 2026-11)

## Context

Продукт: организации подключают источники данных, работают с Atlas/Doc,
загружают документы, смотрят историю; нужны per-org лимиты и доступы
(ARCHITECTURE_AUDIT G1–G9: сегодня — единое неразделённое пространство).

## Decision

**Иерархия Tenant → Organization → Workspace:**

- **Organization (tenant, billing-unit)** — владелец подписки, лимитов,
  пользователей; `org_id`.
- **Workspace** — единица изоляции ДАННЫХ и работы: свои datasource'ы,
  документы/RAG-индекс, сценарии, история чатов; `workspace_id`.
- **User** — член ≥1 org с ролью per-workspace.

Данные всех воркспейсов организации логически разделяются, но изоляция
доступа — на уровне workspace (организация по умолчанию НЕ видит чужие
воркспейсы; воркспейс может быть расширенным «общим» — флаг).

**Модель хранения — shared schema + PostgreSQL Row-Level Security:**
каждая строка tenant-данных получает `workspace_id`; RLS-политики
`USING (workspace_id = current_setting('app.workspace_id')::uuid)`;
приложение выставляет `SET LOCAL app.workspace_id` в транзакции из
контекста запроса. ClickHouse (аналитика клиентов) — per-tenant таблицы с
`tenant_id` + Row Policy, либо отдельная БД на тенанта для Enterprise
(операционно дороже — по требованию).

## Alternatives

- **Schema-per-tenant / DB-per-tenant (Postgres):** сильная изоляция, но
  N-кратные миграции, connection-pool взрыв, деградация поиска «по всем
  своим» — отвергнуто для базового тарифа.
- **Один общий空间 без RLS (только WHERE в коде):** одна забытая строка =
  утечка; отвергнуто — защита должна быть инвариантом БД, не дисциплиной.

## Consequences

- Требуется миграция app.db **SQLite → PostgreSQL** (G2; SQLite без RLS) —
  первый шаг P0; sqlite-режим остаётся для demo/self-host single-user
  (через тот же SQLAlchemy-слой, RLS-политики условны).
- Один индекс/пул соединений; аудит и метрики естественно группируются по
  `workspace_id`/`org_id`.
- RLS-инвариант проверяется тестом изоляции (см. roadmap P0, критерий).

## Risks

- Пропущенный `SET LOCAL` → RLS-политика по умолчанию deny-all (fail-closed)
  — риск обратный: «ничего не видно»; ловится smoke-тестом.
- Noise-neighbor'ы на общем ClickHouse — смягчение: per-tenant quotas
  (settings max_execution_time/max_result_rows).

## Migration strategy

1. Схема vNext: `+workspace_id` во все tenant-таблицы, FK на workspaces;
2. SQLAlchemy-событие `set_workspace_ctx` на соединение;
3. RLS-политики + роль app без BYPASSRLS;
4. Тест «тенант A не видит Б» (pytest, P0-критерий приёмки);
5. Данные demo-инстанса → один seed-workspace.
