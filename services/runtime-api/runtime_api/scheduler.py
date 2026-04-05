"""Redis sorted-set job scheduler with retry and cron support.

Schedule HTTP calls for future execution. A background worker polls for due
jobs and fires them. Supports retry with exponential backoff and cron-based
recurring jobs.

Usage::

    from runtime_api.scheduler import schedule_job, cancel_job, list_jobs
    from runtime_api.scheduler import start_executor, stop_executor

    # Schedule an HTTP callback in 5 minutes
    job = await schedule_job(redis, {
        "execute_at": time.time() + 300,
        "request": {
            "method": "POST",
            "url": "http://my-service:8080/callback",
            "body": {"task": "process-data"}
        },
    })

    # Start background executor (on app startup)
    asyncio.create_task(start_executor(redis))
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional
from uuid import uuid4

import httpx

from runtime_api import config

logger = logging.getLogger("runtime_api.scheduler")

# Redis keys
JOBS_KEY = "scheduler:jobs"              # Sorted set: score=execute_at, member=job_json
EXECUTING_KEY = "scheduler:executing"    # Hash: job_id -> job_json (in-flight)
HISTORY_KEY = "scheduler:history"        # Hash: job_id -> job_json (completed/failed)
IDEMPOTENCY_PREFIX = "scheduler:idem:"   # String keys for dedup
HISTORY_TTL = 86400 * 7                  # 7 days

DEFAULT_RETRY = {
    "max_attempts": 3,
    "backoff": [30, 120, 300],
    "attempt": 0,
}

_stop_event: Optional[asyncio.Event] = None


# --- Job CRUD ---

def _make_job(spec: dict[str, Any]) -> dict[str, Any]:
    """Build a complete job from a user-provided spec."""
    now = time.time()
    execute_at = spec.get("execute_at")
    if isinstance(execute_at, str):
        from datetime import datetime, timezone
        execute_at = datetime.fromisoformat(execute_at).timestamp()
    if execute_at is None:
        raise ValueError("execute_at is required")

    request = spec.get("request")
    if not request or not request.get("url"):
        raise ValueError("request.url is required")

    return {
        "job_id": f"job_{uuid4().hex[:16]}",
        "execute_at": execute_at,
        "created_at": now,
        "status": "pending",
        "request": {
            "method": request.get("method", "POST"),
            "url": request["url"],
            "headers": request.get("headers", {}),
            "body": request.get("body"),
            "timeout": request.get("timeout", 30),
        },
        "retry": {**DEFAULT_RETRY, **(spec.get("retry") or {})},
        "metadata": spec.get("metadata", {}),
        "callback": spec.get("callback", {}),
        "idempotency_key": spec.get("idempotency_key"),
    }


async def schedule_job(redis: Any, spec: dict[str, Any]) -> dict[str, Any]:
    """Schedule an HTTP call for future execution.

    Args:
        redis: Async Redis client.
        spec: Job specification with execute_at, request, and optional
            retry/metadata/callback/idempotency_key.

    Returns:
        The created job dict.
    """
    job = _make_job(spec)

    # Idempotency check
    idem_key = job.get("idempotency_key")
    if idem_key:
        idem_redis_key = f"{IDEMPOTENCY_PREFIX}{idem_key}"
        existing = await redis.get(idem_redis_key)
        if existing:
            logger.info(f"Duplicate idempotency_key={idem_key}, returning existing job")
            return json.loads(existing)
        await redis.set(idem_redis_key, json.dumps(job), ex=HISTORY_TTL)

    job_json = json.dumps(job)
    await redis.zadd(JOBS_KEY, {job_json: job["execute_at"]})
    logger.info(
        f"Job {job['job_id']} scheduled for "
        f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(job['execute_at']))} UTC "
        f"[{job['request']['method']} {job['request']['url']}]"
    )
    return job


async def cancel_job(redis: Any, job_id: str) -> Optional[dict[str, Any]]:
    """Cancel a scheduled job by ID. Returns the job if found, None otherwise."""
    all_jobs = await redis.zrange(JOBS_KEY, 0, -1)
    for job_json in all_jobs:
        job = json.loads(job_json)
        if job.get("job_id") == job_id:
            removed = await redis.zrem(JOBS_KEY, job_json)
            if removed:
                job["status"] = "cancelled"
                await redis.hset(HISTORY_KEY, job_id, json.dumps(job))
                logger.info(f"Job {job_id} cancelled")
                return job
    return None


async def get_job(redis: Any, job_id: str) -> Optional[dict[str, Any]]:
    """Get a job by ID from pending, executing, or history."""
    executing = await redis.hget(EXECUTING_KEY, job_id)
    if executing:
        return json.loads(executing)

    history = await redis.hget(HISTORY_KEY, job_id)
    if history:
        return json.loads(history)

    all_jobs = await redis.zrange(JOBS_KEY, 0, -1)
    for job_json in all_jobs:
        job = json.loads(job_json)
        if job.get("job_id") == job_id:
            return job

    return None


async def list_jobs(
    redis: Any,
    status: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List scheduled jobs, optionally filtered by status or metadata source."""
    results = []

    if status is None or status == "pending":
        pending = await redis.zrange(JOBS_KEY, 0, -1)
        for job_json in pending:
            job = json.loads(job_json)
            if source and job.get("metadata", {}).get("source") != source:
                continue
            results.append(job)

    if status is None or status == "executing":
        executing = await redis.hgetall(EXECUTING_KEY)
        for job_json in executing.values():
            job = json.loads(job_json)
            if source and job.get("metadata", {}).get("source") != source:
                continue
            results.append(job)

    results.sort(key=lambda j: j.get("execute_at", 0))
    return results[:limit]


