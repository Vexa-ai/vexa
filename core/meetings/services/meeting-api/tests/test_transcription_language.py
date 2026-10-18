"""The language a meeting bot transcribes in (transcription-language.v1), meeting-api's half.

Three things are pinned here:

  * THE READING — every golden of the sealed contract is accepted or refused here exactly as the
    contract's own validate.mjs decides, so meeting-api, identity and the contract agree;
  * THE TIERS — on every spawn path meeting-api serves (POST /bots, which the API, the terminal, the
    MCP and the invite-by-email flow all call, and the calendar sweep, which calls request_bot
    directly), the bot's invocation and the stored meeting carry the first tier that is set:
    meeting, then the person's default, then the deployment's, then auto;
  * THE LIVE CHANGE — PUT /bots/{platform}/{native}/config reaches the running bot as an acts.v1
    `reconfigure`, only the meeting's owner or an owner/contributor of its workspace may send it,
    and the stored setting changes only when a bot received it.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn import build_router
from meeting_api.bot_spawn import service as spawn_service
from meeting_api.bot_spawn import transcription_language as tlang
from meeting_api.bot_spawn.auto_join import auto_join_tick
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.lifecycle.reconfigure_router import RoleUnavailable

USER = 7
URL = "https://meet.google.com/abc-defg-hij"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.delenv(tlang.ENV_LANGUAGE, raising=False)
    monkeypatch.delenv(tlang.ENV_ALLOWED, raising=False)


# ── the reading: the contract's goldens ────────────────────────────────────────────────────────────

def _golden_dir() -> Path:
    rel = Path("meetings") / "contracts" / "transcription-language.v1" / "golden"
    for parent in Path(__file__).resolve().parents:
        if (parent / rel).is_dir():
            return parent / rel
    raise FileNotFoundError(str(rel))


def _read(shape: str, value):
    """Run one golden through the reading meeting-api applies to that shape."""
    if shape == "SpawnFields":
        return tlang.spawn_tier(value.get("language"), value.get("allowed_languages"))
    if shape == "ConfigUpdate":
        return tlang.config_update(value)
    if shape == "DeploymentDefault":
        return tlang.deployment_tier(value)
    if shape in ("LanguageSetting", "UserPreference", "UserPreferenceUpdate"):
        return tlang.spawn_tier(value.get("language") or None, value.get("allowed_languages") or None)
    if shape == "EffectiveSetting":
        return tlang.spawn_tier(value["language"], value["allowed_languages"] or None)
    return None


READ_SHAPES = {"SpawnFields", "ConfigUpdate", "DeploymentDefault", "LanguageSetting",
               "UserPreference", "UserPreferenceUpdate", "EffectiveSetting"}


@pytest.mark.parametrize("path", sorted(_golden_dir().glob("*.json")), ids=lambda p: p.name)
def test_every_golden_is_read_as_the_contract_decides(path):
    doc = json.loads(path.read_text())
    shape = path.name.split(".")[0]
    if shape == "Refused":
        if doc["shape"] not in READ_SHAPES:
            pytest.skip(f"{doc['shape']} is not read by meeting-api")
        with pytest.raises(tlang.LanguageRefused):
            _read(doc["shape"], doc["value"])
    elif shape in READ_SHAPES:
        _read(shape, doc)


def test_precedence_is_whole_setting_and_first_tier_wins():
    meeting = tlang.LanguageSetting("en", ())
    user = tlang.LanguageSetting("de", ("de", "en"))
    deployment = tlang.LanguageSetting(None, ("fr",))
    assert tlang.resolve(meeting, user, deployment) == {
        "language": "en", "allowed_languages": [], "source": "meeting"}
    assert tlang.resolve(None, user, deployment)["source"] == "user"
    assert tlang.resolve(None, None, deployment) == {
        "language": None, "allowed_languages": ["fr"], "source": "deployment"}
    assert tlang.resolve(None, None, None) == {"language": None, "allowed_languages": [], "source": "auto"}


# ── the tiers through POST /bots ───────────────────────────────────────────────────────────────────

def _spawn_client(monkeypatch, context=None):
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()

    async def fetch(_user_id):
        return context or {}

    monkeypatch.setattr(spawn_service, "_fetch_bot_context", fetch)
    app = FastAPI()
    app.include_router(build_router(repo, runtime))
    return TestClient(app), repo, runtime


def _spawn(client, **body):
    return client.post("/bots", headers={"x-user-id": str(USER)}, json={
        "platform": "google_meet", "native_meeting_id": "abc-defg-hij", "meeting_url": URL, **body})


def _invocation(runtime) -> dict:
    return json.loads(runtime.specs[-1]["env"]["VEXA_BOT_CONFIG"])


def _stored(repo) -> dict:
    return next(iter(repo._meetings.values()))["data"]["transcription_language"]


DE_EN_USER = {"transcription_language": {"language": "de", "allowed_languages": ["de", "en"]}}


def test_the_deployment_default_reaches_the_bot_when_nobody_else_sets_one(monkeypatch):
    monkeypatch.setenv(tlang.ENV_LANGUAGE, "de")
    monkeypatch.setenv(tlang.ENV_ALLOWED, "de,en")
    client, repo, runtime = _spawn_client(monkeypatch)
    r = _spawn(client)
    assert r.status_code == 201, r.text
    inv = _invocation(runtime)
    assert inv["language"] == "de" and inv["allowedLanguages"] == ["de", "en"]
    assert _stored(repo) == {"language": "de", "allowed_languages": ["de", "en"], "source": "deployment"}
    assert r.json()["data"]["transcription_language"]["source"] == "deployment"


def test_the_persons_default_beats_the_deployments(monkeypatch):
    monkeypatch.setenv(tlang.ENV_LANGUAGE, "fr")
    client, repo, runtime = _spawn_client(monkeypatch, context=DE_EN_USER)
    assert _spawn(client).status_code == 201
    inv = _invocation(runtime)
    assert inv["language"] == "de" and inv["allowedLanguages"] == ["de", "en"]
    assert _stored(repo)["source"] == "user"


def test_the_meeting_beats_both_and_does_not_inherit_a_lower_tiers_list(monkeypatch):
    monkeypatch.setenv(tlang.ENV_ALLOWED, "de,en")
    client, repo, runtime = _spawn_client(monkeypatch, context=DE_EN_USER)
    assert _spawn(client, language="en").status_code == 201
    inv = _invocation(runtime)
    assert inv["language"] == "en" and "allowedLanguages" not in inv
    assert _stored(repo) == {"language": "en", "allowed_languages": [], "source": "meeting"}


def test_auto_on_the_request_overrides_every_default(monkeypatch):
    monkeypatch.setenv(tlang.ENV_LANGUAGE, "de")
    client, repo, runtime = _spawn_client(monkeypatch, context=DE_EN_USER)
    assert _spawn(client, language="auto").status_code == 201
    inv = _invocation(runtime)
    assert "language" not in inv and "allowedLanguages" not in inv
    assert _stored(repo)["source"] == "meeting"


def test_allowed_languages_on_the_request_reach_the_bot(monkeypatch):
    client, repo, runtime = _spawn_client(monkeypatch)
    assert _spawn(client, allowed_languages=["de", "en"]).status_code == 201
    assert _invocation(runtime)["allowedLanguages"] == ["de", "en"]


def test_nothing_set_anywhere_is_auto(monkeypatch):
    client, repo, runtime = _spawn_client(monkeypatch)
    assert _spawn(client).status_code == 201
    assert "language" not in _invocation(runtime)
    assert _stored(repo)["source"] == "auto"


@pytest.mark.parametrize("body", [
    {"language": "German"},
    {"allowed_languages": "de,en"},
    {"language": "fr", "allowed_languages": ["de", "en"]},
    {"allowed_languages": ["de", "de"]},
    {"language": "auto", "allowed_languages": ["de"]},
])
def test_a_refused_language_is_a_422_before_any_row_or_bot(monkeypatch, body):
    client, repo, runtime = _spawn_client(monkeypatch)
    r = _spawn(client, **body)
    assert r.status_code == 422, r.text
    assert not repo._meetings and not runtime.specs


def test_an_unreadable_stored_default_does_not_stop_the_spawn(monkeypatch):
    client, repo, runtime = _spawn_client(
        monkeypatch, context={"transcription_language": {"language": "German", "allowed_languages": []}})
    assert _spawn(client).status_code == 201
    assert _stored(repo)["source"] == "auto"


# ── the calendar sweep (request_bot called directly) ───────────────────────────────────────────────

def test_the_calendar_sweep_applies_the_persons_and_the_deployments_defaults(monkeypatch):
    now = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)

    async def run(context):
        repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
        repo._meetings[1] = {
            "id": 1, "user_id": USER, "platform": "google_meet", "native_meeting_id": "abc-defg-hij",
            "platform_specific_id": "abc-defg-hij", "status": "scheduled", "bot_container_id": None,
            "start_time": None, "end_time": None, "created_at": "x", "updated_at": "x",
            "data": {"title": "t", "auto_join": True, "scheduled_at": now.isoformat(),
                     "constructed_meeting_url": URL},
        }

        async def fetch(_uid):
            return context

        monkeypatch.setattr(spawn_service, "_fetch_bot_context", fetch)
        counters = await auto_join_tick(repo, runtime, transcribe_gate=lambda: None, now=now,
                                        token_secret="s", redis_url="redis://r", allow_uncapped=True)
        assert counters["spawned"] == 1
        return _invocation(runtime), repo._meetings[1]["data"]["transcription_language"]

    monkeypatch.setenv(tlang.ENV_LANGUAGE, "de")
    inv, stored = asyncio.run(run({}))
    assert inv["language"] == "de" and stored["source"] == "deployment"
    inv, stored = asyncio.run(run(DE_EN_USER))
    assert inv["allowedLanguages"] == ["de", "en"] and stored["source"] == "user"


# ── the deployment default at boot ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("env", [
    {tlang.ENV_LANGUAGE: "German"},
    {tlang.ENV_ALLOWED: "de, en"},
    {tlang.ENV_LANGUAGE: "fr", tlang.ENV_ALLOWED: "de,en"},
])
def test_a_refused_deployment_default_stops_the_boot(env):
    from meeting_api.__main__ import _transcription_language_at_boot
    from meeting_api.config_preflight import ConfigError

    with pytest.raises(ConfigError, match="DEFAULT_TRANSCRIPTION"):
        _transcription_language_at_boot(env)


def test_a_valid_or_empty_deployment_default_boots():
    from meeting_api.__main__ import _transcription_language_at_boot

    _transcription_language_at_boot({})
    _transcription_language_at_boot({tlang.ENV_LANGUAGE: "de", tlang.ENV_ALLOWED: "de,en"})


# ── the live change: PUT /bots/{platform}/{native}/config ──────────────────────────────────────────

class ListeningBus:
    """A command bus where each published channel has `receivers` subscribers."""

    def __init__(self, receivers: int = 1) -> None:
        self.receivers = receivers
        self.published: list[tuple[str, dict]] = []

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, json.loads(message)))
        return self.receivers


class Roles:
    def __init__(self, roles=None, down: bool = False) -> None:
        self.roles = roles or {}
        self.down = down

    async def role_of(self, user_id, workspace_id):
        if self.down:
            raise RoleUnavailable("identity down")
        return self.roles.get((user_id, workspace_id))


OWNER, MEMBER = 7, 8
WS = "ws_team"


def _seed(repo, *, status="active", workspace_id=None, native="abc-defg-hij"):
    data = {"transcription_language": {"language": None, "allowed_languages": [], "source": "auto"}}
    if workspace_id:
        data["workspace_id"] = workspace_id
    m = asyncio.run(repo.create_meeting(user_id=OWNER, platform="google_meet",
                                        native_meeting_id=native, data=data))
    sid = f"sess-{m['id']}"
    asyncio.run(repo.create_session(meeting_id=m["id"], session_uid=sid))
    if status != "requested":
        asyncio.run(repo.update_meeting_status(session_uid=sid, status=status))
    return m["id"]


def _live(bus=None, roles=None):
    repo = InMemoryMeetingRepo()
    bus = bus or ListeningBus()
    app = create_app(meeting_repo=repo, command_publisher=bus, workspace_roles=roles or Roles())
    return TestClient(app), repo, bus


def _put(client, body, user=OWNER, workspaces=None):
    headers = {"x-user-id": str(user)}
    if workspaces:
        headers["x-user-workspaces"] = workspaces
    return client.put("/bots/google_meet/abc-defg-hij/config", headers=headers, json=body)


def test_the_owner_switches_a_running_bot_to_german():
    client, repo, bus = _live()
    mid = _seed(repo)
    r = _put(client, {"language": "de"})
    assert r.status_code == 202, r.text
    assert r.json() == {"meeting_id": mid, "platform": "google_meet", "native_meeting_id": "abc-defg-hij",
                        "transcription_language": {"language": "de", "allowed_languages": [],
                                                   "source": "meeting"}}
    assert bus.published == [(f"bot_commands:meeting:{mid}",
                              {"action": "reconfigure", "language": "de", "allowedLanguages": []})]
    assert repo._meetings[mid]["data"]["transcription_language"]["language"] == "de"


def test_a_change_replaces_the_setting_and_both_nulls_is_auto():
    client, repo, bus = _live()
    mid = _seed(repo)
    assert _put(client, {"allowed_languages": ["de", "en"]}).status_code == 202
    assert bus.published[-1][1] == {"action": "reconfigure", "language": None,
                                    "allowedLanguages": ["de", "en"]}
    assert _put(client, {"language": None, "allowed_languages": None}).status_code == 202
    assert bus.published[-1][1] == {"action": "reconfigure", "language": None, "allowedLanguages": []}
    assert repo._meetings[mid]["data"]["transcription_language"] == {
        "language": None, "allowed_languages": [], "source": "meeting"}


def test_a_workspace_contributor_may_change_it():
    client, repo, bus = _live(roles=Roles({(MEMBER, WS): "contributor"}))
    mid = _seed(repo, workspace_id=WS)
    r = _put(client, {"language": "en"}, user=MEMBER, workspaces=WS)
    assert r.status_code == 202, r.text
    assert bus.published[0][0] == f"bot_commands:meeting:{mid}"


def test_a_workspace_reader_is_refused_and_nothing_is_sent():
    """DENY: a member who may read the meeting may not change how it is transcribed."""
    client, repo, bus = _live(roles=Roles({(MEMBER, WS): "reader"}))
    mid = _seed(repo, workspace_id=WS)
    r = _put(client, {"language": "en"}, user=MEMBER, workspaces=WS)
    assert r.status_code == 403, r.text
    assert "owner or contributor" in r.json()["detail"]
    assert bus.published == []
    assert repo._meetings[mid]["data"]["transcription_language"]["source"] == "auto"


def test_somebody_outside_the_meeting_cannot_see_it():
    """DENY: not the owner, not a member of its workspace — the same 404 as no meeting at all."""
    client, repo, bus = _live(roles=Roles({(MEMBER, "ws_other"): "owner"}))
    _seed(repo, workspace_id=WS)
    assert _put(client, {"language": "en"}, user=MEMBER, workspaces="ws_other").status_code == 404
    assert _put(client, {"language": "en"}, user=MEMBER).status_code == 404
    assert bus.published == []


def test_an_unconfirmable_role_is_a_503_never_a_guess():
    client, repo, bus = _live(roles=Roles(down=True))
    _seed(repo, workspace_id=WS)
    assert _put(client, {"language": "en"}, user=MEMBER, workspaces=WS).status_code == 503
    assert bus.published == []


def test_a_bot_that_is_not_listening_is_a_409_and_nothing_is_stored():
    client, repo, bus = _live(bus=ListeningBus(receivers=0))
    mid = _seed(repo, status="requested")
    r = _put(client, {"language": "de"})
    assert r.status_code == 409, r.text
    assert repo._meetings[mid]["data"]["transcription_language"]["source"] == "auto"


def test_no_running_bot_is_a_404():
    client, repo, bus = _live()
    _seed(repo, status="completed")
    assert _put(client, {"language": "de"}).status_code == 404
    assert bus.published == []


@pytest.mark.parametrize("body", [
    {}, {"language": "German"}, {"language": "auto"}, {"language": "fr", "allowed_languages": ["de", "en"]},
    {"language": "de", "speaker": "x"}, {"allowed_languages": "de"},
])
def test_a_refused_change_is_a_422_and_nothing_is_sent(body):
    client, repo, bus = _live()
    _seed(repo)
    assert _put(client, body).status_code == 422
    assert bus.published == []
