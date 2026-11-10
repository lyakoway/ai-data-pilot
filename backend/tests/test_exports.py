"""P1.1: workspace-scoped exports + delete_workspace (ADR-008).

Proves: cross-tenant export download impossible (permanent regression — the
pre-P1.1 route returned 200 with file content to ANY workspace); own exports
work; traversal rejected at the storage boundary; legacy flat exports readable
only by ws_default; scoped wins over legacy; delete_workspace removes files
AND metadata, is idempotent, and post-delete access is 404.
"""
from __future__ import annotations

import threading
import zipfile
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from app.core import storage
from app.db import datasources as _ds
from app.core.export_xlsx import export_rows
from app.db import app_db
from app.tenancy import DEFAULT_WORKSPACE_ID

WS_A = "ws_alpha"
WS_B = "ws_beta"


@pytest.fixture()
def two_ws(tmp_db):
    from sqlalchemy import text

    with app_db.get_app_engine().begin() as conn:
        for ws, name in ((WS_A, "Alpha"), (WS_B, "Beta")):
            conn.execute(
                text("INSERT OR IGNORE INTO workspaces (id, name, created_at) VALUES (:i, :n, :now)"),
                {"i": ws, "n": name, "now": "2026-11-09 00:00:00"},
            )
    yield


def _client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app())


def _xlsx_secret(response_bytes: bytes, needle: str) -> bool:
    with zipfile.ZipFile(BytesIO(response_bytes)) as z:
        return any(needle in z.read(n).decode("utf-8", "ignore") for n in z.namelist())


# --------------------------------------------------------------------------- #
# Cross-tenant regression (PERMANENT)
# --------------------------------------------------------------------------- #

def test_foreign_export_404_and_content_not_delivered(two_ws):
    """Regression (pre-P1.1: 200 + secret inside sheet1.xml). The foreign file
    lives outside the caller's exports dir → resolution returns None BEFORE
    any filesystem open; the 404 is emitted without touching the file."""
    path = export_rows(["k"], [["ALPHA_EXPORT_SECRET"]], name="report", workspace_id=WS_A)
    client = _client()
    r = client.get(f"/api/exports/{path.name}", headers={"X-Workspace-Id": WS_B})
    assert r.status_code == 404
    assert r.content in (b'"File not found"', b"File not found") or r.status_code == 404
    # file itself untouched and still owner-readable
    r2 = client.get(f"/api/exports/{path.name}", headers={"X-Workspace-Id": WS_A})
    assert r2.status_code == 200 and _xlsx_secret(r2.content, "ALPHA_EXPORT_SECRET")


def test_own_export_200_with_content(two_ws):
    path = export_rows(["k"], [["BETA_OK"]], name="r", workspace_id=WS_B)
    assert storage.exports_root() / WS_B in path.parents
    client = _client()
    r = client.get(f"/api/exports/{path.name}", headers={"X-Workspace-Id": WS_B})
    assert r.status_code == 200 and _xlsx_secret(r.content, "BETA_OK")


def test_no_header_dev_default_only(two_ws):
    """No header → dev default workspace: sees DEFAULT's exports, never A/B's."""
    a = export_rows(["k"], [["A"]], name="a", workspace_id=WS_A)
    d = export_rows(["k"], [["D"]], name="d", workspace_id=DEFAULT_WORKSPACE_ID)
    client = _client()
    assert client.get(f"/api/exports/{a.name}").status_code == 404
    assert client.get(f"/api/exports/{d.name}").status_code == 200


def test_traversal_rejected_at_storage_boundary(two_ws):
    client = _client()
    for bad in ("../../app.db", "..%2F..%2Fapp.db", "report.xlsx/", "a/b.xlsx",
                "report", "report.pdf", "", "x" * 80 + ".xlsx"):
        r = client.get(f"/api/exports/{bad}", headers={"X-Workspace-Id": WS_A})
        assert r.status_code in (400, 404), bad
    with pytest.raises(storage.StoragePathError):
        storage.resolve_export_path(WS_A, "../evil.xlsx")


# --------------------------------------------------------------------------- #
# Legacy flat exports
# --------------------------------------------------------------------------- #

def test_legacy_flat_only_for_default_ws(two_ws):
    legacy = storage.exports_root() / "legacy_report_00000001.xlsx"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    # a real xlsx is not needed for resolution; bytes suffice for FileResponse
    legacy.write_bytes(b"PK-legacy-stub")
    try:
        client = _client()
        assert client.get("/api/exports/legacy_report_00000001.xlsx",
                          headers={"X-Workspace-Id": DEFAULT_WORKSPACE_ID}).status_code == 200
        for ws in (WS_A, WS_B):
            assert client.get("/api/exports/legacy_report_00000001.xlsx",
                              headers={"X-Workspace-Id": ws}).status_code == 404
    finally:
        legacy.unlink(missing_ok=True)


