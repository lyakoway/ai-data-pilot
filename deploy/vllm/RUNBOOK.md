# vLLM inference runtime — runbook (ПОДГОТОВЛЕНО, НЕ ВЫПОЛНЕНО)

> **Статус: сценарий подготовлен, но НЕ запускался** — на машине аудита нет
> NVIDIA GPU и Docker (Apple M1 Pro, macOS 13.6; vLLM требует CUDA).
> Ниже — точные команды для воспроизведения на арендованной GPU-машине.
> Все числа в отчётах этапа 1–2 относятся к внешнему API Z.ai и НЕ являются
> метриками собственного inference.

## 0. Требования к машине

- NVIDIA GPU: для 7–9B BF16 нужно ~20 GB VRAM (A10G/L4/3090 минимум,
  A100 40GB комфортно); для FP8-экспериментов — Ada/Hopper (L40S/A100/H100)
  или `--quantization fp8` на поддерживаемых картах.
- CUDA 12.x, Docker + nvidia-container-toolkit, ~50 GB диска под веса.
- Проверка: `nvidia-smi`, `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi`

## 1. Запуск vLLM как OpenAI-совместимого endpoint

```bash
cd deploy/vllm
cp .env.example .env            # указать VLLM_MODEL и (опц.) VLLM_API_KEY
docker compose up -d            # поднимет vLLM, healthcheck на /health
docker compose ps               # ждать healthy (~1-2 мин, загрузка весов)

# smoke-тест после старта (обязателен):
curl -s http://localhost:8000/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"model":"'"$VLLM_MODEL"'","messages":[{"role":"user","content":"ping"}],"max_tokens":8}' | head -c 400
```

Модель по умолчанию в compose: `Qwen/Qwen2.5-7B-Instruct` (та же весовая
категория, что GLM-сабсет приложения; ревизия пинится через
`VLLM_MODEL_REVISION`, дефолт `main` — ЗАФИКСИРОВАТЬ фактический sha при запуске).

## 2. GPU-метрики во время нагрузки

```bash
nvidia-smi dmon -s um -o T -d 5 > gpu_dmon.log &     # util %, memory MB, каждые 5 с
# или раз в секунду: nvidia-smi --query-gpu=timestamp,utilization.gpu,memory.used --csv -l 1
```

## 3. Замеры готовым harness (без изменений кода)

`backend/scripts/llm_bench.py` принимает `--base-url` — валидирован на Z.ai и
локальном ollama:

```bash
cd backend
# токенный уровень: TTFT/TPOT/ITL (стриминг, per-chunk метки времени)
python scripts/llm_bench.py tokens --models local \
  --base-url http://localhost:8000/v1 --api-model "$VLLM_MODEL" --runs 5

# контекст-матрица 1k–32k
python scripts/llm_bench.py ctx --base-url http://localhost:8000/v1 --api-model "$VLLM_MODEL"

# конкурентность 1/2/5/10 (auto-stop на 429; локально 429 не ожидается —
# смотреть деградацию p95 и GPU util)
python scripts/llm_bench.py load --model local \
  --base-url http://localhost:8000/v1 --api-model "$VLLM_MODEL" \
  --levels 1,2,5,10,20 --requests 12

# black-box проба prefix caching (одинаковый префикс vs уникальные)
python scripts/llm_bench.py prefix --base-url http://localhost:8000/v1 --api-model "$VLLM_MODEL"
```

В отличие от внешнего API, здесь доступен серверный лог vLLM: там видны
cache hit rate (включённый `--enable-prefix-caching` в compose) — проба из
harness дополняется прямым доказательством.

## 4. Эксперимент E: квантование с контролем качества

Одна модель, одно железо, МЕНЯЕТСЯ ТОЛЬКО формат весей. Порядок строгий:
baseline → одно изменение → замер → следующее.

| Конфиг | Как запустить | Что фиксировать |
|---|---|---|
| BF16 (baseline) | compose по умолчанию | TTFT/TPOT/tps, VRAM, Result Accuracy v1+v2 |
| FP8 (W8A8-fp8) | `--quantization fp8` в command (Ada/Hopper) | то же + Δ качества |
| GPTQ-Int4 | отдельный GGUF/AWQ-чекпоинт той же модели (`Qwen2.5-7B-Instruct-GPTQ-Int4`), ревизию зафиксировать | то же |

Качество — на ОБОИХ golden set:

```bash
python scripts/eval_sql_full.py --model ollama:qwen2.5-7b ... # при запуске через приложение
# либо прямой прогон плана через локальный endpoint (см. eval_sql_full --model local --base-url ...)
```

Критерий успеха квантования: деградация Result Accuracy в пределах CI Уилсона
baseline (например, 65.9% ±13 п.п. на v1) при измеримом выигрыше в VRAM/TPOT.
«Занимает меньше памяти» ≠ «успешно».

## 5. Эксперимент F: prefix caching и speculative decoding

1. **Prefix caching** (сначала): compose флаг `--enable-prefix-caching` уже
   включён; сравнить A/B: выключить флаг → прогон `prefix` + `tokens`; включить
   → тот же прогон. Смотреть TTFT на повторном префиксе (схема БД Atlas —
   2–6k токенов, одинаковая между запросами — идеальный кандидат) + cache hit
   rate в логе vLLM.
2. **Speculative decoding** (после, отдельно): добавить в command
   `--speculative-model Qwen/Qwen2.5-0.5B-Instruct --num-speculative-tokens 5`.
   Сравнить TPOT/total; корректность — контрольный прогон golden set (результаты
   должны совпасть с baseline; распределённые различия = баг).

## 6. Ограничения честно

- Сравнение «vLLM 7B vs GLM-4.6 API» — НЕ сравнение рантаймов (разные модели).
  Чистое сравнение рантаймов требует одинаковых весей (например, GGUF в
  llama.cpp vs safetensors в vLLM) — отдельный эксперимент.
- K8s-манифест (`k8s-deployment.yaml`) НЕ применялся и не проверялся.
- FP8 на картах ниже Ada не запустится — проверять совместимость до прогона.
