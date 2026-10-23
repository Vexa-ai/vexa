"""person_resolution.py — one Person, many addresses, as agent-api sees it (PRD 0.13.4 D35, I10).

There is no persons table here or anywhere. The human is the CRM's Person record; an account is a
login that resolves to it by its verified address (ADR-0047 d3 match identifiers). agent-api asks the
CRM through its existing front door, ``POST /internal/persons`` on crm-api (internal secret), with the
accounts it is about to show or grant to, each with the address its roster entry carries. A roster
address is what someone was invited or listed under, never one a sign-in proved, so it is sent without
``email_verified`` and the CRM finds each Person by the account itself: the link its own sign-in with a
proven address wrote.

What it is used for, and the one rule each use keeps:

- **the picker** (tag, invite, share, card member): ``group_people`` folds rows that resolve to one
  Person into one row, ``accounts: N`` when the human has several logins. It shows only what the
  viewer already had: the row's address and id are ones the viewer's own set carried; accounts
  outside that set are COUNTED, never named (their ids and addresses are the Person's private match
  identifiers, D4 ``$match_ids``).
- **grants** (a chat participant, a workspace member): ``linked_accounts`` — the account picked and
  the accounts the person CONFIRMED with it from each login's own session; never an unconfirmed
  link (coordinator ruling 2026-10-17). Before confirmation the picker only hints "2 accounts?".
- **delivery** (invites and nudges): ``primary_address`` — the address the person chose on their
  merge card, else the account's own.

OFF unless ``CRM_API_URL`` is set (no CRM: every account is a Person of one, exactly the old
behaviour). It FAILS TOWARD FEWER GRANTS: when the CRM cannot answer, nothing is grouped and no grant
reaches a second account — never more people than before. Answers are cached for 30 s.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, Iterable, Optional

logger = logging.getLogger("agent_api.person_resolution")

CRM_URL_ENV = "CRM_API_URL"
PATH = "/internal/persons"
PROFILES_PATH = "/internal/profiles"
PUBLIC_PATH = "/internal/profiles/public"
TIMEOUT_S = 3.0
TTL_S = 30.0
MAX_ACCOUNTS = 500


class Resolver:
    """``persons(accounts)`` → ``{subject: {person, accounts, primary, merged}}``; ``{}`` when off or
    unreachable. ``post`` is injectable (tests): ``post(path, json) -> dict``."""

    def __init__(self, base: str = "", secret: str = "", *, post: Optional[Callable[[str, dict], dict]] = None,
                 ttl: float = TTL_S, clock: Callable[[], float] = time.monotonic) -> None:
        self.base = (base or "").strip().rstrip("/")
        self.secret = secret or ""
        self._post = post
        self.ttl = ttl
        self.clock = clock
        self._cache: dict = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self._post is not None or (self.base and self.secret))

    def _call(self, body: dict, path: str = PATH) -> dict:
        if self._post is not None:
            return self._post(path, body)
        import httpx  # noqa: PLC0415
        r = httpx.post(self.base + path, json=body, timeout=TIMEOUT_S,
                       headers={"X-Internal-Secret": self.secret})
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        return r.json()

    def profiles(self, accounts: Iterable[dict]) -> dict:
        """Profile fields (a claimed Person record's person-owned half) per account, or None. The
        CALLER applies the directory rule first; this never answers an org field."""
        if not self.enabled:
            return {}
        want = [{"subject": str(a.get("subject") or ""), "email": str(a.get("email") or "").lower()}
                for a in accounts or [] if a and a.get("subject")][:MAX_ACCOUNTS]
        if not want:
            return {}
        try:
            return dict((self._call({"accounts": want}, PROFILES_PATH) or {}).get("profiles") or {})
        except Exception as exc:  # noqa: BLE001
            logger.warning("profiles unavailable (%s)", type(exc).__name__)
            return {}

    def public_profile(self, handle: str) -> Optional[dict]:
        """A public page the person switched on, with only the fields they marked; None otherwise."""
        if not self.enabled or not handle:
            return None
        try:
            page = self._call({"handle": str(handle)}, PUBLIC_PATH) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("public profile unavailable (%s)", type(exc).__name__)
            return None
        return page if page.get("handle") else None

    def persons(self, accounts: Iterable[dict], *, fresh: bool = False) -> dict:
        if not self.enabled:
            return {}
        want = []
        for a in accounts or []:
            sub = str((a or {}).get("subject") or "").strip()
            if sub and not any(w["subject"] == sub for w in want):
                want.append({"subject": sub, "email": str((a or {}).get("email") or "").strip().lower()})
        if not want:
            return {}
        want = want[:MAX_ACCOUNTS]
        key = tuple(sorted((w["subject"], w["email"]) for w in want))
        now = self.clock()
        with self._lock:
            hit = None if fresh else self._cache.get(key)
            if hit and now - hit[0] < self.ttl:
                return hit[1]
        try:
            out = dict((self._call({"accounts": want}) or {}).get("persons") or {})
        except Exception as exc:  # noqa: BLE001 — no answer groups nothing and grants nobody extra
            logger.warning("person resolution unavailable (%s): every account is its own person", type(exc).__name__)
            return {}
        with self._lock:
            if len(self._cache) > 256:
                self._cache.clear()
            self._cache[key] = (now, out)
        return out


_default: Optional[Resolver] = None


def default() -> Resolver:
    """The process's resolver, from ``CRM_API_URL`` and the internal-tier secret."""
    global _default
    if _default is None:
        secret = (os.environ.get("INTERNAL_API_SECRET") or os.environ.get("VEXA_INTERNAL_API_SECRET") or "").strip()
        _default = Resolver(os.environ.get(CRM_URL_ENV, ""), secret)
    return _default


def group_people(rows: list[dict], resolver: Optional[Resolver]) -> list[dict]:
    """The picker's rows, one per Person. ``rows`` are the viewer's own people (``subject``, ``name``,
    ``email``, ``workspaces``…). Rows resolving to one Person become one: the row whose address is the
    Person's primary (else the first), the union of the shared workspaces, ``accounts`` = every account
    linked to the Person, and ``linked`` = the OTHER subjects of that Person the viewer's set already
    held (so the client can recognise them; nothing new is named). Rows with no Person pass through."""
    if not rows or resolver is None:
        return rows
    persons = resolver.persons(rows)
    if not persons:
        return rows
    out: list[dict] = []
    by_person: dict[str, dict] = {}
    for row in rows:
        sub = str(row.get("subject") or "")
        info = persons.get(sub) or {}
        pid = info.get("person")
        count = len(info.get("accounts") or [sub])
        if not pid:
            out.append(row)
            continue
        have = by_person.get(pid)
        if have is None:
            # "2 accounts" once the person confirmed them; "2 accounts?" (a hint, no grant) before
            merged = {**row, "accounts": count, "linked": [], "accounts_confirmed": bool(info.get("merged"))}
            by_person[pid] = merged
            out.append(merged)
            continue
        primary = str(info.get("primary") or "").lower()
        if primary and str(row.get("email") or "").lower() == primary and str(have.get("email") or "").lower() != primary:
            # the primary address leads the row; the earlier one becomes a linked account
            have["linked"].append(str(have["subject"]))
            for k in ("subject", "email", "name"):
                if row.get(k):
                    have[k] = row[k]
        else:
            have["linked"].append(sub)
        for w in row.get("workspaces") or []:
            have.setdefault("workspaces", [])
            if w not in have["workspaces"]:
                have["workspaces"].append(w)
        have["accounts"] = max(int(have.get("accounts") or 1), count)
    for row in out:
        if row.get("accounts") == 1 and not row.get("linked"):
            row.pop("accounts", None)
            row.pop("linked", None)
            row.pop("accounts_confirmed", None)
    return out


def linked_accounts(subject: str, email: str = "", resolver: Optional[Resolver] = None) -> list[str]:
    """Every account a grant to ``subject`` reaches: ``subject`` first, then the accounts the person
    CONFIRMED together with it on their merge card (``confirmed``). An unconfirmed link reaches
    nothing; just ``[subject]`` when the CRM is off or cannot answer."""
    subject = str(subject or "")
    if not subject:
        return []
    info = (resolver or default()).persons([{"subject": subject, "email": email}], fresh=True).get(subject) or {}
    out = [subject]
    for who in info.get("confirmed") or []:
        who = str(who)
        if who and who not in out:
            out.append(who)
    return out


def primary_address(subject: str, email: str = "", resolver: Optional[Resolver] = None) -> str:
    """Where an invite or a nudge to this account's Person goes: the primary address the person chose,
    else the account's own address."""
    info = (resolver or default()).persons([{"subject": str(subject or ""), "email": email}]).get(str(subject or "")) or {}
    return str(info.get("primary") or email or "").strip().lower()


def union_memberships(root, subject: str, email: str, index, resolver: Optional[Resolver] = None) -> dict:
    """After the person confirmed their merge card from every login (the CRM answers ``merged``), each
    of their accounts gets the workspaces any of them is in, so either address opens them. A role is
    copied up to contributor: owning a workspace stays with the account that owns it (an owner is made
    only by the owner act). Nothing happens unless the CRM says the person merged these accounts.
    Answers ``{merged, granted}`` (``granted`` = memberships added), never which workspaces or ids."""
    from control_plane import workspace_membership as membership_mod  # noqa: PLC0415
    info = (resolver or default()).persons([{"subject": str(subject), "email": email}], fresh=True).get(str(subject)) or {}
    accounts = [str(a) for a in info.get("accounts") or [] if str(a)]
    if not info.get("merged") or str(subject) not in accounts or len(accounts) < 2:
        return {"merged": False, "granted": 0}
    best: dict[str, str] = {}
    for who in accounts:
        try:
            rows, _ = membership_mod.reconciled_memberships(root, who, lambda s: list(index.list(s) or []))
        except Exception:  # noqa: BLE001 — an unreadable enumeration copies nothing for that account
            continue
        for row in rows:
            slug = str((row or {}).get("workspace_id") or "")
            role = membership_mod.is_member(root, slug, who) if slug else None
            if not role:
                continue
            role = "contributor" if role == "owner" else role
            if membership_mod._RANK.get(role, -1) > membership_mod._RANK.get(best.get(slug, ""), -1):
                best[slug] = role
    granted = 0
    for slug, role in sorted(best.items()):
        for who in accounts:
            held = membership_mod.is_member(root, slug, who)
            if held is not None and membership_mod._RANK.get(held, -1) >= membership_mod._RANK[role]:
                continue
            try:
                membership_mod.grant_membership(root, slug, who, role, added_by=str(subject), index=index)
                granted += 1
            except Exception:  # noqa: BLE001 — one workspace that refuses does not stop the rest
                logger.warning("merged accounts: a membership was not copied")
    return {"merged": True, "granted": granted}
