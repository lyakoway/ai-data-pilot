"""RAG retrieval evaluation — chunk-level metrics + mode ablation.

Deterministic (no LLM): measures the RETRIEVAL layer only. Generative RAG
properties (faithfulness, citations, abstention) are measured separately by
`rag_judge.py` — Recall/MRR do not prove final-answer correctness.

Golden set: tests/golden/rag_retrieval_golden_v2.jsonl (chunk-level, with
confusable distractor pairs, EN queries and negative cases).

Metrics (per mode: bm25 / vector / hybrid) over positive cases:
  Recall@1/5/10 — gold chunk present in top-k
  Precision@5   — |gold ∩ top5| / 5   (penalizes distractor pollution)
  MRR@10        — 1 / rank of first gold chunk
  nDCG@10       — binary-relevance DCG/IDCG (multi-gold aware)
Negative cases have no gold chunk: excluded from the metrics above and
reported separately as top-1 chunk (the generation layer must abstain on
them — checked by rag_judge.py).

Usage (from backend/):
    python scripts/eval_rag.py                     # v2 chunk-level set
    python scripts/eval_rag.py --top-k 5,10
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm_bench import RESULTS_DIR, env_info, git_rev, sha256  # noqa: E402

GOLDEN = Path(__file__).resolve().parent.parent / "tests" / "golden" / "rag_retrieval_golden_v2.jsonl"
MODES = ("bm25", "vector", "hybrid")


def load_cases() -> list[dict[str, Any]]:
    return [json.loads(l) for l in GOLDEN.read_text(encoding="utf-8").splitlines() if l.strip()]


def recall_at_k(ranked: list[str], gold: set[str], k: int) -> float:
    return 1.0 if gold & set(ranked[:k]) else 0.0


def precision_at_k(ranked: list[str], gold: set[str], k: int) -> float:
    return len(gold & set(ranked[:k])) / k


def rr_at_k(ranked: list[str], gold: set[str], k: int) -> float:
    for i, cid in enumerate(ranked[:k], start=1):
        if cid in gold:
            return 1.0 / i
    return 0.0


def ndcg_at_k(ranked: list[str], gold: set[str], k: int) -> float:
    dcg = sum(1.0 / math.log2(i + 1) for i, cid in enumerate(ranked[:k], start=1) if cid in gold)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(gold), k) + 1))
    return dcg / idcg if idcg else 0.0


def eval_mode(mode: str, cases: list[dict[str, Any]], ks: list[int]) -> dict[str, Any]:
    from app.core.docs_rag import retrieve

    pos = [c for c in cases if c["expected_chunks"]]
    negatives = [c for c in cases if not c["expected_chunks"]]
    rows: list[dict[str, Any]] = []
    for c in pos:
        gold = set(c["expected_chunks"])
        ranked = [ch.doc_id for ch in retrieve(c["query"], top_k=max(ks), mode=mode)]
        row: dict[str, Any] = {"id": c["id"], "kind": c["kind"]}
        for k in ks:
            row[f"recall@{k}"] = recall_at_k(ranked, gold, k)
            row[f"precision@{k}"] = precision_at_k(ranked, gold, k)
        row["mrr@10"] = rr_at_k(ranked, gold, 10)
        row["ndcg@10"] = ndcg_at_k(ranked, gold, 10)
        row["top1"] = ranked[0] if ranked else None
        row["gold_hit"] = sorted(gold & set(ranked))
        rows.append(row)

    summary: dict[str, Any] = {"mode": mode, "n_positive": len(pos)}
    for k in ks:
        summary[f"recall@{k}"] = round(sum(r[f"recall@{k}"] for r in rows) / len(rows), 3)
        summary[f"precision@{k}"] = round(sum(r[f"precision@{k}"] for r in rows) / len(rows), 3)
    summary["mrr@10"] = round(sum(r["mrr@10"] for r in rows) / len(rows), 3)
    summary["ndcg@10"] = round(sum(r["ndcg@10"] for r in rows) / len(rows), 3)
    for kind in ("positive", "confusable", "positive_en"):
        sub = [r for r in rows if r["kind"] == kind]
        if sub:
            summary[f"recall@5[{kind}]"] = round(sum(r["recall@5"] for r in sub) / len(sub), 3)

    neg_rows = []
    for c in negatives:
        ranked = [ch.doc_id for ch in retrieve(c["query"], top_k=1, mode=mode)]
        neg_rows.append({"id": c["id"], "kind": c["kind"], "top1": ranked[0] if ranked else None})
    return {"summary": summary, "details": rows, "negatives_top1": neg_rows}


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG retrieval eval (chunk-level, deterministic)")
    parser.add_argument("--top-k", default="1,5,10")
    args = parser.parse_args()
    ks = [int(x) for x in args.top_k.split(",")]

    cases = load_cases()
    results = {m: eval_mode(m, cases, ks) for m in MODES}

    print(f"\nRAG retrieval — chunk-level, {len(cases)} cases "
          f"({sum(1 for c in cases if c['expected_chunks'])} positive), golden sha {sha256(GOLDEN.read_text())}")
    hdr = f"{'mode':<8}" + "".join(f"{m:>14}" for m in MODES)
    keys = [f"recall@{k}" for k in ks] + [f"precision@{k}" for k in ks] + ["mrr@10", "ndcg@10",
           "recall@5[positive]", "recall@5[confusable]", "recall@5[positive_en]"]
    print(hdr)
    for key in keys:
        line = f"{key:<8}"[:14] if False else f"{key:<22}"
        for m in MODES:
            line += f"{results[m]['summary'].get(key, '—'):>10}"
        print(line)

    manifest = {
        "tool": "eval_rag.py",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "git_rev": git_rev(),
        "env": env_info(),
        "golden_set": {"path": str(GOLDEN.name), "n_cases": len(cases),
                       "sha256": sha256(GOLDEN.read_text())},
        "grounding": "chunk-level (doc_id exact match); negatives have no gold → "
                     "excluded from metrics, top-1 reported for the abstention check",
        "limitations": [
            "корпус из 10 чанков: Precision@5 ограничен сверху (максимум 0.2 при одном gold-чанке)",
            "retrieval-метрики НЕ доказывают корректность финального ответа (см. rag_judge.py)",
        ],
    }
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    json_path = RESULTS_DIR / f"rag_retrieval_v2_{stamp}.json"
    json_path.write_text(json.dumps({"manifest": manifest, "results": results},
                                    ensure_ascii=False, indent=2), encoding="utf-8")
    csv_path = RESULTS_DIR / f"rag_retrieval_v2_{stamp}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        fields = ["mode"] + list(results["hybrid"]["details"][0].keys())
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for m in MODES:
            for r in results[m]["details"]:
                r = dict(r, mode=m)
                w.writerow(r)
    print(f"\nSaved → {json_path.name}\n       → {csv_path.name}")


if __name__ == "__main__":
    main()
