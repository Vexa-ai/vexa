"""WP-M9 workstream B, incident (1) — the outbox drain vs. the Hub's permanent refusals.

The Hub answers 409 ``{failure:"invalid_state"}`` for a stale NON-terminal callback event once
the capture is terminal — correct server behavior. The engine's drain treated EVERY non-2xx as
retryable, so those events were retried forever: attempts observed at 97 live, six orphaned
events settled by hand in the staging DB, and every event queued behind them (including the
terminal settlements erasure waits on) starved.

Contract under test (``ControlCallbackDispatcher.drain_once``):
  * 2xx with the sealed ack body ⇒ delivered;
  * PERMANENT client refusals (409, 410, 422) ⇒ dead-lettered — marked delivered EXACTLY once,
    with exactly ONE ``log.warning`` carrying the event_id + status + reason, and the drain
    proceeds past them to later events;
  * everything else (5xx, transport errors, a 2xx without the ack) keeps the retry behavior
    (``mark_callback_failed`` — the attempts bookkeeping is unchanged);
  * a retryable failure BARS the same capture's later events for the rest of the sweep — the
    batch is lifecycle-ordered per capture, so POSTing a successor past its still-pending
    predecessor earns a transition-skip 409 the dead-letter lane would misread as staleness;
  * a 409 dead-letters only when it is a verdict on the event, not on its arrival order: the
    event is non-terminal AND its local capture can no longer advance (terminal, or gone).  A
    terminal settlement never 409-dead-letters — erasure gates on its delivery.

Driven with the real dispatcher over ``InMemoryControlStore`` and a scripted ``httpx.AsyncClient``
substitute — no network.
"""
from __future__ import annotations

import logging

import httpx
import pytest

from meeting_api.zaki_control.callbacks import ControlCallbackDispatcher
from meeting_api.zaki_control.fakes import InMemoryControlStore
from meeting_api.zaki_control.ports import CallbackEvent, Capture, Subject

_LOGGER = "meeting_api.zaki_control.callbacks"


class _RecordingStore(InMemoryControlStore):
    """The fake store plus mark-call counters, so retry-vs-dead-letter bookkeeping is observable."""

    def __init__(self):
        super().__init__()
        self.delivered_marks: list[str] = []
        self.failed_marks: list[str] = []

    async def mark_callback_delivered(self, event_id):
        self.delivered_marks.append(event_id)
        return await super().mark_callback_delivered(event_id)

    async def mark_callback_failed(self, event_id):
        self.failed_marks.append(event_id)
        return await super().mark_callback_failed(event_id)


class _Response:
    def __init__(self, status_code: int, body=None):
        self.status_code = status_code
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no json body")
        return self._body


class _ScriptedClient:
    """Stands in for ``httpx.AsyncClient``: pops one scripted item per POST (a response, or an
    exception to raise as a transport failure) and records every POST made."""

    def __init__(self, script: list, posts: list):
        self._script = script
        self._posts = posts

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, content=None, headers=None):
        self._posts.append({"url": url, "content": content, "headers": headers})
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _patch_httpx(monkeypatch, script: list) -> list:
    posts: list = []
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: _ScriptedClient(script, posts))
    return posts


def _ack(event_id: str) -> _Response:
    return _Response(200, {
        "api_version": "zaki-control.v1", "event_id": event_id, "status": "accepted",
    })


def _seeded_dispatcher(event_ids: list[str]):
    store = _RecordingStore()
    for event_id in event_ids:
        store.callbacks[event_id] = CallbackEvent(
            event_id=event_id,
            body={
                "event_id": event_id,
                "event_type": "minutes.capture.status",
                "api_version": "zaki-control.v1",
                "created_at": "2026-07-21T00:00:00+00:00",
                "data": {"capture_id": "cap-1", "state": "joining"},
            },
            subject=Subject("tenant-1", "42"),
            capture_id="cap-1",
        )
    dispatcher = ControlCallbackDispatcher(
        store,
        callback_url="https://hub.example/api/minutes/callback/v1",
        hmac_key="hub-callback-hmac-key-0123456789abcdef",
    )
    return store, dispatcher


