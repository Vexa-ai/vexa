"""The nudges pack (PRD 0.13.4 S6, D15) — enabled through ``VEXA_FLOWS_DEFS_EXTRA=flows_defs.nudges``
together with ``VEXA_FLOWS_NUDGES=1`` (which turns the worker's delivery pass on).

Three flows ARM, MOVE or END a row in ``delivery``; none of them sends anything. Sending is the
delivery gate's, on its own clock (``flows_delivery.run``), so a burst of events can never become a
burst of mail.

* ``nudge_card``    ``crm.record.changed`` with ``object=card`` (the S5 envelope, D8: refs and the
                    lifecycle fields ``kind``, ``state``, ``next_actor``, ``next_check_at`` — no
                    content). Arms the next actor's nudge at ``next_check_at``; a terminal state, no
                    next actor or no check ends every pending nudge for the card; a new next actor
                    ends the previous one's.
* ``nudge_item``    ``nudge.requested`` — a room item that interrupts: a mention, a call that rang
                    you and was missed, a join request you can approve, a slip on a card. Published by
                    the domain that saw it (agent-api for rooms, the S8 detectors for slips), with the
                    display names a template needs and, when the domain already wrote the in-app
                    notice, its id. A plain room message is never armed: messages do not interrupt.
* ``nudge_cancel``  ``nudge.cancelled`` — the producer withdraws one (the call was answered).

THE FLOOR AT THE DOOR (D38 ``nudge_body = template_only``, deny test 26): an event that carries
content (``body``, ``text``, ``message``, …) is refused, non-retryably, and arms nothing.
"""
from __future__ import annotations

import calendar
import re
import time
from datetime import datetime

from flows import Done, EventType, StepError

from flows_delivery import policy as P, store, templates

#: Card states after which nobody is waiting on anybody (S5's lifecycle templates name these).
TERMINAL = frozenset({"done", "declined", "closed", "cancelled", "archived", "withdrawn",
                      "expired", "signed", "accepted", "rejected"})
_REF = re.compile(r"^(card|mention|call|join|slip|room|msg):[A-Za-z0-9._:@+=-]{1,200}$")


def _when(v) -> "float | None":
    """``next_check_at`` as epoch seconds: a number, or an ISO-8601 string (``Z`` or an offset)."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    try:
        s = str(v).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            return float(calendar.timegm(dt.timetuple()))
        return dt.timestamp()
    except ValueError:
        return None


def _now(ctx) -> float:
    at = getattr(ctx, "clock_now", None)
    return float(at) if at else time.time()


def build(reg, db):
    @reg.step
    def nudge_from_card(ctx):
        """Arm, move or end the card's nudge for its next actor (D15). Refs and lifecycle fields only."""
        refs = ctx.refs or {}
        if refs.get("object") != "card":
            return Done({"skipped": "not a card"})
        cid = str(refs.get("record_id") or refs.get("card") or "").strip()
        if not cid:
            raise StepError("a card fact names its record_id", retryable=False)
        ref = f"card:{cid}"
        now = _now(ctx)
        state = str(refs.get("state") or "").lower()
        actor = str(refs.get("next_actor") or "").strip()
        at = _when(refs.get("next_check_at"))
        if state in TERMINAL or not actor or at is None:
            ended = store.finish_ref(db, now, ref, "closed")
            return Done({"ref": ref, "ended": ended})
        reassigned = store.finish_ref(db, now, ref, "reassigned", except_person=actor)
        kind = "slip" if state in ("slipped", "overdue") else "card"
        armed = store.arm(db, now, person=actor, ref=ref, kind=kind, at=at,
                          org=str(refs.get("org_id") or ""), workspace=str(refs.get("workspace") or ""),
                          owner=str(refs.get("owner") or refs.get("actor") or ""),
                          link=templates.card_link(cid))
        return Done({"ref": ref, "person": actor, "armed": armed, "reassigned": reassigned})

    @reg.step
    def nudge_arm_item(ctx):
        """Arm a room item that interrupts (mention, missed call, join request, slip) for one person."""
        refs = dict(ctx.refs or {})
        bad = sorted(k for k in refs if k in templates.FREE_TEXT_KEYS)
        if bad:
            raise StepError(f"a nudge carries template fields only; refused: {', '.join(bad)}",
                            retryable=False)
        person = str(refs.get("person") or refs.get("uid") or "").strip()
        kind = str(refs.get("kind") or "")
        ref = str(refs.get("ref") or "")
        if not person or kind not in P.KINDS or not _REF.match(ref):
            raise StepError("nudge.requested names person, kind and ref", retryable=False)
        if kind not in P.INTERRUPTING:
            return Done({"skipped": "messages do not interrupt"})
        now = _now(ctx)
        link = str(refs.get("link") or "")
        if link and not templates._PATH.match(link):
            raise StepError("a nudge link is a path in this app", retryable=False)
        names = {"who": refs.get("who"), "room": refs.get("room_name"),
                 "workspace": refs.get("workspace_name"), "title": refs.get("title")}
        armed = store.arm(db, now, person=person, ref=ref, kind=kind, at=_when(refs.get("at")) or now,
                          org=str(refs.get("org_id") or ""), workspace=str(refs.get("workspace") or ""),
                          room=str(refs.get("room") or ""), owner=str(refs.get("owner") or ""),
                          names=names, link=link)
        if refs.get("notice_id"):
            db.execute("UPDATE delivery SET notice_id = :n WHERE person = :p AND ref = :r",
                       {"n": str(refs["notice_id"])[:200], "p": person, "r": ref})
        return Done({"ref": ref, "person": person, "armed": armed})

    @reg.step
    def nudge_withdraw(ctx):
        """End one person's pending nudge for an item (the producer withdrew it)."""
        refs = ctx.refs or {}
        person, ref = str(refs.get("person") or refs.get("uid") or ""), str(refs.get("ref") or "")
        if not person or not ref:
            raise StepError("nudge.cancelled names person and ref", retryable=False)
        return Done({"ref": ref, "ended": store.finish(db, _now(ctx), person, ref, "cancelled")})

    reg.flow(name="nudge_card", version=1, on=EventType("crm.record.changed"),
             steps=[reg.steps["nudge_from_card"]])
    reg.flow(name="nudge_item", version=1, on=EventType("nudge.requested"),
             steps=[reg.steps["nudge_arm_item"]])
    reg.flow(name="nudge_cancel", version=1, on=EventType("nudge.cancelled"),
             steps=[reg.steps["nudge_withdraw"]])
