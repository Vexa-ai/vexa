"""F45 — a chat turn is delivered or refused, never silently dropped.

⚠ 2026-09-02, the founder's own session. He sent "and share it with dmitry@vexa.ai" twice into a
session whose worker was mid-turn (a ten-step research run). Both POST /api/chat returned 200.
Neither became a turn: the history holds four turns, the screen showed three of his messages, and
nothing anywhere recorded a loss. He watched a reply appear and reasonably believed it answered
what he had just sent.

The mechanism was one swallowed exception. `_predeliver` XADDs the prompt to the worker's in-topic
and its failure path read "warm delivery must never break a dispatch — relying on the spawn path".
That is true for a COLD unit, where the spawn carries the prompt as its entrypoint. It is false for
a WARM one: `spawn` on a live unit is a touch, there is no entrypoint, and the XADD was the only
delivery the message would ever get.

So the verdict depends on the unit, and the tests below are that distinction.
"""
from __future__ import annotations

import pytest

from control_plane import dispatch as dispatch_mod


class _BoomRedis:
    """A redis that is configured and unreachable — a blip, or a test topology."""

    def xrevrange(self, *a, **kw):
        raise ConnectionError("redis is gone")

    def xadd(self, *a, **kw):
        raise ConnectionError("redis is gone")


def _invocation(prompt="and share it with dmitry@vexa.ai"):
    return {"trigger": "message", "start": {"entrypoint": {"inline": prompt}}}


def test_a_failed_delivery_is_reported_not_swallowed():
    d = object.__new__(dispatch_mod.Dispatcher)
    d._redis = lambda: _BoomRedis()
    d._warm_fail = lambda: None
    assert d._predeliver("u1", _invocation(), "u1:1") is dispatch_mod._DELIVERY_FAILED


def test_nothing_to_deliver_is_not_a_failure():
    d = object.__new__(dispatch_mod.Dispatcher)
    d._redis = lambda: _BoomRedis()
    d._warm_fail = lambda: None
    # a session-only start has no inline prompt; that is None, and None is not the sentinel
    assert d._predeliver("u1", {"trigger": "message", "start": {}}, "u1:1") is None


def test_no_redis_at_all_is_a_topology_not_a_loss():
    # Without redis there is no warm path anywhere, and the spawn genuinely carries the prompt.
    # Raising here would break every deployment that has no warm delivery to begin with.
    d = object.__new__(dispatch_mod.Dispatcher)
    d._redis = lambda: None
    assert d._predeliver("u1", _invocation(), "u1:1") is None


def test_the_sentinel_is_not_None_so_the_two_cases_cannot_be_confused():
    # The whole defect was one value standing for both "nothing to send" and "sending failed".
    assert dispatch_mod._DELIVERY_FAILED is not None
    assert dispatch_mod._DELIVERY_FAILED is not False


def test_WarmDeliveryFailed_exists_and_says_what_it_means():
    assert issubclass(dispatch_mod.WarmDeliveryFailed, RuntimeError)
    doc = dispatch_mod.WarmDeliveryFailed.__doc__ or ""
    assert "only delivery" in doc or "loses the person" in doc
