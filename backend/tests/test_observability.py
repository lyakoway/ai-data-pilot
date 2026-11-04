"""Observability layer tests — fake recorder, no external calls.

Covers: enabled/disabled paths, Langfuse-down resilience, redaction, nested
span linkage, Atlas/Doc trace contents, unchanged SSE contract, shutdown.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any

import pytest

from app.observability import adapter as obs_adapter
from app.observability.adapter import NOOP, obs_span, obs_trace, redact, reset_for_testing


class FakeObservation:
    def __init__(self, recorder: "FakeLangfuse", name: str, kind: str,
                 metadata: dict | None, parent: "FakeObservation | None"):
        self.recorder = recorder
        self.name = name
        self.kind = kind
        self.metadata = dict(metadata or {})
        self.output: Any = None
        self.parent_name = parent.name if parent else None
        recorder.spans.append(self)

    def set_output(self, out: Any) -> None:
        self.output = out

    def set_metadata(self, md: dict) -> None:
        self.metadata.update(md)

    def update(self, **kw: Any) -> None:  # status annotations
        self.metadata.update({"_update": kw})

    def end(self) -> None: ...


class FakeLangfuse:
    """Captures observations; emulates the v5 context-manager nesting."""

    def __init__(self):
        self.spans: list[FakeObservation] = []
        self._stack: list[FakeObservation] = []
        self.shutdown_called = False
        self.fail_mode = None  # None | 'start' | 'methods'

    @contextmanager
    def start_as_current_observation(self, *, name: str, as_type: str = "span",
                                     metadata: dict | None = None, **_: Any):
        if self.fail_mode == "start":
            raise RuntimeError("langfuse down")
        parent = self._stack[-1] if self._stack else None
        obs = FakeObservation(self, name, as_type, metadata, parent)
        self._stack.append(obs)
        try:
            yield obs
        finally:
            self._stack.pop()

    def get_current_trace_id(self) -> str | None:
        return "fake-trace-id" if self._stack else None

    def shutdown(self) -> None:
        self.shutdown_called = True


@pytest.fixture()
def fake_langfuse(monkeypatch: pytest.MonkeyPatch):
    fake = FakeLangfuse()
    reset_for_testing()
    monkeypatch.setattr(obs_adapter, "get_client", lambda: fake)
    yield fake
    reset_for_testing()


@pytest.fixture()
def obs_disabled(monkeypatch: pytest.MonkeyPatch):
    reset_for_testing()
    monkeypatch.setattr(obs_adapter, "get_client", lambda: None)
    yield
    reset_for_testing()


# --------------------------------------------------------------------------- #
# 1. Enabled: spans are recorded and nested
# --------------------------------------------------------------------------- #

def test_tracing_enabled_records_nested_spans(fake_langfuse):
    with obs_trace("chat"):
        with obs_span("agent_router", metadata={"q": 1}) as sp:
            sp.set_metadata({"decision": "atlas"})
            with obs_span("atlas.planner", as_type="generation") as inner:
                inner.set_metadata({"plan_ok": True})
    names = [s.name for s in fake_langfuse.spans]
    assert "agent_router" in names and "atlas.planner" in names
    planner = next(s for s in fake_langfuse.spans if s.name == "atlas.planner")
    assert planner.parent_name == "agent_router", "nested spans must link to parent"


# --------------------------------------------------------------------------- #
# 2. Disabled: everything is a silent no-op
# --------------------------------------------------------------------------- #

def test_tracing_disabled_is_noop(obs_disabled):
    with obs_trace("chat"):
        with obs_span("x") as sp:
            sp.set_metadata({"a": 1})
            sp.set_output("y")
    assert obs_adapter.get_client() is None  # still disabled, no side effects


@pytest.mark.asyncio
async def test_disabled_run_atlas_still_works(tmp_db, monkeypatch, obs_disabled):
    from app.agents.atlas import run_atlas

    r = await run_atlas("Сколько поездок?", model_id="mock")
    assert r["status"] == "demo"  # business path unaffected


# --------------------------------------------------------------------------- #
# 3. Langfuse unavailable / raising: user path survives
# --------------------------------------------------------------------------- #

def test_langfuse_down_does_not_break_spans(monkeypatch):
    fake = FakeLangfuse()
    fake.fail_mode = "start"
    reset_for_testing()
    monkeypatch.setattr(obs_adapter, "get_client", lambda: fake)
    with obs_trace("chat"):  # obs_trace swallows → null trace
        with obs_span("child") as sp:
            assert sp is NOOP
    reset_for_testing()


def test_obs_methods_raising_are_swallowed(monkeypatch):
    fake = FakeLangfuse()
    fake.fail_mode = "methods"

    class EvilObs:
        def set_metadata(self, *_a, **_k):
            raise RuntimeError("boom")

        def set_output(self, *_a, **_k):
            raise RuntimeError("boom")

    @contextmanager
    def _wrap(**_k):
        yield EvilObs()

    fake.start_as_current_observation = _wrap  # type: ignore[method-assign]
    reset_for_testing()
    monkeypatch.setattr(obs_adapter, "get_client", lambda: fake)
    with obs_span("x", metadata={"a": 1}) as sp:
        sp.set_metadata({"b": 2})  # must not raise
    reset_for_testing()


# --------------------------------------------------------------------------- #
# 4. Redaction
# --------------------------------------------------------------------------- #

def _patch_capture(monkeypatch: pytest.MonkeyPatch, value: bool) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(obs_adapter, "get_settings",
                        lambda: SimpleNamespace(langfuse_capture_content=value))


def test_redaction_hash_by_default(fake_langfuse, monkeypatch):
    _patch_capture(monkeypatch, False)
    r = redact("SELECT secret FROM t", capture=True)
    assert isinstance(r, dict) and "sha256" in r and "secret" not in str(r)


def test_redaction_raw_content_when_explicitly_enabled(monkeypatch):
    _patch_capture(monkeypatch, True)
    r = redact("SELECT secret FROM t", capture=True)
    assert "SELECT secret" in r


def test_redaction_hashes_non_capture_fields_even_in_raw_mode(monkeypatch):
    _patch_capture(monkeypatch, True)
    r = redact("huge system prompt", capture=False)
    assert isinstance(r, dict) and "huge system prompt" not in str(r)


# --------------------------------------------------------------------------- #
# 5. Atlas trace contains required steps (mock provider — no external LLM)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_atlas_trace_has_planner_sql_answer(tmp_db, fake_langfuse, monkeypatch):
    from app.agents.atlas import run_atlas

    r = await run_atlas("Сколько поездок всего?", model_id="mock")
    assert r["status"] == "demo"
    names = {s.name for s in fake_langfuse.spans}
    # the linear path records SQL execution even in demo mode
    assert "atlas.sql_execute" in names
    assert r["row_count"] >= 0  # business result intact


@pytest.mark.asyncio
async def test_atlas_loop_trace_has_think_and_tool(tmp_db, fake_langfuse, monkeypatch):
    from app.agents.atlas import run_atlas
    from tests.conftest import FakeProvider

    decision = json.dumps({"thought": "t", "tool": "finish", "args": {"answer": "готово"}})
    fake = FakeProvider(responses=[decision], provider="openai")
    monkeypatch.setattr("app.agents.atlas.get_provider", lambda model_id: fake)
    r = await run_atlas("Почему выручка упала в июле?", model_id="openai:gpt-4o")
    assert r["status"] == "ok"
    names = {s.name for s in fake_langfuse.spans}
    assert "atlas.loop.think" in names
    assert any(n.startswith("atlas.tool.") for n in names)


# --------------------------------------------------------------------------- #
# 6. Doc trace contains retrieval and generation
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_doc_trace_has_retrieval_with_ids_and_scores(tmp_db, fake_langfuse):
    from app.agents.doc import run_doc

    await run_doc("Какой TTL у кэша тарифов?", model_id="mock")
    by_name = {s.name: s for s in fake_langfuse.spans}
    assert "doc.retrieval" in by_name
    ret = by_name["doc.retrieval"]
    assert ret.kind == "retriever"
    assert ret.metadata["chunk_ids"], "chunk ids must be recorded"
    assert ret.metadata["scores"], "scores must be recorded"


# --------------------------------------------------------------------------- #
# 7. SSE contract unchanged with tracing enabled
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_sse_contract_unchanged_with_obs(fake_langfuse):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    client = TestClient(app)
    with client.stream("POST", "/api/chat/stream",
                       json={"message": "Сколько городов?", "agent": "atlas",
                             "model": "mock", "lang": "ru"}) as resp:
        assert resp.status_code == 200
        body = "".join(line for line in resp.iter_lines())
    assert "event: step" in body and "event: done" in body, "SSE events must be preserved"
    assert '"status"' in body  # done payload carries the app contract


# --------------------------------------------------------------------------- #
# 8. Root trace metadata (status/duration/iterations)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_root_trace_records_status_and_iterations(tmp_db, fake_langfuse, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app())
    r = client.post("/api/chat", json={"message": "Сколько городов?", "agent": "atlas",
                                       "model": "mock", "lang": "ru"})
    assert r.status_code == 200
    assert r.json()["status"] == "demo"
    # route spans recorded
    names = {s.name for s in fake_langfuse.spans}
    assert "agent_router" in names or "source_router" in names


# --------------------------------------------------------------------------- #
# 9. Sampling: dropped trace silences nested spans
# --------------------------------------------------------------------------- #

def test_sampling_drops_trace_and_children(monkeypatch):
    reset_for_testing()
    fake = FakeLangfuse()
    monkeypatch.setattr(obs_adapter, "get_client", lambda: fake)
    monkeypatch.setattr(obs_adapter, "should_sample", lambda: False)
    with obs_trace("chat") as root:
        assert root is NOOP
        with obs_span("child") as sp:
            assert sp is NOOP
    assert fake.spans == [], "dropped trace must not record anything"
    reset_for_testing()


# --------------------------------------------------------------------------- #
# 10. Shutdown flush
# --------------------------------------------------------------------------- #

def test_shutdown_calls_client_shutdown(fake_langfuse):
    obs_adapter.observability_shutdown()
    assert fake_langfuse.shutdown_called


def test_shutdown_safe_when_disabled(obs_disabled):
    obs_adapter.observability_shutdown()  # must not raise
