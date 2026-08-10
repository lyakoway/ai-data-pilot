# AI Data Pilot

Аналитический дашборд с двумя AI-агентами в стиле корпоративного ChatOps:

- **Аналитик Олег** — NL → SQL → таблица / график / Excel на демо-БД **RideGo** (микромобильность)
- **Ксюша** — ответы по фейковой внутренней документации (метрики, lineage, backend)

Стек совпадает с [RAG Chat](../ai-RAG-chat): FastAPI + React/Vite, те же design tokens, облачные LLM + **Ollama**.

## Быстрый старт

```bash
chmod +x dev.sh
./dev.sh
```

- UI: http://localhost:5173  
- API: http://localhost:8000/docs  

Без ключей работает **Demo (offline)** — детерминированный SQL и ответы по докам.

### Ключи (опционально)

Скопируйте `backend/.env.example` → `backend/.env`:

```env
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
ZAI_API_KEY=
OLLAMA_BASE_URL=http://localhost:11434
```

Локально: `ollama pull llama3.2:3b` (или другая модель из списка).

## Что умеет

| Фича | Описание |
|------|----------|
| Дашборд KPI | Поездки, выручка, пользователи, графики |
| Сценарии | One-click отчёты, сохранение своих |
| SQL-прозрачность | Показ запроса, как у «Олега» на скринах |
| Excel | Выгрузка результата |
| Два агента | Oleg (БД) / Ksyusha (docs RAG) |
| Multi-model | OpenAI, Anthropic, Z.ai, Ollama, mock |

## Данные

Синтетическая SQLite-база `backend/data/ridego.db` (сиды при старте):

- `dim_city`, `dim_user`, `fact_rides`, `fact_subscriptions`

Доки Ксюши: `backend/data/docs/*.md` (можно править).

Для «боевых» открытых датасетов можно заменить сиды на выгрузки с [Kaggle](https://www.kaggle.com/datasets) или источников из [статьи «Код»](https://thecode.media/5-big-data/) — достаточно обновить схему в `schema_catalog.py` и сидер.

## Портфолио

Карточка добавляется в `lyako-way` как `/portfolio/ai-data-pilot` (по аналогии с `rag-chat`), категория **AI-агенты**.

## Docker

```bash
docker compose up --build
```
