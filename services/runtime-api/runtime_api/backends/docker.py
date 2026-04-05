"""Docker backend — manages containers via the Docker socket API.

Uses requests_unixsocket for container CRUD and the Docker event stream
for exit detection (no polling).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Optional

import requests_unixsocket

from runtime_api import config
from runtime_api.backends import Backend, ContainerInfo, ContainerSpec
from runtime_api.utils import parse_memory

logger = logging.getLogger("runtime_api.backends.docker")

MANAGED_LABEL = "runtime.managed"


class DockerBackend(Backend):
    def __init__(self):
        self._session: Optional[requests_unixsocket.Session] = None
        self._socket_url: str = ""
        self._event_task: Optional[asyncio.Task] = None

    # -- Socket connection --

    def _init_socket(self) -> str:
        if self._socket_url:
            return self._socket_url
        raw = config.DOCKER_HOST
        path = raw.split("//", 1)[1] if "//" in raw else "/var/run/docker.sock"
        if not path.startswith("/"):
            path = f"/{path}"
        encoded = path.replace("/", "%2F")
        self._socket_url = f"http+unix://{encoded}"
        return self._socket_url

    def _get_session(self) -> requests_unixsocket.Session:
        if self._session is not None:
            return self._session
        url = self._init_socket()
        self._session = requests_unixsocket.Session()
        resp = self._session.get(f"{url}/version", timeout=5)
        resp.raise_for_status()
        ver = resp.json().get("ApiVersion", "?")
        logger.info(f"Docker connected (API {ver})")
        return self._session

    # -- Backend interface --

    async def startup(self) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._get_session)

    async def shutdown(self) -> None:
        if self._event_task:
            self._event_task.cancel()
        if self._session:
            self._session.close()
            self._session = None

    async def create(self, spec: ContainerSpec) -> str:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._create_sync, spec)

    async def stop(self, name: str, timeout: int = 10) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._stop_sync, name, timeout)

    async def remove(self, name: str) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._remove_sync, name)

    async def inspect(self, name: str) -> Optional[ContainerInfo]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._inspect_sync, name)

    async def list(self, labels: dict[str, str] | None = None) -> list[ContainerInfo]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._list_sync, labels)

    async def exec(self, name: str, cmd: list[str]) -> AsyncIterator[bytes]:
        loop = asyncio.get_event_loop()
        exec_id = await loop.run_in_executor(None, self._exec_create_sync, name, cmd)
        if not exec_id:
            return

        session = self._get_session()
        url = self._init_socket()

        resp = await loop.run_in_executor(
            None,
            lambda: session.post(f"{url}/exec/{exec_id}/start", json={"Detach": False}, stream=True),
        )
        # TODO: iter_content is synchronous — blocks event loop during streaming.
        # Migrate to httpx or aiohttp for non-blocking exec streaming.
        for chunk in resp.iter_content(chunk_size=4096):
            if chunk:
                yield chunk

    async def listen_events(self, on_exit: callable) -> None:
        """Listen to Docker event stream for container die events."""
        self._event_task = asyncio.create_task(self._event_loop(on_exit))

    async def _event_loop(self, on_exit: callable) -> None:
        loop = asyncio.get_event_loop()
        while True:
            try:
                await loop.run_in_executor(None, self._stream_events, on_exit, loop)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.debug("Docker event stream reconnecting...", exc_info=True)
                await asyncio.sleep(2)

    def _stream_events(self, on_exit: callable, loop: asyncio.AbstractEventLoop) -> None:
        session = self._get_session()
        url = self._init_socket()
        filters = json.dumps({
            "type": ["container"],
            "event": ["die"],
            "label": [f"{MANAGED_LABEL}=true"],
        })
        resp = session.get(f"{url}/events", params={"filters": filters}, stream=True, timeout=None)
        for line in resp.iter_lines():
            if not line:
                continue
            try:
                event = json.loads(line)
                actor = event.get("Actor", {})
                attrs = actor.get("Attributes", {})
                name = attrs.get("name", "")
                exit_code_str = attrs.get("exitCode", "0")
                exit_code = int(exit_code_str) if exit_code_str else 0
                if name:
                    asyncio.run_coroutine_threadsafe(on_exit(name, exit_code), loop)
            except Exception:
                logger.debug("Failed to parse Docker event", exc_info=True)

    # -- Synchronous Docker API operations --

    def _create_sync(self, spec: ContainerSpec) -> str:
        session = self._get_session()
        url = self._init_socket()

        env_list = [f"{k}={v}" for k, v in spec.env.items()]

        labels = {**spec.labels, MANAGED_LABEL: "true"}

        host_config: dict[str, Any] = {
            "NetworkMode": spec.network or config.DOCKER_NETWORK,
            "AutoRemove": spec.auto_remove,
        }
        if spec.shm_size:
            host_config["ShmSize"] = spec.shm_size
        if spec.ports:
            host_config["PortBindings"] = {p: [{"HostPort": "0"}] for p in spec.ports}
        if spec.mounts:
            host_config["Binds"] = spec.mounts

        # GPU passthrough
        if spec.gpu:
            if spec.gpu_type == "vaapi":
                host_config["Devices"] = [
                    {"PathOnHost": "/dev/dri", "PathInContainer": "/dev/dri",
                     "CgroupPermissions": "rwm"},
                ]
            else:  # default nvidia
                host_config["DeviceRequests"] = [
                    {"Driver": "nvidia", "Count": -1, "Capabilities": [["gpu"]]},
                ]

        # Resource limits
        if spec.memory_limit:
            host_config["Memory"] = parse_memory(spec.memory_limit)

        payload: dict[str, Any] = {
            "Image": spec.image,
            "Env": env_list,
            "Labels": labels,
            "HostConfig": host_config,
        }
        if spec.command:
            payload["Cmd"] = spec.command
        if spec.ports:
            payload["ExposedPorts"] = {p: {} for p in spec.ports}

        resp = session.post(f"{url}/containers/create?name={spec.name}", json=payload)
        if resp.status_code == 409:
            logger.info(f"Container {spec.name} already exists, reusing")
            info = self._inspect_raw(spec.name)
            cid = info.get("Id", "")
            state = info.get("State", {}).get("Status", "")
            if state != "running":
                self._start_sync(spec.name)
            return cid
        resp.raise_for_status()
        container_id = resp.json().get("Id", "")
        logger.info(f"Created container {spec.name} ({container_id[:12]})")

        self._start_sync(spec.name)
        return container_id

    def _start_sync(self, name: str) -> bool:
        session = self._get_session()
        url = self._init_socket()
        resp = session.post(f"{url}/containers/{name}/start")
        return resp.status_code in (204, 304)

    def _stop_sync(self, name: str, timeout: int = 10) -> bool:
        session = self._get_session()
        url = self._init_socket()
        resp = session.post(f"{url}/containers/{name}/stop?t={timeout}")
        if resp.status_code in (204, 304, 404):
            return True
        logger.warning(f"Stop {name} failed: {resp.status_code}")
        return False

    def _remove_sync(self, name: str) -> bool:
        session = self._get_session()
        url = self._init_socket()
        resp = session.delete(f"{url}/containers/{name}?force=true")
        if resp.status_code in (204, 404):
            return True
        logger.warning(f"Remove {name} failed: {resp.status_code}")
        return False

    def _inspect_raw(self, name: str) -> dict:
        session = self._get_session()
        url = self._init_socket()
        resp = session.get(f"{url}/containers/{name}/json")
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        return resp.json()

    def _inspect_sync(self, name: str) -> Optional[ContainerInfo]:
        raw = self._inspect_raw(name)
        if not raw:
            return None
        return _docker_to_info(raw)

    def _list_sync(self, labels: dict[str, str] | None = None) -> list[ContainerInfo]:
        session = self._get_session()
        url = self._init_socket()

        filters: dict[str, list] = {"label": [f"{MANAGED_LABEL}=true"]}
        if labels:
            filters["label"].extend(f"{k}={v}" for k, v in labels.items())

        resp = session.get(
            f"{url}/containers/json",
            params={"filters": json.dumps(filters), "all": "true"},
        )
        resp.raise_for_status()

        results = []
        for c in resp.json():
            cname = c.get("Names", [""])[0].lstrip("/")
            state_str = c.get("State", "").lower()
            ports = _extract_ports(c.get("Ports", []))
            clabels = c.get("Labels", {})
            results.append(ContainerInfo(
                id=c.get("Id", ""),
                name=cname,
                status="running" if state_str == "running" else state_str,
                ports=ports,
                labels=clabels,
                created_at=c.get("Created", 0),
                image=c.get("Image", ""),
            ))
        return results

    def _exec_create_sync(self, name: str, cmd: list[str]) -> Optional[str]:
        session = self._get_session()
        url = self._init_socket()
        resp = session.post(
            f"{url}/containers/{name}/exec",
            json={"AttachStdout": True, "AttachStderr": True, "Cmd": cmd},
        )
        if resp.status_code != 201:
            logger.warning(f"Exec create failed for {name}: {resp.status_code}")
            return None
        return resp.json().get("Id")


# -- Helpers --


def _docker_to_info(raw: dict) -> ContainerInfo:
    state = raw.get("State", {})
    name = raw.get("Name", "").lstrip("/")
    ports = {}
    net_ports = raw.get("NetworkSettings", {}).get("Ports", {})
    for internal, bindings in net_ports.items():
        if bindings:
            host_port = bindings[0].get("HostPort")
            if host_port:
                key = internal.split("/")[0]
                ports[key] = int(host_port)
    return ContainerInfo(
        id=raw.get("Id", ""),
        name=name,
        status=state.get("Status", "unknown"),
        exit_code=state.get("ExitCode"),
        ports=ports,
        labels=raw.get("Config", {}).get("Labels", {}),
        created_at=None,
        image=raw.get("Config", {}).get("Image", ""),
    )


def _extract_ports(port_list: list[dict]) -> dict[str, int]:
    result = {}
    for p in port_list:
        public = p.get("PublicPort")
        private = p.get("PrivatePort")
        if public and private:
            result[str(private)] = public
    return result


