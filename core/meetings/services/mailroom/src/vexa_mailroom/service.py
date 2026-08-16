"""``Mailroom.poll_once()`` — the loop: read the mailbox, decide, act on the control plane.

The whole product decision lives in one method, so read it as the specification:

1. **Ingest.** ``MailSource.fetch_new(since=cursor)`` → raw messages, oldest first. Message ids
   already in the seen list are dropped (a restart re-reads a tail; it must not re-act).
2. **Parse.** ``parse_invite`` → a ``ParsedMail``. Anything unparseable stops here as a notice.
3. **Resolve.** The invited address IS the workspace. The resolver looks for a configured address
   in the message's recipients ∪ ICS attendees; no match ⇒ no group effect + a notice. There is no
   inference, no classifier and no fallback workspace — a message we cannot attribute is never
   attributed to somebody.
4. **Act.**
   * ``METHOD:REQUEST``, unknown UID → ``POST /meetings`` (a planned meeting: platform + native id
     from the invite's link, ``scheduled_at`` from DTSTART, ``auto_join`` on) and bind the series.
   * ``METHOD:REQUEST``, known UID, higher SEQUENCE → ``PATCH /meetings/{id}`` with what changed
     (time, title, link) — the series keeps its row.
   * ``METHOD:REQUEST``, known UID, same-or-lower SEQUENCE → nothing (the idempotency rule).
   * ``METHOD:CANCEL`` → ``DELETE /meetings/{id}``; the binding goes ``cancelled`` so the bot
     stops coming. A cancel for a UID we never bound is a no-op, not a notice — nothing to undo.
   * A recurring invite binds the SERIES: one binding, ``recurring=True``, and the row carries the
     next occurrence. (v0 schedules the next occurrence only, the rule ``calendar_sync`` follows —
     two active rows on one native id violate the control plane's unique index.)
5. **Advance the cursor** — only after the batch is acted on, and only over messages that were
   actually processed, so a crash mid-batch re-reads rather than skips.

**Fail-safe, never broadcast.** Every failure path ends in *no group effect + a notice*: an
unresolvable address, a malformed invite, a link-less event, a control plane that refuses. A
notice is recorded for the operator (v0) and is where an outbound "we could not attend that"
email will attach (Stage 1) — nothing is sent tonight, and nothing is ever sent to a room.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence

from .invite import (METHOD_CANCEL, METHOD_REQUEST, REASON_UNKNOWN_WORKSPACE, ParsedMail,
                     Rejection, parse_invite)
from .ports import Binding, MailMessage, MailSource, MeetingApi, Notice

log = logging.getLogger("vexa_mailroom")

# Outcome verbs — one per message, and the poll result is their tally.
CREATED, UPDATED, CANCELLED, DUPLICATE, IGNORED, REJECTED, FAILED = (
    "created", "updated", "cancelled", "duplicate", "ignored", "rejected", "failed")


@dataclass(frozen=True)
class Outcome:
    """What one message did. ``reason`` is set for everything that changed no binding."""
    message_id: str
    action: str
    uid: Optional[str] = None
    workspace_id: Optional[str] = None
    meeting_id: Optional[int] = None
    reason: Optional[str] = None
    detail: str = ""

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class PollResult:
    outcomes: list[Outcome] = field(default_factory=list)
    cursor: Optional[str] = None

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for o in self.outcomes:
            out[o.action] = out.get(o.action, 0) + 1
        return out

    def of(self, action: str) -> list[Outcome]:
        return [o for o in self.outcomes if o.action == action]

    def as_dict(self) -> dict:
        return {"counts": self.counts, "cursor": self.cursor,
                "outcomes": [o.as_dict() for o in self.outcomes]}


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """An ISO-8601 string → a tz-aware UTC datetime (naive input is read as UTC)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def normalize_address(address: str) -> str:
    """Lowercase, and drop a ``+tag`` sub-address — ``mk-dev+sales@d`` resolves as ``mk-dev@d``.

    Sub-addressing is how a user will eventually pass arguments in the address itself; v0 resolves
    the workspace from the base address and ignores the tag rather than failing to resolve.
    """
    address = (address or "").strip().lower()
    if "@" not in address:
        return address
    local, _, domain = address.partition("@")
    local = local.split("+", 1)[0]
    return f"{local}@{domain}"


