"""The bot-account route (credential-broker.v1 `/api/internal/bot-account`, role `bots`, ADR-0049).

meeting-api keeps saved signed-in browser profiles here — a bot account is a session-profile.v1 file
set plus metadata, owned by one person (`user:<id>`) or the whole deployment (`company`) — and reads
them back to restore a bot's browser. Only the `bots` role may call it, and it carries no
X-Vexa-Identity: meeting-api acts for a bot, not for a person the gateway signed. Deciding WHO may
use which account is meeting-api's; the broker stores, lists by owner and destroys.

Storage, all under the broker's one lock:

* ``bot/<id>`` — ``{account, files}``, sealed with ``account.owner`` (every read names that owner).
* ``bot-index`` — ``{accounts: {id: BotAccount}}``, the metadata every list reads; it is the
  authority for an account's state, so a state change does not rewrite the profile.

Delete destroys every stored version of ``bot/<id>`` (`Store.delete`) and its index entry. Every
action is audited as ``bot.account.<action>``; nothing here logs a file path or a file's bytes.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from .broker import Broker
from .models import BotAccountBody
from .reasons import store_unavailable
from .store import Record

INDEX = "bot-index"


def _now() -> str:
    return datetime.fromtimestamp(time.time(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record_path(account_id: str) -> str:
    return "bot/" + account_id


def build(b: Broker) -> APIRouter:
    router = APIRouter()

    def read_index() -> tuple[dict, Optional[Record]]:
        stored = b.get(INDEX)
        accounts = dict((stored.data.get("accounts") or {}) if stored else {})
        return accounts, stored

    def write_index(accounts: dict, stored: Optional[Record]) -> None:
        # CAS on the version just read: an external writer racing the lock is refused, not merged.
        b.put(INDEX, {"accounts": accounts}, cas=stored.version if stored else 0)

    @router.post("/api/internal/bot-account")
    def bot_account(request: Request, body: BotAccountBody):
        """meeting-api's bot-account store: list · get · put · state · delete."""
        who = b.identity(request, {"bots"})
        action = "bot.account." + body.action
        subject = body.id or INDEX
        operation = uuid.uuid4().hex
        with b.lock:
            b.audit(who, subject, action, "requested", operation=operation)
            accounts, stored_index = read_index()
            files = None
            version = 0

            if body.action == "list":
                owners = set(body.owners or ())
                result = sorted((a for a in accounts.values() if a.get("owner") in owners),
                                key=lambda a: (a.get("created_at", ""), a.get("id", "")))

            elif body.action == "get":
                account = accounts.get(body.id)
                if account is None:
                    raise HTTPException(404, "Bot account not found")
                record = b.get(record_path(body.id), owner=account["owner"])
                if record is None:
                    # The index names a profile the store does not hold: a fault, never an empty profile.
                    b.fault("store", "missing", route="/api/internal/bot-account")
                    raise store_unavailable()
                files = list(record.data.get("files") or [])
                version = record.version
                result = [account]

            elif body.action == "put":
                spec = body.account
                existing = accounts.get(body.id)
                if existing is not None and (existing["owner"] != spec.owner or existing["platform"] != spec.platform):
                    raise HTTPException(409, "Bot account belongs to another owner or platform")
                now = _now()
                account = {
                    "id": body.id, "owner": spec.owner, "platform": spec.platform,
                    "display_name": spec.display_name, "state": "ok", "verified_at": now,
                    "created_at": existing["created_at"] if existing else now,
                    "created_by": existing["created_by"] if existing else spec.created_by,
                    "updated_at": now,
                }
                saved = b.put(record_path(body.id),
                              {"account": account, "files": [f.model_dump() for f in body.files or []]},
                              owner=spec.owner)
                version = saved.version
                accounts[body.id] = account
                write_index(accounts, stored_index)
                result = [account]

            elif body.action == "state":
                account = accounts.get(body.id)
                if account is None:
                    raise HTTPException(404, "Bot account not found")
                now = _now()
                account = {**account, "state": body.state, "updated_at": now,
                           **({"verified_at": now} if body.state == "ok" else {})}
                accounts[body.id] = account
                write_index(accounts, stored_index)
                result = [account]

            else:  # delete — idempotent; destroys the profile even when the index lost it
                account = accounts.pop(body.id, None)
                b.remove(record_path(body.id))
                if account is not None:
                    write_index(accounts, stored_index)
                result = [account] if account is not None else []

            b.audit(who, subject, action, "success", operation=operation, version=version)
            return {"accounts": result, "files": files}

    return router
