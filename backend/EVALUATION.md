# Evaluation — методика оценки качества

> Методология и определения всех метрик консолидированы в
> [docs/EVALUATION.md](../docs/EVALUATION.md) (canonical).
> Команды запуска — [BENCHMARK.md](BENCHMARK.md); сводка результатов —
> [docs/BENCHMARK_RESULTS.md](../docs/BENCHMARK_RESULTS.md).

Кратко (детали и ограничения — в canonical-документе):

1. **pytest (детерминированный)** — 215 тестов: логика, guard, аналитика,
   retrieval, security/regression. CI, без ключей.
2. **LLM evaluation (живые прогоны)** — golden set → метрики; воспроизводимо
   командами из BENCHMARK.md; артефакты в `bench_results/` с manifest.

- SQL: Execution Accuracy, Result Accuracy (execution match, нормализация
  множеств строк), Exact Match, Wilson 95% CI, таксономия ошибок.
- RAG: chunk-level Recall/Precision/MRR/nDCG + ablation bm25/vector/hybrid;
  генеративные свойства — LLM-judge с калибровкой.
