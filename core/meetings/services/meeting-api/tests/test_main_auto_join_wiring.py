"""F154, carried over to the start route (ADR-0052 s2d) — `publish_status` must be a real callable.

The auto-join sweep's `_tick` once passed `publish_status=publish_status` while the function it named
had been deleted (commit 1699aa3ac), so every sweep raised NameError and calendar auto-join was
silently dead. The sweep is gone (s2d); the same callable now reaches the planned-row start route
through `__main__._planned_start_deps`, which Flows' booked_bot calls for every booked meeting. This
drives that REAL builder — not a reimplementation — and proves its `publish_status` publishes a
`meeting.status` frame on the owner's channel, best-effort, exactly as the sweep's closure did.

It also carries the gate on the scheduling half of the Meetings↔Flows contract (ADR-0052, revised
4 Nov 2026): every BOOKED start goes through ``POST /internal/meetings/{meeting_id}/start``. Its
inverse — the ad hoc and live paths make no call to Flows — is ``test_flows_edges.py``.
"""
from __future__ import annotations

import importlib.util
import json

from _source_calls import planted, tree_refs, tree_sites

import meeting_api.__main__ as main_mod


class _FakeRedis:
    def __init__(self, fail: bool = False):
        self.published: list[tuple[str, str]] = []
        self.fail = fail

    async def publish(self, channel, message):
        if self.fail:
            raise RuntimeError("redis down")
        self.published.append((channel, message))


async def test_the_start_routes_publish_status_is_wired_and_publishes():
    redis_client = _FakeRedis()
    deps = main_mod._planned_start_deps(redis_client, None)
    assert callable(deps.publish_status)

    await deps.publish_status(
        user_id=42, meeting_id="m-1", native_id="n-1", status="joining", when="2026-09-03T08:52:00Z",
    )

    assert len(redis_client.published) == 1
    channel, message = redis_client.published[0]
    assert channel == "u:42:meetings"
    assert json.loads(message) == {
        "type": "meeting.status",
        "meeting_id": "m-1",
        "native": "n-1",
        "status": "joining",
        "when": "2026-09-03T08:52:00Z",
    }
    # best-effort: a failed publish never fails the start
    await main_mod._planned_start_deps(_FakeRedis(fail=True), None).publish_status(
        user_id=1, meeting_id=1, native_id="x", status="scheduled", when=None)


SPAWN = "meeting_api.bot_spawn"
#: What makes a start a BOOKED start: the planned row it claims by id, and the join time it waits for.
BOOKED_KEYWORDS = {"planned_meeting_id", "join_at"}
PLANNED = "bot_spawn/planned_start.py"
#: name → the (module, enclosing functions) each use of it sits in, exactly.
USES = {
    "start_planned_meeting": {("bot_spawn/start_router.py", ("build_start_router", "start_planned"))},
    "start_row": {(PLANNED, ("start_planned_meeting",))},
    "request_bot": {(PLANNED, ("start_row",)), ("bot_spawn/router.py", ("build_router", "create_bot")),
                    ("lifecycle/meet_respawn.py", ("respawn_due",))},
}
#: the calls that pass a booked start's keys by name: the start route into ``start_row``, and
#: ``request_bot`` handing ``join_at`` to the bot's invocation.
BOOKED_CALLS = {(PLANNED, ("start_planned_meeting",), "start_row"),
                ("bot_spawn/service.py", ("request_bot",), "build_invocation")}
#: the ``request_bot`` calls that pass ``**kwargs``: the start route's own keys, and the Meet
#: respawn's ``SPAWN_FIELDS`` (checked to carry no booked key).
SPLATS = {(PLANNED, ("start_row",)), ("lifecycle/meet_respawn.py", ("respawn_due",))}


def _named(target: str, name: str) -> bool:
    return target.endswith(f".{name}") and target.startswith(SPAWN)


