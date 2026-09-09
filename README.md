---
title: AI Data Pilot
emoji: 📊
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

# 📊 AI Data Pilot — мультиагентная аналитическая платформа

> **Live demo:** [lyakoway-ai-data-pilot.hf.space](https://lyakoway-ai-data-pilot.hf.space/)

Аналитическая система на двух специализированных AI-агентах: **Олег** (Text-to-SQL — базы данных, SQL, графики, Excel) и **Ксюша** (RAG — документация и поиск по загруженным файлам). Авто-роутер сам понимает вопрос и направляет его нужному агенту и нужному источнику данных.

> **Engineering focus:** multi-agent orchestration · Text-to-SQL · hybrid RAG · deterministic analytics · SQL safety · self-correction · automated evaluation

AI Data Pilot — не «чат-бот с двумя LLM», а production-oriented AI-система, в которой поведение агентов, retrieval, генерация SQL, безопасность, корректность и латентность **измеряются по отдельности** — golden set, автотесты и бенчмарки латентности входят в репозиторий наравне с кодом.

[![Demo](https://img.shields.io/badge/demo-lyakoway--ai--data--pilot.hf.space-ff9d00)](https://lyakoway-ai-data-pilot.hf.space/)
![backend](https://img.shields.io/badge/backend-FastAPI-009688)
![frontend](https://img.shields.io/badge/frontend-React%2019%20%2B%20Vite-61dafb)
![tests](https://img.shields.io/badge/tests-174%20passed-brightgreen)
![sources](https://img.shields.io/badge/sources-PostgreSQL%20·%20ClickHouse%20·%20CSV%20·%20Excel-6366f1)

<sub>Демо на бесплатном тарифе может «засыпать» — первый заход после простоя поднимается ~1 мин.
Векторная модель (fastembed) загружается при первом поиске Ксюши (~10 сек).</sub>

## Key capabilities

- 🧭 **Авто-роутинг (двойной)** — по агенту (данные → Олег, документация → Ксюша) и по источнику данных (по смыслу вопроса выбирается нужная БД). Оба роутера двухуровневые: LLM-классификация + детерминированная эвристика как fallback — медленный или недоступный LLM не ломает маршрутизацию. Ручные переключатели остаются как override
- 👤 **Аналитик Олег**
  - **Agent Loop (ReAct)** — для сложных вопросов агент сам решает, какие tools вызвать: `database_query → calculate → analyze → create_chart → finish`. Prompt-based tool-calling работает со всеми провайдерами, включая offline Demo (scripted сценарий)
  - **Execution trace (SSE)** — пошаговая работа в реальном времени; каждый шаг раскрывается (SQL, row_count, инсайты)
  - **Self-correction** — упавший SQL возвращается агенту вместе с ошибкой БД; агент переписывает запрос сам (до 2 раундов), вместо молчаливой подмены данных
  - **Детерминированная аналитика** — тренды, топ-N, аномалии (z-score), проценты считает Python; LLM только интерпретирует и оформляет текст
- 👩‍💻 **Ксюша**
  - **Гибридный поиск** — BM25-IDF + векторные эмбеддинги (fastembed, мультиязычная модель, 50+ языков): находит по смыслу и на другом языке
  - **Загрузка документов** — PDF, Word, Excel, CSV, TXT, MD (drag&drop); Excel одновременно становится SQL-таблицей для Олега
  - **Inline-цитаты `[1]`** и просмотрщик документов: PDF на нужной странице, DOCX рендер, Excel как таблица
- 🗄️ **Источники данных** — PostgreSQL и ClickHouse (кнопки в UI или env, автосхема через интроспекцию, диалект-зависимые промпты); виртуальный источник **«Все загрузки»** — JOIN между загруженными файлами
- ⚡ **Параметризованные сценарии** — шаблоны с `{period}`, `{group_by}`; один сценарий — бесконечное переиспользование
- 👍 **Витрина фидбека** — аналитика оценок 👍/👎 по агентам с фильтрами
- 🤖 **13 конфигураций моделей** — OpenAI, Anthropic, Z.ai, Ollama + offline Demo; переключение на лету (реестр — в [Evaluation & Benchmarks](#evaluation--benchmarks))

## Engineering approach

Система сознательно разделяет вероятностные LLM-рассуждения и детерминированную логику приложения:

- **LLM** — понимание интента, роутинг, генерация SQL, выбор tools и формулировка ответа
- **Python** — численные расчёты, тренды, топ-N, детекция аномалий, валидация результатов
- **SQL Guard** — валидация сгенерированного SQL до исполнения
- **Self-correction** — упавший SQL возвращается агенту вместе с ошибкой БД и повторяется в рамках ограниченного числа попыток
- **Hybrid RAG** — BM25 покрывает точную терминологию, векторный поиск — семантическую близость
- **Evaluation** — генерация SQL, роутинг, retrieval и корректность аналитики оцениваются по отдельности
- **Observability** — каждый шаг агента стримится через SSE: SQL, row counts, промежуточные результаты

## Architecture

```
[React dashboard] ──/api──▶ [FastAPI]
                              ├─ Авто-роутер агента: данные → Олег, документация → Ксюша
                              ├─ Олег: schema → SQL → guard → analytics → chart/xlsx
                              │    ↑ Agent Loop (ReAct): multi-step tool-calling
                              │    ↑ self-correction (2 retry rounds)
                              │    ↑ deterministic insights (Python, not LLM math)
                              │    ↑ execution trace streamed via SSE (step events)
                              ├─ Авто-роутер источника: вопрос → нужная БД
                              ├─ DataSources: RideGo | PostgreSQL | ClickHouse
                              │              | CSV/Excel (SQL + RAG) | «Все загрузки» (JOIN)
                              └─ Ксюша: hybrid RAG (BM25-IDF + vector fastembed)
                                        over built-in docs + uploaded files

App DB (SQLite): scenarios · datasource metadata · feedback · documents · chunks
Analytics DB:    RideGo (seeded) · uploaded CSV/Excel tables
Files:           data/uploads/ (originals for the document viewer)
```

**Хранилище:** сценарии, метаданные источников, фидбек, документы и чанки — в `app.db` (SQLite). Аналитика — `ridego.db` (демо-домен RideGo: `dim_city`, `dim_user`, `fact_rides`, `fact_subscriptions`) и `csv_sources.db` (загруженные таблицы). CSV/Excel попадают в **оба pipeline**: SQL-таблица для Олега + текстовые чанки для Ксюши. Пароли источников хранятся server-side и никогда не возвращаются на фронтенд.

## Engineering decisions

### Детерминированная аналитика

LLM не считает бизнес-метрики. Численные операции и детекцию аномалий выполняет Python (`analytics.py`), LLM интерпретирует и объясняет результат. Цифры в ответе всегда приходят из БД или Python-расчётов — никогда из генерации.

### Ограниченное выполнение агента

ReAct-цикл имеет фиксированный лимит шагов (`MAX_LOOP_STEPS = 6`), self-correction SQL ограничена двумя раундами ремонта (`MAX_SQL_REPAIR_ROUNDS = 2`). Агент не может уйти в бесконечный цикл tools: худший случай — внятный отказ, а не зависший запрос.

### Гибридный поиск

Точная терминология и семантическая близость решают разные retrieval-задачи, поэтому BM25-IDF и векторный поиск комбинируются (взвешенно, 0.4 / 0.6) — без обучения ранжирующей модели. Бонус векторов — кросс-языковые запросы.

### Раздельные уровни оценки

Софт-тесты (pytest) проверяют корректность реализации; Golden Sets — поведение AI: роутинг, генерацию SQL, точность результатов, качество retrieval. Падение теста и падение метрики сигнализируют о разных классах проблем.

### Честная оценка

Result Accuracy публикуется отдельно от SQL Execution Accuracy, а не сводится к одному «общему скору» — ограничения метрик видны и описаны явно (см. [Known Limitations](#known-limitations)).

### Почему два агента

Анализ данных и поиск по документации — разные инструменты, ограничения и режимы отказов. Роутинг на специализированного агента удерживает каждый workflow ограниченным и делает качество измеримым (отдельный routing golden set).

### Разделение ответственности: LLM vs детерминированный код

| Ответственность | Реализация |
|---|---|
| Понимание интента | LLM |
| Роутинг агента | LLM-классификация + детерминированная эвристика (fallback) |
| Роутинг источника данных | LLM-классификация по схемам + эвристика (fallback) |
| Генерация SQL | LLM |
| Валидация SQL | Python (SQL Guard: read-only, один statement, forbidden keywords) |
| Лимиты исполнения | Python: timeout 8 с (30 с для внешнего PostgreSQL), максимум 500 строк |
| Исполнение SQL | SQLite / PostgreSQL / ClickHouse |
| Вычисления (тренды, топ-N, проценты) | Python |
| Детекция аномалий | Python (z-score) |
| Графики | Python готовит спецификацию и данные; рендеринг — React / Recharts |
| Поиск по документам | Python: BM25-IDF + векторные эмбеддинги (fastembed) |
| Финальный текст ответа | LLM |

Принцип: **LLM не используется там, где надёжнее работает обычный код.**

## Evaluation & Benchmarks

**174 автотеста** (pytest): Agent Loop, SQL guard, self-correction, качество retrieval (Recall@1/5 · MRR по режимам BM25 / Vector / Hybrid), числовые contract-тесты аналитики, роутинг, источники данных.

**Golden Set** — 50 SQL-сценариев (simple, агрегации, JOIN, ambiguous, cross-source) + 20 routing-кейсов. Harness: `backend/scripts/evaluate.py`, методология — [EVALUATION.md](backend/EVALUATION.md).

**Качество retrieval (Ксюша)** — детерминированный pytest на 8 golden-парах «вопрос → документ» (file-level grounding), воспроизводится без API-ключей (`pytest tests/test_retrieval_quality.py`): BM25, Vector и Hybrid — **Recall@1 = Recall@5 = MRR = 1.0**; тестами закреплено, что hybrid не хуже каждого из компонентов.

**Живой прогон GLM-4.6** (`python scripts/evaluate.py --suite all --model zai:glm-4.6`):

| Метрика | Значение |
|---|---|
| SQL Execution Accuracy | **100%** (50/50) |
| Agent Routing Accuracy | **100%** (20/20) |
| Result Accuracy | 42% — execution match (см. примечание ниже) |
| Task Completion Rate | 100% (0 repair rounds понадобилось) |
| Латентность p50 / p95 | ~22 с / 32 с |

> **Как читать 42% Result Accuracy.** Совпадение считается по нормализованным множествам строк (execution match): порядок и форматирование не важны, float округляется до 2 знаков — то есть это уже не строгий exact-match. Оставшиеся «промахи» в основном связаны с выбором колонок: модель может вернуть семантически тот же ответ с другим набором или именами колонок. Execution Accuracy 100% означает, что все 50 запросов выполнились и вернули корректную схему результата.

### Латентность по моделям (живой прогон)

| Модель | План (LLM) | Выполнение (БД) | Ответ (LLM) | Итого | SQL ok |
|---|---|---|---|---|---|
| GLM-5.2 (Z.ai) | 7.0 с | 6 мс | 9.4 с | ~16.4 с | 3/3 |
| GLM-4.6 (Z.ai) | 13.5 с | 12 мс | 16.3 с | ~29.8 с | 2/3 |
| GLM-5.3-flash (Z.ai) | 7.0 с | 8 мс | 4.8 с | ~11.9 с | 2/3 |
| GLM-5.3 (Z.ai) | 13.8 с | 11 мс | 5.1 с | ~19.0 с | 1/3 |

<sub>Медианы 3 прогонов одного вопроса через полный цикл (план → БД → ответ) — латентность внешнего API варьируется между запусками. GLM-5.3-поколение отвечает быстрее, но SQL генерирует слабее; GLM-5.2 — единственная без промахов. Скрипт: `python scripts/latency_benchmark.py --models ... --runs 3` (из `backend/`).</sub>

### Реестр моделей (13 конфигураций)

| Провайдер | Модели |
|---|---|
| Demo (offline) | scripted-сценарий, работает без ключей |
| OpenAI | GPT-4o, GPT-4o mini |
| Anthropic | Claude Sonnet 5, Claude Opus 4.8 |
| Z.ai | GLM-5.3-flash, GLM-5.3, GLM-5.2, GLM-4.6, GLM-4.5-flash |
| Ollama (local) | Llama 3.2 3B, Llama 3.1 8B, Mistral |

## Known Limitations

- **Prompt injection через документы.** Ксюша принимает произвольные файлы, их содержимое попадает в контекст LLM; санитизации инъекций на RAG-стороне нет. SQL-сторона закрыта guard'ом (read-only + лимиты), но текст ответа теоретически можно сместить содержимым загруженного файла.
- **Golden set авторский и небольшой.** Routing n=20, retrieval n=8 пар — на текущем retrieval-сете все режимы дают 1.0, то есть он пока не дискриминирует режимы поиска. Вопросы и эвристики роутеров писались одним автором: возможна подгонка эвристики под лексику сета. Следующие шаги: held-out вопросы, более сложный retrieval-сет, кросс-модельный прогон (полные 50 SQL-кейсов — пока только у GLM-4.6; остальные модели измерены на 3 вопросах).
- **Self-correction покрыта юнит-тестами, но не измерена end-to-end.** В головном прогоне SQL не падал (0 ремонтов), поэтому repair success rate не квантифицирован.
- **Single-turn.** Контекст диалога не хранится — каждый запрос обрабатывается независимо.
- **Стоимость — пока не метрика.** Латентность измеряется по моделям; tokens / cost per query не считаются.

## Tech Stack

- **Backend:** Python, FastAPI, SQLAlchemy 2 (SQLite / PostgreSQL / ClickHouse), fastembed, openpyxl
- **Frontend:** React 19, Vite, Recharts, xlsx, docx-preview
- **LLM:** OpenAI / Anthropic / Z.ai / Ollama — prompt-based tool-calling, единый интерфейс провайдеров с soft-fallback в Demo-режим
- **Quality:** pytest + pytest-asyncio, golden-set evaluation harness
- **Infra:** Docker (multi-stage: сборка frontend → раздача из FastAPI), docker compose

## Quick Start

```bash
chmod +x dev.sh
./dev.sh
```

- UI: http://localhost:5173
- API: http://localhost:8001/docs  (порт 8001, чтобы не пересекаться с RAG Chat на 8000)

Без ключей работает **Demo (offline)**.

## Configuration

`backend/.env` ← из `backend/.env.example`:

```env
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
ZAI_API_KEY=
OLLAMA_BASE_URL=http://localhost:11434
DEMO_SCALE=small   # или full для более плотных данных
```

**Тестовый PostgreSQL** (локально): `docker compose -f docker-compose.test.yml up -d` → `demo:demo@localhost:5433/shop` (e-commerce: products, customers, orders) или env `POSTGRES_URL`.

## Testing

```bash
cd backend
pip install -r requirements.txt   # включает pytest, pytest-asyncio
pytest -v
```

Покрытие: аналитический слой (`analytics.py`), SQL guard (`sql_guard.py`), self-correction loop Олега (`oleg.py`), качество retrieval (`test_retrieval_quality.py`), роутинг и источники. Тесты изолированы — используют временную SQLite-БД и не требуют API-ключей.

Golden-set evaluation (нужен ключ провайдера):

```bash
cd backend
python scripts/evaluate.py --suite all --model zai:glm-4.6   # SQL 50 + routing 20
python scripts/latency_benchmark.py --models zai:glm-5.2 --runs 3
```

## Deployment

**Hugging Face Spaces:**

1. Space → **Docker**, порт **7860** (см. YAML в начале README).
2. Secrets (опционально): `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `ZAI_API_KEY`.
3. Root `Dockerfile` собирает frontend и отдаёт его из FastAPI.

Локальная проверка образа:

```bash
docker build -t ai-data-pilot .
docker run --rm -p 7860:7860 ai-data-pilot
# → http://localhost:7860
```

**Docker Compose (dev split):**

```bash
docker compose up --build
```
