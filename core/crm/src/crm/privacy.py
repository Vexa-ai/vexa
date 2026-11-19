"""privacy.py — the CRM's half of the private-data hold (D6, D20).

* EVERY CRM ANSWER IS LABELLED PRIVATE (``X-Vexa-Privacy: private; class=crm``). The gateway/MCP edge
  marks the worker's session from it. S1-C's per-field classification (D4: ``private`` · ``org`` ·
  ``ws-shareable``) will narrow the label to what an answer actually returned; until then a CRM read is
  private, which fails closed.
* A CRM WRITE OR A CARD PROPOSAL FROM A MARKED SESSION IS HELD. Both land where other people read them
  (the org's records, a board's members). The edge forwards the session's current mark as
  ``X-Vexa-Taint``; only ``none`` lets a worker's write through — missing, a source list or
  ``unverifiable`` is held (409 ``private_sources_to_shared``, typed fault ``held_private_egress``).
  A person's own credential is never held: the person is the one deciding to share.

Nothing held is stored here: the content stays in the model's context, and the person's own chat is
where it is shared (D20: a copy into a multi-reader surface becomes a share card).
"""
from __future__ import annotations

from fastapi import HTTPException, Request

LABEL_HEADER = "x-vexa-privacy"
CRM_LABEL = "private; class=crm"
STATE_HEADER = "x-vexa-taint"
REASON = "private_sources_to_shared"
FAULT = "held_private_egress"


def _clean(value) -> bool:
    return str(value or "").strip().lower() == "none"


def hold(request: Request, caller, *, surface: str) -> None:
    """Refuse a worker's write into a multi-reader surface unless its session is unmarked."""
    if not getattr(caller, "delegated", False):
        return
    state = request.headers.get(STATE_HEADER)
    if _clean(state):
        return
    sources = sorted(s for s in str(state or "unverifiable").split(",") if s.strip()) or ["unverifiable"]
    raise HTTPException(409, {
        "refused": REASON,
        "fault": {"code": FAULT, "surface": surface, "held": False,
                  "unverifiable": sources == ["unverifiable"]},
        "surface": surface,
        "sources": sources,
        "instruction": ("Nothing was written. This session read the person's private data "
                        f"({', '.join(sources)}), and a CRM record or a card is read by other people. "
                        "Tell the person in one line what you wanted to record and why it waits, and "
                        "ask them to share it from their own chat. Do not write it another way."),
    })
