"""The bot-account store — credential-broker.v1 role ``bots`` (``POST /api/internal/bot-account``).

meeting-api keeps bot accounts (saved signed-in browser profiles) in the credential broker and never
on its own disk. Every request is one ``BotAccountRequest`` signed as role ``bots`` with the key at
``VEXA_BOT_ACCOUNTS_KEY_FILE`` (``broker_assertion.sign``: the assertion binds method, exact path and
the SHA-256 of the exact body bytes, so the bytes signed are the bytes sent). No ``X-Vexa-Identity``
rides this role: meeting-api acts for a bot, and the assertion's ``actor`` names whom for —
``user:<id>``, ``company`` or ``meeting-api``.

``BrokerClient`` is the port; ``HttpBrokerClient`` the httpx adapter; ``FakeBroker`` the in-memory
store the tests drive with the broker's own semantics (put replaces only on a matching owner and
platform, delete is idempotent, ``state`` stamps ``verified_at`` when ``ok``).
"""
from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Protocol, runtime_checkable

from . import broker_assertion

#: The one broker route this role may call.
BROKER_PATH = "/api/internal/bot-account"
ROLE = "bots"
#: The answer every bot-account route gives on a deployment without the broker wired.
NOT_CONFIGURED = "bot accounts are not configured on this deployment"


class BrokerError(Exception):
    """The broker answered unusably or could not be reached (a fault to retry later)."""


class BrokerNotFound(BrokerError):
    """``get`` of an id the broker does not hold (404)."""


class BrokerConflict(BrokerError):
    """``put`` over an id whose stored owner or platform differs (409)."""


class BrokerNotConfigured(BrokerError):
    """``VEXA_BOT_ACCOUNTS_BROKER_URL`` / ``VEXA_BOT_ACCOUNTS_KEY_FILE`` unset or the key unusable."""


@runtime_checkable
class BrokerClient(Protocol):
    """The five actions of ``BotAccountRequest``. ``files`` are ``(path, bytes)`` pairs here; the
    wire's base64 lives in the adapter."""

    async def list(self, owners: list[str], *, actor: str) -> list[dict]:
        ...

    async def get(self, account_id: str, *, actor: str) -> tuple[dict, list[tuple[str, bytes]]]:
        ...

    async def put(self, account_id: str, *, account: dict, files: list[tuple[str, bytes]],
                  actor: str) -> dict:
        ...

    async def state(self, account_id: str, state: str, *, actor: str) -> dict:
        ...

    async def delete(self, account_id: str, *, actor: str) -> Optional[dict]:
        ...


def new_account_id() -> str:
    return uuid.uuid4().hex


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _encode_files(files: list[tuple[str, bytes]]) -> list[dict]:
    return [{"path": p, "data": base64.b64encode(d).decode("ascii")} for p, d in files]


def _decode_files(entries: Any) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    for e in entries or []:
        out.append((str(e["path"]), base64.b64decode(e["data"])))
    return out


class HttpBrokerClient:
    """``BrokerClient`` over httpx, signing each request as role ``bots``."""

    def __init__(self, base_url: str, key: bytes, *, client: Any = None, timeout: float = 15.0):
        self._url = base_url.rstrip("/")
        self._key = key
        self._client = client
        self._timeout = timeout

    async def _call(self, body: dict, *, actor: str) -> dict:
        raw = json.dumps(body, separators=(",", ":")).encode()
        header = broker_assertion.sign(
            self._key, role=ROLE, actor=actor, session=f"bot-accounts-{uuid.uuid4().hex}",
            method="POST", path=BROKER_PATH, body=raw,
        )
        headers = {broker_assertion.HEADER: header, "content-type": "application/json"}
        try:
            if self._client is not None:
                resp = await self._client.post(f"{self._url}{BROKER_PATH}", content=raw,
                                               headers=headers, timeout=self._timeout)
            else:
                import httpx

                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(f"{self._url}{BROKER_PATH}", content=raw, headers=headers)
        except Exception as e:  # noqa: BLE001 — any transport failure is one typed fault
            raise BrokerError(f"the bot-account store could not be reached ({type(e).__name__})") from None
        if resp.status_code == 404:
            raise BrokerNotFound("no such bot account")
        if resp.status_code == 409:
            raise BrokerConflict("the bot account's owner or platform differs")
        if resp.status_code in (401, 403):
            raise BrokerNotConfigured("the bot-account store refused meeting-api's assertion")
        if resp.status_code != 200:
            raise BrokerError(f"the bot-account store answered {resp.status_code}")
        try:
            out = resp.json()
        except ValueError:
            raise BrokerError("the bot-account store answered a body that is not JSON") from None
        if not isinstance(out, dict) or not isinstance(out.get("accounts"), list):
            raise BrokerError("the bot-account store answered an unexpected body")
        return out

    async def list(self, owners: list[str], *, actor: str) -> list[dict]:
        out = await self._call({"action": "list", "owners": list(owners)}, actor=actor)
        return list(out["accounts"])

    async def get(self, account_id: str, *, actor: str) -> tuple[dict, list[tuple[str, bytes]]]:
        out = await self._call({"action": "get", "id": account_id}, actor=actor)
        if not out["accounts"]:
            raise BrokerNotFound("no such bot account")
        return out["accounts"][0], _decode_files(out.get("files"))

    async def put(self, account_id: str, *, account: dict, files: list[tuple[str, bytes]],
                  actor: str) -> dict:
        body = {"action": "put", "id": account_id,
                "account": {k: account[k] for k in ("owner", "platform", "display_name", "created_by")},
                "files": _encode_files(files)}
        out = await self._call(body, actor=actor)
        if not out["accounts"]:
            raise BrokerError("the bot-account store stored nothing")
        return out["accounts"][0]

    async def state(self, account_id: str, state: str, *, actor: str) -> dict:
        out = await self._call({"action": "state", "id": account_id, "state": state}, actor=actor)
        if not out["accounts"]:
            raise BrokerNotFound("no such bot account")
        return out["accounts"][0]

    async def delete(self, account_id: str, *, actor: str) -> Optional[dict]:
        out = await self._call({"action": "delete", "id": account_id}, actor=actor)
        return out["accounts"][0] if out["accounts"] else None


