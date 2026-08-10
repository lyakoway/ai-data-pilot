from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.agents.ksyusha import run_ksyusha
from app.agents.oleg import run_oleg
from app.config import get_settings
from app.db.schema_catalog import SEED_SCENARIOS
from app.schemas.dto import ChatResponse, ScenarioCreate, ScenarioOut

router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])


def _store_path() -> Path:
    return get_settings().data_dir / "scenarios.json"


def _load() -> list[dict]:
    path = _store_path()
    if not path.exists():
        path.write_text(
            json.dumps(SEED_SCENARIOS, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _save(items: list[dict]) -> None:
    _store_path().write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


@router.get("", response_model=list[ScenarioOut])
def list_scenarios() -> list[ScenarioOut]:
    return [ScenarioOut(**s) for s in _load()]


@router.post("", response_model=ScenarioOut)
def create_scenario(body: ScenarioCreate) -> ScenarioOut:
    items = _load()
    item = {
        "id": uuid.uuid4().hex[:10],
        "name": body.name,
        "agent": body.agent,
        "description": body.description,
        "prompt": body.prompt,
        "chart_type": body.chart_type,
    }
    items.append(item)
    _save(items)
    return ScenarioOut(**item)


@router.delete("/{scenario_id}")
def delete_scenario(scenario_id: str) -> dict:
    items = _load()
    new_items = [s for s in items if s["id"] != scenario_id]
    if len(new_items) == len(items):
        raise HTTPException(404, "Scenario not found")
    _save(new_items)
    return {"ok": True}


@router.post("/{scenario_id}/run", response_model=ChatResponse)
async def run_scenario(scenario_id: str, model: str = "mock", lang: str = "ru") -> ChatResponse:
    items = _load()
    sc = next((s for s in items if s["id"] == scenario_id), None)
    if not sc:
        raise HTTPException(404, "Scenario not found")
    if sc["agent"] == "ksyusha":
        data = await run_ksyusha(sc["prompt"], model_id=model, lang=lang)
    else:
        data = await run_oleg(sc["prompt"], model_id=model, lang=lang, force_excel=True)
    return ChatResponse(**data)