class Mailroom:
    """The service object. Construct with ports; call ``poll_once``."""

    def __init__(self, *, source: MailSource, meetings: MeetingApi, store, notices,
                 workspaces: Mapping[str, str], auto_join: bool = True,
                 batch_limit: int = 50, now=None) -> None:
        self.source = source
        self.meetings = meetings
        self.store = store
        self.notices = notices
        # invited address → workspace id. v0 ships ONE mapping from config; the shape is already a
        # map so multi-workspace is a config change, not a code change.
        self.workspaces = {normalize_address(k): v for k, v in dict(workspaces).items()}
        self.auto_join = auto_join
        self.batch_limit = batch_limit
        self._now = now or (lambda: datetime.now(timezone.utc))

    # ── resolution ────────────────────────────────────────────────────────────────────────────
    def resolve_workspace(self, parsed: ParsedMail) -> tuple[Optional[str], Optional[str]]:
        """(workspace_id, matched_address) — the INVITED address is the resolution.

        Invited means named in the ICS ``ATTENDEE`` list. A message that merely arrived at the
        mailbox (forwarded, BCC'd, expanded through a distribution list) does not bind: otherwise
        forwarding an invitation would put a bot in a meeting whose organizer never asked for one.
        """
        for address in parsed.invited_addresses:
            candidate = normalize_address(address)
            if candidate in self.workspaces:
                return (self.workspaces[candidate], candidate)
        return (None, None)

    def addressed_to_us(self, parsed: ParsedMail) -> bool:
        """Did this message reach our mailbox at all? — the notice test, not the binding test."""
        return any(normalize_address(a) in self.workspaces for a in parsed.addressed_to)

    # ── the loop ──────────────────────────────────────────────────────────────────────────────
    async def poll_once(self, *, limit: Optional[int] = None) -> PollResult:
        cursor = await self.store.cursor()
        seen = list(await self.store.seen())
        seen_set = set(seen)
        messages = list(await self.source.fetch_new(since=cursor, limit=limit or self.batch_limit))
        messages.sort(key=lambda m: (m.created or "", m.id))

        result = PollResult(cursor=cursor)
        for message in messages:
            if message.id in seen_set:
                continue
            try:
                outcome = await self._handle(message)
            except Exception as e:                            # a bad message never wedges the loop
                log.exception("mailroom: message %s failed", message.id)
                outcome = Outcome(message.id, FAILED, reason="exception", detail=str(e))
                await self._notice(message, ParsedMail(), Rejection("exception", str(e)))
            result.outcomes.append(outcome)
            seen.append(message.id)
            seen_set.add(message.id)
            if message.created and (cursor is None or message.created > cursor):
                cursor = message.created
            # The cursor advances per MESSAGE, not per batch: a crash halfway through re-reads the
            # remainder only, and the seen list makes even that re-read a no-op.
            await self.store.set_cursor(cursor, seen)

        result.outcomes.extend(await self.advance_series())
        result.cursor = cursor
        return result

    async def _handle(self, message: MailMessage) -> Outcome:
        parsed = parse_invite(message.raw, now=self._now())

        # A message that never reached our mailbox is not ours to answer — no binding AND no
        # notice. (An unparseable .ics has no ATTENDEE list to read, so this test uses the
        # envelope; binding, below, never does.)
        if not self.addressed_to_us(parsed):
            if not parsed.ok:
                return Outcome(message.id, IGNORED, uid=parsed.uid,
                               reason=parsed.rejection.reason, detail=parsed.rejection.detail)
            rejection = Rejection(REASON_UNKNOWN_WORKSPACE,
                                  "no configured workspace address among "
                                  f"{', '.join(parsed.addressed_to) or '(no recipients)'}")
            return Outcome(message.id, IGNORED, uid=parsed.uid,
                           reason=rejection.reason, detail=rejection.detail)

        if not parsed.ok:
            await self._notice(message, parsed, parsed.rejection)
            return Outcome(message.id, REJECTED, uid=parsed.uid,
                           reason=parsed.rejection.reason, detail=parsed.rejection.detail)

        workspace_id, address = self.resolve_workspace(parsed)
        if workspace_id is None:
            # It reached us but does not name us: the organizer learns why, and no bot is sent.
            rejection = Rejection(REASON_UNKNOWN_WORKSPACE,
                                  "the workspace address is not on the invitation's ATTENDEE list "
                                  f"({', '.join(parsed.attendees) or 'no attendees'})")
            await self._notice(message, parsed, rejection)
            return Outcome(message.id, REJECTED, uid=parsed.uid,
                           reason=rejection.reason, detail=rejection.detail)

        binding = await self.store.get(workspace_id, parsed.uid)
        if parsed.method == METHOD_CANCEL:
            return await self._cancel(message, parsed, workspace_id, binding)
        return await self._request(message, parsed, workspace_id, address, binding)

    # ── actions ───────────────────────────────────────────────────────────────────────────────
    async def _request(self, message: MailMessage, parsed: ParsedMail, workspace_id: str,
                       address: Optional[str], binding: Optional[Binding]) -> Outcome:
        # The SEQUENCE check runs for a CANCELLED binding too. Mail reorders, and a SEQUENCE:1
        # invitation arriving after the SEQUENCE:2 cancellation is an out-of-order delivery, not a
        # resurrection — without this the stale copy would put the bot back into a meeting the
        # organizer already called off. (A genuinely NEWER request after a cancel does re-bind:
        # the organizer reinstating a meeting is a real thing, and it carries a higher SEQUENCE.)
        if binding is not None and parsed.sequence <= binding.sequence:
            return Outcome(message.id, DUPLICATE, uid=parsed.uid, workspace_id=workspace_id,
                           meeting_id=binding.meeting_id, reason="sequence_not_newer",
                           detail=f"SEQUENCE {parsed.sequence} ≤ acted {binding.sequence}"
                                  + (" (binding already cancelled)" if binding.state == "cancelled" else ""))

        if binding is not None and binding.meeting_id is not None and binding.state == "active":
            fields: dict[str, Any] = {}
            if parsed.dtstart and parsed.dtstart != binding.scheduled_at:
                fields["scheduled_at"] = parsed.dtstart
            if parsed.summary and parsed.summary != binding.title:
                fields["title"] = parsed.summary
            if parsed.meeting_url and parsed.meeting_url != binding.meeting_url:
                fields["meeting_url"] = parsed.meeting_url
            if not fields:
                # A newer SEQUENCE that moved nothing we schedule on (an RSVP-only revision):
                # record the sequence so the next one compares correctly, but touch no row.
                await self._bind(binding, parsed, message, workspace_id, address, action="noop")
                return Outcome(message.id, DUPLICATE, uid=parsed.uid, workspace_id=workspace_id,
                               meeting_id=binding.meeting_id, reason="nothing_changed")
            row = await self.meetings.update_planned_meeting(binding.meeting_id, **fields)
            if not isinstance(row, dict) or row.get("error"):
                detail = (row or {}).get("error", "update refused")
                await self._notice(message, parsed, Rejection("meeting_api_refused", str(detail)))
                return Outcome(message.id, FAILED, uid=parsed.uid, workspace_id=workspace_id,
                               meeting_id=binding.meeting_id, reason="meeting_api_refused",
                               detail=str(detail))
            await self._bind(binding, parsed, message, workspace_id, address, action="updated")
            return Outcome(message.id, UPDATED, uid=parsed.uid, workspace_id=workspace_id,
                           meeting_id=binding.meeting_id)

        row = await self.meetings.create_planned_meeting(
            workspace_id=workspace_id,
            meeting_url=parsed.meeting_url,
            title=parsed.summary,
            scheduled_at=parsed.dtstart,
            auto_join=self.auto_join,
        )
        if not isinstance(row, dict) or row.get("error") or row.get("id") is None:
            detail = (row or {}).get("error", "create refused")
            await self._notice(message, parsed, Rejection("meeting_api_refused", str(detail)))
            return Outcome(message.id, FAILED, uid=parsed.uid, workspace_id=workspace_id,
                           reason="meeting_api_refused", detail=str(detail))
        fresh = binding or Binding(workspace_id=workspace_id, uid=parsed.uid)
        fresh.meeting_id = int(row["id"])
        fresh.state = "active"
        await self._bind(fresh, parsed, message, workspace_id, address, action="created")
        # A REQUEST is authoritative whatever its SEQUENCE: RFC 5545 has no "update" method, so a
        # service that only accepts SEQUENCE:0 loses every meeting whose first invitation was
        # lost, rate-limited or delivered out of order. Bind, and say that no prior state existed.
        detail = (f"no prior binding for SEQUENCE:{parsed.sequence} — first invitation never arrived"
                  if parsed.sequence > 0 and binding is None else "")
        return Outcome(message.id, CREATED, uid=parsed.uid, workspace_id=workspace_id,
                       meeting_id=fresh.meeting_id, detail=detail)

    async def _cancel(self, message: MailMessage, parsed: ParsedMail, workspace_id: str,
                      binding: Optional[Binding]) -> Outcome:
        if binding is None:
            # Never bound — a cancellation for a meeting we were not attending changes nothing.
            return Outcome(message.id, IGNORED, uid=parsed.uid, workspace_id=workspace_id,
                           reason="no_binding", detail="cancel for a series we never bound")
        if binding.state == "cancelled":
            return Outcome(message.id, DUPLICATE, uid=parsed.uid, workspace_id=workspace_id,
                           meeting_id=binding.meeting_id, reason="already_cancelled")
        stopped = True
        if binding.meeting_id is not None:
            stopped = await self.meetings.cancel_planned_meeting(binding.meeting_id)
        binding.state = "cancelled"
        binding.sequence = max(binding.sequence, parsed.sequence)
        binding.last_message_id = message.id
        binding.history.append({"at": self._now().isoformat(), "action": "cancelled",
                                "message_id": message.id, "sequence": parsed.sequence,
                                "stopped": stopped})
        await self.store.put(binding)
        if not stopped:
            # The row is already the bot FSM's (live or finished): the binding stops here, and the
            # operator is told rather than the mailroom pretending the bot was recalled.
            await self._notice(message, parsed,
                               Rejection("meeting_api_refused", "row is no longer planned"))
        return Outcome(message.id, CANCELLED, uid=parsed.uid, workspace_id=workspace_id,
                       meeting_id=binding.meeting_id,
                       reason=None if stopped else "row_not_planned")

    # ── bookkeeping ───────────────────────────────────────────────────────────────────────────
    async def _bind(self, binding: Binding, parsed: ParsedMail, message: MailMessage,
                    workspace_id: str, address: Optional[str], *, action: str) -> None:
        binding.workspace_id = workspace_id
        binding.uid = parsed.uid
        binding.sequence = parsed.sequence
        binding.recurring = parsed.recurring or binding.recurring
        binding.scheduled_at = parsed.dtstart or binding.scheduled_at
        binding.meeting_url = parsed.meeting_url or binding.meeting_url
        binding.platform = parsed.platform or binding.platform
        binding.native_meeting_id = parsed.native_meeting_id or binding.native_meeting_id
        binding.title = parsed.summary or binding.title
        binding.invited_address = address or binding.invited_address
        binding.rrule = parsed.rrule or binding.rrule
        binding.series_start = parsed.series_start or binding.series_start
        if parsed.participants:
            binding.participants = [dict(p) for p in parsed.participants]
        binding.last_message_id = message.id
        binding.history.append({"at": self._now().isoformat(), "action": action,
                                "message_id": message.id, "sequence": parsed.sequence})
        await self.store.put(binding)

    async def _notice(self, message: MailMessage, parsed: ParsedMail, rejection: Rejection) -> None:
        await self.notices.record(Notice(
            message_id=message.id,
            reason=rejection.reason,
            detail=rejection.detail,
            to=parsed.organizer or parsed.sender,
            sender=parsed.sender,
            subject=parsed.subject,
            uid=parsed.uid,
            at=self._now().isoformat(),
        ))

    # ── the series sweep ──────────────────────────────────────────────────────────────────────
    async def advance_series(self, *, grace_s: float = 3600.0) -> list[Outcome]:
        """Roll every active recurring binding forward to its NEXT occurrence.

        A recurring invitation is sent ONCE. The planned row carries one occurrence (two active
        rows on one meeting link would violate the control plane's unique index), so something has
        to move it after each occurrence passes — otherwise the bot attends occurrence 1 and never
        occurrence 2, which is the acceptance criterion the whole series binding exists for.

        That something is this sweep: it re-expands the stored ``RRULE`` from the stored series
        start and PATCHes the row's ``scheduled_at``. It runs at the end of every poll, so a
        mailbox that receives nothing still keeps its series alive. A series whose occurrences are
        exhausted (``COUNT``/``UNTIL`` reached) simply stops moving — the binding stays, inert.
        """
        from dateutil.rrule import rrulestr

        now = self._now()
        out: list[Outcome] = []
        for binding in list(await self.store.all()):
            if binding.state != "active" or not binding.recurring or not binding.rrule:
                continue
            if not binding.series_start or binding.meeting_id is None:
                continue
            current = _parse_iso(binding.scheduled_at)
            if current is None or current > now - timedelta(seconds=grace_s):
                continue                      # the current occurrence has not passed yet
            start = _parse_iso(binding.series_start)
            if start is None:
                continue
            try:
                rule = rrulestr(binding.rrule, dtstart=start)
            except (ValueError, TypeError):
                continue
            nxt = rule.after(now)
            if nxt is None:
                continue                      # COUNT/UNTIL exhausted — the series is over
            nxt_iso = nxt.astimezone(timezone.utc).isoformat()
            row = await self.meetings.update_planned_meeting(binding.meeting_id,
                                                             scheduled_at=nxt_iso)
            if not isinstance(row, dict) or row.get("error"):
                out.append(Outcome(binding.last_message_id or "", FAILED, uid=binding.uid,
                                   workspace_id=binding.workspace_id,
                                   meeting_id=binding.meeting_id, reason="meeting_api_refused",
                                   detail=str((row or {}).get("error"))))
                continue
            binding.scheduled_at = nxt_iso
            binding.history.append({"at": now.isoformat(), "action": "advanced",
                                    "scheduled_at": nxt_iso})
            await self.store.put(binding)
            out.append(Outcome(binding.last_message_id or "", UPDATED, uid=binding.uid,
                               workspace_id=binding.workspace_id, meeting_id=binding.meeting_id,
                               reason="series_advanced", detail=nxt_iso))
        return out

    # ── read models (the /internal surface renders these) ─────────────────────────────────────
    async def bindings(self) -> Sequence[dict]:
        return [b.as_dict() for b in await self.store.all()]

    async def recent_notices(self, limit: int = 50) -> Sequence[dict]:
        return [n.as_dict() for n in await self.notices.recent(limit)]
