"""P0.2 cross-tenant isolation tests.

Proves: workspace A cannot read/modify workspace B's datasources, documents,
scenarios or feedback; RAG search is workspace-scoped with NO global
fallback; missing/invalid tenant context is refused (fail-closed), while the
dev scenario (default workspace) keeps working end-to-end.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import app_db, datasources as ds
from app.tenancy import (
    DEFAULT_WORKSPACE_ID,
    TenantContextError,
    resolve_dev_workspace,
    require_workspace,
)

WS_A = "ws_alpha"
WS_B = "ws_beta"


@pytest.fixture()
def two_workspaces(tmp_db):
    """Create workspaces A and B beside the seeded default."""
    eng = app_db.get_app_engine()
    from sqlalchemy import text

    with eng.begin() as conn:
        for ws, name in ((WS_A, "Alpha"), (WS_B, "Beta")):
            conn.execute(
                text("INSERT OR IGNORE INTO workspaces (id, name, created_at) VALUES (:id, :n, :now)"),
                {"id": ws, "n": name, "now": "2026-11-07 00:00:00"},
            )
    yield
    # tmp_db resets the whole DB per test — no manual cleanup needed


# --------------------------------------------------------------------------- #
# Tenant context resolution
# --------------------------------------------------------------------------- #

def test_missing_context_fails_closed_at_db_layer(two_workspaces):
    with pytest.raises(TenantContextError):
        app_db.list_documents()          # no workspace → refusal, not global
    with pytest.raises(TenantContextError):
        app_db.get_chunks_for_search()   # RAG: never a global fallback
    with pytest.raises(TenantContextError):
        app_db.list_scenarios()


def test_invalid_workspace_refused(two_workspaces):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        resolve_dev_workspace("ws_does_not_exist", production=False)
    assert ei.value.status_code == 403


def test_production_missing_header_refused(two_workspaces):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        resolve_dev_workspace(None, production=True)
    assert ei.value.status_code == 403
    # dev without header → the default workspace (documented dev fallback)
    assert resolve_dev_workspace(None, production=False) == DEFAULT_WORKSPACE_ID


# --------------------------------------------------------------------------- #
# Datasource isolation
# --------------------------------------------------------------------------- #

def test_datasource_from_other_workspace_invisible(two_workspaces):
    meta = ds.ingest_csv("secret.csv", "k,v\nalpha_secret,1\n", workspace_id=WS_A)
    # B cannot see, use, or delete A's source
    assert app_db.get_datasource(meta["id"], WS_B) is None
    assert ds.get_source_meta(meta["id"], WS_B) is None
    with pytest.raises(KeyError):
        ds.get_engine_for(meta["id"], WS_B)
    with pytest.raises(KeyError):
        ds.delete_source(meta["id"], WS_B)
    # ... and it still exists for A (B's attempts changed nothing)
    assert app_db.get_datasource(meta["id"], WS_A) is not None


def test_datasource_listing_scoped(two_workspaces):
    ds.ingest_csv("a.csv", "x\n1\n", workspace_id=WS_A)
    ds.ingest_csv("b.csv", "x\n2\n", workspace_id=WS_B)
    a_ids = {s["id"] for s in app_db.list_datasources(WS_A)}
    b_ids = {s["id"] for s in app_db.list_datasources(WS_B)}
    a_csvs = {i for i in a_ids if i.startswith("csv_")}
    b_csvs = {i for i in b_ids if i.startswith("csv_")}
    assert a_csvs and b_csvs and not (a_csvs & b_csvs)
    # built-in global demo source is visible to both
    assert "ridego" in a_ids and "ridego" in b_ids


def test_delete_only_own_source(two_workspaces):
    meta = ds.ingest_csv("mine.csv", "x\n1\n", workspace_id=WS_A)
    assert app_db.delete_datasource_row(meta["id"], WS_B) is False   # no-op
    assert app_db.get_datasource(meta["id"], WS_A) is not None       # intact
    assert app_db.delete_datasource_row(meta["id"], WS_A) is True


# --------------------------------------------------------------------------- #
# Document & RAG isolation
# --------------------------------------------------------------------------- #

def _make_doc(doc_id: str, ws: str, text: str) -> None:
    app_db.save_document(
        {"id": doc_id, "filename": f"{doc_id}.md", "content_type": "text/markdown",
         "size_bytes": len(text), "page_count": 1, "chunk_count": 1,
         "status": "ready", "error": None, "datasource_id": None,
         "created_at": "2026-11-07 00:00:00"},
        workspace_id=ws,
    )
    app_db.insert_chunks(doc_id, [{"label": doc_id, "text": text}])


def test_document_from_other_workspace_invisible(two_workspaces):
    _make_doc("doc_alpha", WS_A, "Альфа-секрет: код доступа 12345")
    assert app_db.get_document("doc_alpha", WS_B) is None
    assert app_db.delete_document_row("doc_alpha", WS_B) is False
    assert app_db.get_document("doc_alpha", WS_A) is not None  # untouched


def test_rag_retrieval_never_crosses_workspaces(two_workspaces):
    from app.core.docs_rag import retrieve

    _make_doc("doc_alpha", WS_A, "Уникальный маркер альфы ALPHA_MARKER_42")
    _make_doc("doc_beta", WS_B, "Обычный текст беты без маркеров")
    hits_a = retrieve("ALPHA_MARKER_42", top_k=5, mode="hybrid", workspace_id=WS_A)
    hits_b = retrieve("ALPHA_MARKER_42", top_k=5, mode="hybrid", workspace_id=WS_B)
    assert any("ALPHA_MARKER_42" in c.text for c in hits_a), "A must find its doc"
    assert not any("ALPHA_MARKER_42" in c.text for c in hits_b), "B must NOT see A's doc"


def test_rag_no_global_fallback(two_workspaces):
    with pytest.raises(TenantContextError):
        # no workspace → refusal even though docs exist
        app_db.get_chunks_for_search()


def test_rag_semantics_preserved_within_workspace(two_workspaces):
    """Retrieval inside ONE workspace keeps its semantics (built-in product
    docs stay global, uploads stay scoped)."""
    from app.core.docs_rag import retrieve

    hits = retrieve("TTL 15 минут", top_k=3, mode="hybrid", workspace_id=WS_A)
    # built-in product doc is global — visible from any workspace
    assert any(c.doc_id == "data_lineage.md#4" for c in hits)


# --------------------------------------------------------------------------- #
# Scenario & feedback isolation
# --------------------------------------------------------------------------- #

def test_scenario_isolation(two_workspaces):
    sc = app_db.create_scenario(
        {"id": "sc_a", "name": "A scenario", "agent": "atlas", "prompt": "p",
         "description": "", "chart_type": None, "datasource_id": None, "parameters": None},
        WS_A,
    )
    assert sc["id"] == "sc_a"
    assert app_db.get_scenario("sc_a", WS_B) is None
    assert [s["id"] for s in app_db.list_scenarios(WS_A)] == ["sc_a"]
    assert app_db.list_scenarios(WS_B) == []
    assert app_db.delete_scenario("sc_a", WS_B) is False
    assert app_db.get_scenario("sc_a", WS_A) is not None


def test_feedback_isolation(two_workspaces):
    app_db.save_feedback({"vote": "up", "agent": "atlas", "message": "alpha"}, WS_A)
    app_db.save_feedback({"vote": "down", "agent": "doc", "message": "beta"}, WS_B)
    stats_a = app_db.feedback_stats(WS_A)
    assert stats_a["up"] == 1 and stats_a["down"] == 0
    stats_b = app_db.feedback_stats(WS_B)
    assert stats_b["up"] == 0 and stats_b["down"] == 1
    assert len(app_db.feedback_list(workspace_id=WS_A)) == 1


# --------------------------------------------------------------------------- #
# HTTP layer: the header is the dev workspace selector
# --------------------------------------------------------------------------- #

def test_http_foreign_datasource_id_refused(two_workspaces):
    meta = ds.ingest_csv("http_secret.csv", "k\nv\n", workspace_id=WS_A)
    client = TestClient(_app())
    r = client.post(
        "/api/chat",
        json={"message": "что в таблице?", "agent": "atlas", "model": "mock",
              "lang": "ru", "datasource_id": meta["id"]},
        headers={"X-Workspace-Id": WS_B},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "error"
    assert "недоступен" in body["answer"]
    assert body["rows"] == []  # no cross-tenant data in the payload


def test_http_dev_scenario_default_workspace(two_workspaces):
    """No header → dev default workspace; the seeded demo keeps working."""
    client = TestClient(_app())
    r = client.post("/api/chat", json={"message": "Сколько городов?",
                                       "agent": "atlas", "model": "mock", "lang": "ru"})
    assert r.status_code == 200
    assert r.json()["status"] in ("demo", "ok")


def test_http_unknown_workspace_403(two_workspaces):
    client = TestClient(_app())
    r = client.get("/api/datasources", headers={"X-Workspace-Id": "ws_hacker"})
    assert r.status_code == 403


def _app():
    from app.main import create_app

    return create_app()
