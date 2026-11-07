"""The bot-account routes — public (through the gateway, scope ``bot``) and internal (bots network).

Public, owner from ``x-user-id`` (the gateway's signed identity, rebuilt by ``IdentityGuard``):

    GET    /bot-accounts                                         list + usable/mine + company_bot_use
    POST   /bot-accounts/sessions                                open a browser session (201)
    GET    /bot-accounts/sessions/{session_id}                   its status
    DELETE /bot-accounts/sessions/{session_id}                   close it
    GET    /bot-accounts/sessions/{session_id}/view/{path:path}  the workload's noVNC client files
    WS     /bot-accounts/sessions/{session_id}/vnc               binary relay to its websockify
    POST   /bot-accounts/{account_id}/verify                     start a verify check (202)
    DELETE /bot-accounts/{account_id}                            delete an account

Only the person who opened a session may read, view or close it; anybody else gets 404 (WS close
4404), the same answer as a session that does not exist.

Internal (``include_in_schema=False``; the gateway routes none of them, and ``route_scopes`` refuses
any that arrives carrying a signed identity):

    PUT  /internal/bot-accounts/sessions/{session_id}/profile   Bearer <session token>, WritebackBody
    POST /internal/bot-accounts/checks/{check_id}               Bearer <check token>, {logged_in, detail}
    GET  /internal/browser-session-check/{check_id}             Bearer <check token> → WritebackBody
    GET  /internal/browser-session/{session_uid}                Bearer <MeetingToken> → WritebackBody
"""
from __future__ import annotations

import asyncio
import base64

import anyio
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, Response

from ..meeting_token import InvalidMeetingToken, admit_session
from ..obs import log_event
from ..regime import require_person
from ..session_profile.profile import MAX_BODY_BYTES, InvalidSessionProfile, parse_profile_upload
from . import sessions as S
from .relay import VNC_PORT, view_path_refusal
from .service import BotAccountRefused, BotAccounts, userdata_key

_TERMINAL = ("completed", "failed")


def _user(x_user_id: Optional[str]) -> int:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing user identity")
    try:
        return int(x_user_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid user identity")


def _bearer(authorization: Optional[str]) -> str:
    if not authorization:
        return ""
    scheme, _, value = authorization.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def _http(e: BotAccountRefused) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.detail)


def _files_body(files: list) -> dict:
    return {"files": [{"path": p, "data": base64.b64encode(d).decode("ascii")} for p, d in files]}


