"""Tests for runtime_api.scheduler — Redis sorted-set job scheduler."""

import json
import time
from unittest.mock import AsyncMock, patch

import pytest

from runtime_api import scheduler
from runtime_api.scheduler import (
    EXECUTING_KEY,
    HISTORY_KEY,
    IDEMPOTENCY_PREFIX,
    JOBS_KEY,
    cancel_job,
    list_jobs,
    recover_orphaned_jobs,
    schedule_job,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_redis():
    """In-memory fake that mimics the async Redis subset used by scheduler."""
    store = {}  # key -> value (str)
    sorted_sets = {}  # key -> {member: score}
    hashes = {}  # key -> {field: value}

    r = AsyncMock()

    # -- sorted sets --
    async def _zadd(key, mapping):
        ss = sorted_sets.setdefault(key, {})
        for member, score in mapping.items():
            ss[member] = score

    async def _zrem(key, member):
        ss = sorted_sets.get(key, {})
        if member in ss:
            del ss[member]
            return 1
        return 0

    async def _zrange(key, start, end):
        ss = sorted_sets.get(key, {})
        items = sorted(ss.items(), key=lambda x: x[1])
        return [m for m, _ in items]

    async def _zrangebyscore(key, mn, mx):
        ss = sorted_sets.get(key, {})
        now = time.time() if mx == "+inf" else float(mx) if isinstance(mx, str) else mx
        # handle "-inf" min
        min_val = float("-inf")
        if isinstance(mn, str) and mn != "-inf":
            min_val = float(mn)
        return [m for m, s in sorted(ss.items(), key=lambda x: x[1])
                if min_val <= s <= now]

    r.zadd = AsyncMock(side_effect=_zadd)
    r.zrem = AsyncMock(side_effect=_zrem)
    r.zrange = AsyncMock(side_effect=_zrange)
    r.zrangebyscore = AsyncMock(side_effect=_zrangebyscore)

    # -- hashes --
    async def _hset(key, field, value):
        hashes.setdefault(key, {})[field] = value

    async def _hget(key, field):
        return hashes.get(key, {}).get(field)

    async def _hdel(key, field):
        h = hashes.get(key, {})
        if field in h:
            del h[field]
            return 1
        return 0

    async def _hgetall(key):
        return dict(hashes.get(key, {}))

    r.hset = AsyncMock(side_effect=_hset)
    r.hget = AsyncMock(side_effect=_hget)
    r.hdel = AsyncMock(side_effect=_hdel)
    r.hgetall = AsyncMock(side_effect=_hgetall)

    # -- strings --
    async def _get(key):
        return store.get(key)

    async def _set(key, value, ex=None):
        store[key] = value

    r.get = AsyncMock(side_effect=_get)
    r.set = AsyncMock(side_effect=_set)

    # expose internals for assertions
    r._store = store
    r._sorted_sets = sorted_sets
    r._hashes = hashes

    return r


def _spec(execute_at=None, url="http://example.com/hook", **extra):
    return {
        "execute_at": execute_at or time.time() + 60,
        "request": {"method": "POST", "url": url},
        **extra,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestScheduleJob:
    @pytest.mark.asyncio
    async def test_job_appears_in_sorted_set(self):
        redis = _fake_redis()
        ts = time.time() + 120
        job = await schedule_job(redis, _spec(execute_at=ts))

        assert job["status"] == "pending"
        assert job["execute_at"] == ts
        members = await redis.zrange(JOBS_KEY, 0, -1)
        assert len(members) == 1
        stored = json.loads(members[0])
        assert stored["job_id"] == job["job_id"]

    @pytest.mark.asyncio
    async def test_correct_score(self):
        redis = _fake_redis()
        ts = time.time() + 300
        await schedule_job(redis, _spec(execute_at=ts))
        ss = redis._sorted_sets.get(JOBS_KEY, {})
        scores = list(ss.values())
        assert scores == [ts]

    @pytest.mark.asyncio
    async def test_idempotent_scheduling(self):
        redis = _fake_redis()
        spec = _spec(idempotency_key="dedup-1")
        job1 = await schedule_job(redis, spec)
        job2 = await schedule_job(redis, spec)

        assert job1["job_id"] == job2["job_id"]
        members = await redis.zrange(JOBS_KEY, 0, -1)
        assert len(members) == 1


class TestCancelJob:
    @pytest.mark.asyncio
    async def test_cancel_removes_from_sorted_set(self):
        redis = _fake_redis()
        job = await schedule_job(redis, _spec())
        result = await cancel_job(redis, job["job_id"])

        assert result is not None
        assert result["status"] == "cancelled"
        members = await redis.zrange(JOBS_KEY, 0, -1)
        assert len(members) == 0

    @pytest.mark.asyncio
    async def test_cancel_stores_in_history(self):
        redis = _fake_redis()
        job = await schedule_job(redis, _spec())
        await cancel_job(redis, job["job_id"])

        hist = await redis.hget(HISTORY_KEY, job["job_id"])
        assert hist is not None
        assert json.loads(hist)["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_returns_none(self):
        redis = _fake_redis()
        result = await cancel_job(redis, "job_doesnotexist")
        assert result is None


class TestDueJobs:
    @pytest.mark.asyncio
    async def test_due_jobs_returned(self):
        redis = _fake_redis()
        past_ts = time.time() - 10
        await schedule_job(redis, _spec(execute_at=past_ts))

        due = await redis.zrangebyscore(JOBS_KEY, "-inf", time.time())
        assert len(due) == 1

    @pytest.mark.asyncio
    async def test_future_jobs_not_returned(self):
        redis = _fake_redis()
        future_ts = time.time() + 9999
        await schedule_job(redis, _spec(execute_at=future_ts))

        due = await redis.zrangebyscore(JOBS_KEY, "-inf", time.time())
        assert len(due) == 0


class TestProcessJob:
    @pytest.mark.asyncio
    async def test_due_job_fires_callback(self):
        redis = _fake_redis()
        past_ts = time.time() - 10
        job = await schedule_job(redis, _spec(execute_at=past_ts, url="http://target/run"))

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = "ok"
        mock_response.request = AsyncMock()

        with patch("runtime_api.scheduler.httpx.AsyncClient") as mock_client_cls:
            client_instance = AsyncMock()
            client_instance.request = AsyncMock(return_value=mock_response)
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = client_instance

            due = await redis.zrangebyscore(JOBS_KEY, "-inf", time.time())
            assert len(due) == 1
            await scheduler._process_job(redis, due[0])

            client_instance.request.assert_called_once()
            call_args = client_instance.request.call_args
            assert call_args[0][0] == "POST"
            assert call_args[0][1] == "http://target/run"

    @pytest.mark.asyncio
    async def test_completed_job_in_history(self):
        redis = _fake_redis()
        past_ts = time.time() - 10
        job = await schedule_job(redis, _spec(execute_at=past_ts))

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = "ok"
        mock_response.request = AsyncMock()

        with patch("runtime_api.scheduler.httpx.AsyncClient") as mock_client_cls:
            client_instance = AsyncMock()
            client_instance.request = AsyncMock(return_value=mock_response)
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = client_instance

            due = await redis.zrangebyscore(JOBS_KEY, "-inf", time.time())
            await scheduler._process_job(redis, due[0])

        hist = await redis.hget(HISTORY_KEY, job["job_id"])
        assert hist is not None
        assert json.loads(hist)["status"] == "completed"


class TestCronReschedule:
    @pytest.mark.asyncio
    async def test_cron_reschedules_after_execution(self):
        redis = _fake_redis()
        past_ts = time.time() - 10
        spec = _spec(execute_at=past_ts, metadata={"cron": "*/5 * * * *"})
        job = await schedule_job(redis, spec)

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = "ok"
        mock_response.request = AsyncMock()

        with patch("runtime_api.scheduler.httpx.AsyncClient") as mock_client_cls:
            client_instance = AsyncMock()
            client_instance.request = AsyncMock(return_value=mock_response)
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = client_instance

            due = await redis.zrangebyscore(JOBS_KEY, "-inf", time.time())
            await scheduler._process_job(redis, due[0])

        # Original job completed, new job should be in sorted set
        members = await redis.zrange(JOBS_KEY, 0, -1)
        assert len(members) >= 1
        new_job = json.loads(members[0])
        assert new_job["job_id"] != job["job_id"]
        assert new_job["execute_at"] > time.time()


class TestRecoverOrphaned:
    @pytest.mark.asyncio
    async def test_orphaned_jobs_requeued(self):
        redis = _fake_redis()
        orphan = {"job_id": "job_orphan123", "status": "executing",
                  "execute_at": time.time() - 60,
                  "request": {"method": "POST", "url": "http://x/y"}}
        await redis.hset(EXECUTING_KEY, "job_orphan123", json.dumps(orphan))

        recovered = await recover_orphaned_jobs(redis)
        assert recovered == 1

        # Should be back in sorted set
        members = await redis.zrange(JOBS_KEY, 0, -1)
        assert len(members) == 1
        requeued = json.loads(members[0])
        assert requeued["status"] == "pending"

        # Should be removed from executing
        executing = await redis.hget(EXECUTING_KEY, "job_orphan123")
        assert executing is None


class TestListJobs:
    @pytest.mark.asyncio
    async def test_list_pending(self):
        redis = _fake_redis()
        await schedule_job(redis, _spec())
        await schedule_job(redis, _spec())
        jobs = await list_jobs(redis)
        assert len(jobs) == 2

    @pytest.mark.asyncio
    async def test_list_with_source_filter(self):
        redis = _fake_redis()
        await schedule_job(redis, _spec(metadata={"source": "cron"}))
        await schedule_job(redis, _spec(metadata={"source": "manual"}))
        jobs = await list_jobs(redis, source="cron")
        assert len(jobs) == 1
        assert jobs[0]["metadata"]["source"] == "cron"
