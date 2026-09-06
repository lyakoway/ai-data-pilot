# Evaluation — методика оценки качества

Два уровня измерения, как в RAG Chat:

1. **pytest (детерминированный)** — 174 теста: логика, guard, аналитика,
   retrieval-метрики. Запускаются в CI, не требуют API-ключей.
2. **LLM evaluation (ручной прогон)** — качество генерации с реальной моделью:
   golden set → прогон → метрики. Числа воспроизводимы одной командой.

## Быстрый старт

```bash
cd backend
python scripts/evaluate.py --suite all --model zai:glm-4.6
python scripts/evaluate.py --suite sql --model openai:gpt-4o-mini --limit 10   # debug
```

Результаты: консольный отчёт + `evaluation_results.json`.

## Метрики LLM-прогона (SQL suite — 30 golden-вопросов)

| Метрика | Что показывает |
|---|---|
| **SQL Execution Accuracy** | % запросов, выполнившихся без ошибки |
| **Result Accuracy** | % совпадений результата с эталонными строками (execution match) |
| **Self-Correction Rate** | % исправленных агентом упавших запросов |
| **Task Completion Rate** | % вопросов, доведённых до результата |
| **Schema Accuracy** | % правильного выбора таблиц |
| **Latency p50 / p95** | время plan → execute → repair |

## Метрики Routing suite (20 golden-вопросов)

| Метрика | Что показывает |
|---|---|
| **Agent Routing Accuracy** | % вопросов, направленных правильному агенту (Олег/Ксюша) |

## Метрики Retrieval (pytest, детерминированно)

`tests/test_retrieval_quality.py` — 8 golden-пар «вопрос → документ»:
Recall@1, Recall@5, MRR для трёх режимов (**bm25**, **vector**, **hybrid**).
Гарантия: hybrid Recall@5 = 100%, hybrid не хуже компонентов.

## Golden set

- `tests/golden/sql_golden.jsonl` — 30 вопросов: simple(10) + join(6) + agg(2) +
  temporal(5) + edge(3) + cross-file(4). Каждому — `reference_sql` (эталон)
  и `expected_tables`.
- `tests/golden/routing_golden.jsonl` — 20 вопросов: oleg(10) / ksyusha(10).

Формат совпадения результатов — execution match: множества строк
(порядко- и формат-независимо, float до 2 знаков).

## Ограничения (честно)

- Result Accuracy чувствительна к выбору колонок: LLM может вернуть тот же
  ответ с другим набором/именами колонок → false negative. Смягчение —
  точные формулировки в вопросах.
- Embeddings загружаются при первом прогоне retrieval-тестов (~10 сек).
- Прогон LLM-суит требует рабочего провайдера (GLM / OpenAI / Ollama).
