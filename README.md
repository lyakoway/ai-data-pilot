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
- 👩‍💻 **Ксюша** — RAG по фейковой док-базе (метрики, lineage, backend)
- ⚡ **Сценарии** — one-click отчёты + сохранение своих
- 🤖 **Модели** — Demo (offline), OpenAI, Anthropic, Z.ai (GLM), Ollama
- 🎨 UI в стиле RAG Chat — dark/light, RU/EN, мобильное меню

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
                              ├─ Oleg: schema → SQL → guard → SQLite → chart/xlsx
                              └─ Ksyusha: keyword RAG over data/docs/*.md
```

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

## Портфолио

Карточка: `/portfolio/ai-data-pilot` в `lyako-way` (категория AI-агенты).
После публикации демо пропишите `hrefPortfolio` и добавьте скриншоты.
