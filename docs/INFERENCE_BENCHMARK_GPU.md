# INFERENCE BENCHMARK GPU — СТАТУС: ЗАБЛОКИРОВАНО (Stage 8, 2026-11-09)

**Ни одного GPU-измерения не проводилось. В этом документе нет и не будет
цифр производительности/качества, пока эксперимент не выполнен фактически.**
Все существующие LLM-цифры проекта — client-observed замеры внешнего API
Z.ai (docs/BENCHMARK_RESULTS.md) и НЕ характеризуют собственный inference
runtime.

## Проверка доступности (выполнена 2026-11-09)

| Проверка | Результат |
|---|---|
| NVIDIA GPU (nvidia-smi) | **нет** — машина Apple M1 Pro (Metal 3), vLLM требует CUDA |
| Docker | **нет** — контейнер vLLM запустить не на чём |
| CUDA toolkit (/usr/local/cuda) | **нет** |
| `pip install vllm` в venv | **падает на сборке** (без CUDA), версия в PyPI на момент проверки: 0.33.0 |
| Облачные креды (AWS/GCP/Azure/RunPod/Vast/Lambda) | **в окружении отсутствуют** — аренду выполнить нечем и не от чьего имени |
| HF-токен | есть (веса скачать можно — но запускать не на чем) |
| llama.cpp Metal (локальный fallback) | сломан на этой конфигурации — `GGML_ASSERT(buf_dst)`, зафиксировано в INFERENCE_BENCHMARK.md с этапа 1 |

## Блокер

У агента нет ни локального CUDA-GPU, ни учётных записей/платёжных
инструментов для аренды. Аренда от имени пользователя требует его действий
(аккаунт RunPod/Vast/Lambda, оплата) — самовольно тратить деньги
пользователя нельзя.

## Что полностью готово к запуску (не требует доработок)

1. `deploy/vllm/RUNBOOK.md` — пошагово: compose (pinned vLLM), smoke-тест,
   `nvidia-smi dmon`, все замеры готовым harness:
   `python scripts/llm_bench.py tokens|load|ctx --base-url http://<gpu-host>:8000/v1`
2. Качество на СВОЁМ golden set: `scripts/eval_sql_full.py --model <локальная
   модель>` — Result Accuracy BF16 vs INT4 vs API-baseline (65.9%), критерий
   деградации в пределах Wilson CI.
3. Критерии успеха и запреты (одно изменение за раз; «меньше памяти ≠ успех»)
   — RUNBOOK §4–5.

## Разблокировка (≈2–4 ч машинного времени + ~$1–4 аренды)

Пользователь выполняет: аккаунт на RunPod/Vast.ai/Lambda → инстанс
1×A10G 24GB / L4 24GB (или H100 при бюджете) → на инстансе:

```bash
git clone https://github.com/lyakoway/ai-data-pilot && cd ai-data-pilot
cd deploy/vllm && cp .env.example .env  # указать модель
docker compose up -d && docker compose ps   # ждать healthy
# затем РАЗДЕЛЬНО, одно изменение за раз (RUNBOOK §4–5):
# 1) BF16 baseline: llm_bench tokens/load/ctx --base-url …
# 2) качество: eval_sql_full --model …  (golden v1, 50 кейсов)
# 3) AWQ/GPTQ INT4-чекпойнт той же модели → повтор 1–2
# 4) prefix caching A/B → speculative decoding (отдельно)
```

Результаты пишет сам harness в `bench_results/` с manifest; после прогона —
заполнить таблицы ниже и убрать этот статус-блок.

## Шаблон результатов (ЗАПОЛНИТЬ ПОСЛЕ ФАКТИЧЕСКОГО ПРОГОНА)

| Конфигурация | Load, s | VRAM | TTFT p50 | TPOT | tok/s | c=1/4/8 p95 | Result Acc (v1) |
|---|---|---|---|---|---|---|---|
| BF16 (…) | — | — | — | — | — | — | — |
| INT4 AWQ/GPTQ (…) | — | — | — | — | — | — | — |

API-baseline для сравнения качества (НЕ производительности — другой стек):
GLM-4.5-flash 65.9% (Wilson 51.1–78.1), docs/BENCHMARK_RESULTS.md.
