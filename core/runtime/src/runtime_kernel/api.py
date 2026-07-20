"""The runtime HTTP API — realizes runtime.v1's operations (create/get/list/stop/destroy) + delivers
RuntimeEvents to each workload's callbackUrl. A thin FastAPI surface over the kernel; the control plane
(meeting-api, agent-api) calls this to spawn workloads. The API IS runtime.v1's operation surface.

O-RT-2 additions:
  • /health — 200 when the backend + store are reachable and the scheduler (if wired) is live; 503 otherwise.
  • durable callback delivery — events go through a CallbackQueue (enqueue + retry-until-ack), replacing
    the old fire-once POST. A receiver that 500s is retried on the next sweep until it acks."""
from __future__ import annotations

from typing import Callable, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .callbacks import CallbackQueue
from .kernel import QuotaExceeded, Runtime
from .models import RuntimeEvent, StopReason, WorkloadSpec
from .obs import TraceMiddleware, log_event
from .scheduler import Scheduler

# A health probe returns True when its dependency is reachable. Probes must never raise.
HealthCheck = Callable[[], bool]


class StopBody(BaseModel):
    reason: Optional[StopReason] = None


def _queue_deliver(rt: Runtime, queue: CallbackQueue) -> Callable[[RuntimeEvent], None]:
    """Durable delivery: enqueue each event for the workload's callbackUrl. The queue posts
    immediately and keeps anything the receiver hasn't acked, so a later sweep() retries it."""
    def deliver(ev: RuntimeEvent) -> None:
        record = rt.store.get(ev.workloadId)
        url = record.spec.callbackUrl if record else None
        if not url:
            return
        queue.enqueue(url, ev.model_dump(exclude_none=True))
    return deliver


def _default_health_checks(rt: Runtime) -> dict[str, HealthCheck]:
    def backend_ok() -> bool:
        return bool(getattr(rt.backend, "name", None))

    def store_ok() -> bool:
        try:
            rt.store.list()
            return True
        except Exception:
            return False

    return {"backend": backend_ok, "store": store_ok}


def create_app(
    runtime: Optional[Runtime] = None,
    deliver: Optional[Callable[[RuntimeEvent], None]] = None,
    callback_queue: Optional[CallbackQueue] = None,
    health_checks: Optional[dict[str, HealthCheck]] = None,
    scheduler: Optional[Scheduler] = None,
    enforcer=None,
) -> FastAPI:
    rt = runtime or Runtime()
    queue = callback_queue or CallbackQueue()
    sink = deliver or _queue_deliver(rt, queue)
    prior = rt.on_event
    rt.on_event = lambda ev: (prior(ev), sink(ev))  # chain: preserve any existing handler, then deliver

    checks: dict[str, HealthCheck] = dict(_default_health_checks(rt))
    if scheduler is not None:
        # The durable cron is live: probe by listing pending jobs (touches redis without firing).
        checks["scheduler"] = lambda: (scheduler.list(limit=1) is not None)
    if health_checks:
        checks.update(health_checks)

    app = FastAPI(title="vexa-runtime", version="0.12.0")
    app.state.runtime = rt
    app.state.callback_queue = queue
    app.state.scheduler = scheduler
    # Reuse the control-plane caller's X-Trace-Id so workload-spawn logs (logevent.v1) join
    # the same trace as the meeting-api/agent-api request that asked for the workload.
    app.add_middleware(TraceMiddleware)
    dump = lambda s: s.model_dump(exclude_none=True)

    @app.get("/health")
    def health():
        results = {}
        for name, probe in checks.items():
            try:
                results[name] = bool(probe())
            except Exception:
                results[name] = False
        healthy = all(results.values())
        # ADDITIVE config.v1 rows (ADR-0026): the declared capability tri-states (scheduler ·
        # bot_spawn · agent_spawn · model_inference, incl. the credentials-file probe). They never
        # affect `status`/`checks` or the status code — existing consumers keep working; an
        # unconfigured capability degrades a FEATURE, not the process.
        from .config_preflight import capability_health

        body = {"status": "ok" if healthy else "degraded", "checks": results,
                "capabilities": capability_health()}
        return JSONResponse(body, status_code=200 if healthy else 503)

    @app.post("/workloads", status_code=201)
    def create(spec: WorkloadSpec):
        try:
            status = rt.create(spec)
            if enforcer is not None:
                # WP-M8: a workload only gets lifetime/idle enforcement if the
                # reaper knows it exists — register at the single spawn seam.
                enforcer.track(spec.workloadId)
            # SYSTEM event: a workload was spawned for the calling control-plane request.
            log_event(
                "workload_spawned",
                audience="system",
                span="workloads.create",
                fields={"workload_id": spec.workloadId, "profile": spec.profile},
            )
            return dump(status)
        except QuotaExceeded as e:
            log_event(
                "workload_quota_exceeded",
                audience="user",
                level="warning",
                span="workloads.create",
                fields={"workload_id": spec.workloadId, "profile": spec.profile, "error": str(e)},
            )
            raise HTTPException(status_code=429, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/workloads")
    def list_workloads():
        return [dump(s) for s in rt.list()]

    @app.get("/workloads/{workload_id}")
    def get(workload_id: str):
        try:
            return dump(rt.get(workload_id))
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown workload")

    @app.post("/workloads/{workload_id}/stop")
    def stop(workload_id: str, body: StopBody = StopBody()):
        try:
            result = dump(rt.stop(workload_id, body.reason or StopReason.stopped))
            if enforcer is not None:
                enforcer.forget(workload_id)
            return result
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown workload")

    @app.delete("/workloads/{workload_id}")
    def destroy(workload_id: str):
        try:
            result = dump(rt.destroy(workload_id))
            if enforcer is not None:
                enforcer.forget(workload_id)
            return result
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown workload")

    # ── schedule.v1 — the durable cron over HTTP (the control plane registers routine jobs here) ──
    def _require_scheduler() -> Scheduler:
        if scheduler is None:
            raise HTTPException(status_code=503, detail="scheduler not wired")
        return scheduler

    @app.post("/schedule", status_code=201)
    def schedule_job(spec: dict):
        """Register a schedule.v1 job (one-shot ``execute_at`` or re-arming ``cron``). The job's
        ``request`` is the HTTP call fired when due — for a routine, a unit.v1 Invocation POSTed to
        agent-api ``/invocations``. The runtime knows nothing about units (clean isolation)."""
        sched = _require_scheduler()
        try:
            return sched.schedule(spec)
        except ValueError as e:  # missing request.url / execute_at|cron — fail loud (P18)
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/schedule")
    def list_jobs(status: Optional[str] = None, limit: int = 50):
        return _require_scheduler().list(status=status, limit=limit)

    @app.delete("/schedule/{job_id}")
    def cancel_job(job_id: str):
        cancelled = _require_scheduler().cancel(job_id)
        if cancelled is None:
            raise HTTPException(status_code=404, detail="unknown job")
        return cancelled

    return app


# ── ASGI entrypoint (P4) ─────────────────────────────────────────────────────────────────────
# ``uvicorn runtime_kernel.api:app`` (the compose CMD) resolves this. Built LAZILY via PEP 562 so
# importing this module never wires a DockerBackend — the app is constructed only when uvicorn
# touches ``api.app`` at startup, with the real Docker backend + env-driven profile registry.
def __getattr__(name: str):
    if name == "app":
        from .__main__ import build_production_app

        return build_production_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
