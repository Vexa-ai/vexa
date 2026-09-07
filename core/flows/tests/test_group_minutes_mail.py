"""THE WRITE-UP OF A GROUP'S MEETING REACHES THE GROUP — Vexa-ai/vexa#1648.

Founder, 2026-09-06, ninety seconds after sending a bot into a call from inside a shared workspace:
*"it was mailed to [the admin] not to [the member]"*. The deployment's mail sink showed the cause —
a `Minutes: <the workspace>` mail whose subject names the workspace and whose recipient list is one
address. `_organizer_address` answers with a single address and falls back to the requester's own
for an ad-hoc meeting, so a meeting that belongs to a group was written up for whoever asked for it.

Four properties:

  1. THE BIND IS THE WHOLE TRIGGER. No `data.workspace_id`, no roster read, no extra mail — so every
     meeting that exists today behaves exactly as it does today.
  2. EVERY OTHER MEMBER GETS IT, and each gets THEIR OWN scaffold link. The link in this mail signs
     its reader in; one shared link would hand every member the organiser's session.
  3. THE ORGANISER IS MAILED ONCE. They are the step's contract and the caller already sent to them.
  4. THE FAN-OUT CANNOT COST THE ORGANISER THEIR MAIL. An unreadable roster, or one member's send
     failing, must not fail a step whose receipt is the organiser's message id.

Offline like the rest of the suite: no network, no clock, no SMTP.
"""
from __future__ import annotations

import json

import flows_defs.production as production
import flows_steps.mailtext as mailtext
import flows_steps.notify as notify_mod
import pytest
from flows import Done, Reaction, Registry, StepCtx

from test_link_loop import FakeChannel, FakeScaffolds, _StubDB

WS = "team-notes"
ORGANISER, MEMBER, READER = "anna@bank.test", "ben@bank.test", "cara@bank.test"
REFS = {"uid": "7", "organizer": ORGANISER, "title": "Group call", "meeting_id": 97,
        "start": 1_700_003_600.0}
PRIOR = {"process_meeting": {"report": "## Decided\n- ship it\n", "group": WS, "room_read": []}}

ROSTER = [
    {"subject": "7", "role": "owner", "email": ORGANISER},
    {"subject": "8", "role": "contributor", "email": MEMBER},
    {"subject": "9", "role": "reader", "email": READER},
]


@pytest.fixture(autouse=True)
def scaffolds(monkeypatch):
    fake = FakeScaffolds()
    monkeypatch.setattr(production, "mint_scaffold", fake)
    return fake


def _ctx(refs: dict, prior: dict | None = None) -> StepCtx:
    r = Reaction("rid", "sid", "e", refs, "f", 1, "step", "running", 1, 0.0, None, None, None)
    return StepCtx(reaction=r, effect_key="rid:step", prior=prior or {},
                   clock_now=1_700_000_000.0, scratch={}, flow=None)


def _rig(monkeypatch, *, bound=WS, roster=ROSTER, settings=None):
    """`bound` is the meeting's `data.workspace_id` — None for an unbound meeting. `roster` is what
    that workspace's `policy/members.json` holds; a string is served verbatim (for the unreadable
    case) and an exception instance is raised."""
    reg = Registry()
    production.build(reg, _StubDB())
    ch = FakeChannel()
    notify_mod.use(ch)

    def read(uid, path, slug=None):
        if slug == "_global":
            return "# Acme Bank\n\nthe org handbook" if path == "README.md" else None
        if path == "policy/members.json" and slug == WS:
            if isinstance(roster, Exception):
                raise roster
            return roster if isinstance(roster, str) else json.dumps(roster)
        return None

    monkeypatch.setattr(production, "ws_file", read)
    monkeypatch.setattr(mailtext, "ws_file", read)
    monkeypatch.setattr(production, "setting",
                        settings or (lambda uid, key: "" if key == "timezone" else True))
    row = {"id": 97, **({"data": {"workspace_id": bound}} if bound else {})}
    monkeypatch.setattr(production.mt, "meeting_row", lambda uid, mid, native=None: row)
    return reg, ch


def teardown_function():
    notify_mod.use(None)


def _sent_to(ch):
    return sorted(m["to"] for m in ch.sent)


