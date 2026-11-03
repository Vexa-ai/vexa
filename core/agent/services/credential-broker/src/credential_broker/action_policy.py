"""The connection policy, enforced at the broker as well.

agent-api resolves the administrator's `_global/POLICIES.md` (narrowed by the person) and forwards
the answer with each action as `ActionPolicy`; it refuses first. The broker refuses again, here, so
the rules hold for anything that reaches the broker: the content of a calendar change or a draft is
held to the forwarded lists, and a worker without a person in the loop is served an account READ only
when the policy forwarded with it allows it (`require_regime`).

A WRITE (a draft, a calendar change) is never served to a worker without a person in the loop, and no
forwarded policy can change that: the routes take the identity without `unwatched_ok`, so the signed
regime alone decides. The forwarded policy is the caller's word; it may narrow a write, never open
one (ADR-0040). Absent a forwarded policy the safe defaults apply (no
background use, no attendee mail, the primary calendar only, no attachments).

Every refusal is a 409 whose sentence names the policy key, so the person knows which line of
`POLICIES.md` (or of their own setting) said no.
"""
from __future__ import annotations

import base64
import binascii
from typing import Iterable, Optional

from fastapi import HTTPException

from .models import ActionPolicy
from .obs import log_event


def effective(policy: Optional[ActionPolicy]) -> ActionPolicy:
    return policy if policy is not None else ActionPolicy()


def refuse(key: str, why: str) -> HTTPException:
    log_event("policy_refused", level="warning", fields={"policy_key": key})
    return HTTPException(409, f"Refused by policy {key}: {why}")


def require_regime(who: dict, policy: ActionPolicy, route: str) -> None:
    """An account read by a worker running without a person is served only when the forwarded policy
    allows it unattended (`connection_actions_unattended`). Writes never call this: they refuse an
    unwatched worker outright."""
    if who.get("role") == "agent" and who.get("unwatched") and not policy.unattended:
        log_event("regime_refused", level="warning", user_id=who["actor"], fields={"route": route})
        raise HTTPException(403, "This session runs without a person in the loop")


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].strip().lower()


def require_domains(addresses: Iterable[str], allowed: Optional[list], key: str) -> None:
    if allowed is None:
        return
    allowed_set = {d.lower() for d in allowed}
    outside = sorted({_domain(a) for a in addresses if _domain(a) not in allowed_set})
    if outside:
        raise refuse(key, f"{', '.join(outside)} is not an allowed domain")


def require_calendar(calendar_id: str, policy: ActionPolicy) -> None:
    if policy.calendars is not None and calendar_id not in policy.calendars:
        raise refuse("calendar_ids", "this calendar is not one an agent may change")


def require_notify(notify: bool, policy: ActionPolicy) -> None:
    if notify and not policy.notify_attendees:
        raise refuse("calendar_notify_attendees", "agents may not ask Google to email attendees")


def attachments(items, policy: ActionPolicy) -> list[tuple]:
    """Decode attachments, refusing them unless policy allows, and above the size cap."""
    if not items:
        return []
    if not policy.attachments:
        raise refuse("draft_attachments", "drafts may not carry attachments")
    out, total = [], 0
    for item in items:
        try:
            data = base64.b64decode(item.data, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(422, "Attachment data is not base64") from None
        total += len(data)
        out.append((item.filename, item.content_type, data))
    if total > policy.attachment_max_bytes:
        raise refuse("draft_attachment_max_kb", f"attachments total {total // 1024} KB, above the cap")
    return out
