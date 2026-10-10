"""The session write-back (``PUT /internal/browser-session/{session_uid}``): the stored browser session
changes only through meeting-api, only with SESSION_PROFILE files, and only from the live
authenticated bot.

Offline: the spawn runs the SHIPPED ``request_bot`` over the in-memory fakes, so the meeting rows,
sessions, MeetingTokens and write-back URLs are the ones a real spawn makes; the store is an
in-memory writer that records every put. Every refusal asserts the store was not touched. The profile,
the route and the body rule are the session-profile.v1 contract's: its PathVectors, WritebackBody and
Refused goldens drive the matcher and the parser here, as the PathVectors drive @vexa/remote-browser's.
"""
from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn import request_bot
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.meeting_token import mint_meeting_token
from meeting_api.session_profile import (
    MAX_FILE_BYTES,
    SESSION_PROFILE,
    SESSION_WRITEBACK_ROUTE,
    WRITEBACK_GRACE_S,
    InvalidSessionProfile,
    build_router,
    is_profile_path,
    parse_profile_upload,
    profile_path_refusal,
)
from meeting_api.session_profile import profile as profile_mod
from meeting_api.session_profile import router as router_mod

SECRET = "test-admin-token"
IDENTITY = "userdata/bot-identity-1"
PREFIX = f"{IDENTITY}/browser-data"
NOW = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
VALID = {
    "Local State": b'{"os_crypt":{}}',
    "Default/Cookies": b"rotated-cookie-db",
    "Default/Local Storage/leveldb/CURRENT": b"MANIFEST-000001\n",
    "Default/Session Storage/000003.log": b"session-storage",
}


# ── the one definition: the session-profile.v1 contract ─────────────────────────────────────────

def _contract_dir() -> Path:
    rel = Path("core") / "meetings" / "contracts" / "session-profile.v1"
    for parent in Path(__file__).resolve().parents:
        if (parent / rel / "session-profile.schema.json").is_file():
            return parent / rel
    raise FileNotFoundError(f"monorepo root with {rel} not found")


CONTRACT_DIR = _contract_dir()


def _goldens(shape: str) -> list[tuple[str, object]]:
    found = sorted((CONTRACT_DIR / "golden").glob(f"{shape}.*.json"))
    assert found, f"the session-profile.v1 contract carries no {shape} goldens"
    return [(p.name, json.loads(p.read_text(encoding="utf-8"))) for p in found]


def _path_vectors() -> list:
    return [pytest.param(v["path"], doc["inProfile"], id=f"{name}:{v['path']!r}")
            for name, doc in _goldens("PathVectors") for v in doc["vectors"]]


def test_vendored_contract_is_the_contract_byte_for_byte():
    """meeting-api reads the profile and the route from its copy of the contract's schema, the same
    bytes the bot restores and collects with (gate:fact-parity `session-profile-contract` too)."""
    vendored = Path(profile_mod.__file__).with_name("session-profile.v1.schema.json")
    assert vendored.read_bytes() == (CONTRACT_DIR / "session-profile.schema.json").read_bytes(), (
        "meeting_api/session_profile/session-profile.v1.schema.json differs from "
        "core/meetings/contracts/session-profile.v1/session-profile.schema.json — copy the contract over it")


def test_profile_and_route_are_the_contracts():
    contract = json.loads((CONTRACT_DIR / "session-profile.schema.json").read_text(encoding="utf-8"))
    assert SESSION_PROFILE == contract["$defs"]["SessionProfile"]["const"]
    assert SESSION_WRITEBACK_ROUTE == next(r["path"] for r in contract["x-routes"] if r["method"] == "PUT")


@pytest.mark.parametrize("path,in_profile", _path_vectors())
def test_contract_path_vectors(path, in_profile):
    """The contract's PathVectors, which @vexa/remote-browser's isSessionProfilePath answers too."""
    assert is_profile_path(path) is in_profile, profile_path_refusal(path)


@pytest.mark.parametrize("name,body", _goldens("WritebackBody"))
def test_contract_writeback_bodies_are_accepted(name, body):
    parsed = parse_profile_upload(json.dumps(body).encode())
    assert parsed == [(f["path"], base64.b64decode(f["data"])) for f in body["files"]]


@pytest.mark.parametrize("name,refused", _goldens("Refused"))
def test_contract_refused_bodies_are_refused(name, refused):
    with pytest.raises(InvalidSessionProfile):
        parse_profile_upload(json.dumps(refused["body"]).encode())


@pytest.mark.parametrize("path", [
    "Local State", "Default/Cookies", "Default/Login Data", "Default/Web Data",
    "Default/Local Storage/leveldb/CURRENT", "Default/Local Storage/leveldb/MANIFEST-000001",
    "Default/Local Storage/leveldb/000003.log", "Default/Session Storage/000012.ldb",
])
def test_profile_paths_accepted(path):
    assert profile_path_refusal(path) is None


