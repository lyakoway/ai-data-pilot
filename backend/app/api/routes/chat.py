from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.agents.ksyusha import run_ksyusha
from app.agents.oleg import run_oleg
from app.schemas.dto import ChatRequest, ChatResponse

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest) -> ChatResponse:
    msg = body.message.strip()
    if not msg:
        raise HTTPException(400, "Empty message")

    if body.agent == "ksyusha":
        data = await run_ksyusha(msg, model_id=body.model, lang=body.lang)
    else:
        data = await run_oleg(
            msg,
            model_id=body.model,
            lang=body.lang,
            force_excel=body.force_excel,
        )
    return ChatResponse(**data)