class FakeBroker:
    """An in-memory ``BrokerClient`` with the broker's semantics. ``calls`` records ``(action, actor)``;
    ``fail=True`` makes every call a ``BrokerError``."""

    def __init__(self, *, fail: bool = False):
        self.accounts: dict[str, dict] = {}
        self.files: dict[str, list[tuple[str, bytes]]] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    def _check(self, action: str, actor: str) -> None:
        self.calls.append((action, actor))
        if self.fail:
            raise BrokerError("the bot-account store could not be reached (fake)")

    def seed(self, account: dict, files: Optional[list[tuple[str, bytes]]] = None) -> dict:
        now = _now_iso()
        row = {"state": "ok", "verified_at": now, "created_at": now, "updated_at": now,
               "created_by": "user:1", **account}
        self.accounts[row["id"]] = row
        self.files[row["id"]] = list(files or [])
        return dict(row)

    async def list(self, owners, *, actor):
        self._check("list", actor)
        return [dict(a) for a in self.accounts.values() if a["owner"] in owners]

    async def get(self, account_id, *, actor):
        self._check("get", actor)
        if account_id not in self.accounts:
            raise BrokerNotFound("no such bot account")
        return dict(self.accounts[account_id]), list(self.files.get(account_id, []))

    async def put(self, account_id, *, account, files, actor):
        self._check("put", actor)
        now = _now_iso()
        cur = self.accounts.get(account_id)
        if cur is not None:
            if cur["owner"] != account["owner"] or cur["platform"] != account["platform"]:
                raise BrokerConflict("the bot account's owner or platform differs")
            row = {**cur, "display_name": account["display_name"], "state": "ok",
                   "verified_at": now, "updated_at": now}
        else:
            row = {"id": account_id, "owner": account["owner"], "platform": account["platform"],
                   "display_name": account["display_name"], "state": "ok", "verified_at": now,
                   "created_at": now, "created_by": account["created_by"], "updated_at": now}
        self.accounts[account_id] = row
        self.files[account_id] = list(files)
        return dict(row)

    async def state(self, account_id, state, *, actor):
        self._check("state", actor)
        if account_id not in self.accounts:
            raise BrokerNotFound("no such bot account")
        now = _now_iso()
        row = {**self.accounts[account_id], "state": state, "updated_at": now}
        if state == "ok":
            row["verified_at"] = now
        self.accounts[account_id] = row
        return dict(row)

    async def delete(self, account_id, *, actor):
        self._check("delete", actor)
        self.files.pop(account_id, None)
        row = self.accounts.pop(account_id, None)
        return dict(row) if row else None


def broker_from_env(env: Optional[dict] = None) -> Optional[HttpBrokerClient]:
    """The configured broker client, or ``None`` when either setting is unset or the key file does
    not exist yet (the feature then answers 409 ``NOT_CONFIGURED``). A key file that exists but is
    unusable (unreadable, short, published) is a configuration fault: ``BrokerNotConfigured``, so the
    boot can refuse it loudly."""
    env = os.environ if env is None else env
    url = (env.get("VEXA_BOT_ACCOUNTS_BROKER_URL") or "").strip()
    key_file = (env.get("VEXA_BOT_ACCOUNTS_KEY_FILE") or "").strip()
    if not url or not key_file:
        return None
    if not os.path.exists(key_file):
        # A deployment upgraded from before bot accounts has the path wired but no key yet (the key
        # generator runs once, at a stack's first start). That is "not configured", said once, not a
        # refused boot: meetings keep running, and the feature answers 409 until the key exists.
        print(json.dumps({"event": "bot_accounts_unconfigured", "service": "meeting-api",
                          "reason": "VEXA_BOT_ACCOUNTS_KEY_FILE names no file yet"}), flush=True)
        return None
    try:
        key = broker_assertion.load_key(key_file)
    except broker_assertion.KeyUnavailable as e:
        raise BrokerNotConfigured(f"VEXA_BOT_ACCOUNTS_KEY_FILE is not usable: {e}") from None
    return HttpBrokerClient(url, key)
