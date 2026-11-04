# Observability — Langfuse instrumentation

Langfuse добавлен как **отдельный слой** LLM-observability поверх существующей
архитектуры: агенты, SSE-контракт и evaluation-контур не изменены. Проект
**не использует Langfuse в production** — это включаемая возможность.

## Зачем

Evaluation-контур отвечает на «какого качества система в среднем» (golden
sets, A/B, regression-гейт). Observability отвечает на «что произошло в этом
конкретном запросе»: полный путь вопроса → роутинг → SQL-план → guard →
данные → ответ, с латентностями каждого шага и метаданными ошибок.

## Архитектура

```
app/observability/adapter.py   ← единственный модуль, знающий про langfuse
   obs_trace(...)   — корневой trace запроса (sampling, drop-флаг)
   obs_span(...)    — вложенный span/generation/retriever
   redact(...)      — политика редукции (hash+длина по умолчанию)
   SafeObservation  — обёртка: НИ один вызов instrumentation не может упасть
   observability_shutdown() — bounded flush при остановке приложения
```

- Бизнес-код импортирует только хелперы адаптера — прямой импорт `langfuse`
  запрещён контрактом (есть тест).
- Вложенность спанов — через OTel-contextvars SDK (async-safe); никакого
  собственного глобального состояния, кроме ленивого singleton-клиента SDK.
- SDK шлёт трейсы батчами в фоне — основной путь не блокируется.
- Отключён / не настроен / упал / raises в любом методе → no-op, запрос
  пользователя не страдает (пин-тесты). Исключения ТЕЛА with-блока
  проходят насквозь (SqlExecutionError доезжает до repair-логики).
- Sampling: `LANGFUSE_SAMPLE_RATE < 1` — сброшенный трейс глушит и все
  вложенные спаны (contextvar drop-флаг).

## Что записывается / чего нет

Записывается: имя span'а, длительность, статус, модель, provider,
hash+длина промпта, hash/обрезанный SQL, row_count, guard-исход
(ok/guard/timeout/execution), chunk_ids и scores retrieval, cited-индексы
и валидность цитат, agent/model/status/duration/iterations корневого trace.

НЕ записывается никогда: API-ключи, пароли источников, полные сырые
промпты/ответы/SQL при дефолтной настройке. Сырой текст (обрезка 2k) —
только при `LANGFUSE_CAPTURE_CONTENT=true`, отдельным флагом для отладки.

## Включение

```bash
# Cloud: ключи в https://cloud.langfuse.com → Project → API Keys
cp backend/.env.example backend/.env   # и заполнить:
#   LANGFUSE_PUBLIC_KEY=pk-lf-...
#   LANGFUSE_SECRET_KEY=sk-lf-...
cd backend && uvicorn app.main:app     # трейсы появятся в UI Langfuse

# Self-hosted (docker compose от Langfuse) — затем указать:
#   LANGFUSE_HOST=http://localhost:3000
```

Отключение: `LANGFUSE_ENABLED=false` или просто пустые ключи — приложение
работает идентично (тесты пинят).

## Структура trace

```
chat (trace: question-redacted, model, status, duration_ms, iterations)
├─ agent_router (span: decision)
├─ source_router (span: decision)
├─ atlas.planner (generation: model, prompt-sha, plan_ok)
│   └─ llm.complete (generation: provider, system-sha, user-msg-sha)
├─ atlas.sql_execute (span: attempt, sql-sha, guard, row_count)
├─ [atlas.sql_repair (generation: db_error, repair_ok) → sql_execute …]
├─ atlas.answer (generation: answer-redacted)
└─ Doc: doc.retrieval (retriever: chunk_ids, scores, top_k)
        doc.generation (generation: cited_indices, citations_valid)
ReAct-цикл: atlas.loop.think (generation: iteration, tool) + atlas.tool.<name>
```

## Ограничения

- Токены/стоимость не пишутся (provider `complete()` не возвращает usage;
  стриминговый путь с usage не используется агентами).
- SSE отдаёт шаги, не токены — trace фиксирует шаги соответственно.
- Оценки/метки (Langfuse scores) не проставляются — это задача
  evaluation-контура, не observability.
- Тесты используют FakeLangfuse; реальная отправка в Langfuse проверялась
  только локальным smoke-запуском без ключей (no-op путь).
