"""The runtime HTTP API — realizes runtime.v1's operations (create/get/list/stop/destroy) + delivers
RuntimeEvents to each workload's callbackUrl. A thin FastAPI surface over the kernel; the control plane
(meeting-api, agent-api) calls this to spawn workloads. The API IS runtime.v1's operation surface."""
from __future__ import annotations

from typing import Callable, Optional

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .kernel import Runtime
from .models import RuntimeEvent, StopReason, WorkloadSpec


class StopBody(BaseModel):
    reason: Optional[StopReason] = None


def _http_deliver(rt: Runtime) -> Callable[[RuntimeEvent], None]:
    """Default delivery: POST each event to the workload's callbackUrl (best-effort, never throws out)."""
    def deliver(ev: RuntimeEvent) -> None:
        entry = rt._workloads.get(ev.workloadId)
        url = entry.spec.callbackUrl if entry else None
        if not url:
            return
        try:
            httpx.post(url, json=ev.model_dump(exclude_none=True), timeout=2.0)
        except Exception:
            pass
    return deliver


def create_app(runtime: Optional[Runtime] = None, deliver: Optional[Callable[[RuntimeEvent], None]] = None) -> FastAPI:
    rt = runtime or Runtime()
    sink = deliver or _http_deliver(rt)
    prior = rt.on_event
    rt.on_event = lambda ev: (prior(ev), sink(ev))  # chain: preserve any existing handler, then deliver

    app = FastAPI(title="vexa-runtime", version="0.12.0")
    dump = lambda s: s.model_dump(exclude_none=True)

    @app.post("/workloads", status_code=201)
    def create(spec: WorkloadSpec):
        try:
            return dump(rt.create(spec))
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
