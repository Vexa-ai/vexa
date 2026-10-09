"""multiplex.py — the ``/ws`` control loop and its redis fan-in.

Carved verbatim from ``services/api-gateway/main.py`` ``websocket_multiplex`` and moved out of
``app.py`` unchanged: it shares nothing with the REST proxy but the ``Authorizer`` port and the
delegation rule (a worker's delegation token opens no socket). ``create_app`` mounts it at ``/ws``;
the conformance ws-harness drives it directly through the package front door
(``gateway.run_multiplex``).
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Dict, List, Set, Tuple

from fastapi import WebSocket, WebSocketDisconnect

from .delegation import is_delegated
from .obs import log_event, set_user_id
from .ports import Authorizer, AuthUnavailable, RedisBus


async def run_multiplex(ws: WebSocket, authorizer: Authorizer, redis: RedisBus) -> None:
    """The ``/ws`` control loop + fan-in, carved verbatim from main.websocket_multiplex.

    PUBLIC (P2 follow-up): the conformance ws-harness drives this directly to exercise the SHIPPED
    multiplex against its fakes — exposed on the front door (``gateway.run_multiplex``) so the
    harness no longer reaches for a private (the P1-flagged smell).

    guard (PRE-accept, opt-in) → accept → authenticate (missing key → error + close 4401) →
      loop over client frames:
      subscribe   → authorize, register a redis fan-in per meeting, ack ``subscribed``;
      unsubscribe → cancel the fan-in task(s), ack ``unsubscribed`` (stops forwarding);
      ping        → ``pong``;
      otherwise   → an ``error`` frame (invalid_json / unknown_action / invalid_*_payload).
    Each subscription fans in ``tc:meeting:{id}:mutable`` / ``bm:meeting:{id}:status`` /
    ``va:meeting:{id}:chat`` and forwards every raw payload to the socket (main.py:2204).
    """
    # --- optional WS guard hook (GUARD_WS_ENABLED, default false) ---
    # HTTP SecurityMiddleware does not intercept /ws (Starlette middleware is HTTP-only).
    # When the toggle is on, resolve the client IP via the same trusted-proxies XFF logic
    # as guard's HTTP path and deny over-limit/banned IPs at connect. Opt-in: the default
    # (false) leaves the WS path unchanged so the conformance harness observes zero change.
    #
    # PRE-ACCEPT: the full guard check (whitelist/blacklist/ban/rate-limit) runs BEFORE
    # ``ws.accept()`` so a banned IP never gets a WebSocket upgrade. On denial, close with
    # 4401 BEFORE accept — Starlette forwards the pre-accept ``websocket.close`` unchanged
    # (its state machine accepts ``websocket.close`` while CONNECTING); uvicorn (0.51 here)
    # turns that into an HTTP 403 to the upgrade request (no upgrade, no frames). A data
    # frame (send_text) cannot be sent before accept, so the rejection is the close alone —
    # the client sees the 403, not an ip_blocked JSON frame.
    from .ratelimit import env_truthy

    if env_truthy(os.getenv("GUARD_WS_ENABLED")):
        from .edge_guard import ws_guard_check

        if not ws_guard_check(ws):
            await ws.close(code=4401)  # pre-accept reject → HTTP 403 to the upgrade
            return

    await ws.accept()

    api_key = ws.headers.get("x-api-key") or ws.query_params.get("api_key")
    if not api_key:
        try:
            await ws.send_text(json.dumps({"type": "error", "error": "missing_api_key"}))
        finally:
            await ws.close(code=4401)  # Unauthorized
        return

    # Connect-time identity resolve (Track G — meeting-status-ws §C.2). Today connect only checked
    # the key was PRESENT and resolved user_id per-subscribe; now we resolve the key to a user up
    # front (the SAME resolver /auth/me + the proxy use — ports.py resolve / app.py:96-99,119-125)
    # so we can auto-subscribe the socket to its USER-SCOPED channel. Fail-closed like the proxy:
    # a present-but-invalid key → invalid_api_key + close 4401, not a silently half-open socket.
    try:
        user_data = await authorizer.resolve(api_key)
    except AuthUnavailable as e:
        # #495: resolve() now RAISES when the validation hop is unreachable/faulted. On the REST
        # surface that becomes a 503; on this already-accepted socket the truthful equivalent is a
        # typed error frame + a distinct retryable close code (4503 ≈ HTTP 503), NOT 4401 (which
        # asserts the key is bad) and NOT an uncaught raise (which drops the socket 1006/1011 with
        # no signal). A valid key must not be told it is invalid because our auth path is down.
        log_event("auth_infra_unavailable", audience="system", level="error", span="ws",
                  fields={"reason": type(e).__name__, "detail": str(e)})
        try:
            await ws.send_text(json.dumps({"type": "error", "error": "auth_unavailable"}))
        finally:
            await ws.close(code=4503)  # retry later — auth infrastructure unavailable
        return
    # A worker's delegation token is an MCP credential (`delegation.py`): it opens no
    # socket here, so it is answered exactly like a key this edge does not accept.
    if not user_data or is_delegated(api_key, user_data):
        try:
            await ws.send_text(json.dumps({"type": "error", "error": "invalid_api_key"}))
        finally:
            await ws.close(code=4401)  # Unauthorized
        return
    user_id = user_data["user_id"]
    set_user_id(user_id)

    sub_tasks: Dict[Tuple, asyncio.Task] = {}
    subscribed_meetings: Set[Tuple] = set()

    async def fan_in(channels: List[str]):
        pubsub = redis.pubsub()
        await pubsub.subscribe(*channels)
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                data = message.get("data")
                try:
                    await ws.send_text(data)  # forward the raw redis payload (main.py:2204)
                except Exception:
                    break
        finally:
            try:
                await pubsub.unsubscribe(*channels)
                await pubsub.close()
            except Exception:
                pass

    async def subscribe_meeting(platform: str, native_id: str, user_id, meeting_id):
        key = (platform, native_id, user_id)
        if key in subscribed_meetings:
            return
        subscribed_meetings.add(key)
        channels = [
            f"tc:meeting:{meeting_id}:mutable",
            f"bm:meeting:{meeting_id}:status",
            f"va:meeting:{meeting_id}:chat",
        ]
        sub_tasks[key] = asyncio.create_task(fan_in(channels))

    async def unsubscribe_meeting(platform: str, native_id: str, user_id):
        key = (platform, native_id, user_id)
        task = sub_tasks.pop(key, None)
        if task:
            task.cancel()
        subscribed_meetings.discard(key)

    # Auto-subscribe the authed socket to its USER scope (Track G — meeting-status-ws §C.2). The
    # user-scoped redis channel `u:{user_id}:meetings` carries every meeting.status frame for this
    # user (the publisher mirrors each bm:meeting:{id}:status onto it — §C.3). No client `subscribe`
    # frame is needed: the identity is resolved at connect. This reuses the SAME verbatim `fan_in`
    # path as the per-meeting channels — the gateway is a thin raw forwarder for the user channel
    # exactly as it is for tc:/bm:/va:. Per-meeting subscriptions below are unchanged.
    #
    # AND to every workspace this identity is a member of — `w:{workspace_id}:meetings`, carrying the
    # status frames of meetings BOUND to that workspace. A bot requested inside a workspace makes the
    # workspace's meeting, so its transitions belong to every member's list, not only the requester's.
    # The membership list comes from the SAME connect-time identity resolve as `user_id` above
    # (`user_data["workspaces"]`, which identity builds from `users.data.memberships[]`), so a client
    # cannot name a workspace it does not belong to — it sends no subscribe frame at all. Membership
    # is therefore evaluated per CONNECTION: a member added mid-session picks the channel up on their
    # next connect, which is the same freshness the rest of this socket's identity already has.
    user_channel = f"u:{user_id}:meetings"
    member_channels = [
        f"w:{str(w).strip()}:meetings"
        for w in (user_data.get("workspaces") or [])
        if str(w).strip()
    ]
    user_sub_task = asyncio.create_task(fan_in([user_channel, *member_channels]))

    try:
        while True:
            try:
                raw = await ws.receive_text()
            except WebSocketDisconnect:
                break

            try:
                msg = json.loads(raw)
            except Exception:
                await ws.send_text(json.dumps({"type": "error", "error": "invalid_json"}))
                continue
            # Syntactically-valid but NON-OBJECT JSON ([1,2,3], 42, "x", null): guard before `.get()`,
            # else AttributeError escapes run_multiplex and KILLS the socket — a trivial public-edge DoS.
            if not isinstance(msg, dict):
                await ws.send_text(json.dumps({"type": "error", "error": "invalid_json"}))
                continue

            action = msg.get("action")
            if action == "subscribe":
                meetings = msg.get("meetings", None)
                if not isinstance(meetings, list):
                    await ws.send_text(json.dumps({
                        "type": "error", "error": "invalid_subscribe_payload",
                        "details": "'meetings' must be a non-empty list"}))
                    continue
                if len(meetings) == 0:
                    await ws.send_text(json.dumps({
                        "type": "error", "error": "invalid_subscribe_payload",
                        "details": "'meetings' list cannot be empty"}))
                    continue
                payload_meetings = []
                for m in meetings:
                    if isinstance(m, dict):
                        plat = str(m.get("platform", "")).strip()
                        nid = str(m.get("native_id", "")).strip()
                        if plat and nid:
                            payload_meetings.append({"platform": plat, "native_meeting_id": nid})
                if not payload_meetings:
                    await ws.send_text(json.dumps({
                        "type": "error", "error": "invalid_subscribe_payload",
                        "details": "no valid meeting objects"}))
                    continue

                # The downstream authorize hop must never crash the socket: a RAISE → authorization_call_failed
                # frame + continue; a non-200 (errors carried, nothing authorized) → authorization_service_error
                # frame, NOT a misleading empty `subscribed` ack that hides the auth backend being down.
                try:
                    result = await authorizer.authorize_subscribe(api_key, payload_meetings)
                except Exception as e:  # noqa: BLE001 — surface as a protocol error, keep the socket alive
                    await ws.send_text(json.dumps({
                        "type": "error", "error": "authorization_call_failed", "details": str(e)}))
                    continue
                authorized = result.get("authorized") or []
                auth_errors = result.get("errors") or []
                if not authorized and auth_errors:
                    first = str(auth_errors[0])
                    code = ("authorization_call_failed"
                            if first.startswith("authorization_call_failed")
                            else "authorization_service_error")
                    await ws.send_text(json.dumps({"type": "error", "error": code, "details": first}))
                    continue
                subscribed: List[Dict[str, str]] = []
                for item in authorized:
                    plat = item.get("platform"); nid = item.get("native_id")
                    user_id = item.get("user_id"); meeting_id = item.get("meeting_id")
                    if plat and nid and user_id and meeting_id:
                        await subscribe_meeting(plat, nid, user_id, meeting_id)
                        subscribed.append({"platform": plat, "native_id": nid})
                await ws.send_text(json.dumps({"type": "subscribed", "meetings": subscribed}))

            elif action == "unsubscribe":
                meetings = msg.get("meetings", None)
                if not isinstance(meetings, list):
                    await ws.send_text(json.dumps({
                        "type": "error", "error": "invalid_unsubscribe_payload",
                        "details": "'meetings' must be a list"}))
                    continue
                unsubscribed: List[Dict[str, str]] = []
                errors: List[str] = []
                for idx, m in enumerate(meetings):
                    if not isinstance(m, dict):
                        errors.append(f"meetings[{idx}] must be an object")
                        continue
                    plat = str(m.get("platform", "")).strip()
                    nid = str(m.get("native_id", "")).strip()
                    if not plat or not nid:
                        errors.append(f"meetings[{idx}] missing 'platform' or 'native_id'")
                        continue
                    matching_key = None
                    for key in subscribed_meetings:
                        if key[0] == plat and key[1] == nid:
                            matching_key = key
                            break
                    if matching_key:
                        await unsubscribe_meeting(plat, nid, matching_key[2])
                        unsubscribed.append({"platform": plat, "native_id": nid})
                    else:
                        errors.append(f"meetings[{idx}] not currently subscribed")
                if errors and not unsubscribed:
                    await ws.send_text(json.dumps({
                        "type": "error", "error": "invalid_unsubscribe_payload", "details": errors}))
                    continue
                await ws.send_text(json.dumps({"type": "unsubscribed", "meetings": unsubscribed}))

            elif action == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
            else:
                await ws.send_text(json.dumps({"type": "error", "error": "unknown_action"}))
    except WebSocketDisconnect:
        pass
    finally:
        user_sub_task.cancel()  # Track G — tear down the user-scope fan-in on disconnect.
        for task in sub_tasks.values():
            task.cancel()


# Backward-compatible private alias (kept so any existing internal reference still resolves; the
# public name ``run_multiplex`` is the front door the conformance harness now imports).
_run_multiplex = run_multiplex