def _dead_letter_warnings(caplog):
    return [
        r for r in caplog.records
        if r.name == _LOGGER and r.levelno == logging.WARNING and "dead-lettered" in r.getMessage()
    ]


async def test_hub_409_dead_letters_the_stale_event_once_and_the_drain_proceeds(monkeypatch, caplog):
    """The live wedge: a stale non-terminal event 409s forever, starving everything behind it.
    It must be marked delivered (dead) exactly once — never retried — and the later event in the
    same batch must still be delivered."""
    store, dispatcher = _seeded_dispatcher(["stale-1", "live-2"])
    posts = _patch_httpx(monkeypatch, [
        _Response(409, {"failure": "invalid_state"}),
        _ack("live-2"),
    ])

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        delivered = await dispatcher.drain_once()

    assert delivered == 1, "only the acknowledged event counts as delivered"
    assert store.delivered_marks == ["stale-1", "live-2"], "the dead event is marked, the drain continues"
    assert store.failed_marks == [], "a permanent refusal must not touch the retry path"
    assert await store.pending_callbacks(limit=50) == (), "nothing left to retry"

    warnings = _dead_letter_warnings(caplog)
    assert len(warnings) == 1, "exactly ONE warning per dead-lettered event"
    message = warnings[0].getMessage()
    assert "stale-1" in message and "409" in message
    assert "hub refused as invalid_state — event is stale, not retryable" in message

    # A second sweep never re-POSTs the dead event: 97-attempt loops are structurally impossible.
    assert await dispatcher.drain_once() == 0
    assert len(posts) == 2


@pytest.mark.parametrize("status", [410, 422])
async def test_other_permanent_client_refusals_dead_letter_too(monkeypatch, caplog, status):
    store, dispatcher = _seeded_dispatcher(["perm-1"])
    _patch_httpx(monkeypatch, [_Response(status, {"failure": "refused"})])

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await dispatcher.drain_once()

    assert store.delivered_marks == ["perm-1"]
    assert store.failed_marks == []
    assert await store.pending_callbacks(limit=50) == ()
    warnings = _dead_letter_warnings(caplog)
    assert len(warnings) == 1 and str(status) in warnings[0].getMessage()


async def test_hub_503_keeps_the_retry_path(monkeypatch, caplog):
    """A 5xx carries no permanence verdict — the event stays pending and a later sweep delivers it."""
    store, dispatcher = _seeded_dispatcher(["flaky-1"])
    script = [_Response(503, {"failure": "unavailable"})]
    _patch_httpx(monkeypatch, script)

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        assert await dispatcher.drain_once() == 0

    assert store.failed_marks == ["flaky-1"], "attempts bookkeeping unchanged: one failed mark"
    assert store.delivered_marks == []
    assert len(await store.pending_callbacks(limit=50)) == 1, "still pending for the next sweep"
    assert _dead_letter_warnings(caplog) == [], "a retryable failure is not a dead-letter"

    script.append(_ack("flaky-1"))
    assert await dispatcher.drain_once() == 1
    assert store.delivered_marks == ["flaky-1"]


async def test_transport_errors_keep_the_retry_path(monkeypatch):
    store, dispatcher = _seeded_dispatcher(["conn-1"])
    _patch_httpx(monkeypatch, [httpx.ConnectError("connection refused")])

    assert await dispatcher.drain_once() == 0

    assert store.failed_marks == ["conn-1"]
    assert store.delivered_marks == []
    assert len(await store.pending_callbacks(limit=50)) == 1


