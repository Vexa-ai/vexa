"""A NEW PERSON'S `whats_waiting` IS NOT AN EMPTY LIST — the rig's half of the same property.

Founder, 2026-09-04: *"a new person's first step is to try a meeting"*. `core/flows` holds the
product half — the `onboarding` flow puts a pending reaction in the queue from account creation
until that person has watched Vexa transcribe something (`tests/test_onboarding_flow.py`). This
file is the rig's half, and the two are different surfaces on purpose: the MCP queue is served from
flows, the rig answers the dogfood control connection out of its own first-turn branch.

WHAT MADE THIS WORTH A TEST is that both halves fail the same way and neither fails loudly. An
empty queue is indistinguishable from a finished one, so a person who has just connected is told
nothing is waiting — by the one call every agent makes first, on the one turn that decides whether
they stay. Vexa-ai/vexa#1637 is that failure on the product half; nothing held the rig to it at all.

The fresh subject here is the real one: no `.scaffolded` in the workspace, and no meetings behind
the gateway. Every read goes through the recorder `conftest.as_user` installs, so this test opens
no socket and asserts on what the rig ANSWERED, not on how it got there.
"""
from __future__ import annotations

import json

from conftest import as_user, tool

#: The two reads the first-turn branch stands on, in the order `whats_waiting` makes them. Keys are
#: url fragments; the recorder answers with the first that matches, so `.scaffolded` is listed
#: before `/meetings` — its url contains both.
FRESH = {"path=.scaffolded": (404, {}), "/meetings": (200, {"meetings": []})}


def _waiting(monkeypatch, uid="7", routes=None):
    as_user(monkeypatch, uid, routes=routes if routes is not None else dict(FRESH))
    return json.loads(tool("whats_waiting")())


def test_a_fresh_subject_is_not_answered_with_an_empty_list(monkeypatch):
    """THE WHOLE POINT (Vexa-ai/vexa#1637). Nothing here is about WHICH item comes back — only
    that one does, for somebody the instance has never seen before."""
    out = _waiting(monkeypatch)
    assert out["waiting"] >= 1, out
    assert out["items"], "a brand-new person was told nothing is waiting"


def test_the_first_step_it_returns_is_to_try_a_meeting(monkeypatch):
    """…and it is the founder's step, not a chore of ours. *"we want them to try a meeting so they
    are activated"* — so the item a new person gets must ask for a meeting, and must not open on
    the workspace scaffold, which is our own setup and reads as a stranger handing over homework."""
    out = _waiting(monkeypatch)
    first = out["items"][0]
    assert first["kind"] == "welcome", out["items"]
    assert "meeting" in json.dumps(first).lower()
    assert {i.get("kind") for i in out["items"]} == {"welcome"}, (
        "the first turn is the welcome and only the welcome")

    # The moves offered back name the meeting, in the person's own next sentence.
    options = " ".join(out.get("next_options") or []).lower()
    assert "meeting link" in options, out.get("next_options")


def test_it_is_the_fresh_subject_that_gets_it_and_not_everyone(monkeypatch):
    """The negative half, which is what keeps the assertion above meaningful: a person whose
    workspace is already scaffolded and who has meetings behind the gateway is past their first
    step, and must not be welcomed again as if they had just arrived."""
    out = _waiting(monkeypatch, routes={"path=.scaffolded": (200, {"content": "{}"}),
                                        "/meetings": (200, {"meetings": [{"id": "1"}]}),
                                        "/reactions": (200, {"reactions": []})})
    assert "welcome" not in {i.get("kind") for i in out.get("items", [])}, out
