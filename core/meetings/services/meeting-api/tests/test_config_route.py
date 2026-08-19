"""B1 (#516 C1) — ``PUT /bots/{platform}/{native}/config``, the mid-call bot-config route.

Drives the SAME shipped ``create_app`` mount with the in-memory fakes: a seeded active meeting is
reconfigured → the route publishes an acts.v1-VALID ``reconfigure`` on ``bot_commands:meeting:{id}``
and persists the new language/task onto the meeting record, without moving the FSM.

The published act is validated against the SEALED ``acts.v1`` schema (loaded by path, P8) rather
than against a shape this test invents — a command the bot's own ``parseAct`` would reject must
never leave here.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient
from referencing import Registry, Resource

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo
from meeting_api.lifecycle.stop_router import InMemoryCommandPublisher


# ── the sealed acts.v1 oracle (by path — the same seam the route validates against) ──────────────


def _acts_schema() -> dict:
    rel = Path("meetings") / "contracts" / "acts.v1" / "acts.schema.json"
    for parent in Path(__file__).resolve().parents:
        candidate = parent / rel
        if candidate.is_file():
            return json.loads(candidate.read_text())
    raise FileNotFoundError(f"monorepo root with {rel} not found")


_SCHEMA = _acts_schema()
_REGISTRY = Registry().with_resource(_SCHEMA["$id"], Resource.from_contents(_SCHEMA))


def assert_valid_act(obj: dict) -> None:
    """The published message must validate as an acts.v1 ``Act`` (the whole union, not just the
    Reconfigure branch — the bot narrows on the union)."""
    jsonschema.Draft202012Validator(
        {"$ref": f"{_SCHEMA['$id']}#/$defs/Act"}, registry=_REGISTRY
    ).validate(obj)


def _seed(repo, *, user_id, platform, native, status="active", data=None):
    m = asyncio.run(
        repo.create_meeting(
            user_id=user_id, platform=platform, native_meeting_id=native, data=data or {}
        )
    )
    sid = f"sess-{m['id']}"
    asyncio.run(repo.create_session(meeting_id=m["id"], session_uid=sid))
    if status != "requested":
        asyncio.run(repo.update_meeting_status(session_uid=sid, status=status))
    return m


def _app(repo, pub):
    return TestClient(create_app(meeting_repo=repo, command_publisher=pub))


# ── the value: a running bot's language and task change ──────────────────────────────────────────


def test_put_config_publishes_a_valid_reconfigure_and_persists_it():
    repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
    m = _seed(repo, user_id=7, platform="google_meet", native="m1", data={"language": "en"})

    r = _app(repo, pub).put(
        "/bots/google_meet/m1/config",
        headers={"x-user-id": "7"},
        json={"language": "es", "task": "translate"},
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["meeting_id"] == m["id"]
    assert body["config"] == {"language": "es", "task": "translate"}

    assert pub.published, "no reconfigure command published"
    chan, msg = pub.published[0]
    assert chan == f"bot_commands:meeting:{m['id']}"
    act = json.loads(msg)
    assert_valid_act(act)
    assert act == {"action": "reconfigure", "language": "es", "task": "translate"}

    latest = asyncio.run(repo.find_latest(7, "google_meet", "m1"))
    assert latest["data"]["language"] == "es"
    assert latest["data"]["task"] == "translate"
    assert latest["status"] == "active", "the config route must never move the FSM"


def test_a_field_the_caller_omitted_is_left_alone():
    """Absent ≠ null. A task-only command must not silently unpin the language the bot is using."""
    repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
    _seed(repo, user_id=7, platform="google_meet", native="m1", data={"language": "en"})

    r = _app(repo, pub).put(
        "/bots/google_meet/m1/config", headers={"x-user-id": "7"}, json={"task": "translate"}
    )
    assert r.status_code == 202, r.text
    act = json.loads(pub.published[0][1])
    assert "language" not in act, "an omitted field must not be published as null"
    assert r.json()["config"] == {"language": "en", "task": "translate"}
    latest = asyncio.run(repo.find_latest(7, "google_meet", "m1"))
    assert latest["data"]["language"] == "en", "the record's language survived a task-only change"


def test_explicit_null_clears_the_pin():
    """The contract types language as ``string | null`` — null is the documented way back to
    model-detect, and it must reach the bot as null, not as an absence."""
    repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
    _seed(repo, user_id=7, platform="google_meet", native="m1", data={"language": "en"})

    r = _app(repo, pub).put(
        "/bots/google_meet/m1/config", headers={"x-user-id": "7"}, json={"language": None}
    )
    assert r.status_code == 202, r.text
    act = json.loads(pub.published[0][1])
    assert_valid_act(act)
    assert act == {"action": "reconfigure", "language": None}
    assert asyncio.run(repo.find_latest(7, "google_meet", "m1"))["data"]["language"] is None


def test_allowed_languages_is_forwarded_but_never_persisted():
    """acts.v1 declares it, so it is contract-legal to send; nothing consumes it, so storing it
    would read as a constraint the stack enforces."""
    repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
    _seed(repo, user_id=7, platform="google_meet", native="m1")

    r = _app(repo, pub).put(
        "/bots/google_meet/m1/config",
        headers={"x-user-id": "7"},
        json={"language": "hi", "allowedLanguages": ["hi", "en"]},
    )
    assert r.status_code == 202, r.text
    act = json.loads(pub.published[0][1])
    assert_valid_act(act)
    assert act["allowedLanguages"] == ["hi", "en"]
    assert "allowedLanguages" not in r.json()["config"]
    assert "allowedLanguages" not in asyncio.run(repo.find_latest(7, "google_meet", "m1"))["data"]


# ── the refusals ─────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("body", [{}, None])
def test_a_command_that_commands_nothing_is_422(body):
    repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
    _seed(repo, user_id=7, platform="google_meet", native="m1")
    r = _app(repo, pub).put(
        "/bots/google_meet/m1/config", headers={"x-user-id": "7"}, json=body
    )
    assert r.status_code == 422, r.text
    assert not pub.published


def test_an_unknown_field_is_422_not_a_silent_drop():
    repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
    _seed(repo, user_id=7, platform="google_meet", native="m1")
    r = _app(repo, pub).put(
        "/bots/google_meet/m1/config", headers={"x-user-id": "7"}, json={"lang": "es"}
    )
    assert r.status_code == 422, r.text
    assert "lang" in r.text
    assert not pub.published


def test_a_wrong_typed_field_is_refused_against_the_sealed_shape():
    repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
    _seed(repo, user_id=7, platform="google_meet", native="m1")
    r = _app(repo, pub).put(
        "/bots/google_meet/m1/config", headers={"x-user-id": "7"}, json={"language": 5}
    )
    assert r.status_code == 422, r.text
    assert not pub.published


def test_unsupported_platform_is_422_before_the_lookup():
    repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
    r = _app(repo, pub).put(
        "/bots/webex/m1/config", headers={"x-user-id": "7"}, json={"language": "es"}
    )
    assert r.status_code == 422, r.text
    assert not pub.published


def test_unknown_meeting_is_404():
    repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
    r = _app(repo, pub).put(
        "/bots/google_meet/nope/config", headers={"x-user-id": "7"}, json={"language": "es"}
    )
    assert r.status_code == 404, r.text
    assert not pub.published


def test_a_stopping_bot_is_404():
    """A bot already asked to leave has no live config to change."""
    repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
    _seed(
        repo, user_id=7, platform="google_meet", native="m1", data={"stop_requested": True}
    )
    r = _app(repo, pub).put(
        "/bots/google_meet/m1/config", headers={"x-user-id": "7"}, json={"language": "es"}
    )
    assert r.status_code == 404, r.text
    assert not pub.published


def test_another_users_meeting_is_404():
    repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
    _seed(repo, user_id=7, platform="google_meet", native="m1")
    r = _app(repo, pub).put(
        "/bots/google_meet/m1/config", headers={"x-user-id": "8"}, json={"language": "es"}
    )
    assert r.status_code == 404, r.text
    assert not pub.published


def test_no_identity_is_401():
    repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
    r = _app(repo, pub).put("/bots/google_meet/m1/config", json={"language": "es"})
    assert r.status_code == 401, r.text
    assert not pub.published


def test_a_dead_command_bus_is_a_narrow_503_not_an_opaque_500():
    class Dead:
        published: list = []

        async def publish(self, channel, message):
            raise RuntimeError("redis down")

    repo, pub = InMemoryMeetingRepo(), Dead()
    _seed(repo, user_id=7, platform="google_meet", native="m1")
    r = _app(repo, pub).put(
        "/bots/google_meet/m1/config", headers={"x-user-id": "7"}, json={"language": "es"}
    )
    assert r.status_code == 503, r.text
    assert "did NOT reach the bot" in r.text


# ── the seal: the route the contract declares is the route we serve ──────────────────────────────


def test_the_served_route_is_the_sealed_one():
    """The gateway forwards this exact path; a route registered under any other shape leaves the
    sealed operation 404ing behind a green conformance gate (the failure this issue names)."""
    app = create_app(
        meeting_repo=InMemoryMeetingRepo(), command_publisher=InMemoryCommandPublisher()
    )
    routes = {
        (method.upper(), path)
        for path, item in app.openapi()["paths"].items()
        for method in item
    }
    assert ("PUT", "/bots/{platform}/{native_meeting_id}/config") in routes
