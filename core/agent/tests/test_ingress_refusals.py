"""The dispatch doors refuse in the same shape as every other refusal, and say so in the log.

`/invocations` and `/events` (`routers/ingress.py`) answer a caller they will not hear with a typed
detail — `{status: refused, reason, instruction}` — and log the refusal once, as one structured line.
"""
from __future__ import annotations

import json
import logging

from control_plane import dispatch_sink
from tests.test_dispatch_sink import SECRET, _routine_body, sink  # noqa: F401 — the fixture

LOGGER = "agent_api.ceiling"


def _refusals(caplog) -> list:
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == LOGGER]


def test_an_unauthenticated_dispatch_is_a_typed_refusal(sink, caplog):  # noqa: F811
    client, runtime = sink
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        r = client.post("/invocations", json=_routine_body())
    assert r.status_code == 401
    detail = r.json()["detail"]
    assert detail["status"] == "refused" and detail["reason"] == "internal_tier_required"
    assert detail["instruction"]
    assert [x["reason"] for x in _refusals(caplog)] == ["internal_tier_required"]
    assert runtime.spawned == []


def test_a_signed_job_asking_for_a_person_is_a_typed_refusal(sink, caplog):  # noqa: F811
    client, runtime = sink
    body = _routine_body(trigger="message")
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        r = client.post("/invocations", json=body,
                        headers={dispatch_sink.HEADER: dispatch_sink.sign(SECRET, body)})
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "signed_dispatch_runs_unwatched"
    assert [x["reason"] for x in _refusals(caplog)] == ["signed_dispatch_runs_unwatched"]
    assert runtime.spawned == []


def test_an_unauthenticated_event_is_a_typed_refusal(sink, caplog):  # noqa: F811
    client, runtime = sink
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        r = client.post("/events", json={"type": "x"})
    assert r.status_code == 401
    assert r.json()["detail"]["reason"] == "internal_tier_required"
    assert [x["reason"] for x in _refusals(caplog)] == ["internal_tier_required"]
    assert runtime.spawned == []
