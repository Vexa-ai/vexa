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

import logging
import os
from typing import Any, Optional
from urllib.parse import quote

import requests_unixsocket

from .backend import WorkloadHandle
from .profiles import Runnable

MANAGED_LABEL = "runtime.managed"

logger = logging.getLogger("runtime_kernel.docker_backend")


def _worker_naming(workload_id: str) -> tuple[str, dict[str, str]]:
    """Map an agent-dispatch workload id to its container leaf name + worker grouping labels.

    Agent workers carry an ``agent-…`` workload id (minted by ``agent_api.units.dispatch_id``). For
    Docker visibility we rename the *container* leaf from ``agent-*`` to ``worker-*`` (the workload id,
    Stream topics, and reaper keys are UNTOUCHED — only the cosmetic container name changes) and stamp
    ``vexa.role=worker`` plus a ``vexa.kind`` (meet | chat | event) so the ephemeral workers can be
    filtered/grouped apart from the compose-owned ``agent-api`` service. Non-agent workloads (e.g.
    ``meeting-bot``) pass through unchanged with no extra labels.
    """
    if not workload_id.startswith("agent-"):
        return workload_id, {}
    rest = workload_id[len("agent-"):]
    if rest.startswith("meet-"):
        kind = "meet"
    elif rest.endswith("-chat"):
        kind = "chat"
    else:
        kind = "event"
    return f"worker-{rest}", {"vexa.role": "worker", "vexa.kind": kind}


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
        leaf, _labels = _worker_naming(workload_id)
        return f"{self._prefix}{leaf}"

    def _req(self, method: str, path: str, *, timeout: int = 30, **kw):
        return self._session.request(method, f"{self._url}{path}", timeout=timeout, **kw)

    def _image_exists(self, ref: str) -> bool:
        r = self._req("GET", f"/images/{ref}/json")
        return r.status_code == 200

    def ensure_worker_image(self, target: str) -> str:
        """Ensure ``target`` (the agent-worker image) is PRESENT on the daemon, PULLING it from its
        registry when absent. The daemon's create API never implicit-pulls, and the compose
        ``agent-worker`` service is a build-only profile that ``up``/``pull`` skip — so on a
        published-images deployment nothing else ever fetches this image.

        This REPLACES the pre-0.12.0 tag ALIAS that re-tagged the agent-api image under this name:
        the worker is a DIFFERENT build (claude-code + node + the ``worker`` package —
        core/agent/worker/Dockerfile), so aliasing agent-api bytes made every dispatch die with
        ``No module named worker`` and left an impostor local tag masquerading as the published image.

        Idempotent (no-op when ``target`` is already local — the compose-built ``:dev`` path).
        FAIL-VISIBLE: if the pull fails, ``target`` is still returned so the spawn fails loudly with
        ``No such image: <target>`` — never silently running the wrong bytes. Never raises."""
        if not target:
            return target
        try:
            if self._image_exists(target):
                return target  # already local (built or previously pulled) — no-op
            repo, _, tag = target.partition(":")
            logger.info("worker image %s not present locally; pulling", target)
            r = self._req(
                "POST",
                f"/images/create?fromImage={quote(repo, safe='')}&tag={quote(tag or 'latest', safe='')}",
                timeout=600,
            )
            # /images/create streams pull progress and can report errors mid-stream with a 200 —
            # only a re-check of the local store proves the pull landed.
            if r.status_code == 200 and self._image_exists(target):
                logger.info("worker image pulled: %s", target)
                return target
            logger.error(
                "worker image %s could not be pulled (%s): %s — agent dispatch will fail with "
                "'No such image' until it is pulled or built (docker compose build agent-worker)",
                target, r.status_code, r.text.strip()[:300],
            )
        except Exception as e:  # noqa: BLE001 — image ensure must NEVER break the boot
            logger.error(
                "worker image %s pull errored: %s — agent dispatch will fail until it is present",
                target, e,
            )
        return target

    def start(self, workload_id: str, runnable: Runnable, env: dict[str, str]) -> WorkloadHandle:
        if not runnable.image:
            raise ValueError("docker backend requires an image")
        name = self._cname(workload_id)
        _leaf, worker_labels = _worker_naming(workload_id)

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

        # The Runtime BROKERS model credentials. Subscription credentials are mounted read-only;
        # API-style provider env (the VEXA_LLM_* completion dials + the claude-code runner's
        # ANTHROPIC_*) is copied from the trusted runtime service into spawned workers.
        creds = os.getenv("HOST_CLAUDE_CREDENTIALS")
        if creds:
            binds.append(f"{creds}:/root/.claude/.credentials.json:ro")
        # DEV hot-mount (parallels the dev.yml service hot-reload): bind the HOST agent_api source over
        # the image's baked copy so a SPAWNED worker runs the latest worker.py with NO image rebuild —
        # the next spawn picks up the change. Host path (daemon-resolved); set only in dev.
        dev_src = os.getenv("VEXA_AGENT_SRC_MOUNT")
        if dev_src:
            binds.append(f"{dev_src}:/app/src/agent_api:ro")
        if binds:
            host_config["Binds"] = binds

        spawn_env = dict(env)
        for key in (
            # llm-module dials (provider-agnostic): completion endpoint/credential/model + the
            # harness runner selection. Dispatch-stamped values win (`key not in spawn_env`).
            "VEXA_LLM_PROVIDER",
            "VEXA_LLM_BASE_URL",
            "VEXA_LLM_API_KEY",
            "VEXA_LLM_MODEL",
            "VEXA_LLM_MAX_TOKENS",
            "VEXA_MODEL_ALLOWLIST",
            "VEXA_RUNNER",
            # claude-code harness credentials (that adapter's concern only)
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_MODEL",
            "ANTHROPIC_DEFAULT_OPUS_MODEL",
            "ANTHROPIC_DEFAULT_SONNET_MODEL",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        ):
            value = os.getenv(key)
            if value and key not in spawn_env:
                spawn_env[key] = value

        payload: dict[str, Any] = {
            "Image": runnable.image,
            "Env": [f"{k}={v}" for k, v in spawn_env.items()],
            "Labels": {MANAGED_LABEL: "true", "runtime.workload_id": workload_id, **worker_labels},
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