def booked_start_violations(mods=None) -> list:
    """Every way a bot could start for a booked row other than through the start route."""
    out = []
    for r in tree_refs(mods):
        for name, allowed in USES.items():
            if _named(r.target, name) and (r.rel, r.functions) not in allowed:
                out.append(f"{r.rel}:{r.line} {'.'.join(r.functions) or '<module>'} → {name}")
    for s in tree_sites(mods):
        name = s.target.rsplit(".", 1)[-1]
        if s.keywords & BOOKED_KEYWORDS and (s.rel, s.functions, name) not in BOOKED_CALLS:
            out.append(f"{s.rel}:{s.line} {'.'.join(s.functions)} passes "
                       f"{sorted(s.keywords & BOOKED_KEYWORDS)} to {name}")
        if _named(s.target, "request_bot") and s.splat and (s.rel, s.functions) not in SPLATS:
            out.append(f"{s.rel}:{s.line} {'.'.join(s.functions)} splats into request_bot")
    return out


def test_every_booked_start_goes_through_the_start_route():
    """ADR-0052 §3 and "The contract between Meetings and Flows": every booked start goes through
    ``POST /internal/meetings/{meeting_id}/start``. Checked by function, not by module:

    * meeting-api keeps no clock of its own: no auto-join sweep, no ``_auto_join_loop`` /
      ``auto_join_tick``;
    * the route is mounted and answered by ``start_router``'s handler ``start_planned``, the only
      user of ``start_planned_meeting``; that is the only user of ``start_row``, the only place a
      row is claimed by id with its ``join_at``; ``request_bot`` is used only there, by
      ``POST /bots`` (``create_bot``) and by the Meet respawn (``respawn_due``), and neither of
      those passes a booked start's keys, by name or through ``**kwargs``.

    NOT covered, a known parallel path (ADR-0052 §6): ``POST /bots`` claims the room's newest
    planned row, a booked one included, inside ``create_meeting_guarded``; a follow-up slice
    closes it."""
    from fastapi.testclient import TestClient

    from meeting_api import create_app
    from meeting_api.bot_spawn.start_router import START_PATH
    from meeting_api.lifecycle.meet_respawn import SPAWN_FIELDS

    assert importlib.util.find_spec("meeting_api.bot_spawn.auto_join") is None
    source = open(main_mod.__file__).read()
    assert "_auto_join_loop" not in source and "auto_join_tick" not in source
    assert START_PATH == "/internal/meetings/{meeting_id}/start"
    # mounted on the one app, answered by start_router's handler (its own refusal of no subject)
    r = TestClient(create_app()).post("/internal/meetings/1/start")
    assert (r.status_code, r.json().get("detail")) == (401, "Missing user identity"), r.text
    assert booked_start_violations() == []
    assert not (set(SPAWN_FIELDS) & BOOKED_KEYWORDS), "the Meet respawn carries a booked start's keys"
    found = {(r.rel, r.functions) for r in tree_refs()}
    for places in USES.values():
        assert places <= found, f"listed but gone: {places - found}"


def _caught(mods, needle):
    found = booked_start_violations(mods)
    assert any(needle in v for v in found), f"{needle!r} not caught: {found}"


def test_a_start_row_sweep_beside_the_route_is_caught():
    """The Factory review's plant: a second clock in the start route's own module."""
    mods = planted(PLANNED, """
async def sweep_due_bookings(repo, runtime, now):
    for row in await repo.list_scheduled_meetings():
        await start_row(row, repo=repo, runtime=runtime, get_context=None, now=now)
""")
    _caught(mods, "sweep_due_bookings → start_row")


def test_another_caller_of_the_start_is_caught():
    mods = planted("calendar_sync/service.py", """
from ..bot_spawn.planned_start import start_planned_meeting


async def _start_imported_now(row, repo, runtime):
    await start_planned_meeting(row["id"], user_id=row["user_id"], repo=repo, runtime=runtime)
""")
    _caught(mods, "_start_imported_now → start_planned_meeting")


def test_a_booked_spawn_by_another_path_is_caught():
    mods = planted("bot_spawn/router.py", """
async def create_bot_for_booking(repo, runtime, row, join_at, extra):
    await request_bot(repo, runtime, user_id=row["user_id"], planned_meeting_id=row["id"],
                      join_at=join_at)
    await request_bot(repo, runtime, **extra)
""")
    _caught(mods, "create_bot_for_booking → request_bot")
    _caught(mods, "passes ['join_at', 'planned_meeting_id'] to request_bot")
    _caught(mods, "create_bot_for_booking splats into request_bot")