# ── 1 · the bind is the trigger ──────────────────────────────────────────────────────────────
def test_an_unbound_meeting_is_written_up_for_its_organiser_alone(monkeypatch):
    reg, ch = _rig(monkeypatch, bound=None)
    out = reg.steps["email_minutes"](_ctx(dict(REFS), PRIOR))
    assert isinstance(out, Done)
    assert _sent_to(ch) == [ORGANISER]


# ── 2 · a bound meeting reaches its members, each with their own link ────────────────────────
def test_a_bound_meeting_reaches_every_member(monkeypatch):
    reg, ch = _rig(monkeypatch)
    out = reg.steps["email_minutes"](_ctx(dict(REFS), PRIOR))

    assert _sent_to(ch) == sorted([ORGANISER, MEMBER, READER])
    # A READER IS INCLUDED. Owner and contributor see everything and a reader reads; the notes are
    # a read, so leaving them out would be a rule nobody wrote.
    assert READER in _sent_to(ch)
    # one report, three copies — the same words reach everybody in the room
    assert len({m["body"] for m in ch.sent}) == 1
    assert "## Decided" in ch.sent[0]["body"]
    # THE RECEIPT IS STILL THE ORGANISER'S MAIL — the first one sent, and the step's contract. A
    # fan-out that overwrote it would make the receipt name a message the organiser never got.
    assert out.result["message_id"] == "<fake-1@test>"
    assert ch.sent[0]["to"] == ORGANISER


def test_every_member_gets_their_own_sign_in_link(monkeypatch):
    """The link signs its reader in. One shared link would hand every member the organiser's
    session — which is the same class of mistake as forwarding an invite."""
    reg, ch = _rig(monkeypatch)
    reg.steps["email_minutes"](_ctx(dict(REFS), PRIOR))
    links = [m["link"] for m in ch.sent]
    assert len(links) == 3 and len(set(links)) == 3


# ── 3 · the organiser is mailed once ─────────────────────────────────────────────────────────
def test_the_organiser_is_not_mailed_twice_by_being_on_the_roster(monkeypatch):
    reg, ch = _rig(monkeypatch)
    reg.steps["email_minutes"](_ctx(dict(REFS), PRIOR))
    assert [m["to"] for m in ch.sent].count(ORGANISER) == 1


def test_a_member_who_turned_minutes_mail_off_is_skipped(monkeypatch):
    """`mail_minutes` is a person's answer about minutes mail, and this is minutes mail. Subject 8
    is the contributor."""
    def settings(uid, key):
        if key == "timezone":
            return ""
        return False if (key == "mail_minutes" and str(uid) == "8") else True

    reg, ch = _rig(monkeypatch, settings=settings)
    reg.steps["email_minutes"](_ctx(dict(REFS), PRIOR))
    assert _sent_to(ch) == sorted([ORGANISER, READER])


# ── 4 · the fan-out never costs the organiser their mail ─────────────────────────────────────
@pytest.mark.parametrize("roster", [
    RuntimeError("agent door refused"),      # the roster read blew up
    "{not json at all",                      # …or came back as something that is not a roster
    "{}",                                    # …or as an object where a list belongs
])
def test_an_unreadable_roster_still_writes_the_organiser_up(monkeypatch, roster):
    reg, ch = _rig(monkeypatch, roster=roster)
    out = reg.steps["email_minutes"](_ctx(dict(REFS), PRIOR))
    assert isinstance(out, Done)
    assert _sent_to(ch) == [ORGANISER]


def test_one_members_send_failing_costs_only_that_member(monkeypatch):
    """One bad address must not cost the rest of the group their notes, and must not fail the step."""
    reg, ch = _rig(monkeypatch)
    real = production.notify

    def flaky(to, subject, body, link=None, **kw):
        if to == MEMBER:
            raise RuntimeError("that mailbox does not exist")
        return real(to, subject, body, link=link, **kw)

    monkeypatch.setattr(production, "notify", flaky)
    out = reg.steps["email_minutes"](_ctx(dict(REFS), PRIOR))
    assert isinstance(out, Done)
    assert _sent_to(ch) == sorted([ORGANISER, READER])
