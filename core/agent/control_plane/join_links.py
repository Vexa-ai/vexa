"""join_links.py — one link that lets people in: to a workspace or to a chat (#1783).

Founder, 2026-10-14: a host creates a workspace, puts its link into the calendar event, and anybody
who opens it (signing in first if they must) joins — N times, revocable, with an optional expiry.
Chats get the same: *join this chat by its link without being tagged*.

ONE MECHANISM, TWO TARGETS. A link is a random token (32 bytes, URL-safe) whose sha256 is the only
thing stored; the plaintext is handed back once, at mint. Every record carries the same terms —
role, ``max_uses``, ``uses``, ``expires_at``, ``revoked``, ``created_by`` — whatever it opens.

* ``workspace`` links ARE the workspace invites `workspace_membership` already stores, minted
  ``mode=open``: one store, so `/join`, the preview and the accept route keep reading exactly one
  file and an invite minted by either door redeems through the other.
* ``chat`` links live beside them, in ``<root>/.invites/.chats/<chat_id>.json`` — outside every
  workspace mount, like the workspace store. What a chat IS (who may link it, what joining it
  means, its title) belongs to the chat domain, which plugs it in with `register_chat_target`;
  until something does, minting a chat link is refused, never faked.

THE LINK. ``<VEXA_UI_URL>/join/<token>``. The token is also the one machine-readable marker of a
workspace in anything a link is pasted into — a calendar event body, an email: `markers` finds them.
"""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from control_plane import workspace_membership as membership_mod

TARGET_KINDS = ("workspace", "chat")
#: Where a link is opened — the terminal's page. `/join?i=<token>` (the older spelling) still works.
JOIN_PATH = "/join/"
#: The default terms of a link a person asks for in words: a handful of people, for a week.
DEFAULT_MAX_USES = 10
MAX_MAX_USES = 1000
DEFAULT_EXPIRES_IN_SEC = 7 * 86400
MAX_EXPIRES_IN_SEC = 365 * 86400
CHAT_STORE_DIR = ".chats"
_CHAT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
#: A token as `secrets.token_urlsafe(32)` mints it (43 characters); the marker regex is bounded so
#: a link in running text is found and nothing around it is swallowed.
_TOKEN = r"[A-Za-z0-9_-]{40,64}"
MARKER = re.compile(r"https?://[^\s/<>\"']+/join(?:/(" + _TOKEN + r")|\?i=(" + _TOKEN + r"))")


