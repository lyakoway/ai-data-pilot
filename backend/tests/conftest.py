"""Shared pytest fixtures.

Tests run against an isolated temp SQLite DB so they never touch the real
``backend/data/ridego.db``. Provider access in agent tests is mocked so no API
keys or network are required.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine

from app import config as config_module
from app.db import seed as seed_module


@pytest.fixture()
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the app config at temp SQLite files (analytics DB + app DB), seed
    them, and reload modules that cached the old settings."""
    db_path = tmp_path / "test.db"
    db_url = f"sqlite:///{db_path}"
    app_db_path = tmp_path / "test_app.db"
    app_db_url = f"sqlite:///{app_db_path}"
    csv_db_path = tmp_path / "test_csv.db"

    # Clear the lru_cache so new settings are picked up, then patch database urls.
    config_module.get_settings.cache_clear()
    monkeypatch.setattr(
        config_module.Settings,
        "database_url",
        property(lambda self: db_url),
        raising=False,
    )
    monkeypatch.setattr(
        config_module.Settings,
        "app_db_url",
        property(lambda self: app_db_url),
        raising=False,
    )
    monkeypatch.setattr(config_module.Settings, "demo_scale", "small", raising=False)

    # Point the uploaded-data DB at the temp dir too (CSV + Excel sources).
    from app.db import datasources as ds_module

    monkeypatch.setattr(ds_module, "_uploaded_db_path", lambda: csv_db_path)

    # Reset the cached app engine so it picks up the new app_db_url.
    from app.db import app_db

    app_db.reset_app_engine()

    # Re-seed the analytics DB into the temp location.
    seed_module.seed_analytics_db(force=True)
    yield db_path
    config_module.get_settings.cache_clear()
    app_db.reset_app_engine()


@pytest.fixture()
def tmp_engine(tmp_db: Path):
    return create_engine(f"sqlite:///{tmp_db}")


class FakeProvider:
    """Configurable async provider that returns scripted responses.

    Pass a list of ``responses`` (returned in order on each ``complete`` call).
    The last response is repeated if the model is called more times than scripts
    provided. Set ``provider`` to control which agent code path is exercised
    (``"mock"`` triggers the deterministic demo path; anything else triggers the
    real-model path including self-correction).
    """

    def __init__(self, responses: list[str] | None = None, provider: str = "openai") -> None:
        self.responses = list(responses or [])
        self.calls = 0
        self.provider = provider
        self.model = "fake"

    async def complete(self, system: str, messages: list[Any], lang: str = "ru") -> str:
        self.calls += 1
        if self.calls <= len(self.responses):
            return self.responses[self.calls - 1]
        return self.responses[-1] if self.responses else ""


@pytest.fixture()
def fake_provider_factory():
    """Return a factory building :class:`FakeProvider` instances."""
    return FakeProvider
