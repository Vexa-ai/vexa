"""THE INVERSE GATE (ADR-0052, "The contract between Meetings and Flows", revised 4 Nov 2026):
meetings depends on Flows for scheduling only. Meeting-api's ad hoc and live paths — ``POST /bots``,
the lifecycle callback, transcripts, webhooks — make no call to Flows and never await one.

Read from the source (``_source_calls``) by function, not by module:

1. **Every use of the two modules that reach Flows** — ``events`` and ``planned_outbox`` — from any
   other module is an edge, and each one is named below with the function it sits in (``EDGES``).
   Anything else fails, whatever it is called: a new publisher, a private helper such as
   ``events._flows_base``, a function handed on without being called. The edges:
   * the planned outbox: ``deliver`` and ``backfill`` in ``__main__``'s outbox loop, and
     ``install`` (the flush hook, a database write) at ``sessions/models.py`` import;
   * the ``meeting.started`` / ``meeting.completed`` publishes, handed — never called — to
     ``events.publish_in_background`` by the lifecycle callback (``_apply_lifecycle_event``);
   * the share mail: ``publish_meeting_shared`` (and ``share_mail_title``, no I/O) in
     ``collector/app.py``'s ``_mail_the_invite``, a Flows feature whose answer the share route
     reports (``notified``), and that function only from the share route
     (``mint_transcript_share_by_id``).
2. **Inside those two modules**, the functions that touch Flows' address or open a client are pinned:
   in ``events``, ``_flows_base``, ``_flows_key`` and ``httpx`` only in ``publish``, and ``publish``
   only in the three ``publish_meeting_*`` functions; in ``planned_outbox``, ``_flows`` and ``httpx``
   only in ``deliver``.
3. **Flows' address and key** (``VEXA_FLOWS_API_URL`` / ``VEXA_FLOWS_API_KEY``) are spelled only in
   ``events._flows_base`` / ``_flows_key`` and ``planned_outbox._flows``, and the URL also in
   ``collector/booking.py``'s ``SCHEDULER_ENV``, which only ``scheduler_configured`` reads (whether a
   scheduler is configured; no call) and ``scheduling_refusal`` names; ``scheduler_configured`` is
   used only by the booking bound and calendar sync's re-arm.

Each rule has a negative control, the plants of the Factory review of #2037 at 7a14d32b among them
(a new publisher awaited on ``POST /bots``, a transcript read awaiting ``_mail_the_invite``,
``httpx`` to ``events._flows_base()`` from ``bot_spawn/router.py``). The behaviour with Flows
stopped or hung is ``test_flows_down.py``.
"""
from __future__ import annotations

import ast

from _source_calls import constants, modules, planted, tree_refs, tree_sites

EVENTS = "meeting_api.events"
OUTBOX = "meeting_api.planned_outbox"
FLOWS_MODULES = ("events.py", "planned_outbox.py")
BACKGROUND = f"{EVENTS}.publish_in_background"
STARTED, COMPLETED = f"{EVENTS}.publish_meeting_started", f"{EVENTS}.publish_meeting_completed"
LIFECYCLE = ("lifecycle/mount.py", ("mount_lifecycle", "_apply_lifecycle_event"))
MAIL = ("collector/app.py", ("build_router", "_mail_the_invite"))
OUTBOX_LOOP = ("__main__.py", ("_attach_background_loops", "_planned_outbox_loop", "_tick"))