async def test_a_2xx_without_the_sealed_ack_still_retries(monkeypatch):
    """Pins the pre-existing contract: a non-acknowledging 2xx is retryable, not success —
    the permanent-refusal lane must not have widened it."""
    store, dispatcher = _seeded_dispatcher(["noack-1"])
    _patch_httpx(monkeypatch, [
        _Response(200, {"api_version": "zaki-control.v1", "event_id": "other", "status": "accepted"}),
    ])

    assert await dispatcher.drain_once() == 0

    assert store.failed_marks == ["noack-1"]
    assert store.delivered_marks == []
    assert len(await store.pending_callbacks(limit=50)) == 1


# --- the ordering barrier and the 409 staleness gate (panel finding: a transient 503 ahead of a
# --- transition-skip 409 must not convert an out-of-order delivery into a lost event) -----------


_SUBJECT = Subject("tenant-1", "42")


def _capture(capture_id: str, state: str) -> Capture:
    return Capture(
        capture_id=capture_id, subject=_SUBJECT, operation_id=f"op-{capture_id}",
        reservation_id=f"res-{capture_id}", platform="google_meet",
        native_meeting_id=f"native-{capture_id}", meeting_id="7", state=state,
    )


def _event(event_id: str, capture_id: str, state: str, *, terminal: bool = False) -> CallbackEvent:
    return CallbackEvent(
        event_id=event_id,
        body={
            "event_id": event_id,
            "event_type": "minutes.capture.usage" if event_id.startswith("usage-") else "minutes.capture.status",
            "api_version": "zaki-control.v1",
            "created_at": "2026-07-21T00:00:00+00:00",
            "data": {"capture_id": capture_id, "state": state},
        },
        subject=_SUBJECT,
        capture_id=capture_id,
        terminal=terminal,
    )


def _dispatcher(store):
    return ControlCallbackDispatcher(
        store,
        callback_url="https://hub.example/api/minutes/callback/v1",
        hmac_key="hub-callback-hmac-key-0123456789abcdef",
    )


async def test_retryable_failure_bars_the_same_captures_later_events_for_the_sweep(monkeypatch, caplog):
    """The out-of-order wedge: E1 (joining) 503s, and pre-fix the drain POSTed E2 (active) into a
    Hub still at ``requested`` — a transition-skip 409 the dead-letter lane killed forever.  E2
    must be SKIPPED (no POST, no attempt) while an unrelated capture's event still flows."""
    store = InMemoryControlStore()
    store.captures["cap-1"] = _capture("cap-1", "requested")
    for event in (
        _event("e1-joining", "cap-1", "joining"),
        _event("e2-active", "cap-1", "active"),
        _event("e3-other", "cap-2", "joining"),
    ):
        store.callbacks[event.event_id] = event
    dispatcher = _dispatcher(store)
    script = [_Response(503, {"failure": "unavailable"}), _ack("e3-other")]
    posts = _patch_httpx(monkeypatch, script)

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        assert await dispatcher.drain_once() == 1

    assert len(posts) == 2, "e2 was never POSTed — the barrier skipped it, e3 (cap-2) still ran"
    assert [p["content"] is not None for p in posts] == [True, True]
    assert {e.event_id for e in await store.pending_callbacks(limit=50)} == {"e1-joining", "e2-active"}
    assert _dead_letter_warnings(caplog) == [], "nothing dead-lettered by a transient outage"

    # Next sweep the Hub is back: both land, in lifecycle order.
    script.extend([_ack("e1-joining"), _ack("e2-active")])
    assert await dispatcher.drain_once() == 2
    assert await store.pending_callbacks(limit=50) == ()
    assert b"e1-joining" in posts[2]["content"] and b"e2-active" in posts[3]["content"]


