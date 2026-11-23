"""Incoming webhooks (credential-broker.v1 `/api/hooks`, `/hooks/{hook_id}`, `/api/internal/sensor/hook-events`).

A hook is a person's, like a connection: someone (Stripe, Vexa's meeting service) posts signed events
to `/hooks/<hook_id>`, the broker checks the sender's signature with the secret the person gave it,
keeps the verified event, and the person's sensor reads it later (role `sensors`, the same seam as
calendar changes). The broker holds no credential of the reader's and makes no outbound call here.

* `POST /api/hooks` (human, agent, sensors) creates one: an unguessable id (`hk_` + 32 hex) and its
  signing secret, stored at `hooks/<id>` sealed with the person as owner — never in metadata.sqlite.
  A `vexa` hook without a secret gets a generated one, answered once and never again. At most
  `MAX_HOOKS` live hooks per person.
* `POST /hooks/{hook_id}` is PUBLIC: no assertion (`app.HOOK_PATH`). The signature is the only
  credential; a bad or stale one is a fixed 401, never an echo. A verified event is committed before
  the 200, deduplicated on (hook, event id), and pruned to `KEEP_DAYS` days and `KEEP_EVENTS` rows.
* `POST /api/hooks/{hook_id}/revoke` (the hook's person) ends it: the secret and every kept event go.
* `POST /api/internal/sensor/hook-events` (sensors, signed for the owner) pages the verified events.

Nothing logged or audited carries a secret, a signature or a body: refusals are typed log lines, and
accepted events are audited as an outcome only.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import uuid
from contextlib import closing

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .broker import Broker
from .models import HookCreateBody, HookEventsBody
from .obs import log_event

#: The largest body a sender may post (the edge allows the same).
MAX_BODY = 256 * 1024
#: A signature's timestamp may differ from the broker's clock by at most this, either way.
TOLERANCE = 300
KEEP_DAYS = 14
KEEP_EVENTS = 10000
MAX_HOOKS = 20
PAGE = 100
REFUSED = "Signature refused"
NO_HOOK = "No such hook"


def record_path(hook_id: str) -> str:
    return "hooks/" + hook_id


def hook_path(hook_id: str) -> str:
    return "/hooks/" + hook_id


def _refuse(scheme: str, kind: str):
    log_event("hook_refused", level="warning", fields={"scheme": scheme, "kind": kind, "route": "/hooks/{hook}"})
    raise HTTPException(401, REFUSED)


def _fresh(ts: str, scheme: str) -> None:
    if abs(time.time() - int(ts)) > TOLERANCE:
        _refuse(scheme, "stale")


def verify_stripe(secret: str, raw: bytes, header: str) -> None:
    """Stripe-Signature `t=<unix>,v1=<hex>[,v1=<hex>…]`: any v1 is HMAC-SHA256(secret, "<t>." + body)
    (several during a secret rotation)."""
    if not header:
        _refuse("stripe", "missing")
    t, sigs = "", []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t" and not t:
            t = value
        elif key == "v1":
            sigs.append(value)
    if not t.isdigit() or not sigs:
        _refuse("stripe", "malformed")
    expected = hmac.new(secret.encode(), f"{t}.".encode() + raw, hashlib.sha256).hexdigest()
    # every candidate compared, so the time taken does not say which one matched
    if not any([hmac.compare_digest(expected.encode(), s.encode()) for s in sigs]):
        _refuse("stripe", "mismatch")
    _fresh(t, "stripe")


def verify_vexa(secret: str, raw: bytes, signature: str, ts: str) -> None:
    """webhook.v1 (meeting-api `sign_payload`): X-Webhook-Signature `sha256=<hex>` over
    "<X-Webhook-Timestamp>." + body. The legacy `Authorization: Bearer <secret>` is never read."""
    if not signature or not ts:
        _refuse("vexa", "missing")
    if not signature.startswith("sha256=") or not ts.isdigit():
        _refuse("vexa", "malformed")
    expected = "sha256=" + hmac.new(secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected.encode(), signature.encode()):
        _refuse("vexa", "mismatch")
    _fresh(ts, "vexa")


EVENT_ID_KEY = {"stripe": "id", "vexa": "event_id"}


def build(b: Broker) -> APIRouter:
    router = APIRouter()

    @router.post("/api/hooks")
    def create(request: Request, body: HookCreateBody):
        who = b.identity(request, {"human", "agent", "sensors"})
        actor = str(who["actor"])
        hook_id = "hk_" + secrets.token_hex(16)
        generated = body.secret is None
        secret = secrets.token_hex(32) if generated else body.secret
        with b.lock:
            live = b.sql("SELECT COUNT(*) AS n FROM hooks WHERE actor=? AND revoked=0", (actor,), one=True)["n"]
            if live >= MAX_HOOKS:
                raise HTTPException(409, "Too many hooks")
            b.audit(who, hook_id, "hooks.create", "requested")
            saved = b.put(record_path(hook_id), {"value": secret, "scheme": body.scheme}, owner=actor)
            b.sql("INSERT INTO hooks(id, actor, scheme, label, created, revoked) VALUES (?,?,?,?,?,0)",
                  (hook_id, actor, body.scheme, body.label, time.time()))
            b.audit(who, hook_id, "hooks.create", "stored", receipt_id=saved.receipt, version=saved.version)
        answer = {"hook_id": hook_id, "path": hook_path(hook_id), "scheme": body.scheme}
        if generated:
            answer["secret"] = secret
        return answer

    @router.post("/api/hooks/{hook_id}/revoke")
    def revoke(request: Request, hook_id: str):
        """Revoking only narrows, so a worker with nobody in the loop may do it (as sensor grants)."""
        who = b.identity(request, {"human", "agent", "sensors"}, unwatched_ok=True)
        with b.lock:
            row = b.sql("SELECT * FROM hooks WHERE id=? AND actor=?", (hook_id, str(who["actor"])), one=True)
            if not row:
                raise HTTPException(404, NO_HOOK)
            b.audit(who, hook_id, "hooks.revoke", "requested")
            if not row["revoked"]:
                b.sql("UPDATE hooks SET revoked=? WHERE id=?", (time.time(), hook_id))
            b.remove(record_path(hook_id))
            b.sql("DELETE FROM hook_events WHERE hook=?", (hook_id,))
            b.audit(who, hook_id, "hooks.revoke", "removed")
        return {"hook_id": hook_id, "path": hook_path(hook_id), "scheme": row["scheme"]}

    def accept(hook_id: str, raw: bytes, headers) -> dict:
        row = b.sql("SELECT * FROM hooks WHERE id=? AND revoked=0", (hook_id,), one=True)
        if not row:
            raise HTTPException(404, NO_HOOK)
        record = b.get(record_path(hook_id), owner=row["actor"])
        secret = (record.data.get("value") if record else "") or ""
        if not secret:
            b.fault("store", "hook_secret_missing", route="/hooks/{hook}")
            raise HTTPException(404, NO_HOOK)
        scheme = row["scheme"]
        if scheme == "stripe":
            verify_stripe(secret, raw, headers.get("stripe-signature", ""))
        else:
            verify_vexa(secret, raw, headers.get("x-webhook-signature", ""), headers.get("x-webhook-timestamp", ""))
        try:
            text = raw.decode("utf-8")
            event = json.loads(text)
        except ValueError:
            event = None
        event_id = event.get(EVENT_ID_KEY[scheme]) if isinstance(event, dict) else None
        if not isinstance(event_id, str) or not event_id or len(event_id) > 255:
            log_event("hook_refused", level="warning", fields={"scheme": scheme, "kind": "body", "route": "/hooks/{hook}"})
            raise HTTPException(400, "Body is not a JSON event with an id")
        now = time.time()
        with b.lock, closing(b.db()) as c, c:
            stored = c.execute("INSERT OR IGNORE INTO hook_events(hook, event_id, received, body) VALUES (?,?,?,?)",
                               (hook_id, event_id, now, text)).rowcount
            c.execute("DELETE FROM hook_events WHERE received < ?", (now - KEEP_DAYS * 86400,))
            c.execute("DELETE FROM hook_events WHERE hook=? AND seq <= (SELECT seq FROM hook_events WHERE hook=?"
                      " ORDER BY seq DESC LIMIT 1 OFFSET ?)", (hook_id, hook_id, KEEP_EVENTS))
        # committed above, before the answer; the audit row carries the outcome, never the body
        b.audit({"actor": row["actor"], "session": "", "role": "public"}, hook_id, "hooks.receive",
                "stored" if stored else "duplicate")
        return {"received": True}

    @router.post("/hooks/{hook_id}")
    async def receive(request: Request, hook_id: str):
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_BODY:
            raise HTTPException(413, "Body too large")
        raw, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_BODY:
                raise HTTPException(413, "Body too large")
            raw.append(chunk)
        return await run_in_threadpool(accept, hook_id, b"".join(raw), request.headers)

    @router.post("/api/internal/sensor/hook-events")
    def hook_events(request: Request, body: HookEventsBody):
        who = b.identity(request, {"sensors"})
        if str(who["actor"]) != body.owner:
            raise HTTPException(403, "A sensor reads only the person its request is signed for")
        row = b.sql("SELECT * FROM hooks WHERE id=? AND actor=? AND revoked=0", (body.hook_id, body.owner), one=True)
        if not row:
            raise HTTPException(404, NO_HOOK)
        cursor = body.cursor or 0
        rows = b.sql("SELECT seq, event_id, received, body FROM hook_events WHERE hook=? AND seq>? ORDER BY seq LIMIT ?",
                     (body.hook_id, cursor, PAGE), rows=True)
        items = [{"seq": r["seq"], "event_id": r["event_id"], "received": r["received"], "event": json.loads(r["body"])}
                 for r in rows]
        b.audit(who, body.hook_id, "hooks.events", "success", operation=uuid.uuid4().hex, receipt=str(len(items)))
        return {"hook_id": body.hook_id, "scheme": row["scheme"], "items": items,
                "cursor": items[-1]["seq"] if items else cursor}

    return router
