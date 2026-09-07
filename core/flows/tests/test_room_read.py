"""THE MEETING ROOM: whose DESKS the post-meeting turn may read, and in what order.

Founder, 2026-09-02, in two halves that must not be confused with each other:

  MEMBERSHIP IS THE INVITE. Everybody on it is eligible. Being quiet in a meeting you were in does
  not remove your desk from the room — the point of reading a desk is to understand what somebody
  meant, and the quiet ones are exactly the people whose context is not in the transcript.
  SPEAKING ONLY ORDERS. Matched participants first, by how much they spoke; everyone else after
  them in invite order.

  THE CUT IS AGENT-API'S, and only agent-api's (R-B17). `room_read_max` (default 12) still
  travels, as `read_max` on the dispatch — but flows no longer applies it to the ADDRESS list.
  Both sides used to cut, and agent-api's comment says why its own is the right one: *"capping
  before resolution would silently under-fill the room"*. Twelve addresses of which nine have no
  desk is a three-desk room.

Flows PROPOSES; agent-api verifies membership itself, resolves each ADDRESS through admin-api and
mounts only people who already have a subject and a desk. So this side sends addresses, never
subject ids, and never creates an account to answer a question about the room.

Five properties:

  1. ORDER is speaking time, descending, then invite order.
  2. THE MATCH IS AGAINST THE INVITE'S OWN `CN=` NAMES, and a tie orders nobody. It can never
     REMOVE anyone — it only decides who is at the front.
  3. NEVER ZERO. No transcript, no timings, no names, nothing matching: the answer is still the
     first `cap` addresses in invite order. An empty room from a matcher that could not do its job
     is a silent loss of the whole feature and looks exactly like a meeting where nobody spoke.
  4. THE CAP is `room_read_max`, default 12 — enforced by agent-api, on resolved desks, once.
  5. THE DISPATCH carries `room_meeting_id` (the ROW id), `room_participants`, `room_participant_names` and
     `room_read_max`, plus `X-Internal-Secret` — and none of them on any other turn.
"""
from __future__ import annotations

import flows_defs.production as production
import flows_steps.agent as agent_mod
import flows_steps.meeting as mt
import pytest
from flows import Reaction, Registry, StepCtx

from test_link_loop import _StubDB

# Bound at IMPORT time, before any test in any file has run: other test files in this suite rebind
# `dispatch_turn` at module level, and reading it inside a test can hand you their stub.
REAL_DISPATCH_TURN = agent_mod.dispatch_turn

ROOM = ["anna.smith@bank.test", "ben@bank.test", "cara.jones@bank.test", "dan.smith@bank.test"]
NAMES = {"anna.smith@bank.test": "Anna Smith", "ben@bank.test": "Ben",
         "cara.jones@bank.test": "Cara Jones", "dan.smith@bank.test": "Dan Smith"}


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


# ── 1 · order ────────────────────────────────────────────────────────────────────────────────
def test_speakers_come_first_in_order_of_speaking_time(monkeypatch):
    _rig(monkeypatch, _segs(("Ben", 5, "a"), ("Anna Smith", 40, "b"), ("Ben", 20, "c"),
                            ("Cara Jones", 12, "d")))
    assert mt.room_order("7", 97, ROOM, NAMES) == [
        "anna.smith@bank.test", "ben@bank.test", "cara.jones@bank.test",
        "dan.smith@bank.test"]                       # dan never spoke — last, not absent


def test_the_quiet_are_still_in_the_room(monkeypatch):
    """The half that is easiest to get wrong. A person who said nothing is exactly the person whose
    desk explains what they meant; dropping them would make the room useless for the meetings that
    need it most."""
    _rig(monkeypatch, _segs(("Anna Smith", 10, "a")))
    out = mt.room_order("7", 97, ROOM, NAMES)
    assert out[0] == "anna.smith@bank.test"
    assert set(out) == set(ROOM)


def test_the_unmatched_keep_the_invites_own_order(monkeypatch):
    _rig(monkeypatch, _segs(("Cara Jones", 10, "a")))
    assert mt.room_order("7", 97, ROOM, NAMES) == [
        "cara.jones@bank.test",                      # spoke
        "anna.smith@bank.test", "ben@bank.test", "dan.smith@bank.test"]   # invite order


