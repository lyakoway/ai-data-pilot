"""LLM-as-a-judge evaluation of RAG answer quality (Doc agent).

Measures GENERATIVE properties that Recall/MRR cannot: faithfulness, answer
relevancy, citation correctness and abstention on negative questions.

Protocol (fixed, versioned):
  1. Deterministic pre-checks (no LLM): every [n] citation in the answer must
     reference a provided source index; empty answers flagged.
  2. Each answer is judged by a separate LLM call with a FIXED rubric prompt
     (RUBRIC_VERSION below) that includes 3 calibration examples with known
     expected scores; the judge must reproduce them (agreement reported).
  3. Metrics: faithfulness / relevancy / citation_correctness in [0,1] per
     answer; abstention_correct (did it honestly refuse) for negatives.

Known limitations (stated in every report): the judge and the answering model
come from the SAME provider family (only Z.ai key available) — self-judging
bias; single judge, no inter-annotator stats; n is small. Scores are
directional signals, not ground truth.

Usage (from backend/, requires ZAI_API_KEY; free model by default):
    python scripts/rag_judge.py                       # 24 cases (rag2 set)
    python scripts/rag_judge.py --limit 8             # quick sample
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm_bench import RESULTS_DIR, env_info, git_rev, sha256  # noqa: E402

RUBRIC_VERSION = "rag-judge-rubric-v2"
JUDGE_MODEL = "zai:glm-4.5-flash"
ANSWER_MODEL = "zai:glm-4.5-flash"
GOLDEN = Path(__file__).resolve().parent.parent / "tests" / "golden" / "rag_retrieval_golden_v2.jsonl"

CALIBRATION = """\
Пример 1 (эталон: faithfulness=1, relevancy=1, citations=1):
Q: Какой TTL у кэша тарифов?
CONTEXT: [1] Кэш тарифов в Redis, TTL 15 минут.
A: TTL кэша тарифов — 15 минут [1].
Пример 2 (эталон: faithfulness=0, relevancy=1, citations=0):
Q: Какой TTL у кэша тарифов?
CONTEXT: [1] Кэш тарифов в Redis, TTL 15 минут.
A: TTL кэша тарифов — 20 минут, и он хранится в Mongo [1].   # числа и хранилище противоречат контексту
Пример 3 (эталон: faithfulness=1, relevancy=0, citations=1):
Q: Сколько городов у RideGo?
CONTEXT: [1] Кэш тарифов в Redis, TTL 15 минут.
A: В контексте нет данных о числе городов [1].   # честный отказ: релевантность ответа вопросу = 0
"""

RUBRIC = f"""Ты — строгий оценщик RAG-ответов. {RUBRIC_VERSION}.
Оцени ответ A на вопрос Q строго по CONTEXT. Верни ТОЛЬКО JSON:
{{"faithfulness": 0|1, "relevancy": 0|1, "citations": 0|1, "reason": "<=15 слов"}}
- faithfulness=1: КАЖДОЕ фактическое утверждение A (числа, названия, условия, причинно-следственные связи) буквально подтверждается CONTEXT. Любое правдоподобное уточнение, которого нет в CONTEXT → 0.
- relevancy=1: A содержит запрошенную Q информацию. Честный отказ («в контексте нет данных») → relevancy=0 (faithfulness при этом может быть 1). Отказ оценивается отдельно метрикой abstention, не считай его ошибкой модели.
- citations=1: в A есть ХОТЯ БЫ ОДИН маркер [n], каждый [n] существует в CONTEXT и поддерживает утверждение, рядом с которым стоит. Полное отсутствие [n] в A → 0. Ссылки вида «имя_файла.md#1» вместо [n] → 0.

