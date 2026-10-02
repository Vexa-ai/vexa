"""The INBOX SEAM — one poller, two sources, one set of facts.

`mailbox.py` polls an INBOX. Which inbox technology that is must not reach the parser, the
routing decision, the admission or the flows: both sources fetch the RAW RFC822 source and hand
back the same `InboundMessage`, so a Gmail invite and a mailpit invite produce byte-identical
facts.

    VEXA_MAIL_INBOX = imap     (default) — IMAP against imap.gmail.com, exactly as before
                    = mailpit           — the dev stack's mail double, REST only (no IMAP/POP3)
    VEXA_MAILPIT_URL           — mailpit's HTTP base (default http://127.0.0.1:8025)
    VEXA_MAIL_ADDR             — the address this inbox answers as; mailpit filters on it
    VEXA_MAILPIT_LOOKBACK_S    — re-scan window behind the watermark (default 300)

Contracts both sources keep — they are the ones the live witness paid for, so a source that
breaks any of them is a regression, not a variant:

  C1  **durable cursor** — a restart resumes where it stopped and NEVER re-admits; on first boot
      the cursor anchors at the CURRENT tail, so an existing mailbox is not replayed.
  C2  **threading** — inbound In-Reply-To/References are surfaced verbatim, so a reply routes by
      THREAD (the `mail_thread` row), never by sender.
  C3  **ICS attachments** are read and decoded — the invite is an attachment, not a body.
  C4  **no sleeping, no internal polling** — `fetch()` returns what is there and stops.

Why mailpit needs more than an integer: IMAP UIDs are monotonic, so one number is a complete
position. Mailpit IDs are random base62 (`6bdn12ZorjbbiXdRJuW3xR`) and carry no order at all, so
the position is a **`Created` watermark plus a seen-ID set**, both persisted — the watermark bounds
the scan, the set makes the re-scan window idempotent. Both live next to the IMAP cursor:
`mail_cursor.token` holds the watermark (the same nullable TEXT column the Exchange/Graph seam in
Vexa-ai/vexa#1318 adds, deliberately — the two converge on merge) and `mail_seen` holds the ids.

Stdlib only (imaplib/urllib), matching `flows_steps/common.py`'s `http()` idiom.
"""
from __future__ import annotations

import calendar
import email as email_mod
import email.utils
import imaplib
import json
import math
import os
import re
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Iterator

# Exactly the two content types and the exact suffix test the IMAP poller has always used — a
# widened match here would change the facts the Gmail path produces, which is the one thing this
# seam may not do.
ICS_TYPES = ("text/calendar", "application/ics")

PAGE = 200          # mailpit list page size
MAX_SCAN = 2000     # never walk further back than this many messages in one poll


@dataclass(frozen=True)
class InboundMessage:
    """One inbound mail, source-independent.

    `cursor` is the source-native position AFTER this message; the poller persists it once the
    message has been routed (C1). `ext_id` is the source-native id — IMAP UID or mailpit ID —
    and is only ever a fallback dedup key for a message with no Message-ID."""
    cursor: str
    ext_id: str
    message_id: str
    frm: str
    subject: str
    headers: dict = field(default_factory=dict)
    body: str = ""
    ics: str | None = None


def from_rfc822(raw: bytes, *, cursor: str, ext_id: str) -> InboundMessage:
    """RFC822 bytes → InboundMessage. THE shared parse: both sources go through this function and
    nothing else, which is what makes the two paths produce identical facts."""
    msg = email_mod.message_from_bytes(raw)
    body, ics = "", None
    for part in msg.walk():
        ct = part.get_content_type()
        if ct == "text/plain" and not body:
            body = (part.get_payload(decode=True) or b"").decode(errors="replace")
        if ct in ICS_TYPES or (part.get_filename() or "").endswith(".ics"):
            ics = (part.get_payload(decode=True) or b"").decode(errors="replace")
    return InboundMessage(
        cursor=cursor, ext_id=ext_id,
        message_id=(msg.get("Message-ID", "") or "").strip(),
        frm=email_mod.utils.parseaddr(msg.get("From", ""))[1].lower(),
        subject=msg.get("Subject", "") or "",
        headers=dict(msg.items()), body=body, ics=ics)


# ---------------------------------------------------------------------------------------------
# Timestamps. Mailpit renders `Created` as Go's RFC3339Nano, which TRIMS trailing zeros — `.5Z`
# and `.503Z` are both possible, and comparing those as strings orders them wrongly. So every
# comparison goes through epochs, and every stored watermark is normalised to fixed-width
# microseconds, which then IS safely string-ordered (the pruning DELETE relies on that).
# ---------------------------------------------------------------------------------------------
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2}):(\d{2})"
                  r"(?:\.(\d+))?(Z|z|[+-]\d{2}:?\d{2})?$")


