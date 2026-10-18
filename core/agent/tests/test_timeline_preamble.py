"""Temporal awareness in context, every turn — PRD decision 31 §1 (the agent half).

What the flows route renders is proven in `core/flows/tests/test_timeline_render.py`. What is
proven here is the part this service owns: that the block reaches the turn prompt, that it costs
one call a minute, and that nothing about it can fail a turn.
"""
from __future__ import annotations

import pytest

from shared import timeline as T
from worker import engine

BLOCK = ("## Where this person is in time\n\n**Now: Wednesday 02 September 2026, 16:20 WEST.** "
         "State times in this zone.\n\nLast events concerning them:\n"
         "  12:23 WEST  invite.received  ASWF DNA TSC\n")


@pytest.fixture(autouse=True)
def _clean_cache():
    T.invalidate()
    yield
    T.invalidate()


def _configured(monkeypatch):
    monkeypatch.setenv("VEXA_FLOWS_API_URL", "http://flows:18200")
    monkeypatch.setenv("VEXA_FLOWS_TIMELINE_KEY", "a-read-only-key")
    monkeypatch.setenv("VEXA_OWNER", "126")


# ── the subject ──────────────────────────────────────────────────────────────────────────────────

def test_the_subject_is_the_dispatch_owner(monkeypatch):
    monkeypatch.setenv("VEXA_OWNER", "126")
    monkeypatch.setenv("VEXA_PRINCIPAL_EMAIL", "admin@vexa.ai")
    assert T.subject() == "126"


def test_the_principal_address_is_the_fallback(monkeypatch):
    monkeypatch.delenv("VEXA_OWNER", raising=False)
    monkeypatch.setenv("VEXA_PRINCIPAL_EMAIL", "admin@vexa.ai")
    assert T.subject() == "admin@vexa.ai"


# ── the degrade path — every branch of it returns "" and none of them raises ─────────────────────

def test_no_route_configured_means_no_block(monkeypatch):
    monkeypatch.delenv("VEXA_FLOWS_API_URL", raising=False)
    monkeypatch.setenv("VEXA_FLOWS_TIMELINE_KEY", "k")
    assert T.fetch("126") == ""


def test_no_key_means_no_block(monkeypatch):
    """A worker with no credential behaves exactly as it did before the timeline existed."""
    monkeypatch.setenv("VEXA_FLOWS_API_URL", "http://flows:18200")
    monkeypatch.delenv("VEXA_FLOWS_TIMELINE_KEY", raising=False)
    monkeypatch.delenv("VEXA_FLOWS_API_KEY", raising=False)
    assert T.fetch("126") == ""


def test_no_subject_means_no_block(monkeypatch):
    monkeypatch.delenv("VEXA_OWNER", raising=False)
    monkeypatch.delenv("VEXA_PRINCIPAL_EMAIL", raising=False)
    assert T.timeline_preamble() == ""


def test_a_route_that_is_down_costs_the_turn_nothing(monkeypatch):
    _configured(monkeypatch)
    monkeypatch.setattr(T.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("connection refused")))
    assert T.timeline_preamble() == ""


def test_a_route_that_answers_nonsense_is_ignored_not_pasted(monkeypatch):
    _configured(monkeypatch)
    monkeypatch.setattr(T, "fetch", lambda uid, **kw: "")
    assert T.timeline_preamble("126") == ""


# ── the cache ────────────────────────────────────────────────────────────────────────────────────

def test_one_call_a_minute_however_many_turns(monkeypatch):
    _configured(monkeypatch)
    calls = []
    monkeypatch.setattr(T, "fetch", lambda uid, **kw: calls.append(uid) or BLOCK)
    assert T.timeline_preamble("126", now=1000.0) == BLOCK
    assert T.timeline_preamble("126", now=1030.0) == BLOCK
    assert calls == ["126"]


def test_the_block_refreshes_after_the_ttl(monkeypatch):
    _configured(monkeypatch)
    calls = []
    monkeypatch.setattr(T, "fetch", lambda uid, **kw: calls.append(uid) or BLOCK)
    T.timeline_preamble("126", now=1000.0)
    T.timeline_preamble("126", now=1000.0 + T.CACHE_TTL_S + 1)
    assert len(calls) == 2


def test_a_failure_is_cached_too(monkeypatch):
    """A flows-api that is down must cost this worker one timeout a minute, not one a turn."""
    _configured(monkeypatch)
    calls = []
    monkeypatch.setattr(T, "fetch", lambda uid, **kw: calls.append(uid) or "")
    assert T.timeline_preamble("126", now=1000.0) == ""
    assert T.timeline_preamble("126", now=1030.0) == ""
    assert calls == ["126"]


def test_two_people_do_not_share_a_block(monkeypatch):
    _configured(monkeypatch)
    monkeypatch.setattr(T, "fetch", lambda uid, **kw: f"block for {uid}")
    assert T.timeline_preamble("126", now=1000.0) == "block for 126"
    assert T.timeline_preamble("204", now=1000.0) == "block for 204"


def test_invalidate_forgets_one_or_all(monkeypatch):
    _configured(monkeypatch)
    seen = []
    monkeypatch.setattr(T, "fetch", lambda uid, **kw: seen.append(uid) or BLOCK)
    T.timeline_preamble("126", now=1000.0)
    T.invalidate("126")
    T.timeline_preamble("126", now=1000.0)
    assert len(seen) == 2


# ── it reaches the turn ──────────────────────────────────────────────────────────────────────────

def test_the_block_ships_on_the_turn_prompt(tmp_path, monkeypatch):
    """Decision 31 §1 is *in context every turn*. A sense of now that arrives only when asked for
    is the lookup the decision replaced."""
    seen = {}

    def fake_run(work, prompt, harness, **kw):
        seen["prompt"] = prompt
        yield {"type": "done", "reply": "ok", "sessionId": "s"}

    monkeypatch.setattr(engine, "run_harness_turn", fake_run)
    monkeypatch.setattr(engine, "active_mounts",
                        lambda: [{"slug": "desk-1", "path": str(tmp_path), "write": True,
                                  "primary": True}])
    monkeypatch.setattr(engine, "_ensure_repo", lambda w: None)
    monkeypatch.setattr(engine, "timeline_preamble", lambda: BLOCK)

    class H:
        def prepare(self, work, chat_root=None):
            pass

        def transcript_bytes(self, work, sid):
            return 0

    list(engine.run_turn_over_workspace(tmp_path, "hello", harness=H(), commit=False))
    assert "Where this person is in time" in seen["prompt"]
    assert "16:20 WEST" in seen["prompt"]


def test_a_turn_with_no_timeline_is_still_a_turn(tmp_path, monkeypatch):
    seen = {}

    def fake_run(work, prompt, harness, **kw):
        seen["prompt"] = prompt
        yield {"type": "done", "reply": "ok", "sessionId": "s"}

    monkeypatch.setattr(engine, "run_harness_turn", fake_run)
    monkeypatch.setattr(engine, "active_mounts",
                        lambda: [{"slug": "desk-1", "path": str(tmp_path), "write": True,
                                  "primary": True}])
    monkeypatch.setattr(engine, "_ensure_repo", lambda w: None)
    monkeypatch.setattr(engine, "timeline_preamble", lambda: "")

    class H:
        def prepare(self, work, chat_root=None):
            pass

        def transcript_bytes(self, work, sid):
            return 0

    list(engine.run_turn_over_workspace(tmp_path, "hello", harness=H(), commit=False))
    assert "hello" in seen["prompt"] and "Where this person is in time" not in seen["prompt"]
