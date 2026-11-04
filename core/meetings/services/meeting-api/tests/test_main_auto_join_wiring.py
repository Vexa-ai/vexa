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

from _source_calls import tree_sites

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


def _named(site, name: str) -> bool:
    return site.target.endswith(f".{name}") and site.target.startswith(SPAWN)


def test_every_booked_start_goes_through_the_start_route():
    """ADR-0052 §3 and "The contract between Meetings and Flows": every booked start goes through
    ``POST /internal/meetings/{meeting_id}/start``, the one call Flows makes into meetings.

    * meeting-api keeps no clock of its own: no auto-join sweep, no ``_auto_join_loop`` /
      ``auto_join_tick``;
    * the route's handler is ``start_router``'s, and only it calls ``start_planned_meeting``;
    * only ``planned_start`` starts a bot for a booked row — claiming the row by id with its
      ``join_at`` (``start_row``, ``request_bot(planned_meeting_id=…, join_at=…)``); every other
      ``request_bot`` call (``POST /bots``, the Meet respawn) passes neither."""
    from meeting_api.bot_spawn.start_router import START_PATH
    from meeting_api.lifecycle.meet_respawn import SPAWN_FIELDS

    assert importlib.util.find_spec("meeting_api.bot_spawn.auto_join") is None
    source = open(main_mod.__file__).read()
    assert "_auto_join_loop" not in source and "auto_join_tick" not in source
    assert START_PATH == "/internal/meetings/{meeting_id}/start"
    from fastapi.testclient import TestClient

    from meeting_api import create_app

    # mounted on the one app, answered by start_router's handler (its own refusal of no subject)
    r = TestClient(create_app()).post("/internal/meetings/1/start")
    assert (r.status_code, r.json().get("detail")) == (401, "Missing user identity"), r.text

    sites = tree_sites()
    starters = {s.rel for s in sites if _named(s, "start_planned_meeting")}
    assert starters == {"bot_spawn/start_router.py"}, starters
    assert {s.rel for s in sites if _named(s, "start_row")} == {"bot_spawn/planned_start.py"}
    spawns = [s for s in sites if _named(s, "request_bot")]
    assert {s.rel for s in spawns} == {"bot_spawn/router.py", "bot_spawn/planned_start.py",
                                       "lifecycle/meet_respawn.py"}
    for s in spawns:
        if s.rel == "bot_spawn/planned_start.py":
            continue
        assert not (s.keywords & BOOKED_KEYWORDS), f"{s.rel}:{s.line} starts a booked bot"
        assert not s.splat or s.rel == "lifecycle/meet_respawn.py", f"{s.rel}:{s.line} splats"
    assert not (set(SPAWN_FIELDS) & BOOKED_KEYWORDS), "the Meet respawn carries a booked start's keys"
