from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.agents.ksyusha import run_ksyusha_streaming
from app.agents.oleg import run_oleg_streaming
from app.agents.router import route_agent
from app.schemas.dto import ChatRequest, ChatResponse

router = APIRouter(prefix="/api", tags=["chat"])


async def _resolve_agent(body: ChatRequest, msg: str) -> tuple[str, dict[str, Any] | None]:
    """Resolve the effective agent for a request.

    Returns ``(agent_id, router_step)``. ``router_step`` is set only when the
    auto-router made the decision — callers prepend it to the answer's steps
    (and stream it first) so the user sees who took the question.
    """
    if body.agent != "auto":
        return body.agent, None
    t0 = time.perf_counter()
    agent = await route_agent(msg, model_id=body.model)
    ru = body.lang != "en"
    who = "Ксюше (документация)" if agent == "ksyusha" else "Олегу (данные)"
    if not ru:
        who = "Ksyusha (docs)" if agent == "ksyusha" else "Oleg (data)"
    step = {
        "id": "step_0_route",
        "title": "Определяю агента" if ru else "Routing the question",
        "tool": "router",
        "status": "done",
        "summary": f"→ {who}",
        "detail": {"decision": agent},
        "duration_ms": int((time.perf_counter() - t0) * 1000),
    }
    return agent, step


async def _resolve_datasource(
    body: ChatRequest, agent: str, msg: str
) -> tuple[str, dict[str, Any] | None]:
    """Auto-route the data source for Oleg when the user hasn't pinned one.

    Returns ``(datasource_id, router_step)``. Only active when datasource_id
    is 'auto' (or unset) and the agent is Oleg.
    """
    if agent != "oleg":
        return body.datasource_id or "ridego", None
    ds = body.datasource_id
    if ds and ds not in {"", "auto"}:
        return ds, None  # user pinned a source manually

    from app.agents.source_router import route_source

    t0 = time.perf_counter()
    source_id = await route_source(msg, model_id=body.model)
    ru = body.lang != "en"
    from app.db.datasources import get_source_meta

    meta = None
    try:
        meta = get_source_meta(source_id)
    except Exception:  # noqa: BLE001
        pass
    src_name = (meta["name"] if meta else source_id)
    step = {
        "id": "step_0_source",
        "title": "Выбираю источник данных" if ru else "Picking data source",
        "tool": "source_router",
        "status": "done",
        "summary": f"→ {src_name}",
        "detail": {"decision": source_id},
        "duration_ms": int((time.perf_counter() - t0) * 1000),
    }
    return source_id, step


async def _run_agent(body: ChatRequest, msg: str, agent: str, on_step=None, datasource_id=None) -> dict[str, Any]:
    """Run the resolved agent with the request's parameters."""
    from app.agents.ksyusha import _noop_step as _ks_noop
    from app.agents.oleg import _noop_step as _ol_noop

    if agent == "ksyusha":
        return await run_ksyusha_streaming(
            msg, model_id=body.model, lang=body.lang,
            on_step=on_step if on_step is not None else _ks_noop,
        )
    return await run_oleg_streaming(
        msg,
        model_id=body.model,
        lang=body.lang,
        force_excel=body.force_excel,
        datasource_id=datasource_id or body.datasource_id or "ridego",
        on_step=on_step if on_step is not None else _ol_noop,
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest) -> ChatResponse:
    msg = body.message.strip()
    if not msg:
        raise HTTPException(400, "Empty message")

    agent, router_step = await _resolve_agent(body, msg)
    ds_id, ds_step = await _resolve_datasource(body, agent, msg)
    data = await _run_agent(body, msg, agent, datasource_id=ds_id)
    if ds_step is not None:
        data.setdefault("steps", []).insert(0, ds_step)
    if router_step is not None:
        data.setdefault("steps", []).insert(0, router_step)
    return ChatResponse(**data)


def _sse(event: str, payload: dict[str, Any]) -> str:
    """Format a single Server-Sent Events frame. Serialises immediately so later
    mutations of ``payload`` cannot retroactively change an already-yielded frame."""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
async def chat_stream(body: ChatRequest) -> StreamingResponse:
    """Streaming variant of /chat. Both agents stream execution-trace steps and
    finish with a ``done`` event carrying the full ChatResponse."""
    msg = body.message.strip()
    if not msg:
        raise HTTPException(400, "Empty message")

    queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

    async def on_step(step: dict[str, Any]) -> None:
        await queue.put(("step", dict(step)))

    async def runner() -> None:
        try:
            agent, router_step = await _resolve_agent(body, msg)
            if router_step is not None:
                await queue.put(("step", dict(router_step)))
            ds_id, ds_step = await _resolve_datasource(body, agent, msg)
            if ds_step is not None:
                await queue.put(("step", dict(ds_step)))
            data = await _run_agent(body, msg, agent, on_step=on_step, datasource_id=ds_id)
            if ds_step is not None:
                data.setdefault("steps", []).insert(0, ds_step)
            if router_step is not None:
                data.setdefault("steps", []).insert(0, router_step)
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
