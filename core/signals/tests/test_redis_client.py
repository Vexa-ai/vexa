"""The service redis client survives a slow redis (app.dev 2026-10-16: discover died on
`TimeoutError: Timeout reading from redis:6379` under a blocking XREADGROUP and lost the turn)."""
from __future__ import annotations

from meeting_signals.__main__ import BLOCK_MS, redis_client


def test_the_client_retries_a_timeout_and_reads_longer_than_any_blocking_call():
    r = redis_client("redis://localhost:6379/0")
    kw = r.connection_pool.connection_kwargs
    assert kw["retry_on_timeout"] is True or r.get_retry() is not None
    assert kw["socket_timeout"] and kw["socket_timeout"] * 1000 > BLOCK_MS * 2
    assert kw["socket_connect_timeout"]
    retry = kw.get("retry") or r.get_retry()
    assert retry is not None and retry._retries >= 3
