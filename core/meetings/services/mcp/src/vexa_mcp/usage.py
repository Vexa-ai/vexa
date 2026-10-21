"""USAGE — one record per MCP `tools/call`, logged always and optionally delivered to a sink.

WHY: a hosted deployment needs to know who calls which tool from which client, and the only place
that sees a tool call AS a tool call is this edge — the gateway sees an opaque POST /mcp.

WHAT is recorded, per JSON-RPC `tools/call` POSTed to the MCP mount:

    user_id      the gateway-injected `x-user-id` (the gateway strips every client-supplied
                 authority header before injecting it). No user id -> no sink event.
    tool         `params.name`.
    ok           HTTP status < 400, AND, when the answer is a JSON body (this transport answers
                 POSTs with JSON), no JSON-RPC `error` and no `result.isError: true` for that
                 call's id. An answer streamed as SSE is judged on the status alone.
    client_name  the `clientInfo.name` the session's `initialize` sent, remembered per
                 `mcp-session-id` (bounded LRU); the User-Agent when unknown.
    user_agent   the request's User-Agent.
    auth         "unknown" — the gateway does not say which credential type authenticated.
    at           ISO-8601 UTC.

Every tool call also writes ONE log line, sink or no sink:
`mcp_tool_call user_id=… tool=… ok=… client=…`.

THE SINK is off unless both `MCP_USAGE_URL` and `MCP_USAGE_SECRET` are set. Events go to a bounded
in-memory queue (overflow is dropped and counted in the log) and a daemon thread POSTs them in
batches — every 5 s or at 50 events — signed exactly as meeting-api signs its webhooks
(`Authorization: Bearer <secret>`, `X-Webhook-Timestamp`, `X-Webhook-Signature:
sha256=<hmac(secret, "<ts>." + body)>`). Fire-and-forget: a 3 s timeout, every failure logged and
swallowed. Nothing here can fail, slow or alter the request it observes.

THE MIDDLEWARE only reads: it tees a POST /mcp request body (≤ 1 MB) as the app consumes it and
copies a JSON response body (≤ 1 MB) as it is sent. Every other request — the SSE GET stream, a
DELETE, a non-MCP route — passes straight through untouched.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import queue
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

import httpx

logger = logging.getLogger("vexa_mcp.usage")

URL_ENV = "MCP_USAGE_URL"
SECRET_ENV = "MCP_USAGE_SECRET"

MAX_BODY = 1024 * 1024
FLUSH_EVERY_S = 5.0
FLUSH_AT = 50
QUEUE_MAX = 10_000
TIMEOUT_S = 3.0
SESSIONS_MAX = 10_000


# ── signing — the webhook.v1 scheme meeting-api's `webhooks/delivery.build_headers` uses ────────

def sign_payload(payload: bytes, secret: str, timestamp: str) -> str:
    """`sha256=<hmac_sha256(secret, "<timestamp>." + payload)>`."""
    sig = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def build_headers(secret: str, payload: bytes, timestamp: Optional[str] = None) -> Dict[str, str]:
    ts = timestamp or str(int(time.time()))
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {secret}",
        "X-Webhook-Timestamp": ts,
        "X-Webhook-Signature": sign_payload(payload, secret, ts),
    }


# ── the sink ────────────────────────────────────────────────────────────────────────────────────

class UsageSink:
    """Bounded queue + one daemon sender thread. `submit` never blocks and never raises."""

    def __init__(self, url: str, secret: str, *,
                 transport: Optional[httpx.BaseTransport] = None,
                 flush_every: float = FLUSH_EVERY_S, flush_at: int = FLUSH_AT,
                 queue_max: int = QUEUE_MAX, timeout: float = TIMEOUT_S,
                 start: bool = True) -> None:
        self.url = url
        self._secret = secret
        self._transport = transport
        self._flush_every = flush_every
        self._flush_at = flush_at
        self._timeout = timeout
        self._q: "queue.Queue[dict]" = queue.Queue(maxsize=queue_max)
        self._wake = threading.Event()
        self._send_lock = threading.Lock()
        self.dropped = 0
        self.sent = 0
        self.failed_batches = 0
        self._thread: Optional[threading.Thread] = None
        if start:
            self._thread = threading.Thread(target=self._run, name="mcp-usage-sink", daemon=True)
            self._thread.start()

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> Optional["UsageSink"]:
        env = os.environ if env is None else env
        url = (env.get(URL_ENV) or "").strip()
        secret = (env.get(SECRET_ENV) or "").strip()
        if not url and not secret:
            return None
        if not (url and secret):
            logger.warning("mcp usage sink OFF — %s and %s must both be set (only %s is)",
                           URL_ENV, SECRET_ENV, URL_ENV if url else SECRET_ENV)
            return None
        return cls(url, secret)

    def submit(self, event: dict) -> None:
        try:
            self._q.put_nowait(event)
        except queue.Full:
            self.dropped += 1
            if self.dropped == 1 or self.dropped % 100 == 0:
                logger.warning("mcp usage sink queue full — %d event(s) dropped so far", self.dropped)
            return
        except Exception:  # noqa: BLE001 — never into the request path
            return
        if self._q.qsize() >= self._flush_at:
            self._wake.set()

    def _drain(self, limit: int) -> List[dict]:
        out: List[dict] = []
        while len(out) < limit:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                break
        return out

    def flush(self) -> int:
        """Send everything queued now, in batches of `flush_at`. Returns events delivered."""
        delivered = 0
        with self._send_lock:
            while True:
                batch = self._drain(self._flush_at)
                if not batch:
                    return delivered
                if self._post(batch):
                    delivered += len(batch)

    def _post(self, events: List[dict]) -> bool:
        try:
            body = json.dumps({"events": events}, separators=(",", ":")).encode()
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                r = client.post(self.url, content=body, headers=build_headers(self._secret, body))
            if r.status_code >= 400:
                self.failed_batches += 1
                logger.warning("mcp usage sink answered %d — %d event(s) not recorded",
                               r.status_code, len(events))
                return False
            self.sent += len(events)
            return True
        except Exception as e:  # noqa: BLE001 — fire-and-forget
            self.failed_batches += 1
            logger.warning("mcp usage sink unreachable (%s) — %d event(s) not recorded",
                           type(e).__name__, len(events))
            return False

    def _run(self) -> None:
        while True:
            self._wake.wait(self._flush_every)
            self._wake.clear()
            try:
                self.flush()
            except Exception:  # noqa: BLE001 — the sender thread must outlive any one batch
                logger.exception("mcp usage sink flush failed")


# ── the middleware ──────────────────────────────────────────────────────────────────────────────

class _LRU:
    def __init__(self, maxsize: int) -> None:
        self._d: "OrderedDict[str, str]" = OrderedDict()
        self._max = maxsize
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            v = self._d.get(key)
            if v is not None:
                self._d.move_to_end(key)
            return v

    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._d[key] = value
            self._d.move_to_end(key)
            while len(self._d) > self._max:
                self._d.popitem(last=False)

    def __len__(self) -> int:
        return len(self._d)


def _headers(scope) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k, v in scope.get("headers") or []:
        try:
            out[k.decode("latin-1").lower()] = v.decode("latin-1")
        except Exception:  # noqa: BLE001
            continue
    return out


def _messages(raw: bytes) -> List[dict]:
    try:
        doc = json.loads(raw)
    except Exception:  # noqa: BLE001
        return []
    items = doc if isinstance(doc, list) else [doc]
    return [m for m in items if isinstance(m, dict)]


def _failed(reply: Optional[dict]) -> bool:
    if not isinstance(reply, dict):
        return False
    if reply.get("error") is not None:
        return True
    result = reply.get("result")
    return isinstance(result, dict) and result.get("isError") is True


class UsageMiddleware:
    """Pure ASGI. Observes POST <mount> only; everything else is a straight pass-through."""

    def __init__(self, app, sink: Optional[UsageSink] = None, *, mount: str = "/mcp",
                 sessions_max: int = SESSIONS_MAX) -> None:
        self.app = app
        self.sink = sink
        self.mount = mount.rstrip("/") or "/"
        self.sessions = _LRU(sessions_max)

    async def __call__(self, scope, receive, send):
        if (scope.get("type") != "http" or scope.get("method") != "POST"
                or (scope.get("path") or "").rstrip("/") != self.mount):
            await self.app(scope, receive, send)
            return

        req = bytearray()
        too_big = False

        async def tee_receive():
            nonlocal too_big
            message = await receive()
            if message.get("type") == "http.request" and not too_big:
                chunk = message.get("body") or b""
                if len(req) + len(chunk) > MAX_BODY:
                    too_big = True
                    req.clear()
                else:
                    req.extend(chunk)
            return message

        status = 0
        resp_headers: Dict[str, str] = {}
        resp = bytearray()
        resp_json = False

        async def tee_send(message):
            nonlocal status, resp_json
            try:
                if message.get("type") == "http.response.start":
                    status = int(message.get("status") or 0)
                    for k, v in message.get("headers") or []:
                        resp_headers[k.decode("latin-1").lower()] = v.decode("latin-1")
                    resp_json = resp_headers.get("content-type", "").startswith("application/json")
                elif message.get("type") == "http.response.body" and resp_json:
                    chunk = message.get("body") or b""
                    if len(resp) + len(chunk) <= MAX_BODY:
                        resp.extend(chunk)
            except Exception:  # noqa: BLE001 — observing must never break the response
                pass
            await send(message)

        try:
            await self.app(scope, tee_receive, tee_send)
        finally:
            if not too_big and req:
                try:
                    self._observe(scope, bytes(req), status, resp_headers,
                                  bytes(resp) if resp_json else b"")
                except Exception:  # noqa: BLE001
                    logger.debug("mcp usage observation failed", exc_info=True)

    def _observe(self, scope, raw: bytes, status: int, resp_headers: Dict[str, str],
                 resp_body: bytes) -> None:
        msgs = _messages(raw)
        if not msgs:
            return
        headers = _headers(scope)
        for m in msgs:
            if m.get("method") == "initialize":
                sid = resp_headers.get("mcp-session-id")
                info = (m.get("params") or {}).get("clientInfo") or {}
                name = info.get("name") if isinstance(info, dict) else None
                if sid and isinstance(name, str) and name.strip():
                    self.sessions.set(sid, name.strip()[:200])
        calls = [m for m in msgs if m.get("method") == "tools/call"]
        if not calls:
            return
        replies: Dict[Any, dict] = {}
        for r in _messages(resp_body) if resp_body else []:
            if "id" in r:
                replies[json.dumps(r.get("id"))] = r
        user_id = (headers.get("x-user-id") or "").strip()
        user_agent = headers.get("user-agent") or ""
        sid = headers.get("mcp-session-id") or ""
        client = (self.sessions.get(sid) if sid else None) or user_agent or "unknown"
        for m in calls:
            params = m.get("params") if isinstance(m.get("params"), dict) else {}
            tool = str(params.get("name") or "")
            ok = 0 < status < 400 and not _failed(replies.get(json.dumps(m.get("id"))))
            logger.info("mcp_tool_call user_id=%s tool=%s ok=%s client=%s",
                        user_id or "-", tool or "-", str(ok).lower(), json.dumps(client))
            if self.sink is None or not user_id or not tool:
                continue
            self.sink.submit({
                "user_id": user_id,
                "tool": tool,
                "ok": ok,
                "client_name": client,
                "user_agent": user_agent,
                "auth": "unknown",
                "at": datetime.now(timezone.utc).isoformat(),
            })
