"""Feedback API — persist 👍/👎 votes on agent answers."""
from __future__ import annotations

from fastapi import APIRouter

from app.db import app_db
from app.schemas.dto import FeedbackCreate, FeedbackOut

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


@router.post("", response_model=FeedbackOut)
def submit_feedback(body: FeedbackCreate) -> FeedbackOut:
    saved = app_db.save_feedback(body.model_dump())
    return FeedbackOut(id=saved["id"], ok=True)


@router.get("/stats")
def feedback_summary() -> dict:
    return app_db.feedback_stats()
