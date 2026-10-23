"""A request the edge guard blocks leaves no credential in any log record.

guard writes a line for every block it makes (rate limit, blacklist), and that line carries the
request's headers. The credentials a client sends the gateway (X-API-Key, Authorization: Bearer,
Cookie) reach those lines redacted. Every logger is captured at DEBUG, and each scenario first
proves guard did log the block, so a silent logger cannot pass.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from gateway.edge_guard import apply_guard, build_guard_config

_SECRET = "vxa_SENTINEL_d41d8cd98f00b204e9800998ecf8427e"
_CREDENTIAL_HEADERS = {
    "X-API-Key": _SECRET,
    "Authorization": f"Bearer {_SECRET}",
    "Cookie": f"session={_SECRET}",
}


async def _ok() -> dict[str, str]:
    return {"ok": "true"}


def _app(**overrides: Any) -> FastAPI:
    """The gateway's own guard config, in-memory, with ``overrides`` set to make it trip."""
    config = build_guard_config().model_copy(
        update={"enable_redis": False, "redis_url": None, "exclude_paths": [],
                "trusted_proxies": ["127.0.0.1"], **overrides}
    )
    app = FastAPI()
    app.add_api_route("/", _ok, methods=["GET"])
    apply_guard(app, config=config)
    return app


async def _send(app: FastAPI, ip: str, count: int) -> list[int]:
    headers = {**_CREDENTIAL_HEADERS, "X-Forwarded-For": ip}
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        return [(await ac.get("/?api_key=" + _SECRET, headers=headers)).status_code
                for _ in range(count)]


def _assert_logged_without_secret(caplog: pytest.LogCaptureFixture) -> None:
    guard_records = [r for r in caplog.records if r.name.startswith("guard")]
    assert guard_records, "guard logged nothing for the block, so redaction went unobserved"
    # The test's own httpx client logs the URL it sent; only the gateway side is under test.
    leaking = [f"{r.name}: {r.getMessage()}" for r in caplog.records
               if r.name != "httpx" and _SECRET in r.getMessage()]
    assert leaking == []


async def test_a_rate_limited_request_logs_no_credential(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    app = _app(rate_limit=2, enable_rate_limiting=True)
    assert (await _send(app, "10.20.0.1", 3))[-1] == 429
    _assert_logged_without_secret(caplog)


async def test_a_blacklisted_request_logs_no_credential(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    app = _app(blacklist=["10.20.0.2"])
    assert await _send(app, "10.20.0.2", 1) == [403]
    _assert_logged_without_secret(caplog)
