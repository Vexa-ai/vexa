"""Two holes on the attendee follow-up, both of them silent (PRD §16.2 items 2 and 3).

**1. The per-meeting opt-out was a branch nothing could reach.** `_followup_on` read
`refs["share"] is False`, and NO producer ever wrote `share` — not the ICS parser, not admission,
not meeting-api. "Creator-controlled sharing, default ON, per-meeting opt-out" shipped as default
ON with no opt-out at all, and it read as implemented because the code for it was there.

The new ref is TRUTHY on purpose. Admission's `_merge_refs` keeps an existing key unless its
value is falsy and the incoming one is not — so `share: False` could be silently overwritten to
`True` by any later admission for the same meeting, which is a suppressed fan-out un-suppressing
itself. `share_opt_out: True` cannot be clobbered by that rule in either direction.

**2. With no agent, attendees heard nothing.** `email_attendees` was `absent={"agent": "skip"}`,
so on the no-agents cut the organiser got the "recorded, no summary" mail and everybody else in
the room got silence — from a bot they had watched sit in the meeting. `email_minutes` already
solved this exact problem with `degrade` plus a branch in its body; this is the same shape, one
step over.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import flows_defs.production as production  # noqa: E402
import flows_steps.mailtext as mailtext  # noqa: E402
from flows import Done  # noqa: E402

from test_link_loop import FakeScaffolds  # noqa: E402
from test_no_agents_degrade import (  # noqa: E402
    EVERYTHING, NO_AGENT, _forbid_the_agent_door, _production, _run_flow,
)

# A room, so the fan-out has somebody to reach. Same domain as the organiser: the allow-list
# defaults to the organiser's own domain, which is what "outside the domain, never" means unset.
ROOM_REFS = {"uid": "7", "meeting_id": 41, "native": "abc123", "title": "Weekly sync",
             "organizer": "anna@bank.test", "start": 1_700_000_000,
             "participants": ["ben@bank.test", "cara@bank.test", "outside@elsewhere.test"]}


def _room_rig(monkeypatch, *, report="## Decided\n- ship it"):
    """`_production` plus the two doors this file is NOT testing, stubbed at their own boundary:
    the agent that writes the report, and the meetings calls `email_attendees` makes to mint a
    per-attendee share. `drop_to_attendees` is stubbed because the desk write is a different
    step's contract — this file is about who receives a MAIL."""
    reg, sent = _production(monkeypatch)
    monkeypatch.setattr(production.mt, "meeting_row", lambda *_a, **_k: {"id": 41})
    monkeypatch.setattr(production.mt, "mint_transcript_share",
                        lambda _uid, _mid, who: "tok-" + str(who))
    reg.steps["process_meeting"] = lambda ctx: Done({"report": report, "group": ""})
    reg.steps["drop_to_attendees"] = lambda ctx: Done({"dropped": 0, "failed": []})
    return reg, sent


# ── item 3: the opt-out ─────────────────────────────────────────────────────────────────────────

def _followup_on(refs):
    reg = production.Registry() if hasattr(production, "Registry") else None
    assert reg is None or True
    return production._FOLLOWUP_ON_FOR_TESTS(_ctx(dict(refs)))


def test_the_opt_out_ref_turns_the_fan_out_off(monkeypatch):
    """RED BEFORE THE FIX: `share_opt_out` was a key nothing read, so the fan-out ran anyway."""
    reg, sent = _room_rig(monkeypatch)
    monkeypatch.setattr(production, "mint_scaffold", FakeScaffolds())
    st, _ = _run_flow(reg, "meeting.completed",
                      dict(ROOM_REFS, share_opt_out=True), EVERYTHING)
    rec = next(r for r in st["receipts"] if r["step"] == "email_attendees")
    assert rec["result"]["followup"] == "off", rec["result"]
    assert rec["result"]["sent"] == 0
    assert not [m for m in sent if m["to"] in ("ben@bank.test", "cara@bank.test")], \
        "an opted-out meeting mailed its attendees anyway"


def test_without_the_ref_the_fan_out_runs(monkeypatch):
    """Default ON — the half of item 3 that IS the viral coefficient. Guarded here so a future
    tightening of the opt-out cannot quietly invert the default."""
    reg, sent = _room_rig(monkeypatch)
    monkeypatch.setattr(production, "mint_scaffold", FakeScaffolds())
    st, _ = _run_flow(reg, "meeting.completed", dict(ROOM_REFS), EVERYTHING)
    rec = next(r for r in st["receipts"] if r["step"] == "email_attendees")
    assert rec["result"]["sent"] == 2, rec["result"]
    assert sorted(rec["result"]["to"]) == ["ben@bank.test", "cara@bank.test"]


def test_the_legacy_share_false_ref_still_opts_out(monkeypatch):
    """Back-compat: `share: False` was the shipped spelling. A deployment or a fixture that still
    sets it keeps meaning it — losing a kill switch to a refactor re-enables a fan-out somebody
    turned off on purpose."""
    reg, sent = _room_rig(monkeypatch)
    monkeypatch.setattr(production, "mint_scaffold", FakeScaffolds())
    st, _ = _run_flow(reg, "meeting.completed", dict(ROOM_REFS, share=False), EVERYTHING)
    rec = next(r for r in st["receipts"] if r["step"] == "email_attendees")
    assert rec["result"]["followup"] == "off"


