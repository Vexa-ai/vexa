"""The mail issuer of subject tickets (subject-ticket.v1, `iss: mail`).

Mail reaches Vexa inside the flows tier — the mailbox lane (`flows_integrations.mailbox`) and the
worker's `mailbox` sensor (`flows_sensors.mailbox`) — so the producer that vouches for an
authenticated invite or reply is here, and signs with ITS OWN key: a PKCS#8 Ed25519 PEM at
`VEXA_FLOWS_MAIL_TICKET_KEY_FILE`, mounted only into the processes that read mail. The gateway's
service door holds the public half (`mail.pem`).

Two tickets, minted only after the intake's own checks passed (the DMARC verdict, the organizer
being who sent it, the allow-list):

* an INVITE (`invite.received`): for the authenticated organizer (`addr`, and `sub` when they are
  a user), bound to the invite's meeting (`native`), naming its attendees (`peers`), opening the
  bot for that meeting once (`once: bot.dispatch`), its transcript, an autonomous turn and attendee
  desks — until a day after the meeting starts;
* a REPLY (`mail.reply`): for the thread's person, from its authenticated sender, opening the
  reply's turn and their desk for a day.

No key, no ticket: the fact is admitted as before and the door refuses the bound calls it would
have opened, which a named warning (once per process) says. Minting never raises into the intake.
"""
from __future__ import annotations

import os
import time
from typing import Optional

import flows_obs
from flows_integrations import fact_keys
from flows_steps import subject_ticket as st

#: The mail issuer's private key file (PKCS#8 PEM, Ed25519).
KEY_FILE_ENV = "VEXA_FLOWS_MAIL_TICKET_KEY_FILE"

INVITE_CLASSES = (st.BOT_DISPATCH, st.TRANSCRIPT_READ, st.DESK_WRITE, st.CHAT_AUTONOMOUS)
INVITE_ONCE = (st.BOT_DISPATCH,)
REPLY_CLASSES = (st.CHAT_AUTONOMOUS, st.DESK_WRITE)
REPLY_TTL_S = 86400
#: an invite ticket lives until a day after its meeting starts, and never less than an hour
AFTER_START_S = 86400
MIN_TTL_S = 3600

_signers: dict = {}
_warned: set = set()


def _warn_once(reason: str) -> None:
    if reason in _warned:
        return
    _warned.add(reason)
    flows_obs.log_event("mail_ticket_unavailable", level="warning",
                        fields={"reason": reason, "key": KEY_FILE_ENV})


def signer():
    """The mail issuer's signer, or None (no key configured, or not a readable Ed25519 key)."""
    path = (os.environ.get(KEY_FILE_ENV) or "").strip()
    if not path:
        _warn_once("unset")
        return None
    if path not in _signers:
        _signers[path] = fact_keys.signer_from_file(path)
    if _signers[path] is None:
        _warn_once("unreadable")
    return _signers[path]


def _mint(**claims) -> str:
    sign = signer()
    if sign is None:
        return ""
    try:
        return st.mint(sign, iss=st.MAIL, **claims)
    except Exception as e:  # noqa: BLE001 — a ticket we cannot mint never stops the intake
        flows_obs.log_event("mail_ticket_refused", level="warning",
                            fields={"evt": str(claims.get("evt") or "")[:120],
                                    "error": f"{type(e).__name__}: {e}"[:200]})
        return ""


def invite_ticket(payload: dict, *, evt: str, sub: Optional[str] = "",
                  now: Optional[float] = None) -> str:
    """The ticket an authenticated invite carries, or "" (no key, nothing to bind)."""
    from flows_integrations.mailbox import invite_native
    t = float(now if now is not None else time.time())
    cap = st.MAX_LIFETIME[(st.MAIL, st.EVENT)]
    try:
        start = float(payload.get("start") or 0)
    except (TypeError, ValueError):
        start = 0.0
    ttl = int(min(cap, max(MIN_TTL_S, start + AFTER_START_S - t)))
    platform, native = invite_native(str(payload.get("url") or ""))
    return _mint(sub=str(sub or ""), addr=str(payload.get("organizer") or ""), evt=evt,
                 cls=INVITE_CLASSES, once=INVITE_ONCE, ttl=ttl, now=int(t),
                 native=st.native_of(platform, native),
                 peers=[p for p in payload.get("participants") or [] if isinstance(p, str)])


def reply_ticket(*, uid: str, addr: str, evt: str, now: Optional[float] = None) -> str:
    """The ticket an authenticated reply on a person's thread carries, or ""."""
    t = float(now if now is not None else time.time())
    return _mint(sub=str(uid or ""), addr=str(addr or ""), evt=evt, cls=REPLY_CLASSES,
                 ttl=REPLY_TTL_S, now=int(t))


def carrying(refs: dict, ticket: str) -> dict:
    """`refs` with `ticket` among its `_tickets` (unchanged when there is none)."""
    if not ticket:
        return refs
    held = st.tickets_of(refs)
    return {**refs, st.REFS_KEY: st.merged(held, [ticket])}