async def _read_capped(request: Request, cap: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap:
        raise HTTPException(status_code=413, detail=f"the profile body is larger than {cap} bytes")
    buf = bytearray()
    async for chunk in request.stream():
        buf.extend(chunk)
        if len(buf) > cap:
            raise HTTPException(status_code=413, detail=f"the profile body is larger than {cap} bytes")
    return bytes(buf)


def build_router(ba: BotAccounts, *, meeting_repo: Any = None, token_secret: Optional[str] = None) -> APIRouter:
    router = APIRouter()

    async def _stop_later(workload_id: Optional[str], why: str) -> None:
        # After the answer is sent, so the workload that called reads its 200 before it is stopped.
        await asyncio.sleep(0)
        await ba._stop(workload_id, why=why)

    # ── public ────────────────────────────────────────────────────────────────────────────────
    @router.get("/bot-accounts")
    async def list_bot_accounts(x_user_id: Optional[str] = Header(default=None)):
        user_id = _user(x_user_id)
        try:
            return await ba.list_for(user_id)
        except BotAccountRefused as e:
            raise _http(e)

    @router.post("/bot-accounts/sessions", status_code=201, dependencies=[Depends(require_person)])
    async def open_browser_session(request: Request, x_user_id: Optional[str] = Header(default=None)):
        user_id = _user(x_user_id)
        try:
            body = await request.json()
        except Exception:
            body = None
        if not isinstance(body, dict):
            raise HTTPException(status_code=422, detail="body must be an object")
        try:
            out = await ba.open_session(user_id, body)
        except BotAccountRefused as e:
            raise _http(e)
        return JSONResponse(status_code=201, content=out)

    @router.get("/bot-accounts/sessions/{session_id}")
    async def get_browser_session(session_id: str, x_user_id: Optional[str] = Header(default=None)):
        user_id = _user(x_user_id)
        try:
            return ba.session_view(await ba.owned_session(user_id, session_id))
        except BotAccountRefused as e:
            raise _http(e)

    @router.delete("/bot-accounts/sessions/{session_id}")
    async def close_browser_session(session_id: str, x_user_id: Optional[str] = Header(default=None)):
        user_id = _user(x_user_id)
        try:
            return await ba.close_session(user_id, session_id)
        except BotAccountRefused as e:
            raise _http(e)

    @router.get("/bot-accounts/sessions/{session_id}/view/{path:path}")
    async def view_browser_session(session_id: str, path: str,
                                   x_user_id: Optional[str] = Header(default=None)):
        user_id = _user(x_user_id)
        try:
            rec = await ba.owned_session(user_id, session_id)
        except BotAccountRefused as e:
            raise _http(e)
        if rec.get("status") not in S.OPEN_STATUSES:
            raise HTTPException(status_code=404, detail="no such browser session")
        why = view_path_refusal(path)
        if why is not None:
            raise HTTPException(status_code=404, detail=f"the path {why}")
        try:
            status, ctype, body = await ba.upstream.fetch(f"http://{rec['host']}:{VNC_PORT}/{path}")
        except Exception:  # noqa: BLE001 — the workload not answering yet
            raise HTTPException(status_code=503, detail="the browser session is not answering yet")
        if status == 404:
            raise HTTPException(status_code=404, detail="no such file")
        if status != 200:
            raise HTTPException(status_code=502, detail="the browser session answered unusably")
        return Response(content=body, media_type=ctype,
                        headers={"cache-control": "no-store", "x-content-type-options": "nosniff"})

    @router.websocket("/bot-accounts/sessions/{session_id}/vnc")
    async def relay_browser_session(websocket: WebSocket, session_id: str):
        try:
            user_id = int(websocket.headers.get("x-user-id") or "")
        except ValueError:
            await websocket.close(code=4401)
            return
        try:
            rec = await ba.owned_session(user_id, session_id)
        except BotAccountRefused:
            await websocket.close(code=4404)
            return
        if rec.get("status") not in S.OPEN_STATUSES:
            await websocket.close(code=4404)
            return
        offered = list(websocket.scope.get("subprotocols") or [])
        want = ["binary"] if "binary" in offered else []
        try:
            upstream, agreed = await ba.upstream.connect(
                f"ws://{rec['host']}:{VNC_PORT}/websockify", subprotocols=want)
        except Exception as e:  # noqa: BLE001 — the workload not answering yet
            log_event("browser_session_relay_failed", audience="system", level="warning",
                      span="bot_accounts", fields={"session_id": session_id, "error": type(e).__name__})
            await websocket.close(code=1011)
            return
        await websocket.accept(subprotocol=agreed if agreed in offered else None)

        async def downstream_to_upstream():
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    return
                data = msg.get("bytes")
                if data is None and msg.get("text") is not None:
                    data = msg["text"]
                if data is not None:
                    await upstream.send(data)

        async def upstream_to_downstream():
            while True:
                data = await upstream.recv()
                if isinstance(data, (bytes, bytearray)):
                    await websocket.send_bytes(bytes(data))
                else:
                    await websocket.send_text(str(data))

        # Either direction ending (the viewer leaving, the workload closing) ends both.
        try:
            async with anyio.create_task_group() as tg:
                async def pump(direction):
                    try:
                        await direction()
                    except Exception:  # noqa: BLE001 — a closed side is the end of the relay
                        pass
                    tg.cancel_scope.cancel()

                tg.start_soon(pump, downstream_to_upstream)
                tg.start_soon(pump, upstream_to_downstream)
        finally:
            with anyio.CancelScope(shield=True):
                await upstream.close()
                try:
                    await websocket.close()
                except Exception:  # noqa: BLE001 — already closed by the client
                    pass

    @router.post("/bot-accounts/{account_id}/verify", status_code=202)
    async def verify_bot_account(account_id: str, x_user_id: Optional[str] = Header(default=None)):
        user_id = _user(x_user_id)
        try:
            out = await ba.verify(user_id, account_id)
        except BotAccountRefused as e:
            raise _http(e)
        return JSONResponse(status_code=202, content=out)

    @router.delete("/bot-accounts/{account_id}", dependencies=[Depends(require_person)])
    async def delete_bot_account(account_id: str, x_user_id: Optional[str] = Header(default=None)):
        user_id = _user(x_user_id)
        try:
            return await ba.delete_account(user_id, account_id)
        except BotAccountRefused as e:
            raise _http(e)

    # ── internal ──────────────────────────────────────────────────────────────────────────────
    @router.put("/internal/bot-accounts/sessions/{session_id}/profile", include_in_schema=False)
    async def save_browser_session_profile(session_id: str, request: Request, background: BackgroundTasks,
                                           authorization: Optional[str] = Header(default=None)):
        token = _bearer(authorization)
        if not token:
            raise HTTPException(status_code=401, detail="a profile save needs the browser session's token")
        raw = await _read_capped(request, MAX_BODY_BYTES)
        try:
            files = parse_profile_upload(raw)
        except InvalidSessionProfile as e:
            log_event("bot_account_save_refused", audience="system", level="warning",
                      span="bot_accounts", fields={"session_id": session_id, "reason": str(e)})
            raise HTTPException(status_code=422, detail=str(e))
        try:
            out, workload_id = await ba.save_profile(session_id, token, files)
        except BotAccountRefused as e:
            raise _http(e)
        background.add_task(_stop_later, workload_id, "saved")
        return out

    @router.post("/internal/bot-accounts/checks/{check_id}", include_in_schema=False)
    async def bot_account_check_result(check_id: str, request: Request, background: BackgroundTasks,
                                       authorization: Optional[str] = Header(default=None)):
        token = _bearer(authorization)
        if not token:
            raise HTTPException(status_code=401, detail="a check result needs the check's token")
        try:
            body = await request.json()
        except Exception:
            body = None
        try:
            out, workload_id = await ba.check_result(check_id, token, body)
        except BotAccountRefused as e:
            raise _http(e)
        background.add_task(_stop_later, workload_id, "checked")
        return out

    @router.get("/internal/browser-session-check/{check_id}", include_in_schema=False)
    async def bot_account_check_profile(check_id: str, authorization: Optional[str] = Header(default=None)):
        token = _bearer(authorization)
        if not token:
            raise HTTPException(status_code=401, detail="a check restore needs the check's token")
        try:
            return _files_body(await ba.check_profile(check_id, token))
        except BotAccountRefused as e:
            raise _http(e)

    @router.get("/internal/browser-session/{session_uid}", include_in_schema=False)
    async def restore_browser_session(session_uid: str, authorization: Optional[str] = Header(default=None)):
        token = _bearer(authorization)
        if not token:
            raise HTTPException(status_code=401, detail="a session restore needs the bot's MeetingToken")
        try:
            claims = admit_session(token, session_uid=session_uid, secret=token_secret)
            meeting_id = int(claims["meeting_id"])
        except (InvalidMeetingToken, KeyError, TypeError, ValueError) as e:
            raise HTTPException(status_code=401, detail=f"invalid session token: {e}")
        if not ba.configured:
            raise HTTPException(status_code=409, detail="bot accounts are not configured on this deployment")
        meeting = await meeting_repo.get_meeting(meeting_id) if meeting_repo is not None else None
        account_id = ((meeting or {}).get("data") or {}).get("bot_account_id")
        if not account_id:
            raise HTTPException(status_code=404, detail="this session was not spawned with a bot account")
        latest = await meeting_repo.latest_auth_session(userdata_key(account_id))
        if (latest is None or latest.get("session_uid") != session_uid
                or int(latest.get("meeting_id") or 0) != meeting_id
                or latest.get("status") in _TERMINAL):
            log_event("bot_account_restore_refused", audience="system", level="warning",
                      span="bot_accounts", meeting_id=str(meeting_id),
                      fields={"reason": "not the live bot of this account"})
            raise HTTPException(status_code=403, detail="this session is not the live bot of its bot account")
        try:
            files = await ba.restore_files(account_id)
        except BotAccountRefused as e:
            if e.status == 503:
                raise HTTPException(status_code=502, detail="the profile store did not answer")
            raise _http(e)
        log_event("bot_account_restored", audience="system", span="bot_accounts",
                  meeting_id=str(meeting_id), fields={"account_id": account_id, "files": len(files)})
        return _files_body(files)

    return router
