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
from app.tenancy import DEFAULT_WORKSPACE_ID

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


# --------------------------------------------------------------------------- #
# Exports (P1.1) — same module, same rules: path built ONLY from validated
# components; name-as-id (no metadata registry); workspace-scoped layout:
#     data/exports/<workspace_id>/<slug>_<uuid8>.xlsx   — new (P1.1)
#     data/exports/<filename>                           — legacy flat
# Legacy flat exports carry NO owner information anywhere — they are readable
# ONLY by ws_default (the pre-P0.x single tenant was de-facto the default
# workspace). No flat fallback exists for any other workspace.
# --------------------------------------------------------------------------- #

_EXPORT_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,60}\.xlsx$")


def exports_root() -> Path:
    root = get_settings().exports_dir
    root.mkdir(parents=True, exist_ok=True)
    return root


def workspace_export_dir(workspace_id: str) -> Path:
    if not workspace_id or not _WS_RE.match(workspace_id):
        raise StoragePathError(f"invalid workspace id: {workspace_id!r}")
    d = exports_root() / workspace_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_export(workspace_id: str, slug: str) -> Path:
    """Unique workspace-scoped path for a new export (caller saves into it)."""
    if not workspace_id or not _WS_RE.match(workspace_id):
        raise StoragePathError(f"invalid workspace id: {workspace_id!r}")
    safe_slug = re.sub(r"[^\w\-]+", "_", slug.strip(), flags=re.UNICODE).strip("_")[:40] or "report"
    import uuid as _uuid

    return workspace_export_dir(workspace_id) / f"{safe_slug}_{_uuid.uuid4().hex[:8]}.xlsx"


def resolve_export_path(workspace_id: str, filename: str) -> Path | None:
    """Existing export file for an ownership-implied lookup: the file lives in
    the workspace's directory (name-as-id — no foreign file can resolve).
    Legacy flat fallback exists ONLY for ws_default (see module docstring).
    Raises StoragePathError on unsafe filenames (→ HTTP 400)."""
    if not workspace_id or not _WS_RE.match(workspace_id):
        raise StoragePathError(f"invalid workspace id: {workspace_id!r}")
    if not filename or not _EXPORT_NAME_RE.match(filename):
        raise StoragePathError(f"invalid export filename: {filename!r}")
    scoped = exports_root() / workspace_id / filename
    if scoped.exists():
        return scoped
    if workspace_id == DEFAULT_WORKSPACE_ID:
        legacy = exports_root() / filename
        if legacy.exists():
            return legacy
    return None
