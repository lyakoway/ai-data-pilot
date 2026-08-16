"""Feedback API — persist votes + analytics panel data."""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.db import app_db
from app.schemas.dto import FeedbackCreate, FeedbackOut

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


@router.post("", response_model=FeedbackOut)
def submit_feedback(body: FeedbackCreate) -> FeedbackOut:
    saved = app_db.save_feedback(body.model_dump())
    return FeedbackOut(id=saved["id"], ok=True)


@router.get("/stats")
def feedback_summary() -> dict:
    """Aggregate: total + per-agent + satisfaction rate."""
    return app_db.feedback_stats()


@router.get("/list")
def feedback_entries(
    limit: int = Query(20, ge=1, le=100),
    agent: str | None = Query(None),
) -> list[dict]:
    """Recent feedback for the analytics panel."""
    return app_db.feedback_list(limit=limit, agent=agent)
