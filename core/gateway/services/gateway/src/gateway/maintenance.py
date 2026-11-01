"""The maintenance record, read side (#1964): ``GET /status`` and ``X-Vexa-Maintenance``.

admin-api is the only WRITER of the record (one JSON string under ``vexa:maintenance:active``);
the gateway is the only READER. Three pieces live here:

  * ``MaintenanceCache`` — an in-process snapshot ``{state, record, read_at}`` with a 5 s TTL. The
    first request to find it stale runs ONE store read, bounded at 0.25 s and single-flight; the
    requests that arrive during that read use the current snapshot and never wait. A read that
    fails, times out or returns unparseable JSON leaves the snapshot ``unreachable`` with no
    record. So Redis is touched at most once per TTL per process, and with Redis down nothing in
    the request path waits longer than the bound.
  * ``status_body`` — the public ``/status`` body. Fixed shape, no versions, hosts or counts.
  * ``MaintenanceHeaderMiddleware`` — pure ASGI: while a record is active, every HTTP reply the
    gateway builds carries ``x-vexa-maintenance: <id>``. Installed OUTERMOST (after the guard), so
    the guard's own 429/403 and the edge's 401/403/404 carry it too. A bare 500 from Starlette's
    ``ServerErrorMiddleware`` and the ``/ws`` handshake do not pass through here (known limits).
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

from .ports import MaintenanceStore

MAINTENANCE_KEY = "vexa:maintenance:active"
MAINTENANCE_HEADER = "x-vexa-maintenance"
TTL_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 0.25
#: The id is put in a response header, so it is only accepted in this header-safe shape (the same
#: rule admin-api validates on write). Anything else is treated as an unreadable record.
_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_PLATFORMS = ("google_meet", "zoom", "teams")
_TOP_LEVEL = ("api", "transcription", "dashboard")


class NullMaintenanceStore:
    """The default store: there is never a record. ``create_app`` without a store uses it."""

    async def get(self) -> Optional[str]:
        return None


class RedisMaintenanceStore:
    """``MaintenanceStore`` over the gateway's existing ``redis.asyncio`` client (decode_responses)."""

    def __init__(self, client: Any):
        self._client = client

    async def get(self) -> Optional[str]:
        return await self._client.get(MAINTENANCE_KEY)


def parse_record(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """The record as the gateway trusts it, or ``None`` for "no record". Raises on bad JSON."""
    if raw is None or raw == "":
        return None
    doc = json.loads(raw)
    if not isinstance(doc, dict) or not isinstance(doc.get("id"), str) or not _ID_RE.match(doc["id"]):
        raise ValueError("maintenance record has no header-safe id")
    components = doc.get("components") or []
    return {
        "id": doc["id"],
        "kind": doc.get("kind") if doc.get("kind") in ("update", "incident") else "update",
        "started_at": doc.get("started_at"),
        "expected_end": doc.get("expected_end"),
        "message": str(doc.get("message") or ""),
        "components": [str(c) for c in components] if isinstance(components, list) else [],
    }


class MaintenanceCache:
    """The in-process snapshot of the record. ``snapshot()`` is what every request reads."""

    def __init__(self, store: Optional[MaintenanceStore] = None, *, ttl: float = TTL_SECONDS,
                 read_timeout: float = READ_TIMEOUT_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        self._store = store if store is not None else NullMaintenanceStore()
        self._ttl, self._timeout, self._clock = ttl, read_timeout, clock
        self._lock = asyncio.Lock()
        self.state: str = "ok"          # "ok" | "unreachable"
        self.record: Optional[Dict[str, Any]] = None
        self.read_at: Optional[float] = None
        self.updated_at: datetime = datetime.now(timezone.utc)
        self.reads: int = 0             # store reads attempted (the tests pin single-flight on it)

    def _stale(self) -> bool:
        return self.read_at is None or (self._clock() - self.read_at) >= self._ttl

    async def snapshot(self) -> Tuple[str, Optional[Dict[str, Any]]]:
        if self._stale() and not self._lock.locked():
            async with self._lock:
                if self._stale():
                    await self._refresh()
        return self.state, self.record

    async def _refresh(self) -> None:
        self.reads += 1
        try:
            raw = await asyncio.wait_for(self._store.get(), self._timeout)
            self.state, self.record = "ok", parse_record(raw)
        except Exception:
            self.state, self.record = "unreachable", None
        self.read_at = self._clock()
        self.updated_at = datetime.now(timezone.utc)


def status_body(state: str, record: Optional[Dict[str, Any]], updated_at: datetime) -> Dict[str, Any]:
    """The ``/status`` body for one snapshot. ``outage`` is reserved for live signals (next slice)."""
    marks = {name: "operational" for name in _TOP_LEVEL + tuple(f"bots.{p}" for p in _PLATFORMS)}
    maintenance = None
    if state != "ok":
        status, marks["api"] = "degraded", "degraded"
    elif record is None:
        status = "operational"
    else:
        status = "maintenance"
        named = record["components"] or list(marks)
        for name in named:
            targets = [f"bots.{p}" for p in _PLATFORMS] if name == "bots" else [name]
            for t in targets:
                if t in marks:
                    marks[t] = "maintenance"
        maintenance = {k: record[k] for k in ("id", "kind", "started_at", "expected_end", "message")}
    return {
        "status": status,
        "components": {
            "api": marks["api"],
            "bots": {p: marks[f"bots.{p}"] for p in _PLATFORMS},
            "transcription": marks["transcription"],
            "dashboard": marks["dashboard"],
        },
        "maintenance": maintenance,
        "updated_at": updated_at.isoformat().replace("+00:00", "Z"),
    }


class MaintenanceHeaderMiddleware:
    """Pure ASGI: append ``x-vexa-maintenance: <id>`` to every HTTP reply while a record is active."""

    def __init__(self, app, cache: MaintenanceCache):
        self._app, self._cache = app, cache

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        _state, record = await self._cache.snapshot()
        if record is None:
            await self._app(scope, receive, send)
            return
        value = record["id"].encode("ascii")

        async def send_stamped(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((MAINTENANCE_HEADER.encode("ascii"), value))
                message = {**message, "headers": headers}
            await send(message)

        await self._app(scope, receive, send_stamped)


def apply_maintenance_header(app, cache: Optional[MaintenanceCache] = None) -> None:
    """Install the header middleware. Call AFTER ``apply_guard`` so it is the outermost layer."""
    cache = cache if cache is not None else app.state.maintenance_cache
    app.add_middleware(MaintenanceHeaderMiddleware, cache=cache)