def test_character_count_orders_when_a_producer_gives_no_timings(monkeypatch):
    monkeypatch.setattr(mt, "user_api_key", lambda uid: "key")
    monkeypatch.setattr(mt, "http", lambda *a, **k: (200, {"segments": [
        {"speaker": "Ben", "text": "short"},
        {"speaker": "Anna Smith", "text": "a very much longer contribution indeed"}]}))
    assert mt.room_order("7", 97, ROOM, NAMES)[:2] == [
        "anna.smith@bank.test", "ben@bank.test"]


# ── 2 · the match is against the invite's CN= names, and it only orders ─────────────────────
@pytest.mark.parametrize("label,first", [
    ("Anna Smith", "anna.smith@bank.test"),
    ("anna smith", "anna.smith@bank.test"),      # case is not a signal
    ("Anna-Maria Smith", "anna.smith@bank.test"),  # an extra token does not break the overlap
    ("Ben", "ben@bank.test"),
    ("Cara", "cara.jones@bank.test"),            # unique on one token
])
def test_labels_that_resolve_put_that_person_first(monkeypatch, label, first):
    _rig(monkeypatch, _segs((label, 10, "x")))
    assert mt.room_order("7", 97, ROOM, NAMES)[0] == first


@pytest.mark.parametrize("label", [
    "Smith",           # Anna Smith AND Dan Smith — a tie
    "Speaker 1",       # what a diarizer emits when it has no name
    "",
    "Zoe",             # nobody on the invite
    "A",               # one character: never enough to carry a match
])
def test_an_ambiguous_or_unknown_label_orders_nobody_and_removes_nobody(monkeypatch, label):
    """A tie must not pick one of two people — but it must not empty the room either. Everyone is
    still there, in the invite's own order."""
    _rig(monkeypatch, _segs((label, 30, "x")))
    assert mt.room_order("7", 97, ROOM, NAMES) == ROOM


def test_matching_uses_the_invite_names_not_the_email_local_part(monkeypatch):
    """`participant_names` is why this is a match and not a guess. Deriving "Ben Ashworth" from
    `b.ash@` is the failure the CN= line exists to prevent, so with no names nothing is ordered."""
    room = ["b.ash@bank.test", "other@bank.test"]
    _rig(monkeypatch, _segs(("Ben Ashworth", 30, "x")))
    assert mt.room_order("7", 97, room, {"b.ash@bank.test": "Ben Ashworth"})[0] == "b.ash@bank.test"
    assert mt.room_order("7", 97, room, {}) == room          # no names → invite order, not empty


# ── 3 · never zero ───────────────────────────────────────────────────────────────────────────
def test_an_unreadable_transcript_degrades_to_invite_order(monkeypatch):
    from flows import StepError

    def boom(*a, **k):
        raise StepError("gateway down")
    monkeypatch.setattr(mt, "user_api_key", lambda uid: "key")
    monkeypatch.setattr(mt, "http", boom)
    assert mt.room_order("7", 97, ROOM, NAMES) == ROOM


def test_a_meeting_where_nobody_spoke_still_has_a_room(monkeypatch):
    _rig(monkeypatch, [])
    assert mt.room_order("7", 97, ROOM, NAMES) == ROOM


def test_no_participants_is_the_only_empty_room(monkeypatch):
    """Nothing to order and nobody to mount — and a step with no participants must not reach for
    the network to learn that."""
    calls = _rig(monkeypatch, _segs(("Anna Smith", 10, "x")))
    assert mt.room_order("7", 97, [], NAMES) == []
    assert calls == []


def test_duplicate_addresses_are_collapsed(monkeypatch):
    _rig(monkeypatch, [])
    assert mt.room_order("7", 97, ["a@x.test", "A@x.test ", "b@x.test"], {}) == [
        "a@x.test", "b@x.test"]


# ── 4 · the cap ──────────────────────────────────────────────────────────────────────────────
def test_the_cap_keeps_the_front_of_the_list(monkeypatch):
    room = [f"p{i}@bank.test" for i in range(20)]
    names = {f"p{i}@bank.test": f"Speaker{i}" for i in range(20)}
    _rig(monkeypatch, _segs(*[(f"Speaker{i}", 100 - i, "x") for i in range(20)]))
    assert mt.room_order("7", 97, room, names, cap=3) == [
        "p0@bank.test", "p1@bank.test", "p2@bank.test"]


