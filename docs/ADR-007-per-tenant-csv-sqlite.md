# ADR-007: Per-tenant sqlite для аналитических CSV/Excel таблиц (P0.4)

## EN summary

Uploaded CSV/Excel analytics moved from one shared sqlite to
`csv_db/<workspace>.db` per workspace — physical SQL isolation: generated SQL
naming a foreign table cannot resolve it (`no such table`). Audit confirmed
the vulnerability by execution first; SQL Guard (keyword-only) was NOT
weakened or changed — an allowlist was rejected as the primary fix
(fragile SQL parsing, no tenant context) and stays a defense-in-depth P1
item. Migration script: dry-run default, collision→report+skip (per-ws file
wins), orphans untouched, copy→verify→drop atomicity, re-run finishes
interrupted work. Concurrency re-verified at N=64 real threads: 0 errors —
no queueing needed (new revisit trigger documented).


Статус: Accepted (Stage 7 / P0.4, 2026-11)

## Context

Аудит P0.4 подтвердил уязвимость **выполнением**: все workspace использовали
один общий `data/csv_sources.db`; `get_engine_for` отдавал ownership-checked
метаданные, но engine был общим, а SQL Guard таблицы не проверяет. Итог:
`run_sql(engine_B, 'SELECT … FROM "<table_A>"')` возвращал данные чужого
workspace (галлюцинация имени таблицы или prompt injection через имя
загружаемого файла). Существовавший тест ловил только metadata-уровень.

## Decision

**Вариант (b): per-tenant sqlite file — `data/csv_db/<workspace_id>.db`.**

- `_uploaded_db_path(workspace_id)` — строго workspace-aware, **без
  нуль-аргументного fallback**: отсутствие workspace → TenantContextError,
  общий файл не существует как путь разрешения.
- `_uploaded_engine(workspace_id)` — потокобезопасный кеш (Lock) per-ws.
- `ingest_rows` создаёт таблицы в файле workspace; `get_engine_for` /
  `_build_all_uploads_catalog` / `delete_source` пробрасывают ws.
- Миграция: `scripts/migrate_csv_sources.py` (dry-run default; коллизии —
  репорт+пропуск, per-ws файл всегда выигрывает при чтении; сироты — репорт,
  не трогаем; атомарность: copy → verify rowcount → drop; повторный запуск
  добивает прерванную миграцию).

## Alternatives

- **(a) per-tenant schema/namespace в sqlite — отвергнуто**: SQLite не имеет
  schemas; ATTACH-подход потребовал бы переписывать каждый запрос и открыл бы
  ATTACH-атаку поверхность (Guard запрещает ATTACH, но архитектура бы его
  легитимизировала).
- **(c) PostgreSQL + RLS — отвергнуто для P0.4**: правильная конечная цель
  (ADR-003), но это P1-миграция всего app.db; нарушает требование
  «минимальное изменение» текущего этапа.

## Почему не allowlist таблиц в Guard как замена

Allowlist в Guard (`sanitize_sql`/`run_sql`) — defense-in-depth кандидат, но:
(1) извлечение имён таблиц из произвольного SQL хрупко (подзапросы, CTE,
юникод-кавычки, комментарии) — обход за минуты у мотивированного атакующего;
(2) Guard сейчас не знает, КАКОМУ workspace принадлежит engine — пришлось бы
протаскивать контекст в чистую функцию; (3) главное — физическая изоляция
решает класс проблемы целиком (в т.ч. будущие kind'ы источников), а не
конкретный вектор. Allowlist остаётся в P1-списке как второй слой.

## Consequences

- Кросс-workspace SQL невозможен физически: `no such table` —
  regression-тест `test_cross_tenant_sql_no_such_table` закреплён навсегда.
- Фича сохранена: два источника одного ws JOIN'ятся через `all_uploads`
  (один файл) — тест.
- Пустой файл `csv_db/<ws>.db` (~12 KB) остаётся после удаления последнего
  источника — принято; удаление workspace как фичи нет (P1).
- Evaluation не тронут: фикстуры ездят на `ws_default` = один файл;
  conftest-патч `_uploaded_db_path` сохраняет fail-closed семантику.

## Risks

- Сироты в общем файле (нет/битый workspace в metadata) — решение за
  оператором; скрипт их не трогает.
- Много файлов = больше fd/пулов при сотнях workspace — до этого момента
  переход на PostgreSQL (ADR-003) успеет случиться.

## P0.5 verification addendum (2026-11)

1. **Конкурентный ingest в один ws** — стресс-тест 8 потоков: без ошибок и
   порчи (SQLite сериализует записи, пул per-ws). Пин-тест
   `test_concurrent_ingest_same_workspace_no_corruption`.
   **P1.2-реверификация (2026-11)**: триггер «десятки одновременных загрузок»
   НЕ сработал, граница N=8 НЕ подтвердилась — прогон N=16/32/64 реальных
   потоков: 0 ошибок, 0 corruption, все таблицы с точными данными, 0.2–0.5 s.
   Вывод: hardening конкурентности **не требуется**, ограничение/очередь не
   вводятся. Новый триггер пересмотра: воспроизводимые `database is locked`
   в проде или multi-ws UI с массовыми загрузками.
2. **Прерванная миграция** — повторный `--apply` добивает drop legacy-копии
   при совпадении rowcount (`FINISHED`); при расхождении — `MISMATCH`-репорт,
   общий файл сохраняется (данные не теряются молча). Оба пути покрыты тестами.
3. **Тавтологичность regression-теста** — проверено: `git worktree` на
   pre-P0.4 коммите `e9d62a5` + тот же тест → FAILED (DID NOT RAISE
   SqlExecutionError) — тест падает без фикса, тавтологии нет.
4. **Cleanup пустых per-ws файлов** — реализован (не отложен): dispose кеша →
   проверка sqlite_master → unlink при нуле таблиц; цена низкая, мусор не
   накапливается. Пин-тест.
5. Исполняемых упоминаний legacy-пути в `app/` — 0: docstring переписан без
   имени файла («single supported layout»), неиспользуемая константа удалена.

## Migration strategy

`migrate_csv_sources.py --apply` на инстансах с legacy-данными; новые
установки миграции не требуют. Повторный запуск безопасен (идемпотентен);
drop из общего файла только после сверки rowcount.
