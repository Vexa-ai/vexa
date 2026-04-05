"""Container manager — delegates lifecycle to Runtime API, keeps exec local.

Container lifecycle (create/stop/list) goes through Runtime API.
Container exec (docker exec for agent CLI streaming) stays as local Docker CLI
subprocess — streaming exec through HTTP would add latency for no benefit.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from agent_api import config

logger = logging.getLogger("agent_api.container_manager")


@dataclass
class ContainerInfo:
    name: str
    user_id: str
    session_id: str = "default"
    workspace_name: str = "default"
    last_activity: float = field(default_factory=time.time)


class ContainerManager:
    """Manages agent containers via Runtime API + local docker exec."""

    def __init__(self, runtime_api_url: str = "", api_key: str = ""):
        self._runtime_api = runtime_api_url or config.RUNTIME_API_URL
        self._api_key = api_key or config.API_KEY
        self._containers: dict[str, ContainerInfo] = {}  # "user_id:session_id" -> info
        self._http: Optional[httpx.AsyncClient] = None
        self._admin_http: Optional[httpx.AsyncClient] = None
        self._new_container: bool = False
        self._last_user_data: dict[str, dict] = {}  # user_id -> user.data, per-user cache

    async def startup(self):
        """Initialize HTTP clients for Runtime API and Admin API, discover existing containers."""
        headers = {"X-API-Key": self._api_key} if self._api_key else {}
        self._http = httpx.AsyncClient(
            base_url=self._runtime_api, timeout=30, headers=headers,
        )
        admin_headers = {"X-Admin-API-Key": config.ADMIN_API_TOKEN} if config.ADMIN_API_TOKEN else {}
        self._admin_http = httpx.AsyncClient(
            base_url=config.ADMIN_API_URL, timeout=10, headers=admin_headers,
        )
        # Discover existing agent containers
        try:
            resp = await self._http.get("/containers", params={"profile": "agent"})
            if resp.status_code == 200:
                for c in resp.json():
                    if c.get("status") == "running":
                        uid = c.get("user_id", "")
                        key = f"{uid}:default"
                        self._containers[key] = ContainerInfo(name=c["name"], user_id=uid, session_id="default")
                        logger.info(f"Discovered container {c['name']} for user {uid}")
        except Exception as e:
            logger.warning(f"Could not discover containers from Runtime API: {e}")
        logger.info(f"Container manager started (runtime={self._runtime_api})")

    async def shutdown(self):
        """Close HTTP clients."""
        if self._http:
            await self._http.aclose()
        if self._admin_http:
            await self._admin_http.aclose()
        logger.info("Container manager shut down")

    # --- User data ---

    async def get_user_data(self, user_id: str) -> dict:
        """Fetch user.data from admin-api. Returns empty dict on failure.

        Admin-api expects integer user IDs. Non-numeric user_ids skip the lookup.
        Result is cached per user_id for the container's lifetime.
        """
        # Return cached if available
        if user_id in self._last_user_data:
            return self._last_user_data[user_id]

        # Admin API requires integer user ID
        try:
            int_id = int(user_id)
        except (ValueError, TypeError):
            logger.debug(f"Non-numeric user_id '{user_id}', skipping admin-api lookup")
            return {}

        try:
            resp = await self._admin_http.get(f"/admin/users/{int_id}")
            if resp.status_code == 200:
                data = resp.json().get("data", {}) or {}
                self._last_user_data[user_id] = data
                return data
            elif resp.status_code == 404:
                logger.debug(f"User {int_id} not found in admin-api")
            else:
                logger.warning(f"Admin-api returned {resp.status_code} for user {int_id}")
        except Exception as e:
            logger.warning(f"Failed to fetch user data for {user_id}: {e}")
        return {}

    # --- Container operations ---

    async def _is_alive(self, name: str) -> bool:
        """Check if container is actually running via docker inspect."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "inspect", "--format", "{{.State.Status}}", name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            return proc.returncode == 0 and stdout.decode().strip() == "running"
        except Exception:
            return False

    async def ensure_container(self, user_id: str, session_id: str = "default", **create_kwargs) -> str:
        """Ensure a running agent container exists. Returns container name.

        Additional kwargs are passed to the Runtime API POST /containers body.
        """
        self._new_container = False
        key = f"{user_id}:{session_id}"

        # Check local cache
        info = self._containers.get(key)
        if info:
            if await self._is_alive(info.name):
                info.last_activity = time.time()
                await self._touch(info.name)
                return info.name
            self._containers.pop(key, None)
            self._last_user_data.pop(user_id, None)  # stale config for dead container

        # Create via Runtime API
        logger.info(f"Requesting container for user {user_id} session {session_id}")
        body = {"user_id": user_id, "profile": "agent", **create_kwargs}

        # Inject Claude credentials into container config
        agent_config = body.setdefault("config", {})
        agent_env = agent_config.setdefault("env", {})
        agent_mounts = agent_config.setdefault("mounts", [])

        # Pass ANTHROPIC_API_KEY if available
        if config.ANTHROPIC_API_KEY:
            agent_env["ANTHROPIC_API_KEY"] = config.ANTHROPIC_API_KEY

        # S3/MinIO credentials for workspace sync (aws s3 sync) inside container
        if config.S3_ACCESS_KEY:
            agent_env["AWS_ACCESS_KEY_ID"] = config.S3_ACCESS_KEY
            agent_env["AWS_SECRET_ACCESS_KEY"] = config.S3_SECRET_KEY
        if config.S3_ENDPOINT:
            agent_env["S3_ENDPOINT"] = config.S3_ENDPOINT
            agent_env["AWS_DEFAULT_REGION"] = "us-east-1"

        # Mount Claude OAuth credential files if paths are set
        if config.CLAUDE_CREDENTIALS_PATH:
            agent_mounts.append(
                f"{config.CLAUDE_CREDENTIALS_PATH}:/root/.claude/.credentials.json:ro"
            )
        if config.CLAUDE_JSON_PATH:
            agent_mounts.append(
                f"{config.CLAUDE_JSON_PATH}:/root/.claude.json:ro"
            )

        # Inject per-user env vars from admin-api user.data['env']
        user_data = await self.get_user_data(user_id)
        user_env = user_data.get("env", {})
        if user_env and isinstance(user_env, dict):
            agent_env.update(user_env)
            logger.info(f"Injected {len(user_env)} user env vars for {user_id}")

        resp = await self._http.post("/containers", json=body)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Runtime API failed: {resp.status_code} {resp.text[:200]}")

        data = resp.json()
        name = data["name"]
        self._containers[key] = ContainerInfo(name=name, user_id=user_id, session_id=session_id)
        self._new_container = True
        logger.info(f"Container {name} created for user {user_id} session {session_id}")
        return name

    async def start_agent(self, session_id: str, agent_config: dict = None,
                          callback_url: str = None) -> str:
        """Create an agent container via Runtime API. Returns container name."""
        body = {"user_id": session_id, "profile": "agent"}
        if agent_config:
            body["config"] = agent_config
        if callback_url:
            body["callback_url"] = callback_url
        resp = await self._http.post("/containers", json=body)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Runtime API failed: {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        name = data["name"]
        key = f"{session_id}:default"
        self._containers[key] = ContainerInfo(name=name, user_id=session_id)
        return name

    async def stop_agent(self, container_id: str):
        """Stop a container via Runtime API."""
        try:
            await self._http.delete(f"/containers/{container_id}")
        except Exception as e:
            logger.warning(f"Error stopping {container_id}: {e}")
        # Remove from cache if present
        for key, info in list(self._containers.items()):
            if info.name == container_id:
                self._containers.pop(key, None)
                break

    async def get_status(self, container_id: str) -> dict:
        """Get container status from Runtime API."""
        resp = await self._http.get(f"/containers/{container_id}")
        if resp.status_code == 404:
            return {"name": container_id, "status": "not_found"}
        resp.raise_for_status()
        return resp.json()

    async def stop_session_container(self, user_id: str, session_id: str = "default"):
        """Stop the container for a specific user session."""
        key = f"{user_id}:{session_id}"
        info = self._containers.get(key)
        if not info:
            return
        await self.stop_agent(info.name)

    async def _touch(self, container: str):
        """Tell Runtime API this container is actively in use."""
        try:
            await self._http.post(f"/containers/{container}/touch")
        except Exception:
            pass

    # --- Exec operations (local docker CLI) ---

    async def exec_stream(self, container: str, cmd: str) -> asyncio.subprocess.Process:
        """Run a shell command in the container, return subprocess for streaming."""
        await self._touch(container)
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", "-i", container, "bash", "-c", cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,
        )
        return proc

    async def exec_simple(self, container: str, cmd: list[str]) -> Optional[str]:
        """Run a command in the container, return stdout or None."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", container, *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode == 0 and stdout.strip():
                return stdout.decode(errors="replace").strip()
        except (asyncio.TimeoutError, Exception) as e:
            logger.debug(f"exec_simple failed: {e}")
        return None

    async def exec_with_stdin(self, container: str, cmd: list[str],
                              stdin_data: bytes) -> Optional[str]:
        """Run a command in the container with stdin piped. Returns stdout or None."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", "-i", container, *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(input=stdin_data), timeout=30,
            )
            if proc.returncode == 0 and stdout.strip():
                return stdout.decode(errors="replace").strip()
        except (asyncio.TimeoutError, Exception) as e:
            logger.debug(f"exec_with_stdin failed: {e}")
        return None

    async def interrupt(self, user_id: str, session_id: str = "default",
                        process_pattern: str = "claude.*stream-json"):
        """Kill active agent process in user's container."""
        key = f"{user_id}:{session_id}"
        info = self._containers.get(key)
        if not info:
            return
        try:
            await self.exec_simple(info.name, [
                "sh", "-c", f"pkill -f '{process_pattern}' || true",
            ])
        except Exception as e:
            logger.warning(f"Interrupt failed for {user_id}:{session_id}: {e}")

    async def reset_session(self, user_id: str, session_id: str = "default"):
        """Kill active process and clear session state in container."""
        await self.interrupt(user_id, session_id)
        key = f"{user_id}:{session_id}"
        info = self._containers.get(key)
        if info:
            await self.exec_simple(info.name, ["rm", "-f", "/tmp/.agent-session"])

    def get_container_name(self, user_id: str, session_id: str = "default") -> Optional[str]:
        key = f"{user_id}:{session_id}"
        info = self._containers.get(key)
        return info.name if info else None