def test_the_cap_applies_to_invite_order_too(monkeypatch):
    _rig(monkeypatch, [])
    assert mt.room_order("7", 97, ROOM, NAMES, cap=2) == ROOM[:2]


def test_the_cap_is_sent_to_agent_api_and_applied_nowhere_else(monkeypatch):
    """`room_read_max` still defaults to twelve — but flows no longer CUTS with it (R-B17).

    It used to cut the ADDRESS list here and also send the number to agent-api, which caps MOUNTED
    DESKS instead and says why in its own words: *"capping before resolution would silently
    under-fill the room"*. Both cuts ran, so twelve addresses of which nine had no desk produced a
    three-desk room. Flows orders; agent-api resolves and then cuts. The number still travels — as
    `read_max` on the wire — and the ordered room travels whole."""
    reg = Registry()
    production.build(reg, _StubDB())
    seen = {}

    def note_cap(uid, mid, participants, names, cap=0):
        seen["cap"] = cap
        return list(participants)

    class _Flow:
        def __init__(self, **p):
            self._p = p

        def param(self, k, default=None):
            return self._p.get(k, default)

    monkeypatch.setattr(production.mt, "room_order", note_cap)
    monkeypatch.setattr(production.mt, "meeting_row", lambda uid, m, native=None: {"id": 97})
    monkeypatch.setattr(production.ag, "dispatch_turn",
                        lambda uid, s, p, room=None, **kw: seen.update(room=room) or 0)
    monkeypatch.setattr(production, "setting", lambda uid, key: "")

    def run(flow):
        r = Reaction("rid", "sid", "e", {"uid": "7", "meeting_id": 97, "native": "abc",
                                         "organizer": "a@x.test", "title": "T",
                                         "participants": ROOM, "participant_names": NAMES,
                                         "start": 1_700_003_600.0},
                     "f", 1, "step", "running", 1, 0.0, None, None, None)
        seen.clear()
        reg.steps["process_meeting"](StepCtx(reaction=r, effect_key="k", prior={},
                                             clock_now=1_700_000_000.0, scratch={}, flow=flow))
        return seen

    # the number: still twelve by default, still overridable, still default on a typo or a zero
    assert run(None)["room"]["read_max"] == 12
    assert run(_Flow())["room"]["read_max"] == 12
    assert run(_Flow(room_read_max=4))["room"]["read_max"] == 4
    assert run(_Flow(room_read_max="nonsense"))["room"]["read_max"] == 12
    assert run(_Flow(room_read_max=0))["room"]["read_max"] == 12
    # and the cut: flows asks room_order for NO cap, and the whole ordered room goes on the wire
    got = run(_Flow(room_read_max=4))
    assert not got["cap"], "flows must not pre-cut the address list — agent-api owns the cap"
    assert got["room"]["read"] == ROOM, "the full ordered room travels; agent-api resolves it"


# ── 5 · what actually goes on the wire ───────────────────────────────────────────────────────
def _chat_post(monkeypatch):
    """Record the headers and body of the `/api/chat` POST the dispatch makes."""
    seen = {}

    def fake_http(method, url, headers, body=None, timeout=20):
        if url.endswith("/api/chat"):
            seen["headers"] = dict(headers)
            seen["body"] = dict(body or {})
        return 200, {"turns": []}
    monkeypatch.setattr(agent_mod, "http", fake_http)
    monkeypatch.setattr(agent_mod, "require_internal_secret", lambda: "s3cr3t")
    return seen


def test_the_room_travels_as_agent_apis_four_fields_plus_the_internal_header(monkeypatch):
    seen = _chat_post(monkeypatch)
    REAL_DISPATCH_TURN("7", "meet-97", "hi", room={
        "meeting_id": 97, "read": ["anna.smith@bank.test"], "names": NAMES, "read_max": 12})

    assert seen["body"] == {
        "prompt": "hi", "session": "meet-97",
        "room_meeting_id": "97",                     # the ROW id, as a string
        # THE WIRE NAMES ARE agent-api's, not ours. ChatBody is `extra="forbid"`, so a field this
        # side invents does not degrade to an empty room — it 422s the whole dispatch. These two
        # were `room_read` / `participant_names` here and `room_participants` /
        # `room_participant_names` there, written by two workers the same afternoon, and every
        # post-meeting turn would have failed. Pinned against the receiving contract, not ours.
        "room_participants": ["anna.smith@bank.test"],   # ADDRESSES — agent-api resolves identity
        "room_participant_names": NAMES,
        "room_read_max": 12}
    assert seen["headers"]["X-Internal-Secret"] == "s3cr3t"
    assert seen["headers"]["X-User-Id"] == "7"


