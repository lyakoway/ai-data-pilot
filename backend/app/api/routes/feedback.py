"""Feedback API — persist votes + analytics panel data (tenant-scoped)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.db import app_db
from app.schemas.dto import FeedbackCreate, FeedbackOut
from app.tenancy import TenantContext, require_tenant

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


@router.post("", response_model=FeedbackOut)
def submit_feedback(body: FeedbackCreate, ctx: TenantContext = Depends(require_tenant)) -> FeedbackOut:
    saved = app_db.save_feedback(body.model_dump(), ctx.workspace_id)
    return FeedbackOut(id=saved["id"], ok=True)


@router.get("/stats")
def feedback_summary(ctx: TenantContext = Depends(require_tenant)) -> dict:
    """Aggregate: total + per-agent + satisfaction rate (workspace only)."""
    return app_db.feedback_stats(ctx.workspace_id)


@router.get("/list")
def feedback_entries(
    limit: int = Query(20, ge=1, le=100),
    agent: str | None = Query(None),
    ctx: TenantContext = Depends(require_tenant),
) -> list[dict]:
    """Recent feedback for the analytics panel (workspace only)."""
    return app_db.feedback_list(limit=limit, agent=agent, workspace_id=ctx.workspace_id)
