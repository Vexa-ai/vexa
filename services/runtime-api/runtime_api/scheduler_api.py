"""/scheduler endpoints — schedule, list, cancel, and inspect jobs."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from runtime_api.scheduler import cancel_job, get_job, list_jobs, schedule_job

logger = logging.getLogger("runtime_api.scheduler_api")

scheduler_router = APIRouter(prefix="/scheduler", tags=["scheduler"])


class ScheduleRequest(BaseModel):
    execute_at: float | str
    request: dict
    retry: Optional[dict] = None
    metadata: dict = Field(default_factory=dict)
    callback: dict = Field(default_factory=dict)
    idempotency_key: Optional[str] = None


@scheduler_router.post("/jobs", status_code=201)
async def create_job(req: ScheduleRequest, request: Request):
    """Schedule an HTTP call for future execution."""
    redis = request.app.state.redis
    spec = req.model_dump(exclude_none=True)
    try:
        job = await schedule_job(redis, spec)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return job


@scheduler_router.get("/jobs")
async def get_jobs(
    request: Request,
    status: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 50,
):
    """List scheduled jobs."""
    redis = request.app.state.redis
    jobs = await list_jobs(redis, status=status, source=source, limit=limit)
    return jobs


@scheduler_router.get("/jobs/{job_id}")
async def get_job_by_id(job_id: str, request: Request):
    """Get a job by ID."""
    redis = request.app.state.redis
    job = await get_job(redis, job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")
    return job


@scheduler_router.delete("/jobs/{job_id}")
async def delete_job(job_id: str, request: Request):
    """Cancel a scheduled job."""
    redis = request.app.state.redis
    job = await cancel_job(redis, job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found or already executed")
    return job
