# INFERENCE BENCHMARK — локальный инференс

Статус: **выполнен минимальный CPU-эксперимент llama.cpp; GPU-эксперименты
(vLLM, квантование, prefix caching, speculative decoding) подготовлены и НЕ
запускались** — на машине аудита нет NVIDIA GPU и Docker (Apple M1 Pro 16 GB,
macOS 13.6; vLLM требует CUDA). Stage 8-проверка (2026-11-09) подтвердила
блокер и зафиксировала путь разблокировки:
[INFERENCE_BENCHMARK_GPU.md](INFERENCE_BENCHMARK_GPU.md). Результаты внешнего API Z.ai к этому
документу не относятся и не являются метриками собственного инференса.

## 1. Выполнено: llama.cpp на CPU (2026-10-22)

- Модель: llama3.2:3b, Q4_K_M (1.87 GiB) — из локального ollama-store, без
  скачивания. Ревизия весов не пинится (blob sha256-dde5aa3f…).
- Runtime: homebrew llama.cpp 0.5.0 (ggml 0.25.3), 8 потоков, `-ngl 0`.
- **Metal-путь сломан** на этой конфигурации: `GGML_ASSERT(buf_dst)` в
  `ggml_metal_get_tensor_async` — воспроизводится и в ollama llama-server,
  и в homebrew llama-bench. Все числа ниже — CPU-only.
- Артефакт: `backend/bench_results/llama_cpp_local_20261022.json`.

| Тест (3 повтора) | tok/s |
|---|---|
| prefill 512 | 73.3 ± 3.3 |
| prefill 2048 | 51.4 ± 3.1 |
| generate 128 | 6.2 ± 3.3 |
| generate 256 | 3.9 ± 2.6 |

Производные: TTFT(512)≈7.0 s, TTFT(2048)≈39.8 s, TTFT(6069)≈118 s;
TPOT 162–254 ms/token. Вывод: CPU-only в ~20–30× медленнее хостед-эндпоинта
по TPOT — для E2E приложения непригоден; починка Metal — предусловие
локальных MLX/llama.cpp эксперимиментов на этой машине.

## 2. Подготовлено, НЕ выполнено: vLLM на GPU

Полный runbook: [`deploy/vllm/RUNBOOK.md`](../deploy/vllm/RUNBOOK.md)
(pinned `vllm/vllm-openai:v0.6.6`, healthcheck, веса вне образа, K8s-манифест
— не применялся; smoke-тест; `nvidia-smi dmon`; все замеры готовым
`llm_bench.py --base-url` без изменения кода).

Матрица экспериментов (порядок строгий, один фактор за раз):

1. **Baseline BF16** → tokens/load/ctx на своём endpoint + Result Accuracy
   на golden v1+v2 (`eval_sql_full.py` с `--base-url`).
2. **Квантование**: FP8 (`--quantization fp8`, Ada/Hopper) и GPTQ-Int4 той же
   модели. Критерий успеха — деградация Result Accuracy в пределах Wilson CI
   baseline при измеримом выигрыше VRAM/TPOT (не «меньше памяти = успех»).
3. **Prefix caching**: A/B `--enable-prefix-caching` + cache-hit из лога vLLM
   (схема Atlas 2–6k токенов — идеальный кандидат), затем отдельно
   **speculative decoding** (`--speculative-model Qwen2.5-0.5B`, контроль
   качества на golden set).

Сравнение «vLLM 7B vs GLM API» НЕ является сравнением рантаймов (разные
модели); чистое сравнение требует одинаковых весей/входов/параметров.

## 3. Заблокировано

- NVIDIA GPU / CUDA / Docker — отсутствуют на машине аудита.
- Скачивание больших весов — вне политики аудита (единственная локальная
  модель уже была на диске).
- K8s-проверка манифеста — требует кластера.
