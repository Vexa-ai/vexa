"""DockerBackend — runs a workload as a real Docker container via the Docker **socket API**
(`requests_unixsocket`), matching main's `services/runtime-api/runtime_api/backends/docker.py`.

The runtime talks to the mounted `/var/run/docker.sock` directly — there is **no `docker` CLI in the
image** (main's has none either). Implements the same sync `Backend` port as `ProcessBackend`, so the
kernel's lifecycle is identical regardless of substrate.

Host config (how the spawned container runs) comes from the runtime service's env, not the workload
env: `DOCKER_NETWORK` puts the bot on the same compose network as redis/meeting-api (without it the
bot can't reach the stack), and `DOCKER_SHM_SIZE` gives chromium a real `/dev/shm`.
"""
from __future__ import annotations

import os
from typing import Any, Optional

import requests_unixsocket

from .backend import WorkloadHandle
from .profiles import Runnable

MANAGED_LABEL = "runtime.managed"


def _socket_url() -> str:
    """Encode DOCKER_HOST (unix:///var/run/docker.sock) as a requests_unixsocket http+unix URL."""
    raw = os.getenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    path = raw.split("//", 1)[1] if "//" in raw else "/var/run/docker.sock"
    if not path.startswith("/"):
        path = f"/{path}"
    return f"http+unix://{path.replace('/', '%2F')}"


def _shm_bytes() -> Optional[int]:
    raw = os.getenv("DOCKER_SHM_SIZE", "2g").strip().lower()
    if not raw:
        return None
    mult = {"k": 1024, "m": 1024**2, "g": 1024**3}.get(raw[-1])
    try:
        return int(raw[:-1]) * mult if mult else int(raw)
    except ValueError:
        return None


class DockerBackend:
    name = "docker"

    def __init__(self, name_prefix: str = "vexa-") -> None:
        self._prefix = name_prefix
        self._url = _socket_url()
        self._session = requests_unixsocket.Session()

    def _cname(self, workload_id: str) -> str:
        return f"{self._prefix}{workload_id}"

    def _req(self, method: str, path: str, *, timeout: int = 30, **kw):
        return self._session.request(method, f"{self._url}{path}", timeout=timeout, **kw)

    def start(self, workload_id: str, runnable: Runnable, env: dict[str, str]) -> WorkloadHandle:
        if not runnable.image:
            raise ValueError("docker backend requires an image")
        name = self._cname(workload_id)

        host_config: dict[str, Any] = {}
        network = os.getenv("DOCKER_NETWORK")
        if network:
            host_config["NetworkMode"] = network
        shm = _shm_bytes()
        if shm:
            host_config["ShmSize"] = shm

        # Workspace mount (Workspace primitive): the dispatch's granted git folder is PORTED IN, not
        # cloned — a bind of a host path / named volume the workload env names. Generic: the backend
        # just forwards source→target; the control plane decides what to mount (mode is enforced by the
        # token at the boundary above this).
        binds: list[str] = []
        mount_src = env.get("VEXA_WORKSPACE_MOUNT_SOURCE")
        mount_tgt = env.get("VEXA_WORKSPACE_MOUNT_TARGET")
        if mount_src and mount_tgt:
            binds.append(f"{mount_src}:{mount_tgt}")

        # The Runtime BROKERS the model credential. Two ways, both kept OUT of the dispatch envelope (the
        # agent only ever holds creds the trusted runtime injects): a Claude subscription credentials
        # file bind-mounted read-only (HOST_CLAUDE_CREDENTIALS — the quorum pattern), and/or an
        # ANTHROPIC_API_KEY forwarded from the spawner's env.
        creds = os.getenv("HOST_CLAUDE_CREDENTIALS")
        if creds:
            binds.append(f"{creds}:/root/.claude/.credentials.json:ro")
        if binds:
            host_config["Binds"] = binds

        spawn_env = dict(env)
        anthropic = os.getenv("ANTHROPIC_API_KEY")
        if anthropic and "ANTHROPIC_API_KEY" not in spawn_env:
            spawn_env["ANTHROPIC_API_KEY"] = anthropic

        payload: dict[str, Any] = {
            "Image": runnable.image,
            "Env": [f"{k}={v}" for k, v in spawn_env.items()],
            "Labels": {MANAGED_LABEL: "true", "runtime.workload_id": workload_id},
            "HostConfig": host_config,
        }
        if runnable.command:
            payload["Cmd"] = list(runnable.command)

        r = self._req("POST", f"/containers/create?name={name}", json=payload)
        if r.status_code == 409:  # stale container with this name — replace it
            self._req("DELETE", f"/containers/{name}?force=true")
            r = self._req("POST", f"/containers/create?name={name}", json=payload)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"docker create {name} failed ({r.status_code}): {r.text.strip()}")
        cid = r.json().get("Id", name)

        s = self._req("POST", f"/containers/{cid}/start")
        if s.status_code not in (204, 304):
            raise RuntimeError(f"docker start {name} failed ({s.status_code}): {s.text.strip()}")
        return WorkloadHandle(id=workload_id, impl=name)

    def exit_code(self, h: WorkloadHandle) -> Optional[int]:
        return self._exit_from_inspect(h._impl)  # type: ignore[attr-defined]

    def _exit_from_inspect(self, name: str) -> Optional[int]:
        r = self._req("GET", f"/containers/{name}/json")
        if r.status_code != 200:
            return None  # gone/unknown → still-resolving
        state = r.json().get("State", {})
        if state.get("Running"):
            return None
        code = state.get("ExitCode")
        return int(code) if code is not None else None

    def terminate(self, h: WorkloadHandle) -> None:
        self._req("POST", f"/containers/{h._impl}/stop?t=5")  # type: ignore[attr-defined]

    def kill(self, h: WorkloadHandle) -> None:
        self._req("POST", f"/containers/{h._impl}/kill")  # type: ignore[attr-defined]

    def cleanup(self, h: WorkloadHandle) -> None:
        self._req("DELETE", f"/containers/{h._impl}?force=true")  # type: ignore[attr-defined]
