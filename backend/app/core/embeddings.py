"""Local vector embeddings via fastembed (ONNX, no API key, multilingual).

Uses paraphrase-multilingual-MiniLM-L12-v2 (384 dim, ~50 languages, 0.22GB).
Model downloads on first use and is cached in ~/.cache/fastembed/.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import numpy as np

_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# Lazy singleton — the model loads once and stays in memory.
_model = None
_available: bool | None = None


def _get_model():
    global _model, _available
    if _model is None:
        try:
            from fastembed import TextEmbedding
            _model = TextEmbedding(_MODEL_NAME)
            _available = True
        except Exception:  # noqa: BLE001 — fastembed not installed or model unavailable
            _available = False
    return _model


def embeddings_available() -> bool:
    """Check if the embedding model can be loaded (without loading it)."""
    if _available is not None:
        return _available
    try:
        from importlib.util import find_spec
        return find_spec("fastembed") is not None
    except Exception:  # noqa: BLE001
        return False


def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """Embed a batch of texts. Returns None if the model is unavailable."""
    model = _get_model()
    if model is None:
        return None
    try:
        vectors = [np.array(e).tolist() for e in model.embed(texts)]
        return vectors
    except Exception:  # noqa: BLE001
        return None


def embed_query(text: str) -> list[float] | None:
    """Embed a single query string."""
    result = embed_texts([text])
    return result[0] if result else None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    va, vb = np.array(a), np.array(b)
    norm = np.linalg.norm(va) * np.linalg.norm(vb)
    if norm == 0:
        return 0.0
    return float(np.dot(va, vb) / norm)
