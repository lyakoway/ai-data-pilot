"""Workspace-scoped upload storage (P0.3).

Layout:
    data/uploads/<workspace_id>/<doc_id><ext>   — new, workspace-scoped
    data/uploads/<doc_id><ext>                  — legacy, flat (pre-P0.3)

Path safety is enforced here and nowhere else:
- doc_id must match the upload format (12 hex chars) — no traversal payloads;
- workspace_id must match [A-Za-z0-9_-]+ — comes from validated tenant
  context, but is re-checked at the storage boundary (defense in depth);
- ext must be one of the upload-allowed extensions.

Legacy files are NEVER moved implicitly. Resolution order for read/delete:
workspace-scoped path first, then the flat legacy path — and ONLY after the
caller has passed an ownership check on the document metadata (the route
does that before touching storage). No global fallback exists below this
module: every function requires a workspace_id.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.config import get_settings

_DOC_ID_RE = re.compile(r"^[a-f0-9]{12}$")
_WS_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Must stay in sync with documents.ALLOWED_EXT (single source: storage layer)
ALLOWED_EXT = {".pdf", ".docx", ".doc", ".txt", ".md", ".xlsx", ".xls", ".csv"}


class StoragePathError(ValueError):
    """Raised on any attempt to build a path outside the uploads root."""


def _validate(workspace_id: str, doc_id: str, ext: str) -> tuple[str, str]:
    if not workspace_id or not _WS_RE.match(workspace_id):
        raise StoragePathError(f"invalid workspace id: {workspace_id!r}")
    if not doc_id or not _DOC_ID_RE.match(doc_id):
        raise StoragePathError(f"invalid document id: {doc_id!r}")
    if ext not in ALLOWED_EXT:
        raise StoragePathError(f"invalid file extension: {ext!r}")
    return workspace_id, doc_id


def uploads_root() -> Path:
    root = get_settings().data_dir / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def workspace_upload_dir(workspace_id: str) -> Path:
    if not workspace_id or not _WS_RE.match(workspace_id):
        raise StoragePathError(f"invalid workspace id: {workspace_id!r}")
    d = uploads_root() / workspace_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def scoped_upload_path(workspace_id: str, doc_id: str, ext: str) -> Path:
    """The new-layout path: uploads/<ws>/<doc_id><ext> (validated, no traversal)."""
    ws, did = _validate(workspace_id, doc_id, ext)
    return uploads_root() / ws / f"{did}{ext}"


def legacy_upload_path(doc_id: str, ext: str) -> Path:
    """The pre-P0.3 flat path: uploads/<doc_id><ext>. Only for reads/deletes
    of files created before P0.3 — callers must have verified ownership."""
    _, did = _validate("ws_dummy_ok", doc_id, ext)
    return uploads_root() / f"{did}{ext}"


def resolve_upload_path(workspace_id: str, doc_id: str, ext: str) -> Path | None:
    """Existing file for an OWNERSHIP-VERIFIED document: scoped path first,
    legacy flat path second. Returns None when the file is absent (caller
    reports 404). Raises StoragePathError on unsafe identifiers."""
    scoped = scoped_upload_path(workspace_id, doc_id, ext)
    if scoped.exists():
        return scoped
    legacy = legacy_upload_path(doc_id, ext)
    if legacy.exists():
        return legacy
    return None


def delete_upload_file(workspace_id: str, doc_id: str, ext: str) -> bool:
    """Delete the file of an OWNERSHIP-VERIFIED document (scoped or legacy).
    Returns True when a file was removed."""
    removed = False
    scoped = scoped_upload_path(workspace_id, doc_id, ext)
    if scoped.exists():
        scoped.unlink()
        removed = True
    legacy = legacy_upload_path(doc_id, ext)
    if legacy.exists():
        legacy.unlink()
        removed = True
    return removed


def write_upload(workspace_id: str, doc_id: str, ext: str, data: bytes) -> Path:
    """Persist new upload bytes into the workspace-scoped layout."""
    path = scoped_upload_path(workspace_id, doc_id, ext)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path
