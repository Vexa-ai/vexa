"""THE GATE — one row, one instant, one decision. Pure: no I/O, no clock, no SQL.

The order is the whole policy, so it reads top to bottom:

  1. We do not know whether the item is still open or seen  →  wait a minute and ask again.
     (Never send on a guess: deny test 30 is "once seen, the pending email is suppressed", and an
     unreachable crm is not evidence that it was not seen.)
  2. Closed, acted on, or seen anywhere                       →  done. Nothing else goes out.
  3. The room's bell does not let this kind through           →  done (muted). It is in the Inbox.
  4. Armed: a missed call goes straight to the digest line;
     everything else gets the in-app notice now and waits out the idle window.
  5. Notified, idle window over:
       email off                                              →  done (the Inbox has it).
       quiet hours                                            →  wait until they end, local time.
       today's budget spent                                   →  the digest (which waits for budget).
       otherwise                                              →  ONE email; then the digest.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import policy as P


@dataclass(frozen=True)
class Attention:
    """What crm (cards) or agent-api (notices) says about the item, for this person, now."""
    open: bool = True
    seen: bool = False
    acted: bool = False


@dataclass(frozen=True)
class Decision:
    action: str                       # defer · done · notify · email · digest
    wake_at: Optional[float] = None
    outcome: Optional[str] = None
    reason: str = ""


RECHECK_S = 60.0


def decide(row: dict, *, att: Optional[Attention], pol: dict, tz: str, now: float, sent_today: int) -> Decision:
    if att is None:
        return Decision("defer", wake_at=now + RECHECK_S, reason="attention_unknown")
    if not att.open:
        return Decision("done", outcome="closed")
    if att.acted:
        return Decision("done", outcome="acted")
    if att.seen:
        return Decision("done", outcome="seen")
    level = P.level_for(pol, room=row.get("room") or "", workspace=row.get("workspace") or "")
    if not P.interrupts(row.get("kind") or "", level):
        return Decision("done", outcome="muted", reason=f"level:{level}")
    stage = row.get("stage")
    if stage == "armed":
        if row.get("kind") == "call":
            at = P.next_digest_at(now, pol, tz)
            return (Decision("digest", wake_at=at) if at is not None and pol["channels"]["email"]
                    else Decision("done", outcome="email_off" if not pol["channels"]["email"] else "inbox_only"))
        return Decision("notify", wake_at=now + 60.0 * pol["idle_min"])
    if stage == "notified":
        if not pol["channels"]["email"]:
            return Decision("done", outcome="email_off")
        if P.in_quiet(now, pol, tz):
            return Decision("defer", wake_at=P.quiet_end(now, pol, tz), reason="quiet_hours")
        if sent_today >= pol["budget"]:
            at = P.next_digest_at(now, pol, tz)
            if at is None:
                return Decision("defer", wake_at=P.quiet_end(P.next_day(now, tz), pol, tz),
                                reason="over_budget")
            return Decision("digest", wake_at=at, reason="over_budget")
        return Decision("email")
    return Decision("defer", wake_at=now + RECHECK_S, reason=f"unexpected_stage:{stage}")


def after_email(now: float, pol: dict, tz: str) -> Decision:
    """Where an emailed item goes next: the digest, or done when the person has digests off."""
    at = P.next_digest_at(now, pol, tz)
    return Decision("digest", wake_at=at) if at is not None else Decision("done", outcome="emailed")


def digest_gate(*, pol: dict, tz: str, now: float, sent_today: int) -> Decision:
    """Whether one person's digest may go now (its items already re-checked)."""
    if not pol["channels"]["email"] or pol["digest"] == "off":
        return Decision("done", outcome="email_off")
    if P.in_quiet(now, pol, tz):
        return Decision("defer", wake_at=P.quiet_end(now, pol, tz), reason="quiet_hours")
    if sent_today >= pol["budget"]:
        return Decision("defer", wake_at=P.quiet_end(P.next_day(now, tz), pol, tz), reason="over_budget")
    return Decision("email")
