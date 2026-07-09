"""The Slim SDK — two PEER domain sub-clients behind one gateway edge:

    slim.agent.*     → the agent control plane   (process, watch, doc, models)
    slim.meetings.*  → the meetings control plane (send/stop bot)

Each sub-client owns its URL PREFIX in ONE place, so the two one-rank domains read symmetrically at the
call site and a gateway rename is a single-line change. The client knows ONLY the gateway base URL + the
X-API-Key — no redis, no per-domain hosts — which is what makes it a living proof of `meetings ⊥ agent`.
"""
from __future__ import annotations

import asyncio

import httpx

from .sse import read_sse_events


class _Domain:
    """Shared base for a domain sub-client: the gateway base, the auth headers, and the domain PREFIX."""
    PREFIX = ""  # overridden per domain

    def __init__(self, base: str, headers: dict, timeout: float) -> None:
        self._base = base
        self._headers = headers
        self._timeout = timeout

    def url(self, path: str) -> str:
        return f"{self._base}{self.PREFIX}{path}"


class AgentApi(_Domain):
    """The agent control plane. Canonical prefix is `/agent` (peer to `/meetings`). The gateway serves
    both `/agent/*` and the deprecated `/api/*` alias; this client targets the canonical name."""
    PREFIX = "/agent"

    async def models(self) -> dict:
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.get(self.url("/models"), headers=self._headers)
            r.raise_for_status()
            return r.json()

    async def start_processing(self, native: str, *, platform: str = "google_meet") -> dict:
        """Launch the copilot processor for a meeting — spawn-or-touch the listening agent so it turns the
        live transcript into notes + cards. Idempotent: if it's already running, this keeps it alive.
        (This only STARTS the producer; observe its output separately with `watch`.)"""
        return await self._set_processing(native, on=True, platform=platform)

    async def stop_processing(self, native: str, *, platform: str = "google_meet") -> dict:
        """Turn the copilot processor OFF — the raw transcript keeps flowing, but no notes/cards are
        produced. The processing cursor is frozen, so a later start resumes where it left off."""
        return await self._set_processing(native, on=False, platform=platform)

    async def _set_processing(self, native: str, *, on: bool, platform: str) -> dict:
        body = {"native_id": native, "platform": platform, "on": on}
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.post(self.url("/meeting/process"), headers=self._headers, json=body)
            r.raise_for_status()
            return r.json()

    async def read_doc(self, native: str) -> "dict | None":
        """The agent's durable meeting doc, or None if not written yet."""
        path = f"kg/entities/meeting/{native}.md"
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.get(self.url("/workspace/file"), headers=self._headers, params={"path": path})
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()

    async def chat(self, prompt: str, *, session: "str | None" = None, active: "dict | None" = None,
                   files: "list[str] | None" = None, on_event=None) -> str:
        """One chat turn over the subject's workspace; stream the SSE reply and return the text.

        Tools are authorized SERVER-SIDE from `active` context (the client cannot pick tools — fail-closed).
        `files=[...]` are workspace-relative paths prompt-injected for the agent to `Read` (in-domain; no
        tool/token needed — `Read` is already granted)."""
        if files:
            listed = ", ".join(files)
            prompt = (f"Read these workspace files before answering (use the Read tool): {listed}\n\n{prompt}")
        body: dict = {"prompt": prompt}
        if session:
            body["session"] = session
        if active:
            body["active"] = active
        chunks: list[str] = []
        async for evt in self._post_sse("/chat", body):
            if on_event:
                on_event(evt)
            if evt.get("type") in ("message-delta", "text", "assistant"):
                chunks.append(evt.get("text") or evt.get("delta") or "")
        return "".join(chunks)

    async def init_workspace(self) -> dict:
        """Materialize this subject's workspace from the validated seed template (idempotent)."""
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(self.url("/workspace/init"), headers=self._headers)
            r.raise_for_status()
            return r.json()

    async def swap_workspace(self) -> dict:
        """Select which validated workspace/template the next dispatch mounts (501 until Phase 6 swap)."""
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(self.url("/workspace/swap"), headers=self._headers)
            r.raise_for_status()
            return r.json()

    # ── routines (the cadence engine: a routine compiles to a durable schedule.v1 cron job) ──────────
    async def create_routine(self, name: str, *, cron: str, prompt: str, run_now: bool = False) -> dict:
        """Author a durable routine — POST `/routines` compiles it to a schedule.v1 cron job (the runtime
        owns the durable cron). `run_now` fires one immediate dispatch so the author sees a result."""
        body = {"name": name, "cron": cron, "prompt": prompt, "run_now": run_now}
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.post(self.url("/routines"), headers=self._headers, json=body)
            r.raise_for_status()
            return r.json()

    async def list_routines(self) -> list:
        """The subject's routines as cards (file + compiled-job state)."""
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.get(self.url("/routines"), headers=self._headers)
            r.raise_for_status()
            return r.json().get("routines", [])

    async def set_routine_enabled(self, name: str, *, enabled: bool) -> dict:
        """Enable/disable a routine by name — PATCH flips the file flag and reconciles the cron job."""
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.patch(self.url(f"/routines/{name}/enabled"), headers=self._headers,
                              json={"enabled": enabled})
            r.raise_for_status()
            return r.json()

    async def workspace_tree(self, *, hidden: bool = False) -> list:
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.get(self.url("/workspace/tree"), headers=self._headers,
                            params={"hidden": str(hidden).lower()})
            r.raise_for_status()
            return r.json().get("files", [])

    async def workspace_file(self, path: str) -> "dict | None":
        """Read any workspace file (the generic form of read_doc); None if absent."""
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.get(self.url("/workspace/file"), headers=self._headers, params={"path": path})
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()

    async def _post_sse(self, path: str, body: dict):
        """POST a request whose response is an SSE stream; yield parsed events (chat turn feed)."""
        timeout = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout) as c:
            async with c.stream("POST", self.url(path), headers=self._headers, json=body) as r:
                r.raise_for_status()
                async for evt in read_sse_events(r):
                    yield evt

    async def watch(self, native: str, *, seconds: float, on_event) -> dict:
        """Tail the merged live feed for `seconds`. For each event: count it by type and hand it to
        `on_event`. Returns the tally, e.g. {"transcript": 80, "note": 99, "card": 33}."""
        tally: dict[str, int] = {}
        async for evt in self._meeting_events(native, for_seconds=seconds):
            kind = evt.get("type", "?")
            tally[kind] = tally.get(kind, 0) + 1
            on_event(evt)
        return tally

    async def _meeting_events(self, native: str, *, for_seconds: float):
        """Open the meeting's SSE feed and yield parsed events until `for_seconds` elapse, then stop.
        A read-timeout (the meeting went quiet) just ends the window — it is not an error."""
        params = {"meeting_id": native, "session_uid": native}
        timeout = httpx.Timeout(connect=10.0, read=for_seconds + 10, write=10.0, pool=10.0)
        deadline = asyncio.get_event_loop().time() + for_seconds
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                async with c.stream("GET", self.url("/meeting/stream"),
                                    headers=self._headers, params=params) as r:
                    r.raise_for_status()
                    async for evt in read_sse_events(r):
                        yield evt
                        if asyncio.get_event_loop().time() >= deadline:
                            return
        except (httpx.ReadTimeout, asyncio.TimeoutError):
            return  # the watch window elapsed with no more events


