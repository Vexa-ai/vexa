"""The MAIL SENSOR — the edge between a shared mailbox and the bus (ADR-0050 shape, thin adapter).

Founder, 2026-10-14: inbound on `info@vexa.ai` is *"a standard flow edge script or worker, as we
defined it — this should just filter out non-invites or whatever system messages it works with."*
That mailbox is also where customers write. So the sensor:

  * is READ-ONLY and INCREMENTAL. It reads forward from its cursor and never marks, moves, labels,
    copies or deletes anything (IMAP: the folder is opened with EXAMINE and bodies are fetched with
    `BODY.PEEK[]`, so not even `\\Seen` changes — `inbox.ImapInbox`). The support sweep and the people
    who read that inbox see exactly what they saw before.
  * runs a CHEAP, NON-LLM FILTER that keeps only what Vexa acts on:
      - a meeting invitation (a `text/calendar` part with a VEVENT),
      - a reply to mail Vexa sent (In-Reply-To or any References id is a Message-ID Vexa sent:
        `mail_thread` or the outbound audit `mail_send`),
    and DROPS EVERYTHING ELSE AT THE EDGE: no event, no row, no model, no body kept — one counter.
  * EMITS through the existing intake (`mailbox.handle`), which still applies the allow-list, the
    invite policy and the quarantine for what passed the filter (an invite from an organizer nobody
    can place, a forged thread).

THE INTERFACE is the ADR-0050 sensor's — `sensor_id`, `owner`, `cursor`, `filter`, `emit` — so this
folds into the flows-sensors framework when it lands (one sensor per mailbox, owned by the global
account) without changing what it keeps or drops. `dry_run` is the framework's `sensor_test`: what
the sensor WOULD emit and drop for a set of messages, with nothing emitted.

`VEXA_MAIL_INBOUND_FILTER=all` restores the previous behaviour (every message routed) for a
deployment whose mailbox is Vexa's alone and whose users mail it directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Optional

import flows_config as cfg

KEEP_INVITE = "invite"
KEEP_REPLY = "reply"
KEEP_ALL = "all"
FILTERS = ("vexa", "all")


def filter_mode() -> str:
    mode = (cfg.get("VEXA_MAIL_INBOUND_FILTER") or "vexa").strip().lower()
    return mode if mode in FILTERS else "vexa"


def _ids(headers: dict) -> list:
    out = []
    for key in ("In-Reply-To", "References"):
        for token in str((headers or {}).get(key) or "").split():
            token = token.strip()
            if token.startswith("<") and token.endswith(">") and token not in out:
                out.append(token)
    return out


def vexa_message(db, message_ids: Iterable[str]) -> bool:
    """Is any of these a Message-ID Vexa sent? Asked of the two places a sent id is recorded."""
    ids = [m for m in message_ids if m]
    if not ids:
        return False
    for mid in ids:
        if db.execute("SELECT 1 FROM mail_thread WHERE message_id = :m", {"m": mid}):
            return True
        try:
            if db.execute("SELECT 1 FROM mail_send WHERE message_id = :m", {"m": mid}):
                return True
        except Exception:  # noqa: BLE001 — a database without the audit table yet
            pass
    return False


def classify(db, msg, mode: Optional[str] = None) -> tuple[Optional[str], str]:
    """(kept-as, why) — `None` means DROP at the edge. Pure apart from the sent-id lookup."""
    mode = mode or filter_mode()
    if mode == "all":
        return KEEP_ALL, "VEXA_MAIL_INBOUND_FILTER=all"
    if msg.ics and "BEGIN:VEVENT" in msg.ics:
        return KEEP_INVITE, "a calendar invitation"
    if vexa_message(db, _ids(msg.headers)):
        return KEEP_REPLY, "a reply to mail Vexa sent"
    return None, "neither an invitation nor a reply to mail Vexa sent"


def _domain(addr: str) -> str:
    return (addr or "").rpartition("@")[2].lower() or "-"


@dataclass
class MailSensor:
    """One sensor per mailbox. `emit(msg)` is the intake (`mailbox.process`); `inbox` is the
    `inbox.Inbox` source, which owns the cursor's persistence."""
    sensor_id: str
    owner: str
    inbox: object
    emit: Callable
    mode: str = ""
    dropped: int = 0

    def filter(self, db, msg) -> tuple[Optional[str], str]:
        return classify(db, msg, self.mode or None)

    def tick(self, db, cursor: str) -> str:
        """Read forward from `cursor`, keep or drop each message, emit what is kept, advance the
        cursor past every message either way. Returns the new cursor."""
        for msg in self.inbox.fetch(cursor):
            kept, _why = self.filter(db, msg)
            if kept is None:
                self.dropped += 1
            else:
                self.emit(msg)
            cursor = msg.cursor
            self.inbox.commit(db, msg)
        return cursor

    def dry_run(self, db, messages: Iterable) -> dict:
        """`sensor_test`: what this sensor would emit and drop for these messages. Emits nothing,
        writes nothing, and reports each message by its sender's DOMAIN only — never an address, a
        subject or a body, because the mailbox is shared with people who are not Vexa's."""
        emitted, dropped = [], []
        for msg in messages:
            kept, why = self.filter(db, msg)
            row = {"from_domain": _domain(msg.frm), "why": why}
            if kept is None:
                dropped.append(row)
            else:
                emitted.append({**row, "event": ("invite.received" if kept == KEEP_INVITE
                                                 else "mail.reply" if kept == KEEP_REPLY
                                                 else "routed")})
        return {"sensor_id": self.sensor_id, "owner": self.owner,
                "filter": self.mode or filter_mode(),
                "would_emit": emitted, "dropped": dropped,
                "counts": {"emit": len(emitted), "drop": len(dropped)}}


def sensor_id_for(address: str) -> str:
    return f"mail:{(address or '').strip().lower()}"