class LinkError(RuntimeError):
    """A refusal with the HTTP status it maps to (same shape as `MembershipError`)."""

    def __init__(self, message: str, *, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class MintedLink:
    id: str
    token: str
    kind: str
    target_id: str
    role: str
    max_uses: int
    expires_at: int


def link_url(ui_url: str, token: str) -> str:
    """``<ui_url>/join/<token>``; empty when the deployment has not declared its app URL."""
    base = str(ui_url or "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}{JOIN_PATH}{urllib.parse.quote(token, safe='')}"


def markers(text: str) -> list[str]:
    """Every join-link token in ``text`` (in order, de-duplicated) — how a sensor reading an event
    body or an email finds the workspace it names. Resolve each with `preview`."""
    out: list[str] = []
    for m in MARKER.finditer(str(text or "")):
        tok = m.group(1) or m.group(2)
        if tok and tok not in out:
            out.append(tok)
    return out


def terms(max_uses: Optional[int], expires_in_sec: Optional[int]) -> tuple[int, int]:
    """The terms a link is minted with, bounded: refused out of range, defaulted when unsaid."""
    n = DEFAULT_MAX_USES if max_uses is None else int(max_uses)
    if n < 1 or n > MAX_MAX_USES:
        raise LinkError(f"max_uses must be between 1 and {MAX_MAX_USES}", status=400)
    e = DEFAULT_EXPIRES_IN_SEC if expires_in_sec is None else int(expires_in_sec)
    if e <= 0 or e > MAX_EXPIRES_IN_SEC:
        raise LinkError("a link expires between a second and a year from now", status=400)
    return n, e


# ── the chat target, as the chat domain declares it ────────────────────────────────────────────
@dataclass(frozen=True)
class ChatTarget:
    """What the chat domain tells this module about chats. ``may_link(subject, chat_id)`` — may this
    person hand out a link to it; ``is_in(subject, chat_id)`` — are they a participant already;
    ``join(subject, email, chat_id, role)`` — make them one (``role`` is ``read`` or ``write``);
    ``title(chat_id)`` — its name, for the preview a link's holder sees."""
    may_link: Callable[[str, str], bool]
    is_in: Callable[[str, str], bool]
    join: Callable[[str, str, str, str], None]
    title: Callable[[str], str]


_CHAT: dict[str, ChatTarget] = {}


def register_chat_target(target: Optional[ChatTarget]) -> None:
    """Plug the chat domain in (or out, with None)."""
    if target is None:
        _CHAT.pop("chat", None)
    else:
        _CHAT["chat"] = target


def chat_target() -> Optional[ChatTarget]:
    return _CHAT.get("chat")


# ── the chat store ──────────────────────────────────────────────────────────────────────────────
_LOCKS: dict[str, threading.Lock] = {}
_GUARD = threading.Lock()


def _lock(key: str) -> threading.Lock:
    with _GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _chat_path(root: Path, chat_id: str) -> Path:
    cid = str(chat_id or "").strip()
    if not _CHAT_ID.match(cid):
        raise LinkError("invalid chat id", status=400)
    base = (Path(root).resolve() / membership_mod.INVITE_STORE_DIR / CHAT_STORE_DIR)
    path = (base / f"{cid}.json").resolve()
    if base not in path.parents:
        raise LinkError("invalid chat id", status=400)
    return path


def _read_chat(root: Path, chat_id: str) -> list[dict]:
    p = _chat_path(root, chat_id)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _write_chat(root: Path, chat_id: str, rows: list[dict]) -> None:
    p = _chat_path(root, chat_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, indent=2) + "\n")
    tmp.replace(p)


def _chats_with_links(root: Path) -> list[str]:
    d = Path(root) / membership_mod.INVITE_STORE_DIR / CHAT_STORE_DIR
    return sorted(p.stem for p in d.glob("*.json")) if d.is_dir() else []


# ── the one mechanism ───────────────────────────────────────────────────────────────────────────
def mint(root: Path, kind: str, target_id: str, *, role: str, created_by: str,
         max_uses: Optional[int] = None, expires_in_sec: Optional[int] = None,
         now: Optional[float] = None) -> MintedLink:
    """Mint a link to ``target_id``. Who may mint is the caller's check (the route's); this decides
    the terms and stores the hash. A workspace link is an open workspace invite."""
    if kind not in TARGET_KINDS:
        raise LinkError(f"a link opens one of {', '.join(TARGET_KINDS)}", status=400)
    n, e = terms(max_uses, expires_in_sec)
    if kind == "workspace":
        try:
            m = membership_mod.mint_invite(root, target_id, role=role, created_by=created_by,
                                           expires_in_sec=e, max_uses=n, mode="open", now=now)
        except membership_mod.MembershipError as exc:
            raise LinkError(str(exc), status=exc.status) from exc
        return MintedLink(id=m.id, token=m.token, kind=kind, target_id=target_id, role=m.role,
                          max_uses=m.max_uses, expires_at=m.expires_at)
    if chat_target() is None:
        raise LinkError("chat links are not available on this deployment yet", status=501)
    role = "write" if role in ("contributor", "write", "owner") else "read"
    t = now if now is not None else time.time()
    token = secrets.token_urlsafe(32)
    rec = {"id": secrets.token_hex(8), "hash": membership_mod.hash_token(token), "kind": "chat",
           "role": role, "mode": "open", "expires_at": int(t + e), "max_uses": n, "uses": 0,
           "created_by": created_by, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "revoked": False}
    with _lock(f"chat:{target_id}"):
        rows = _read_chat(root, target_id)
        rows.append(rec)
        _write_chat(root, target_id, rows)
    return MintedLink(id=rec["id"], token=token, kind="chat", target_id=target_id, role=role,
                      max_uses=n, expires_at=rec["expires_at"])


def find(root: Path, token: str) -> Optional[tuple[str, str, dict]]:
    """``(kind, target_id, record)`` for ``token``, or None. Matched on the hash only."""
    tok = str(token or "").strip()
    if not tok:
        return None
    found = membership_mod.find_invite(Path(root), tok)
    if found is not None:
        return "workspace", found[0], found[1]
    h = membership_mod.hash_token(tok)
    for cid in _chats_with_links(root):
        for rec in _read_chat(root, cid):
            if rec.get("hash") == h:
                return "chat", cid, rec
    return None


def why_not(rec: dict, *, now: Optional[float] = None) -> Optional[str]:
    """``revoked`` · ``expired`` · ``used_up``, or None while the link still opens."""
    t = now if now is not None else time.time()
    if rec.get("revoked"):
        return "revoked"
    if int(rec.get("expires_at", 0)) < t:
        return "expired"
    if int(rec.get("uses", 0)) >= int(rec.get("max_uses", 1)):
        return "used_up"
    return None


def redeem_chat(root: Path, chat_id: str, token: str, *, subject: str, email: str = "",
                now: Optional[float] = None) -> dict:
    """Let ``subject`` into ``chat_id`` by its link: validated, joined through the chat domain, one
    use counted for a person who was not already in. Same atomicity as a workspace accept."""
    target = chat_target()
    if target is None:
        raise LinkError("chat links are not available on this deployment yet", status=501)
    h = membership_mod.hash_token(str(token or ""))
    with _lock(f"chat:{chat_id}"):
        rows = _read_chat(root, chat_id)
        rec = next((r for r in rows if r.get("hash") == h), None)
        if rec is None:
            raise LinkError("invalid link", status=404)
        reason = why_not(rec, now=now)
        if reason in ("revoked", "expired"):
            raise LinkError(f"link {reason}", status=410)
        # A person already in is let through without spending a use, even on a spent link.
        already = bool(target.is_in(subject, chat_id))
        if reason == "used_up" and not already:
            raise LinkError("link fully used", status=410)
        if not already:
            target.join(subject, email, chat_id, str(rec.get("role") or "read"))
            rec["uses"] = int(rec.get("uses", 0)) + 1
            _write_chat(root, chat_id, rows)
    return {"kind": "chat", "chat_id": chat_id, "role": rec.get("role"), "already_member": already,
            "created_by": rec.get("created_by")}


def listing(root: Path, kind: str, target_id: str, *, now: Optional[float] = None) -> list[dict]:
    """A target's links WITHOUT their hashes, each with its ``state`` (live · revoked · expired ·
    used_up) — the token itself is never stored, so a link's URL is shown only once, at mint."""
    rows = (membership_mod.list_invites(root, target_id) if kind == "workspace"
            else [{k: v for k, v in r.items() if k != "hash"} for r in _read_chat(root, target_id)])
    out = []
    for r in rows:
        if kind == "workspace" and r.get("mode") != "open":
            continue          # an invite bound to one address is a person's invite, not a link
        out.append({"id": r.get("id"), "kind": kind, "target_id": target_id, "role": r.get("role"),
                    "uses": int(r.get("uses", 0)), "max_uses": int(r.get("max_uses", 1)),
                    "expires_at": r.get("expires_at"), "created_by": r.get("created_by"),
                    "created_at": r.get("created_at"), "state": why_not(r, now=now) or "live"})
    return out


def revoke(root: Path, kind: str, target_id: str, link_id: str) -> None:
    if kind == "workspace":
        try:
            membership_mod.revoke_invite(root, target_id, link_id)
        except membership_mod.MembershipError as exc:
            raise LinkError(str(exc), status=exc.status) from exc
        return
    with _lock(f"chat:{target_id}"):
        rows = _read_chat(root, target_id)
        rec = next((r for r in rows if r.get("id") == link_id), None)
        if rec is None:
            raise LinkError("unknown link", status=404)
        if not rec.get("revoked"):
            rec["revoked"] = True
            _write_chat(root, target_id, rows)
