"""P0.3 workspace-scoped upload storage tests.

Proves: own files readable/deletable; foreign files refused (and untouched);
paths are workspace-scoped; path traversal impossible; no global fallback;
legacy files (flat layout) resolve only through ownership; metadata+file
deleted together; the migration script is idempotent and leaves orphans alone.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import storage
from app.db import app_db
from app.tenancy import DEFAULT_WORKSPACE_ID

WS_A = "ws_alpha"
WS_B = "ws_beta"


def _mk_doc(doc_id: str, ws: str, filename: str = "note.md", text: str = "hello") -> Path:
    app_db.save_document(
        {"id": doc_id, "filename": filename, "content_type": "text/markdown",
         "size_bytes": len(text), "page_count": 1, "chunk_count": 1,
         "status": "ready", "error": None, "datasource_id": None,
         "created_at": "2026-11-08 00:00:00"},
        workspace_id=ws,
    )
    return storage.write_upload(ws, doc_id, Path(filename).suffix.lower(), text.encode())


@pytest.fixture()
def two_ws(tmp_db):
    from sqlalchemy import text

    eng = app_db.get_app_engine()
    with eng.begin() as conn:
        for ws, name in ((WS_A, "Alpha"), (WS_B, "Beta")):
            conn.execute(
                text("INSERT OR IGNORE INTO workspaces (id, name, created_at) VALUES (:id, :n, :now)"),
                {"id": ws, "n": name, "now": "2026-11-08 00:00:00"},
            )
    yield


def _client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app())


# --------------------------------------------------------------------------- #
# Path layer: scoping + traversal
# --------------------------------------------------------------------------- #

def test_upload_path_is_workspace_scoped(two_ws):
    p = storage.scoped_upload_path(WS_A, "abcdef012345", ".md")
    assert p.parent.name == WS_A
    assert p.parent.parent.name == "uploads"


def test_path_traversal_rejected_in_every_component(two_ws):
    for bad_ws in ("../evil", "a/b", "..", ""):
        with pytest.raises(storage.StoragePathError):
            storage.scoped_upload_path(bad_ws, "abcdef012345", ".md")
    for bad_id in ("../../etc/passwd", "abc", "ABCDEF012345", "../x", ""):
        with pytest.raises(storage.StoragePathError):
            storage.scoped_upload_path(WS_A, bad_id, ".md")
    for bad_ext in (".sh", ".exe", "", ".py"):
        with pytest.raises(storage.StoragePathError):
            storage.scoped_upload_path(WS_A, "abcdef012345", bad_ext)


def test_no_global_fallback_for_unknown_file(two_ws):
    # a doc in metadata but no file anywhere → None, never a foreign file
    app_db.save_document(
        {"id": "aaaaaaaaaaaa", "filename": "gone.md", "content_type": "text/markdown",
         "size_bytes": 1, "page_count": 0, "chunk_count": 0, "status": "ready",
         "error": None, "datasource_id": None, "created_at": "2026-11-08 00:00:00"},
        workspace_id=WS_A,
    )
    assert storage.resolve_upload_path(WS_A, "aaaaaaaaaaaa", ".md") is None


# --------------------------------------------------------------------------- #
# HTTP: ownership + file access
# --------------------------------------------------------------------------- #

def test_own_file_readable(two_ws):
    path = _mk_doc("abcdef012345", WS_A, text="ALPHA_CONTENT")
    client = _client()
    r = client.get(f"/api/documents/abcdef012345/file", headers={"X-Workspace-Id": WS_A})
    assert r.status_code == 200
    assert b"ALPHA_CONTENT" in r.content


def test_foreign_file_not_readable_and_untouched(two_ws):
    path = _mk_doc("abcdef012346", WS_A, text="ALPHA_SECRET")
    client = _client()
    r = client.get("/api/documents/abcdef012346/file", headers={"X-Workspace-Id": WS_B})
    assert r.status_code == 404
    assert "ALPHA_SECRET" not in r.text.encode("utf-8", errors="ignore").decode("utf-8", "ignore") or r.status_code == 404
    assert path.exists() and path.read_text() == "ALPHA_SECRET"  # untouched


def test_foreign_file_not_deletable(two_ws):
    path = _mk_doc("abcdef012347", WS_A, text="KEEP_ME")
    client = _client()
    r = client.delete("/api/documents/abcdef012347", headers={"X-Workspace-Id": WS_B})
    assert r.status_code == 404
    assert path.exists() and path.read_text() == "KEEP_ME"      # file intact
    assert app_db.get_document("abcdef012347", WS_A) is not None  # metadata intact


def test_own_delete_removes_metadata_and_file(two_ws):
    path = _mk_doc("abcdef012348", WS_A, text="BYE")
    assert path.exists()
    client = _client()
    r = client.delete("/api/documents/abcdef012348", headers={"X-Workspace-Id": WS_A})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert not path.exists()                                       # file gone
    assert app_db.get_document("abcdef012348", WS_A) is None       # metadata gone


def test_http_traversal_id_gets_404_not_storage_access(two_ws):
    client = _client()
    r = client.get("/api/documents/..%2F..%2Fetc%2Fpasswd/file", headers={"X-Workspace-Id": WS_A})
    assert r.status_code in (404, 400)  # rejected before any path build


# --------------------------------------------------------------------------- #
# Legacy (flat) files
# --------------------------------------------------------------------------- #

def test_legacy_file_with_known_workspace_readable_by_owner(two_ws):
    doc_id = "abcdef012349"
    app_db.save_document(
        {"id": doc_id, "filename": "old.md", "content_type": "text/markdown",
         "size_bytes": 4, "page_count": 1, "chunk_count": 0, "status": "ready",
         "error": None, "datasource_id": None, "created_at": "2026-11-08 00:00:00"},
        workspace_id=WS_A,
    )
    legacy = storage.uploads_root() / f"{doc_id}.md"
    legacy.write_text("LEGACY_OK")
    try:
        client = _client()
        r = client.get(f"/api/documents/{doc_id}/file", headers={"X-Workspace-Id": WS_A})
        assert r.status_code == 200 and b"LEGACY_OK" in r.content
        # foreign workspace still cannot read it (metadata ownership first)
        r2 = client.get(f"/api/documents/{doc_id}/file", headers={"X-Workspace-Id": WS_B})
        assert r2.status_code == 404
    finally:
        legacy.unlink(missing_ok=True)


def test_legacy_file_unknown_workspace_not_readable_via_any_ws(two_ws):
    """No metadata row → no ownership → no access from ANY workspace."""
    orphan = storage.uploads_root() / "abcdef012350.md"
    orphan.write_text("ORPHAN")
    try:
        client = _client()
        for ws in (WS_A, WS_B, DEFAULT_WORKSPACE_ID):
            r = client.get("/api/documents/abcdef012350/file", headers={"X-Workspace-Id": ws})
            assert r.status_code == 404, ws
    finally:
        orphan.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Upload writes into the scoped layout
# --------------------------------------------------------------------------- #

def test_new_upload_lands_in_workspace_dir(two_ws):
    client = _client()
    r = client.post(
        "/api/documents",
        files={"file": ("report.md", b"# new upload", "text/markdown")},
        headers={"X-Workspace-Id": WS_A},
    )
    assert r.status_code == 200
    doc = r.json()
    assert doc["status"] == "ready"
    scoped = storage.uploads_root() / WS_A / f"{doc['id']}.md"
    assert scoped.exists(), "new uploads must land in uploads/<ws>/"
    flat = storage.uploads_root() / f"{doc['id']}.md"
    assert not flat.exists()


# --------------------------------------------------------------------------- #
# Migration script: idempotent, orphans untouched
# --------------------------------------------------------------------------- #

def test_migration_script_moves_and_is_idempotent(two_ws, capsys):
    from scripts.migrate_uploads import main as migrate_main
    import sys as _sys

    doc_id = "abcdef012351"
    app_db.save_document(
        {"id": doc_id, "filename": "m.md", "content_type": "text/markdown",
         "size_bytes": 3, "page_count": 1, "chunk_count": 0, "status": "ready",
         "error": None, "datasource_id": None, "created_at": "2026-11-08 00:00:00"},
        workspace_id=WS_A,
    )
    flat = storage.uploads_root() / f"{doc_id}.md"
    flat.write_text("MIGRATE_ME")
    orphan = storage.uploads_root() / "ffffffffffff.md"
    orphan.write_text("ORPHAN")

    orig_argv = _sys.argv
    try:
        _sys.argv = ["migrate", "--apply"]
        migrate_main()          # first run: moves
        assert not flat.exists()
        assert (storage.uploads_root() / WS_A / f"{doc_id}.md").read_text() == "MIGRATE_ME"
        assert orphan.exists()  # orphan untouched
        _sys.argv = ["migrate", "--apply"]
        migrate_main()          # second run: idempotent
        assert (storage.uploads_root() / WS_A / f"{doc_id}.md").read_text() == "MIGRATE_ME"
        out = capsys.readouterr().out
        assert "untouched-orphans=1" in out
    finally:
        _sys.argv = orig_argv
        orphan.unlink(missing_ok=True)
        (storage.uploads_root() / WS_A / f"{doc_id}.md").unlink(missing_ok=True)
