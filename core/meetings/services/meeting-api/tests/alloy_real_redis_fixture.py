"""ALLOY: collision-safe helpers for the explicit disposable-Redis test lane."""
from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any


_HIGH_MEETING_ID_FLOOR = 2_000_000_000
_HIGH_MEETING_ID_MAX = 2_147_483_647

# ALLOY: atomically remove a fixture key only while its exact reserved payload remains.
_COMPARE_AND_DELETE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""


def allocate_high_meeting_ids(count: int) -> tuple[int, ...]:
    """ALLOY: allocate a high, process-unique candidate range for test-only rows."""
    range_size = _HIGH_MEETING_ID_MAX - _HIGH_MEETING_ID_FLOOR + 1
    if count < 1 or count > range_size:
        raise ValueError("count must fit the high meeting-id allocation range")
    possible_starts = range_size - count + 1
    start = _HIGH_MEETING_ID_FLOOR + secrets.randbelow(
        possible_starts,
    )
    return tuple(start + offset for offset in range(count))


@asynccontextmanager
async def collision_safe_redis_rows(
    redis: Any,
    rows: Mapping[str, str],
    *,
    ttl_sec: int,
) -> AsyncIterator[None]:
    """ALLOY: reserve unique test keys without overwriting or deleting foreign data."""
    keys = tuple(rows)
    existing = await redis.mget(keys)
    collisions = [
        key
        for key, value in zip(keys, existing)
        if value is not None
    ]
    if collisions:
        raise AssertionError(
            f"pre-existing disposable Redis key(s): {collisions}",
        )

    created: list[tuple[str, str]] = []
    try:
        for key, value in rows.items():
            stored = await redis.set(
                key,
                value,
                ex=ttl_sec,
                nx=True,
            )
            if not stored:
                raise AssertionError(
                    f"disposable Redis key raced after preflight: {key}",
                )
            created.append((key, value))
        yield
    finally:
        for key, expected_value in created:
            await redis.eval(
                _COMPARE_AND_DELETE_SCRIPT,
                1,
                key,
                expected_value,
            )
