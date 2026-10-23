"""outbound.py — THE ONE OUTBOUND MAIL PATH. Every transactional mail this deployment sends passes here.

    kind  →  switch (`_global/POLICIES.md` `mail.<kind>`)  →  cap  →  template  →  notify  →  audit

Before this module each step called `notify(...)` with its own words and its own gate, and the
terminal's sign-in mailer was a second sender with its own identity. Nobody could answer *"what does
this deployment mail, to whom, in which words"* without reading the code, and the global account —
whose question that is (founder, 2026-10-14) — could change none of it. Now:

  * WHAT is sent is a KIND (`mailkinds.KINDS`) and the company switches each one on, off, or to
    `admins` only. The sign-in link is LOCKED on: no policy file may lock people out.
  * IN WHICH WORDS is the kind's template (`_global/mail/<template>.md`, else the baked starter).
    The template VERSION (a content hash) travels with every send.
  * HOW MANY is the kind's daily cap (`mail_caps` in `_global`), under the operator's ceiling
    (`VEXA_MAIL_MAX_PER_DAY`). The sign-in link is never capped here — the terminal rate-limits it.
  * EVERY ATTEMPT IS AUDITED, sent or not: one `mail_send` row (kind, template version, recipient
    DOMAIN, result) and one structured log line. Never the address, never the body.
  * The transport (`notify` → `emailx`) owns identity (`VEXA_MAIL_SMTP_FROM`, `VEXA_MAIL_REPLY_TO`),
    the relay switch (`VEXA_MAIL_RELAY`) and the rule that test domains are never relayed.

A kind that is OFF sends nothing and raises nothing: the step reads `Outcome.sent` and records why.
A transport failure is audited and RE-RAISED, so the engine's retry policy still applies.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Optional

import flows_config

from . import mailkinds, mailtext, policies
from . import notify as _notify

logger = logging.getLogger(__name__)

SENT, OFF, ADMINS_ONLY, CAPPED, SUPPRESSED, FAILED = (
    "sent", "off", "admins-only", "capped", "suppressed", "failed")


@dataclass(frozen=True)
class Outcome:
    kind: str
    result: str
    template_version: str
    message_id: Optional[str] = None
    detail: str = ""
    #: `delivered` or `captured` (the capture double: nobody receives it) — say "mailed" only for
    #: `delivered`; "" when nothing was sent
    delivery: str = ""

    @property
    def sent(self) -> bool:
        return self.result == SENT


def domain_of(address: str) -> str:
    return (address or "").strip().lower().rpartition("@")[2].rstrip(">").strip() or "-"


def _day_start(now: float) -> float:
    return now - (now % 86400)


def audit(db, *, kind: str, version: str, to: str, result: str, detail: str = "",
          message_id: Optional[str] = None, test: bool = False, now: Optional[float] = None) -> None:
    """One row + one log line. Never raises: an audit that cannot be written must not unsend a mail
    or fail a step — it is logged loudly instead."""
    at = float(now if now is not None else time.time())
    fields = {"kind": kind, "template_version": version, "recipient_domain": domain_of(to),
              "result": result, "test": bool(test)}
    try:
        import flows_obs
        flows_obs.log_event("mail_send", fields={**fields, "detail": detail[:200]})
    except Exception:  # noqa: BLE001
        logger.info("mail_send %s", fields)
    if db is None:
        return
    try:
        db.execute(
            """INSERT INTO mail_send (id, at, kind, template_version, recipient_domain, result,
                                      detail, message_id, test)
               VALUES (:i,:a,:k,:v,:d,:r,:x,:m,:t)""",
            {"i": uuid.uuid4().hex, "a": at, "k": kind, "v": version, "d": domain_of(to),
             "r": result, "x": (detail or "")[:500], "m": message_id, "t": 1 if test else 0})
    except Exception as e:  # noqa: BLE001
        logger.warning("mail_send audit row not written (%s): %s", type(e).__name__, fields)


def sent_today(db, kind: str, now: float) -> int:
    if db is None:
        return 0
    try:
        rows = db.execute("SELECT COUNT(*) FROM mail_send WHERE kind = :k AND at >= :s "
                          "AND result = 'sent' AND test = 0",
                          {"k": kind, "s": _day_start(now)})
        return int(rows[0][0]) if rows else 0
    except Exception:  # noqa: BLE001
        return 0


def cap_for(rules: dict, kind: str) -> int:
    """The lower of the company's cap for this kind (`mail_caps`, else its `*`) and the operator's."""
    operator = max(flows_config.get_int("VEXA_MAIL_MAX_PER_DAY"), 0)
    caps = mailkinds.parse_caps((rules or {}).get("mail_caps"))
    company = caps.get(kind, caps.get("*"))
    if company is None:
        return operator
    return min(company, operator) if operator else company


def _rules(uid: str) -> dict:
    return policies.read(uid) if str(uid or "").strip() else policies.read_company()


def _template(k: "mailkinds.Kind", uid: str) -> tuple[str, str]:
    """The live template, FAIL-SOFT: a `_global` that cannot be read right now is the baked starter,
    exactly as for policies — a mail must never fail because the company layer blinked."""
    try:
        return mailtext.template(k.template, uid or policies.COMPANY_READER)
    except KeyError:
        raise
    except Exception:  # noqa: BLE001
        logger.debug("mail template %s: _global unreadable — using the baked starter", k.template)
        return mailtext.DEFAULTS[k.template], "baked"


def template_version(kind: str, uid: str = "") -> str:
    k = mailkinds.get(kind)
    if k.template is None:
        return "fixed"
    raw, _source = _template(k, uid)
    return mailtext.version(raw)


def send(kind: str, to: str, *, uid: str = "", values: Optional[dict] = None,
         subject: Optional[str] = None, body: Optional[str] = None, link: Optional[str] = None,
         in_reply_to: Optional[str] = None, db=None, test: bool = False,
         is_admin=None, now: Optional[float] = None, sender=None) -> Outcome:
    """Send one mail of one kind, or record why not.

    `subject`/`body` given: the caller composed them (an agent's reply, a report body under its
    template head) — the switch, cap and audit still apply. Otherwise the kind's template renders
    with `values`. `test=True` is a test-send: the switch and the cap do not apply (the global
    account is checking a kind it has not turned on yet) and the row is marked `test`.

    `is_admin(address) -> bool` answers the `admins` switch; `sender` replaces `notify.notify`
    (same signature) — a step module passes its own `notify` so a test that intercepts it there
    still sees the mail."""
    k = mailkinds.get(kind)
    now = float(now if now is not None else time.time())
    rules = _rules(uid)
    version = "fixed"
    if k.template is not None:
        raw, _source = _template(k, uid)
        version = mailtext.version(raw)
    if not test:
        switch = mailkinds.switch(rules, kind)
        if switch == mailkinds.OFF:
            audit(db, kind=kind, version=version, to=to, result=OFF,
                  detail=f"{mailkinds.policy_key(kind)} is off", now=now)
            return Outcome(kind, OFF, version, detail=f"{mailkinds.policy_key(kind)} is off")
        if switch == mailkinds.ADMINS:
            if is_admin is None:
                from .common import is_admin_address as is_admin
            if not is_admin(to):
                audit(db, kind=kind, version=version, to=to, result=ADMINS_ONLY,
                      detail=f"{mailkinds.policy_key(kind)} is admins", now=now)
                return Outcome(kind, ADMINS_ONLY, version,
                               detail=f"{mailkinds.policy_key(kind)} is admins")
        if not k.locked:
            cap = cap_for(rules, kind)
            if cap and sent_today(db, kind, now) >= cap:
                audit(db, kind=kind, version=version, to=to, result=CAPPED,
                      detail=f"daily cap {cap} reached", now=now)
                return Outcome(kind, CAPPED, version, detail=f"daily cap {cap} reached")
    if subject is None or body is None:
        if k.template is None:
            raise ValueError(f"mail kind {kind!r} has no template — the caller composes it")
        rendered_subject, rendered_body = mailtext.render_text(
            raw, uid or policies.COMPANY_READER, values)
        subject = subject if subject is not None else (rendered_subject or k.title)
        body = body if body is not None else rendered_body
    from . import emailx
    try:
        refused = emailx.relay_refusal(to)
        if refused:
            raise emailx.MailSuppressed(to, refused)
        send_one = sender if sender is not None else _notify.notify
        mid = send_one(to, subject, body, link=link, in_reply_to=in_reply_to)
    except emailx.MailSuppressed as e:
        audit(db, kind=kind, version=version, to=to, result=SUPPRESSED, detail=e.why,
              test=test, now=now)
        return Outcome(kind, SUPPRESSED, version, detail=e.why)
    except Exception as e:
        audit(db, kind=kind, version=version, to=to, result=FAILED,
              detail=f"{type(e).__name__}: {e}"[:300], test=test, now=now)
        raise
    delivery = emailx.delivery_mode()
    audit(db, kind=kind, version=version, to=to, result=SENT, message_id=mid, test=test, now=now,
          detail=delivery)
    return Outcome(kind, SENT, version, message_id=mid, delivery=delivery)


def preview(kind: str, *, uid: str = "", sample: Optional[dict] = None,
            draft: Optional[str] = None) -> dict:
    """Render a kind with its sample values (overridden by `sample`), from the live template or from
    an unsaved `draft`. Sends nothing."""
    k = mailkinds.get(kind)
    if k.template is None:
        raise ValueError(f"mail kind {kind!r} has no editable template")
    reader = uid or policies.COMPANY_READER
    if draft is not None and draft.strip():
        raw, source = draft, "draft"
    else:
        raw, source = _template(k, uid)
    values = {**k.sample, **(sample or {})}
    subject, body = mailtext.render_text(raw, reader, values)
    if k.link:
        body = _notify.compose(body, "https://<the link Vexa appends>").rstrip("\n")
    return {"kind": kind, "template": k.template, "source": source,
            "template_version": mailtext.version(raw), "subject": subject or k.title,
            "body": body, "values": values}
