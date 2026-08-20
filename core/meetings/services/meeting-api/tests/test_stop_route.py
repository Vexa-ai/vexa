"""DELETE /bots/{platform}/{native} — the user-stop route (lifecycle/stop_router).

Drives the SAME shipped ``create_app`` mount with the in-memory fakes: a seeded active meeting is
stopped → the route marks it ``stopping`` + ``stop_requested`` and publishes the bot's ``leave``
command on ``bot_commands:meeting:{id}``. (The bot's terminal lifecycle event — classified by the
existing callback — is exercised by the lifecycle tests; here we assert the trigger.)
"""
from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo
from meeting_api.lifecycle.stop_router import InMemoryCommandPublisher


def _seed(repo, *, user_id, platform, native, status="active"):
    """Seed a meeting AT a given lifecycle status. The status is load-bearing for the stop path:
    `stopping` may only be written over a status in which the bot reached the meeting (#807)."""
    m = asyncio.run(
        repo.create_meeting(user_id=user_id, platform=platform, native_meeting_id=native, data={})
    )
    sid = f"sess-{m['id']}"
    asyncio.run(repo.create_session(meeting_id=m["id"], session_uid=sid))
    if status != "requested":
        asyncio.run(repo.update_meeting_status(session_uid=sid, status=status))
    return m


def _seed_active(repo, *, user_id, platform, native):
    return _seed(repo, user_id=user_id, platform=platform, native=native, status="active")


