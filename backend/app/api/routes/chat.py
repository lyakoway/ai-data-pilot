from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.agents.doc import run_doc_streaming
from app.agents.atlas import run_atlas_streaming
from app.agents.router import route_agent
from app.observability import mark_status, obs_span, obs_trace, redact, timing_ms
from app.schemas.dto import ChatRequest, ChatResponse
from app.tenancy import TenantContext, require_tenant

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
    who = "Доку (документация)" if agent == "doc" else "Атласу (данные)"
    if not ru:
        who = "Doc (docs)" if agent == "doc" else "Atlas (data)"
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
    body: ChatRequest, agent: str, msg: str, ctx: TenantContext | None = None
) -> tuple[str, dict[str, Any] | None]:
    """Auto-route the data source for Atlas when the user hasn't pinned one.

    Returns ``(datasource_id, router_step)``. Only active when datasource_id
    is 'auto' (or unset) and the agent is Atlas.
    """
    if agent != "atlas":
        return body.datasource_id or "ridego", None
    ds = body.datasource_id
    if ds and ds not in {"", "auto"}:
        # P0.2 ownership: a user-pinned source must belong to the caller's
        # workspace (built-in demo sources are global). Foreign id → honest
        # 404-style refusal, never silent cross-tenant access.
        if ctx is not None:
            from app.db.datasources import get_source_meta as _meta

            if _meta(ds, ctx.workspace_id) is None and ds != "all_uploads":
                return ds, {
                    "id": "step_0_source",
                    "title": "Источник данных",
                    "tool": "source_router",
                    "status": "error",
                    "summary": f"Источник {ds!r} недоступен в этом workspace",
                    "detail": {"decision": ds, "denied": True},
                    "duration_ms": 0,
                }
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


async def _run_agent(body: ChatRequest, msg: str, agent: str, on_step=None, datasource_id=None,
                     workspace_id: str | None = None) -> dict[str, Any]:
    """Run the resolved agent with the request's parameters."""
    from app.agents.doc import _noop_step as _ks_noop
    from app.agents.atlas import _noop_step as _ol_noop

    if agent == "doc":
        return await run_doc_streaming(
            msg, model_id=body.model, lang=body.lang,
            on_step=on_step if on_step is not None else _ks_noop,
            workspace_id=workspace_id,
        )
    return await run_atlas_streaming(
        msg,
        model_id=body.model,
        lang=body.lang,
        force_excel=body.force_excel,
        datasource_id=datasource_id or body.datasource_id or "ridego",
        on_step=on_step if on_step is not None else _ol_noop,
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, ctx: TenantContext = Depends(require_tenant)) -> ChatResponse:
    msg = body.message.strip()
    if not msg:
        raise HTTPException(400, "Empty message")

    t_start = time.perf_counter()
    with obs_trace("chat", metadata={"agent_requested": body.agent, "model": body.model}) as trace:
        with obs_span("agent_router", metadata={"question": redact(msg, capture=True)}) as sp:
            agent, router_step = await _resolve_agent(body, msg)
            sp.set_metadata("decision", agent)
        ds_id = None
        with obs_span("source_router") as sp:
            ds_id, ds_step = await _resolve_datasource(body, agent, msg, ctx)
            sp.set_metadata("decision", ds_id)
        if ds_step is not None and ds_step.get("detail", {}).get("denied"):
            # P0.2: the requested datasource does not belong to this
            # workspace — honest refusal, no fallback, no agent run.
            data = {
                "agent": agent, "status": "error", "warnings": [],
                "steps": [ds_step], "answer": ds_step["summary"],
                "sql": None, "explanation": None, "columns": [], "rows": [],
                "row_count": 0, "chart": None, "excel_url": None,
                "tables_used": [], "insights": {}, "suggestions": [],
            }
        else:
            data = await _run_agent(body, msg, agent, datasource_id=ds_id, workspace_id=ctx.workspace_id)
        if ds_step is not None:
            data.setdefault("steps", []).insert(0, ds_step)
        if router_step is not None:
            data.setdefault("steps", []).insert(0, router_step)
        trace.set_output({
            "status": data.get("status"),
            "agent": data.get("agent"),
            "answer": redact(data.get("answer") or "", capture=True),
        })
        trace.set_metadata({
            "duration_ms": timing_ms(t_start),
            "final_status": data.get("status"),
            "iterations": len(data.get("steps") or []),
            "sql_repairs": sum(1 for s in (data.get("steps") or [])
                               if s.get("tool") == "database_query") - 1 if agent == "atlas" else 0,
        })
        mark_status(trace, data.get("status") != "error", str(data.get("status")))
    return ChatResponse(**data)


def _sse(event: str, payload: dict[str, Any]) -> str:
    """Format a single Server-Sent Events frame. Serialises immediately so later
    mutations of ``payload`` cannot retroactively change an already-yielded frame."""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
async def chat_stream(body: ChatRequest, ctx: TenantContext = Depends(require_tenant)) -> StreamingResponse:
    """Streaming variant of /chat. Both agents stream execution-trace steps and
    finish with a ``done`` event carrying the full ChatResponse."""
    msg = body.message.strip()
    if not msg:
        raise HTTPException(400, "Empty message")

    queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

    async def on_step(step: dict[str, Any]) -> None:
        await queue.put(("step", dict(step)))

    async def runner() -> None:
        t_start = time.perf_counter()
        with obs_trace("chat_stream", metadata={"agent_requested": body.agent, "model": body.model}) as trace:
            try:
                with obs_span("agent_router", metadata={"question": redact(msg, capture=True)}) as sp:
                    agent, router_step = await _resolve_agent(body, msg)
                    sp.set_metadata("decision", agent)
                    if router_step is not None:
                        await queue.put(("step", dict(router_step)))
                with obs_span("source_router") as sp:
                    ds_id, ds_step = await _resolve_datasource(body, agent, msg, ctx)
                    sp.set_metadata("decision", ds_id)
                    if ds_step is not None:
                        await queue.put(("step", dict(ds_step)))
                if ds_step is not None and ds_step.get("detail", {}).get("denied"):
                    data = {
                        "agent": agent, "status": "error", "warnings": [],
                        "steps": [ds_step], "answer": ds_step["summary"],
                        "sql": None, "explanation": None, "columns": [], "rows": [],
                        "row_count": 0, "chart": None, "excel_url": None,
                        "tables_used": [], "insights": {}, "suggestions": [],
                    }
                else:
                    data = await _run_agent(body, msg, agent, on_step=on_step, datasource_id=ds_id, workspace_id=ctx.workspace_id)
                if ds_step is not None:
                    data.setdefault("steps", []).insert(0, ds_step)
                if router_step is not None:
                    data.setdefault("steps", []).insert(0, router_step)
                trace.set_output({"status": data.get("status"), "agent": data.get("agent")})
                trace.set_metadata({
                    "duration_ms": timing_ms(t_start),
                    "final_status": data.get("status"),
                    "iterations": len(data.get("steps") or []),
                })
                mark_status(trace, data.get("status") != "error", str(data.get("status")))
                await queue.put(("done", data))
            except Exception as e:  # noqa: BLE001 — surface any failure to the client
                trace.set_metadata({"final_status": "exception", "duration_ms": timing_ms(t_start)})
                mark_status(trace, False, "exception")
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
