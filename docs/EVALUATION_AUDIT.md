# EVALUATION AUDIT — AI Data Pilot (этап 3, 2026-10-24)

Аудит состояния evaluation-инфраструктуры на коммите `6dee1da` (этапы 1–2
предыдущих итераций уже в истории). Ответы на 8 вопросов плана + приоритеты.

Легенда статуса: ✅ работает · ⚠️ работает с ограничениями · ❌ отсутствовало
на момент аудита (реализовано в этом этапе — помечено [И]).

## 1. Что реализовано и действительно работает

| Компонент | Статус | Где |
|---|---|---|
| pytest-контур (unit/integration, 186 → 215 тестов) | ✅ | `backend/tests/` |
| SQL-eval: Exec/Result Accuracy, Exact Match, Wilson CI, категории, классификация инфра-сбоев | ✅ | `scripts/eval_sql_full.py`, golden v1(50)+v2(20) |
| Сравнение 5 моделей GLM, мерж rerun-ов | ✅ | `scripts/compare_sql_evals.py` |
| Latency: токенный TTFT/TPOT/ITL (reasoning vs answer), нагрузочные 1–10, контекст 1k–32k, prefix-проба | ✅ | `scripts/llm_bench.py` |
| E2E через SSE | ✅ | `scripts/e2e_bench.py` |
| Retrieval-метрики (file-level, 8 пар) | ⚠️ сет не дискриминировал | `tests/test_retrieval_quality.py` |
| CI: pytest на push | ✅ | `.github/workflows/tests.yml` |
| Воспроизводимость: manifest (git, env, hash промпта/датасета, версии) | ✅ | все артефакты |

## 2. Метрики, считающиеся корректно

Execution/Result Accuracy с раздельным учётом инфра-сбоев; Wilson 95% CI;
TTFT (reasoning/answer отдельно); p50/p95/p99 nearest-rank; rps/tps по
фактически завершённым; Recall/MRR file-level (v1). Проверено пересчётом из
details (этап 2, все 7 артефактов сходятся).

## 3. Недостаточная выборка или методика (найдено этим аудитом)

| Проблема | Где | Что сделано |
|---|---|---|
| RAG-набор из 8 пар не дискриминировал режимы (все 1.0) — метрика ничего не измеряла | `tests/test_retrieval_quality.py` | [И] chunk-level v2: 24 кейса (конфузибл-пары, EN, 4 негатива); bm25/vector теперь отличимы от hybrid |
| Retrieval ≠ финальный ответ: faithfulness/citations/abstention не измерялись вообще | — | [И] `scripts/rag_judge.py` (LLM-judge с калибровкой) |
| Ошибки SQL не классифицировались: «неправильный результат» без причин | артефакты | [И] `scripts/error_analysis.py`: таксономия 9 типов по структуре SQL |
| Routing-eval только по 20 вопросам, без source-routing | `scripts/evaluate.py` | помечено ограничением (P2 — расширение) |
| Нет единой точки входа: 6 скриптов с разными конвенциями артефактов | `scripts/` | [И] `scripts/run_eval.py` — оркестратор поверх, run-id + manifest |
| Regression-порогов не существовало | — | [И] `scripts/check_regression.py` (Wilson-гейт) + CI-разделение |

## 4. Сценарии, не покрытые тестами (до этого этапа)

- SQL Guard: функции `load_extension/readfile/writefile`, доступ к
  `sqlite_master` — **не покрывались и реально проходили guard** (P0, закрыто
  [И]: `tests/test_security.py` + паттерн FORBIDDEN);
- prompt injection в вопросе и раскрытие системного промпта — контракт не
  пинен тестом [И закрыто на mock-пути];
- oversized-вход: `ChatRequest.message` не имел `max_length` — [И] 20k chars;
- malformed tool args (non-dict): `dispatch_tool` **крашился TypeError вне
  try** — реальный баг ReAct-цикла [И закрыт + тест];