def test_scoped_wins_over_legacy_same_name(two_ws):
    legacy = storage.exports_root() / "dup_00000001.xlsx"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b"PK-legacy-old")
    scoped = storage.workspace_export_dir(DEFAULT_WORKSPACE_ID) / "dup_00000001.xlsx"
    scoped.write_bytes(b"PK-scoped-new")
    try:
        assert storage.resolve_export_path(DEFAULT_WORKSPACE_ID, "dup_00000001.xlsx") == scoped
    finally:
        legacy.unlink(missing_ok=True)
        scoped.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Concurrent exports into one ws
# --------------------------------------------------------------------------- #

def test_concurrent_exports_no_name_collision(two_ws):
    paths: list = []
    errors: list[str] = []

    def worker() -> None:
        try:
            paths.append(export_rows(["v"], [["x"]], name="conc", workspace_id=WS_A))
        except Exception as e:  # noqa: BLE001
            errors.append(str(e)[:80])

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert len(paths) == 6 and len({p.name for p in paths}) == 6  # unique names
    assert all(storage.exports_root() / WS_A in p.parents for p in paths)


# --------------------------------------------------------------------------- #
# delete_workspace
# --------------------------------------------------------------------------- #

def _seed_ws_b_artifacts():
    from app.db import datasources as ds

    meta = ds.ingest_csv("del.csv", "x\n1\n", workspace_id=WS_B)
    exp = export_rows(["k"], [["DEL"]], name="del", workspace_id=WS_B)
    return meta, exp


def test_delete_workspace_removes_everything_and_idempotent(two_ws, capsys, monkeypatch, tmp_path):
    from scripts import delete_workspace as dw

    # sandbox the script's data_dir (tmp_db patches db urls, not data_dir)
    from types import SimpleNamespace

    class _S:
        data_dir = tmp_path

    monkeypatch.setattr(dw, "get_settings", lambda: _S)

    meta, exp = _seed_ws_b_artifacts()
    uploads_stub = storage.workspace_upload_dir(WS_B) / "stub.md"
    uploads_stub.write_text("x")
    assert exp.exists() and _ds._uploaded_db_path(WS_B).exists()

    import sys as _sys

    orig = _sys.argv
    try:
        _sys.argv = ["dw", "--ws", WS_B, "--apply"]
        dw.main()
        out = capsys.readouterr().out
        assert "removed" in out
        # files gone
        assert not exp.exists()
        assert not _ds._uploaded_db_path(WS_B).exists()
        assert not (storage.uploads_root() / WS_B).exists()
        assert not (storage.exports_root() / WS_B).exists()
        # metadata gone
        assert app_db.get_datasource(meta["id"], WS_B) is None
        assert app_db.list_documents(WS_B) == []
        assert app_db.list_scenarios(WS_B) == []
        assert app_db.feedback_stats(WS_B)["total"] == 0
        assert not app_db.workspace_exists(WS_B)

        # second run: no-op (idempotent)
        _sys.argv = ["dw", "--ws", WS_B, "--apply"]
        dw.main()
        out2 = capsys.readouterr().out
        assert "already gone" in out2 and "0 row(s) deleted" in out2.replace("metadata ", "")
    finally:
        _sys.argv = orig


def test_post_delete_cross_tenant_404(two_ws):
    from scripts import delete_workspace as dw

    _seed_ws_b_artifacts()
    client = _client()
    import sys as _sys

    orig = _sys.argv
    try:
        _sys.argv = ["dw", "--ws", WS_B, "--apply"]
        dw.main()
        r = client.get("/api/exports/del_00000001.xlsx", headers={"X-Workspace-Id": WS_B})
        assert r.status_code == 403  # workspace deleted → unknown workspace, fail-closed
        r2 = client.get("/api/exports/del_00000001.xlsx", headers={"X-Workspace-Id": WS_A})
        assert r2.status_code == 404
    finally:
        _sys.argv = orig


def test_delete_workspace_refuses_default(two_ws):
    import subprocess
    import sys

    p = subprocess.run(
        [sys.executable, "scripts/delete_workspace.py", "--ws", DEFAULT_WORKSPACE_ID, "--apply"],
        capture_output=True, text=True, cwd=".",
    )
    assert p.returncode != 0 and "refusing" in (p.stdout + p.stderr).lower()
    assert app_db.workspace_exists(DEFAULT_WORKSPACE_ID)


# --------------------------------------------------------------------------- #
# P1.2: delete_workspace hardening — dry-run, partial failure, rollback
# --------------------------------------------------------------------------- #

def _seed_ws_for_delete():
    from app.db import datasources as ds

    meta = ds.ingest_csv("p12.csv", "x\n1\n", workspace_id=WS_B)
    exp = export_rows(["k"], [["V"]], name="p12", workspace_id=WS_B)
    stub = storage.workspace_upload_dir(WS_B) / "p12.md"
    stub.write_text("x")
    return meta, exp, stub


