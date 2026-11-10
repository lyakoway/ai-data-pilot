"""Export query results to Excel."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from app.config import get_settings


def export_rows(columns: list[str], rows: list[Any], name: str = "report",
                workspace_id: str | None = None) -> Path:
    """Write an xlsx export into the WORKSPACE-scoped exports directory
    (P1.1). Library callers may omit the workspace — the dev default applies
    (same pattern as the agents); production always passes one."""
    from app.core.storage import write_export
    from app.tenancy import DEFAULT_WORKSPACE_ID

    path = write_export(workspace_id or DEFAULT_WORKSPACE_ID, name)

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
