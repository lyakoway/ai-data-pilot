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

Два AI-агента и авто-роутер: система сама понимает вопрос и направляет его нужному агенту —
**Олег** (Text-to-SQL: базы данных, SQL, графики, Excel) или **Ксюша** (RAG: документация, поиск по загруженным файлам).

[![Demo](https://img.shields.io/badge/demo-lyakoway--ai--data--pilot.hf.space-ff9d00)](https://lyakoway-ai-data-pilot.hf.space/)
![backend](https://img.shields.io/badge/backend-FastAPI-009688)
![frontend](https://img.shields.io/badge/frontend-React%2019%20%2B%20Vite-61dafb)
![tests](https://img.shields.io/badge/tests-161%20passed-brightgreen)
![sources](https://img.shields.io/badge/sources-PostgreSQL%20·%20ClickHouse%20·%20CSV%20·%20Excel-6366f1)

<sub>Демо на бесплатном тарифе может «засыпать» — первый заход после простоя поднимается ~1 мин.
Векторная модель (fastembed) загружается при первом поиске Ксюши (~10 сек).</sub>

## Возможности

- 🧭 **Авто-роутинг** — двойной: агент (данные → Олег, документация → Ксюша) и источник данных (по смыслу вопроса выбирается нужная БД). Переключатели остаются как ручной override
- 👤 **Аналитик Олег**
  - **Agent Loop (ReAct)** — для сложных вопросов агент сам решает какие tools вызвать: `database_query → calculate → analyze → chart → finish`. Prompt-based tool-calling работает со всеми провайдерами, включая Demo (scripted сценарий)
  - **Execution trace (SSE)** — пошаговая работа в реальном времени; каждый шаг раскрывается (SQL, row_count, инсайты)
  - **Self-correction** — упавший SQL агент переписывает сам (до 2 попыток), вместо молчаливой подмены данных
  - **Детерминированная аналитика** — тренды, топ-N, аномалии (z-score) считает Python; LLM только оформляет текст. Цифры всегда точные
- 👩‍💻 **Ксюша**
  - **Гибридный поиск** — BM25-IDF + векторные эмбеддинги (fastembed, мультиязычная модель, 50+ языков): находит по смыслу и на другом языке
  - **Загрузка документов** — PDF, Word, Excel, CSV, TXT, MD (drag&drop); Excel одновременно становится SQL-таблицей для Олега
  - **Inline-цитаты `[1]`** и просмотрщик документов: PDF на нужной странице, DOCX рендер, Excel как таблица
- 🗄️ **Источники данных**
  - PostgreSQL и ClickHouse (кнопки в интерфейсе, автосхема через интроспекцию, диалект-зависимые промпты)
  - Виртуальный источник **«Все загрузки»** — Олег видит все загруженные таблицы и строит JOIN между файлами
- ⚡ **Параметризованные сценарии** — шаблоны с `{period}`, `{group_by}`; один сценарий — бесконечное переиспользование
- 👍 **Витрина фидбека** — аналитика оценок 👍/👎 по агентам с фильтрами
- 🤖 **Модели** — Demo (offline), OpenAI, Anthropic, Z.ai (GLM), Ollama
- 🧪 **161 тест** — pytest: Agent Loop, self-correction, SQL guard, execution trace, все типы источников, RAG, параметризованные сценарии

## Быстрый старт (локально)

```bash
chmod +x dev.sh
./dev.sh
```

- UI: http://localhost:5173  
- API: http://localhost:8001/docs  (порт 8001, чтобы не пересекаться с RAG Chat на 8000)

Без ключей работает **Demo (offline)**.

### Ключи (опционально)

`backend/.env` ← из `backend/.env.example`:

```env
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
ZAI_API_KEY=
OLLAMA_BASE_URL=http://localhost:11434
DEMO_SCALE=small   # или full для более плотных данных
```

## Архитектура

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

**Хранилище:** Сценарии, метаданные источников, документы, чанки и голоса — в `app.db`. Данные аналитики — в `ridego.db` (демо-домен) и `csv_sources.db` (загруженные таблицы).

**Источники данных:** RideGo (демо) — встроенный. CSV/Excel — через drag&drop, попадают **в оба pipeline**: SQL-таблица для Олега + текстовые чанки для Ксюши. **PostgreSQL** и **ClickHouse** — кнопки в интерфейсе (или env `POSTGRES_URL`), автосхема через интроспекцию, диалект-зависимые промпты. Пароли хранятся server-side, никогда не возвращаются на фронтенд.

**Тестовый PostgreSQL** (локально): `docker compose -f docker-compose.test.yml up -d` → `demo:demo@localhost:5433/shop` (e-commerce: products, customers, orders).

Демо-домен: **RideGo** (микромобильность) — `dim_city`, `dim_user`, `fact_rides`, `fact_subscriptions`.

## Деплой на Hugging Face Spaces

1. Space → **Docker**, порт **7860** (см. YAML в начале README).
2. Secrets (опционально): `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `ZAI_API_KEY`.
3. Root `Dockerfile` собирает frontend и отдаёт его из FastAPI.

Локальная проверка образа:

```bash
docker build -t ai-data-pilot .
docker run --rm -p 7860:7860 ai-data-pilot
# → http://localhost:7860
```

## Docker Compose (dev split)

```bash
docker compose up --build
```

## Тесты

```bash
cd backend
pip install -r requirements.txt   # включает pytest, pytest-asyncio
pytest -v
```

Покрытие: аналитический слой (`analytics.py`), SQL guard (`sql_guard.py`), self-correction loop Олега (`oleg.py`). Тесты изолированы — используют временную SQLite-БД и не требуют API-ключей.

## Портфолио

Карточка: `/portfolio/ai-data-pilot` в `lyako-way` (категория AI-агенты).
После публикации демо пропишите `hrefPortfolio` и добавьте скриншоты.
