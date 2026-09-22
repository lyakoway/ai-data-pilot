[English](README.md) | Русский

# 📊 AI Data Pilot — мультиагентная аналитика

> **Live demo:** [lyakoway-ai-data-pilot.hf.space](https://lyakoway-ai-data-pilot.hf.space/)

Аналитика на естественном языке: SQL, график и объяснение. Два специализированных
агента — **Олег** (Data Agent: Text-to-SQL, графики, Excel) и **Ксюша**
(Knowledge Agent: RAG по документам и загрузкам) — плюс двухуровневый
авто-роутер. В английском UI: Data Agent / Knowledge Agent.

[![Demo](https://img.shields.io/badge/demo-lyakoway--ai--data--pilot.hf.space-ff9d00)](https://lyakoway-ai-data-pilot.hf.space/)
![backend](https://img.shields.io/badge/backend-FastAPI-009688)
![frontend](https://img.shields.io/badge/frontend-React%2019%20%2B%20Vite-61dafb)
![tests](https://img.shields.io/badge/tests-174%20passed-brightgreen)
![sources](https://img.shields.io/badge/sources-PostgreSQL%20·%20ClickHouse%20·%20CSV%20·%20Excel-6366f1)

<sub>Демо-Space на бесплатном тарифе может «засыпать» — первый заход после
простоя занимает ~1 мин. Векторная модель (fastembed) грузится при первом
поиске Ксюши (~10 с).</sub>

**Что ушло в прод — три решения по замерам:**

1. **Считает Python** — тренды, проценты, топ-N и z-score — в Python-слое. LLM
   пишет текст, не считает бизнес-цифры.
2. **Упавший SQL — контракт** — SQL Guard (SELECT-only) + две переписки +
   честная ошибка. Тихой подмены результата нет.
3. **Два агента, не один промпт** — Олег — SQL, Ксюша — документы.
   Двухуровневый роутер, решение видно в SSE-trace.

Headline SQL-качества на публичном тестовом паке (GLM-4.6): **~85%
нормализованной точности результата** (те же числа без учёта алиасов, порядка строк
и формата чисел — не string exact-match). **~98% execution** значит только,
что запрос выполнился. **2ч → 2мин** на отчётность — из продового опыта, не это демо.

## Ключевые возможности

- 🧭 **Двойной авто-роутинг** — по агенту (данные → Олег, документы → Ксюша) и
  по источнику (вопрос → нужная БД). Оба роутера: LLM-классификация +
  детерминированная эвристика как fallback. Ручные переключатели остаются
  как override.
- 👤 **Олег (Data Agent)**
  - **Agent Loop (ReAct)** — `database_query → calculate → analyze → create_chart → finish`. Prompt-based tool-calling работает с любым провайдером, включая офлайн Demo.
  - **Execution trace (SSE)** — живые шаги: SQL, row_count, insights.
  - **Self-correction** — упавший SQL возвращается с ошибкой БД. Агент переписывает (до 2 раундов).
  - **Детерминированная аналитика** — числа считает Python. LLM только интерпретирует.
- 👩‍💻 **Ксюша (Knowledge Agent)**
  - **Гибридный поиск** — BM25-IDF + векторные эмбеддинги (fastembed, 50+ языков). Ablation поиска — в кейсе RAG Chat, не здесь.
  - **Загрузки** — PDF, Word, Excel, CSV, TXT, MD. Excel — ещё и SQL-таблица для Олега.
  - **Инлайн-цитаты `[1]`** и просмотрщик документов (PDF-страница, DOCX, Excel-таблица).
- 🗄️ **Источники** — PostgreSQL и ClickHouse (UI или env, интроспекция схемы, диалектные промпты), виртуальный источник **«Все загрузки»** с JOIN между файлами.
- ⚡ **Параметризованные сценарии** — шаблоны с `{period}`, `{group_by}`.
- 👍 **Фидбек** — аналитика 👍/👎 по агентам.
- 🤖 **13 конфигураций моделей** — OpenAI, Anthropic, Z.ai, Ollama + офлайн Demo. **GLM-4.6 — по умолчанию** (на ней снята оценка ~85% SQL).

## Инженерный подход

Вероятностные LLM-рассуждения отделены от детерминированной логики приложения:

- **LLM** — интент, роутинг, генерация SQL, выбор инструмента, текст
- **Python** — числа, тренды, топ-N, аномалии, валидация результата
- **SQL Guard** — валидация сгенерированного SQL до выполнения
- **Self-correction** — упавший SQL возвращается с ошибкой БД, ограниченные ретраи
- **Observability** — каждый шаг агента стримится по SSE

Сквозная латентность — **десятки секунд** (план LLM + ответ), а не загрузка
страницы. Стриминговые шаги делают ожидание наблюдаемым.

## Архитектура

```
[React-дашборд] ──/api──▶ [FastAPI]
                              ├─ Роутер агентов: данные → Олег, документы → Ксюша
                              ├─ Олег: схема → SQL → guard → аналитика → chart/xlsx
                              │    ↑ Agent Loop (ReAct): многошаговый tool-calling
                              │    ↑ self-correction (2 раунда починки)
                              │    ↑ детерминированные инсайты (Python, не LLM-математика)
                              │    ↑ execution trace стримится по SSE
                              ├─ Роутер источников: вопрос → нужная БД
                              ├─ Источники: RideGo | PostgreSQL | ClickHouse
                              │              | CSV/Excel (SQL + RAG) | Все загрузки (JOIN)
                              └─ Ксюша: гибридный RAG (BM25-IDF + векторы fastembed)
                                        по встроенным документам и загруженным файлам

App DB (SQLite): сценарии · метаданные источников · фидбек · документы · чанки
Analytics DB:    RideGo (seeded) · загруженные CSV/Excel-таблицы
Files:           data/uploads/ (оригиналы для просмотрщика документов)
```

**Хранение:** сценарии, метаданные источников, фидбек, документы и чанки живут
в `app.db` (SQLite). Аналитика: `ridego.db` (демо-домен RideGo: `dim_city`,
`dim_user`, `fact_rides`, `fact_subscriptions`) и `csv_sources.db` (загруженные
таблицы). CSV/Excel входят в **оба** конвейера: SQL-таблица для Олега и
текстовые чанки для Ксюши. Пароли источников остаются на сервере и никогда
не возвращаются на фронтенд.

## Инженерные решения

### Детерминированная аналитика

LLM не считает бизнес-метрики. Численная работа и поиск аномалий — в Python
(`analytics.py`). Цифры в ответе всегда приходят из БД или Python — никогда
из генерации.

### Ограниченное выполнение агента

У ReAct-цикла жёсткий лимит шагов (`MAX_LOOP_STEPS = 6`). Self-correction SQL —
два раунда починки (`MAX_SQL_REPAIR_ROUNDS = 2`). Худший случай — честный отказ,
а не зависший tool-loop.

### Почему два агента

Анализ данных и поиск по документам — разные инструменты, лимиты и режимы
отказа. Роутинг на узкого агента держит каждый сценарий ограниченным и измеримым.

### LLM vs детерминированный код

| Ответственность | Реализация |
|---|---|
| Интент | LLM |
| Роутинг агентов | LLM-классификация + детерминированная эвристика (fallback) |
| Роутинг источников | LLM по схемам + эвристика (fallback) |
| Генерация SQL | LLM |
| Валидация SQL | Python (SQL Guard: только чтение, один statement, запрещённые ключевые слова) |
| Лимиты выполнения | Python: таймаут 8 с (30 с для удалённого PostgreSQL), макс. 500 строк |
| Выполнение SQL | SQLite / PostgreSQL / ClickHouse |
| Числа (тренды, топ-N, проценты) | Python |
| Поиск аномалий | Python (z-score) |
| Графики | Python готовит spec + данные, React / Recharts рисует |
| Поиск по документам | Python: BM25-IDF + векторные эмбеддинги (fastembed) |
| Текст финального ответа | LLM |

**LLM не используется там, где обычный код надёжнее.**

## Оценка и бенчмарки

Цифры ниже совпадают со
[страницей кейса](https://lyakoway.vercel.app/portfolio/ai-data-pilot).
SQL-оценка — на **публичном паке RideGo**, не продовых базах.

**174 pytest-теста:** Agent Loop, SQL guard, self-correction, качество поиска
(Recall@1/5 · MRR для BM25 / Vector / Hybrid), контракты численной аналитики,
роутинг, источники. Изолированные temp-SQLite, ключи API не нужны.

Харнесс: `backend/scripts/evaluate.py` — методология в
[EVALUATION.md](backend/EVALUATION.md).

**Качество SQL — held-out, живой прогон GLM-4.6:**

| Метрика | Результат |
|---|---|
| **Нормализованная точность результата** (те же числа после канонизации) | **~85%** |
| SQL Execution Accuracy (запрос выполнился — это не правильность) | ~98% |

Headline-качество — **~85% нормализованных**: те же числа без учёта алиасов
колонок, порядка строк и формата чисел. Строгий string exact-match — не
headline. ~98% значит только, что запрос выполнился.

**Модель по умолчанию: GLM-4.6** — на ней снята оценка ~85%. Более быстрые
модели не обязательно лучше в SQL.

### Латентность по моделям (живой прогон)

| Модель | План (LLM) | Выполнение (БД) | Ответ (LLM) | Итого | SQL ok |
|---|---|---|---|---|---|
| **GLM-4.6 (Z.ai) — по умолчанию** | 13.5 с | 12 мс | 16.3 с | ~29.8 с | 2/3 |
| GLM-5.2 (Z.ai) | 7.0 с | 6 мс | 9.4 с | ~16.4 с | 3/3 |
| GLM-5.3-flash (Z.ai) | 7.0 с | 8 мс | 4.8 с | ~11.9 с | 2/3 |
| GLM-5.3 (Z.ai) | 13.8 с | 11 мс | 5.1 с | ~19.0 с | 1/3 |

<sub>Медианы 3 прогонов **одного** вопроса через полный цикл (план → БД →
ответ). Латентность внешнего API варьируется. GLM-4.6 остаётся default: оценка
~85% снята на ней. GLM-5.2 быстрее на этом семпле. GLM-5.3-flash ещё быстрее,
но SQL слабее. Скрипт: `python scripts/latency_benchmark.py`
(из `backend/`).</sub>

Полный цикл — **~16–30 с** в зависимости от модели — нижняя граница провайдера
на план + ответ, а не дашборд за долю секунды. Стриминговые шаги — локальные
источники пропускают внешний handshake.

### Реестр моделей (13 конфигураций)

| Провайдер | Модели |
|---|---|
| Demo (офлайн) | scripted-сценарий, без ключей |
| OpenAI | GPT-4o, GPT-4o mini |
| Anthropic | Claude Sonnet 5, Claude Opus 4.8 |
| Z.ai | GLM-5.3-flash, GLM-5.3, GLM-5.2, **GLM-4.6 (default)**, GLM-4.5-flash |
| Ollama (локально) | Llama 3.2 3B, Llama 3.1 8B, Mistral |

## Технологии

- **Бэкенд:** Python, FastAPI, SQLAlchemy 2 (SQLite / PostgreSQL / ClickHouse), fastembed, openpyxl
- **Фронтенд:** React 19, Vite, Recharts, xlsx, docx-preview
- **LLM:** OpenAI / Anthropic / Z.ai / Ollama — prompt-based tool-calling, мягкий fallback на Demo
- **Качество:** pytest + pytest-asyncio, харнесс оценки на golden set
- **Инфра:** Docker (multi-stage: сборка фронтенда → FastAPI static), docker compose

## Быстрый старт

```bash
chmod +x dev.sh
./dev.sh
```

- UI: http://localhost:5173
- API: http://localhost:8001/docs  (порт 8001, чтобы не пересекаться с RAG Chat на 8000)

**Demo (офлайн)** работает без ключей.

## Конфигурация

`backend/.env` ← из `backend/.env.example`:

```env
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
ZAI_API_KEY=
OLLAMA_BASE_URL=http://localhost:11434
DEMO_SCALE=small   # или full для более плотных данных
```

**Тестовый PostgreSQL** (локально): `docker compose -f docker-compose.test.yml up -d`
→ `demo:demo@localhost:5433/shop` или env `POSTGRES_URL`.

## Тестирование

```bash
cd backend
pip install -r requirements.txt   # включает pytest, pytest-asyncio
pytest -v
```

Покрытие: слой аналитики (`analytics.py`), SQL guard (`sql_guard.py`), цикл
self-correction Олега, качество поиска, роутинг и источники. Изолированные
temp-SQLite — без API-ключей.

Оценка на golden set (нужен ключ провайдера):

```bash
cd backend
python scripts/evaluate.py --suite all --model zai:glm-4.6
python scripts/latency_benchmark.py --models zai:glm-4.6 --runs 3
```

## Деплой

**Hugging Face Spaces:**

1. Space → **Docker**, порт **7860** (YAML — вверху английского README).
2. Secrets (опционально): `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `ZAI_API_KEY`.
3. Корневой `Dockerfile` собирает фронтенд и раздаёт его через FastAPI.

Локальная проверка образа:

```bash
docker build -t ai-data-pilot .
docker run --rm -p 7860:7860 ai-data-pilot
# → http://localhost:7860
```

**Docker Compose (dev-разделение):**

```bash
docker compose up --build
```
