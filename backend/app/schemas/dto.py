from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    agent: Literal["oleg", "ksyusha"] = "oleg"
    model: str = "mock"
    lang: Literal["ru", "en"] = "ru"
    force_excel: bool = False
    datasource_id: str | None = None


class ScenarioCreate(BaseModel):
    name: str
    agent: Literal["oleg", "ksyusha"] = "oleg"
    description: str = ""
    prompt: str
    chart_type: str | None = None
    datasource_id: str | None = None


class ScenarioOut(BaseModel):
    id: str
    name: str
    agent: str
    description: str
    prompt: str
    chart_type: str | None = None
    datasource_id: str | None = None


class ChatResponse(BaseModel):
    agent: str
    answer: str
    # ok = real model + query executed; demo = offline deterministic mode;
    # partial = real model but degraded (e.g. self-correction kicked in);
    # error = query could not be executed.
    status: Literal["ok", "demo", "partial", "error"] = "ok"
    warnings: list[str] = []
    insights: dict[str, Any] = {}
    sql: str | None = None
    explanation: str | None = None
    columns: list[str] = []
    rows: list[list[Any]] = []
    row_count: int = 0
    chart: dict[str, Any] | None = None
    excel_url: str | None = None
    tables_used: list[str] = []
    sources: list[dict[str, Any]] = []
    suggestions: list[str] = []
