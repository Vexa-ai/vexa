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
) -> FastAPI:
    rt = runtime or Runtime()
    queue = callback_queue or CallbackQueue()
    sink = deliver or _queue_deliver(rt, queue)
    prior = rt.on_event
    rt.on_event = lambda ev: (prior(ev), sink(ev))  # chain: preserve any existing handler, then deliver

    checks: dict[str, HealthCheck] = dict(_default_health_checks(rt))
    if health_checks:
        checks.update(health_checks)

    app = FastAPI(title="vexa-runtime", version="0.12.0")
    app.state.runtime = rt
    app.state.callback_queue = queue
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
        body = {"status": "ok" if healthy else "degraded", "checks": results}
        return JSONResponse(body, status_code=200 if healthy else 503)

    @app.post("/workloads", status_code=201)
    def create(spec: WorkloadSpec):
        try:
            status = rt.create(spec)
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
            return dump(rt.stop(workload_id, body.reason or StopReason.stopped))
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown workload")

    @app.delete("/workloads/{workload_id}")
    def destroy(workload_id: str):
        try:
            return dump(rt.destroy(workload_id))
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown workload")

    return app
