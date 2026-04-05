"""Tests for the /scheduler API endpoints."""

from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app():
    """Create test app with scheduler router and fake Redis."""
    import fakeredis.aioredis
    from fastapi import FastAPI
    from runtime_api.scheduler_api import scheduler_router

    @asynccontextmanager
    async def lifespan(app):
        app.state.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        yield

    test_app = FastAPI(lifespan=lifespan)
    test_app.include_router(scheduler_router)

    yield test_app


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def _job_spec(execute_at=None, url="http://example.com/callback", **kwargs):
    """Helper to build a valid job spec."""
    return {
        "execute_at": execute_at or time.time() + 3600,
        "request": {"method": "POST", "url": url},
        **kwargs,
    }


def test_create_job(client):
    """POST /scheduler/jobs creates a job and returns 201 with job_id."""
    resp = client.post("/scheduler/jobs", json=_job_spec())
    assert resp.status_code == 201
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "pending"
    assert data["request"]["url"] == "http://example.com/callback"


def test_list_jobs(client):
    """GET /scheduler/jobs lists all scheduled jobs."""
    # Create two jobs
    client.post("/scheduler/jobs", json=_job_spec())
    client.post("/scheduler/jobs", json=_job_spec(url="http://example.com/other"))

    resp = client.get("/scheduler/jobs")
    assert resp.status_code == 200
    jobs = resp.json()
    assert len(jobs) >= 2


def test_get_job_by_id(client):
    """GET /scheduler/jobs/{job_id} returns a specific job."""
    create_resp = client.post("/scheduler/jobs", json=_job_spec())
    job_id = create_resp.json()["job_id"]

    resp = client.get(f"/scheduler/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["job_id"] == job_id


def test_get_job_not_found(client):
    """GET /scheduler/jobs/{job_id} returns 404 for nonexistent job."""
    resp = client.get("/scheduler/jobs/job_nonexistent")
    assert resp.status_code == 404


def test_cancel_job(client):
    """DELETE /scheduler/jobs/{job_id} cancels a job."""
    create_resp = client.post("/scheduler/jobs", json=_job_spec())
    job_id = create_resp.json()["job_id"]

    resp = client.delete(f"/scheduler/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


def test_cancel_job_not_found(client):
    """DELETE /scheduler/jobs/{job_id} returns 404 for nonexistent job."""
    resp = client.delete("/scheduler/jobs/job_nonexistent")
    assert resp.status_code == 404


def test_create_job_with_cron(client):
    """POST /scheduler/jobs with cron metadata creates a recurring job."""
    spec = _job_spec(metadata={"cron": "*/5 * * * *", "source": "test"})
    resp = client.post("/scheduler/jobs", json=spec)
    assert resp.status_code == 201
    data = resp.json()
    assert data["metadata"]["cron"] == "*/5 * * * *"


def test_create_job_invalid_no_execute_at(client):
    """POST /scheduler/jobs without execute_at returns 422 (validation error)."""
    resp = client.post("/scheduler/jobs", json={
        "request": {"url": "http://example.com/callback"},
    })
    assert resp.status_code == 422


def test_create_job_invalid_no_request_url(client):
    """POST /scheduler/jobs without request.url returns 400."""
    resp = client.post("/scheduler/jobs", json={
        "execute_at": time.time() + 3600,
        "request": {},
    })
    assert resp.status_code == 400


def test_create_job_iso_timestamp(client):
    """POST /scheduler/jobs accepts ISO 8601 execute_at string."""
    spec = _job_spec(execute_at="2099-01-01T00:00:00+00:00")
    resp = client.post("/scheduler/jobs", json=spec)
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "pending"


def test_create_job_with_idempotency_key(client):
    """Duplicate idempotency_key returns the same job."""
    spec = _job_spec(idempotency_key="unique-123")
    resp1 = client.post("/scheduler/jobs", json=spec)
    resp2 = client.post("/scheduler/jobs", json=spec)
    assert resp1.status_code == 201
    assert resp2.status_code == 201
    assert resp1.json()["job_id"] == resp2.json()["job_id"]


def test_list_jobs_filter_by_status(client):
    """GET /scheduler/jobs?status=pending only returns pending jobs."""
    client.post("/scheduler/jobs", json=_job_spec())
    resp = client.get("/scheduler/jobs?status=pending")
    assert resp.status_code == 200
    for job in resp.json():
        assert job["status"] == "pending"
