"""F212 — an AD HOC meeting's mail must not depend on `refs["organizer"]`.

Found live on the dogfood stack, 2026-09-03, 18:31Z: since meeting-api publishes
`meeting.completed` for every meeting (`Vexa-ai/vexa#1502`), a bot started AD HOC — `POST /bots`
or the MCP tool, no calendar invite in sight — now reaches `post_meeting` too, and its
`meeting.completed` refs carry only `{admitted_by, completion_reason, meeting_id, native,
platform, uid}` (`meeting_api.events.meeting_completed_refs`) — no `organizer`, by design: the
person who dispatched the bot IS `uid`.

`email_minutes` read `ctx.refs["organizer"]` unconditionally at two call sites and raised
`KeyError('organizer')` on every attempt, retried to R-B23's ceiling, and failed — the no-agents
product's own promise (after a meeting: "meeting ended, transcript ready" mail to the person)
never reached the one person who dispatched the bot. `drop_to_attendees` degraded more quietly:
`ctx.refs.get("organizer") or "the organiser"` handed the literal string "the organiser" to
`ensure_platform_user` as if it were an address.

Four properties this file holds, all against `flows_defs.production`:

  1. no organizer, a resolvable uid → `email_minutes` mails the person BEHIND `uid`.
  2. organizer present on the ref → unchanged: the calendar-invite path never even calls the
     uid → address lookup.
  3. neither resolves → a typed, NON-RETRYING failure naming the uid, never a bare `KeyError`.
  4. no attendees (an ad hoc meeting has none) → `email_attendees` no-ops with a typed reason,
     and `drop_to_attendees` still completes — dropping to the ad hoc room's one desk, the
     dispatcher's own, resolved the same way, and never to the placeholder string
     "the organiser".

No network, no clock, no DB: steps are called directly with the refs their flow would hand them,
same rig `test_link_loop.py` and `test_attendee_mail_shape.py` already use.
"""
from __future__ import annotations

import sys
from pathlib import Path

import flows_defs.production as production
import flows_steps.mailtext as mailtext
import pytest
from flows import Done, StepError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from test_link_loop import FakeScaffolds, _ctx, _rig  # noqa: E402


@pytest.fixture(autouse=True)
def scaffolds(monkeypatch):
    """Every production touch mints a scaffold before it sends; this stands in for agent-api."""
    fake = FakeScaffolds()
    monkeypatch.setattr(production, "mint_scaffold", fake)
    return fake


@pytest.fixture(autouse=True)
def _no_admin_mail_override(monkeypatch):
    """The live mail-head reader is stubbed — see `test_link_loop`'s identical fixture for why a
    test that skips this can pass for the wrong reason (a neighbouring stack's 404)."""
    monkeypatch.setattr(mailtext, "ws_file", lambda *_a, **_k: None)


# `admitted_by`/`completion_reason`/`platform` ride along on the real event (`meeting_api.events.
# meeting_completed_refs`) but no step reads them; they are omitted here because the shape under
# test is "no organizer", not "every field a real event carries".
ADHOC_REFS = {"uid": "7", "meeting_id": 41, "title": "Ad hoc sync", "native": "abc123"}
PRIOR = {"process_meeting": {"report": "## Decided\n- ship it", "group": ""}}


# ── (1) no organizer, resolvable uid → mail goes to the dispatcher ─────────────────────────────
def test_email_minutes_mails_the_dispatcher_when_there_is_no_organizer(monkeypatch, scaffolds):
    reg, ch = _rig()
    monkeypatch.setattr(production, "setting", lambda uid, key: True)
    monkeypatch.setattr(production, "address_for_uid",
                        lambda uid: "dispatcher@bank.test" if uid == "7" else "")
    out = reg.steps["email_minutes"](_ctx(dict(ADHOC_REFS), PRIOR))
    assert isinstance(out, Done)
    assert len(ch.sent) == 1
    msg = ch.sent[0]
    assert msg["to"] == "dispatcher@bank.test"
    assert "## Decided" in msg["body"]                    # the note still travels VERBATIM
    rec = scaffolds.for_("dispatcher@bank.test")
    assert (rec["kind"], rec["opening"]) == ("post-meeting", "minutes-review")


# ── (2) organizer present → unchanged, and the uid lookup is never even consulted ──────────────
def test_email_minutes_prefers_the_calendar_organizer_when_one_is_on_the_ref(monkeypatch, scaffolds):
    reg, ch = _rig()
    monkeypatch.setattr(production, "setting", lambda uid, key: True)
    looked_up = []
    monkeypatch.setattr(production, "address_for_uid",
                        lambda uid: looked_up.append(uid) or "should-not-be-used@x.test")
    refs = dict(ADHOC_REFS, organizer="anna@bank.test")
    out = reg.steps["email_minutes"](_ctx(refs, PRIOR))
    assert isinstance(out, Done)
    assert ch.sent[0]["to"] == "anna@bank.test"
    assert looked_up == []             # the ref answered it — the fallback lookup never ran
    assert scaffolds.for_("anna@bank.test")


# ── (3) neither resolves → typed, non-retrying, names the uid — never a KeyError ───────────────
def test_email_minutes_fails_typed_and_non_retrying_when_neither_resolves(monkeypatch, scaffolds):
    reg, ch = _rig()
    monkeypatch.setattr(production, "setting", lambda uid, key: True)
    monkeypatch.setattr(production, "address_for_uid", lambda uid: "")
    with pytest.raises(StepError) as ei:
        reg.steps["email_minutes"](_ctx(dict(ADHOC_REFS), PRIOR))
    assert ei.value.retryable is False
    assert "7" in str(ei.value)                            # names the uid an operator can act on
    assert ch.sent == []                                   # never sent to nobody


# ── (4) no attendees → email_attendees no-ops, drop_to_attendees still completes ────────────────
def test_no_attendees_no_ops_the_fanout_and_still_drops_the_dispatchers_own_desk(
        monkeypatch, scaffolds):
    reg, ch = _rig()
    monkeypatch.setattr(production, "setting", lambda uid, key: True)
    monkeypatch.setattr(production, "address_for_uid",
                        lambda uid: "dispatcher@bank.test" if uid == "7" else "")
    minted_for = []
    monkeypatch.setattr(production, "ensure_platform_user",
                        lambda email: minted_for.append(email) or "99")
    monkeypatch.setattr(production, "ws_file", lambda *_a, **_k: None)
    monkeypatch.setattr(production.ag, "workspace_init", lambda *_a, **_k: None)
    monkeypatch.setattr(production.ag, "workspace_write", lambda *_a, **_k: None)

    out_a = reg.steps["email_attendees"](_ctx(dict(ADHOC_REFS), PRIOR))
    assert isinstance(out_a, Done)
    assert out_a.result["sent"] == 0
    assert out_a.result.get("skipped")                     # typed reason, not a silent zero
    assert ch.sent == []

    out_d = reg.steps["drop_to_attendees"](_ctx(
        dict(ADHOC_REFS), {**PRIOR, "email_attendees": out_a.result}))
    assert isinstance(out_d, Done)                          # completes — no exception
    assert out_d.result["dropped"] == 1                     # the one desk in an ad hoc room: uid's
    assert out_d.result["failed"] == []
    assert minted_for == ["dispatcher@bank.test"]            # never the string "the organiser"