@pytest.mark.parametrize("path,reason", [
    ("../Local State", "'..'"),
    ("Default/../Local State", "'..'"),
    ("Default/Local Storage/leveldb/../../../../etc/cron.d/x", "'..'"),
    ("./Local State", "'.'"),
    ("Default//Cookies", "empty"),
    ("/etc/passwd", "absolute"),
    ("Default\\Cookies", "backslash"),
    ("Default/Cookies\x00", "control"),
    ("Default/Local Storage/leveldb/CURRENT\n", "control"),
    ("Default/Extensions/abc/1.0/manifest.json", "not part of the session profile"),
    ("Default/Cache/data_0", "not part of the session profile"),
    ("Default/Local Storage/leveldb/LOCK", "not part of the session profile"),
    ("Default/Local Storage/leveldb/sub/000003.log", "not part of the session profile"),
    ("Default/Session Storage/evil.ldb", "not part of the session profile"),
    ("Default/Bookmarks", "not part of the session profile"),
    ("", "not a path"),
    (None, "not a path"),
])
def test_profile_paths_refused(path, reason):
    refusal = profile_path_refusal(path)
    assert refusal is not None and reason in refusal, refusal


# ── the deployment + the bots a real spawn makes ────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def auth_env(monkeypatch):
    for k in ("MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "S3_ACCESS_KEY", "S3_SECRET_KEY",
              "ADMIN_API_URL", "INTERNAL_API_SECRET"):
        monkeypatch.delenv(k, raising=False)
    env = {
        "BOT_AUTHENTICATED": "true",
        "BOT_USERDATA_S3_PATH": IDENTITY,
        "BOT_S3_ENDPOINT": "http://storage:9000",
        "BOT_S3_BUCKET": "vexa",
        "BOT_S3_ACCESS_KEY": "bot-read-key",
        "BOT_S3_SECRET_KEY": "bot-read-secret",
        "MINIO_ACCESS_KEY": "storage-root-key",
        "MINIO_SECRET_KEY": "storage-root-secret",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)


class FakeWriter:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.fail = False

    async def put(self, key: str, data: bytes) -> None:
        if self.fail:
            raise RuntimeError("store unavailable")
        self.objects[key] = data


class Deployment:
    def __init__(self):
        self.repo = InMemoryMeetingRepo()
        self.runtime = FakeRuntimeClient()
        self.writer = FakeWriter()
        self.clock = NOW
        app = FastAPI()
        app.include_router(build_router(self.repo, token_secret=SECRET,
                                        writer_factory=lambda cfg: self.writer,
                                        clock=lambda: self.clock))
        self.client = TestClient(app)

    def spawn(self, native: str = "abc-defg-hij", *, continue_meeting: bool = False) -> dict:
        asyncio.run(request_bot(
            self.repo, self.runtime, user_id=7, platform="google_meet", native_meeting_id=native,
            bot_name="VexaBot", redis_url="redis://redis:6379/0",
            meeting_api_url="http://meeting-api:8080", token_secret=SECRET,
            continue_meeting=continue_meeting,
        ))
        inv = json.loads(self.runtime.specs[-1]["env"]["VEXA_BOT_CONFIG"])
        return {"meeting_id": inv["meeting_id"], "session_uid": inv["connectionId"], "token": inv["token"],
                "url": inv.get("sessionWritebackUrl")}

    def end(self, bot: dict, *, ago: float) -> None:
        row = self.repo._meetings[bot["meeting_id"]]
        row["status"] = "completed"
        row["end_time"] = (self.clock - timedelta(seconds=ago)).isoformat().replace("+00:00", "Z")

    def put(self, bot: dict, files=None, *, token: str | None = "same", body=None, session_uid=None):
        headers = {}
        if token == "same":
            headers["Authorization"] = f"Bearer {bot['token']}"
        elif token is not None:
            headers["Authorization"] = token
        if body is None:
            body = {"files": [{"path": p, "data": base64.b64encode(d).decode()}
                              for p, d in (VALID if files is None else files).items()]}
        content = body if isinstance(body, (bytes, str)) else json.dumps(body)
        # The URL the spawn put in the bot's invocation, as the bot uses it; a bot spawned without
        # one is driven at the contract's route for its session.
        url = (SESSION_WRITEBACK_ROUTE.format(session_uid=session_uid) if session_uid
               else urlsplit(bot["url"]).path if bot.get("url")
               else SESSION_WRITEBACK_ROUTE.format(session_uid=bot["session_uid"]))
        return self.client.put(url, content=content, headers={**headers, "Content-Type": "application/json"})


@pytest.fixture
def dep():
    return Deployment()


# ── the live authenticated bot writes the session profile ───────────────────────────────────────