async def recover_orphaned_jobs(redis: Any) -> int:
    """Re-queue jobs that were executing when the service crashed."""
    executing = await redis.hgetall(EXECUTING_KEY)
    recovered = 0
    for job_id, job_json in executing.items():
        job = json.loads(job_json)
        job["status"] = "pending"
        await redis.zadd(JOBS_KEY, {json.dumps(job): time.time()})
        await redis.hdel(EXECUTING_KEY, job_id)
        logger.warning(f"Recovered orphaned job {job_id}")
        recovered += 1
    return recovered


# --- Executor (background worker) ---

async def _fire_request(request: dict[str, Any]) -> dict[str, Any]:
    """Execute an HTTP request and return result info."""
    method = request.get("method", "POST")
    url = request["url"]
    headers = request.get("headers", {})
    body = request.get("body")
    timeout = request.get("timeout", 30)

    start = time.time()
    async with httpx.AsyncClient(follow_redirects=True) as client:
        if body is not None:
            if "Content-Type" not in headers and "content-type" not in headers:
                headers["Content-Type"] = "application/json"
            resp = await client.request(
                method, url,
                headers=headers,
                content=json.dumps(body).encode() if isinstance(body, dict) else body,
                timeout=timeout,
            )
        else:
            resp = await client.request(method, url, headers=headers, timeout=timeout)

    elapsed_ms = int((time.time() - start) * 1000)

    if resp.status_code >= 500 or resp.status_code == 429:
        raise httpx.HTTPStatusError(
            f"Server error {resp.status_code}",
            request=resp.request,
            response=resp,
        )

    return {
        "status_code": resp.status_code,
        "response_time_ms": elapsed_ms,
        "body_preview": resp.text[:200] if resp.text else None,
    }


async def _notify_callback(job: dict[str, Any], outcome: str) -> None:
    """Fire a callback URL if configured."""
    callback = job.get("callback", {})
    url = callback.get(f"on_{outcome}")
    if not url:
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, json={
                "job_id": job["job_id"],
                "status": job["status"],
                "result": job.get("result"),
                "error": job.get("error"),
                "metadata": job.get("metadata", {}),
            }, timeout=10)
    except Exception as e:
        logger.warning(f"Callback {outcome} to {url} failed: {e}")


