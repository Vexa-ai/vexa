"""Speak in a live meeting (#1866) — ``POST``/``DELETE /bots/{platform}/{native}/speak``.

  * ``POST`` publishes ONE ``acts.v1`` ``speak`` (carrying the response's ``request_id`` as ``id``) on
    the chosen bot's ``bot_commands:meeting:{id}`` channel — the caller's own newest active,
    voice-enabled row, else one bound to a workspace the caller edits — or refuses with the documented
    code, publishing nothing.
  * ``DELETE`` publishes ``speak_stop`` to every bot it resolves; it needs neither ``active`` nor the
    voice flag, and is not rate-limited — stopping always works.
  * The audit lines carry who, which meeting, the request id and the length — never the spoken words.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo
from meeting_api.lifecycle.reconfigure_router import RoleUnavailable
from meeting_api.lifecycle.stop_router import InMemoryCommandPublisher

OWNER, MEMBER, STRANGER = 7, 8, 9
WS = "ws_team"
PLAT, NATIVE = "google_meet", "abc-defg-hij"
URL = f"/bots/{PLAT}/{NATIVE}/speak"


def _contract(rel: str) -> dict:
    for parent in Path(__file__).resolve().parents:
        if (parent / rel).is_file():
            return json.loads((parent / rel).read_text())
    raise FileNotFoundError(rel)


def _validator(rel: str, defn: str) -> Draft202012Validator:
    root = _contract(rel)
    resource = Resource.from_contents(root, default_specification=DRAFT202012)
    registry = Registry().with_resource(uri=root["$id"], resource=resource)
    return Draft202012Validator({"$ref": f"{root['$id']}#/$defs/{defn}"}, registry=registry)


ACT = _validator("core/meetings/contracts/acts.v1/acts.schema.json", "Act")


class Bus(InMemoryCommandPublisher):
    """The command bus; ``receivers`` is what redis answers (1 = the bot is subscribed)."""

    def __init__(self, receivers: int = 1, down: bool = False) -> None:
        super().__init__()
        self.receivers = receivers
        self.down = down

    async def publish(self, channel, message):
        if self.down:
            raise ConnectionError("redis down")
        await super().publish(channel, message)
        return self.receivers

    def acts(self) -> list[tuple[str, dict]]:
        return [(c, json.loads(m)) for c, m in self.published]


class Roles:
    def __init__(self, roles=None, down: bool = False) -> None:
        self.roles = roles or {}
        self.down = down

    async def role_of(self, user_id, workspace_id):
        if self.down:
            raise RoleUnavailable("identity down")
        return self.roles.get((user_id, workspace_id))


def _seed(repo, *, status="active", voice=True, workspace_id=None, stop_requested=False,
          native=NATIVE, user=OWNER):
    data = {}
    if voice:
        data["voice_agent_enabled"] = True
    if workspace_id:
        data["workspace_id"] = workspace_id
    if stop_requested:
        data["stop_requested"] = True
    m = asyncio.run(repo.create_meeting(user_id=user, platform=PLAT, native_meeting_id=native, data=data))
    sid = f"sess-{m['id']}"
    asyncio.run(repo.create_session(meeting_id=m["id"], session_uid=sid))
    if status != "requested":
        asyncio.run(repo.update_meeting_status(session_uid=sid, status=status))
    return m["id"]


def _live(bus=None, roles=None):
    repo, bus = InMemoryMeetingRepo(), bus or Bus()
    app = create_app(meeting_repo=repo, command_publisher=bus, workspace_roles=roles or Roles())
    return TestClient(app), repo, bus


def _h(user=OWNER, workspaces=None) -> dict:
    h = {"x-user-id": str(user)}
    if workspaces:
        h["x-user-workspaces"] = workspaces
    return h


# ── A1 · the owner speaks: 200 queued, exactly one valid act ────────────────────────────────────


def test_owner_speak_publishes_one_speak_act_carrying_the_request_id():
    client, repo, bus = _live()
    mid = _seed(repo)

    r = client.post(URL, headers=_h(), json={"text": "hi"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"status": "queued", "request_id": body["request_id"], "meeting_id": mid,
                    "platform": PLAT, "native_meeting_id": NATIVE, "chars": 2}
    assert bus.acts() == [(f"bot_commands:meeting:{mid}",
                           {"action": "speak", "text": "hi", "id": body["request_id"]})]
    ACT.validate(bus.acts()[0][1])


def test_a_voice_preset_rides_the_act_and_auto_does_not():
    client, repo, bus = _live()
    _seed(repo)

    assert client.post(URL, headers=_h(), json={"text": "a", "voice": "af_bella"}).status_code == 200
    assert client.post(URL, headers=_h(), json={"text": "b", "voice": "auto"}).status_code == 200

    first, second = bus.acts()[0][1], bus.acts()[1][1]
    assert first["voice"] == "af_bella"
    assert "voice" not in second
    for _, act in bus.acts():
        ACT.validate(act)


def test_mcp_and_unknown_fields_are_accepted_and_ignored():
    client, repo, bus = _live()
    _seed(repo)

    r = client.post(URL, headers=_h(), json={"text": "hi", "language": "en", "asked_by_a_human": True,
                                             "format": "pcm", "sample_rate": 24000, "provider": "x",
                                             "something_else": 1})

    assert r.status_code == 200, r.text
    assert set(bus.acts()[0][1]) == {"action", "text", "id"}


def test_only_the_newest_active_voice_bot_speaks():
    client, repo, bus = _live()
    _seed(repo)
    newest = _seed(repo)

    assert client.post(URL, headers=_h(), json={"text": "hi"}).status_code == 200

    assert [c for c, _ in bus.acts()] == [f"bot_commands:meeting:{newest}"]


# ── A2 · who may speak ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["owner", "contributor"])
def test_a_workspace_editor_can_speak_through_a_bound_bot(role):
    client, repo, bus = _live(roles=Roles({(MEMBER, WS): role}))
    mid = _seed(repo, workspace_id=WS)

    r = client.post(URL, headers=_h(MEMBER, WS), json={"text": "hi"})

    assert r.status_code == 200, r.text
    assert [c for c, _ in bus.acts()] == [f"bot_commands:meeting:{mid}"]


def test_a_workspace_reader_is_refused_403_naming_the_role():
    client, repo, bus = _live(roles=Roles({(MEMBER, WS): "reader"}))
    _seed(repo, workspace_id=WS)

    r = client.post(URL, headers=_h(MEMBER, WS), json={"text": "hi"})

    assert r.status_code == 403
    assert "reader" in r.json()["detail"]
    assert bus.published == []


def test_a_stranger_gets_404():
    client, repo, bus = _live()
    _seed(repo, workspace_id=WS)

    r = client.post(URL, headers=_h(STRANGER), json={"text": "hi"})

    assert r.status_code == 404
    assert bus.published == []


def test_identity_down_is_503():
    client, repo, bus = _live(roles=Roles(down=True))
    _seed(repo, workspace_id=WS)

    r = client.post(URL, headers=_h(MEMBER, WS), json={"text": "hi"})

    assert r.status_code == 503
    assert bus.published == []


# ── A3 · 409: the bot cannot speak right now ────────────────────────────────────────────────────


@pytest.mark.parametrize("seed", [
    {"status": "joining"},
    {"status": "awaiting_admission"},
    {"voice": False},
    {"stop_requested": True},
])
def test_a_bot_that_cannot_speak_is_409_and_nothing_is_published(seed):
    client, repo, bus = _live()
    _seed(repo, **seed)

    r = client.post(URL, headers=_h(), json={"text": "hi"})

    assert r.status_code == 409, r.text
    assert bus.published == []


def test_a_bot_without_the_voice_flag_says_how_to_fix_it():
    client, repo, _ = _live()
    _seed(repo, voice=False)

    r = client.post(URL, headers=_h(), json={"text": "hi"})

    assert r.json()["detail"] == ("this bot was started without voice_agent_enabled: true, so it "
                                  "cannot speak — start a bot with that flag")


def test_no_bot_listening_is_409():
    client, repo, _ = _live(bus=Bus(receivers=0))
    _seed(repo)

    r = client.post(URL, headers=_h(), json={"text": "hi"})

    assert r.status_code == 409


def test_bus_down_is_503():
    client, repo, _ = _live(bus=Bus(down=True))
    _seed(repo)

    r = client.post(URL, headers=_h(), json={"text": "hi"})

    assert r.status_code == 503


# ── A4 · malformed requests ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("body", [
    {},
    {"text": ""},
    {"text": "   "},
    {"text": 5},
    {"text": "x" * 501},
    {"text": "hi", "voice": "bad voice!"},
    {"text": "hi", "voice": "v" * 65},
    {"text": "hi", "audio_base64": "AAAA"},
    {"text": "hi", "audio_url": "https://example.com/a.wav"},
    ["not", "an", "object"],
])
def test_a_malformed_body_is_422(body):
    client, repo, bus = _live()
    _seed(repo)

    r = client.post(URL, headers=_h(), json=body)

    assert r.status_code == 422, r.text
    assert bus.published == []


def test_audio_input_points_at_514():
    client, repo, _ = _live()
    _seed(repo)

    r = client.post(URL, headers=_h(), json={"text": "hi", "audio_base64": "AAAA"})

    assert r.json()["detail"] == "audio input is not served by this route yet — see #514"


def test_exactly_500_characters_is_accepted():
    client, repo, _ = _live()
    _seed(repo)

    assert client.post(URL, headers=_h(), json={"text": "x" * 500}).status_code == 200


def test_a_body_that_is_not_json_is_422():
    client, repo, _ = _live()
    _seed(repo)

    r = client.post(URL, headers={**_h(), "content-type": "application/json"}, content=b"{nope")

    assert r.status_code == 422


@pytest.mark.parametrize("platform", ["voice_note", "browser_session", "vexa_call", "skype"])
def test_a_platform_without_a_meeting_to_speak_into_is_422(platform):
    client, _, _ = _live()

    r = client.post(f"/bots/{platform}/{NATIVE}/speak", headers=_h(), json={"text": "hi"})

    assert r.status_code == 422


def test_a_body_over_16_kib_is_413():
    client, repo, bus = _live()
    _seed(repo)

    r = client.post(URL, headers=_h(), json={"text": "hi", "padding": "x" * 16_384})

    assert r.status_code == 413
    assert bus.published == []


def test_no_identity_is_401():
    client, repo, _ = _live()
    _seed(repo)

    assert client.post(URL, json={"text": "hi"}).status_code == 401
    assert client.delete(URL).status_code == 401


# ── A5 · the per-meeting limit ──────────────────────────────────────────────────────────────────


def test_the_seventh_speak_in_a_minute_is_429_and_not_published_and_chat_is_unaffected():
    client, repo, bus = _live()
    _seed(repo)

    codes = [client.post(URL, headers=_h(), json={"text": f"n{i}"}).status_code for i in range(7)]

    assert codes == [200] * 6 + [429]
    assert len(bus.published) == 6
    r = client.post(URL, headers=_h(), json={"text": "again"})
    assert int(r.headers["Retry-After"]) >= 1
    chat = client.post(f"/bots/{PLAT}/{NATIVE}/chat", headers=_h(), json={"text": "still fine"})
    assert chat.status_code == 200, chat.text


# ── A6 · DELETE stops speech ────────────────────────────────────────────────────────────────────


def test_delete_sends_speak_stop_without_needing_active_or_the_flag():
    client, repo, bus = _live()
    mid = _seed(repo, status="joining", voice=False)

    r = client.delete(URL, headers=_h())

    assert r.status_code == 200, r.text
    assert r.json() == {"status": "stop_sent", "bots": 1}
    assert bus.acts() == [(f"bot_commands:meeting:{mid}", {"action": "speak_stop"})]
    ACT.validate(bus.acts()[0][1])


def test_delete_reaches_every_owned_bot_and_is_not_rate_limited():
    client, repo, bus = _live()
    a, b = _seed(repo), _seed(repo)
    for i in range(6):
        client.post(URL, headers=_h(), json={"text": f"n{i}"})
    bus.published.clear()

    r = client.delete(URL, headers=_h())

    assert r.status_code == 200
    assert r.json()["bots"] == 2
    assert sorted(c for c, _ in bus.acts()) == sorted([f"bot_commands:meeting:{a}",
                                                       f"bot_commands:meeting:{b}"])


def test_delete_by_a_workspace_editor_and_refusals():
    client, repo, bus = _live(roles=Roles({(MEMBER, WS): "contributor"}))
    _seed(repo, workspace_id=WS)

    assert client.delete(URL, headers=_h(MEMBER, WS)).status_code == 200
    assert client.delete(URL, headers=_h(STRANGER)).status_code == 404


def test_delete_with_nothing_listening_is_409_and_bus_down_is_503():
    for bus, code in ((Bus(receivers=0), 409), (Bus(down=True), 503)):
        client, repo, _ = _live(bus=bus)
        _seed(repo)
        assert client.delete(URL, headers=_h()).status_code == code


# ── audit: who, which meeting, the request id — never the words ─────────────────────────────────


def test_the_audit_lines_never_carry_the_spoken_text(capsys):
    client, repo, _ = _live()
    _seed(repo)
    secret = "the merger closes on Friday"

    capsys.readouterr()
    ok = client.post(URL, headers=_h(), json={"text": secret})
    client.post(URL, headers=_h(STRANGER), json={"text": secret})
    client.delete(URL, headers=_h())

    assert ok.status_code == 200
    out = capsys.readouterr()
    logged = out.out + out.err
    assert "bot_speak_queued" in logged
    assert "bot_speak_refused" in logged
    assert "bot_speak_stop_sent" in logged
    assert ok.json()["request_id"] in logged
    assert secret not in logged
