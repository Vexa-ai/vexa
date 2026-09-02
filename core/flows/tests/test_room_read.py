"""WHOSE WORKSPACES the post-meeting turn may read: the people who SPOKE, ordered, capped.

Founder, 2026-09-02, on mounting every attendee's workspace: *"need to make sure agent will not
die if it has 200 folders in it."* So flows computes the selection — it is where the transcript is
reachable — and hands it to the dispatch as a PROPOSAL. agent-api verifies it against the
meeting's real participants and mounts the intersection read-only, so this list can only ever
NARROW that side's answer; nothing here mounts anything.

Everybody on the invite still gets the mail and the drop entity. Only the READ mounts are capped.

Four properties:

  1. ORDER IS SPEAKING TIME, descending. The person who said the most is the one whose workspace
     is most likely to explain what they said.
  2. THE MATCH IS BY NAME, AND A TIE MATCHES NOBODY. This list decides whose workspace a turn may
     read, so an ambiguous label must resolve to neither candidate: a missing mount costs the
     report some context, a wrong one shows one person's workspace to a room.
  3. THE CAP IS `room_read_max`, DEFAULT 12.
  4. IT NEVER RAISES AND NEVER MOUNTS. A selection that cannot be computed is an empty list, and
     an empty list means the turn reads nobody.
"""
from __future__ import annotations

import flows_defs.production as production
import flows_steps.meeting as mt
import pytest
from flows import Reaction, Registry, StepCtx

import flows_steps.agent as agent_mod
from test_link_loop import _StubDB

# Bound at IMPORT time, before any test in any file has run. `dispatch_turn` is rebound at module
# level by other test files in this suite, so reading it inside a test can hand you somebody
# else's stub and a green assertion about nothing.
REAL_DISPATCH_TURN = agent_mod.dispatch_turn

ROOM = ["anna.smith@bank.test", "ben@bank.test", "cara.jones@bank.test", "dan.smith@bank.test"]


def _segs(*rows):
    """(speaker, seconds, text) → the gateway's segment shape (`start`/`end`, not `start_time`)."""
    out, t = [], 0.0
    for speaker, secs, text in rows:
        out.append({"speaker": speaker, "start": t, "end": t + secs, "text": text})
        t += secs
    return out


def _rig(monkeypatch, segs, *, status=200):
    calls = []

    def fake_http(method, url, headers, body=None, timeout=20):
        calls.append(url)
        return status, {"segments": segs}
    monkeypatch.setattr(mt, "http", fake_http)
    monkeypatch.setattr(mt, "user_api_key", lambda uid: "key")
    return calls


# ── 1 · order is speaking time ───────────────────────────────────────────────────────────────
def test_the_order_is_speaking_time_descending(monkeypatch):
    _rig(monkeypatch, _segs(("Ben", 5, "a"), ("Anna Smith", 40, "b"), ("Ben", 20, "c"),
                            ("Cara Jones", 12, "d")))
    assert mt.speaking_order("7", 97, ROOM) == [
        "anna.smith@bank.test", "ben@bank.test", "cara.jones@bank.test"]


def test_somebody_who_never_spoke_is_not_in_the_list(monkeypatch):
    _rig(monkeypatch, _segs(("Anna Smith", 10, "a")))
    out = mt.speaking_order("7", 97, ROOM)
    assert out == ["anna.smith@bank.test"]
    assert "dan.smith@bank.test" not in out          # on the invite, silent in the room


def test_character_count_orders_when_a_producer_gives_no_timings(monkeypatch):
    """Some producers hand back segments with no usable start/end. The seconds are then unknown,
    but the ORDER still is not: characters spoken is a proxy for the same thing."""
    monkeypatch.setattr(mt, "user_api_key", lambda uid: "key")
    monkeypatch.setattr(mt, "http", lambda *a, **k: (200, {"segments": [
        {"speaker": "Ben", "text": "short"},
        {"speaker": "Anna Smith", "text": "a very much longer contribution indeed"}]}))
    assert mt.speaking_order("7", 97, ROOM) == ["anna.smith@bank.test", "ben@bank.test"]


# ── 2 · the match, and what it refuses ───────────────────────────────────────────────────────
@pytest.mark.parametrize("label,expected", [
    ("Anna Smith", "anna.smith@bank.test"),      # both tokens
    ("anna smith", "anna.smith@bank.test"),      # case is not a signal
    ("Anna-Maria Smith", "anna.smith@bank.test"),  # an extra token does not break the overlap
    ("Ben", "ben@bank.test"),                    # a one-token address, matched by its one token
    ("Cara", "cara.jones@bank.test"),            # unique on one token: nobody else is a Cara
])
def test_labels_that_resolve_to_exactly_one_person(monkeypatch, label, expected):
    _rig(monkeypatch, _segs((label, 10, "x")))
    assert mt.speaking_order("7", 97, ROOM) == [expected]


@pytest.mark.parametrize("label", [
    "Smith",           # anna.smith AND dan.smith — a tie
    "Speaker 1",       # the label a diarizer gives when it has no name at all
    "",                # no label
    "Zoe",             # nobody on the invite
    "A",               # one character: never enough to carry a match
])
def test_an_ambiguous_or_unknown_label_matches_nobody(monkeypatch, label):
    """A missing mount costs the report some context. A wrong one shows one person's workspace to
    a room, which is not a thing that can be taken back."""
    _rig(monkeypatch, _segs((label, 30, "x")))
    assert mt.speaking_order("7", 97, ROOM) == []


