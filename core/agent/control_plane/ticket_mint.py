"""agent-api's subject tickets: the producer signature on what agent-api tells flows about a person.

flows reaches the other domains through the gateway's service door, and the door refuses a call
that acts for a person unless a ticket a producer signed names that person for that call class
(`subject_ticket`). agent-api is the producer of two facts:

* a ROUTINE GRANT — the person, from their own signed-in session, armed a routine (created it on,
  or turned it on). flows keeps it with the flow version and presents it on every run.
* a MESSAGE TICKET — an event ticket for the person whose chat a message, notice or announcement
  is posted into.

The key (`VEXA_SUBJECT_TICKET_KEY_FILE`, Ed25519 PKCS#8 PEM) is agent-api's alone; the door holds
its public half. With no key, or one that cannot be read, agent-api still talks to flows, without a
ticket, and logs once that the door will refuse the calls that follow.
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Optional

from control_plane import subject_ticket

log = logging.getLogger(__name__)

KEY_ENV = "VEXA_SUBJECT_TICKET_KEY_FILE"
#: The header a routine grant travels in on a resume (beside the `grant` body field).
GRANT_HEADER = "X-Vexa-Subject-Grant"
#: What a routine the person turned on lets flows do for them: its turns, and its bots.
GRANT_CLASSES = (subject_ticket.ROUTINE_TURN, subject_ticket.BOT_DISPATCH)
GRANT_TTL_SEC = 400 * 86400
#: What a message lets flows do for its recipient: the one turn or notice in their chat.
MESSAGE_CLASSES = (subject_ticket.ROUTINE_TURN,)
MESSAGE_TTL_SEC = 86400

_signers: dict = {}


def _signer() -> Optional[Callable[[bytes], bytes]]:
    """`key.sign` for the configured key, loaded once per path; None (one warning per path) when
    no key is configured or it cannot be read."""
    path = (os.getenv("VEXA_SUBJECT_TICKET_KEY_FILE") or "").strip()
    if path in _signers:
        return _signers[path]
    sign = None
    if not path:
        log.warning("subject_ticket_key_missing: %s is not set; routines and messages reach flows "
                    "without a subject ticket and the gateway door will refuse the flows calls "
                    "they cause (subject_ticket_required)", KEY_ENV)
    else:
        try:
            from control_plane.identity_token import read_signing_key  # noqa: PLC0415
            sign = read_signing_key(path).sign
        except Exception as e:  # noqa: BLE001 — flows is still told; the door refuses later
            log.warning("subject_ticket_key_unreadable: %s=%s could not be loaded (%s); routines "
                        "and messages reach flows without a subject ticket and the gateway door "
                        "will refuse the flows calls they cause (subject_ticket_required)",
                        KEY_ENV, path, type(e).__name__)
    _signers[path] = sign
    return sign


def _mint(*, kind: str, sub, cls, evt: str, ttl: int) -> str:
    sign = _signer()
    if sign is None or not str(sub or "").strip():
        return ""
    try:
        return subject_ticket.mint(sign, iss=subject_ticket.AGENT, kind=kind, sub=sub, cls=cls,
                                   evt=evt, ttl=ttl)
    except Exception as e:  # noqa: BLE001 — flows is still told; the door refuses later
        log.warning("subject_ticket_mint_failed: %s %s for %s (%s)", kind, evt, sub, e)
        return ""


def routine_grant(uid, name: str) -> str:
    """The grant for routine `name` of person `uid`, armed from their own session; "" without a
    key."""
    return _mint(kind=subject_ticket.GRANT, sub=uid, cls=GRANT_CLASSES,
                 evt=f"user:{uid}/{name}", ttl=GRANT_TTL_SEC)


def message_ticket(uid, evt: str) -> str:
    """An event ticket for posting into `uid`'s chat because of fact `evt`; "" without a key."""
    return _mint(kind=subject_ticket.EVENT, sub=uid, cls=MESSAGE_CLASSES, evt=evt,
                 ttl=MESSAGE_TTL_SEC)
