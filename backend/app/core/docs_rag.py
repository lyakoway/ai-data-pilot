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


_TOKEN = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9_./-]{2,}")


def _tokenize(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN.findall(text)}


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
    q = _tokenize(query)
    if not q:
        return []
    scored: list[DocChunk] = []
    for ch in load_chunks():
        tokens = _tokenize(ch.title + " " + ch.text)
        overlap = len(q & tokens)
        if overlap == 0:
            continue
        # Boost title hits
        title_hit = len(q & _tokenize(ch.title))
        score = overlap + title_hit * 2
        scored.append(DocChunk(ch.doc_id, ch.title, ch.text, float(score)))
    scored.sort(key=lambda c: c.score, reverse=True)
    top = scored[:top_k]
    # Normalise scores to 0..1 relative to the strongest match so the UI can
    # show a relevance percentage. The best fragment is always 1.0.
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
