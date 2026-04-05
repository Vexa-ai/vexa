"""Tests for the /containers API endpoints."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from runtime_api.backends import Backend, ContainerInfo, ContainerSpec


class MockBackend(Backend):
    """In-memory backend for testing."""

    def __init__(self):
        self.containers: dict[str, ContainerInfo] = {}
        self.created_specs: list[ContainerSpec] = []

    async def create(self, spec: ContainerSpec) -> str:
        self.created_specs.append(spec)
        info = ContainerInfo(
            id=f"mock-{spec.name}",
            name=spec.name,
            status="running",
            ports={k.split("/")[0]: 30000 + i for i, k in enumerate(spec.ports)},
            labels=spec.labels,
            created_at=time.time(),
            image=spec.image,
        )
        self.containers[spec.name] = info
        return info.id

    async def stop(self, name: str, timeout: int = 10) -> bool:
        if name in self.containers:
            self.containers[name].status = "stopped"
        return True

    async def remove(self, name: str) -> bool:
        self.containers.pop(name, None)
        return True

    async def inspect(self, name: str) -> Optional[ContainerInfo]:
        return self.containers.get(name)

    async def list(self, labels: dict[str, str] | None = None) -> list[ContainerInfo]:
        return list(self.containers.values())

    async def exec(self, name: str, cmd: list[str]) -> AsyncIterator[bytes]:
        yield b"mock output"


@pytest.fixture
def app():
    """Create test app with mock backend and fake Redis — bypasses real startup."""
    import fakeredis.aioredis
    from runtime_api import config
    from runtime_api.api import router
    from runtime_api.scheduler_api import scheduler_router

    # Use a temporary profiles file
    import tempfile, os
    from fastapi import FastAPI

    profiles = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    profiles.write("""
profiles:
  sandbox:
    image: "ubuntu:24.04"
    command: ["sleep", "infinity"]
    resources:
      memory_limit: "2Gi"
    idle_timeout: 600
  worker:
    image: "python:3.12-slim"
    idle_timeout: 300
""")
    profiles.close()
    config.PROFILES_PATH = profiles.name
    config.API_KEYS = []  # disable auth for tests

    mock_backend = MockBackend()

    @asynccontextmanager
    async def lifespan(app):
        app.state.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        app.state.backend = mock_backend
        from runtime_api.profiles import load_profiles
        load_profiles()
        yield

    test_app = FastAPI(lifespan=lifespan)
    test_app.include_router(router)
    test_app.include_router(scheduler_router)

    yield test_app
    os.unlink(profiles.name)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_create_container(client):
    resp = client.post("/containers", json={
        "profile": "sandbox",
        "user_id": "user-1",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["profile"] == "sandbox"
    assert data["user_id"] == "user-1"
    assert data["status"] == "running"


def test_list_containers(client):
    client.post("/containers", json={"profile": "sandbox", "user_id": "user-1"})
    resp = client.get("/containers")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


def test_delete_container(client):
    create_resp = client.post("/containers", json={"profile": "sandbox", "user_id": "user-1"})
    name = create_resp.json()["name"]
    resp = client.delete(f"/containers/{name}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "stopped"


def test_touch_container(client):
    create_resp = client.post("/containers", json={"profile": "sandbox", "user_id": "user-1"})
    name = create_resp.json()["name"]
    resp = client.post(f"/containers/{name}/touch")
    assert resp.status_code == 200
    assert resp.json()["status"] == "touched"


def test_unknown_profile(client):
    resp = client.post("/containers", json={"profile": "nonexistent", "user_id": "user-1"})
    assert resp.status_code == 400


def test_container_not_found(client):
    resp = client.get("/containers/nonexistent")
    assert resp.status_code == 404


def test_profiles_endpoint(client):
    resp = client.get("/profiles")
    assert resp.status_code == 200
    profiles = resp.json()
    assert "sandbox" in profiles
    assert "worker" in profiles
