"""The DOWNSTREAM hop's structured logging (service = ``meeting-api``) for the in-process
conformance chain.

This stands in for the real ``meeting-api`` service's own ``obs`` module. It binds to the
SAME module-global contextvars as ``obs`` (same process), so a trace_id minted by the
gateway edge middleware and forwarded over ``X-Trace-Id`` is read back here and bound for
the downstream request — producing log lines with ``service="meeting-api"`` that share the
gateway's ``trace_id``. That cross-hop trace is exactly what ``test_tracing.py`` asserts.
"""
from __future__ import annotations

from .obs import make_log_event, make_trace_middleware  # noqa: F401  (re-exported for the split)

SERVICE = "meeting-api"
log_event = make_log_event(SERVICE)
TraceMiddleware = make_trace_middleware(log_event)
