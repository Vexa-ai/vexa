"""Conformance's view of the gateway's structured logging — RE-EXPORTED from the PRODUCTION
emitter (`gateway.obs`).

Before the carve this module was the harness's own copy of the trace emitter. It is now a
thin re-export of `gateway.obs` so there is ONE emitter (the shipped one): the `capture` sink,
`TRACE_HEADER`, `TraceMiddleware`, `log_event` and the trace contextvars the conformance
tracing eval (`test_tracing.py`) installs/asserts are the SAME objects the production
`gateway.create_app` uses. Driving the real app therefore drives the real tracing.

The downstream (`service="meeting-api"`) hop's emitter is built from the same factories in
`downstream_obs.py`, sharing these module-global contextvars — the in-process model of the
cross-process `X-Trace-Id` propagation.
"""
from __future__ import annotations

from gateway.obs import (  # noqa: F401  (re-exported public surface)
    SERVICE,
    TRACE_HEADER,
    TraceMiddleware,
    capture,
    get_trace_id,
    log_event,
    make_log_event,
    make_trace_middleware,
    new_trace_id,
    set_trace_id,
    set_user_id,
)