#: target → the (module, enclosing functions) each use of it sits in, exactly.
EDGES = {
    f"{OUTBOX}.deliver": {OUTBOX_LOOP},
    f"{OUTBOX}.backfill": {OUTBOX_LOOP},
    f"{OUTBOX}.install": {("sessions/models.py", ())},
    BACKGROUND: {LIFECYCLE},
    STARTED: {LIFECYCLE},
    COMPLETED: {LIFECYCLE},
    f"{EVENTS}.publish_meeting_shared": {MAIL},
    f"{EVENTS}.share_mail_title": {MAIL},
}
#: functions whose users are pinned: the one edge a request path awaits, and the scheduler probe.
CALLERS = {
    "meeting_api.collector.app._mail_the_invite": {
        ("collector/app.py", ("build_router", "mint_transcript_share_by_id"))},
    "meeting_api.collector.booking.scheduler_configured": {
        ("collector/booking.py", ("unscheduled",)),
        ("calendar_sync/service.py", ("_rearm_after_scheduling",))},
    "meeting_api.collector.booking.SCHEDULER_ENV": {
        ("collector/booking.py", ()), ("collector/booking.py", ("scheduler_configured",)),
        ("collector/booking.py", ("scheduling_refusal",))},
}
#: inside the two modules: name → the functions it may be used in.
INSIDE = {
    "events.py": {f"{EVENTS}._flows_base": {("publish",)}, f"{EVENTS}._flows_key": {("publish",)},
                  "httpx": {("publish",)},
                  f"{EVENTS}.publish": {("publish_meeting_started",), ("publish_meeting_completed",),
                                        ("publish_meeting_shared",)}},
    "planned_outbox.py": {f"{OUTBOX}._flows": {("deliver",)}, "httpx": {("deliver",)}},
}
#: where Flows' address and key may be spelled.
SPELLED = {
    "VEXA_FLOWS_API_URL": {("events.py", ("_flows_base",)), ("planned_outbox.py", ("_flows",)),
                           ("collector/booking.py", ())},
    "VEXA_FLOWS_API_KEY": {("events.py", ("_flows_key",)), ("planned_outbox.py", ("_flows",))},
}


def _in(target: str, name: str) -> bool:
    return target == name or target.startswith(name + ".")


def violations(mods=None) -> list:
    """Every use of Flows the contract does not name, as ``module:line functions → target``."""
    out = []
    all_refs = tree_refs(mods)
    for r in all_refs:
        where = (r.rel, r.functions)
        say = f"{r.rel}:{r.line} {'.'.join(r.functions) or '<module>'} → {r.target}"
        if r.rel not in FLOWS_MODULES and (_in(r.target, EVENTS) or _in(r.target, OUTBOX)):
            if where not in EDGES.get(r.target, set()):
                out.append(say)
        for name, allowed in CALLERS.items():
            if r.target == name and where not in allowed:
                out.append(say)
        for name, allowed in INSIDE.get(r.rel, {}).items():
            if _in(r.target, name) and r.functions not in allowed:
                out.append(say)
    for rel, src in (mods or modules()).items():
        for value, functions, line in constants(src, rel):
            if value in SPELLED and (rel, functions) not in SPELLED[value]:
                out.append(f"{rel}:{line} {'.'.join(functions) or '<module>'} spells {value}")
    # the started/completed publishes travel only as publish_in_background's first argument
    handed = [s for s in tree_sites(mods) if s.target == BACKGROUND]
    for s in handed:
        if s.awaited or s.first_arg not in (STARTED, COMPLETED):
            out.append(f"{s.rel}:{s.line} hands {s.first_arg} to publish_in_background"
                       f"{' and awaits it' if s.awaited else ''}")
    for target in (STARTED, COMPLETED):
        uses = sum(1 for r in all_refs if r.target == target and r.rel not in FLOWS_MODULES)
        if uses != sum(1 for s in handed if s.first_arg == target):
            out.append(f"{target} is used other than as publish_in_background's argument")
    return out


def test_meeting_api_reaches_flows_only_on_the_named_edges():
    assert violations() == []


def test_every_named_edge_is_still_there():
    """So the lists cannot outlive the code they name."""
    found = {(r.target, r.rel, r.functions) for r in tree_refs()}
    for table in (EDGES, CALLERS):
        for target, places in table.items():
            for rel, fns in places:
                assert (target, rel, fns) in found, f"{target} in {rel} {fns} is listed but gone"
    for rel, table in INSIDE.items():
        for name, places in table.items():
            for fns in places:
                assert any(f_rel == rel and f_fns == fns and _in(t, name)
                           for t, f_rel, f_fns in found), (rel, name, fns)