def test_the_outside_domain_attendee_is_never_mailed(monkeypatch):
    """PRD §16.2, unchanged and re-asserted here because this file adds a second way to NOT send:
    an opt-out that worked by accidentally also breaking the allow-list would look identical."""
    reg, sent = _room_rig(monkeypatch)
    monkeypatch.setattr(production, "mint_scaffold", FakeScaffolds())
    _run_flow(reg, "meeting.completed", dict(ROOM_REFS), EVERYTHING)
    assert not [m for m in sent if m["to"] == "outside@elsewhere.test"]


# ── item 2: the degraded fan-out ────────────────────────────────────────────────────────────────

def test_with_no_agent_the_attendees_are_told_the_meeting_was_recorded(monkeypatch):
    """THE HEADLINE. RED BEFORE THE FIX: `email_attendees` was skipped outright and the receipt
    read `skipped: agent:not_present` — the organiser was told, the room was not."""
    reg, sent = _production(monkeypatch)
    _forbid_the_agent_door(monkeypatch)
    st, _ = _run_flow(reg, "meeting.completed", dict(ROOM_REFS), NO_AGENT)

    assert st["status"] == "done", f"{st['step']}: {st['reason']}"
    to = sorted(m["to"] for m in sent)
    assert to == ["anna@bank.test", "ben@bank.test", "cara@bank.test"], \
        f"the degraded fan-out did not reach the room: {to}"


def test_the_degraded_attendee_mail_claims_no_minutes_and_carries_no_button(monkeypatch):
    """The `NotPresent` doctrine, pointed at a stranger. There is no report, so the mail must not
    say there is one; there is no chat, so a button would open nothing. `_forbid_the_agent_door`
    is what proves the second half — a link here means `mint_scaffold` was called."""
    reg, sent = _production(monkeypatch)
    _forbid_the_agent_door(monkeypatch)
    _run_flow(reg, "meeting.completed", dict(ROOM_REFS), NO_AGENT)

    for msg in [m for m in sent if m["to"] != "anna@bank.test"]:
        assert msg["link"] is None, "a degraded attendee mail carried a button into nothing"
        assert "minutes" not in msg["subject"].lower(), msg["subject"]
        assert "recorded" in msg["body"].lower()


def test_the_degraded_fan_out_is_the_same_words_for_everyone(monkeypatch):
    """One meeting, one report — and with no agent, one absence of a report. Nothing is selected
    per person here, because selecting is exactly what the missing domain did."""
    reg, sent = _production(monkeypatch)
    _forbid_the_agent_door(monkeypatch)
    _run_flow(reg, "meeting.completed", dict(ROOM_REFS), NO_AGENT)

    bodies = {m["body"] for m in sent if m["to"] != "anna@bank.test"}
    assert len(bodies) == 1, "the degraded attendee mails differed per person"


def test_the_opt_out_still_wins_with_no_agent(monkeypatch):
    """The creator's refusal is not a feature of the agent domain."""
    reg, sent = _production(monkeypatch)
    _forbid_the_agent_door(monkeypatch)
    _run_flow(reg, "meeting.completed", dict(ROOM_REFS, share_opt_out=True), NO_AGENT)
    assert [m["to"] for m in sent] == ["anna@bank.test"]


def test_the_allow_list_still_holds_with_no_agent(monkeypatch):
    """Outside the domain, never — including on the cut with the least machinery to enforce it."""
    reg, sent = _production(monkeypatch)
    _forbid_the_agent_door(monkeypatch)
    _run_flow(reg, "meeting.completed", dict(ROOM_REFS), NO_AGENT)
    assert not [m for m in sent if m["to"] == "outside@elsewhere.test"]


def test_the_agent_present_fan_out_is_unchanged(monkeypatch):
    """THE REGRESSION GUARD. With agents deployed the attendee mail is still the head plus the
    shared report plus one minted button — this branch must cost that nothing."""
    reg, sent = _room_rig(monkeypatch)
    scaffolds = FakeScaffolds()
    monkeypatch.setattr(production, "mint_scaffold", scaffolds)
    _run_flow(reg, "meeting.completed", dict(ROOM_REFS), EVERYTHING)

    room = [m for m in sent if m["to"] in ("ben@bank.test", "cara@bank.test")]
    assert len(room) == 2
    for msg in room:
        assert msg["link"], "the attendee button is gone on the agent-present path"


# ── the creator has to be able to FIND the opt-out ──────────────────────────────────────────────

def _organizer_mail(sent):
    return next(m for m in sent if m["to"] == "anna@bank.test")


def test_the_organiser_is_told_sharing_is_on_and_how_to_stop_it(monkeypatch):
    """Default ON with an opt-out nobody can find is just default ON. The minutes mail is the only
    mail the creator reliably gets, so it is where the token has to appear."""
    reg, sent = _room_rig(monkeypatch)
    monkeypatch.setattr(production, "mint_scaffold", FakeScaffolds())
    _run_flow(reg, "meeting.completed", dict(ROOM_REFS), EVERYTHING)

    body = _organizer_mail(sent)["body"]
    assert "#noshare" in body, "the creator is never told how to exclude a meeting"
    assert "gets these notes too" in body


def test_an_opted_out_meeting_never_claims_the_notes_were_shared(monkeypatch):
    """The sentence is a claim about what is about to happen. On a meeting the creator already
    excluded, nothing is shared — announcing it anyway is the same untruth as a mail titled
    "Minutes" with no minutes under it."""
    reg, sent = _room_rig(monkeypatch)
    monkeypatch.setattr(production, "mint_scaffold", FakeScaffolds())
    _run_flow(reg, "meeting.completed", dict(ROOM_REFS, share_opt_out=True), EVERYTHING)

    body = _organizer_mail(sent)["body"]
    assert "gets these notes too" not in body
    assert "## Decided" in body, "the organiser still gets their own report"
