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
from .mounts import workspace_binds
from .profiles import Runnable

MANAGED_LABEL = "runtime.managed"
WORKLOAD_ID_LABEL = "runtime.workload_id"
_COMPOSE_LABEL = "com.docker.compose.project"

logger = logging.getLogger("runtime_kernel.docker_backend")


def _stop_grace_sec() -> int:
    """How long ``terminate`` lets a workload leave gracefully before the daemon SIGKILLs it.

    A live meeting bot needs real time to honour SIGTERM (leave the meeting, flush the recording,
    POST its terminal lifecycle callback) — its own signal handler bounds that at <25s, so the old
    hard-coded ``t=5`` guaranteed every runtime-initiated stop of a live bot died 137 mid-leave."""
    try:
        return max(1, int(float(os.getenv("RUNTIME_STOP_GRACE_SEC", "30"))))
    except ValueError:
        return 30


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


def _workload_id_from_leaf(leaf: str) -> str:
    """Reverse ``_worker_naming`` for the label-less name-match fallback: a ``worker-*`` container
    leaf maps back to its ``agent-*`` workload id; anything else IS the workload id."""
    if leaf.startswith("worker-"):
        return f"agent-{leaf[len('worker-'):]}"
    return leaf


def _stack_network() -> Optional[str]:
    """The stack-unique compose network every workload this runtime spawns joins (``start()`` sets
    ``HostConfig.NetworkMode`` from the same env). On a SHARED daemon (two vexa stacks on one host —
    the release-host layout) the managed label and the name prefix are IDENTICAL across stacks, so
    the network is THE discriminator that scopes discovery and ``find`` to THIS stack's containers —
    and it works retroactively for label-less incident-era containers too. Unset ⇒ single-stack
    deployment, no scoping (docker's default bridge)."""
    return os.getenv("DOCKER_NETWORK") or None


