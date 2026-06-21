"""O-OBS-1 — distributed tracing + structured logging conformance (the future gate:tracing).

A synthetic request enters the gateway (the edge) with NO trace-id. The trace middleware
MINTS exactly one ``trace_id``, binds it via contextvars, and FORWARDS it downstream via the
``X-Trace-Id`` header. This eval asserts, OFFLINE (TestClient, no docker, no meetings):

  1. the edge mints ONE trace_id (request had none) and echoes it on the response;
  2. it is forwarded to the downstream (meeting-api) hop — proven by downstream log lines
     carrying the SAME id and a distinct ``service``;
  3. EVERY structured log line emitted across BOTH hops carries that SAME trace_id;
  4. every captured line conforms to ``logevent.v1`` (validated BY PATH);
  5. a freeform / non-conformant log line is DETECTED and would fail the gate;
  6. the user-vs-system split holds — a user-facing event has ``audience=user`` and a
     system/debug event has ``audience=system``.

Capture is via an in-process sink (``obs.capture``) so lines are collected deterministically
across the TestClient's threadpool — no fragile stdout capture.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gateway_conformance import obs
from gateway_conformance.contracts import (
    assert_logevent_conforms,
    is_conformant_logevent,
)
from gateway_conformance.fake_meeting_api import VALID_API_KEY
from gateway_conformance.gateway_app import build_gateway

AUTH = {"x-api-key": VALID_API_KEY}
TRACE_HEADER = obs.TRACE_HEADER


@pytest.fixture
def captured_lines():
    """Install the obs sink for the duration of one test; lines land in the returned list."""
    sink: list[dict] = []
    obs.capture(sink)
    try:
        yield sink
    finally:
        obs.capture(None)


def _post_create_bot(headers: dict):
    """A representative request that traverses gateway → downstream (meeting-api) and emits
    user + system events on both hops."""
    client = TestClient(build_gateway())
    return client.post(
        "/bots",
        headers=headers,
        json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij"},
    )


def test_edge_mints_one_trace_id_when_absent(captured_lines):
    """No X-Trace-Id on the request → the edge mints exactly ONE, echoes it on the response,
    and the 'request_received' line records minted=True."""
    resp = _post_create_bot(AUTH)
    assert resp.status_code == 201, resp.text

    echoed = resp.headers.get(TRACE_HEADER)
    assert echoed, "gateway must echo the minted trace_id on the response"

    trace_ids = {ln["trace_id"] for ln in captured_lines}
    assert len(trace_ids) == 1, f"expected ONE trace_id across all lines, got {trace_ids}"
    assert echoed in trace_ids

    # Exactly the edge 'request_received' lines are flagged minted (the gateway edge minted it).
    minted_flags = [
        ln["fields"]["minted"]
        for ln in captured_lines
        if ln["event"] == "request_received" and ln["service"] == "gateway"
    ]
    assert minted_flags == [True], f"edge should mint exactly once, got {minted_flags}"


def test_trace_id_forwarded_to_downstream_hop(captured_lines):
    """The same trace_id reaches the downstream meeting-api hop (forwarded via X-Trace-Id)."""
    _post_create_bot(AUTH)

    services = {ln["service"] for ln in captured_lines}
    assert {"gateway", "meeting-api"} <= services, f"both hops must log; saw {services}"

    downstream = [ln for ln in captured_lines if ln["service"] == "meeting-api"]
    assert downstream, "downstream (meeting-api) hop must emit at least one line"

    edge_trace = next(ln["trace_id"] for ln in captured_lines if ln["service"] == "gateway")
    assert all(ln["trace_id"] == edge_trace for ln in downstream), (
        "downstream lines must carry the SAME trace_id the edge minted — "
        f"got {[ln['trace_id'] for ln in downstream]} vs {edge_trace}"
    )


def test_every_line_shares_one_trace_id_and_conforms(captured_lines):
    """The core assertion: ONE trace_id across every line across every hop, and each line
    conforms to logevent.v1."""
    _post_create_bot(AUTH)
    assert captured_lines, "no log lines captured"

    trace_ids = {ln["trace_id"] for ln in captured_lines}
    assert len(trace_ids) == 1, f"all lines must share one trace_id; got {trace_ids}"

    for ln in captured_lines:
        assert_logevent_conforms(ln)  # raises with a readable message on any violation


def test_incoming_trace_id_is_propagated_not_replaced(captured_lines):
    """If the request ALREADY carries X-Trace-Id, the edge reuses it (does not mint) and it
    threads through every hop — the cross-process continuation case."""
    given = "feedfacecafebeef0123456789abcdef"
    resp = _post_create_bot({**AUTH, TRACE_HEADER: given})
    assert resp.status_code == 201
    assert resp.headers.get(TRACE_HEADER) == given

    assert {ln["trace_id"] for ln in captured_lines} == {given}
    minted = [
        ln["fields"]["minted"]
        for ln in captured_lines
        if ln["event"] == "request_received" and ln["service"] == "gateway"
    ]
    assert minted == [False], "a supplied trace_id must NOT be re-minted"


def test_user_vs_system_audience_split(captured_lines):
    """A user-facing event carries audience=user; a system/debug event carries audience=system."""
    _post_create_bot(AUTH)

    by_event = {}
    for ln in captured_lines:
        by_event.setdefault(ln["event"], set()).add(ln["audience"])

    # Gateway 'request_accepted' and downstream 'bot_join_requested' are USER-facing.
    assert "user" in by_event.get("request_accepted", set()), by_event
    assert by_event.get("bot_join_requested") == {"user"}, by_event
    # The proxy hop + edge receive are SYSTEM/operator events.
    assert by_event.get("downstream_forwarded") == {"system"}, by_event
    assert by_event.get("request_received") == {"system"}, by_event

    # Every line's audience is one of the two contract-allowed values.
    assert {a for s in by_event.values() for a in s} <= {"user", "system"}


def test_freeform_log_line_is_detected_and_fails():
    """A freeform / non-conformant log line (the GAP this work closes) is detected: it does
    NOT validate against logevent.v1, and assert_logevent_conforms raises."""
    # The kind of line the parent services emit today: no trace_id, no audience, freeform msg.
    freeform = {"msg": "callback abc delivered (attempt 1) -> 200", "level": "info"}
    assert not is_conformant_logevent(freeform), "freeform line must be flagged non-conformant"
    with pytest.raises(AssertionError):
        assert_logevent_conforms(freeform)

    # And a line missing ONLY the trace_id (the precise gap) is also rejected.
    no_trace = {
        "ts": "2026-06-21T10:00:00Z",
        "level": "info",
        "service": "gateway",
        "audience": "system",
        "event": "something_happened",
    }
    assert not is_conformant_logevent(no_trace), "a line without trace_id must be rejected"