def test_no_room_means_the_body_and_headers_this_dispatch_always_sent(monkeypatch):
    """Every other turn in the system — onboarding, the email conversation, the re-ask — must not
    grow a header or a field, and must never present the internal secret."""
    seen = _chat_post(monkeypatch)
    REAL_DISPATCH_TURN("7", "s", "hello")
    assert seen["body"] == {"prompt": "hello", "session": "s"}
    assert "X-Internal-Secret" not in seen["headers"]

    REAL_DISPATCH_TURN("7", "s", "hello", room={"meeting_id": None, "read": ["a@b.test"]})
    assert seen["body"] == {"prompt": "hello", "session": "s"}
    assert "X-Internal-Secret" not in seen["headers"]


def test_the_room_addresses_the_ROW_not_the_native_id(monkeypatch):
    """The same identity bug that mailed meeting 97's attendees a link with no token: a meeting
    planned from an unmatched url is `platform='unknown'` with an empty native, so no pair
    addresses it and only the row id exists. The room gate resolves a meetings-domain ROW."""
    reg = Registry()
    production.build(reg, _StubDB())
    seen = {}
    monkeypatch.setattr(production.mt, "meeting_row",
                        lambda uid, m, native=None: {"id": 412})
    monkeypatch.setattr(production.mt, "room_order",
                        lambda uid, mid, participants, names, cap=12: list(participants))
    monkeypatch.setattr(production.ag, "dispatch_turn",
                        lambda uid, s, p, room=None, **kw: seen.update(room=room, prompt=p) or 0)
    monkeypatch.setattr(production, "setting", lambda uid, key: "")
    r = Reaction("rid", "sid", "e", {"uid": "7", "meeting_id": "96088138284", "native": "",
                                     "organizer": "a@x.test", "title": "T",
                                     "participants": ROOM, "participant_names": NAMES,
                                     "start": 1_700_003_600.0},
                 "f", 1, "step", "running", 1, 0.0, None, None, None)
    reg.steps["process_meeting"](StepCtx(reaction=r, effect_key="k", prior={},
                                         clock_now=1_700_000_000.0, scratch={}, flow=None))
    assert seen["room"]["meeting_id"] == 412
    assert seen["room"]["read"] == ROOM
    assert seen["room"]["names"] == NAMES


def test_the_kick_names_the_desks_it_may_read(monkeypatch):
    reg = Registry()
    production.build(reg, _StubDB())
    seen = {}
    monkeypatch.setattr(production.mt, "meeting_row", lambda uid, m, native=None: {"id": 97})
    monkeypatch.setattr(production.mt, "room_order",
                        lambda uid, mid, participants, names, cap=12: ["anna.smith@bank.test",
                                                                       "ben@bank.test"])
    monkeypatch.setattr(production.ag, "dispatch_turn",
                        lambda uid, s, p, room=None, **kw: seen.update(prompt=p) or 0)
    monkeypatch.setattr(production, "setting", lambda uid, key: "")
    r = Reaction("rid", "sid", "e", {"uid": "7", "meeting_id": 97, "native": "abc",
                                     "organizer": "a@x.test", "title": "T",
                                     "participants": ROOM, "participant_names": NAMES,
                                     "start": 1_700_003_600.0},
                 "f", 1, "step", "running", 1, 0.0, None, None, None)
    reg.steps["process_meeting"](StepCtx(reaction=r, effect_key="k", prior={},
                                         clock_now=1_700_000_000.0, scratch={}, flow=None))
    assert "anna.smith@bank.test, ben@bank.test" in seen["prompt"]
    assert "READ-ONLY access to the desks" in seen["prompt"]
    assert "never copy a line, a note or a phrase out of one into this report" in seen["prompt"]