def test_the_background_publish_is_not_a_coroutine():
    """``publish_in_background`` is a plain function: calling it cannot make the caller wait."""
    tree = ast.parse(modules()["events.py"])
    (fn,) = [n for n in tree.body if getattr(n, "name", None) == "publish_in_background"]
    assert isinstance(fn, ast.FunctionDef)


# ── negative controls: each plant is caught ─────────────────────────────────────────────────────
def _caught(mods, *needles):
    found = violations(mods)
    for needle in needles:
        assert any(needle in v for v in found), f"{needle!r} not caught: {found}"


def test_a_new_publisher_awaited_on_post_bots_is_caught():
    mods = planted("events.py", """
async def publish_bot_requested(meeting_id):
    return await publish("bot.requested", f"req-{meeting_id}", {"meeting_id": str(meeting_id)})
""")
    mods = planted("bot_spawn/router.py", """
from .. import events as _ev


async def _announce(meeting_id):
    await _ev.publish_bot_requested(meeting_id)
""", mods)
    _caught(mods, "_announce → meeting_api.events.publish_bot_requested",
            "publish_bot_requested → meeting_api.events.publish")


def test_a_transcript_read_awaiting_the_share_mail_is_caught():
    mods = planted("collector/app.py", """
async def get_transcript_by_native(platform, native_meeting_id, request):
    await _mail_the_invite(7, 1, {"id": "g"}, "a@b.example", request)
""")
    _caught(mods, "get_transcript_by_native → meeting_api.collector.app._mail_the_invite")


def test_httpx_to_the_flows_address_from_a_live_path_is_caught():
    mods = planted("bot_spawn/router.py", """
import os

import httpx

from ..events import _flows_base


async def _tell_flows(body):
    async with httpx.AsyncClient() as client:
        await client.post(_flows_base() + "/events", json=body)
    await httpx.AsyncClient().post(os.getenv("VEXA_FLOWS_API_URL") + "/events", json=body)
""")
    _caught(mods, "_tell_flows → meeting_api.events._flows_base",
            "_tell_flows spells VEXA_FLOWS_API_URL")


def test_a_second_client_inside_the_publisher_module_is_caught():
    mods = planted("events.py", """
async def ping_flows():
    import httpx

    async with httpx.AsyncClient() as client:
        await client.get(_flows_base() + "/health")
""")
    _caught(mods, "ping_flows → httpx.AsyncClient", "ping_flows → meeting_api.events._flows_base")


def test_a_publish_handed_on_or_awaited_elsewhere_is_caught():
    mods = planted("bot_spawn/service.py", """
from .. import events as _ev
from .. import planned_outbox as _ob

_PUBLISH = _ev.publish_meeting_started


async def _drain_now(session_factory):
    await _ob.deliver(session_factory)
    _ev.publish_in_background(_ev.publish, "meeting.requested", "x", {})
""")
    _caught(mods, "<module> → meeting_api.events.publish_meeting_started",
            "_drain_now → meeting_api.planned_outbox.deliver",
            "_drain_now → meeting_api.events.publish_in_background",
            "hands meeting_api.events.publish to publish_in_background")


def test_the_scheduler_address_used_to_reach_flows_is_caught():
    mods = planted("bot_spawn/router.py", """
import os

from ..collector.booking import SCHEDULER_ENV, scheduler_configured


def _flows_url():
    return os.environ[SCHEDULER_ENV] if scheduler_configured() else ""
""")
    _caught(mods, "_flows_url → meeting_api.collector.booking.SCHEDULER_ENV",
            "_flows_url → meeting_api.collector.booking.scheduler_configured")
