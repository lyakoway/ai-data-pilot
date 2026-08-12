"""Data sources API: list, upload CSV, delete."""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.db import datasources

router = APIRouter(prefix="/api/datasources", tags=["datasources"])

# Accept CSV plus a few spreadsheet-like text MIME types browsers may send.
_ACCEPTED = {".csv"}
_MAX_BYTES = 25 * 1024 * 1024  # 25 MB cap on uploaded CSVs


@router.get("")
def list_datasources() -> list[dict]:
    return datasources.list_sources()


@router.post("/upload")
async def upload_csv(file: UploadFile = File(...)) -> dict:
    if not file.filename or not file.filename.lower().endswith(tuple(_ACCEPTED)):
        raise HTTPException(400, "Only .csv files are accepted.")
    raw = await file.read()
    if len(raw) > _MAX_BYTES:
        raise HTTPException(413, "CSV is too large (max 25 MB).")
    try:
        text = raw.decode("utf-8-sig")  # utf-8-sig tolerates a BOM
    except UnicodeDecodeError:
        try:
            text = raw.decode("cp1251")  # common for RU Excel exports
        except UnicodeDecodeError as e:
            raise HTTPException(400, "CSV must be UTF-8 or CP1251 encoded.") from e
    try:
        meta = datasources.ingest_csv(file.filename, text)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {
        "id": meta["id"],
        "name": meta["name"],
        "kind": meta["kind"],
        "description": meta["description"],
        "row_count": meta["row_count"],
        "columns": meta["columns"],
        "created_at": meta["created_at"],
    }


@router.delete("/{source_id}")
def delete_datasource(source_id: str) -> dict:
    try:
        datasources.delete_source(source_id)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}
