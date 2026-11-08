"""CORS hardening tests (P0.1, security gap G1).

The old middleware used `allow_origins + ["*"]` with allow_credentials=True —
verified empirically to return `Access-Control-Allow-Origin: *` to ANY origin.
These tests pin the fixed contract: explicit allowlist, wildcard impossible,
fail-fast on bad production config, dev scenario preserved.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app, resolve_cors_origins


def _client_with_settings(settings: Settings) -> TestClient:
    """create_app reads the cached get_settings(); patch it for this test."""
    import app.main as main_mod
    from unittest.mock import patch

    with patch.object(main_mod, "get_settings", lambda: settings):
        app = create_app()
    return TestClient(app)


# --------------------------------------------------------------------------- #
# Resolver: production validation
# --------------------------------------------------------------------------- #

def test_production_valid_allowlist_passes():
    origins = ["https://app.example.com", "https://www.example.com:8443"]
    assert resolve_cors_origins(Settings(app_env="production", cors_origins=origins)) == origins


@pytest.mark.parametrize("bad,reason", [
    ([], "empty allowlist"),
    (["*"], "wildcard"),
    (["https://ok.example", "*"], "wildcard mixed in"),
    (["app.example.com"], "no scheme"),
    (["ftp://x.example"], "non-http scheme"),
    (["http://localhost:5173"], "localhost in production"),
    (["http://127.0.0.1:8001"], "loopback ip in production"),
])
def test_production_invalid_configs_fail_fast(bad: list[str], reason: str):
    with pytest.raises(RuntimeError, match="CORS"):
        resolve_cors_origins(Settings(app_env="production", cors_origins=bad))


def test_wildcard_rejected_even_in_dev():
    with pytest.raises(RuntimeError, match=r"\*"):
        resolve_cors_origins(Settings(app_env="dev", cors_origins=["https://x.example", "*"]))


def test_dev_defaults_pass_through():
    s = Settings()
    origins = resolve_cors_origins(s)
    assert "http://localhost:5173" in origins
    assert "*" not in origins


# --------------------------------------------------------------------------- #
# Middleware behaviour on real requests
# --------------------------------------------------------------------------- #

def test_allowed_origin_gets_cors_headers():
    client = _client_with_settings(Settings(cors_origins=["http://localhost:5173"]))
    r = client.get("/api/models", headers={"Origin": "http://localhost:5173"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert r.headers.get("access-control-allow-credentials") == "true"


def test_disallowed_origin_gets_no_cors_headers():
    client = _client_with_settings(Settings(cors_origins=["http://localhost:5173"]))
    r = client.get("/api/models", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_no_wildcard_in_built_middleware():
    """G1 regression pin: the app must never be built with a '*' origin."""
    client = _client_with_settings(Settings())
    r = client.get("/api/models", headers={"Origin": "https://evil.example"})
    assert r.headers.get("access-control-allow-origin") != "*"
    assert "access-control-allow-origin" not in r.headers


def test_preflight_from_allowed_origin():
    client = _client_with_settings(Settings(cors_origins=["http://localhost:5173"]))
    r = client.options(
        "/api/chat",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "POST" in r.headers.get("access-control-allow-methods", "")
    assert "content-type" in r.headers.get("access-control-allow-headers", "")


# --------------------------------------------------------------------------- #
# Dev scenario compatibility (vite proxy → localhost:5173)
# --------------------------------------------------------------------------- #

def test_dev_scenario_local_frontend_still_allowed():
    """The existing local dev flow (frontend on 5173, API on 8001) must keep
    working unchanged — both default origins answer with CORS headers."""
    client = _client_with_settings(Settings())  # app_env=dev, default list
    for origin in ("http://localhost:5173", "http://127.0.0.1:8001"):
        r = client.get("/api/models", headers={"Origin": origin})
        assert r.headers.get("access-control-allow-origin") == origin, origin


def test_create_app_fails_loudly_on_bad_production(monkeypatch):
    import app.main as main_mod
    from unittest.mock import patch

    bad = Settings(app_env="production", cors_origins=[])
    with patch.object(main_mod, "get_settings", lambda: bad):
        with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
            create_app()
