"""Lightweight keyword RAG over fake internal docs for agent Ksyusha."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings


@dataclass
class DocChunk:
    doc_id: str
    title: str
    text: str
    score: float = 0.0
    page: int = 1


_TOKEN = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9_./-]{2,}")

# Common Russian inflection endings to strip for crude stemming.
_RU_SUFFIXES = [
    "иями", "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими", "их", "ых",
    "ая", "яя", "ое", "ее", "ые", "ие", "ой", "ей", "ый", "ий", "ом", "ем",
    "ам", "ям", "ах", "ях", "ов", "ев", "ей", "ий", "ью", "ия", "ии",
    "у", "ю", "а", "я", "о", "е", "ы", "и", "й", "ь",
]


def _stem(token: str) -> str:
    """Crude Russian stemmer: strip common inflection endings (longest first)."""
    if len(token) <= 3:
        return token
    for suffix in _RU_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)]
    return token


def _tokenize(text: str) -> set[str]:
    raw = {t.lower() for t in _TOKEN.findall(text)}
    # Return both raw and stemmed forms so exact and inflected matches both work.
    return raw | {_stem(t) for t in raw}


def load_chunks() -> list[DocChunk]:
    docs_dir = get_settings().docs_dir
    chunks: list[DocChunk] = []
    for path in sorted(docs_dir.glob("**/*")):
        if path.suffix.lower() not in {".md", ".txt"}:
            continue
        raw = path.read_text(encoding="utf-8")
        title = path.stem.replace("_", " ")
        # Split by ## headers or paragraphs
        parts = re.split(r"\n(?=## )", raw)
        for i, part in enumerate(parts):
            part = part.strip()
            if len(part) < 40:
                continue
            heading = part.splitlines()[0].lstrip("# ").strip() if part.startswith("#") else title
            chunks.append(
                DocChunk(
                    doc_id=f"{path.name}#{i}",
                    title=heading[:120],
                    text=part[:1800],
                )
            )
    return chunks


def retrieve(query: str, top_k: int = 4) -> list[DocChunk]:
    """Search uploaded documents (BM25-IDF) + built-in docs, merged ranked."""
    q = _tokenize(query)
    if not q:
        return []

    all_scored: list[DocChunk] = []

    # --- Uploaded documents (BM25-IDF scoring) ---
    try:
        from app.db import app_db
        uploaded = app_db.get_chunks_for_search()
        if uploaded:
            # Compute IDF per query token across all chunks.
            df: dict[str, int] = {}
            chunk_tokens: list[set[str]] = []
            for ch in uploaded:
                toks = _tokenize(ch["filename"] + " " + ch["text"])
                chunk_tokens.append(toks)
                for t in q:
                    if t in toks:
                        df[t] = df.get(t, 0) + 1
            N = len(uploaded)
            for ch, toks in zip(uploaded, chunk_tokens):
                score = 0.0
                for t in q:
                    if t in toks:
                        idf = 1.0 + (N / (df.get(t, 1)))
                        score += idf
                if score > 0:
                    label = ch.get("label") or f"{ch['filename']} стр. {ch['page']}"
                    all_scored.append(DocChunk(
                        doc_id=f"{ch['document_id']}#{ch['chunk_index']}",
                        title=label,
                        text=ch["text"],
                        score=score * 2.0,  # uploaded docs get a relevance boost
                        page=ch.get("page", 1),
                    ))
    except Exception:  # noqa: BLE001 — uploaded search is additive, never blocks
        pass

    # --- Built-in docs (existing keyword search) ---
    for ch in load_chunks():
        tokens = _tokenize(ch.title + " " + ch.text)
        overlap = len(q & tokens)
        if overlap == 0:
            continue
        title_hit = len(q & _tokenize(ch.title))
        score = overlap + title_hit * 2
        all_scored.append(DocChunk(ch.doc_id, ch.title, ch.text, float(score)))

    all_scored.sort(key=lambda c: c.score, reverse=True)
    top = all_scored[:top_k]
    # Normalise scores to 0..1.
    if top:
        max_score = top[0].score or 1.0
        for ch in top:
            ch.score = round(ch.score / max_score, 3)
    return top


def ensure_docs() -> None:
    """Write default fake docs if the folder is empty."""
    docs_dir = get_settings().docs_dir
    docs_dir.mkdir(parents=True, exist_ok=True)
    if any(docs_dir.glob("*.md")):
        return

    (docs_dir / "metrics_dictionary.md").write_text(
        """# Словарь метрик RideGo

## Utilization
Utilization (утилизация) — доля активного флота.
Формула: `sh_active / sh_available`.
В витрине аналитики сырые поля не хранятся как utilization;
для демо-дашборда можно аппроксимировать спрос как
`rides_count / population * 1000`.

Источник истины в проде (пример): `iceberg.bi.ss_wide_rlp_total`.

## Active subscription user
Пользователь с `fact_subscriptions.status = 'active'` на дату отчёта.

## Partner trip
Поездка с `fact_rides.is_partner = 1`. В стандартных отчётах исключается.
""",
        encoding="utf-8",
    )

    (docs_dir / "data_lineage.md").write_text(
        """# Data lineage и хранилища

## fact_rides
Инкрементальная витрина поездок. Источник: `rides.api` → Kafka → DWH.
Поля `distance_km`, `revenue_rub`, `duration_min` считаются на этапе ETL.

## dim_city
Справочник городов. `is_inhouse = 1` — собственные города RideGo,
`0` — франшиза. Франшизные города обычно исключают из unit-экономики HQ.

## MongoDB customer_subscription
Операционный документ подписки (не аналитическая витрина).
Поля: `brand`, `status`, `startedAt`, `cancelledAt`, `cancelReason`.
Аналитика читает уже нормализованную `fact_subscriptions`.

## Redis pricing cache
Кэш тарифов в Redis, TTL 15 минут.
Ключ: `pricing:{cityId}:{vehicleType}`.
При изменении цены в админке ключ инвалидируется; до инвалидации
могут отдаваться старые диапазоны.
""",
        encoding="utf-8",
    )

    (docs_dir / "backend_logic.md").write_text(
        """# Backend logic (фейковая документация для Ксюши)

## Reset errors в админке ТС
Кнопка «Сбросить ошибки» вызывает `TransportCardService.ResetCriticalErrors`.
Критические флаги сбрасываются только если SIM-доступ активен.
Если SIM disabled — API возвращает 409 Conflict.

## Anti-fraud battery serial
Сервис `opsactions.api`, файл `AntifraudBatterySerialNumberService.cs`.
Суть: не даём накручивать счётчик замен АКБ одним и тем же serial.
Используется Redis TTL whitelist `SkippableSerialNumber`.

## PositionCodes
Справочник кодов позиций ТС в `location.api`.
Коды 1–10 — штатные, 11+ — сервисные/ремонт.
При переводе города в inactive поле location не очищается,
но перестаёт попадать в публичный geo-feed.
""",
        encoding="utf-8",
    )


def docs_root() -> Path:
    return get_settings().docs_dir