# ── the CROSS-SERVICE contract, read off the other service's source ────────────────────────────

def test_every_room_field_flows_sends_is_declared_in_agent_apis_ChatBody():
    """The bug this exists to prevent actually happened, on 2026-09-02, between two workers.

    flows sent `room_read` + `participant_names`; agent-api's `ChatBody` declared
    `room_participants` + `room_participant_names`. Both sides had tests. Both suites were green.
    `ChatBody` is `extra="forbid"`, so the mismatch would not have degraded to an empty room — it
    would have 422'd EVERY post-meeting dispatch, and the first sign of it would have been the
    founder's meeting producing nothing.

    A test that pins our own dict against our own constant cannot catch that: it is true no matter
    what the other service calls things. So this one reads the RECEIVING contract off agent-api's
    source and asserts we are a subset of it. It is deliberately crude — a regex over a file — and
    crude is the point: it needs no import of core.agent, no running service, and it fails the
    moment either side renames a field without the other.
    """
    import pathlib
    import re

    here = pathlib.Path(__file__).resolve()
    # ANCHOR ON OUR OWN TREE, never on the tree we are checking. This used to search upward for
    # `core/agent` and take the first hit — so in a checkout that does not carry agent-api the
    # generator raised StopIteration and the guard died as an ERROR, in a deployment where its
    # absence is correct: the no-agents product (PRD decision 40.6) is gateway + meetings + flows
    # + identity, and the by-need cut (decision 43) does not ship the agent domain's source.
    root = next(p for p in here.parents if (p / "core" / "flows").is_dir())
    # SEARCH THE CONTROL PLANE, not one file. `ChatBody` lived in `api.py` until agent-api split
    # that module into `control_plane/routers/` plus `api_shared.py` (PR #1459); the request models
    # moved with it. Pinning the filename made this guard fail closed on a pure refactor — correct,
    # but it also means the guard is dark until someone repoints it, and a dark cross-domain guard
    # is exactly what let the 2026-09-02 mismatch through. Finding the class wherever it lives keeps
    # the check crude (still a regex over source, still no import) and keeps it ALIVE across moves.
    plane = root / "core" / "agent" / "control_plane"
    if not plane.is_dir():
        # SKIPPED, NOT PASSED, and with the reason named: a cross-domain guard that quietly
        # answered green in a tree where it checked nothing would be worse than no guard — it
        # would report agreement between two services when only one of them is here. Where the
        # source IS present the guard runs exactly as before; nothing about it is softened.
        pytest.skip(f"agent-api source is not in this tree ({plane} absent) — the room contract "
                    "cannot be read off a service this checkout does not carry")
    src = ""
    for cand in sorted(plane.rglob("*.py")):
        text = cand.read_text()
        if "class ChatBody(BaseModel):" in text:
            src = text
            break
    assert src, f"agent-api's ChatBody not found anywhere under {plane}"
    m = re.search(r"class ChatBody\(BaseModel\):(.*?)(?=\nclass )", src, re.S)
    assert m, "could not find ChatBody in agent-api's api.py"
    declared = set(re.findall(r"^\s{4}([a-z_][a-z0-9_]*)\s*:", m.group(1), re.M))
    assert "room_meeting_id" in declared, (
        "agent-api no longer declares room_meeting_id — the room contract moved; "
        f"fields now: {sorted(declared)}")

    seen = {}
    _post = lambda body, headers: seen.update(body=body)  # noqa: E731
    sent = _room_body_keys()
    unknown = sent - declared
    assert not unknown, (
        f"flows sends room fields agent-api's ChatBody does not declare: {sorted(unknown)}. "
        f"ChatBody is extra=forbid, so this 422s every post-meeting dispatch rather than "
        f"degrading. agent-api declares: {sorted(f for f in declared if f.startswith('room'))}")


def _room_body_keys() -> set:
    """The room keys `dispatch_turn` puts on the wire, read off OUR source for the same reason the
    test above reads theirs — asserting against a hand-copied literal would drift with the code."""
    import pathlib
    import re
    agent_py = pathlib.Path(__file__).resolve().parents[1] / "src" / "flows_steps" / "agent.py"
    return set(re.findall(r'body\["(room_[a-z_]+)"\]\s*=', agent_py.read_text()))
