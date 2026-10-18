"""A FLOW-DISPATCHED TURN SAYS SO ON THE WIRE (Vexa-ai/vexa#1605).

Nobody typed a flow's kick, and on 2026-09-06 the founder opened a held meeting's chat and read the
whole `process-meeting` instruction back as his own grey bubble — because the post to `/api/chat`
carried nothing that said a machine had written it. agent-api marks the turn (`shared/marks.
flow_mark`) and the chat then shows "Meeting processed"; this side's only job is to say WHICH FLOW
and WHICH STEP, out of the reaction it is already running.

Four properties, and the last one is the one that has actually bitten:

  1. a dispatch that identifies itself sends both headers;
  2. a dispatch that does not sends exactly what it always sent — no new header, no new field;
  3. `process_meeting` and `feedback_turn` identify themselves;
  4. THE HEADER NAMES ARE agent-api's. On 2026-09-02 two workers named the room's fields
     differently on the two sides the same afternoon and every post-meeting dispatch would have
     422'd. Headers degrade where a body field 422s — which is exactly why these are headers — but
     a header nobody reads is a label nobody sees, so it is still read off the other service's
     source rather than a literal copied by hand.
"""
from __future__ import annotations

import pathlib
import re

import flows_defs.production as production
import flows_defs.production_agent as production_agent  # noqa: F401 — registered via production.build
import flows_steps.agent as agent_mod
import pytest
from flows import Reaction, Registry, StepCtx

from test_link_loop import _StubDB

# Bound at IMPORT time: other files in this suite rebind `dispatch_turn` at module level, and
# reading it inside a test can hand you their stub (the note `test_room_read.py` carries).
REAL_DISPATCH_TURN = agent_mod.dispatch_turn


def _chat_post(monkeypatch):
    """Record the headers and body of the `/api/chat` POST the dispatch makes."""
    seen = {}

    def fake_http(method, url, headers, body=None, timeout=20):
        if url.endswith("/api/chat"):
            seen["headers"] = dict(headers)
            seen["body"] = dict(body or {})
        return 200, {"turns": []}
    monkeypatch.setattr(agent_mod, "http", fake_http)
    return seen


def test_a_dispatch_that_names_its_step_sends_both_headers(monkeypatch):
    seen = _chat_post(monkeypatch)
    REAL_DISPATCH_TURN("7", "meet-97", "[post-meeting] Meeting 97 is over.",
                       flow="post_meeting", step="process_meeting")
    assert seen["headers"]["X-Vexa-Flow"] == "post_meeting"
    assert seen["headers"]["X-Vexa-Flow-Step"] == "process_meeting"
    # THE KICK IS UNTOUCHED. The mark is agent-api's to compose; this side only says who is calling.
    assert seen["body"] == {"prompt": "[post-meeting] Meeting 97 is over.", "session": "meet-97"}


def test_a_dispatch_that_names_neither_sends_what_it_always_sent(monkeypatch):
    """A turn dispatched by anything that does not identify itself must not grow a header — and no
    dispatch grows a BODY field, which is what keeps this safe against an agent-api that predates
    the change (`ChatBody` is `extra="forbid"`)."""
    seen = _chat_post(monkeypatch)
    REAL_DISPATCH_TURN("7", "s", "hello")
    assert seen["body"] == {"prompt": "hello", "session": "s"}
    assert "X-Vexa-Flow" not in seen["headers"] and "X-Vexa-Flow-Step" not in seen["headers"]

    REAL_DISPATCH_TURN("7", "s", "hello", flow="post_meeting")      # half of it is none of it
    assert "X-Vexa-Flow" not in seen["headers"]