def iso_epoch(s: str) -> float:
    m = _ISO.match((s or "").strip())
    if not m:
        return 0.0
    y, mo, d, h, mi, sec, frac, tz = m.groups()
    ep = float(calendar.timegm((int(y), int(mo), int(d), int(h), int(mi), int(sec), 0, 1, -1)))
    if frac:
        ep += float("0." + frac)
    if tz and tz not in ("Z", "z"):
        off = tz[1:].replace(":", "")
        delta = int(off[:2]) * 3600 + int(off[2:4] or 0) * 60
        ep -= delta if tz[0] == "+" else -delta
    return ep


def iso_norm(value) -> str:
    """Any accepted timestamp (or epoch) → `YYYY-MM-DDTHH:MM:SS.ffffffZ`, fixed width."""
    ep = float(value) if isinstance(value, (int, float)) else iso_epoch(value)
    whole = math.floor(ep)
    micro = int(round((ep - whole) * 1_000_000))
    if micro >= 1_000_000:
        whole, micro = whole + 1, 0
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(whole)) + f".{micro:06d}Z"


class Inbox:
    """The seam. Five methods; nothing else may vary between sources."""

    name = "abstract"

    def address(self) -> str:
        raise NotImplementedError

    def restore(self, db) -> str | None:
        """The persisted position, or None on first boot."""
        raise NotImplementedError

    def anchor(self, db, cursor: str) -> None:
        """Persist the first-boot position (C1: history is never replayed)."""
        raise NotImplementedError

    def tail_cursor(self) -> str:
        raise NotImplementedError

    def fetch(self, cursor: str) -> Iterator[InboundMessage]:
        raise NotImplementedError

    def commit(self, db, msg: InboundMessage) -> None:
        """Persist the position AFTER this message has been routed."""
        raise NotImplementedError


# ---------------------------------------------------------------------------------------------
# IMAP — the original wiring, moved behind the seam and otherwise untouched: same host, same
# UID search, same `n:*` guard, same single-integer cursor row.
# ---------------------------------------------------------------------------------------------
class ImapInbox(Inbox):
    name = "imap"
    host = "imap.gmail.com"
    folder = "INBOX"

    def _creds(self):
        from flows_steps import emailx as mx
        return mx.creds()

    def address(self) -> str:
        return self._creds()[0]

    def restore(self, db) -> str | None:
        row = db.execute("SELECT uid FROM mail_cursor WHERE id = 1")
        return str(row[0][0]) if row else None

    def anchor(self, db, cursor: str) -> None:
        db.execute("INSERT INTO mail_cursor (id, uid) VALUES (1, :u) ON CONFLICT (id) DO NOTHING",
                   {"u": int(cursor)})

    def _open(self):
        addr, pw = self._creds()
        im = imaplib.IMAP4_SSL(self.host)
        im.login(addr, pw)
        im.select(self.folder)
        return im

    def tail_cursor(self) -> str:
        with self._open() as im:
            _, d = im.uid("search", None, "ALL")
            uids = d[0].split() if d and d[0] else []
            return str(int(uids[-1])) if uids else "0"

    def fetch(self, cursor: str) -> Iterator[InboundMessage]:
        low = int(cursor or 0)
        with self._open() as im:
            _, d = im.uid("search", None, f"UID {low + 1}:*")
            for raw in (d[0].split() if d and d[0] else []):
                uid = int(raw)
                if uid <= low:
                    continue          # IMAP's `n:*` returns the last UID even when the range is empty
                _, md = im.uid("fetch", raw, "(RFC822)")
                yield from_rfc822(md[0][1], cursor=str(uid), ext_id=str(uid))

    def commit(self, db, msg: InboundMessage) -> None:
        db.execute("UPDATE mail_cursor SET uid = :u WHERE id = 1", {"u": int(msg.cursor)})


