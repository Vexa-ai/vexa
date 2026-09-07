"""GRAPH INBOX — Microsoft 365 behind the same seam as IMAP and mailpit.

`VEXA_MAIL_INBOX=graph`. The poller, the parser, the routing decision, the admission and every
flow are unchanged: an Exchange-delivered invite and a Gmail-delivered invite become the same
`InboundMessage` and therefore the same facts.

Ported from PR Vexa-ai/vexa#1318 (transport seam + Graph transport), rebased onto the inbox seam
this line already has — which is where the two designs differ, and the line's is better:

  * **The cursor.** #1318 held Graph's position as `receivedDateTime gt <ISO>`, per message. Two
    messages that share a `receivedDateTime` (Graph's is second-granular, and a burst of
    invitations from one organizer will) make the second one unreachable forever, silently. This
    inbox uses the WATERMARK + `mail_seen` machinery `MailpitInbox` already established for
    exactly this class of source: an overlapping `ge` window, and idempotence from the id set.
  * **The schema.** #1318 added `mail_cursor.token`; this line already has it, and `mail_seen`
    besides. Nothing is added here — no migration, no drift.
  * **`delta`.** #1318's optional delta-link mode is DROPPED. Its own comment concedes a delta
    link is only valid once a whole page-set is drained, so a crash mid-batch re-delivers — which
    is what the watermark does anyway, without a second cursor shape to reason about.

⚠ **NO LIVE TENANT HAS EVER ANSWERED THIS CODE** — see `graph_client.py`. Everything below is
exercised against a fake HTTP layer.
"""
from __future__ import annotations

from typing import Iterator

from flows_integrations.graph_client import GraphClient, client as graph_client
from flows_integrations.inbox import (
    Inbox,
    InboundMessage,
    advance_watermark,
    ensure_token_column,
    iso_epoch,
    iso_norm,
    load_seen,
    mark_seen,
    read_watermark,
    write_watermark,
)


def _esc(v: str) -> str:
    """ICS is line-oriented; an embedded newline would end the property mid-value."""
    return str(v or "").replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "")


def synthesize_ics(m: dict) -> str | None:
    """Exchange strips iMIP: a meeting request often arrives as an `eventMessageRequest` with the
    calendar data lifted OUT of MIME into message PROPERTIES and no `.ics` part at all. Rebuild
    the minimum VEVENT the parser needs.

    Shape per the Graph `eventMessage` resource (`meetingMessageType` · `startDateTime` ·
    `endDateTime` · `location`, each a DateTimeTimeZone whose `timeZone` may be a WINDOWS name,
    which `outlook.resolve_tzid` handles).

    ⚠ **The least-verified piece in this port.** Written from the documented resource shape,
    exercised against fixtures only, never against a live Exchange tenant."""
    if (m.get("meetingMessageType") or "") != "meetingRequest":
        return None
    st = m.get("startDateTime") or {}
    dt = (st.get("dateTime") or "")[:19].replace("-", "").replace(":", "")
    if len(dt) != 15:
        return None
    tz = st.get("timeZone") or ""
    tzpart = "" if tz.upper() == "UTC" else f';TZID="{tz}"'
    stamp = "Z" if tz.upper() == "UTC" else ""
    where = ((m.get("location") or {}).get("displayName") or "")
    body = ((m.get("body") or {}).get("content") or "")
    organizer = ((m.get("from") or {}).get("emailAddress") or {}).get("address", "")
    # UID: the invite's own iCalUId is not exposed on the message, so the occurrence key falls
    # back to the internetMessageId — one admission per delivered request, which is the same
    # guarantee, and a re-sent update arrives as a distinct message anyway.
    uid = (m.get("internetMessageId") or m.get("id") or "").strip().strip("<>")
    return "\r\n".join([
        "BEGIN:VCALENDAR", "PRODID:-//Vexa//graph-inbox//EN", "VERSION:2.0", "METHOD:REQUEST",
        "BEGIN:VEVENT", f"UID:{uid}", f"DTSTART{tzpart}:{dt}{stamp}",
        f"ORGANIZER:mailto:{organizer}", f"SUMMARY:{_esc(m.get('subject') or 'Meeting')}",
        f"LOCATION:{_esc(where)}", f"DESCRIPTION:{_esc(body[:900])}",
        "END:VEVENT", "END:VCALENDAR", ""])


class GraphInbox(Inbox):
    name = "graph"

    def __init__(self, client: GraphClient | None = None, lookback_s: float | None = None) -> None:
        self._client = client
        # The re-scan window behind the watermark. Same dial and same default as mailpit's, and
        # it is read through the same declared key so there is one number to reason about.
        import flows_config
        self.lookback = float(lookback_s if lookback_s is not None
                              else flows_config.get_int("VEXA_MAILPIT_LOOKBACK_S"))
        self._seen: set[str] = set()

    @property
    def client(self) -> GraphClient:
        if self._client is None:
            self._client = graph_client()
        return self._client

    def address(self) -> str:
        return self.client.address()

    # ── cursor ───────────────────────────────────────────────────────────────────────────────
    def restore(self, db) -> str | None:
        ensure_token_column(db)
        token = read_watermark(db)
        if token:
            self._seen = load_seen(db, self.name, iso_norm(iso_epoch(token) - self.lookback))
        return token

    def anchor(self, db, cursor: str) -> None:
        """Anchor at the tail: everything already in the mailbox is behind the watermark and is
        never fetched (C1). Unlike mailpit's anchor this does not pre-mark ids — the window that
        follows starts at the anchor itself, and a message that arrived in the same second is a
        message we DO want."""
        ensure_token_column(db)
        write_watermark(db, iso_norm(cursor))

    def tail_cursor(self) -> str:
        return iso_norm(self.client.newest_received())

    def commit(self, db, msg: InboundMessage) -> None:
        mark_seen(db, self.name, msg.ext_id, msg.cursor, self._seen)
        advance_watermark(db, self.name, msg.cursor,
                          prune_before=iso_norm(iso_epoch(msg.cursor) - self.lookback - 86400))

    # ── fetch ────────────────────────────────────────────────────────────────────────────────
    def fetch(self, cursor: str) -> Iterator[InboundMessage]:
        floor = iso_norm(iso_epoch(cursor) - self.lookback)
        # Graph wants seconds, not the fixed-width microseconds the watermark stores.
        for m in self.client.messages_since(floor[:19] + "Z"):
            mid = m.get("id") or ""
            if not mid or mid in self._seen:
                continue
            yield self.to_inbound(m)

    def to_inbound(self, m: dict) -> InboundMessage:
        """One Graph message → the same `InboundMessage` the IMAP path produces.

        `headers` carries `internetMessageHeaders` verbatim, which is what keeps C2 (threading by
        In-Reply-To/References) identical across sources. Graph only populates that collection on
        a `$select` that names it — `graph_client.SELECT` does."""
        headers = {h.get("name", ""): h.get("value", "")
                   for h in (m.get("internetMessageHeaders") or []) if isinstance(h, dict)}
        frm = ((m.get("from") or {}).get("emailAddress") or {}).get("address", "").lower()
        ics = None
        if m.get("hasAttachments"):
            ics = self.client.ics_attachment(m["id"])
        if ics is None:
            ics = synthesize_ics(m)
        return InboundMessage(
            cursor=iso_norm(m.get("receivedDateTime") or ""),
            ext_id=m.get("id") or "",
            message_id=(m.get("internetMessageId") or "").strip(),
            frm=frm,
            subject=m.get("subject") or "",
            headers=headers,
            body=((m.get("body") or {}).get("content") or ""),
            ics=ics)