def test_spawn_names_the_write_back_url_for_this_session(dep):
    """meeting-api tells the bot where to write back (invocation.v1 sessionWritebackUrl): the
    contract's route for exactly this session, on the meeting-api the bot already reports to."""
    bot = dep.spawn()
    assert bot["url"] == "http://meeting-api:8080" + SESSION_WRITEBACK_ROUTE.format(session_uid=bot["session_uid"])


def test_anonymous_spawn_names_no_write_back_url(dep, monkeypatch):
    monkeypatch.setenv("BOT_AUTHENTICATED", "false")
    assert dep.spawn()["url"] is None


def test_live_bot_writes_its_session_profile(dep):
    bot = dep.spawn()
    r = dep.put(bot)
    assert r.status_code == 200, r.text
    assert r.json() == {"written": len(VALID), "bytes": sum(len(d) for d in VALID.values())}
    assert dep.writer.objects == {f"{PREFIX}/{p}": d for p, d in VALID.items()}


def test_write_back_inside_the_teardown_window_is_accepted(dep):
    """The bot writes back after its terminal callback; that is inside the window."""
    bot = dep.spawn()
    dep.end(bot, ago=WRITEBACK_GRACE_S - 30)
    assert dep.put(bot).status_code == 200


# ── who may write ───────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("auth", [None, "Basic abc", "Bearer ", "Bearer not-a-token"])
def test_unauthenticated_caller_is_refused(dep, auth):
    bot = dep.spawn()
    r = dep.put(bot, token=auth)
    assert r.status_code == 401, r.text
    assert dep.writer.objects == {}


def test_another_sessions_token_is_refused(dep):
    """A MeetingToken admits only the session it was minted for."""
    bot = dep.spawn()
    other = mint_meeting_token(bot["meeting_id"], 7, "google_meet", "abc-defg-hij",
                               secret=SECRET, session_uid="some-other-session")
    r = dep.put(bot, token=f"Bearer {other}")
    assert r.status_code == 401
    assert dep.writer.objects == {}


def test_token_for_another_meeting_is_refused(dep):
    """A token bound to the live session but naming another meeting is not the live bot."""
    bot = dep.spawn()
    forged_meeting = mint_meeting_token(bot["meeting_id"] + 100, 7, "google_meet", "abc-defg-hij",
                                        secret=SECRET, session_uid=bot["session_uid"])
    r = dep.put(bot, token=f"Bearer {forged_meeting}")
    assert r.status_code == 403, r.text
    assert dep.writer.objects == {}


def test_superseded_bot_is_refused_and_the_live_one_accepted(dep):
    """After a later authenticated spawn, the earlier bot can no longer write — even inside the
    window — and the later one can."""
    first = dep.spawn("aaa-aaaa-aaa")
    dep.end(first, ago=5)
    second = dep.spawn("bbb-bbbb-bbb")
    assert dep.put(first).status_code == 403
    assert dep.writer.objects == {}
    assert dep.put(second).status_code == 200


def test_meeting_ended_long_ago_is_refused(dep):
    bot = dep.spawn()
    dep.end(bot, ago=WRITEBACK_GRACE_S + 1)
    r = dep.put(bot)
    assert r.status_code == 403
    assert dep.writer.objects == {}


def test_bot_spawned_without_the_identity_is_refused(dep, monkeypatch):
    """A meeting spawned anonymously has no claim on the stored session."""
    monkeypatch.setenv("BOT_AUTHENTICATED", "false")
    bot = dep.spawn()
    monkeypatch.setenv("BOT_AUTHENTICATED", "true")
    r = dep.put(bot)
    assert r.status_code == 403
    assert dep.writer.objects == {}


def test_continued_meeting_run_is_an_authenticated_run(dep, monkeypatch):
    """continue_meeting reopens an earlier row; in authenticated mode the new run carries the
    identity, so its bot is the live authenticated bot (and the earlier run's is not)."""
    monkeypatch.setenv("BOT_AUTHENTICATED", "false")
    first = dep.spawn()
    dep.end(first, ago=5)
    monkeypatch.setenv("BOT_AUTHENTICATED", "true")
    again = dep.spawn(continue_meeting=True)
    assert again["meeting_id"] == first["meeting_id"]
    assert dep.put(first).status_code == 403
    assert dep.put(again).status_code == 200


def test_authenticated_mode_off_is_refused(dep, monkeypatch):
    bot = dep.spawn()
    monkeypatch.setenv("BOT_AUTHENTICATED", "false")
    assert dep.put(bot).status_code == 409
    assert dep.writer.objects == {}


def test_bot_pair_reusing_a_root_key_refuses_the_write_too(dep, monkeypatch):
    bot = dep.spawn()
    monkeypatch.setenv("BOT_S3_SECRET_KEY", "storage-root-secret")
    r = dep.put(bot)
    assert r.status_code == 503
    assert "storage-root-secret" not in r.text
    assert dep.writer.objects == {}


