from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from app.agents.ksyusha import run_ksyusha
from app.agents.oleg import run_oleg
from app.db import app_db
from app.schemas.dto import ChatResponse, ScenarioCreate, ScenarioOut

router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])


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
    }
    app_db.create_scenario(item)
    return ScenarioOut(**item)


@router.delete("/{scenario_id}")
def delete_scenario(scenario_id: str) -> dict:
    if not app_db.delete_scenario(scenario_id):
        raise HTTPException(404, "Scenario not found")
    return {"ok": True}


@router.post("/{scenario_id}/run", response_model=ChatResponse)
async def run_scenario(scenario_id: str, model: str = "mock", lang: str = "ru") -> ChatResponse:
    sc = app_db.get_scenario(scenario_id)
    if not sc:
        raise HTTPException(404, "Scenario not found")
    if sc["agent"] == "ksyusha":
        data = await run_ksyusha(sc["prompt"], model_id=model, lang=lang)
    else:
        datasource_id = sc.get("datasource_id") or "ridego"
        data = await run_oleg(
            sc["prompt"],
            model_id=model,
            lang=lang,
            force_excel=True,
            datasource_id=datasource_id,
        )
    return ChatResponse(**data)
