"""AI Data Pilot — FastAPI entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import chat, datasources, meta, scenarios
from app.config import get_settings
from app.core.docs_rag import ensure_docs
from app.db.seed import seed_analytics_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    ensure_docs()
    seed_analytics_db()
    # Ensure scenarios file exists
    from app.api.routes.scenarios import _load

    _load()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins + ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(chat.router)
    app.include_router(scenarios.router)
    app.include_router(meta.router)
    app.include_router(datasources.router)

    # Optional: serve built frontend from static/ (HF Spaces / single container)
    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app


app = create_app()