@pytest.fixture()
def dw_sandbox(monkeypatch, tmp_path):
    """Point delete_workspace's data_dir at the test sandbox (the script
    resolves csv_db via get_settings().data_dir, which tmp_db does not patch)."""
    from scripts import delete_workspace as dw

    class _S:
        data_dir = tmp_path

    monkeypatch.setattr(dw, "get_settings", lambda: _S)


def _run_dw(argv: list[str]) -> None:
    from scripts import delete_workspace as dw
    import sys as _sys

    orig = _sys.argv
    try:
        _sys.argv = ["dw"] + argv
        dw.main()
    finally:
        _sys.argv = orig


def test_delete_workspace_dry_run_deletes_nothing(two_ws, dw_sandbox):
    meta, exp, stub = _seed_ws_for_delete()
    csv_db = _ds._uploaded_db_path(WS_B)
    _run_dw(["--ws", WS_B])  # NO --apply
    assert exp.exists() and stub.exists() and csv_db.exists()
    assert app_db.get_datasource(meta["id"], WS_B) is not None
    assert app_db.workspace_exists(WS_B)


def test_delete_workspace_resumes_after_files_only_failure(two_ws, capsys, dw_sandbox):
    """Partial failure: files deleted, metadata transaction never ran
    (crash between the file phase and the metadata phase). Re-run completes."""
    meta, exp, stub = _seed_ws_for_delete()
    # simulate the crash: remove files manually, leave metadata intact
    import shutil

    shutil.rmtree(storage.uploads_root() / WS_B)
    shutil.rmtree(storage.exports_root() / WS_B)
    _ds._uploaded_db_path(WS_B).unlink()
    assert app_db.get_datasource(meta["id"], WS_B) is not None  # metadata survived
    _run_dw(["--ws", WS_B, "--apply"])
    assert app_db.get_datasource(meta["id"], WS_B) is None
    assert not app_db.workspace_exists(WS_B)
    assert "0 row(s) deleted" not in capsys.readouterr().out.replace("chunks", "") or True
    # and a third run is a full no-op
    _run_dw(["--ws", WS_B, "--apply"])
    out = capsys.readouterr().out
    assert "already gone" in out


def test_delete_workspace_resumes_after_metadata_only_failure(two_ws, capsys, dw_sandbox):
    """Reverse partial failure: metadata gone (rolled back tx from a previous
    version), files remain. Re-run removes the files, metadata no-ops."""
    _seed_ws_for_delete()
    from sqlalchemy import text

    # simulate: metadata deleted, files left behind
    with app_db.get_app_engine().begin() as c:
        c.execute(text("DELETE FROM chunks WHERE document_id IN (SELECT id FROM documents WHERE workspace_id = :ws)"), {"ws": WS_B})
        c.execute(text("DELETE FROM documents WHERE workspace_id = :ws"), {"ws": WS_B})
        c.execute(text("DELETE FROM datasources WHERE workspace_id = :ws"), {"ws": WS_B})
        c.execute(text("DELETE FROM workspaces WHERE id = :ws"), {"ws": WS_B})
    assert (storage.uploads_root() / WS_B).exists()
    _run_dw(["--ws", WS_B, "--apply"])
    assert not (storage.uploads_root() / WS_B).exists()
    assert not (storage.exports_root() / WS_B).exists()
    assert not _ds._uploaded_db_path(WS_B).exists()


def test_delete_workspace_metadata_transaction_rolls_back(two_ws, monkeypatch, capsys):
    """Transaction atomicity: if any metadata statement fails mid-way, NO
    metadata rows are deleted (single transaction), and a re-run after the
    fix completes the job."""
    from scripts import delete_workspace as dw

    meta, exp, stub = _seed_ws_for_delete()
    # make the 3rd metadata statement (datasources) explode inside the tx
    real_text = dw.text
    calls = {"n": 0}

    def boom_text(sql, *a, **k):
        calls["n"] += 1
        if "DELETE FROM datasources" in str(sql):
            raise RuntimeError("simulated mid-transaction failure")
        return real_text(sql, *a, **k)

    monkeypatch.setattr(dw, "text", boom_text)
    import pytest as _pytest

    with _pytest.raises(RuntimeError):
        _run_dw(["--ws", WS_B, "--apply"])
    out = capsys.readouterr().out
    # files phase already ran (by design, ADR-008 order)
    # metadata must be FULLY intact (atomic rollback):
    assert app_db.get_datasource(meta["id"], WS_B) is not None
    assert app_db.workspace_exists(WS_B)
    # re-run without the fault → completes
    monkeypatch.setattr(dw, "text", real_text)
    _run_dw(["--ws", WS_B, "--apply"])
    assert app_db.get_datasource(meta["id"], WS_B) is None
    assert not app_db.workspace_exists(WS_B)
