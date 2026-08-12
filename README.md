---
title: AI Data Pilot
emoji: 📊
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

# 📊 AI Data Pilot — аналитические агенты Олег и Ксюша

> Дашборд с двумя AI-агентами: **Олег** ходит в БД (NL → SQL → таблица / график / Excel),
> **Ксюша** отвечает по внутренней документации. Сохранённые сценарии — в один клик.

[![Demo](https://img.shields.io/badge/demo-🤗%20Hugging%20Face%20Spaces-ff9d00)](https://huggingface.co/spaces)
![backend](https://img.shields.io/badge/backend-FastAPI-009688)
![frontend](https://img.shields.io/badge/frontend-React%2019%20%2B%20Vite-61dafb)
![sql](https://img.shields.io/badge/Text--to--SQL-ready-6366f1)

<sub>Демо на бесплатном тарифе может «засыпать» — первый заход после простоя поднимается ~1 мин.</sub>

## Возможности

- 👤 **Аналитик Олег** — Text-to-SQL, KPI, Recharts, Excel, показ SQL и методологии
  - **Self-correction** — если SQL упал, агент видит ошибку и переписывает запрос (до 2 попыток) вместо молчаливой подмены данных
  - **Детерминированная аналитика** — тренды, топ-N, аномалии (z-score) считает Python; LLM только оформляет текст. Цифры в ответе всегда точные
  - **Прозрачные статусы** — каждый ответ помечен: `Реальный ответ` / `Демо-режим` / `С коррекцией` / `Ошибка`
  - **Таймаут запросов** — долгие SQL не вешают endpoint
  - **Мульти-источники** — работайте со встроенной демо-БД RideGo **или загрузите свой файл** (CSV или Excel `.xlsx`). Типы колонок и схема автогенерируются, каждый лист Excel становится отдельным источником, и Олег строит SQL по вашим данным
- 👍 **Голосовалка** — оценки 👍/👎 сохраняются на backend (для мониторинга качества ответов)
- 👩‍💻 **Ксюша** — RAG по фейковой док-базе (метрики, lineage, backend)
- ⚡ **Сценарии** — one-click отчёты + сохранение своих
- 🤖 **Модели** — Demo (offline), OpenAI, Anthropic, Z.ai (GLM), Ollama
- 🎨 UI в стиле RAG Chat — dark/light, RU/EN, мобильное меню
- 🧪 **Тесты** — pytest (70 тестов) покрывает аналитический слой, SQL guard, self-correction loop, мульти-источники (CSV + Excel) и app-БД

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
                              ├─ Oleg: schema → SQL → guard → analytics → chart/xlsx
                              │         ↑ self-correction (2 retry rounds)
                              │         ↑ deterministic insights (Python, not LLM math)
                              ├─ DataSources: RideGo (built-in) | user-uploaded CSV
                              └─ Ksyusha: keyword RAG over data/docs/*.md

App DB (SQLite): scenarios · datasource metadata · feedback votes
Analytics DB:    RideGo (seeded) · uploaded CSV tables
```

**Хранилище:** Сценарии, метаданные источников и голоса (👍/👎) — в `app.db` (SQLite). Данные аналитики — в `ridego.db` (встроенный демо-домен) и `csv_sources.db` (загруженные CSV).

**Источники данных:** RideGo (демо, ~21k поездок) — встроенный. CSV-файлы загружаются через UI → автодетекция типов колонок → SQLite-таблица → автогенерация схемы для промпта Олега. Сценарии привязаны к источнику.

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