class MeetingsApi(_Domain):
    """The meetings control plane. Canonical prefix is `/meetings`; the bare resource paths are the
    current (public) surface, so PREFIX stays `""` until the `/meetings/*` alias deploys."""
    PREFIX = ""

    async def send_bot(self, native: str, *, url: str, platform: str = "google_meet",
                       bot_name: str = "VexaSlim", language: str = "en") -> dict:
        body = {"platform": platform, "native_meeting_id": native,
                "bot_name": bot_name, "language": language, "url": url}
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(self.url("/bots"), headers=self._headers, json=body)
            r.raise_for_status()
            return r.json()

    async def stop_bot(self, native: str, *, platform: str = "google_meet") -> int:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.delete(self.url(f"/bots/{platform}/{native}"), headers=self._headers)
            return r.status_code


class Slim:
    """The gateway-only client. Two peer domains as sub-clients: `slim.agent` and `slim.meetings`."""

    def __init__(self, base: str, key: str, *, timeout: float = 15.0) -> None:
        self.base = base
        # X-API-Key is the ONLY auth: the gateway resolves it → user and injects X-User-Id downstream.
        self._headers = {"X-API-Key": key, "Content-Type": "application/json"}
        self._timeout = timeout
        self.agent = AgentApi(base, self._headers, timeout)
        self.meetings = MeetingsApi(base, self._headers, timeout)

    async def auth_me(self) -> dict:
        """`GET /auth/me` — the subject the gateway resolves this key to (identity-read at the edge).
        Raises on an unresolvable key, so it doubles as a connection verify."""
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.get(f"{self.base}/auth/me", headers=self._headers)
            r.raise_for_status()
            return r.json()
