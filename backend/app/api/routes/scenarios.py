from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agents.ksyusha import run_ksyusha
from app.agents.oleg import run_oleg
from app.db import app_db
from app.schemas.dto import ChatResponse, ScenarioCreate, ScenarioOut, ScenarioRunRequest

router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])


class _SafeDict(dict):
    """dict subclass for str.format_map that leaves unknown {placeholders} intact."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _substitute(prompt: str, params: list[dict] | None, values: dict | None) -> str:
    """Replace {name} placeholders in ``prompt`` using declared ``params``.

    For each declared parameter we use the provided value from ``values`` or
    fall back to the parameter's ``default``. Unknown placeholders are left as-is.
    """
    if not params:
        return prompt
    resolved: dict[str, str] = {}
    for p in params:
        name = p.get("name")
        if not name:
            continue
        raw = (values or {}).get(name, p.get("default"))
        resolved[name] = "" if raw is None else str(raw)
    try:
        return prompt.format_map(_SafeDict(resolved))
    except (IndexError, ValueError):
        # Braces that aren't valid format specifiers — return as-is.
        return prompt


@router.get("", response_model=list[ScenarioOut])
def list_scenarios() -> list[ScenarioOut]:
    return [ScenarioOut(**s) for s in app_db.list_scenarios()]


@router.post("", response_model=ScenarioOut)
def create_scenario(body: ScenarioCreate) -> ScenarioOut:
    item = {
        "id": uuid.uuid4().hex[:10],
        "name": body.name,
        "agent": body.agent,
        "description": body.description,
        "prompt": body.prompt,
        "chart_type": body.chart_type,
        "datasource_id": body.datasource_id,
        "parameters": body.parameters,
    }
    app_db.create_scenario(item)
    return ScenarioOut(**item)


@router.delete("/{scenario_id}")
def delete_scenario(scenario_id: str) -> dict:
    if not app_db.delete_scenario(scenario_id):
        raise HTTPException(404, "Scenario not found")
    return {"ok": True}


@router.post("/{scenario_id}/run", response_model=ChatResponse)
async def run_scenario(
    scenario_id: str,
    model: str = "mock",
    lang: str = "ru",
    body: ScenarioRunRequest | None = None,
) -> ChatResponse:
    sc = app_db.get_scenario(scenario_id)
    if not sc:
        raise HTTPException(404, "Scenario not found")
    prompt = _substitute(sc["prompt"], sc.get("parameters"), body.values if body else None)
    if sc["agent"] == "ksyusha":
        data = await run_ksyusha(prompt, model_id=model, lang=lang)
    else:
        datasource_id = sc.get("datasource_id") or "ridego"
        data = await run_oleg(
            prompt,
            model_id=model,
            lang=lang,
            force_excel=True,
            datasource_id=datasource_id,
        )
    return ChatResponse(**data)
