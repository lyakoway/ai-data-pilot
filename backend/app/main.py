"""AI Data Pilot — FastAPI entrypoint."""
from __future__ import annotations

import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import chat, datasources, documents, feedback, meta, scenarios
from app.config import get_settings
from app.core.docs_rag import ensure_docs
from app.db.seed import seed_analytics_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    ensure_docs()
    seed_analytics_db()
    # Initialise the app DB (scenarios, datasources, feedback tables + seed data).
    from app.db.app_db import get_app_engine

    get_app_engine()
    # Register a pre-configured PostgreSQL source if POSTGRES_URL is set.
    from app.db.datasources import register_env_postgres, register_clickhouse_playground

    register_env_postgres()
    # ClickHouse Playground (public demo) — register on startup.
    register_clickhouse_playground()
    yield
    # Flush any pending observability traces before shutdown (no-op when
    # Langfuse is disabled — see app/observability/adapter.py).
    from app.observability import observability_shutdown

    observability_shutdown()


_ORIGIN_RE = re.compile(r"^https?://[A-Za-z0-9.\-]+(:\d+)?$")
_LOCAL_HOST_RE = re.compile(r"https?://(localhost|127\.0\.0\.1)(:\d+)?")


def resolve_cors_origins(settings: Any) -> list[str]:
    """CORS allowlist resolution + production validation (P0.1, gap G1).

    - dev: the configured allowlist as-is (defaults cover the vite proxy);
    - production: the allowlist MUST be explicit and sane — non-empty, no
      wildcard, absolute http(s) origins, no localhost — otherwise the app
      refuses to start with a clear error instead of silently allowing all
      origins (the old `cors_origins + ["*"]` behaviour).
    """
    origins = list(settings.cors_origins)
    if settings.app_env != "production":
        if "*" in origins:
            # Explicit wildcard is rejected in ANY mode: with
            # allow_credentials=True it disables the CORS protection entirely.
            raise RuntimeError(
                "CORS_ORIGINS содержит '*': wildcard запрещён (см. docs/SAAS_ARCHITECTURE.md, G1). "
                "Укажите явный список источников."
            )
        return origins
    problems: list[str] = []
    if not origins:
        problems.append("allowlist пуст (задайте CORS_ORIGINS, JSON-список)")
    for o in origins:
        if o == "*":
            problems.append(f"wildcard '*' в production-allowlist недопустим: {o!r}")
        elif not _ORIGIN_RE.match(o):
            problems.append(f"не является абсолютным http(s)-origin: {o!r}")
        elif _LOCAL_HOST_RE.match(o):
            problems.append(f"localhost/127.0.0.1 в production-allowlist: {o!r}")
    if problems:
        raise RuntimeError(
            "Некорректная production-конфигурация CORS (APP_ENV=production): "
            + "; ".join(problems)
        )
    return origins


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolve_cors_origins(settings),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(chat.router)
    app.include_router(scenarios.router)
    app.include_router(meta.router)
    app.include_router(datasources.router)
    app.include_router(documents.router)
    app.include_router(feedback.router)

    # Optional: serve built frontend from static/ (HF Spaces / single container)
    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app


app = create_app()
