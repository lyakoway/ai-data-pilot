from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.agents.ksyusha import run_ksyusha
from app.agents.oleg import run_oleg, run_oleg_streaming
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
            datasource_id=body.datasource_id or "ridego",
        )
    return ChatResponse(**data)


def _sse(event: str, payload: dict[str, Any]) -> str:
    """Format a single Server-Sent Events frame. Serialises immediately so later
    mutations of ``payload`` cannot retroactively change an already-yielded frame."""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
async def chat_stream(body: ChatRequest) -> StreamingResponse:
    """Streaming variant of /chat for agents that emit execution-trace steps.

    Currently Олег streams ``step`` events as he works, then a final ``done``
    with the full ChatResponse. Ксюша has no steps yet, so she streams a single
    ``done`` event (the UI treats it identically to the non-streaming endpoint).
    """
    msg = body.message.strip()
    if not msg:
        raise HTTPException(400, "Empty message")

    queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

    async def on_step(step: dict[str, Any]) -> None:
        await queue.put(("step", dict(step)))

    async def runner() -> None:
        try:
            if body.agent == "ksyusha":
                data = await run_ksyusha(msg, model_id=body.model, lang=body.lang)
            else:
                data = await run_oleg_streaming(
                    msg,
                    model_id=body.model,
                    lang=body.lang,
                    force_excel=body.force_excel,
                    datasource_id=body.datasource_id or "ridego",
                    on_step=on_step,
                )
            await queue.put(("done", data))
        except Exception as e:  # noqa: BLE001 — surface any failure to the client
            await queue.put(("error", {"message": str(e)}))

    async def event_stream():
        task = asyncio.create_task(runner())
        try:
            while True:
                event, payload = await queue.get()
                yield _sse(event, payload)
                if event in ("done", "error"):
                    break
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable proxy buffering (nginx)
        },
    )
