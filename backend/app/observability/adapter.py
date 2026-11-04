"""Langfuse instrumentation layer — thin adapter, isolated from business logic.

Design constraints (stage-5 requirements):
- business code imports ONLY this module's helpers (never `langfuse` directly);
- disabled / unconfigured / crashed Langfuse degrades to a no-op: a user
  request must never fail because of observability;
- the official v5 SDK (OpenTelemetry-based) batches asynchronously, so the
  hot path is not blocked; `observability_shutdown()` flushes on app exit;
- REDACTION BY DEFAULT: prompts/completions/SQL are recorded as
  sha256[:12] + length. Raw (truncated) content is sent only when
  LANGFUSE_CAPTURE_CONTENT=true — never secrets or credentials;
- sampling: a dropped trace makes all its nested spans no-ops (contextvar).

Span linkage relies on the SDK's own contextvars (async-safe); the only
module-level state is the lazily built client singleton (the SDK is designed
process-global) plus the drop-flag contextvar.
"""
from __future__ import annotations

import hashlib
import random
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from app.config import get_settings

# Set on the root trace when sampling drops it: nested spans check this and no-op.
_trace_dropped: ContextVar[bool] = ContextVar("obs_trace_dropped", default=False)

_client: Any = None
_client_init_done = False

_CONTENT_PREVIEW_CHARS = 2000


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]


def redact(text: str | None, capture: bool, settings: Any = None) -> Any:
    """Redaction policy: hash+length by default; truncated raw text only if
    content capture is explicitly enabled. Never pass secrets here — callers
    must not hand credentials into observability at all."""
    if text is None:
        return None
    s = settings or get_settings()
    if getattr(s, "langfuse_capture_content", False) and capture:
        return text[:_CONTENT_PREVIEW_CHARS]
    return {"sha256": _sha(str(text)), "len": len(str(text))}


class _NoopObservation:
    """Duck-typed stand-in used when tracing is disabled or dropped."""

    def set_output(self, *_a: Any, **_k: Any) -> None: ...
    def set_metadata(self, *_a: Any, **_k: Any) -> None: ...
    def set_status(self, *_a: Any, **_k: Any) -> None: ...
    def update(self, *_a: Any, **_k: Any) -> None: ...
    def end(self, *_a: Any, **_k: Any) -> None: ...


NOOP = _NoopObservation()


class SafeObservation:
    """Wrapper handed to business code instead of the raw SDK observation:
    EVERY method is guarded, so instrumentation calls can never raise —
    regardless of SDK state (offline, broken, race on shutdown)."""

    def __init__(self, obs: Any) -> None:
        self._obs = obs

    def set_output(self, *a: Any, **k: Any) -> None:
        try:
            self._obs.set_output(*a, **k)
        except Exception:  # noqa: BLE001
            pass

    def set_metadata(self, *a: Any, **k: Any) -> None:
        try:
            self._obs.set_metadata(*a, **k)
        except Exception:  # noqa: BLE001
            pass

    def update(self, *a: Any, **k: Any) -> None:
        try:
            self._obs.update(*a, **k)
        except Exception:  # noqa: BLE001
            pass

    def end(self, *a: Any, **k: Any) -> None:
        try:
            self._obs.end(*a, **k)
        except Exception:  # noqa: BLE001
            pass


def get_client() -> Any:
    """Lazily build the Langfuse client. Returns None when disabled or the
    SDK/credentials are unavailable — callers must treat None as no-op."""
    global _client, _client_init_done
    if _client_init_done:
        return _client
    _client_init_done = True
    try:
        s = get_settings()
        keys_present = bool(s.langfuse_public_key and s.langfuse_secret_key)
        if not s.langfuse_enabled or not keys_present:
            return None
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=s.langfuse_public_key,
            secret_key=s.langfuse_secret_key,
            host=s.langfuse_host,
        )
    except Exception:  # noqa: BLE001 — observability must never break startup
        _client = None
    return _client


def reset_for_testing() -> None:
    global _client, _client_init_done
    _client = None
    _client_init_done = False


def observability_enabled() -> bool:
    return get_client() is not None


def get_trace_id() -> str | None:
    """Correlation id for logs: the current Langfuse trace id, if any."""
    try:
        client = get_client()
        if client is None:
            return None
        return client.get_current_trace_id()
    except Exception:  # noqa: BLE001
        return None


@contextmanager
def obs_span(
    name: str,
    *,
    as_type: str = "span",
    metadata: dict[str, Any] | None = None,
    input: Any = None,
    output: Any = None,
    model: str | None = None,
) -> Iterator[Any]:
    """Context manager for a traced observation. Nests automatically inside
    the enclosing observation (SDK contextvars). Never raises.

    IMPORTANT: exceptions from the WITH-BODY are business exceptions — they
    propagate unchanged (e.g. SqlExecutionError must reach the agent's repair
    handlers). Only SDK enter/exit failures are swallowed."""
    if _trace_dropped.get():
        yield NOOP
        return
    client = get_client()
    if client is None:
        yield NOOP
        return
    try:
        cm = client.start_as_current_observation(
            name=name, as_type=as_type, metadata=metadata,
            input=input, output=output, model=model,
        )
        obs = SafeObservation(cm.__enter__())
    except Exception:  # noqa: BLE001 — SDK unavailable: silent no-op
        yield NOOP
        return
    try:
        yield obs
    except BaseException:  # body exception: hand to SDK, then re-raise
        import sys
        try:
            cm.__exit__(*sys.exc_info())
        except Exception:  # noqa: BLE001 — SDK exit failure must not mask it
            pass
        raise
    else:
        try:
            cm.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass


def should_sample() -> bool:
    s = get_settings()
    rate = s.langfuse_sample_rate
    if rate >= 1.0:
        return True
    return random.random() < rate


@contextmanager
def obs_trace(name: str, *, user_id: str | None = None, metadata: dict[str, Any] | None = None) -> Iterator[Any]:
    """ROOT trace for one user request (context manager). Applies sampling:
    a dropped trace flips the drop-flag so nested `obs_span` calls no-op.
    Entering/exiting the SDK context manager is fully guarded — a broken
    Langfuse never breaks the request."""
    client = get_client()
    if client is None:
        yield NOOP
        return
    if not should_sample():
        token = _trace_dropped.set(True)
        try:
            yield NOOP
        finally:
            _trace_dropped.reset(token)
        return
    inner: Any = None
    try:
        inner = client.start_as_current_observation(name=name, metadata=metadata)
        trace = SafeObservation(inner.__enter__())
    except Exception:  # noqa: BLE001 — SDK enter failure: silent no-op
        yield NOOP
        return
    try:
        yield trace
    except BaseException:  # body exception: hand to SDK, then re-raise
        import sys
        try:
            inner.__exit__(*sys.exc_info())
        except Exception:  # noqa: BLE001
            pass
        raise
    else:
        try:
            inner.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass


def mark_status(obs: Any, ok: bool, message: str = "") -> None:
    """Best-effort status annotation; used on spans and the root trace."""
    try:
        if obs is not None and obs is not NOOP:
            obs.update(status_message=message or ("ok" if ok else "error"))
    except Exception:  # noqa: BLE001
        pass


def timing_ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def observability_shutdown() -> None:
    """Flush pending traces on app shutdown (bounded by the SDK's timeout)."""
    try:
        client = get_client()
        if client is not None:
            client.shutdown()
    except Exception:  # noqa: BLE001
        pass