def test_a_tie_removes_both_candidates_not_just_one(monkeypatch):
    _rig(monkeypatch, _segs(("Smith", 30, "x"), ("Ben", 5, "y")))
    assert mt.speaking_order("7", 97, ROOM) == ["ben@bank.test"]


# ── 3 · the cap ──────────────────────────────────────────────────────────────────────────────
def test_the_cap_keeps_the_biggest_talkers(monkeypatch):
    room = [f"spk{i}@bank.test" for i in range(20)]
    _rig(monkeypatch, _segs(*[(f"spk{i}", 100 - i, "x") for i in range(20)]))
    assert mt.speaking_order("7", 97, room, cap=3) == [
        "spk0@bank.test", "spk1@bank.test", "spk2@bank.test"]


def test_the_default_cap_is_twelve(monkeypatch):
    reg = Registry()
    production.build(reg, _StubDB())
    seen = {}

    class _Flow:
        def __init__(self, **p):
            self._p = p

        def param(self, k, default=None):
            return self._p.get(k, default)

    def note_cap(uid, mid, participants, cap=12):
        seen["cap"] = cap
        return []

    monkeypatch.setattr(production.mt, "speaking_order", note_cap)
    monkeypatch.setattr(production.ag, "dispatch_turn", lambda *a, **k: 0)
    monkeypatch.setattr(production.ag, "commit_shas", lambda uid: [])
    monkeypatch.setattr(production, "setting", lambda uid, key: "")

    def run(flow):
        r = Reaction("rid", "sid", "e", {"uid": "7", "meeting_id": 97, "native": "abc",
                                         "organizer": "a@x.test", "title": "T",
                                         "participants": ROOM, "start": 1_700_003_600.0},
                     "f", 1, "step", "running", 1, 0.0, None, None, None)
        seen.clear()
        reg.steps["process_meeting"](StepCtx(reaction=r, effect_key="k", prior={},
                                             clock_now=1_700_000_000.0, scratch={}, flow=flow))
        return seen["cap"]

    assert run(None) == 12
    assert run(_Flow()) == 12
    assert run(_Flow(room_read_max=4)) == 4
    assert run(_Flow(room_read_max="nonsense")) == 12   # a typo is the default, never an error
    assert run(_Flow(room_read_max=0)) == 12


# ── 4 · it never raises, and the dispatch carries it as a proposal ───────────────────────────
def test_a_transcript_that_cannot_be_read_selects_nobody(monkeypatch):
    from flows import StepError

    def boom(*a, **k):
        raise StepError("gateway down")
    monkeypatch.setattr(mt, "user_api_key", lambda uid: "key")
    monkeypatch.setattr(mt, "http", boom)
    assert mt.speaking_order("7", 97, ROOM) == []


def test_no_participants_means_no_transcript_read_at_all(monkeypatch):
    """Nothing a label could match, so the read is not worth doing — and a step with no
    participants in its refs must not reach for the network to learn that."""
    calls = _rig(monkeypatch, _segs(("Anna Smith", 10, "x")))
    assert mt.speaking_order("7", 97, []) == []
    assert calls == []


def test_the_proposal_reaches_the_dispatch_and_the_kick_names_it(monkeypatch):
    reg = Registry()
    production.build(reg, _StubDB())
    seen = {}
    monkeypatch.setattr(production.ag, "dispatch_turn",
                        lambda uid, session, prompt, room_read=None: seen.update(
                            prompt=prompt, room_read=room_read) or 0)
    monkeypatch.setattr(production.ag, "commit_shas", lambda uid: [])
    monkeypatch.setattr(production, "setting", lambda uid, key: "")
    monkeypatch.setattr(production.mt, "speaking_order",
                        lambda uid, mid, participants, cap=12: ["anna.smith@bank.test",
                                                                "ben@bank.test"])
    r = Reaction("rid", "sid", "e", {"uid": "7", "meeting_id": 97, "native": "abc",
                                     "organizer": "a@x.test", "title": "T",
                                     "participants": ROOM, "start": 1_700_003_600.0},
                 "f", 1, "step", "running", 1, 0.0, None, None, None)
    reg.steps["process_meeting"](StepCtx(reaction=r, effect_key="k", prior={},
                                         clock_now=1_700_000_000.0, scratch={}, flow=None))

    assert seen["room_read"] == ["anna.smith@bank.test", "ben@bank.test"]
    assert "anna.smith@bank.test, ben@bank.test" in seen["prompt"]
    assert "READ-ONLY access to the workspaces of the people who spoke" in seen["prompt"]
    assert ("MEETING-RELEVANT FACTS ONLY, ATTRIBUTED — a person's workspace informs the "
            "report, it is never quoted into it.") in seen["prompt"]


def test_an_empty_proposal_leaves_the_dispatch_body_as_it_always_was(monkeypatch):
    """`room_read` is omitted, not sent empty: every dispatch that is not a post-meeting turn
    sends exactly the body it sent before this existed."""
    posted = {}

    def fake_http(method, url, headers, body=None, timeout=20):
        if url.endswith("/api/chat"):
            posted.update(body or {})
        return 200, {"turns": []}
    monkeypatch.setattr(agent_mod, "http", fake_http)

    REAL_DISPATCH_TURN("7", "s", "hello")
    assert posted == {"prompt": "hello", "session": "s"}
    posted.clear()
    REAL_DISPATCH_TURN("7", "s", "hello", room_read=[])
    assert posted == {"prompt": "hello", "session": "s"}
    posted.clear()
    REAL_DISPATCH_TURN("7", "s", "hello", room_read=["a@b.test"])
    assert posted == {"prompt": "hello", "session": "s", "room_read": ["a@b.test"]}
