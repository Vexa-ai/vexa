"""A synchronous, Clock-gated job scheduler for compiled `schedule.v1` jobs.

A faithful, minimal MIRROR of the runtime kernel's `runtime/src/runtime_kernel/scheduler.py`
(sorted-set by `execute_at`, idempotency dedup, injectable `dispatch`, cron re-arm, explicit
`tick()` driven by a `Clock`). We mirror rather than import it for the same reason as the Clock
port: the runtime is outside meeting-api's import graph, and the SSOT is the `schedule.v1`
contract — the compiler validates against it; this scheduler merely fires conformant jobs.

This brick is the eval engine for O-MTG-3: it consumes the compiler's output and proves the
fire/re-arm/cancel behaviour with a `FakeClock` and a capturing dispatch (no real bot spawns).

Storage is an in-memory sorted list (the runtime uses a redis sorted set; the wire shape and
the operations — zadd / zrangebyscore / zrem — are identical, just backed by a list here so the
eval needs no redis at all). The fire ACTION is `dispatch(request)`; the eval injects a capture.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from croniter import croniter

from .clock import Clock, SystemClock

logger = logging.getLogger("meeting_api.scheduling.scheduler")

DEFAULT_RETRY = {"max_attempts": 3, "backoff": [30, 120, 300], "attempt": 0}

# A dispatch is given the job's `request` (the captured POST /bots call) and returns a result
# dict on success / raises on a retryable failure. Production does HTTP; the eval captures.
Dispatch = Callable[[Dict[str, Any]], Dict[str, Any]]


class Scheduler:
    """Schedule compiled `schedule.v1` jobs and fire them when the `Clock` says they're due."""

    def __init__(self, dispatch: Dispatch, clock: Optional[Clock] = None) -> None:
        self._dispatch = dispatch
        self.clock: Clock = clock or SystemClock()
        # member-json -> score(execute_at); mirrors the redis sorted set `scheduler:jobs`.
        self._jobs: Dict[str, float] = {}
        self._executing: Dict[str, str] = {}   # job_id -> job json (in-flight)
        self._history: Dict[str, str] = {}      # job_id -> job json (terminal)
        self._idem: Dict[str, str] = {}         # idempotency_key -> job json

    # ── job CRUD ─────────────────────────────────────────────────────────────
    def _resolve_execute_at(self, spec: Dict[str, Any]) -> float:
        now = self.clock.now()
        execute_at = spec.get("execute_at")
        cron = spec.get("cron")
        if execute_at is None and cron:
            execute_at = croniter(cron, datetime.fromtimestamp(now, tz=timezone.utc)).get_next(float)
        if isinstance(execute_at, str):
            execute_at = datetime.fromisoformat(execute_at).timestamp()
        if execute_at is None:
            raise ValueError("execute_at or cron is required")
        return float(execute_at)

    def _make_job(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        request = spec.get("request")
        if not request or not request.get("url"):
            raise ValueError("request.url is required")
        return {
            "job_id": f"job_{uuid4().hex[:16]}",
            "execute_at": self._resolve_execute_at(spec),
            "created_at": self.clock.now(),
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
            "cron": spec.get("cron"),
            "idempotency_key": spec.get("idempotency_key"),
        }

    def schedule(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Enqueue a `schedule.v1` job spec. A duplicate idempotency_key returns the existing job."""
        job = self._make_job(spec)
        idem_key = job.get("idempotency_key")
        if idem_key:
            existing = self._idem.get(idem_key)
            if existing:
                logger.info("duplicate idempotency_key=%s, returning existing job", idem_key)
                return json.loads(existing)
            self._idem[idem_key] = json.dumps(job)
        self._jobs[json.dumps(job)] = job["execute_at"]
        return job

    def cancel(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Remove a pending job so it never fires. Returns the cancelled job, or None."""
        for raw in list(self._jobs):
            job = json.loads(raw)
            if job.get("job_id") == job_id:
                del self._jobs[raw]
                job["status"] = "cancelled"
                self._history[job_id] = json.dumps(job)
                return job
        return None

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        for store in (self._executing, self._history):
            raw = store.get(job_id)
            if raw:
                return json.loads(raw)
        for raw in self._jobs:
            job = json.loads(raw)
            if job.get("job_id") == job_id:
                return job
        return None

    def list(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        if status in (None, "pending"):
            results += [json.loads(r) for r in self._jobs]
        if status in (None, "executing"):
            results += [json.loads(r) for r in self._executing.values()]
        results.sort(key=lambda j: j.get("execute_at", 0))
        return results

    # ── execution ────────────────────────────────────────────────────────────
    def _reschedule_cron(self, job: Dict[str, Any]) -> None:
        cron = job.get("cron")
        if not cron:
            return
        next_at = croniter(
            cron, datetime.fromtimestamp(self.clock.now(), tz=timezone.utc)
        ).get_next(float)
        self.schedule(
            {
                "execute_at": next_at,
                "request": job["request"],
                "metadata": job.get("metadata", {}),
                "cron": cron,
            }
        )

    def _process(self, raw: str) -> None:
        job = json.loads(raw)
        job_id = job["job_id"]
        # Atomic claim — if it's already gone (taken/cancelled), skip.
        if self._jobs.pop(raw, None) is None:
            return
        job["status"] = "executing"
        self._executing[job_id] = json.dumps(job)

        retry = job.get("retry", {})
        try:
            result = self._dispatch(job["request"])
            job["status"] = "completed"
            job["result"] = result
            job["completed_at"] = self.clock.now()
        except Exception as e:  # noqa: BLE001 — any dispatch failure is a retry candidate
            attempt = retry.get("attempt", 0) + 1
            max_attempts = retry.get("max_attempts", 3)
            backoff = retry.get("backoff", [30, 120, 300])
            if attempt < max_attempts:
                delay = backoff[min(attempt - 1, len(backoff) - 1)]
                job["retry"]["attempt"] = attempt
                job["status"] = "pending"
                self._executing.pop(job_id, None)
                self._jobs[json.dumps(job)] = self.clock.now() + delay
                logger.warning(
                    "job %s attempt %d/%d failed (%s), retry in %ss",
                    job_id, attempt, max_attempts, e, delay,
                )
                return
            job["status"] = "failed"
            job["error"] = str(e)
            job["failed_at"] = self.clock.now()
            logger.error("job %s permanently failed after %d attempts: %s", job_id, max_attempts, e)

        self._executing.pop(job_id, None)
        self._history[job_id] = json.dumps(job)
        if job["status"] == "completed":
            self._reschedule_cron(job)

    def tick(self) -> int:
        """Fire every job due at the current Clock time. Returns the count processed.

        A real deployment loops `tick()` on an interval; the eval calls it explicitly after
        advancing the FakeClock.
        """
        now = self.clock.now()
        due = [raw for raw, score in self._jobs.items() if score <= now]
        for raw in due:
            self._process(raw)
        return len(due)
