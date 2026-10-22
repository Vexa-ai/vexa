"""The `mailbox` sensor kind — a mailbox read as a sensor (ADR-0050), e.g. the shared `info@`.

Founder, 2026-10-14: inbound on `info@vexa.ai` is *"a standard flow edge script or worker"*, owned by
the global account and designed from chat like any person's sensor (`sensor_create` → `sensor_test`
→ `sensor_action enable` → `sensor_connect`). Nothing about it is system-only: this is one
`SensorKind`, and the pool, the dry run, the versioning and the health are the common ones.

What a tick does — no model, ever:

  * READS READ-ONLY. The mailbox may be one people read too: IMAP opens the folder with EXAMINE and
    fetches with `BODY.PEEK[]` (`flows_integrations.inbox`), so nothing is marked, moved, labelled or
    deleted. Mail it does not keep leaves no row and no event.
  * KEEPS ONLY what Vexa acts on (`flows_integrations.mail_sensor.classify`):
      - a meeting invitation → `invite.received` {organizer, url, start, event_key, sequence, title,
        participants, workspace, workspace_invite_link, workspace_invite_token}, deduped by
        UID+sequence — the contract the seeded `invite_join` flow follows (bot at start − 60 s);
      - a cancellation → `invite.cancelled` {event_key};
      - a reply to mail Vexa sent (In-Reply-To/References names a Vexa Message-ID) whose sender is
        that thread's own person → `mail.reply`.
  * AUTHORISES before emitting, with the existing intake rules (`mailbox.route`): an organizer who is
    neither a user nor inside the allow-list, or `bot_joins_on_invite: off`, emits nothing (and, on a
    live sensor, leaves the quarantine row with the meeting's facts).
  * THE WORKSPACE: an invite whose text carries this deployment's workspace invite link
    (`<VEXA_UI_URL>/join/<token>`) names that workspace; the token is resolved by the producer of
    the link (`workspace_invite_token`), never trusted here.
  * FIRST READ (no cursor — a new sensor, or `sensor_test`): the newest messages are read and only
    invitations to meetings that have not started yet are kept; the cursor anchors at the tail. A
    reply from before the sensor existed is history, never a new agent turn.

The connection is the deployment's mailbox (`VEXA_MAIL_*`, operator config). A sensor on a person's
own Gmail connection is the same filter over a broker read, the next kind to add.
"""
from __future__ import annotations

import re
import time
from typing import Callable, Optional

from .base import Change, SensorFault, SensorKind, SensorRow, Tick

#: how many of the newest messages a first read (and `sensor_test`) looks at
FIRST_READ = 25

_SEQ = re.compile(r"^SEQUENCE:(\d+)", re.M)
_REC = re.compile(r"^RECURRENCE-ID[^:]*:(\S+)", re.M)
_UID = re.compile(r"^UID:(.+)$", re.M)


def _flows_db():
    from flows import postgres_db
    from flows_steps.common import db_url
    return postgres_db(db_url())


def _event_key(ics: str, ev: dict | None = None) -> str:
    from flows_integrations.mailbox import _unfold
    text = _unfold(ics or "")
    uid = (ev or {}).get("ics_uid") or ((_UID.search(text).group(1).strip())
                                        if _UID.search(text) else "")
    rec = _REC.search(text)
    return f"{uid}@{rec.group(1).strip()}" if rec else uid


def _sequence(ics: str) -> int:
    from flows_integrations.mailbox import _unfold
    m = _SEQ.search(_unfold(ics or ""))
    return int(m.group(1)) if m else 0


