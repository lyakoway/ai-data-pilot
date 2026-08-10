"""Export query results to Excel."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from app.config import get_settings


def export_rows(columns: list[str], rows: list[list[Any]], name: str = "report") -> Path:
    settings = get_settings()
    settings.exports_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re_slug(name)[:40] or "report"
    path = settings.exports_dir / f"{safe_name}_{uuid.uuid4().hex[:8]}.xlsx"

    wb = Workbook()
    ws = wb.active
    ws.title = "data"
    ws.append(columns)
    for row in rows:
        ws.append(row)
    wb.save(path)
    return path


def re_slug(text: str) -> str:
    import re

    return re.sub(r"[^\w\-]+", "_", text.strip(), flags=re.UNICODE).strip("_") or "report"