def _run_process_meeting(monkeypatch, seen):
    reg = Registry()
    production.build(reg, _StubDB())
    monkeypatch.setattr(production.mt, "meeting_row", lambda uid, m, native=None: {"id": 97})
    monkeypatch.setattr(production.mt, "room_order",
                        lambda uid, mid, participants, names, cap=0: list(participants))
    monkeypatch.setattr(production, "setting", lambda uid, key: "")
    monkeypatch.setattr(production.ag, "dispatch_turn",
                        lambda uid, s, p, room=None, flow="", step="":
                        seen.update(flow=flow, step=step) or 0)
    monkeypatch.setattr(production.ag, "head_sha", lambda uid: "")
    r = Reaction("rid", "sid", "e", {"uid": "7", "meeting_id": 97, "native": "abc",
                                     "organizer": "a@x.test", "title": "T",
                                     "participants": ["a@x.test"], "participant_names": {},
                                     "start": 1_700_003_600.0},
                 "post_meeting", 4, "process_meeting", "running", 1, 0.0, None, None, None)
    reg.steps["process_meeting"](StepCtx(reaction=r, effect_key="k", prior={},
                                         clock_now=1_700_000_000.0, scratch={}, flow=None))
    return seen


def test_process_meeting_identifies_itself(monkeypatch):
    """The step the founder's bubble came from — read off the REACTION, which is where the engine
    already writes both names, rather than from a literal this file could get wrong."""
    seen = _run_process_meeting(monkeypatch, {})
    assert (seen["flow"], seen["step"]) == ("post_meeting", "process_meeting")


def test_the_email_conversation_turn_identifies_itself(monkeypatch):
    """`feedback_turn` wraps somebody's EMAIL in an instruction block. The words in the block are
    ours; the mail is theirs, and neither is a sentence they typed into this chat."""
    seen = {}
    reg = Registry()
    production.build(reg, _StubDB())
    monkeypatch.setattr(production.ag, "collect_outbox", lambda uid, s, h: (None, "h0"))
    monkeypatch.setattr(production.ag, "dispatch_turn",
                        lambda uid, s, p, room=None, flow="", step="":
                        seen.update(flow=flow, step=step) or 0)
    r = Reaction("rid", "sid", "mail.reply",
                 {"uid": "7", "session": "main", "text": "thanks", "from_addr": "a@x.test"},
                 "email_chat", 1, "feedback_turn", "running", 1, 0.0, None, None, None)
    reg.steps["feedback_turn"](StepCtx(reaction=r, effect_key="k", prior={},
                                       clock_now=1_700_000_000.0, scratch={}, flow=None))
    assert (seen["flow"], seen["step"]) == ("email_chat", "feedback_turn")


# ── the CROSS-SERVICE contract, read off the other service's source ─────────────────────────────

def _headers_flows_sends() -> set:
    agent_py = pathlib.Path(__file__).resolve().parents[1] / "src" / "flows_steps" / "agent.py"
    return set(re.findall(r'headers\["(X-Vexa-[A-Za-z-]+)"\]\s*=', agent_py.read_text()))


def test_the_headers_flows_sends_are_the_ones_agent_api_reads():
    here = pathlib.Path(__file__).resolve()
    root = next(p for p in here.parents if (p / "core" / "flows").is_dir())
    plane = root / "core" / "agent" / "control_plane"
    if not plane.is_dir():
        # SKIPPED, NOT PASSED, with the reason named — the by-need cut (decision 43) does not ship
        # the agent domain's source, and a cross-domain guard that quietly answered green in a tree
        # where it checked nothing would report agreement between two services when one is absent.
        pytest.skip(f"agent-api source is not in this tree ({plane} absent) — the flow-mark "
                    "contract cannot be read off a service this checkout does not carry")
    read = set()
    for cand in sorted(plane.rglob("*.py")):
        for name in re.findall(r'request\.headers\.get\("(x-vexa-[a-z-]+)"', cand.read_text()):
            read.add(name)
    sent = {h.lower() for h in _headers_flows_sends()}
    assert sent, "flows no longer names its flow and step on the dispatch — the mark has no input"
    unknown = sent - read
    assert not unknown, (
        f"flows sends headers agent-api does not read: {sorted(unknown)}. A header nobody reads "
        f"degrades silently — the turn simply renders as the person's own words again, which is the "
        f"whole defect. agent-api reads: {sorted(read)}")