class MailboxSensor(SensorKind):
    kind = "mailbox"
    event_types = ("invite.received", "invite.cancelled", "mail.reply")
    min_interval_s = 15.0
    connection = ""
    #: the deployment's own mailbox: a sensor of it belongs to the instance, never to one person
    scopes = ("instance",)

    def __init__(self, inbox_factory: Optional[Callable] = None, db_factory: Optional[Callable] = None,
                 known_uid: Optional[Callable] = None, rules: Optional[Callable] = None,
                 clock: Callable = time.time):
        self._inbox = inbox_factory
        self._db = db_factory
        self._known_uid = known_uid
        self._rules = rules
        self._clock = clock

    # ── the doors, injectable so the whole kind runs offline ─────────────────────────────────────
    def _get_inbox(self):
        if self._inbox is not None:
            return self._inbox()
        from flows_integrations.inbox import get_inbox
        return get_inbox()

    def _get_db(self):
        return self._db() if self._db is not None else _flows_db()

    def _who(self, email: str):
        if self._known_uid is not None:
            return self._known_uid(email)
        from flows_steps.common import platform_user_id
        return platform_user_id(email) or None

    def _company_rules(self) -> dict:
        if self._rules is not None:
            return self._rules()
        from flows_steps import policies
        return policies.read_company()

    # ── one read ─────────────────────────────────────────────────────────────────────────────────
    def tick(self, sensor: SensorRow, cursor: Optional[str]) -> Tick:
        from flows_steps.mail_transport import MailTransportError
        try:
            inbox = self._get_inbox()
            self_addr = inbox.address().lower()
            if cursor is None:
                msgs = list(inbox.recent(FIRST_READ))
                new_cursor = msgs[-1].cursor if msgs else inbox.tail_cursor()
            else:
                msgs = list(inbox.fetch(cursor))
                new_cursor = msgs[-1].cursor if msgs else cursor
        except MailTransportError as e:
            raise SensorFault(f"mailbox {e.kind}: {e.detail}"[:200],
                              retryable=bool(e.retryable)) from None
        except NotImplementedError as e:
            raise SensorFault(str(e), retryable=False) from None
        live = sensor.status in ("active", "degraded")
        db = self._get_db()
        rules = self._company_rules()
        changes = []
        for msg in msgs:
            change = self._judge(db, rules, self_addr, msg, first=cursor is None, live=live)
            if change is not None:
                changes.append(change)
        return Tick(changes=changes, cursor=new_cursor)

    def _judge(self, db, rules: dict, self_addr: str, msg, *, first: bool, live: bool):
        from flows_integrations import mail_policy
        from flows_integrations.mail_sensor import KEEP_INVITE, KEEP_REPLY, classify
        from flows_integrations.mailbox import reply_refs, route
        kept, _why = classify(db, msg, "vexa")
        if kept is None:
            return None                                      # dropped at the edge, no trace
        ics = msg.ics or ""
        if kept == KEEP_INVITE and "METHOD:CANCEL" in ics:
            key = _event_key(ics)
            if not key:
                return None
            return Change("invite.cancelled", f"{key}:cancel:{_sequence(ics)}",
                          {"event_key": key, "organizer": msg.frm, "title": msg.subject[:200]})
        allowed = mail_policy.allow_domains(self_addr, rules)
        decision = route(db, self_addr, msg.frm, msg.headers, msg.ics, self._who,
                         lambda _uid: True, allowed=allowed)
        if decision is None:
            return None
        kind, payload = decision
        ext = str(msg.message_id or msg.ext_id)
        if kind == "invite":
            if rules.get("bot_joins_on_invite", True) is False:
                if live:
                    mail_policy.quarantine(db, ext_id=ext, frm=msg.frm,
                                           kind=mail_policy.INVITE_POLICY_OFF,
                                           reason="bot_joins_on_invite is off in _global/POLICIES.md",
                                           facts=payload)
                return None
            if first and float(payload.get("start") or 0) <= self._clock():
                return None                                  # history: that meeting has started
            key = _event_key(ics, payload)
            seq = _sequence(ics)
            refs = {"organizer": payload["organizer"], "url": payload["url"],
                    "start": payload["start"], "event_key": key, "sequence": seq,
                    "title": payload.get("title") or "Meeting",
                    "participants": payload.get("participants") or [],
                    "workspace": payload.get("workspace") or "",
                    **{k: payload[k] for k in ("workspace_invite_link", "workspace_invite_token",
                                               "ics_uid", "occurrence", "share_opt_out", "group",
                                               "participant_names") if payload.get(k)}}
            return Change("invite.received", f"{key}:{seq}", refs)
        if kind == "invite_quarantine":
            if live:
                mail_policy.quarantine(db, ext_id=ext, frm=msg.frm, kind=mail_policy.UNVERIFIED_INVITE,
                                       reason=(f"invite organizer {payload['organizer']} is not a user "
                                               f"and not inside the mail allow-list"), facts=payload)
            return None
        if kind == "thread_reply" and kept == KEEP_REPLY and not first:
            if live:
                limited = mail_policy.rate_check(db, msg.frm)
                if limited:
                    mail_policy.quarantine(db, ext_id=ext, frm=msg.frm, kind=mail_policy.RATE_LIMITED,
                                           reason=limited, facts={"subject": msg.subject})
                    return None
                mail_policy.record_turn(db, ext, msg.frm)
            return Change("mail.reply", f"mail-{ext}",
                          reply_refs(payload["uid"], payload["session"], msg))
        if kind == "quarantine" and live:
            mail_policy.quarantine(db, ext_id=ext, frm=msg.frm, kind=payload["kind"],
                                   reason=payload["reason"], facts={"subject": msg.subject})
        return None
