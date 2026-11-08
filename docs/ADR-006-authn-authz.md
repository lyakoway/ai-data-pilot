# ADR-006: Authentication и Authorization

Статус: Accepted (Stage 6, 2026-11)

## Context

Сегодня аутентификации нет вовсе (G1/G5): CORS с `*`, любые datasource_id
доступны всем. Продукт требует: пользователи, организации, роли, сессии,
доступ к подключённым источникам per-workspace.

## Decision

**AuthN — внешний OIDC-провайдер (Auth0 / Keycloak / Cognito), JWT.**

- Логин/пароли/2FA/MFA — на стороне IdP; наше приложение — RS256-проверка
  access-token (`jwks`-кэш) в FastAPI-зависимости; refresh — у IdP.
  Локальных паролей не храним.
- Токен несёт `sub` (user_id), `org` claim'ы; маппинг user→org/workspaces —
  в нашей БД (`org_members`, `workspace_members`), NOT в токене (иначе
  отзыв роли ждёт истечения токена).
- **API-ключи** для программного доступа: per-workspace, хранятся ТОЛЬКО
  хешированными (argon2), префикс для индексификации `adp_live_…`.

**AuthZ — RBAC, роли per-workspace + backend-enforced:**

| Роль | Права |
|---|---|
| `org_owner` | биллинг-профиль (P3), создание/удаление workspace, приглашение admin |
| `admin` (workspace) | источники, документы, роли, лимиты-просмотр |
| `analyst` | запросы Atlas/Doc, сценарии, экспорт, история (свою + workspace) |
| `viewer` | только чтение истории/дашбордов |

Реализация — цепочка зависимостей FastAPI:
`decode_jwt → load_membership → require_workspace(role)`; контекст
`(user_id, org_id, workspace_id, role)` передаётся ЯВНО в сервисы; RLS
(ADR-003) — второй эшелон: даже баг авторизации не даёт чужие данные.
Доступ к подключённым источникам — только через ownership-check (ADR-004).

**Сессии:** короткоживущие access (≤15 min) + refresh у IdP; logout —
revocation у IdP; для API-ключей — own revocation list.

## Alternatives

- **Собственные пароли/сессии:** crypto-поверхность (хеши, сбросы, 2FA) без
  необходимости — отвергнуто.
- **ABAC/полная policy-engine (OPA):** избыточно для 4 ролей; RBAC
  покрывает; OPA — опция при появлении per-row политик.
- **mTLS/сервисные токены для UI:** не пользовательский кейс.

## Consequences

- `+python-jose`/`pyjwt` + `jwks` (или зависимость IdP-SDK); middleware
  для CORS-фикса (убрать `+["*"]` — G1) — P0.
- Все существующие API-роуты получают зависимость `require_workspace` —
  ломающее изменение для локального demo; решение: env-режим
  `AUTH_MODE=oidc|dev` (dev — synthetic single-workspace контекст с
  WARNING в логе и запрещён в production-build).
- История запросов и аудит привязаны к `user_id` (G7 закрывается).

## Risks

- JWKS-endpoint недоступен → fail-closed (401), не open;
- dev-режим случайно в prod — проверка при старте: `AUTH_MODE=dev` +
  не-локальный host → отказ старта.

## Migration strategy

P0: CORS-фикс + скелет зависимостей + `AUTH_MODE=dev`-контекст;
P1: OIDC-интеграция, orgs/workspaces API, приглашения, API-ключи;
существующие тесты гоняются в dev-режиме (контракт не меняется).
