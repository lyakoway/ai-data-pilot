#!/bin/bash
# Sequential SQL quality evals across all 5 GLM models (50 golden cases each).
# Usage: bash scripts/run_sql_evals.sh   (from backend/)
set -u
cd "$(dirname "$0")/.."
PY=.venv/bin/python
for m in zai:glm-4.6 zai:glm-5.2 zai:glm-5.3-flash zai:glm-4.5-flash zai:glm-5.3; do
  echo "=================================================="
  echo "=== SQL EVAL: $m"
  echo "=================================================="
  $PY scripts/eval_sql_full.py --model "$m" || echo "EVAL FAILED: $m"
done
echo "ALL SQL EVALS DONE"
