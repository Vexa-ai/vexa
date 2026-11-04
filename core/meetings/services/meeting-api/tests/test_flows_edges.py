"""THE INVERSE GATE (ADR-0052, "The contract between Meetings and Flows", revised 4 Nov 2026):
meetings depends on Flows for scheduling only. Meeting-api's ad hoc and live paths — ``POST /bots``,
the lifecycle callback, transcripts, webhooks — make no call to Flows and never await one.

The meeting-api → Flows edges, all of them:

* the planned outbox's drain (``planned_outbox.deliver`` / ``backfill``), run by ``__main__``'s
  outbox loop — the one fact Flows schedules from;
* the ``meeting.started`` / ``meeting.completed`` publishes, handed off the lifecycle callback's path
  to ``events.publish_in_background`` and never awaited there;
* the share mail (``events.publish_meeting_shared`` in ``collector/app.py``'s ``_mail_the_invite``):
  a Flows feature — Flows sends that mail — whose answer the share route reports
  (``notified: {address: true|false}``); not on a live path.

Read from the source (``_source_calls``), so a new call anywhere else fails the build. The behaviour
with Flows stopped or hung is ``test_flows_down.py``.
"""
from __future__ import annotations

import ast

from _source_calls import modules, sites, tree_sites

EVENTS = "meeting_api.events"
OUTBOX = "meeting_api.planned_outbox"
#: Every function that reaches Flows' intake.
FLOWS_CALLS = {
    f"{EVENTS}.publish", f"{EVENTS}.publish_meeting_started", f"{EVENTS}.publish_meeting_completed",
    f"{EVENTS}.publish_meeting_shared", f"{OUTBOX}.deliver", f"{OUTBOX}.backfill",
}
BACKGROUND = f"{EVENTS}.publish_in_background"
#: (module, the innermost enclosing functions, target) — the edges this contract allows.
ALLOWED = {
    ("__main__.py", ("_planned_outbox_loop", "_tick"), f"{OUTBOX}.backfill"),
    ("__main__.py", ("_planned_outbox_loop", "_tick"), f"{OUTBOX}.deliver"),
    ("collector/app.py", ("_mail_the_invite",), f"{EVENTS}.publish_meeting_shared"),
}
#: The modules that name Flows' address or key: the publisher, the drain, and the booking bound that
#: reads whether a scheduler is configured (``collector/booking.py``, no call).
ADDRESS_READERS = {
    "VEXA_FLOWS_API_URL": {"events.py", "planned_outbox.py", "collector/booking.py"},
    "VEXA_FLOWS_API_KEY": {"events.py", "planned_outbox.py"},
}


def _flows_calls(all_sites) -> list:
    return [s for s in all_sites if s.target in FLOWS_CALLS and s.rel not in ("events.py",
                                                                               "planned_outbox.py")]


def _allowed(site) -> bool:
    return any(site.rel == rel and site.target == target and site.functions[-len(fns):] == fns
               for rel, fns, target in ALLOWED)


def test_meeting_api_calls_flows_only_on_the_allowed_edges():
    found = _flows_calls(tree_sites())
    stray = [f"{s.rel}:{s.line} {'.'.join(s.functions)} → {s.target}" for s in found
             if not _allowed(s)]
    assert not stray, ("meeting-api reaches Flows outside the scheduling contract (ADR-0052): "
                       + "; ".join(stray))
    # and every allowed edge is still there, so the list cannot outlive the code it names
    assert {(rel, fns, t) for rel, fns, t in ALLOWED
            if any(_allowed(s) and (s.rel, s.target) == (rel, t) for s in found)} == ALLOWED


def test_the_lifecycle_callback_hands_its_publishes_to_the_background():
    mount = [s for s in sites(modules()["lifecycle/mount.py"], "lifecycle/mount.py")]
    handed = {s.first_arg for s in mount if s.target == BACKGROUND}
    assert handed == {f"{EVENTS}.publish_meeting_started", f"{EVENTS}.publish_meeting_completed"}
    assert not [s for s in mount if s.target in FLOWS_CALLS], (
        "the lifecycle callback calls a Flows publish itself")
    awaited = [f"{s.rel}:{s.line}" for s in tree_sites() if s.target == BACKGROUND and s.awaited]
    assert not awaited, f"a background publish is awaited: {awaited}"


def test_the_background_publish_is_not_a_coroutine():
    """``publish_in_background`` is a plain function: calling it cannot make the caller wait."""
    tree = ast.parse(modules()["events.py"])
    (fn,) = [n for n in tree.body if getattr(n, "name", None) == "publish_in_background"]
    assert isinstance(fn, ast.FunctionDef)


def test_only_the_publisher_the_drain_and_the_booking_bound_name_flows_address():
    found: dict = {key: set() for key in ADDRESS_READERS}
    for rel, src in modules().items():
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Constant) and node.value in found:
                found[node.value].add(rel)
    assert found == ADDRESS_READERS


# ── negative controls: the gate sees a planted edge ─────────────────────────────────────────────
PLANTED = '''
from .. import events
from ..events import publish_meeting_completed
from .. import planned_outbox


async def request_bot_route(meeting_id):
    await events.publish_meeting_started(meeting_id, "n", "google_meet", 7)
    await publish_meeting_completed(meeting_id, "n", "google_meet", 7, "stopped")
    await planned_outbox.deliver(None)
    events.publish_in_background(events.publish_meeting_started, meeting_id)
'''


def test_a_planted_call_on_a_live_path_is_caught():
    planted = sites(PLANTED, "bot_spawn/router.py")
    caught = {s.target for s in _flows_calls(planted)}
    assert caught == {f"{EVENTS}.publish_meeting_started", f"{EVENTS}.publish_meeting_completed",
                      f"{OUTBOX}.deliver"}
    assert all(s.awaited for s in _flows_calls(planted))
    assert [s.first_arg for s in planted if s.target == BACKGROUND] == [
        f"{EVENTS}.publish_meeting_started"]
