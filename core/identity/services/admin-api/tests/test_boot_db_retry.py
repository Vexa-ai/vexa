"""#901 · admin-api bounded initial-DB-connect retry.

On a cold start where Postgres DNS isn't resolvable yet, admin-api used to throw
``socket.gaierror`` straight out of the startup schema-convergence and exit(3), leaning on the
k8s restart loop (observed 2× in the first ~20s of the v0.12.17-rc.1 smoke). The fix wraps the
initial connect in ``_connect_with_retry``: transient connect errors (DNS/refused/reset) retry
with bounded exponential backoff; after the bound it re-raises LOUD (no infinite retry).

Discriminating red→green:
  * fails N times with socket.gaierror then succeeds → returns the value, no raise (today: raises)
  * persistently failing → still raises after exactly max_attempts tries (fail loud, not forever)
  * a NON-transient error (auth/config) fails fast without burning the retry budget

Uses ``asyncio.run`` in plain sync tests (matching the suite's existing pattern) — no
pytest-asyncio marker dependency.
"""
from __future__ import annotations

import asyncio
import socket

import pytest

from admin_api.__main__ import _connect_with_retry, _is_transient_connect_error


async def _nosleep(_delay):
    return None


def test_retries_then_succeeds():
    calls = {"n": 0}

    async def connect():
        calls["n"] += 1
        if calls["n"] < 4:
            raise socket.gaierror(-2, "Name or service not known")
        return "healthy"

    result = asyncio.run(
        _connect_with_retry(
            connect, max_attempts=10, base_delay=0.0, max_delay=0.0, sleep=_nosleep
        )
    )
    assert result == "healthy"
    assert calls["n"] == 4  # 3 failures then a success


def test_wrapped_gaierror_is_transient_and_retried():
    # SQLAlchemy wraps the driver error; the gaierror is chained as __cause__. Retry must unwrap.
    calls = {"n": 0}

    async def connect():
        calls["n"] += 1
        if calls["n"] < 2:
            try:
                raise socket.gaierror(-2, "Name or service not known")
            except socket.gaierror as e:
                raise RuntimeError("(sqlalchemy) OperationalError connecting") from e
        return "ok"

    result = asyncio.run(
        _connect_with_retry(
            connect, max_attempts=5, base_delay=0.0, max_delay=0.0, sleep=_nosleep
        )
    )
    assert result == "ok"
    assert calls["n"] == 2


def test_fails_loud_after_bound():
    calls = {"n": 0}

    async def connect():
        calls["n"] += 1
        raise socket.gaierror(-2, "Name or service not known")

    with pytest.raises(socket.gaierror):
        asyncio.run(
            _connect_with_retry(
                connect, max_attempts=5, base_delay=0.0, max_delay=0.0, sleep=_nosleep
            )
        )
    assert calls["n"] == 5  # bounded — exactly max_attempts, never infinite


def test_non_transient_error_fails_fast():
    calls = {"n": 0}

    async def connect():
        calls["n"] += 1
        raise ValueError("bad DSN / auth misconfig — not a network blip")

    with pytest.raises(ValueError):
        asyncio.run(
            _connect_with_retry(
                connect, max_attempts=10, base_delay=0.0, max_delay=0.0, sleep=_nosleep
            )
        )
    assert calls["n"] == 1  # no retry budget burned on a config error


def test_transient_classifier():
    assert _is_transient_connect_error(socket.gaierror(-2, "x"))
    assert _is_transient_connect_error(ConnectionRefusedError())
    assert not _is_transient_connect_error(ValueError("x"))
