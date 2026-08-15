"""Plain text / Markdown parsing — sections by headings or char budget."""
from __future__ import annotations

import re

from pathlib import Path

from app.parsers.base import PageSegment, ParseError

_PAGE_CHAR_BUDGET = 1800
_HEADING_RE = re.compile(r"^#{1,3}\s+", re.MULTILINE)


def parse(path: Path) -> list[PageSegment]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            text = path.read_text(encoding="cp1251")
        except Exception as exc:
            raise ParseError(f"Не удалось прочитать файл: {exc}") from exc
    text = text.strip()
    if not text:
        raise ParseError("Файл пуст.")

    # Split by markdown headings if present, else by char budget.
    if _HEADING_RE.search(text):
        parts = re.split(r"(?=^#{1,3}\s+)", text, flags=re.MULTILINE)
        segments = []
        for i, part in enumerate(parts, start=1):
            part = part.strip()
            if part:
                heading = part.splitlines()[0].lstrip("# ").strip()
                segments.append(PageSegment(page=i, text=part, label=heading[:80] or None))
        return segments

    # No headings — char-budget pseudo-pages.
    segments = []
    page = 1
    for i in range(0, len(text), _PAGE_CHAR_BUDGET):
        segments.append(PageSegment(page=page, text=text[i : i + _PAGE_CHAR_BUDGET]))
        page += 1
    return segments
