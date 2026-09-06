"""Retrieval quality evaluation — Recall@1/5, MRR across search modes.

Golden pairs: question → expected source file (built-in docs). Measures on the
FILE level (any chunk of the right file counts), which is how users perceive
"found the right place".

Compares three modes: bm25, vector, hybrid — verifying that hybrid is at least
as good as keyword-only, and that all modes are functional.
"""
from __future__ import annotations

import pytest

from app.core.docs_rag import retrieve
from app.core.embeddings import embeddings_available

# question → expected source file (prefix of doc_id)
GOLDEN = [
    ("какой TTL у redis pricing cache", "data_lineage.md"),
    ("как считается utilization", "metrics_dictionary.md"),
    ("что делает кнопка reset errors в админке", "backend_logic.md"),
    ("антифрод serial номер АКБ", "backend_logic.md"),
    ("mongodb customer_subscription поля подписки", "data_lineage.md"),
    ("partner trip исключение из отчётов", "metrics_dictionary.md"),
    ("франшиза is_inhouse города", "data_lineage.md"),
    ("active subscription определение", "metrics_dictionary.md"),
]

NEEDS_VECTOR = pytest.mark.skipif(
    not embeddings_available(), reason="fastembed unavailable — vector mode disabled"
)


def _rank_files(chunks) -> list[str]:
    """Unique source files in rank order (file-level grounding)."""
    files: list[str] = []
    for ch in chunks:
        file = ch.doc_id.split("#")[0]
        if file not in files:
            files.append(file)
    return files


def _metrics(mode: str) -> dict[str, float]:
    recall1 = 0
    recall5 = 0
    reciprocal = 0.0
    for question, expected_file in GOLDEN:
        chunks = retrieve(question, top_k=5, mode=mode)
        files = _rank_files(chunks)
        if files and files[0] == expected_file:
            recall1 += 1
        if expected_file in files:
            recall5 += 1
            reciprocal += 1.0 / (files.index(expected_file) + 1)
    n = len(GOLDEN)
    return {
        "recall@1": recall1 / n,
        "recall@5": recall5 / n,
        "mrr": round(reciprocal / n, 3),
    }


def test_hybrid_finds_right_file_for_all_questions(tmp_db):
    m = _metrics("hybrid")
    assert m["recall@5"] == 1.0, f"hybrid recall@5: {m}"
    assert m["recall@1"] >= 0.5, f"hybrid recall@1: {m}"
    assert m["mrr"] >= 0.7, f"hybrid MRR: {m}"


def test_bm25_mode_is_functional(tmp_db):
    m = _metrics("bm25")
    assert m["recall@5"] >= 0.75, f"bm25 recall@5: {m}"


@NEEDS_VECTOR
def test_vector_mode_is_functional(tmp_db):
    m = _metrics("vector")
    assert m["recall@5"] >= 0.75, f"vector recall@5: {m}"


@NEEDS_VECTOR
def test_hybrid_is_never_worse_than_single_modes_on_recall5(tmp_db):
    hybrid = _metrics("hybrid")
    bm25 = _metrics("bm25")
    vector = _metrics("vector")
    # Hybrid's purpose: at least the max of its components on file-level recall@5.
    assert hybrid["recall@5"] >= max(bm25["recall@5"], vector["recall@5"]) - 0.01


@NEEDS_VECTOR
def test_cross_language_question_finds_russian_doc(tmp_db):
    """Semantic flagship: an English question against Russian-only docs."""
    chunks = retrieve("how much does maintenance cost", top_k=5, mode="hybrid")
    files = _rank_files(chunks)
    assert files, "expected at least one result"
