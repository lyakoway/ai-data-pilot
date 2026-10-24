# Benchmark methodology — AI Data Pilot

Три уровня измерений, строго разделённые (одно число никогда не выдаётся за другое):

| Уровень | Инструмент | Что меряет |
|---|---|---|
| **Чистый LLM-вызов (внешний API)** | `scripts/llm_bench.py tokens` / `load` | TTFT / TPOT / ITL / токены / throughput на уровне стриминга OpenAI-совместимого API Z.ai |
| **Приложение end-to-end** | `scripts/e2e_bench.py` | latency через реальный SSE-эндпоинт `/api/chat/stream` (полный цикл Atlas) |
| **Качество SQL** | `scripts/eval_sql_full.py` (+ существующий `scripts/evaluate.py`) | Execution Accuracy, Result Accuracy, Exact Match, self-correction на golden set из 50 кейсов |
| **Retrieval (детерминированно)** | `scripts/llm_bench.py retrieval` | Recall@1/3/5, MRR@5 для bm25 / vector / hybrid |

Все результаты сохраняются в `backend/bench_results/*.json` (+ CSV для SQL-оценки)
с manifest'ом: git-ревизия, версия модели (как её отчитывается API), hash промпта
и golden set, параметры генерации, время запуска.

## Важные методологические ограничения (читать до интерпретации чисел)

1. **Внешний API, не свой inference-рантайм.** Все LLM-числа ниже —
   client-observed характеристики хостед-эндпоинта Z.ai
   (`api.z.ai/api/paas/v4`). Никакие утверждения о GPU-утилизации, KV cache
   или серверном батчинге не делаются — нет доступа к серверу.
2. **TPOT/ITL — каденс чанков, наблюдаемый клиентом.** Шлюз провайдера может
   склеивать несколько токенов в один flush; на коротких ответах ответ может
   прийти одним burst'ом. Поэтому TPOT приводится вместе с
   `completion_tok_per_s` (посчитано по usage.completion_tokens от API).
3. **Thinking-модели.** GLM-5.x на Z.ai всегда генерируют `reasoning_content`
   перед ответом (у glm-5.3-flash отключить нельзя, у glm-4.6/4.5-flash можно
   через `thinking: {type: disabled}`). Поэтому TTFT указывается в двух
   вариантах: `ttft_reasoning` (первый reasoning-токен) и `ttft_answer`
   (первый токен собственно ответа; для thinking-моделей включает всю фазу
   размышления). Это же объясняет высокую E2E-latency приложения: каждый вызов
   Atlas (`complete()`, без отключения thinking) оплачивает reasoning-фазу.
4. **`time_to_first_event` в E2E — это не TTFT модели.** Приложение стримит
   по SSE шаги агента (planner → database_query → answer), а не токены.
   Первое SSE-событие ≈ момент, когда агент начал планирование.
5. **Result Accuracy ≠ Exact Match.** Result Accuracy — совпадение множеств
   нормализованных строк (порядок/алиасы/формат чисел не учитываются, float до
   2 знаков) с эталонным запросом. Exact Match — строковое совпадение SQL
   после минимальной нормализации; для Text-to-SQL это сознательно жёсткая и
   малоинформативная метрика (другой алиас = mismatch), приводится для
   полноты.

## Команды воспроизведения (из `backend/`)