# ---------------------------------------------------------------------------------------------
# Mailpit — the dev stack's mail double. REST only: no IMAP, no POP3 on this deployment.
# ---------------------------------------------------------------------------------------------
class MailpitInbox(Inbox):
    name = "mailpit"

    def __init__(self, base_url: str | None = None, addr: str | None = None,
                 opener: Callable[[str], bytes] | None = None,
                 lookback_s: float | None = None) -> None:
        self.base = (base_url or os.environ.get("VEXA_MAILPIT_URL")
                     or "http://127.0.0.1:8025").rstrip("/")
        self.addr = (addr or os.environ.get("VEXA_MAIL_ADDR") or "").strip().lower()
        if not self.addr:
            raise ValueError("VEXA_MAIL_ADDR is required for VEXA_MAIL_INBOX=mailpit — it is the "
                             "recipient this inbox answers as, and mailpit accepts every address")
        self._open = opener or self._urlopen
        self.lookback = float(lookback_s if lookback_s is not None
                              else os.environ.get("VEXA_MAILPIT_LOOKBACK_S", "300"))
        self._seen: set[str] = set()

    # --- transport ---------------------------------------------------------------------------
    def _urlopen(self, path: str) -> bytes:  # pragma: no cover — exercised against live mailpit
        req = urllib.request.Request(self.base + path, method="GET")
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read()

    def _json(self, path: str) -> dict:
        raw = self._open(path)
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    def address(self) -> str:
        return self.addr

    # --- cursor ------------------------------------------------------------------------------
    @staticmethod
    def ensure_schema(db) -> None:
        """`schema.sql` is CREATE TABLE IF NOT EXISTS with no migration runner, so a database that
        was deployed before this seam has a `mail_cursor` with no `token` column. Add it — one
        idempotent ALTER, run only on the mailpit path, so an IMAP deployment is never touched."""
        try:
            db.execute("SELECT token FROM mail_cursor WHERE id = 1")
        except Exception:  # noqa: BLE001 — "column does not exist" is a schema fact, not a failure
            db.execute("ALTER TABLE mail_cursor ADD COLUMN token TEXT")

    def restore(self, db) -> str | None:
        self.ensure_schema(db)
        row = db.execute("SELECT token FROM mail_cursor WHERE id = 1")
        token = (row[0][0] if row else None) or None
        if token:
            floor = iso_norm(iso_epoch(token) - self.lookback)
            self._seen = {r[0] for r in db.execute(
                "SELECT ext_id FROM mail_seen WHERE source = :s AND created >= :f",
                {"s": self.name, "f": floor})}
        return token

    def anchor(self, db, cursor: str) -> None:
        """Anchoring is not just a watermark here: everything already in the box AT OR BEFORE the
        anchor is marked seen. That makes the two boot cases one rule — anchoring at the tail
        skips the double's whole rehearsal history (C1), while anchoring at an operator-supplied
        earlier position still admits everything after it."""
        self.ensure_schema(db)
        at = iso_norm(cursor)
        db.execute("INSERT INTO mail_cursor (id, uid, token) VALUES (1, 0, :t) "
                   "ON CONFLICT (id) DO UPDATE SET token = :t", {"t": at})
        for created, m in self._scan(iso_epoch(at) - self.lookback):
            if created <= at:
                self._mark_seen(db, m.get("ID") or "", created)

    def tail_cursor(self) -> str:
        """The newest message's timestamp, or now — first boot never replays the double's history
        (a rig mailbox holds every rehearsal that came before)."""
        msgs = (self._json(f"/api/v1/messages?limit=1&start=0").get("messages") or [])
        return iso_norm(msgs[0].get("Created", "")) if msgs else iso_norm(time.time())

    def _mark_seen(self, db, ext_id: str, created: str) -> None:
        db.execute("INSERT INTO mail_seen (source, ext_id, created, seen_at) "
                   "VALUES (:s, :e, :c, :t) ON CONFLICT (source, ext_id) DO NOTHING",
                   {"s": self.name, "e": ext_id, "c": created, "t": time.time()})
        self._seen.add(ext_id)

    def commit(self, db, msg: InboundMessage) -> None:
        created = msg.cursor
        self._mark_seen(db, msg.ext_id, created)
        row = db.execute("SELECT token FROM mail_cursor WHERE id = 1")
        held = (row[0][0] if row else None) or ""
        if iso_epoch(created) >= iso_epoch(held):
            db.execute("UPDATE mail_cursor SET token = :t WHERE id = 1", {"t": created})
            # anything older than the re-scan window can never be fetched again
            db.execute("DELETE FROM mail_seen WHERE source = :s AND created < :f",
                       {"s": self.name, "f": iso_norm(iso_epoch(created) - self.lookback - 86400)})

    # --- fetch -------------------------------------------------------------------------------
    def _for_us(self, m: dict) -> bool:
        for field_name in ("To", "Cc", "Bcc"):
            for who in (m.get(field_name) or []):
                if (who.get("Address") or "").strip().lower() == self.addr:
                    return True
        return False

    def _scan(self, floor: float) -> list[tuple[str, dict]]:
        """Every listed message at or after `floor`, oldest first. Mailpit lists newest first, so
        the walk stops at the first message that falls out of the window."""
        picked: list[tuple[str, dict]] = []
        start = 0
        while start < MAX_SCAN:
            msgs = self._json(f"/api/v1/messages?limit={PAGE}&start={start}").get("messages") or []
            if not msgs:
                break
            done = False
            for m in msgs:
                created = iso_norm(m.get("Created", ""))
                if iso_epoch(created) < floor:
                    done = True
                    break
                picked.append((created, m))
            if done or len(msgs) < PAGE:
                break
            start += PAGE
        picked.sort(key=lambda t: (t[0], t[1].get("ID", "")))
        return picked

    def fetch(self, cursor: str) -> Iterator[InboundMessage]:
        for created, m in self._scan(iso_epoch(cursor) - self.lookback):
            mid = m.get("ID") or ""
            if mid in self._seen or not self._for_us(m):
                continue
            raw = self._open(f"/api/v1/message/{mid}/raw")
            yield from_rfc822(raw if isinstance(raw, bytes) else raw.encode(),
                              cursor=created, ext_id=mid)


def get_inbox() -> Inbox:
    kind = (os.environ.get("VEXA_MAIL_INBOX") or "imap").strip().lower()
    if kind in ("", "imap", "gmail"):
        return ImapInbox()
    if kind == "mailpit":
        return MailpitInbox()
    raise ValueError(f"VEXA_MAIL_INBOX={kind!r} — expected 'imap' (default) or 'mailpit'")
