"""F161 — a queued message's `turn-accepted` must not wait on the PREVIOUS turn's write-back.

Diagnosed in Vexa-ai/vexa#1508 (not fixed there — "not a one-liner"): `run_message`
(worker/engine.py) ran the write-back phase + `refresh_desk_readme()` + `turn-complete`
synchronously, inline, before returning — so the outer `serve()` loop could not `xread` (let alone
ack) a message that arrived while write-back was still running. Write-back's own budget is
`VEXA_WRITEBACK_MAX_SECONDS=22`, with the phase's own docstring recording 35-37s measured
overshoot, against the dispatcher's `_ACK_DEADLINE_SEC=10` — so any turn that triggered write-back
queued its successor behind a false "no turn-accepted" warning almost every time.

RED before the fix: message 2's `turn-accepted` does not appear until message 1's ENTIRE
write-back phase (blocked here on `release`, standing in for the 22-37s real one) has finished —
the first assertion below times out / fails. GREEN after: message 2 acks within milliseconds,
strictly before any write-back-tagged event, and the write-back is not lost — it still lands,
just as a background trailer that finishes before `serve()` returns.
"""
from __future__ import annotations

import json
import threading
import time

from worker import engine


class FakeStream:
    """A minimal, thread-safe-enough redis-stream fake: `xadd` may be called from the trailer's
    background thread concurrently with the main serve() thread, so both sides take a lock."""

    def __init__(self, inbox):
        self._lock = threading.Lock()
        self.out: list[tuple[str, dict]] = []
        self._inbox = list(inbox)

    def xadd(self, name, fields):
        with self._lock:
            self.out.append((name, fields))
        return str(len(self.out))

    def xread(self, streams, count=1, block=None):
        with self._lock:
            if not self._inbox:
                return []
            eid, fields = self._inbox.pop(0)
        in_topic = next(iter(streams))
        return [(in_topic, [(eid, fields)])]

    def snapshot(self):
        with self._lock:
            return [json.loads(f["event"]) for _t, f in self.out]


def _kinds(stream):
    return [(e.get("type"), e.get("phase"), e.get("turn_id")) for e in stream.snapshot()]


def test_second_messages_ack_does_not_wait_on_the_first_turns_writeback(monkeypatch):
    """The core claim: message 2 is acked while message 1's write-back is still in flight, and the
    write-back is not dropped — it completes and lands before `serve()` (joining its trailers)
    returns."""
    monkeypatch.delenv("VEXA_WRITEBACK", raising=False)

    release = threading.Event()

    def turn(_p):
        # every turn (t0 and t1 both run this) does one tool call + a short answer — fast, never
        # blocks. Only the write-back phase below is deliberately slow.
        yield {"type": "tool-call", "tool": "Read", "args": {}, "callId": "a"}
        yield {"type": "message-delta", "text": "She chairs the DNA TSC."}
        yield {"type": "done", "reply": "She chairs the DNA TSC.", "sessionId": "s1"}

    def writeback(_p):
        # stands in for the real 22-37s budget (`writeback_budget`'s own measured overshoot) —
        # blocked on an Event the test controls, so the assertion below is deterministic, not timed.
        release.wait(timeout=5)
        yield {"type": "tool-call", "tool": "mcp__vexa__entity_upsert", "args": {}, "callId": "b"}
        yield {"type": "done", "reply": "recorded", "sessionId": "s1"}

    # message 2 is already sitting in the in-topic when serve() starts — the boot-anchor / warm
    # delivery path this stands in for is covered by test_boot_drain.py; what matters here is only
    # that it is there to be read the instant the outer loop asks.
    second = ("2-0", {"turn": json.dumps({"type": "message", "prompt": "and Sam Reyes?", "nonce": "n2"})})
    stream = FakeStream([second])

    server = threading.Thread(
        target=engine.serve,
        kwargs=dict(stream=stream, out_topic="out", in_topic="in", turn=turn,
                    start={"entrypoint": {"inline": "who is Olga Avramenko?"}},
                    idle_ms=50, writeback=writeback),
        daemon=True, name="serve-under-test",
    )
    server.start()

    deadline = time.monotonic() + 3.0
    acked_t1 = False
    while time.monotonic() < deadline:
        if ("turn-accepted", None, "t1") in _kinds(stream):
            acked_t1 = True
            break
        time.sleep(0.01)
    assert acked_t1, "message 2's turn-accepted must arrive without waiting on message 1's write-back"

    # ...and it must have arrived BEFORE any write-back-phase event — proving this was not a race
    # that merely happened to resolve in time, but the ack genuinely preceding write-back.
    kinds = _kinds(stream)
    t1_ack_idx = kinds.index(("turn-accepted", None, "t1"))
    wb_idx = next((i for i, k in enumerate(kinds) if k[1] == "writeback"), None)
    assert wb_idx is None, "write-back must still be blocked on `release` at this point"
    assert t1_ack_idx >= 0

    # release message 1's write-back and let serve() run to completion (idle exit joins trailers)
    release.set()
    server.join(timeout=5)
    assert not server.is_alive(), "serve() must exit — idle-exit joins any in-flight trailer"

    kinds = _kinds(stream)
    assert ("tool-call", "writeback", "t0") in kinds, "the write-back was not lost, only deferred"
    assert ("turn-complete", None, "t0") in kinds
    assert ("turn-complete", None, "t1") in kinds
    # and the ordering claim still holds in the FINAL log, now that both turns are done
    t1_ack_idx = kinds.index(("turn-accepted", None, "t1"))
    wb_idx = next(i for i, k in enumerate(kinds) if k[1] == "writeback")
    assert t1_ack_idx < wb_idx


def test_a_worker_with_no_second_message_still_completes_its_writeback_before_exit(monkeypatch):
    """The other half of the same fix: deferring write-back to a background thread must not let
    the process/container exit (TTL-on-idle) while that thread is still running. `serve()`'s
    idle-exit path must join outstanding trailers first, or a daemon thread gets killed mid-commit
    — worse than the 22-37s it was trying to save."""
    monkeypatch.delenv("VEXA_WRITEBACK", raising=False)

    def turn(_p):
        yield {"type": "tool-call", "tool": "Read", "args": {}, "callId": "a"}
        yield {"type": "message-delta", "text": "She chairs the DNA TSC."}
        yield {"type": "done", "reply": "She chairs the DNA TSC.", "sessionId": "s1"}

    ran_writeback = threading.Event()

    def writeback(_p):
        time.sleep(0.15)  # long enough to still be running when the idle xread first comes back empty
        ran_writeback.set()
        yield {"type": "tool-call", "tool": "mcp__vexa__entity_upsert", "args": {}, "callId": "b"}
        yield {"type": "done", "reply": "recorded", "sessionId": "s1"}

    stream = FakeStream([])  # nothing queued — the very next xread is the idle timeout
    engine.serve(stream, out_topic="out", in_topic="in", turn=turn,
                start={"entrypoint": {"inline": "who is Olga Avramenko?"}},
                idle_ms=1, writeback=writeback)

    assert ran_writeback.is_set(), "serve() returned before its own trailer's write-back had run"
    kinds = _kinds(stream)
    assert ("turn-complete", None, "t0") in kinds
