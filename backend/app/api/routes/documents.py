"""Document endpoints: upload, list, file download, delete."""
from __future__ import annotations

import shutil
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.config import get_settings
from app.db import app_db

router = APIRouter(prefix="/api/documents", tags=["documents"])

ALLOWED_EXT = {".pdf", ".docx", ".doc", ".txt", ".md", ".xlsx", ".xls"}
_MAX_BYTES = 25 * 1024 * 1024


def _upload_dir() -> Path:
    d = get_settings().data_dir / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _chunk_segments(segments, char_size: int = 1500, overlap: int = 200) -> list[dict]:
    """Char-window chunking with overlap; each chunk carries page provenance."""
    chunks: list[dict] = []
    idx = 0
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        if len(text) <= char_size:
            chunks.append({"text": text, "page": seg.page, "label": seg.label, "index": idx})
            idx += 1
            continue
        step = max(char_size - overlap, 1)
        for start in range(0, len(text), step):
            piece = text[start : start + char_size].strip()
            if piece:
                chunks.append({"text": piece, "page": seg.page, "label": seg.label, "index": idx})
                idx += 1
            if start + char_size >= len(text):
                break
    return chunks


@router.post("")
async def upload_document(file: UploadFile = File(...)) -> dict:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Формат {ext or '?'} не поддерживается. Разрешены: PDF, Word, TXT, MD.")

    raw = await file.read()
    if len(raw) > _MAX_BYTES:
        raise HTTPException(413, "File is too large (max 25 MB).")

    doc_id = uuid.uuid4().hex[:12]
    dest = _upload_dir() / f"{doc_id}{ext}"
    dest.write_bytes(raw)

    doc = {
        "id": doc_id,
        "filename": file.filename or "document",
        "content_type": file.content_type or "application/octet-stream",
        "size_bytes": len(raw),
        "page_count": 0,
        "chunk_count": 0,
        "status": "processing",
        "error": None,
        "created_at": datetime.utcnow().isoformat(timespec="seconds"),
    }

    from app.parsers.base import ParseError, parse_document

    try:
        segments = parse_document(dest, doc["content_type"])
        chunks = _chunk_segments(segments)
        app_db.insert_chunks(doc_id, chunks)
        doc["page_count"] = max((s.page for s in segments), default=0)
        doc["chunk_count"] = len(chunks)
        doc["status"] = "ready"
    except ParseError as e:
        doc["status"] = "error"
        doc["error"] = str(e)
        dest.unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001
        doc["status"] = "error"
        doc["error"] = f"Ошибка обработки: {e}"
        dest.unlink(missing_ok=True)

    app_db.save_document(doc)
    return doc


@router.get("")
def list_documents() -> list[dict]:
    return app_db.list_documents()


@router.get("/{document_id}/file")
def get_document_file(document_id: str):
    doc = app_db.get_document(document_id)
    if not doc:
        raise HTTPException(404, "Документ не найден")
    ext = Path(doc["filename"]).suffix.lower()
    path = _upload_dir() / f"{document_id}{ext}"
    if not path.exists():
        raise HTTPException(404, "Файл отсутствует на диске")
    return FileResponse(path, filename=doc["filename"], media_type=doc["content_type"],
                        content_disposition_type="inline")


@router.delete("/{document_id}")
def delete_document(document_id: str) -> dict:
    doc = app_db.get_document(document_id)
    if not doc:
        raise HTTPException(404, "Документ не найден")
    ext = Path(doc["filename"]).suffix.lower()
    path = _upload_dir() / f"{document_id}{ext}"
    path.unlink(missing_ok=True)
    app_db.delete_document_row(document_id)
    return {"ok": True}
