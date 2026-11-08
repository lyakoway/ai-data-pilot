# ADR-004: Tenant data isolation

Статус: Accepted (Stage 6, 2026-11)

## Context

Подтверждённые векторы утечки сегодня (ARCHITECTURE_AUDIT): G3 — пароли
datasource в открытом виде в app.db и в DSN-строках; G4 — RAG-корпус
глобальный (`get_chunks_for_search` без фильтра владельца); G5 — доступ к
любому datasource_id без проверки принадлежности; G8 — общие квоты.

## Decision

**App-данные (Postgres, ex-SQLite):** RLS по `workspace_id`
(ADR-003); доступ к datasource — только через ownership-check
`datasources.workspace_id = ctx.workspace` ДО построения engine
(сегодня `get_engine_for(source_id)` кеширует engine по id без
принадлежности — добавить слой `resolve_workspace_source(ws, id)`).

**Credentials источников:** envelope-encryption — AES-GCM (libsodium
`crypto_seal`) с master-ключом из KMS/env (`APP_MASTER_KEY`, rotation
версиями `dek_version`); в БД только шифротекст; DSN собирается в момент
подключения; в логи/Langfuse — никогда (redaction уже штыкует хеши, сюда
добавляем запрет полей `password`).

**Документы/RAG:** `documents`/`chunks` получают `workspace_id`;
`get_chunks_for_search(ws)` — фильтр в SQL (сегодня G4 — прямой leak);
файловое хранилище — префикс `uploads/{workspace_id}/…`; имя файла в
поиске — тоже workspace-scoped. Embedding'и считаются на лету (fastembed),
отдельный векторный стор не нужен → изоляция бесплатно через SQL.

**История запросов (новое):** `chat_messages(workspace_id, user_id,
agent, status, sql_sha, duration_ms, created_at)` — RLS + отдельный
retention per org.

**Cross-tenant защита — эшелоны:**
1. RLS (инвариант БД, fail-closed);
2. ownership-check в сервисном слое (сообщения об ошибках не раскрывают
   существование чужих id — «not found», не «forbidden»);
3. SQL Guard без изменений (запрещённые функции — этап 3) + NEW:
   `sanitize_sql` остаётся, но исполнение идёт только от engine,
   привязанного к workspace-принадлежащему источнику;
4. audit log (append-only): `who/workspace/action/target/rows/status/ts`.

## Alternatives

- Шифрование диска вместо envelope — защищает от кражи диска, не от SQLi/
  дампа БД: недостаточно.
- DB-per-tenant для источников клиентов — см. ADR-003 (Enterprise-only).

## Consequences

- Ротация мастер-ключа = перешифровка DEK (процедура, batch-job P1).
- Production-инсталляция требует управления `APP_MASTER_KEY` (KMS/secret
  manager; в .env — только для локальной разработки).
- Тест изоляции становится обязательным контрактом (P0): тенант A
  запрашивает id тенанта B → 404, chunks/документы невидимы, история
  фильтруется.

## Risks

- Забытыйownership-check в новом коде → mitigation: линтер-правило
  «engine строится только из `resolve_workspace_source`» (code review +
  тест на API-уровне: попытка чужого datasource_id).
- Backup-дампы содержат шифротексты credentials — ок; ротация после
  инцидента обязательна.

## Migration strategy

P0: добавить `workspace_id` + RLS + envelope-encryption credentials +
ownership-check; P1: аудит-лог полный, retention;см. SAAS_ROADMAP.