def _in_stack_network(network: Optional[str], inspect_body: dict) -> bool:
    """Whether an INSPECTED container is attached to the stack network (no scoping when unset).
    Checks both ``HostConfig.NetworkMode`` (what ``start()`` sets) and the live
    ``NetworkSettings.Networks`` map (covers containers attached by name after create)."""
    if not network:
        return True
    if (inspect_body.get("HostConfig") or {}).get("NetworkMode") == network:
        return True
    return network in ((inspect_body.get("NetworkSettings") or {}).get("Networks") or {})


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

        # Workspace mount set (Workspace primitive + WP-A1.1): the dispatch's granted git folders are
        # PORTED IN, not cloned — a bind of a host path / named volume the workload env names. The mount
        # plumbing is SHARED across all three backends (runtime_kernel.mounts.workspace_binds): the store
        # backing bind exposes every in-store active workspace, and any out-of-store mount (a future
        # cross-store shared workspace) gets its own bind. Generic: the backend forwards source→target for
        # each; the control plane decides the set (per-workspace write is gated by the token above this).
        binds: list[str] = []
        for b in workspace_binds(env):
            binds.append(f"{b.source}:{b.target}:ro" if b.read_only else f"{b.source}:{b.target}")

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
            "Labels": {MANAGED_LABEL: "true", WORKLOAD_ID_LABEL: workload_id, **worker_labels},
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

    def find(self, workload_id: str) -> Optional[WorkloadHandle]:
        """Re-derive a live handle for a workload whose in-process handle was lost (restart): the
        container name is deterministic (``prefix + leaf``), so an inspect proves it still exists.
        Returns ``None`` when the substrate has no such container (any state counts as found —
        an exited container still needs stop/destroy to observe/reclaim it truthfully).

        STACK-SCOPED: on a shared daemon a same-named container in ANOTHER stack's network must
        stay invisible — otherwise a re-derived handle would let this stack's stop/destroy reach a
        foreign stack's live bot."""
        name = self._cname(workload_id)
        r = self._req("GET", f"/containers/{name}/json")
        if r.status_code != 200:
            return None
        if not _in_stack_network(_stack_network(), r.json() or {}):
            return None  # exists, but it is ANOTHER stack's container — not ours to touch
        return WorkloadHandle(id=workload_id, impl=name)

    def list_workload_containers(self) -> list[dict]:
        """Discover the workload containers THIS backend spawned — running or exited — for boot
        re-adoption (the orphaned-live-bot fix): every ``start()`` stamps ``runtime.managed=true`` +
        ``runtime.workload_id``, so a label filter recovers them after the runtime process was
        recreated and its in-memory registry lost. A name-prefix fallback catches label-less strays,
        guarded by the compose-project label so the stack's own services are never adopted.

        STACK-SCOPED (the shared-daemon fix): the labels and the name prefix are the SAME constants
        in every vexa stack, so on a shared daemon a label-only filter would adopt ANOTHER stack's
        live bots. When the stack network is configured, both passes additionally filter on it —
        only containers attached to THIS stack's compose network are ever adopted.

        Returns ``[{workload_id, name, running, exit_code, started_at}, …]``; never raises."""
        found: dict[str, dict] = {}
        network = _stack_network()
        try:
            import json as _json

            def _filters(spec: dict) -> str:
                if network:
                    spec = {**spec, "network": [network]}
                return quote(_json.dumps(spec), safe="")

            r = self._req("GET", f"/containers/json?all=1&filters={_filters({'label': [f'{MANAGED_LABEL}=true']})}")
            if r.status_code == 200:
                for c in r.json():
                    wid = (c.get("Labels") or {}).get(WORKLOAD_ID_LABEL)
                    if wid:
                        found[wid] = self._adoptable(wid, c)
            # Fallback: prefix-named containers WITHOUT the labels (spawned before the labels
            # existed). Compose-owned services can share the prefix — exclude anything compose owns.
            fallback_qs = f"?all=1&filters={_filters({})}" if network else "?all=1"
            r = self._req("GET", f"/containers/json{fallback_qs}")
            if r.status_code == 200:
                for c in r.json():
                    labels = c.get("Labels") or {}
                    if WORKLOAD_ID_LABEL in labels or _COMPOSE_LABEL in labels:
                        continue
                    for raw in c.get("Names") or []:
                        leaf = raw.lstrip("/")
                        if leaf.startswith(self._prefix):
                            wid = _workload_id_from_leaf(leaf[len(self._prefix):])
                            found.setdefault(wid, self._adoptable(wid, c))
                            break
        except Exception as e:  # noqa: BLE001 — discovery is a boot aid; it must never crash the boot
            logger.warning("workload container discovery failed: %s", e)
        return list(found.values())

    def _adoptable(self, workload_id: str, c: dict) -> dict:
        """Shape one /containers/json entry as an adoption record for the kernel."""
        name = self._cname(workload_id)
        running = c.get("State") == "running"
        return {
            "workload_id": workload_id,
            "name": name,
            "running": running,
            # /containers/json has no exit code — inspect only the exited ones.
            "exit_code": None if running else self._exit_from_inspect(name),
        }

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
        # SIGTERM + a real grace window (RUNTIME_STOP_GRACE_SEC, default 30): a live meeting bot
        # leaves the meeting / flushes / posts its terminal callback on SIGTERM within <25s. The
        # daemon SIGKILLs after the grace, so termination is still guaranteed.
        grace = _stop_grace_sec()
        self._req("POST", f"/containers/{h._impl}/stop?t={grace}", timeout=grace + 30)  # type: ignore[attr-defined]

    def kill(self, h: WorkloadHandle) -> None:
        self._req("POST", f"/containers/{h._impl}/kill")  # type: ignore[attr-defined]

    def cleanup(self, h: WorkloadHandle) -> None:
        # Reclaim MUST be truthful: a failed force-delete while the container may still be running
        # would let `destroy` report `destroyed` over a live bot. 404 = already gone (fine).
        r = self._req("DELETE", f"/containers/{h._impl}?force=true")  # type: ignore[attr-defined]
        if r.status_code not in (204, 404):
            raise RuntimeError(
                f"docker delete {h._impl} failed ({r.status_code}): {r.text.strip()[:200]}"
            )