def test_delete_bots_stops_active_meeting():
    repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
    app = create_app(meeting_repo=repo, command_publisher=pub)
    m = _seed_active(repo, user_id=7, platform="google_meet", native="m1")

    r = TestClient(app).delete("/bots/google_meet/m1", headers={"x-user-id": "7"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "stopping"
    assert body["meeting_id"] == m["id"]

    # the leave command was published on the bot's command channel
    assert pub.published, "no leave command published"
    chan, msg = pub.published[0]
    assert chan == f"bot_commands:meeting:{m['id']}"
    assert json.loads(msg)["action"] == "leave"

    # the meeting row was marked stopping + stop_requested (the user-intent signal)
    latest = asyncio.run(repo.find_latest(7, "google_meet", "m1"))
    assert latest["status"] == "stopping"
    assert latest["data"].get("stop_requested") is True


# ── #807: stopping a bot that never reached the meeting must not claim it was live ───────────────


def test_stopping_a_pre_active_bot_preserves_the_stage_it_died_in():
    """The producer half of the never-admitted-but-`completed` bug. Writing `stopping` over
    `awaiting_admission` destroyed the only record of the stage, and every downstream reader then
    concluded the bot had been live. The stage must survive the stop; the user-intent signal rides
    in `data` where it always did."""
    for stage in ("requested", "joining", "awaiting_admission"):
        repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
        app = create_app(meeting_repo=repo, command_publisher=pub)
        _seed(repo, user_id=7, platform="google_meet", native=f"m-{stage}", status=stage)

        r = TestClient(app).delete(f"/bots/google_meet/m-{stage}", headers={"x-user-id": "7"})
        assert r.status_code == 200, r.text

        latest = asyncio.run(repo.find_latest(7, "google_meet", f"m-{stage}"))
        assert latest["status"] == stage, (
            f"a stop at {stage} must not overwrite the stage with 'stopping' — that is the evidence "
            f"the terminal classifier needs to tell 'never admitted' from 'was live'"
        )
        assert latest["data"].get("stop_requested") is True, "user intent must still be recorded"


def test_stop_still_moves_a_live_bot_to_stopping():
    """No-regression: for a bot that DID reach the meeting, `stopping` is exactly right."""
    for stage in ("active",):
        repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
        app = create_app(meeting_repo=repo, command_publisher=pub)
        _seed(repo, user_id=7, platform="google_meet", native=f"live-{stage}", status=stage)

        r = TestClient(app).delete(f"/bots/google_meet/live-{stage}", headers={"x-user-id": "7"})
        assert r.status_code == 200, r.text
        latest = asyncio.run(repo.find_latest(7, "google_meet", f"live-{stage}"))
        assert latest["status"] == "stopping"


def test_delete_bots_404_when_no_active_meeting():
    repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
    r = TestClient(create_app(meeting_repo=repo, command_publisher=pub)).delete(
        "/bots/google_meet/nope", headers={"x-user-id": "7"}
    )
    assert r.status_code == 404
    assert not pub.published


def test_delete_bots_401_without_identity():
    r = TestClient(
        create_app(meeting_repo=InMemoryMeetingRepo(), command_publisher=InMemoryCommandPublisher())
    ).delete("/bots/google_meet/m1")
    assert r.status_code == 401


def test_second_delete_never_re_stops_a_pre_active_bot():
    """The stop trigger is one-shot for EVERY stage. Preserving the pre-active stage means the row
    is still findable by `find_active` afterwards, so the guard has to be the user-intent flag —
    otherwise a redelivered DELETE would publish a second leave command and tear the workload down
    again. (The SQL adapter's active set contains `stopping` too, so this was already reachable in
    production for a live bot; the fake's narrower set hid it.)"""
    for stage in ("requested", "awaiting_admission", "active"):
        repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
        app = create_app(meeting_repo=repo, command_publisher=pub)
        _seed(repo, user_id=7, platform="google_meet", native=f"once-{stage}", status=stage)

        first = TestClient(app).delete(f"/bots/google_meet/once-{stage}", headers={"x-user-id": "7"})
        assert first.status_code == 200, first.text
        published = len(pub.published)

        second = TestClient(app).delete(f"/bots/google_meet/once-{stage}", headers={"x-user-id": "7"})
        assert second.status_code == 404, f"{stage}: a redelivered stop must not re-trigger"
        assert len(pub.published) == published, f"{stage}: second DELETE re-published a leave command"


# ── "explicit stop must be stop, evict" (founder ruling 2026-08-17) ──────────────────────────────
#
# A stop means the MEETING, not one container. `find_active` returns the newest row — enough for the
# POST dedup, wrong for a stop: a SIBLING row on the same (user, platform, native) survived, and a
# sibling still waiting in the lobby walked into the meeting after the user had said no. Siblings
# are not hypothetical; they are the exact shape the auto-join sweep's duplicate guard exists for
# (a manual "Send bot now" plus a calendar import that failed to adopt it — rows 26237 + 26251 on
# one native id, live 2026-08-17).


def _stopped(repo, user_id, platform, native):
    rows = asyncio.run(repo.find_active_rows(user_id, platform, native))
    return [row for row in rows if (row.get("data") or {}).get("stop_requested")]


def test_stop_evicts_a_sibling_waiting_in_the_lobby():
    """The founder's case: one bot is live, a second is still `awaiting_admission`. ONE stop takes
    both down — flag, leave command, and (for the booting one) its workload."""
    repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
    app = create_app(meeting_repo=repo, command_publisher=pub)
    live = _seed(repo, user_id=7, platform="google_meet", native="mjm", status="active")
    waiting = _seed(repo, user_id=7, platform="google_meet", native="mjm",
                    status="awaiting_admission")

    r = TestClient(app).delete("/bots/google_meet/mjm", headers={"x-user-id": "7"})
    assert r.status_code == 200, r.text

    stopped_ids = {row["id"] for row in _stopped(repo, 7, "google_meet", "mjm")}
    assert stopped_ids == {live["id"], waiting["id"]}, (
        "the stop left a sibling running — it will join the meeting the user just ended"
    )

    # each bot listens on its OWN meeting-scoped channel, so eviction means addressing it by id
    channels = {chan for chan, _ in pub.published}
    assert channels == {
        f"bot_commands:meeting:{live['id']}",
        f"bot_commands:meeting:{waiting['id']}",
    }
    # `also_stopped` names what the stop took BEYOND the row the caller would have gotten from
    # `find_active` (the newest — here the waiting sibling). Reported so a duplicate bot is
    # visible in the response and not merely in a log line.
    assert r.json()["meeting_id"] == waiting["id"]
    assert r.json()["also_stopped"] == [live["id"]]


def test_stop_evicts_every_pre_active_sibling_and_preserves_each_stage():
    """Eviction must not cost the #807 guarantee: each evicted row keeps the stage it died in."""
    repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
    app = create_app(meeting_repo=repo, command_publisher=pub)
    seeded = {
        stage: _seed(repo, user_id=7, platform="google_meet", native="multi", status=stage)
        for stage in ("requested", "joining", "awaiting_admission")
    }

    r = TestClient(app).delete("/bots/google_meet/multi", headers={"x-user-id": "7"})
    assert r.status_code == 200, r.text

    rows = {row["id"]: row for row in asyncio.run(repo.find_active_rows(7, "google_meet", "multi"))}
    assert len(rows) == 3
    for stage, meeting in seeded.items():
        row = rows[meeting["id"]]
        assert (row["data"] or {}).get("stop_requested") is True, f"{stage} survived the stop"
        assert row["status"] == stage, f"{stage}: eviction overwrote the stage it died in (#807)"


def test_stopped_siblings_never_re_dispatch_the_occurrence():
    """The two halves meeting: every row the stop evicted classifies USER_STOPPED, so calendar sync
    will not recreate the occurrence for ANY of them. Eviction without this would only move the
    problem — the sibling dies now and is reborn on the next sync."""
    from meeting_api.lifecycle.occurrence import Disposition, disposition

    repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
    app = create_app(meeting_repo=repo, command_publisher=pub)
    _seed(repo, user_id=7, platform="google_meet", native="reborn", status="active")
    _seed(repo, user_id=7, platform="google_meet", native="reborn", status="awaiting_admission")

    assert TestClient(app).delete(
        "/bots/google_meet/reborn", headers={"x-user-id": "7"}
    ).status_code == 200

    for row in asyncio.run(repo.find_active_rows(7, "google_meet", "reborn")):
        # the bots' own terminal events land later; the flag is what survives into them
        for terminal in ("completed", "failed"):
            assert disposition({**row, "status": terminal}) is Disposition.USER_STOPPED, (
                "a stopped row whose terminal lands as %s must stay final for the occurrence"
                % terminal
            )


def test_a_sibling_spawned_after_the_stop_is_still_evicted():
    """The one-shot guard is PER ROW, not per request. A redelivered DELETE is a no-op (404), but a
    NEW sibling that appeared after the first stop is a new bot heading for the same meeting — and
    the user's stop already said no to that room."""
    repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
    app = create_app(meeting_repo=repo, command_publisher=pub)
    _seed(repo, user_id=7, platform="google_meet", native="late", status="active")

    assert TestClient(app).delete(
        "/bots/google_meet/late", headers={"x-user-id": "7"}
    ).status_code == 200
    published = len(pub.published)

    # nothing new → the redelivery guard holds
    assert TestClient(app).delete(
        "/bots/google_meet/late", headers={"x-user-id": "7"}
    ).status_code == 404
    assert len(pub.published) == published

    # a fresh sibling arrives → the stop reaches it
    latecomer = _seed(repo, user_id=7, platform="google_meet", native="late", status="joining")
    r = TestClient(app).delete("/bots/google_meet/late", headers={"x-user-id": "7"})
    assert r.status_code == 200, r.text
    assert (chan := f"bot_commands:meeting:{latecomer['id']}") in {c for c, _ in pub.published}, chan


def test_stop_tears_down_an_evicted_siblings_workload():
    """A booting sibling has not subscribed to its command channel yet, which is precisely why it
    outlived the stop. The leave command alone cannot reach it — its workload must be torn down."""
    from meeting_api.bot_spawn.fakes import FakeRuntimeClient

    repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
    runtime = FakeRuntimeClient()
    app = create_app(meeting_repo=repo, command_publisher=pub, runtime=runtime)
    _seed(repo, user_id=7, platform="google_meet", native="tear", status="active")
    booting = _seed(repo, user_id=7, platform="google_meet", native="tear", status="joining")
    asyncio.run(repo.set_bot_container(meeting_id=booting["id"], bot_container_id="wl-booting"))

    assert TestClient(app).delete(
        "/bots/google_meet/tear", headers={"x-user-id": "7"}
    ).status_code == 200
    assert "wl-booting" in runtime.deleted, (
        "the evicted sibling's workload survived — a bot nobody can command is still joining"
    )


def test_stop_marks_a_row_that_has_no_session_yet():
    """A row spawned so recently that its session write has not landed still has to carry the user
    intent — the flag is the only durable evidence, and without it the calendar recreates the
    occurrence and sends another bot."""
    repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
    app = create_app(meeting_repo=repo, command_publisher=pub)
    sessionless = asyncio.run(repo.create_meeting(
        user_id=7, platform="google_meet", native_meeting_id="nosess", data={}
    ))

    r = TestClient(app).delete("/bots/google_meet/nosess", headers={"x-user-id": "7"})
    assert r.status_code == 200, r.text
    rows = {row["id"]: row for row in asyncio.run(repo.find_active_rows(7, "google_meet", "nosess"))}
    assert (rows[sessionless["id"]]["data"] or {}).get("stop_requested") is True
