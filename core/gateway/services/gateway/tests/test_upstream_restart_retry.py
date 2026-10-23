"""A service being replaced is not a failed request (Vexa-ai/vexa#1783, friction 2026-10-14 09:30).

`workspace_remove` on a workspace answered `502 upstream unreachable` twice in one second: the
gateway accepted the DELETE while agent-api's container was being swapped by a deploy, and no
connection could be opened. Nothing had reached agent-api — a connect that never opened sent
nothing — so trying again for a few seconds is safe for EVERY method, and turns a swap window into
a slower answer instead of an error. A failure after bytes may have been sent is NOT retried: a
DELETE or POST is not repeated on a guess.
"""
import httpx
import pytest
from fastapi.testclient import TestClient

from gateway import app as gateway_app
from gateway import create_app
from conftest import VALID_KEY, FakeAuthorizer, FakeDownstream, FakeRedis, needs_agent

pytestmark = needs_agent
AUTH = {"x-api-key": VALID_KEY}


class _Flaky(FakeDownstream):
    def __init__(self, fail: int, exc: Exception):
        super().__init__(status_code=200, body={"deleted": True})
        self.fail, self.exc, self.calls = fail, exc, 0

    async def request(self, method, url, **kw):
        self.calls += 1
        if self.calls <= self.fail:
            raise self.exc
        return await super().request(method, url, **kw)


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    async def _no_wait(_s):
        return None
    monkeypatch.setattr(gateway_app, "_retry_sleep", _no_wait)


def _client(ds):
    return TestClient(create_app(FakeAuthorizer(), ds, FakeRedis(), agent_api_url="http://agent-api"))


def test_a_connect_failure_during_a_swap_is_retried_and_succeeds():
    ds = _Flaky(2, httpx.ConnectError("connection refused"))
    r = _client(ds).delete("/agent/workspace/finos", headers=AUTH)
    assert r.status_code == 200 and r.json() == {"deleted": True}
    assert ds.calls == 3


def test_a_connect_failure_that_outlasts_the_window_is_still_502():
    ds = _Flaky(10_000, httpx.ConnectError("connection refused"))
    r = _client(ds).delete("/agent/workspace/finos", headers=AUTH)
    assert r.status_code == 502 and r.json()["detail"] == "upstream unreachable: ConnectError"
    assert 1 < ds.calls < 10_000


def test_a_failure_after_the_request_may_have_been_sent_is_not_repeated():
    ds = _Flaky(1, httpx.ReadError("connection reset"))
    r = _client(ds).delete("/agent/workspace/finos", headers=AUTH)
    assert r.status_code == 502 and ds.calls == 1
