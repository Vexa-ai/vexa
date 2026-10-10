"""An authenticated bot never carries half of a storage root pair.

``BOT_S3_*`` rides every authenticated bot's invocation into its container. If either half equals
the bundled storage's root pair (``MINIO_*``) or an operator S3's (``S3_*``), every bot would hold a
key that reaches the whole store — so the spawn refuses: a 503-shaped ``AuthSessionNotConfigured``
before any meeting row is written or any workload created, naming the variables and never a value.
Drives the SHIPPED ``request_bot`` over the in-memory fakes, offline.
"""
from __future__ import annotations

import json
import logging

import pytest

from meeting_api.bot_spawn import AuthSessionNotConfigured, request_bot, storage_root_reuse
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo

SECRET = "test-admin-token"
ROOT = {
    "MINIO_ACCESS_KEY": "minio-root-access-0001",
    "MINIO_SECRET_KEY": "minio-root-secret-0002",
    "S3_ACCESS_KEY": "operator-s3-access-0003",
    "S3_SECRET_KEY": "operator-s3-secret-0004",
}
BOT = {"BOT_S3_ACCESS_KEY": "bot-read-access-0005", "BOT_S3_SECRET_KEY": "bot-read-secret-0006"}
ALL_VALUES = list(ROOT.values()) + list(BOT.values())


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k in ("ADMIN_API_URL", "INTERNAL_API_SECRET"):
        monkeypatch.delenv(k, raising=False)
    for k, v in {**ROOT, **BOT, "BOT_AUTHENTICATED": "true", "BOT_USERDATA_S3_PATH": "userdata/bot-identity-1",
                 "BOT_S3_ENDPOINT": "http://storage:9000", "BOT_S3_BUCKET": "vexa"}.items():
        monkeypatch.setenv(k, v)


async def _spawn(repo, runtime):
    return await request_bot(
        repo, runtime, user_id=7, platform="google_meet", native_meeting_id="abc-defg-hij",
        bot_name="VexaBot", redis_url="redis://redis:6379/0",
        meeting_api_url="http://meeting-api:8080", token_secret=SECRET,
    )


@pytest.mark.parametrize("bot_var,root_var", [
    ("BOT_S3_ACCESS_KEY", "MINIO_ACCESS_KEY"),
    ("BOT_S3_ACCESS_KEY", "S3_ACCESS_KEY"),
    ("BOT_S3_SECRET_KEY", "MINIO_SECRET_KEY"),
    ("BOT_S3_SECRET_KEY", "S3_SECRET_KEY"),
])
async def test_bot_pair_reusing_a_root_half_is_refused_with_nothing_spawned(monkeypatch, caplog, capsys, bot_var, root_var):
    monkeypatch.setenv(bot_var, ROOT[root_var])
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    caplog.set_level(logging.DEBUG)
    with pytest.raises(AuthSessionNotConfigured) as exc:
        await _spawn(repo, runtime)
    message = str(exc.value)
    assert f"{bot_var} = {root_var}" in message                  # names the variables
    assert repo._meetings == {} and repo.sessions == []           # no row, no session
    assert runtime.specs == []                                    # no bot
    seen = message + caplog.text + "".join(capsys.readouterr())
    assert not any(v in seen for v in ALL_VALUES), "a key or secret value reached the message or the log"


async def test_distinct_pairs_spawn_with_the_bots_own_pair():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    await _spawn(repo, runtime)
    inv = json.loads(runtime.specs[0]["env"]["VEXA_BOT_CONFIG"])
    assert inv["s3AccessKey"] == BOT["BOT_S3_ACCESS_KEY"]
    assert inv["s3SecretKey"] == BOT["BOT_S3_SECRET_KEY"]
    assert not any(v in json.dumps(inv) for v in ROOT.values())   # no root value rides the invocation


async def test_anonymous_mode_never_checks_or_forwards_the_bot_pair(monkeypatch):
    monkeypatch.setenv("BOT_AUTHENTICATED", "false")
    monkeypatch.setenv("BOT_S3_ACCESS_KEY", ROOT["MINIO_ACCESS_KEY"])
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    await _spawn(repo, runtime)
    inv = json.loads(runtime.specs[0]["env"]["VEXA_BOT_CONFIG"])
    assert "s3AccessKey" not in inv and "s3SecretKey" not in inv


def test_rule_matches_only_set_equal_values():
    assert storage_root_reuse({**ROOT, **BOT}) == []
    assert storage_root_reuse({"BOT_S3_ACCESS_KEY": "", "MINIO_ACCESS_KEY": ""}) == []
    assert storage_root_reuse({"BOT_S3_SECRET_KEY": " x ", "S3_SECRET_KEY": "x"}) == ["BOT_S3_SECRET_KEY = S3_SECRET_KEY"]
    # a bot access key equal to a root SECRET is not the same half — but the pair is still distinct
    assert storage_root_reuse({"BOT_S3_ACCESS_KEY": "same", "MINIO_SECRET_KEY": "same"}) == []
    assert storage_root_reuse({"BOT_S3_ACCESS_KEY": "a", "BOT_S3_SECRET_KEY": "b",
                               "MINIO_ACCESS_KEY": "a", "S3_SECRET_KEY": "b"}) == [
        "BOT_S3_ACCESS_KEY = MINIO_ACCESS_KEY", "BOT_S3_SECRET_KEY = S3_SECRET_KEY"]
