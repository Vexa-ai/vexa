"""``BotAccounts`` — the brick's one object: its ports and every rule the routes, the spawn and the
lifecycle hook apply.

Ports (each injected, each with a fake): ``broker`` (``BrokerClient``, ``None`` when the deployment
has none — every route then answers 409 ``NOT_CONFIGURED``), ``store`` (``SessionStore``),
``runtime`` (bot_spawn's ``RuntimeClient``: the workload is spawned with profile ``meeting-bot`` and
the one env var ``VEXA_BROWSER_SESSION``), ``admin`` (``AdminOracle``) and ``upstream``
(``VncUpstream``).

A refusal is a ``BotAccountRefused(status, detail)``; the router turns it into that HTTP answer.
Nothing here logs a token, a cookie or a profile byte.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from ..obs import log_event
from ..webhooks import ssrf
from . import sessions as S
from .broker import (
    NOT_CONFIGURED,
    BrokerClient,
    BrokerConflict,
    BrokerError,
    BrokerNotFound,
    new_account_id,
)
from .policy import AdminOracle, FakeAdminOracle, company_bot_use, owner_of, usable
from .relay import VNC_PORT, FakeVncUpstream, VncUpstream

#: Bot-account platforms, and the meeting platform each serves.
PLATFORMS = ("google", "microsoft", "zoom")
MEETING_PLATFORM_ACCOUNT = {"google_meet": "google", "teams": "microsoft", "zoom": "zoom"}
PLATFORM_LABELS = {"google": "Google", "microsoft": "Microsoft", "zoom": "Zoom"}
ACCOUNT_ID = re.compile(r"^[a-f0-9]{32}$")
SESSION_ID = ACCOUNT_ID
#: The prefix of the per-identity key a bot-account spawn carries (``meeting.data.auth_userdata_path``),
#: so ``find_active_by_userdata`` / ``latest_auth_session`` serialize and bind per account.
USERDATA_KEY_PREFIX = "bot-account:"
SIGNED_OUT_REMEDY = "browser_session_open"
AUTH_SESSION_MISSING = "auth_session_missing"


#: An operator's extra sign-in hosts — a company identity provider the platform hands the sign-in to
#: (a bank's ADFS for its Teams account). Comma-separated exact host names; empty by default. The
#: sign-in browser may navigate to these over https beside the platform's own hosts. A wildcard
#: (``*.example.com``) is refused: name each host the sign-in passes through.
SIGNIN_EXTRA_HOSTS_ENV = "VEXA_BOT_SIGNIN_EXTRA_HOSTS"
_HOST_NAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")


def parse_signin_extra_hosts(raw: Optional[str]) -> tuple:
    """``VEXA_BOT_SIGNIN_EXTRA_HOSTS`` as a tuple of exact host names, or ``ValueError`` naming the first
    entry that may not widen the sign-in browser: a wildcard (``*.example.com``), an IP literal, a
    single label, ``localhost`` or a metadata name."""
    entries = []
    for part in (raw or "").split(","):
        entry = part.strip().lower().rstrip(".")
        if not entry:
            continue
        if "*" in entry:
            raise ValueError(f"{SIGNIN_EXTRA_HOSTS_ENV}: {part.strip()!r} is a wildcard; name each sign-in "
                             "host exactly (for example adfs.example.com, login.example.okta.com)")
        name = entry
        if (ssrf.literal_address(name) is not None or ssrf.is_blocked_hostname(name)
                or not _HOST_NAME.match(name) or name.rsplit(".", 1)[-1].isdigit()):
            raise ValueError(f"{SIGNIN_EXTRA_HOSTS_ENV}: {part.strip()!r} is not a host name a sign-in "
                             "browser may reach (an IP literal, a single label, localhost or a metadata name)")
        entries.append(entry)
    return tuple(entries)


def userdata_key(account_id: str) -> str:
    return f"{USERDATA_KEY_PREFIX}{account_id}"


class BotAccountRefused(Exception):
    def __init__(self, status: int, detail: Any):
        super().__init__(str(detail))
        self.status = status
        self.detail = detail


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Settings:
    #: meeting-api's own base URL on the bots network — the same ``MEETING_API_URL`` bot_spawn builds
    #: ``meetingApiCallbackUrl`` from.
    callback_base: str = "http://meeting-api:8080"
    session_timeout_s: int = 900
    check_timeout_s: int = 180
    #: How the workload's host is named on the bots network: the runtime's container/Pod name.
    host_template: str = "vexa-{workload_id}"
    #: ``VEXA_BOT_SIGNIN_EXTRA_HOSTS``, parsed (``parse_signin_extra_hosts``).
    signin_extra_hosts: tuple = ()

    @classmethod
    def from_env(cls, env: Optional[dict] = None) -> "Settings":
        env = os.environ if env is None else env

        def _int(name: str, default: int) -> int:
            try:
                v = int(str(env.get(name) or "").strip() or default)
            except ValueError:
                return default
            return v if v > 0 else default

        return cls(
            callback_base=(env.get("MEETING_API_URL") or "http://meeting-api:8080").rstrip("/"),
            session_timeout_s=_int("VEXA_BOT_ACCOUNTS_SESSION_TIMEOUT_S", 900),
            check_timeout_s=_int("VEXA_BOT_ACCOUNTS_CHECK_TIMEOUT_S", 180),
            host_template=(env.get("VEXA_BOT_ACCOUNTS_WORKLOAD_HOST") or "vexa-{workload_id}"),
            signin_extra_hosts=parse_signin_extra_hosts(env.get(SIGNIN_EXTRA_HOSTS_ENV)),
        )


@dataclass
class BotAccounts:
    broker: Optional[BrokerClient]
    runtime: Any
    store: S.SessionStore = field(default_factory=S.InMemorySessionStore)
    admin: AdminOracle = field(default_factory=FakeAdminOracle)
    upstream: VncUpstream = field(default_factory=FakeVncUpstream)
    settings: Settings = field(default_factory=Settings)
    #: How an extra sign-in host is resolved before a session may reach it (``ssrf.resolve_host``).
    resolver: Optional[Callable[[str], list]] = None
    clock: Callable[[], float] = time.time
    policy: Callable[[], str] = company_bot_use

    # ── shared ────────────────────────────────────────────────────────────────────────────────
    @property
    def configured(self) -> bool:
        return self.broker is not None

    def _require(self) -> BrokerClient:
        if self.broker is None:
            raise BotAccountRefused(409, NOT_CONFIGURED)
        return self.broker

    def host_for(self, workload_id: str) -> str:
        return self.settings.host_template.format(workload_id=workload_id)

    async def _get_account(self, account_id: str, *, actor: str) -> tuple[dict, list]:
        broker = self._require()
        if not ACCOUNT_ID.match(account_id or ""):
            raise BotAccountRefused(404, "no such bot account")
        try:
            return await broker.get(account_id, actor=actor)
        except BrokerNotFound:
            raise BotAccountRefused(404, "no such bot account") from None
        except BrokerError as e:
            raise BotAccountRefused(503, str(e)) from None

    def _visible(self, account: dict, user_id: int) -> bool:
        return account.get("owner") in (owner_of(user_id), "company")

    async def _stop(self, workload_id: Optional[str], *, why: str) -> None:
        if not workload_id:
            return
        try:
            await self.runtime.delete_workload(workload_id)
        except Exception as e:  # noqa: BLE001 — the runtime's maxLifetimeSec is the backstop
            log_event("browser_session_stop_failed", audience="system", level="warning",
                      span="bot_accounts", fields={"workload_id": workload_id, "why": why,
                                                   "error": type(e).__name__})

    async def _spawn(self, workload_id: str, env_payload: dict, *, lifetime_s: int) -> None:
        from ..bot_spawn.invocation import conforms_workload_spec
        from ..bot_spawn.ports import QuotaExceeded

        spec = {
            "workloadId": workload_id,
            "profile": "meeting-bot",
            "env": {"VEXA_BROWSER_SESSION": json.dumps(env_payload, separators=(",", ":"))},
            "maxLifetimeSec": int(lifetime_s),
        }
        conforms_workload_spec(spec)
        try:
            result = await self.runtime.create_workload(spec)
        except QuotaExceeded:
            raise BotAccountRefused(429, "the runtime is at its workload limit — try again shortly") from None
        except Exception as e:  # noqa: BLE001 — SpawnFailed and transport faults alike
            raise BotAccountRefused(502, f"the browser workload did not start: {e}") from None
        if (result or {}).get("state") in ("stopped", "destroyed"):
            raise BotAccountRefused(502, "the browser workload did not start")

    # ── GET /bot-accounts ─────────────────────────────────────────────────────────────────────
    async def list_for(self, user_id: int) -> dict:
        broker = self._require()
        mine = owner_of(user_id)
        try:
            accounts = await broker.list([mine, "company"], actor=mine)
        except BrokerError as e:
            raise BotAccountRefused(503, str(e)) from None
        policy = self.policy()
        roles = await self.admin.roles(user_id) if any(a.get("owner") == "company" for a in accounts) else {}
        out = [{**a, "mine": a.get("owner") == mine,
                "usable": usable(a, user_id=user_id, policy=policy, roles=roles)} for a in accounts]
        return {"accounts": out, "company_bot_use": policy}

    # ── browser sessions ──────────────────────────────────────────────────────────────────────
    async def open_session(self, user_id: int, body: dict) -> dict:
        self._require()
        platform = body.get("platform")
        if platform not in PLATFORMS:
            raise BotAccountRefused(422, f"platform must be one of {', '.join(PLATFORMS)}")
        display_name = body.get("display_name")
        if display_name is not None:
            if not isinstance(display_name, str) or not display_name.strip() or len(display_name.strip()) > 120:
                raise BotAccountRefused(422, "display_name must be 1 to 120 characters")
            display_name = display_name.strip()
        owner_word = body.get("owner", "me")
        if owner_word not in ("me", "company"):
            raise BotAccountRefused(422, "owner must be 'me' or 'company'")
        owner = owner_of(user_id) if owner_word == "me" else "company"
        account_id = body.get("account_id")
        if account_id is not None:
            if not isinstance(account_id, str) or not ACCOUNT_ID.match(account_id):
                raise BotAccountRefused(422, "account_id must be a bot account id")
            account, _files = await self._get_account(account_id, actor=owner_of(user_id))
            if not self._visible(account, user_id):
                raise BotAccountRefused(404, "no such bot account")
            if account.get("platform") != platform:
                raise BotAccountRefused(422, f"bot account {account_id} is a {account.get('platform')} account")
            owner = account["owner"]
            display_name = display_name or account.get("display_name")
        if owner == "company":
            roles = await self.admin.roles(user_id)
            if not roles.get("admin_account"):
                raise BotAccountRefused(403, "only the admin account may sign in a company bot account")

        now = self.clock()
        timeout = self.settings.session_timeout_s
        ttl = timeout + S.RECORD_MARGIN_S
        session_id = S.new_id()
        held = await self.store.claim_user(user_id, session_id, ttl_s=ttl)
        if held is not None:
            prior = await self.store.load("session", held)
            if prior is not None and prior.get("status") in S.OPEN_STATUSES and prior.get("expires_ts", 0) > now:
                raise BotAccountRefused(409, f"you already have an open browser session ({held}) — close it first")
            await self.store.release_user(user_id, held)
            if await self.store.claim_user(user_id, session_id, ttl_s=ttl) is not None:
                raise BotAccountRefused(409, "you already have an open browser session — close it first")

        token = S.new_token()
        vnc_password = S.new_vnc_password()
        workload_id = f"bs-{session_id}"
        expires_ts = now + timeout
        record = {
            "session_id": session_id, "user_id": int(user_id), "owner": owner, "platform": platform,
            "display_name": display_name, "account_id": account_id, "workload_id": workload_id,
            "host": self.host_for(workload_id), "status": "starting", "detail": None,
            "vnc_password": vnc_password, "token_hash": S.token_hash(token),
            "expires_at": _iso(expires_ts), "expires_ts": expires_ts,
        }
        await self.store.save("session", session_id, record, ttl_s=ttl)
        await self.store.index(f"session:{session_id}", expires_ts)
        env_payload = {
            "mode": "signin", "session_id": session_id, "platform": platform, "token": token,
            "vnc_password": vnc_password,
            "save_url": f"{self.settings.callback_base}/internal/bot-accounts/sessions/{session_id}/profile",
            "timeout_ms": timeout * 1000,
        }
        extra_hosts = self._signin_extra_hosts()
        if extra_hosts:
            env_payload["extra_hosts"] = extra_hosts
        try:
            await self._spawn(workload_id, env_payload, lifetime_s=ttl)
        except BotAccountRefused as e:
            record.update(status="failed", detail=str(e.detail))
            await self.store.save("session", session_id, record, ttl_s=ttl)
            await self.store.unindex(f"session:{session_id}")
            await self.store.release_user(user_id, session_id)
            raise
        log_event("browser_session_opened", audience="user", span="bot_accounts", user_id=user_id,
                  fields={"session_id": session_id, "platform": platform, "owner": owner})
        return {
            "session_id": session_id, "status": "starting", "platform": platform, "owner": owner,
            "view_path": f"/bot-accounts/sessions/{session_id}/view/vnc.html",
            "ws_path": f"/bot-accounts/sessions/{session_id}/vnc",
            "vnc_password": vnc_password, "expires_at": record["expires_at"],
        }

    def _signin_extra_hosts(self) -> list:
        """The operator's extra sign-in hosts this session may reach. Each is resolved now and left
        out (logged) when it does not resolve or any address it resolves to is internal."""
        resolve = self.resolver or ssrf.resolve_host
        out = []
        for entry in self.settings.signin_extra_hosts:
            addrs = resolve(entry)
            if not addrs or any(ssrf.is_blocked_ip(a) for a in addrs):
                log_event("browser_session_extra_host_refused", audience="system", level="warning",
                          span="bot_accounts",
                          fields={"host": entry, "why": "internal address" if addrs else "does not resolve"})
                continue
            out.append(entry)
        return out

    async def owned_session(self, user_id: int, session_id: str, *, refresh: bool = True) -> dict:
        """The caller's session record, refreshed; ``BotAccountRefused(404)`` for anybody else's."""
        rec = await self.store.load("session", session_id) if SESSION_ID.match(session_id or "") else None
        if rec is None or int(rec.get("user_id") or -1) != int(user_id):
            raise BotAccountRefused(404, "no such browser session")
        return await self._refresh(rec) if refresh else rec

    async def _save_session(self, rec: dict) -> None:
        ttl = max(1, int(rec.get("expires_ts", self.clock()) - self.clock()) + S.RECORD_MARGIN_S)
        await self.store.save("session", rec["session_id"], rec, ttl_s=ttl)

    async def _finish(self, rec: dict, status: str, detail: Optional[str]) -> dict:
        rec.update(status=status, detail=detail)
        await self._save_session(rec)
        await self.store.unindex(f"session:{rec['session_id']}")
        await self.store.release_user(rec["user_id"], rec["session_id"])
        return rec

    async def _refresh(self, rec: dict) -> dict:
        status = rec.get("status")
        if status not in S.OPEN_STATUSES:
            return rec
        if rec.get("expires_ts", 0) <= self.clock():
            await self._stop(rec.get("workload_id"), why="expired")
            return await self._finish(rec, "closed", "the browser session expired")
        try:
            w = await self.runtime.get_workload(rec["workload_id"])
        except Exception:  # noqa: BLE001 — unknown is not a verdict; keep the status
            return rec
        state = (w or {}).get("state")
        if state in ("stopped", "destroyed"):
            return await self._finish(rec, "failed", "the browser workload stopped before the sign-in was saved")
        if status == "starting" and state == "running":
            if await self.upstream.probe(f"http://{rec['host']}:{VNC_PORT}/"):
                rec["status"] = "ready"
                await self._save_session(rec)
        return rec

    @staticmethod
    def session_view(rec: dict) -> dict:
        # vnc_password rides to the owner only (owned_session 404s everybody else), so a viewer can
        # fetch it here rather than carry it through a chat stream.
        return {k: rec.get(k) for k in ("session_id", "status", "platform", "owner", "account_id",
                                        "detail", "expires_at", "vnc_password")}

    async def close_session(self, user_id: int, session_id: str) -> dict:
        rec = await self.owned_session(user_id, session_id, refresh=False)
        if rec.get("status") in S.OPEN_STATUSES:
            await self._stop(rec.get("workload_id"), why="closed")
            await self._finish(rec, "closed", "closed by its owner")
        log_event("browser_session_closed", audience="user", span="bot_accounts", user_id=user_id,
                  fields={"session_id": session_id})
        return {"session_id": session_id, "status": "closed"}

    async def save_profile(self, session_id: str, token: str, files: list) -> tuple[dict, Optional[str]]:
        """The sign-in workload's save. Returns the answer and the workload to stop afterwards."""
        broker = self._require()
        rec = await self.store.load("session", session_id) if SESSION_ID.match(session_id or "") else None
        if rec is None or not S.token_matches(token, rec.get("token_hash")):
            raise BotAccountRefused(401, "invalid browser-session token")
        if rec.get("status") not in S.OPEN_STATUSES or rec.get("expires_ts", 0) <= self.clock():
            raise BotAccountRefused(409, "this browser session is no longer open")
        owner, platform = rec["owner"], rec["platform"]
        account_id = rec.get("account_id")
        display_name = rec.get("display_name")
        if account_id:
            current, _ = await self._get_account(account_id, actor=owner)
            if current.get("owner") != owner or current.get("platform") != platform:
                raise BotAccountRefused(409, "the bot account's owner or platform differs")
            display_name = display_name or current.get("display_name")
        else:
            account_id = new_account_id()
        account = {"owner": owner, "platform": platform,
                   "display_name": display_name or f"{PLATFORM_LABELS[platform]} bot account",
                   "created_by": owner_of(rec["user_id"])}
        try:
            stored = await broker.put(account_id, account=account, files=files, actor=owner)
        except BrokerConflict:
            raise BotAccountRefused(409, "the bot account's owner or platform differs") from None
        except BrokerError as e:
            raise BotAccountRefused(502, str(e)) from None
        rec["account_id"] = account_id
        await self._finish(rec, "saved", None)
        log_event("bot_account_saved", audience="user", span="bot_accounts", user_id=rec["user_id"],
                  fields={"account_id": account_id, "owner": owner, "platform": platform,
                          "files": len(files)})
        return {"account_id": stored.get("id", account_id), "files": len(files)}, rec.get("workload_id")

    # ── verify ────────────────────────────────────────────────────────────────────────────────
    async def verify(self, user_id: int, account_id: str) -> dict:
        account, _ = await self._get_account(account_id, actor=owner_of(user_id))
        if not self._visible(account, user_id):
            raise BotAccountRefused(404, "no such bot account")
        if account["owner"] == "company":
            roles = await self.admin.roles(user_id)
            if not usable(account, user_id=user_id, policy=self.policy(), roles=roles):
                raise BotAccountRefused(403, "this company bot account is not available to you")
        now = self.clock()
        timeout = self.settings.check_timeout_s
        ttl = timeout + S.RECORD_MARGIN_S
        check_id = S.new_id()
        token = S.new_token()
        workload_id = f"bc-{check_id}"
        expires_ts = now + timeout
        rec = {"check_id": check_id, "user_id": int(user_id), "account_id": account_id,
               "platform": account["platform"], "workload_id": workload_id, "status": "checking",
               "detail": None, "token_hash": S.token_hash(token), "expires_at": _iso(expires_ts),
               "expires_ts": expires_ts}
        await self.store.save("check", check_id, rec, ttl_s=ttl)
        await self.store.index(f"check:{check_id}", expires_ts)
        base = self.settings.callback_base
        env_payload = {
            "mode": "verify", "session_id": check_id, "platform": account["platform"], "token": token,
            "restore_url": f"{base}/internal/browser-session-check/{check_id}",
            "result_url": f"{base}/internal/bot-accounts/checks/{check_id}",
            "timeout_ms": timeout * 1000,
        }
        try:
            await self._spawn(workload_id, env_payload, lifetime_s=ttl)
        except BotAccountRefused:
            rec.update(status="failed")
            await self.store.save("check", check_id, rec, ttl_s=ttl)
            await self.store.unindex(f"check:{check_id}")
            raise
        log_event("bot_account_check_started", audience="user", span="bot_accounts", user_id=user_id,
                  fields={"account_id": account_id, "check_id": check_id})
        return {"account_id": account_id, "check_id": check_id, "status": "checking"}

    async def _open_check(self, check_id: str, token: str) -> dict:
        self._require()
        rec = await self.store.load("check", check_id) if SESSION_ID.match(check_id or "") else None
        if rec is None or not S.token_matches(token, rec.get("token_hash")):
            raise BotAccountRefused(401, "invalid check token")
        if rec.get("status") not in S.CHECK_OPEN or rec.get("expires_ts", 0) <= self.clock():
            raise BotAccountRefused(409, "this check is no longer open")
        return rec

    async def check_profile(self, check_id: str, token: str) -> list:
        rec = await self._open_check(check_id, token)
        _account, files = await self._get_account(rec["account_id"], actor="meeting-api")
        return files

    async def check_result(self, check_id: str, token: str, body: Any) -> tuple[dict, Optional[str]]:
        rec = await self._open_check(check_id, token)
        if not isinstance(body, dict) or not isinstance(body.get("logged_in"), bool):
            raise BotAccountRefused(422, "body must be {logged_in: bool, detail: str}")
        detail = body.get("detail")
        detail = detail[:500] if isinstance(detail, str) else None
        state = "ok" if body["logged_in"] else "signed_out"
        try:
            await self.broker.state(rec["account_id"], state, actor="meeting-api")
        except BrokerNotFound:
            raise BotAccountRefused(404, "no such bot account") from None
        except BrokerError as e:
            raise BotAccountRefused(502, str(e)) from None
        rec.update(status="done", detail=detail)
        ttl = max(1, int(rec["expires_ts"] - self.clock()) + S.RECORD_MARGIN_S)
        await self.store.save("check", check_id, rec, ttl_s=ttl)
        await self.store.unindex(f"check:{check_id}")
        log_event("bot_account_verified", audience="user", span="bot_accounts", user_id=rec["user_id"],
                  fields={"account_id": rec["account_id"], "state": state})
        return {"account_id": rec["account_id"], "state": state}, rec.get("workload_id")

    # ── delete ────────────────────────────────────────────────────────────────────────────────
    async def delete_account(self, user_id: int, account_id: str) -> dict:
        account, _ = await self._get_account(account_id, actor=owner_of(user_id))
        if not self._visible(account, user_id):
            raise BotAccountRefused(404, "no such bot account")
        if account["owner"] == "company":
            roles = await self.admin.roles(user_id)
            if not roles.get("admin_account"):
                raise BotAccountRefused(403, "only the admin account may delete a company bot account")
        try:
            await self.broker.delete(account_id, actor=account["owner"])
        except BrokerError as e:
            raise BotAccountRefused(502, str(e)) from None
        log_event("bot_account_deleted", audience="user", span="bot_accounts", user_id=user_id,
                  fields={"account_id": account_id, "owner": account["owner"],
                          "platform": account.get("platform")})
        return {"account_id": account_id, "deleted": True}

    # ── the meeting bot ───────────────────────────────────────────────────────────────────────
    async def resolve_for_spawn(self, user_id: int, meeting_platform: str, choice: Optional[str]) -> Optional[dict]:
        """The bot account a ``POST /bots`` joins as, or ``None`` (a guest bot, as before).

        ``choice``: an account id, ``"default"`` (also when absent) or ``"none"``. ``default`` is the
        caller's most recently verified ``ok`` account for the platform, else a company account
        usable to them, else none — and a store that cannot answer is none, never a failed spawn. An
        explicit id is held to every rule: configured, visible, usable, same platform, signed in."""
        choice = "default" if choice is None else choice
        if choice == "none":
            return None
        platform = MEETING_PLATFORM_ACCOUNT.get(meeting_platform)
        if choice != "default":
            if not isinstance(choice, str) or not ACCOUNT_ID.match(choice):
                raise BotAccountRefused(422, "bot_account must be a bot account id, 'default' or 'none'")
            if platform is None:
                raise BotAccountRefused(422, f"a {meeting_platform} bot cannot join as a bot account")
            account, _ = await self._get_account(choice, actor=owner_of(user_id))
            if not self._visible(account, user_id):
                raise BotAccountRefused(404, "no such bot account")
            if account.get("owner") == "company":
                roles = await self.admin.roles(user_id)
                if not usable(account, user_id=user_id, policy=self.policy(), roles=roles):
                    raise BotAccountRefused(403, "this company bot account is not available to you")
            if account.get("platform") != platform:
                raise BotAccountRefused(
                    422, f"bot account {choice} is a {account.get('platform')} account; this meeting needs {platform}")
            if account.get("state") != "ok":
                raise BotAccountRefused(409, {
                    "detail": f"bot account {choice} is signed out — sign it in again",
                    "bot_account": {"id": choice, "state": account.get("state"), "remedy": SIGNED_OUT_REMEDY},
                })
            return account
        if self.broker is None or platform is None:
            return None
        mine = owner_of(user_id)
        try:
            accounts = await self.broker.list([mine, "company"], actor=mine)
        except BrokerError as e:
            log_event("bot_account_default_unavailable", audience="system", level="warning",
                      span="bot_accounts", user_id=user_id, fields={"error": str(e)})
            return None
        ok = [a for a in accounts if a.get("platform") == platform and a.get("state") == "ok"]

        def newest(rows):
            return max(rows, key=lambda a: a.get("verified_at") or "", default=None)

        own = newest([a for a in ok if a.get("owner") == mine])
        if own is not None:
            return own
        company = [a for a in ok if a.get("owner") == "company"]
        if not company:
            return None
        roles = await self.admin.roles(user_id)
        policy = self.policy()
        return newest([a for a in company if usable(a, user_id=user_id, policy=policy, roles=roles)])

    async def restore_files(self, account_id: str) -> list:
        _account, files = await self._get_account(account_id, actor="meeting-api")
        return files

    async def write_back(self, account_id: str, files: list) -> dict:
        broker = self._require()
        account, _ = await self._get_account(account_id, actor="meeting-api")
        try:
            return await broker.put(account_id, account=account, files=files, actor=account["owner"])
        except BrokerError as e:
            raise BotAccountRefused(502, str(e)) from None

    async def on_terminal(self, meeting_row: dict, completion_reason: Optional[str]) -> Optional[dict]:
        """The lifecycle hook: a bot that joined as a bot account and ended ``auth_session_missing``
        marks the account ``signed_out`` and answers the data the meeting then shows."""
        data = (meeting_row or {}).get("data") or {}
        account_id = data.get("bot_account_id")
        if not account_id or completion_reason != AUTH_SESSION_MISSING or self.broker is None:
            return None
        try:
            await self.broker.state(account_id, "signed_out", actor="meeting-api")
        except BrokerError as e:
            log_event("bot_account_signout_failed", audience="system", level="warning",
                      span="bot_accounts", meeting_id=str(meeting_row.get("id")),
                      fields={"account_id": account_id, "error": type(e).__name__})
        log_event("bot_account_signed_out", audience="user", span="bot_accounts",
                  user_id=meeting_row.get("user_id"), meeting_id=str(meeting_row.get("id")),
                  fields={"account_id": account_id})
        return {"bot_account": {"id": account_id, "state": "signed_out", "remedy": SIGNED_OUT_REMEDY}}

    # ── the reaper ────────────────────────────────────────────────────────────────────────────
    async def reap(self) -> int:
        """Close every session and check past its expiry and stop its workload."""
        reaped = 0
        for member in await self.store.expired(self.clock()):
            kind, _, rid = (member or "").partition(":")
            if kind not in ("session", "check"):
                await self.store.unindex(member)
                continue
            rec = await self.store.load(kind, rid)
            if rec is not None and rec.get("status") in (S.OPEN_STATUSES + S.CHECK_OPEN):
                await self._stop(rec.get("workload_id"), why="expired")
                if kind == "session":
                    await self._finish(rec, "closed", "the browser session expired")
                else:
                    rec.update(status="closed", detail="the check expired")
                    await self.store.save(kind, rid, rec, ttl_s=S.RECORD_MARGIN_S)
                reaped += 1
            await self.store.unindex(member)
        return reaped
