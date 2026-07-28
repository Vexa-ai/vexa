"""ALLOY: offline proofs for collision-safe disposable-Redis test setup."""
from __future__ import annotations

import alloy_real_redis_fixture as fixture_module
import pytest

from alloy_real_redis_fixture import (
    allocate_high_meeting_ids,
    collision_safe_redis_rows,
)


class FakeRedis:
    def __init__(
        self,
        *,
        existing: dict[str, str] | None = None,
        race_key: str | None = None,
    ):
        self.values = dict(existing or {})
        self.race_key = race_key
        self.commands: list[tuple] = []

    async def mget(self, keys):
        keys = list(keys)
        self.commands.append(("mget", tuple(keys)))
        return [self.values.get(key) for key in keys]

    async def set(self, key, value, *, ex, nx):
        self.commands.append(("set", key, value, ex, nx))
        if key == self.race_key and key not in self.values:
            self.values[key] = "foreign-race-value"
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def delete(self, *keys):
        self.commands.append(("delete", tuple(keys)))
        for key in keys:
            self.values.pop(key, None)
        return len(keys)

    async def eval(self, _script, numkeys, key, expected_value):
        assert numkeys == 1
        self.commands.append(
            ("eval_compare_delete", key, expected_value),
        )
        if self.values.get(key) != expected_value:
            return 0
        self.values.pop(key)
        return 1


def test_high_meeting_id_allocator_returns_unique_ids_outside_default_fixture_range():
    meeting_ids = allocate_high_meeting_ids(4)

    assert len(meeting_ids) == 4
    assert len(set(meeting_ids)) == 4
    assert min(meeting_ids) >= 2_000_000_000
    assert max(meeting_ids) <= 2_147_483_647


def test_high_meeting_id_allocator_includes_signed_32_bit_max_without_crossing_it(
    monkeypatch,
):
    monkeypatch.setattr(
        fixture_module.secrets,
        "randbelow",
        lambda upper_bound: upper_bound - 1,
    )

    assert allocate_high_meeting_ids(4) == (
        2_147_483_644,
        2_147_483_645,
        2_147_483_646,
        2_147_483_647,
    )


@pytest.mark.asyncio
async def test_collision_safe_rows_stop_before_writes_when_preflight_finds_a_key():
    rows = {
        "alloy:stt:telemetry:v1:2000000001": "owned",
        "alloy:stt:telemetry:v1:2000000002": "neighbor",
    }
    foreign_key = next(iter(rows))
    redis = FakeRedis(existing={foreign_key: "foreign-preexisting-value"})

    with pytest.raises(AssertionError, match="pre-existing"):
        async with collision_safe_redis_rows(redis, rows, ttl_sec=60):
            pytest.fail("collision-safe fixture yielded after a failed preflight")

    assert redis.values == {foreign_key: "foreign-preexisting-value"}
    assert redis.commands == [("mget", tuple(rows))]


@pytest.mark.asyncio
async def test_collision_safe_rows_use_nx_ttl_and_cleanup_only_created_keys_on_race():
    rows = {
        "alloy:stt:telemetry:v1:2000000011": "first",
        "alloy:stt:telemetry:v1:2000000012": "second",
    }
    first_key, raced_key = rows
    redis = FakeRedis(race_key=raced_key)

    with pytest.raises(AssertionError, match="raced"):
        async with collision_safe_redis_rows(redis, rows, ttl_sec=60):
            pytest.fail("collision-safe fixture yielded after a raced SET")

    assert redis.values == {raced_key: "foreign-race-value"}
    set_commands = [command for command in redis.commands if command[0] == "set"]
    assert set_commands == [
        ("set", first_key, "first", 60, True),
        ("set", raced_key, "second", 60, True),
    ]
    assert redis.commands[-1] == (
        "eval_compare_delete",
        first_key,
        "first",
    )


@pytest.mark.asyncio
async def test_collision_safe_rows_cleanup_every_successfully_created_key():
    rows = {
        "alloy:stt:telemetry:v1:2000000021": "first",
        "alloy:stt:telemetry:v1:2000000022": "second",
    }
    redis = FakeRedis()

    async with collision_safe_redis_rows(redis, rows, ttl_sec=45):
        assert redis.values == rows

    assert redis.values == {}
    assert redis.commands[-2:] == [
        ("eval_compare_delete", key, value)
        for key, value in rows.items()
    ]
    assert all(
        command[-2:] == (45, True)
        for command in redis.commands
        if command[0] == "set"
    )


@pytest.mark.asyncio
async def test_cleanup_preserves_foreign_value_that_replaces_a_reserved_key():
    rows = {
        "alloy:stt:telemetry:v1:2000000031": "first-owned-value",
        "alloy:stt:telemetry:v1:2000000032": "second-owned-value",
    }
    replaced_key, still_owned_key = rows
    redis = FakeRedis()

    async with collision_safe_redis_rows(redis, rows, ttl_sec=45):
        redis.values[replaced_key] = "foreign-replacement"

    assert redis.values == {replaced_key: "foreign-replacement"}
    assert redis.commands[-2:] == [
        ("eval_compare_delete", replaced_key, rows[replaced_key]),
        ("eval_compare_delete", still_owned_key, rows[still_owned_key]),
    ]