# ── what may be written ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "Default/Extensions/abc/1.0/manifest.json",
    "Default/Preferences.bak",
    "Default/Local Storage/leveldb/LOCK",
    "Default/Cache/data_0",
])
def test_file_outside_the_profile_refuses_the_whole_write(dep, path):
    bot = dep.spawn()
    r = dep.put(bot, {**VALID, path: b"planted"})
    assert r.status_code == 422, r.text
    assert "not part of the session profile" in r.json()["detail"]
    assert dep.writer.objects == {}          # all-or-nothing: the valid files were not written either


@pytest.mark.parametrize("path", [
    "../Local State",
    "Default/../../outside",
    "Default/Local Storage/leveldb/../../../../../etc/x",
    "/Default/Cookies",
    "Default\\Cookies",
    "Default/Session Storage/sub/000001.log",
])
def test_traversal_or_odd_name_is_refused(dep, path):
    bot = dep.spawn()
    r = dep.put(bot, {path: b"x"})
    assert r.status_code == 422, r.text
    assert dep.writer.objects == {}


@pytest.mark.parametrize("entry", [
    {"path": "Default/Cookies", "data": "eA==", "link": "/etc/passwd"},
    {"path": "Default/Cookies", "symlink": "../../outside"},
    {"path": "Default/Cookies", "data": "eA==", "mode": 0o777},
    {"path": "Default/Cookies"},
])
def test_entry_with_anything_but_path_and_bytes_is_refused(dep, entry):
    """No link targets, modes or other fields: an entry is a path and its bytes."""
    bot = dep.spawn()
    r = dep.put(bot, body={"files": [entry]})
    assert r.status_code == 422, r.text
    assert dep.writer.objects == {}


def test_oversized_file_is_refused(dep):
    bot = dep.spawn()
    r = dep.put(bot, {"Default/Cookies": b"\0" * (MAX_FILE_BYTES + 1)})
    assert r.status_code == 422, r.text
    assert "larger than" in r.json()["detail"]
    assert dep.writer.objects == {}


def test_oversized_body_is_refused_before_it_is_parsed(dep, monkeypatch):
    bot = dep.spawn()
    monkeypatch.setattr(router_mod, "MAX_BODY_BYTES", 64)
    r = dep.put(bot)
    assert r.status_code == 413
    assert dep.writer.objects == {}


@pytest.mark.parametrize("body", [
    b"not json",
    {"files": []},
    {"files": {"Default/Cookies": "eA=="}},
    {"files": [{"path": "Default/Cookies", "data": "eA=="}], "extra": True},
    {"files": [{"path": "Default/Cookies", "data": "not base64!"}]},
    {"files": [{"path": "Default/Cookies", "data": "eA=="}, {"path": "Default/Cookies", "data": "eQ=="}]},
])
def test_malformed_body_is_refused(dep, body):
    bot = dep.spawn()
    r = dep.put(bot, body=body)
    assert r.status_code == 422, r.text
    assert dep.writer.objects == {}


def test_store_failure_is_a_502_naming_nothing_secret(dep):
    bot = dep.spawn()
    dep.writer.fail = True
    r = dep.put(bot)
    assert r.status_code == 502
    assert "storage-root" not in r.text and "bot-read" not in r.text


def test_limits_on_count_and_total(monkeypatch):
    one = {"path": "Default/Cookies", "data": base64.b64encode(b"abcd").decode()}
    two = {"path": "Local State", "data": base64.b64encode(b"efgh").decode()}
    monkeypatch.setattr(profile_mod, "MAX_TOTAL_BYTES", 6)
    with pytest.raises(InvalidSessionProfile, match="together"):
        parse_profile_upload(json.dumps({"files": [one, two]}).encode())
    monkeypatch.setattr(profile_mod, "MAX_TOTAL_BYTES", 100)
    monkeypatch.setattr(profile_mod, "MAX_FILES", 1)
    with pytest.raises(InvalidSessionProfile, match="exceed the limit"):
        parse_profile_upload(json.dumps({"files": [one, two]}).encode())
    assert parse_profile_upload(json.dumps({"files": [one]}).encode()) == [("Default/Cookies", b"abcd")]


def test_profile_limits_are_the_ones_the_bot_reads():
    assert MAX_FILE_BYTES == SESSION_PROFILE["maxFileBytes"]


# ── the shipped app mounts it, internal only ────────────────────────────────────────────────────

def test_shipped_app_mounts_the_route_internal_only():
    app = create_app(token_secret=SECRET)
    client = TestClient(app)
    r = client.put("/internal/browser-session/some-session", content=b"{}")
    assert r.status_code == 401
    assert not any(p.startswith("/internal/browser-session") for p in app.openapi().get("paths", {}))
