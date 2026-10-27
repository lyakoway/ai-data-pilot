# EVALUATION — методология и определения метрик

Canonical-документ. Команды запуска — [backend/BENCHMARK.md](../backend/BENCHMARK.md);
сводные числа — [BENCHMARK_RESULTS.md](BENCHMARK_RESULTS.md); аудит —
[EVALUATION_AUDIT.md](EVALUATION_AUDIT.md). Уровни измерений строго
разделены: локальные детерминированные / внешний LLM API / E2E приложение /
локальный inference runtime.

## 1. Уровни и инструменты

| Уровень | Инструмент | Ключи API | Детерминирован |
|---|---|---|---|
| Unit/integration pytest (215) | `pytest` | нет | да |
| RAG retrieval (chunk-level) | `scripts/eval_rag.py` | нет | да |
| SQL quality (v1/v2 golden) | `scripts/eval_sql_full.py` | Z.ai | нет (LLM) |
| Routing accuracy | `scripts/evaluate.py --suite routing` | Z.ai | нет |
| RAG generative (LLM-judge) | `scripts/rag_judge.py` | Z.ai | нет |
| E2E SSE latency | `scripts/e2e_bench.py` | Z.ai (mock — нет) | нет |
| Токенный/нагрузочный/контекст | `scripts/llm_bench.py` | Z.ai | нет |
| Оркестрация всего | `scripts/run_eval.py --suite …` | по suite | — |
| Regression-гейт | `scripts/check_regression.py` | нет | да (по артефактам) |
| Таксономия ошибок SQL | `scripts/error_analysis.py` | нет | да (эвристика) |
| Сравнение моделей | `scripts/compare_sql_evals.py --merge` | нет | да (по артефактам) |

## 2. Text-to-SQL метрики

- **Execution Accuracy** = доля кейсов, где сгенерированный SQL выполнился.
  ≠ правильность. Инфра-сбои (клиентский timeout, 429) считаются отдельно
  (`infra_failure_share`), дублируются в `…_excl_infra`.
- **Result Accuracy** = доля кейсов (из проверяемых), где нормализованное
  МНОЖЕСТВО строк совпало с эталоном. Нормализация: строки → множества
  кортежей (порядок строк/колонок-дубликатов не важен), float → round 2,
  NULL → "". Ambiguous-кейсы (check≠"result") — execution-only и из знаменателя
  исключены. Ограничения: чувствительна к выбору колонок (другой набор
  колонок = false negative); сравнение множеств НЕ проверяет порядок в
  топ-K запросах (top-3 другим порядком = провал — сознательно строго).
  Одинаковый кап 500 строк (guard-обёртка) применяется к обоим сравниваемым.
- **Exact Match** = строковое равенство SQL после lowercase/collapse-ws/trim-`;`.
  Сознательно жёсткая, НЕ headline (другой алиас = mismatch).
- **Self-Correction Success Rate** = исправленные/требовавшие исправления
  (знаменатель — только кейсы с попыткой repair). 0 repaired → метрика
  неопределима (не 0%).
- **Wilson 95% CI** для всех долей; n<20 → p99 не выводится.
- **Таксономия ошибок** (`error_analysis.py`, эвристика по структуре SQL):
  infra / no_sql / syntax-exec / wrong_table / wrong_join / aggregation /
  temporal / filter / interpretation. Метки направляющие, не доказательные.

## 3. RAG метрики

**Retrieval (детерминировано, chunk-level, v2: 24 кейса):**
- Recall@k = gold-чанк в top-k; Precision@k = |gold ∩ top-k|/k;
  MRR@10 = 1/rank первого gold; nDCG@10 c бинарными релевантностями
  (multi-gold учитён в IDCG).
- Ablation: bm25 / vector / hybrid на одинаковых запросах и корпусе.
  Ограничение: корпус 10 чанков → Precision@5 ограничен 0.2 сверху при
  одном gold-чанке; Recall@5 упирается в 1.0 — различимость держится на @1
  и конфузибл-парах.
- Негативные кейсы не имеют gold → из retrieval-метрик исключены; их роль —
  в генеративной проверке abstention.

**Генеративные (LLM-as-a-judge, `rag_judge.py`, рубрика v2):**
- faithfulness = каждое фактическое утверждение буквально из CONTEXT;
- answer relevancy = ответ содержит запрошенную информацию (честный отказ → 0,
  он оценивается отдельно);
- citation correctness = есть ≥1 корректный [n], каждый существует и
  поддерживает утверждение (формат «файл.md#1» вместо [n] → 0);
- abstention = на негативе система честно отказалась.
- Детерминированная часть: валидность индексов [n] без LLM.
- Judge калибруется на 3 фиксированных примерах (согласие публикуется).
  Ограничения: judge и отвечающая модель — одно семейство (self-judging bias),
  один judge, бинарные оценки, малый n. Направляющий сигнал, не ground truth.

## 4. Latency/throughput метрики

- TTFT reasoning / TTFT answer — раздельно (thinking-модели); t0 до create().
- TPOT/ITL — каденс чанков, наблюдаемый клиентом (шлюз может склеивать flush);
  completion_tok_per_s по usage (включает reasoning у thinking-моделей —
  семантика зафиксирована), answer_chunk_per_s — только ответ.
- p50/p95/p99 — nearest-rank (lower); mean/std; rps/tps — только по фактически
  завершённым; авто-стоп эскалации конкурентности при 429.
- E2E: time_to_first_event (первый SSE-шаг, ≠ TTFT), total, длительности
  шагов из самого приложения, repairs, статусы, numeric grounding (эвристика).
- Контекст-матрица 1k–32k: TTFT vs длина входа; prefix-проба — black-box,
  механизм недоказуем.

## 5. Статистические правила

- Доли сопровождаются Wilson 95% CI и знаменателем (match/checked).
- Сравнения на границах CI не заявляются значимыми.
- Regression-гейт (`check_regression.py`): блокирует только если baseline
  выше верхней границы Wilson-CI кандидата; контрактные проверки
  (exec<50%, infra>20%) — вне статистики. Малые выборки не блокируют релиз
  по одной метрике — осознанно.

## 6. Воспроизводимость артефактов

Каждый прогон: run_id (для `run_eval.py`) или timestamp-имя файла, git SHA,
env (python/openai/httpx), версии моделей как отчитывает API, hash промпта и
датасета, параметры генерации, сырые per-case данные отдельным файлом
(JSON+CSV). Исторические артефакты не перезаписываются.
