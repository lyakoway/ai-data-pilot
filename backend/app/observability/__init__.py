"""LLM observability layer (Langfuse). Import helpers from the adapter:

    from app.observability import obs_span, obs_trace, redact

Disabled / unconfigured / broken Langfuse degrades to no-op everywhere —
see adapter.py for the design contract.
"""
from app.observability.adapter import (  # noqa: F401
    NOOP,
    get_client,
    get_trace_id,
    mark_status,
    observability_enabled,
    observability_shutdown,
    obs_span,
    obs_trace,
    redact,
    reset_for_testing,
    timing_ms,
)
