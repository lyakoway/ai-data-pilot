from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy import text

from app.config import get_settings
from app.tenancy import TenantContext, require_tenant
from app.core.sql_guard import analytics_engine
from app.db.seed import seed_analytics_db
from app.llm.registry import list_models

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/health")
def health() -> dict:
    return {"ok": True, "app": get_settings().app_name}


@router.get("/models")
def models() -> list[dict]:
    return list_models()


@router.get("/agents")
def agents() -> list[dict]:
    return [
        {
            "id": "atlas",
            "name": "Аналитик Атлас",
            "name_en": "Analyst Atlas",
            "role": "SQL · метрики · Excel",
            "description": "Ходит в аналитическую БД, строит SQL, таблицы и графики.",
        },
        {
            "id": "doc",
            "name": "Док",
            "name_en": "Doc",
            "role": "Документация · схема · backend",
            "description": "Отвечает по фейковой внутренней док-базе (метрики, lineage, логика).",
        },
    ]


@router.get("/postgres-demo")
def postgres_demo() -> dict:
    """Detect the bundled demo PostgreSQL so the frontend can prefill a
    working connection: 5432 inside the app container, 5433 for the local
    docker-compose test stack. Returns ``port: null`` when none is running."""
    try:
        import psycopg2
    except ImportError:  # pragma: no cover - psycopg2 ships with requirements
        return {"host": None, "port": None}
    for port in (5432, 5433):
        try:
            conn = psycopg2.connect(
                host="127.0.0.1", port=port, dbname="shop",
                user="demo", password="demo", connect_timeout=2,
            )
            conn.close()
            return {"host": "127.0.0.1", "port": port}
        except Exception:
            continue
    return {"host": None, "port": None}


@router.get("/dashboard/kpis")
def dashboard_kpis() -> dict:
    seed_analytics_db()
    engine = analytics_engine()
    with engine.connect() as conn:
        rides = conn.execute(
            text("SELECT COUNT(*) FROM fact_rides WHERE is_partner = 0")
        ).scalar()
        revenue = conn.execute(
            text("SELECT ROUND(SUM(revenue_rub), 0) FROM fact_rides WHERE is_partner = 0")
        ).scalar()
        users = conn.execute(text("SELECT COUNT(*) FROM dim_user")).scalar()
        cities = conn.execute(
            text("SELECT COUNT(*) FROM dim_city WHERE is_inhouse = 1")
        ).scalar()
        top = conn.execute(
            text(
                "SELECT c.city_name, COUNT(*) AS n "
                "FROM fact_rides r JOIN dim_city c ON c.city_id = r.city_id "
                "WHERE r.is_partner = 0 "
                "GROUP BY c.city_name ORDER BY n DESC LIMIT 5"
            )
        ).fetchall()
        by_region = conn.execute(
            text(
                "SELECT c.region, ROUND(SUM(r.revenue_rub), 0) AS revenue "
                "FROM fact_rides r JOIN dim_city c ON c.city_id = r.city_id "
                "WHERE r.is_partner = 0 "
                "GROUP BY c.region ORDER BY revenue DESC"
            )
        ).fetchall()
    return {
        "rides": int(rides or 0),
        "revenue_rub": int(revenue or 0),
        "users": int(users or 0),
        "inhouse_cities": int(cities or 0),
        "top_cities": [{"city": r[0], "rides": int(r[1])} for r in top],
        "revenue_by_region": [{"region": r[0], "revenue": int(r[1])} for r in by_region],
    }


@router.get("/exports/{filename}")
def get_export(filename: str, ctx: TenantContext = Depends(require_tenant)):
    """Workspace-scoped export download (P1.1, ADR-008): the file must live in
    the CALLER's exports directory — a foreign workspace's file can never
    resolve (name-as-id), so the refusal happens before any filesystem access.
    Legacy flat exports resolve only for the default workspace."""
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    from app.core.storage import StoragePathError, resolve_export_path
    from app.observability import obs_span

    try:
        path = resolve_export_path(ctx.workspace_id, filename)
    except StoragePathError as e:
        # unsafe filename (traversal/extension) — rejected at the storage
        # boundary, before any path is constructed or opened
        raise HTTPException(400, "Invalid export filename") from e
    if path is None:
        with obs_span("export.not_found") as sp:
            sp.set_metadata({"workspace_id": ctx.workspace_id, "filename": filename[:80],
                             "reason": "not_in_workspace"})
        raise HTTPException(404, "File not found")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )
