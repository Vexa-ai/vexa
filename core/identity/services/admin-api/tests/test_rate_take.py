"""POST /internal/rate/take — one shared count per window for every terminal replica (PRD 0.13.4
M6, security review: "rate limits and locks are process-local"). Internal tier, no dev escape; a
refused request is not counted; a store that cannot answer is 503 (the terminal then keeps its own
count)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from admin_api.app.main import create_app

SECRET = "internal-secret-for-rates"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", SECRET)
    monkeypatch.setenv("DEV_MODE", "false")
    with TestClient(create_app()) as c:
        yield c


def _take(c, key="link-ip:203.0.113.4", limit=3, window=900, secret=SECRET):
    headers = {"X-Internal-Secret": secret} if secret is not None else {}
    return c.post("/internal/rate/take", headers=headers,
                  json={"key": key, "limit": limit, "window_sec": window})


def test_the_limit_is_shared_and_a_refusal_is_not_counted(client, link_ledger):
    got = [_take(client).json() for _ in range(5)]
    assert [g["allowed"] for g in got] == [True, True, True, False, False]
    assert got[2]["count"] == 3 and got[4]["count"] == 3
    # another subject has its own window
    assert _take(client, key="link-ip:198.51.100.7").json() == {"allowed": True, "count": 1}
    # the window key expires on its own
    assert all(v == 901 for k, v in link_ledger.keys.items() if k.startswith("vexa:rate:"))


def test_a_store_that_cannot_answer_is_503(client, link_ledger):
    link_ledger.down = True
    assert _take(client).status_code == 503


@pytest.mark.parametrize("secret", [None, "wrong"])
def test_the_door_needs_the_internal_secret(client, secret):
    assert _take(client, secret=secret).status_code == 403


@pytest.mark.parametrize("key", ["", "nobucket", "UP:x", "link-ip:" + "a" * 300, "link ip:x"])
def test_a_malformed_key_is_refused(client, key):
    assert _take(client, key=key).status_code == 422
