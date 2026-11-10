# ADR-008: Workspace-scoped exports + delete_workspace (P1.1)

## EN summary

Exports share the upload storage boundary: `exports/<ws>/<slug>_<uuid8>.xlsx`,
strict filename regex, name-as-id (a foreign file cannot resolve — 404 before
any filesystem access). A metadata registry was rejected as redundant.
Legacy flat exports carry no owner info → readable only by the default dev
workspace; scoped wins on collision. Not-found attempts emit a Langfuse span
(workspace + filename) for detection. `delete_workspace` is a CLI (dry-run
default), files-then-metadata-in-one-transaction, idempotent, refuses the
default workspace. A public DELETE endpoint waits for authn (P1a).


Статус: Accepted (Stage 7 / P1.1, 2026-11)

## Context

Аудит с воспроизведением (как P0.3/P0.4): `GET /api/exports/{filename}` не
имел tenant-зависимости вовсе — чужой workspace (и клиент без заголовка)
получал 200 и содержимое файла (секрет внутри `sheet1.xml`, подтверждено).
`..`-фильтр был инлайн-проверкой на уровне роута, не storage-границей.
Создание: `export_rows` писал плоско в `data/exports/`, без workspace.
`delete_workspace` не существовал: файлы ws (uploads, csv_db, exports)
переживали удаление метаданных.

## Decision

1. **Exports — через ТОТ ЖЕ storage-слой** (`app/core/storage.py`), без
   дублирования: `write_export(ws, slug)` → `exports/<ws>/<slug>_<uuid8>.xlsx`;
   `resolve_export_path(ws, filename)` со строгим regex
   `^[A-Za-z0-9_\-]{1,60}\.xlsx$` (нарушение → 400 на storage-границе, путь
   не строится); resolution только внутри директории ws.
2. **Name-as-id, без метаданных-реестра**: файл живёт в директории ws —
   чужое имя физически не разрешается (404 до открытия файла). Реестр с
   ownership-строками отвергнут: дополнительная сущность и рассинхрон ради
   того, что даёт сама файловая раскладка.
3. **Legacy-плоские exports**: информации о владельце не существует нигде →
   читаемы ТОЛЬКО `ws_default` (до-P0.x одиночный тенант де-факто = default);
   при одинаковом имени scoped-копия выигрывает. Fallback для не-default
   отсутствует.
4. **Роут**: `require_tenant` + storage-resolution; not-found фиксируется
   Langfuse-спаном `export.not_found` (workspace_id, filename, reason) —
   детекция попыток чужих имён.
5. **delete_workspace** — CLI (`scripts/delete_workspace.py --ws X`,
   dry-run default, `--apply`), НЕ публичный API: файлы (uploads/<ws>/,
   csv_db/<ws>.db, exports/<ws>/) → затем метаданные одной транзакцией в
   порядке зависимостей (chunks → documents → datasources → scenarios →
   feedback → workspaces). Идемпотентен; ws_default защищён от удаления.

## Alternatives

- Таблица exports с ownership — отвергнуто (см. п.2).
- Список-эндпоинт exports с проверками — не нужен: имена unguessable
  (uuid4[:8]), а ownership решается раскладкой.
- Публичный DELETE /workspaces/{id} — отложено до authn (P1a): без
  аутентификации endpoint удаления был бы самой опасной поверхностью.

## Consequences

- `excel_url` в ответах чата остаётся прежним по формату (SSE-контракт не
  изменён); скачивание теперь требует заголовка workspace того же ws, в
  котором работал агент.
- Библиотечные вызовы `export_rows` без ws пишут в ws_default (паттерн
  агентов из P0.2) — прямые вызовы (тесты) не ломаются.
- Concurrent export в один ws: имена уникальны по uuid8 — покрыто тестом.

## Risks / triggers

- Если появится фича «поделиться export'ом между workspace» — пересмотреть
  п.2 (появится легитимный cross-ws доступ → нужен реестр или подписанные
  ссылки). Триггер: продуктовое требование shared exports.
- Legacy-файлы ws_default остаются плоскими навсегда (миграция не нужна:
  разрешение уже корректно).
- P1-хвост: envelope-шифрование credentials, audit log, PostgreSQL+RLS
  (ADR-003), OIDC (ADR-006).
