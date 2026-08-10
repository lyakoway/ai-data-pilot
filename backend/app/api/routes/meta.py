from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse
from sqlalchemy import text

from app.config import get_settings
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
            "id": "oleg",
            "name": "Аналитик Олег",
            "name_en": "Analyst Oleg",
            "role": "SQL · метрики · Excel",
            "description": "Ходит в аналитическую БД, строит SQL, таблицы и графики.",
        },
        {
            "id": "ksyusha",
            "name": "Ксюша",
            "name_en": "Ksyusha",
            "role": "Документация · схема · backend",
            "description": "Отвечает по фейковой внутренней док-базе (метрики, lineage, логика).",
        },
    ]


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
def get_export(filename: str):
    path = get_settings().exports_dir / filename
    if not path.exists() or ".." in filename or "/" in filename:
        from fastapi import HTTPException

        raise HTTPException(404, "File not found")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )
