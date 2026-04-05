"""HTTP client — SSE streaming to agent-api."""

import json
from typing import AsyncIterator, Optional

import httpx


class VexaClient:
    """Thin async client for agent-api."""

    def __init__(self, endpoint: str, api_key: str):
        self.endpoint = endpoint.rstrip("/")
        self.headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    async def chat_stream(
        self,
        user_id: str,
        message: str,
        session_id: Optional[str] = None,
        session_name: Optional[str] = None,
        model: Optional[str] = None,
        cli_flags: Optional[list] = None,
    ) -> AsyncIterator[dict]:
        """POST /api/chat, yield parsed SSE events."""
        payload = {"user_id": user_id, "message": message}
        if session_id:
            payload["session_id"] = session_id
        if session_name:
            payload["session_name"] = session_name
        if model:
            payload["model"] = model
        if cli_flags:
            payload["cli_flags"] = cli_flags

        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as http:
            async with http.stream(
                "POST",
                f"{self.endpoint}/api/chat",
                json=payload,
                headers=self.headers,
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise RuntimeError(f"HTTP {resp.status_code}: {body.decode()[:500]}")
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            yield json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue

    async def interrupt(self, user_id: str):
        """DELETE /api/chat — interrupt running agent."""
        async with httpx.AsyncClient(timeout=10) as http:
            await http.request(
                "DELETE",
                f"{self.endpoint}/api/chat",
                json={"user_id": user_id},
                headers=self.headers,
            )

    async def list_sessions(self, user_id: str) -> list:
        """GET /api/sessions."""
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.get(
                f"{self.endpoint}/api/sessions",
                params={"user_id": user_id},
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json().get("sessions", [])

    async def rename_session(self, user_id: str, session_id: str, name: str) -> dict:
        """PUT /api/sessions/{session_id}."""
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.put(
                f"{self.endpoint}/api/sessions/{session_id}",
                json={"user_id": user_id, "name": name},
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def create_session(self, user_id: str, name: str) -> dict:
        """POST /api/sessions."""
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.post(
                f"{self.endpoint}/api/sessions",
                json={"user_id": user_id, "name": name},
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def reset_session(self, user_id: str):
        """POST /api/chat/reset."""
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.post(
                f"{self.endpoint}/api/chat/reset",
                json={"user_id": user_id},
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def workspace_files(self, user_id: str) -> list:
        """GET /api/workspace/files."""
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.get(
                f"{self.endpoint}/api/workspace/files",
                params={"user_id": user_id},
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json().get("files", [])

    async def workspace_read(self, user_id: str, path: str) -> str:
        """GET /api/workspace/file."""
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.get(
                f"{self.endpoint}/api/workspace/file",
                params={"user_id": user_id, "path": path},
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json().get("content", "")

    async def workspace_write(self, user_id: str, path: str, content: str) -> dict:
        """POST /api/workspace/file."""
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.post(
                f"{self.endpoint}/api/workspace/file",
                json={"user_id": user_id, "path": path, "content": content},
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def status(self, user_id: str) -> dict:
        """GET /internal/workspace/status + /health."""
        async with httpx.AsyncClient(timeout=10) as http:
            health = await http.get(f"{self.endpoint}/health")
            ws = await http.get(
                f"{self.endpoint}/internal/workspace/status",
                params={"user_id": user_id},
            )
            return {
                "health": health.json() if health.status_code == 200 else {"status": "unreachable"},
                "workspace": ws.json() if ws.status_code == 200 else {},
            }