async def test_transient_failure_then_409_never_loses_the_terminal_settlement(monkeypatch, caplog):
    """Panel probe 2, the worst case: the terminal pair (status-completed + usage-1) is written in
    one transaction.  A 503 on the status event must bar the settlement for the sweep — pre-fix it
    was POSTed, 409-skip-refused, dead-lettered, and ``terminal_callbacks_delivered`` then opened
    the erasure gate on seconds the Hub never settled."""
    store = InMemoryControlStore()
    capture = _capture("cap-9", "active")
    store.captures["cap-9"] = capture
    await store.record_capture_transition(
        capture=capture, state="completed", failure_code=None,
        events=(
            _event("status-cap-9-completed", "cap-9", "completed", terminal=True),
            _event("usage-cap-9-1", "cap-9", "completed", terminal=True),
        ),
    )
    dispatcher = _dispatcher(store)
    script = [_Response(503, {"failure": "unavailable"})]
    posts = _patch_httpx(monkeypatch, script)

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        assert await dispatcher.drain_once() == 0

    assert len(posts) == 1, "the settlement was never POSTed behind its failed predecessor"
    assert not await store.terminal_callbacks_delivered("cap-9"), "the erasure gate stays CLOSED"
    assert _dead_letter_warnings(caplog) == []

    script.extend([_ack("status-cap-9-completed"), _ack("usage-cap-9-1")])
    assert await dispatcher.drain_once() == 2
    assert await store.terminal_callbacks_delivered("cap-9"), "settled — now erasure may proceed"


async def test_409_while_the_local_capture_still_advances_is_retried_not_dead_lettered(monkeypatch, caplog):
    """A 409 earned across sweeps (predecessor dead or delayed out-of-band) is an ordering signal
    while the local capture is non-terminal: retry it — one sweep later the Hub accepts."""
    store = InMemoryControlStore()
    store.captures["cap-1"] = _capture("cap-1", "joining")
    event = _event("e-active", "cap-1", "active")
    store.callbacks[event.event_id] = event
    dispatcher = _dispatcher(store)
    script = [_Response(409, {"failure": "invalid_state"})]
    _patch_httpx(monkeypatch, script)

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        assert await dispatcher.drain_once() == 0

    assert len(await store.pending_callbacks(limit=50)) == 1, "still pending — attempts bumped, not dead"
    assert _dead_letter_warnings(caplog) == []

    script.append(_ack("e-active"))
    assert await dispatcher.drain_once() == 1
    assert await store.pending_callbacks(limit=50) == ()


async def test_409_on_a_stale_event_of_a_locally_terminal_capture_still_dead_letters(monkeypatch, caplog):
    """Incident (1) exactly: capture completed locally, a stale non-terminal step 409s — that IS
    the permanent shape, and it must dead-letter, not resurrect the 97-attempt loop."""
    store = InMemoryControlStore()
    store.captures["cap-1"] = _capture("cap-1", "completed")
    event = _event("stale-joining", "cap-1", "joining")
    store.callbacks[event.event_id] = event
    dispatcher = _dispatcher(store)
    _patch_httpx(monkeypatch, [_Response(409, {"failure": "invalid_state"})])

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await dispatcher.drain_once()

    assert await store.pending_callbacks(limit=50) == ()
    warnings = _dead_letter_warnings(caplog)
    assert len(warnings) == 1 and "stale-joining" in warnings[0].getMessage()


async def test_409_on_a_terminal_settlement_never_dead_letters(monkeypatch, caplog):
    """The settlement is the one event whose loss silently un-meters compute AND unlocks erasure:
    a 409 on it always retries, keeping ``terminal_callbacks_delivered`` honest."""
    store = InMemoryControlStore()
    capture = _capture("cap-9", "active")
    store.captures["cap-9"] = capture
    await store.record_capture_transition(
        capture=capture, state="completed", failure_code=None,
        events=(_event("usage-cap-9-1", "cap-9", "completed", terminal=True),),
    )
    dispatcher = _dispatcher(store)
    _patch_httpx(monkeypatch, [
        _Response(409, {"failure": "invalid_state"}),
        _Response(409, {"failure": "invalid_state"}),
    ])

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        assert await dispatcher.drain_once() == 0
        assert await dispatcher.drain_once() == 0

    assert len(await store.pending_callbacks(limit=50)) == 1
    assert not await store.terminal_callbacks_delivered("cap-9"), "the erasure gate stays closed"
    assert _dead_letter_warnings(caplog) == []