- большие результаты SQL в сообщение модели [И пин тестом];
- провал провайдера → статус ответа [И пин тестом: status=error, rows=[]).

## 5. Результаты, которые невозможно воспроизвести

- Заявка README «~85%» — устранена в этапе 2 (заменена измеренными числами).
- Latency-таблица README — воспроизводима (независимый SSE-перезамер).
- LLM-прогоны воспроизводимы командой из BENCHMARK.md при живом ключе;
  точные числа невоспроизводимы (внешний API, нестохастический temperature≠0
  режим провайдера) — зафиксировано manifest'ами.

## 6. Измерения, зависящие от внешнего провайдера

Все SQL/RAG-judge/E2E/токенные прогоны (Z.ai): латентность, 429-лимиты,
стабильность thinking-поведения, досрочные обрывы JSON. RAG retrieval-метрики
и pytest — полностью локальны и детерминированы.

## 7. Наиболее важное для production

1. [P0, закрыто] Guard-обход через SQL-функции + краш цикла на malformed args.
2. [P0, частично] Abstention Дока = 0 (не отказывается при отсутствии ответа) —
   системный промпт требует «отвечай ПОДРОБНО»; нужен отказ-режим (P1 задача
   кода, не замерена статистически).
3. [P1] Faithfulness Дока: полный прогон judge (24/24) — relevancy 0.95,
   faithfulness 0.05, abstention 2/4; калибровка judge нестабильна (1/3–3/3
   по прогонам) — количественно недостоверно, направленно устойчиво: промпт
   «ПОДРОБНО» провоцирует уточнения сверх контекста. Прямой риск доверия.
4. [P1] 429 при конкурентности ≥5 на бесплатном endpoint — нужен rate-limiter
   на стороне приложения (сейчас только SDK-ретраи).
5. [P2] temporal-категория SQL (20–33%) — доработка промпта диалекта дат.

## 8. План и приоритеты

| Приоритет | Работа | Файл | Риск | Статус |
|---|---|---|---|---|
| P0 | Guard: запрет опасных функций и sqlite_master | `app/core/sql_guard.py` | RCE/файловый доступ через SQL-функции | ✅ сделано + тесты |
| P0 | dispatch_tool: non-dict args не должен крашить цикл | `app/agents/tools.py` | DoS одного запроса | ✅ сделано + тесты |
| P0 | max_length сообщения (cost/abuse) | `app/schemas/dto.py` | неконтролируемый расход токенов | ✅ сделано + тесты |
| P1 | RAG chunk-level набор + ablation | `tests/golden/rag_retrieval_golden_v2.jsonl`, `scripts/eval_rag.py` | метрика ничего не измеряла | ✅ сделано |
| P1 | LLM-judge генеративных свойств RAG | `scripts/rag_judge.py` | невидимые галлюцинации Дока | ✅ сделано (ограничения см. в скрипте) |
| P1 | Таксономия SQL-ошибок | `scripts/error_analysis.py` | слепая зона «почему неправильно» | ✅ сделано |
| P1 | Единый оркестратор оценки | `scripts/run_eval.py` | расползание артефактов | ✅ сделано |
| P1 | Regression-гейт + CI-разделение | `scripts/check_regression.py`, `.github/workflows/evaluation.yml` | тихая деградация между релизами | ✅ сделано |
| P2 | Отказ-режим Дока (abstention в рантайме, не только замер) | `app/agents/doc.py` | доверие | ⏳ рекомендация |
| P2 | Расширение routing/source-routing набора | `tests/golden/routing_golden.jsonl` | слабая выборка | ⏳ |
| P2 | temporal-промпты Atlas | `app/agents/atlas.py` | качество | ⏳ |
| P2 | GPU-эксперименты | `deploy/vllm/RUNBOOK.md` | вакансия MWS | ⏳ блокер: нет GPU |

Аудит выполнялся на Apple M1 Pro 16GB без NVIDIA GPU и Docker: всё, что
помечено ⏳-GPU, не выполнялось и не заявляется.