{CALIBRATION}
Оцени теперь реальный кейс."""


def citations_valid(answer: str, n_sources: int) -> bool:
    cited = {int(m) for m in re.findall(r"\[(\d+)\]", answer)}
    return all(1 <= c <= n_sources for c in cited) if cited else n_sources == 0


async def answer_question(question: str) -> dict[str, Any]:
    from app.agents.doc import run_doc_streaming

    t0 = time.perf_counter()
    res = await run_doc_streaming(question, model_id=ANSWER_MODEL, lang="ru")
    dt = time.perf_counter() - t0
    sources = res.get("sources") or []
    return {
        "answer": res.get("answer") or "",
        "status": res.get("status"),
        "sources": [{"n": i + 1, "doc_id": s.get("doc_id"), "title": s.get("title")} for i, s in enumerate(sources)],
        "latency_s": round(dt, 2),
    }


async def judge(question: str, context: str, answer: str) -> dict[str, Any]:
    from app.llm.base import ChatMessage
    from app.llm.registry import get_provider

    provider = get_provider(JUDGE_MODEL)
    user = f"Q: {question}\nCONTEXT:\n{context}\nA: {answer}"
    raw = await provider.complete(RUBRIC, [ChatMessage("user", user)], lang="ru")
    # take the LAST {...} block: thinking models often echo the rubric first
    blocks = re.findall(r"\{[^{}]*\}", raw or "")
    for block in reversed(blocks):
        try:
            d = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(d.get("faithfulness"), (int, float)):
            return {k: d.get(k) for k in ("faithfulness", "relevancy", "citations", "reason")}
    return {"parse_error": (raw or "")[:160], "raw_blocks": len(blocks)}


async def calibration_check() -> dict[str, Any]:
    """Judge must reproduce the three rubric calibration examples."""
    cases = [
        ("Какой TTL у кэша тарифов?", "[1] Кэш тарифов в Redis, TTL 15 минут.",
         "TTL кэша тарифов — 15 минут [1].", (1, 1, 1)),
        ("Какой TTL у кэша тарифов?", "[1] Кэш тарифов в Redis, TTL 15 минут.",
         "TTL кэша тарифов — 20 минут, и он хранится в Mongo [1].", (0, 1, 0)),
        ("Сколько городов у RideGo?", "[1] Кэш тарифов в Redis, TTL 15 минут.",
         "В контексте нет данных о числе городов [1].", (1, 0, 1)),
    ]
    ok = 0
    details = []
    for q, ctx, a, expected in cases:
        got = await judge(q, ctx, a)
        actual = (got.get("faithfulness"), got.get("relevancy"), got.get("citations"))
        match = actual == expected
        ok += match
        details.append({"expected": expected, "got": actual, "match": match})
    return {"agreement": f"{ok}/{len(cases)}", "details": details}


def context_of(sources: list[dict[str, Any]], chunks: dict[str, str]) -> str:
    return "\n".join(f"[{s['n']}] {s.get('title','')} ({s.get('doc_id','')})\n{chunks.get(s.get('doc_id',''), '')[:600]}"
                     for s in sources)


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    from app.core.docs_rag import load_chunks

    chunks = {c.doc_id: c.text for c in load_chunks()}
    cases = [json.loads(l) for l in GOLDEN.read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.limit:
        # keep a representative mix: every kind, first N overall
        seen: dict[str, int] = {}
        picked = []
        for c in cases:
            k = c["kind"]
            if seen.get(k, 0) < max(1, args.limit // 3):
                picked.append(c)
                seen[k] = seen.get(k, 0) + 1
        cases = picked

    print(f"RAG judge: {len(cases)} cases, answer model {ANSWER_MODEL}, judge {JUDGE_MODEL}")
    cal = await calibration_check()
    print(f"  calibration agreement: {cal['agreement']}")

    rows = []
    infra = 0
    for i, c in enumerate(cases, 1):
        try:
            ans = await answer_question(c["query"])
        except Exception as e:  # noqa: BLE001 — provider timeout/429: record, continue
            infra += 1
            rows.append({"id": c["id"], "kind": c["kind"], "question": c["query"],
                         "infra_error": f"{type(e).__name__}: {str(e)[:120]}"})
            print(f"  [{i:>2}/{len(cases)}] {c['id']} INFRA: {type(e).__name__}", flush=True)
            continue
        ctx = context_of(ans["sources"], chunks)
        deterministic = citations_valid(ans["answer"], len(ans["sources"]))
        neg = not c["expected_chunks"]
        if neg:
            # abstention: honest "нет данных" — judged by relevancy of a refusal
            try:
                j = await judge(c["query"], ctx, ans["answer"])
            except Exception as e:  # noqa: BLE001
                j = {"parse_error": f"{type(e).__name__}"}
            abstain_ok = bool(j.get("relevancy") == 0) or "нет данных" in ans["answer"].lower() or "не нашла" in ans["answer"].lower()
            rows.append({"id": c["id"], "kind": c["kind"], "question": c["query"],
                         "answer": ans["answer"][:300], "n_sources": len(ans["sources"]),
                         "citations_valid": deterministic, "abstention_ok": abstain_ok,
                         "judge": j, "latency_s": ans["latency_s"]})
            print(f"  [{i:>2}/{len(cases)}] {c['id']} NEG: abstain={abstain_ok} cites_valid={deterministic}", flush=True)
        else:
            try:
                j = await judge(c["query"], ctx, ans["answer"])
            except Exception as e:  # noqa: BLE001
                j = {"parse_error": f"{type(e).__name__}", "faithfulness": None,
                     "relevancy": None, "citations": None}
            rows.append({"id": c["id"], "kind": c["kind"], "question": c["query"],
                         "answer": ans["answer"][:300], "n_sources": len(ans["sources"]),
                         "citations_valid": deterministic,
                         "faithfulness": j.get("faithfulness"), "relevancy": j.get("relevancy"),
                         "citations_judged": j.get("citations"), "judge_reason": j.get("reason"),
                         "latency_s": ans["latency_s"]})
            print(f"  [{i:>2}/{len(cases)}] {c['id']}: faith={j.get('faithfulness')} rel={j.get('relevancy')} "
                  f"cit={j.get('citations')} det_cit={deterministic}", flush=True)

    pos = [r for r in rows if not str(r["kind"]).startswith("negative")]
    neg = [r for r in rows if str(r["kind"]).startswith("negative")]

    def mean(key: str) -> Any:
        vals = [r[key] for r in pos if isinstance(r.get(key), (int, float))]
        return round(sum(vals) / len(vals), 3) if vals else None

    summary = {
        "n_cases": len(cases), "n_infra_failed": infra,
        "n_positive": len([r for r in pos if "infra_error" not in r]),
        "n_negative": len([r for r in neg if "infra_error" not in r]),
        "faithfulness": mean("faithfulness"),
        "answer_relevancy": mean("relevancy"),
        "citation_correctness_judged": mean("citations_judged"),
        "citation_validity_deterministic": round(sum(1 for r in pos if r["citations_valid"]) / len(pos), 3) if pos else None,
        "abstention_ok_share": round(sum(1 for r in neg if r.get("abstention_ok")) / len(neg), 3) if neg else None,
        "judge_calibration_agreement": cal["agreement"],
    }
    return {
        "manifest": {
            "tool": "rag_judge.py", "rubric_version": RUBRIC_VERSION,
            "judge_model": JUDGE_MODEL, "answer_model": ANSWER_MODEL,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "git_rev": git_rev(), "env": env_info(),
            "golden_sha256": sha256(GOLDEN.read_text()),
            "limitations": [
                "judge and answer model are the SAME provider family (only Z.ai key available) — self-judging bias",
                "binary judge scores, single judge, no inter-annotator statistics",
                "citation_validity is deterministic (indices exist); citation_correctness_judged is the LLM judgment of support",
            ],
        },
        "calibration": cal,
        "summary": summary,
        "details": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG generative eval (LLM-as-a-judge)")
    parser.add_argument("--limit", type=int, default=None, help="approx sample size (keeps all kinds)")
    args = parser.parse_args()
    payload = asyncio.run(main_async(args))
    s = payload["summary"]
    print("\n" + "=" * 60)
    print(f"RAG JUDGE — positives n={s['n_positive']}, negatives n={s['n_negative']}")
    print(f"  faithfulness            : {s['faithfulness']}")
    print(f"  answer relevancy        : {s['answer_relevancy']}")
    print(f"  citations (judge)       : {s['citation_correctness_judged']}")
    print(f"  citations (deterministic indices-valid): {s['citation_validity_deterministic']}")
    print(f"  abstention ok (negatives): {s['abstention_ok_share']}")
    print(f"  judge calibration        : {s['judge_calibration_agreement']}")
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"rag_judge_{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved → {path.name}")


if __name__ == "__main__":
    main()
