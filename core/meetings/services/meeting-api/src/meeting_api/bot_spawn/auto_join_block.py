"""Auto-join block list — meetings the calendar bot must never join on its own.

Auto-join sends a bot to every planned meeting whose time arrives. Some organisations gate their
calls: a bot knocking on the lobby is unwelcome even when a person would be admitted. This module
answers one question for the sweep: does this planned meeting touch a blocked party?

Two lists feed it, and either one blocks:

* the DEPLOYMENT's, from ``VEXA_AUTO_JOIN_BLOCK`` (operator configuration, parsed at boot);
* the OWNER's, ``auto_join_block`` on their identity record (``PUT /user/calendar``), delivered on
  the same ``/internal/users/{id}/bot-context`` answer the sweep already fetches.

An entry is either a DOMAIN (``example.com``: matches that domain and every subdomain) or an EMAIL
ADDRESS (``person@example.com``: matches exactly). A meeting is blocked when an entry matches:

* the organiser's address (the calendar event's ``ORGANIZER``),
* any invited address (the event's ``ATTENDEE`` lines, ``data.attendees``), or
* the host of the meeting link (``meet.example.com`` for a self-hosted Jitsi, for example).

"Touching" a blocked party is enough, deliberately: a gated organisation's people attending a call
someone else organised is the same exposure. The block applies to AUTO-JOIN only — a person who
sends a bot with "Send bot now" or ``POST /bots`` has decided, and is not second-guessed here.
"""
from __future__ import annotations

import os
import re
from typing import Iterable, Optional
from urllib.parse import urlsplit

BLOCK_ENV = "VEXA_AUTO_JOIN_BLOCK"

#: Most entries one list may hold. The per-user list is capped at the same number by identity.
MAX_ENTRIES = 200

_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_LOCAL = re.compile(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}")


class BlockList:
    """Parsed entries: ``domains`` (each matches itself and its subdomains) and exact ``emails``."""

    __slots__ = ("domains", "emails")

    def __init__(self, domains: Iterable[str] = (), emails: Iterable[str] = ()):
        self.domains = frozenset(domains)
        self.emails = frozenset(emails)

    def __bool__(self) -> bool:
        return bool(self.domains or self.emails)

    def _domain_hit(self, host: str) -> Optional[str]:
        host = host.strip().rstrip(".").lower()
        for domain in self.domains:
            if host == domain or host.endswith("." + domain):
                return domain
        return None

    def match_email(self, email: str) -> Optional[str]:
        """The entry ``email`` matches, or None."""
        email = (email or "").strip().lower()
        if email.startswith("mailto:"):
            email = email[7:]
        if "@" not in email:
            return None
        if email in self.emails:
            return email
        return self._domain_hit(email.rsplit("@", 1)[1])

    def match_host(self, host: str) -> Optional[str]:
        return self._domain_hit(host or "") if host else None


def _valid_domain(text: str) -> bool:
    return (len(text) <= 253 and "." in text
            and all(_LABEL.fullmatch(label) for label in text.split(".")))


def normalize_entry(entry: str) -> str:
    """One entry, lowercased and checked, or ``ValueError`` naming it. A leading ``@`` or ``*.`` on
    a domain is accepted and dropped (``@example.com`` and ``*.example.com`` mean ``example.com``)."""
    item = (entry or "").strip().lower().rstrip(".")
    if item.startswith("*."):
        item = item[2:]
    elif item.startswith("@"):
        item = item[1:]
    if "@" in item:
        local, _, domain = item.rpartition("@")
        if _LOCAL.fullmatch(local) and _valid_domain(domain):
            return item
        raise ValueError(f"{entry!r} is not an email address")
    if _valid_domain(item):
        return item
    raise ValueError(f"{entry!r} is not a domain (example.com) or an email address")


def parse_block(raw, *, what: str = BLOCK_ENV) -> BlockList:
    """A comma/space-separated string or a list of entries → ``BlockList``; ``ValueError`` naming
    the first bad entry, or more than ``MAX_ENTRIES``."""
    items = re.split(r"[\s,]+", raw.strip()) if isinstance(raw, str) else list(raw or [])
    domains: list[str] = []
    emails: list[str] = []
    for entry in items:
        if not isinstance(entry, str):
            raise ValueError(f"{what}: {entry!r} is not text")
        if not entry.strip():
            continue
        try:
            item = normalize_entry(entry)
        except ValueError as e:
            raise ValueError(f"{what}: {e}") from None
        (emails if "@" in item else domains).append(item)
    if len(domains) + len(emails) > MAX_ENTRIES:
        raise ValueError(f"{what}: at most {MAX_ENTRIES} entries")
    return BlockList(domains, emails)


def deployment_block(raw: Optional[str] = None) -> BlockList:
    """The deployment's list from ``VEXA_AUTO_JOIN_BLOCK`` (``raw`` is the test seam)."""
    return parse_block(os.getenv(BLOCK_ENV, "") if raw is None else raw)


def _organisers(data: dict) -> list[str]:
    """Every ORGANIZER address the row's calendar sources carry (the occurrence and its series)."""
    out: list[str] = []
    sources = data.get("calendar_sources")
    for source in sources if isinstance(sources, list) else []:
        event = source.get("event") if isinstance(source, dict) else None
        if not isinstance(event, dict):
            continue
        for part in ("component", "series_master"):
            comp = event.get(part)
            props = comp.get("properties") if isinstance(comp, dict) else None
            for entry in (props or {}).get("ORGANIZER") or []:
                value = entry.get("value") if isinstance(entry, dict) else None
                if isinstance(value, str) and value.strip():
                    out.append(value.strip())
    return out


def _attendees(data: dict) -> list[str]:
    raw = data.get("attendees")
    return [a["email"] for a in raw if isinstance(a, dict) and isinstance(a.get("email"), str)] \
        if isinstance(raw, list) else []


def _link_hosts(row: dict, data: dict) -> list[str]:
    hosts: list[str] = []
    for url in (data.get("constructed_meeting_url"), data.get("meeting_url"), row.get("meeting_url")):
        if isinstance(url, str) and url.strip():
            try:
                host = urlsplit(url.strip()).hostname
            except ValueError:
                host = None
            if host and host not in hosts:
                hosts.append(host)
    return hosts


def blocked_reason(row: dict, *lists: Optional[BlockList]) -> Optional[str]:
    """Why this planned meeting must not be auto-joined, or None. The reason names the matching
    entry and which party matched; it is written on the row, so the owner sees it."""
    active = [b for b in lists if b]
    if not active:
        return None
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    checks = ([("organiser", e, "email") for e in _organisers(data)]
              + [("invitee", e, "email") for e in _attendees(data)]
              + [("meeting link host", h, "host") for h in _link_hosts(row, data)])
    for party, value, kind in checks:
        for block in active:
            hit = block.match_email(value) if kind == "email" else block.match_host(value)
            if hit:
                return (f"auto-join is blocked for this meeting: the {party} matches the block-list "
                        f"entry {hit!r}. Send the bot by hand if it should join.")
    return None
