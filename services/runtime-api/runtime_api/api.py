"""/containers CRUD endpoints.

Provides REST API for container lifecycle management:
  POST   /containers              Create and start a container
  GET    /containers              List containers (?profile=&user_id=)
  GET    /containers/{name}       Inspect container
  DELETE /containers/{name}       Stop and remove container
  POST   /containers/{name}/touch Heartbeat (resets idle timer)
  POST   /containers/{name}/exec  Execute command inside container
  GET    /containers/{name}/wait  Long-poll until target state
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from runtime_api import state
from runtime_api.backends import Backend, ContainerSpec
from runtime_api.profiles import get_profile, get_all_profiles

logger = logging.getLogger("runtime_api.api")

router = APIRouter()


# -- Request/Response Models --


class CreateContainerRequest(BaseModel):
    profile: str
    user_id: str
    config: dict = Field(default_factory=dict)
    callback_url: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
    name: Optional[str] = None


class ContainerResponse(BaseModel):
    name: str
    profile: Optional[str] = None
    user_id: Optional[str] = None
    status: Optional[str] = None
    container_id: Optional[str] = None
    ports: dict = Field(default_factory=dict)
    created_at: Optional[float] = None
    metadata: dict = Field(default_factory=dict)
    ip: Optional[str] = None


class ExecRequest(BaseModel):
    cmd: list[str]


class WaitRequest(BaseModel):
    target_status: str = "stopped"
    timeout: float = 300


# -- Helpers --


def _sanitize_name(name: str) -> str:
    """Allow only alphanumeric, hyphens, underscores."""
    return re.sub(r'[^a-zA-Z0-9_-]', '', name)[:128]


def _validate_callback_url(url: str) -> str:
    """Validate callback URL — reject non-HTTP schemes and internal/private targets.

    Set ALLOW_PRIVATE_CALLBACKS=1 for dev/testing where callbacks target
    the Docker bridge gateway or other LAN addresses.
    """
    import ipaddress
    import socket

    from runtime_api import config

    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        raise ValueError(f"callback_url must be http(s), got {parsed.scheme}")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("callback_url must include a hostname")

    # Dev/test escape hatch — skip private-IP checks
    if config.ALLOW_PRIVATE_CALLBACKS:
        return url

    # Normalize and check hostname against known-bad patterns
    hostname_lower = hostname.lower().rstrip('.')
    if hostname_lower in ('localhost', 'metadata.google.internal'):
        raise ValueError("callback_url cannot target localhost or metadata service")
    # Block .internal, .local, .svc TLDs (K8s, mDNS, internal services)
    for suffix in ('.internal', '.local', '.svc', '.svc.cluster.local'):
        if hostname_lower.endswith(suffix):
            raise ValueError(f"callback_url cannot target internal services ({suffix})")

    # Resolve hostname to IP and check if private/loopback/link-local
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        # It's a DNS name — resolve it
        try:
            resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            addrs = [ipaddress.ip_address(r[4][0]) for r in resolved]
        except socket.gaierror:
            # Can't resolve — allow it (may be valid external host)
            return url
        for addr in addrs:
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                raise ValueError(
                    f"callback_url resolves to private/loopback address {addr}"
                )
        return url

    # Direct IP address check
    if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
        raise ValueError(
            f"callback_url cannot target private/loopback/link-local address {addr}"
        )
    return url


def _get_backend(request: Request) -> Backend:
    return request.app.state.backend


def _get_redis(request: Request):
    return request.app.state.redis


def _container_response(name: str, data: dict) -> dict:
    return {
        "name": name,
        "profile": data.get("profile"),
        "user_id": data.get("user_id"),
        "status": data.get("status"),
        "container_id": data.get("container_id"),
        "ports": data.get("ports", {}),
        "created_at": data.get("created_at"),
        "metadata": data.get("metadata", {}),
        "ip": data.get("ip"),
    }


# -- Endpoints --


@router.post("/containers", status_code=201)
async def create_container(req: CreateContainerRequest, request: Request):
    """Create and start a container from a profile."""
    backend = _get_backend(request)
    redis = _get_redis(request)

    profile_def = get_profile(req.profile)
    if not profile_def:
        raise HTTPException(400, f"Unknown profile: {req.profile}")

    # Validate callback URL
    if req.callback_url:
        try:
            _validate_callback_url(req.callback_url)
        except ValueError as e:
            raise HTTPException(400, str(e))

    # Generate container name
    if req.name:
        name = _sanitize_name(req.name)
        if not name:
            raise HTTPException(400, "Container name is invalid after sanitization")
    else:
        suffix = uuid.uuid4().hex[:8]
        # v0.10.5 Pack Q (#275) — pod name uses meeting_id when present
        # in config (BOT_CONFIG), NOT user_id. Pre-Pack-Q: `meeting-{user_id}-{uuid}`
        # produced names like `meeting-2087-ee434b6d` for actual meeting_id
        # 11058 — operator-confusing + audit-tooling-broken-by-design (the
        # natural inference "pod name's integer = meeting_id" was wrong).
        # Now: pod is `meeting-{meeting_id}-{uuid}` so kubectl logs +
        # browse-by-name + Redis state-index reconciliation are aligned
        # with the canonical meeting identifier.
        #
        # Field path: `req.config.meeting_id` (verified by [PLATFORM]
        # cross-reference 2026-04-27 13:13:51Z on #272 — BOT_CONFIG carries
        # meeting_id at the top level). Initial Pack Q implementation used
        # `req.metadata` which is operator-supplied + may not have it;
        # `req.config` is the bot's launch config which always has it for
        # meeting + browser-session profiles.
        meeting_id = None
        if isinstance(req.config, dict):
            meeting_id = req.config.get("meeting_id")
        # Defensive secondary: meeting-api callers also pass meeting_id
        # via metadata (we set this in meeting-api/meetings.py:_create_container).
        if meeting_id is None and isinstance(req.metadata, dict):
            meeting_id = req.metadata.get("meeting_id")
        # Fallback for profiles without an associated meeting (e.g. agent).
        identifier = meeting_id if meeting_id else req.user_id
        name = _sanitize_name(f"{req.profile}-{identifier}-{suffix}")

    # Build env from profile defaults + user config
    env = {**profile_def.get("env", {})}
    user_env = req.config.get("env", {})
    if isinstance(user_env, dict):
        env.update(user_env)

    # Build labels
    labels = {
        "runtime.managed": "true",
        "runtime.profile": req.profile,
        "runtime.user_id": req.user_id,
    }

    # Build mounts from profile + user config
    mounts = list(profile_def.get("mounts", []))
    user_mounts = req.config.get("mounts", [])
    if user_mounts:
        mounts.extend(user_mounts)

    # Build ports from profile
    ports = dict(profile_def.get("ports", {}))

    # Resource config
    resources = profile_def.get("resources", {})

    spec = ContainerSpec(
        name=name,
        image=req.config.get("image") or profile_def["image"],
        command=req.config.get("command") or profile_def.get("command"),
        env=env,
        labels=labels,
        ports=ports,
        mounts=mounts,
        network=req.config.get("network") or profile_def.get("network"),
        shm_size=resources.get("shm_size", 0),
        auto_remove=profile_def.get("auto_remove", True),
        cpu_request=resources.get("cpu_request"),
        cpu_limit=resources.get("cpu_limit"),
        memory_request=resources.get("memory_request"),
        memory_limit=resources.get("memory_limit"),
        gpu=profile_def.get("gpu", False),
        gpu_type=profile_def.get("gpu_type"),
        node_selector=profile_def.get("node_selector", {}),
        working_dir=profile_def.get("working_dir"),
        k8s_overrides=profile_def.get("k8s_overrides", {}),
    )

    # Store state BEFORE starting the container so that the callback_url is
    # available if the container exits immediately (race with Docker event stream).
    container_data = {
        "status": "creating",
        "profile": req.profile,
        "user_id": req.user_id,
        "image": req.config.get("image") or profile_def["image"],
        "created_at": time.time(),
        "ports": {},
        "container_id": "",
        "callback_url": req.callback_url,
        "metadata": req.metadata,
    }
    await state.set_container(redis, name, container_data)

    try:
        container_id = await backend.create(spec)
    except Exception as e:
        logger.error(f"Failed to create container {name}: {e}", exc_info=True)
        await state.delete_container(redis, name)
        raise HTTPException(500, f"Container creation failed: {e}")

    # Get ports and IP from backend
    info = await backend.inspect(name)
    result_ports = info.ports if info else {}
    result_ip = info.ip if info else None

    # Update state with container_id, ports, and IP (only if not already stopped
    # by the exit handler — avoids overwriting stopped/failed status).
    current = await state.get_container(redis, name)
    if current and current.get("status") in ("creating", "running"):
        container_data["status"] = "running"
        container_data["ports"] = result_ports
        container_data["container_id"] = container_id
        container_data["ip"] = result_ip
        await state.set_container(redis, name, container_data)
    else:
        # Container already exited and callback was fired; just update
        # for response but don't overwrite stopped state.
        container_data["status"] = "running"
        container_data["ports"] = result_ports
        container_data["container_id"] = container_id
        container_data["ip"] = result_ip

    return _container_response(name, container_data)


@router.get("/containers")
async def list_containers(
    request: Request,
    user_id: Optional[str] = None,
    profile: Optional[str] = None,
):
    """List containers, optionally filtered by user_id and/or profile."""
    redis = _get_redis(request)
    containers = await state.list_containers(redis, user_id=user_id, profile=profile)
    return [_container_response(c.get("name", ""), c) for c in containers]


@router.get("/containers/{name}")
async def get_container(name: str, request: Request):
    """Get container details."""
    redis = _get_redis(request)
    backend = _get_backend(request)
    data = await state.get_container(redis, name)
    if not data:
        raise HTTPException(404, f"Container {name} not found")
    # Enrich with live IP from backend (K8s pods don't have DNS names)
    if not data.get("ip") and data.get("status") == "running":
        info = await backend.inspect(name)
        if info and info.ip:
            data["ip"] = info.ip
            await state.set_container(redis, name, data)
    return _container_response(name, data)


@router.delete("/containers/{name}")
async def delete_container(name: str, request: Request):
    """Stop and remove a container."""
    backend = _get_backend(request)
    redis = _get_redis(request)

    await backend.stop(name)
    await backend.remove(name)
    await state.set_stopped(redis, name)
    logger.info(f"Stopped and removed {name}")
    return {"name": name, "status": "stopped"}


@router.post("/containers/{name}/touch")
async def touch_container(name: str, request: Request):
    """Update last activity timestamp — keeps container alive during active use."""
    redis = _get_redis(request)
    data = await state.get_container(redis, name)
    if not data:
        raise HTTPException(404, f"Container {name} not found")
    await state.set_container(redis, name, data)  # updates updated_at
    return {"name": name, "status": "touched"}


@router.post("/containers/{name}/exec")
async def exec_in_container(name: str, req: ExecRequest, request: Request):
    """Execute a command inside a running container."""
    backend = _get_backend(request)
    redis = _get_redis(request)

    data = await state.get_container(redis, name)
    if not data:
        raise HTTPException(404, f"Container {name} not found")
    if data.get("status") != "running":
        raise HTTPException(400, f"Container {name} is not running")

    output = b""
    async for chunk in backend.exec(name, req.cmd):
        output += chunk

    # Touch on exec to reset idle timer
    await state.set_container(redis, name, data)

    return {"name": name, "output": output.decode(errors="replace")}


@router.get("/containers/{name}/wait")
async def wait_for_container(
    name: str,
    request: Request,
    target_status: str = "stopped",
    timeout: float = 300,
):
    """Long-poll until container reaches target status or timeout."""
    redis = _get_redis(request)
    deadline = time.time() + timeout

    while time.time() < deadline:
        data = await state.get_container(redis, name)
        if not data:
            return {"name": name, "status": "not_found", "reached": True}
        if data.get("status") == target_status:
            return {
                "name": name,
                "status": target_status,
                "reached": True,
                "exit_code": data.get("exit_code"),
            }
        if data.get("status") in ("stopped", "failed") and target_status != data.get("status"):
            return {
                "name": name,
                "status": data.get("status"),
                "reached": False,
                "exit_code": data.get("exit_code"),
            }
        await asyncio.sleep(2)

    return {"name": name, "status": "timeout", "reached": False}


@router.get("/profiles")
async def list_profiles():
    """List available container profiles."""
    return get_all_profiles()


@router.get("/health")
async def health(request: Request):
    """Health check endpoint."""
    redis = _get_redis(request)
    containers = await state.list_containers(redis)
    running = sum(1 for c in containers if c.get("status") == "running")
    return {"status": "ok", "containers": len(containers), "running": running}
