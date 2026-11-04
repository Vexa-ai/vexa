"""bot-limits.v1 on ``/internal/users/{id}/bot-context`` — the one carrier of an account's bot limits.

Founder, 4 Nov 2026: every bot limit lives in Meetings; plan defaults live in platform_settings,
per-account overrides in users.data. Identity owns those tables, so it resolves them and hands
meetings one versioned object over the bot-context seam meetings already reads on every spawn path.
The concurrency cap is the ``users.max_concurrent_bots`` column, as it always was.

The contract's ``Resolution`` goldens (core/identity/contracts/bot-limits.v1/golden) are replayed
here: each ``expect`` is exactly what identity answers. meeting-api reads the ``BotLimits`` goldens.
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from admin_api.app import db as app_db
from admin_api.app.main import BOT_LIMITS_VERSION, _resolve_bot_limits, create_app
from admin_api.schema.models import Base
from admin_api.schema.sync import ensure_schema_sync

from conftest import requires_docker
from test_stack_admin_api import ADMIN_TOKEN, INTERNAL_SECRET, _admin, _dispose_async_engine


def _golden_dir() -> Path:
    for parent in Path(__file__).resolve().parents:
        d = parent / "contracts" / "bot-limits.v1" / "golden"
        if d.is_dir():
            return d
        d = parent / "core" / "identity" / "contracts" / "bot-limits.v1" / "golden"
        if d.is_dir():
            return d
    raise AssertionError("bot-limits.v1 goldens not found")


VECTORS = sorted(_golden_dir().glob("Resolution.*.json"))


def test_the_contract_has_resolution_vectors():
    assert len(VECTORS) >= 4


@pytest.mark.parametrize("path", VECTORS, ids=lambda p: p.name)
def test_identity_answers_every_resolution_vector(path):
    v = json.loads(path.read_text())
    cap = 0 if v.get("is_admin_account") else v["max_concurrent_bots"]
    assert _resolve_bot_limits(cap, v["user_data"], v["platform"]) == v["expect"]


def test_the_answer_is_versioned():
    assert _resolve_bot_limits(1, {"bot_limits": {"version": 9}}, {})["version"] == BOT_LIMITS_VERSION == 1


def test_an_unknown_plan_or_a_malformed_layer_is_skipped():
    platform = {"default": {"seat_cooldown_s": 60}, "plans": {"payg": {"seat_cooldown_s": 90}}}
    assert _resolve_bot_limits(2, {"subscription_tier": "gone"}, platform) == \
        {"version": 1, "max_concurrent_bots": 2, "seat_cooldown_s": 60}
    assert _resolve_bot_limits(2, {"bot_limits": "lots"}, {"plans": [1], "default": None}) == \
        {"version": 1, "max_concurrent_bots": 2}


def test_a_limit_identity_has_never_heard_of_passes_through():
    """Meetings owns the vocabulary (limits.py); a key added there needs nothing here."""
    assert _resolve_bot_limits(2, {"bot_limits": {"booked_max_per_account": 500}}, {}) == \
        {"version": 1, "max_concurrent_bots": 2, "booked_max_per_account": 500}


@pytest.fixture()
def client(pg_url, pg_async_url, monkeypatch):
    sync_engine = create_engine(pg_url)
    Base.metadata.drop_all(sync_engine)
    ensure_schema_sync(sync_engine, Base)
    sync_engine.dispose()
    monkeypatch.setenv("ADMIN_API_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL_SECRET)
    monkeypatch.setenv("DEV_MODE", "false")
    app_db.configure(pg_async_url)
    with TestClient(create_app()) as c:
        yield c
    _dispose_async_engine()


@requires_docker
def test_bot_context_carries_the_resolved_limits_and_no_second_cap(client, pg_url):
    uid = client.post("/admin/users", headers=_admin(), json={"email": "limits@vexa.ai"}).json()["id"]
    client.patch(f"/admin/users/{uid}", headers=_admin(), json={"max_concurrent_bots": 4})
    ctx = client.get(f"/internal/users/{uid}/bot-context",
                     headers={"X-Internal-Secret": INTERNAL_SECRET}).json()
    assert ctx["bot_limits"] == {"version": 1, "max_concurrent_bots": 4}
    assert "max_concurrent" not in ctx, "the cap rides bot_limits only"

    engine = create_engine(pg_url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO platform_settings (key, value) VALUES ('bot_limits', "
                          "CAST(:v AS jsonb))"),
                     {"v": '{"default": {"seat_cooldown_s": 60, "queue_wait_s": 1800}}'})
        conn.execute(text("UPDATE users SET data = COALESCE(data, '{}'::jsonb) || "
                          "'{\"bot_limits\": {\"seat_cooldown_s\": 300}}'::jsonb WHERE id = :id"),
                     {"id": uid})
    engine.dispose()

    ctx = client.get(f"/internal/users/{uid}/bot-context",
                     headers={"X-Internal-Secret": INTERNAL_SECRET}).json()
    assert ctx["bot_limits"] == {"version": 1, "max_concurrent_bots": 4,
                                 "seat_cooldown_s": 300, "queue_wait_s": 1800}