```bash
# Токенный уровень: TTFT/TPOT/ITL (5 моделей, thinking on/off)
python scripts/llm_bench.py tokens --models zai:glm-4.6,zai:glm-4.5-flash,zai:glm-5.2,zai:glm-5.3,zai:glm-5.3-flash --runs 3 --scenario short
python scripts/llm_bench.py tokens --models ... --scenario long          # промпт ~2.6k токенов

# Нагрузочный тест (конкурентность 1/2/5/10, 10 запросов на уровень, warm-up 2)
python scripts/llm_bench.py load --model zai:glm-4.5-flash --levels 1,2,5,10 --requests 10
python scripts/llm_bench.py load --model zai:glm-4.5-flash --scenario long

# E2E через реальный SSE-эндпоинт (поднимает uvicorn сам)
python scripts/e2e_bench.py --models mock,zai:glm-4.6,zai:glm-5.2,zai:glm-5.3-flash --runs 3

# Качество SQL на golden set v1 (50 кейсов) / v2 (20 кейсов, temporal/join/groupby/pct)
python scripts/eval_sql_full.py --model zai:glm-4.6
python scripts/eval_sql_full.py --model zai:glm-4.5-flash --golden tests/golden/sql_golden_v2.jsonl
python scripts/eval_sql_full.py --model zai:glm-4.6 --only sql_009,sql_010   # точечный rerun

# Единая сравнительная таблица из артефактов (с mержем rerun-ов и Wilson 95% CI)
python scripts/compare_sql_evals.py --merge

# Контекст-матрица: TTFT vs длина входа (1k/4k/8k/16k/32k)
python scripts/llm_bench.py ctx --model zai:glm-4.5-flash

# Black-box проба prefix caching (одинаковый префикс vs уникальные)
python scripts/llm_bench.py prefix --model zai:glm-4.5-flash

# Retrieval-метрики (без LLM, детерминированно)
python scripts/llm_bench.py retrieval
```

## Уровни измерений — строгое разделение

| Уровень | Инструмент | Что нельзя заявлять |
|---|---|---|
| Внешний API Z.ai (tokens/load/ctx/prefix) | `llm_bench.py --base-url` (по умолчанию Z.ai) | это client-observed метрики ХОСТЕД-эндпоинта: очереди, шлюз-батчинг и серверные решения провайдера не наблюдаемы; это НЕ метрики собственного GPU-инференса |
| Приложение E2E (SSE) | `e2e_bench.py` | `time_to_first_event` — момент первого шага агента, НЕ TTFT модели |
| Собственный runtime (vLLM/ollama) | тот же `llm_bench.py --base-url http://...` | сравнение рантаймов валидно только на одинаковых весях/входах/параметрах |

Проба `prefix` на внешнем API — индикатор совместимости с серверным кэшем,
НЕ доказательство его работы; на своём vLLM доказательством служат cache-hit
в серверном логе (см. deploy/vllm/RUNBOOK.md).

## Формулы

- `ttft_reasoning_s` = t(первый чанк с `delta.reasoning_content`) − t(отправка запроса)
- `ttft_answer_s` = t(первый чанк с `delta.content`) − t(отправка запроса)
- `tpot_chunk_ms` = (total_s − ttft_answer_s) / (n_answer_chunks − 1)
- `itl_ms` = межчанковые интервалы answer-потока (mean, p95)
- `completion_tok_per_s` = usage.completion_tokens / total_s (данные usage от API)
- `throughput_rps` (load) = завершённых без ошибки запросов / wall time
- `throughput_tps` (load) = Σ usage.completion_tokens завершённых / wall time
- `latency p50/p95/p99` — по фактическим завершённым запросам
- `SQL Execution Accuracy` = доля кейсов, где сгенерированный SQL выполнился
- `Result Accuracy` = доля кейсов (из проверяемых), где нормализованное
  множество строк совпало с эталоном
- `Self-Correction Rate` = доля репейр-попыток, после которых SQL выполнился
- `Recall@k / MRR@5` (retrieval) — file-level: правильным считается любой
  чанк ожидаемого файла

## Параметры генерации, зафиксированные в прогонах

- токенный уровень: `temperature=0.1`, `stream=True`, max_tokens:
  short 300 (think_off) / 1000 (think_on); long 700 / 2500
- SQL-eval и E2E: параметры приложения (`app/llm/providers.py`):
  `temperature=0.1` (complete), max_tokens не задаётся (default провайдера),
  thinking не отключается приложением
- во всех случаях промпты и golden set не менялись; их hash — в manifest JSON

## Провайдер и лимиты

- Z.ai (`api.z.ai/api/paas/v4`), ключ из `backend/.env` (`ZAI_API_KEY`).
  Публичных лимитов RPS для тарифа нет; нагрузочный тест намеренно
  консервативен: до 10 конкурентных запросов, 10 запросов на уровень,
  при первом 429 эскалация уровней останавливается.
- Для нагрузочного теста по умолчанию используется `glm-4.5-flash`
  (бесплатная модель) — чтобы не расходовать платный лимит.