async def _process_job(redis: Any, job_data: str) -> None:
    """Process a single due job."""
    job = json.loads(job_data)
    job_id = job["job_id"]

    # Atomic remove — if another worker already took it, skip
    removed = await redis.zrem(JOBS_KEY, job_data)
    if not removed:
        return

    # Track as executing
    job["status"] = "executing"
    await redis.hset(EXECUTING_KEY, job_id, json.dumps(job))

    request = job["request"]
    retry = job.get("retry", {})

    try:
        result = await _fire_request(request)
        job["status"] = "completed"
        job["result"] = result
        job["completed_at"] = time.time()
        logger.info(
            f"Job {job_id} completed — "
            f"{request['method']} {request['url']} -> {result['status_code']} "
            f"({result['response_time_ms']}ms)"
        )
        await _notify_callback(job, "success")

    except Exception as e:
        attempt = retry.get("attempt", 0) + 1
        max_attempts = retry.get("max_attempts", 3)
        backoff = retry.get("backoff", [30, 120, 300])

        if attempt < max_attempts:
            delay = backoff[min(attempt - 1, len(backoff) - 1)]
            job["retry"]["attempt"] = attempt
            job["status"] = "pending"
            next_time = time.time() + delay
            await redis.zadd(JOBS_KEY, {json.dumps(job): next_time})
            logger.warning(
                f"Job {job_id} attempt {attempt}/{max_attempts} failed "
                f"({e}), retry in {delay}s"
            )
        else:
            job["status"] = "failed"
            job["error"] = str(e)
            job["failed_at"] = time.time()
            logger.error(f"Job {job_id} permanently failed after {max_attempts} attempts: {e}")
            await _notify_callback(job, "failure")

    # Remove from executing, store in history
    await redis.hdel(EXECUTING_KEY, job_id)
    await redis.hset(HISTORY_KEY, job_id, json.dumps(job))

    # Reschedule cron jobs
    cron_expr = job.get("metadata", {}).get("cron")
    if cron_expr and job["status"] == "completed":
        try:
            from croniter import croniter
            from datetime import datetime, timezone
            cron = croniter(cron_expr, datetime.now(timezone.utc))
            next_time = cron.get_next(float)
            next_job = {
                "execute_at": next_time,
                "request": job["request"],
                "metadata": job["metadata"],
            }
            new_job = await schedule_job(redis, next_job)
            logger.info(f"Cron job rescheduled as {new_job['job_id']}")
        except Exception as e:
            logger.error(f"Failed to reschedule cron job {job_id}: {e}")


async def _executor_loop(redis: Any) -> None:
    """Main executor loop — polls for due jobs and processes them."""
    global _stop_event
    _stop_event = asyncio.Event()

    recovered = await recover_orphaned_jobs(redis)
    if recovered:
        logger.info(f"Recovered {recovered} orphaned jobs on startup")

    poll_interval = config.SCHEDULER_POLL_INTERVAL
    logger.info(f"Scheduler executor started (poll every {poll_interval}s)")

    while not _stop_event.is_set():
        try:
            now = time.time()
            due_jobs = await redis.zrangebyscore(JOBS_KEY, "-inf", now)
            for job_data in due_jobs:
                if _stop_event.is_set():
                    break
                await _process_job(redis, job_data)
        except Exception as e:
            logger.error(f"Executor loop error: {e}", exc_info=True)

        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=poll_interval)
        except asyncio.TimeoutError:
            pass


async def start_executor(redis: Any) -> None:
    """Start the scheduler executor (blocking — run as asyncio.create_task)."""
    await _executor_loop(redis)


async def stop_executor() -> None:
    """Signal the executor to stop gracefully."""
    global _stop_event
    if _stop_event:
        _stop_event.set()
        logger.info("Executor stop requested")
