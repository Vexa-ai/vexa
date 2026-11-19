"""taint.py — a tool this edge calls DIRECTLY (crm, flows) reports its privacy label to the gateway (D6).

Tools that go back through the gateway are marked there, from the label on the answer. A tool this
edge calls at its domain's own door (no `forward`) never passes the gateway, so this edge:

* before the call, asks the gateway for the session's current mark and forwards it to the domain as
  ``X-Vexa-Taint`` (crm-api holds a CRM write or a card proposal from a marked session on it);
* after the call, reads the answer's ``X-Vexa-Privacy`` (no label = private) and reports it on
  ``POST /internal/mcp/taint`` with the request's signed re-entry; the gateway takes the unit from the
  identity it resolved, never from here;
* when the report fails, WITHHOLDS the answer (503 ``privacy_mark_unavailable``): private data never
  reaches a session the hold cannot see.

Stateless: no Redis here, and no decision — the gateway marks, the domain holds.
"""
from __future__ import annotations

import re
from typing import Optional

import httpx

from . import reentry as reentry_mod

PATH = "/internal/mcp/taint"
LABEL_HEADER = "x-vexa-privacy"
STATE_HEADER = "X-Vexa-Taint"
UNVERIFIABLE = "unverifiable"
TIMEOUT_S = 5.0


def _clean(s) -> str:
    return re.sub(r"[^a-z0-9_.:() -]", "", str(s or "").strip().lower())[:64]


def parse_label(value: Optional[str]) -> frozenset:
    """The private classes an answer's label names; missing or unreadable = ``unlabelled``."""
    raw = str(value or "").strip().lower()
    if raw in ("none", "shareable"):
        return frozenset()
    if not raw.startswith("private"):
        return frozenset({"unlabelled"})
    _, _, rest = raw.partition(";")
    classes = set()
    for part in rest.split(";"):
        k, _, v = part.strip().partition("=")
        if k.strip() == "class":
            classes |= {_clean(c) for c in v.split(",") if _clean(c)}
    return frozenset(classes or {"private"})


async def _post(gateway_url: str, caller_key: str, sources: list, transport) -> Optional[dict]:
    if not gateway_url or not caller_key:
        return None
    headers = {"X-API-Key": caller_key, **reentry_mod.headers()}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=transport) as client:
            r = await client.post(gateway_url.rstrip("/") + PATH, headers=headers, json={"sources": sources})
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        body = r.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def behind_gateway() -> bool:
    """Did this request come through the gateway (it signed an identity onto it)? Every worker's call
    does; a request with none was never a worker's, and has no session to mark — an in-process test
    harness, or a call that would be refused at any service anyway."""
    return bool(reentry_mod.headers())


async def state(gateway_url: Optional[str], caller_key: str, transport=None) -> str:
    """The session's current mark for ``X-Vexa-Taint``: ``none``, the sources, or ``unverifiable``."""
    if not behind_gateway():
        return UNVERIFIABLE
    body = await _post(gateway_url or "", caller_key, [], transport)
    return str((body or {}).get("state") or UNVERIFIABLE)


async def report(gateway_url: Optional[str], caller_key: str, label: Optional[str], transport=None) -> bool:
    """Report a direct read's label. False = the answer must be withheld."""
    found = sorted(parse_label(label))
    if not found or not behind_gateway():
        return True
    body = await _post(gateway_url or "", caller_key, found, transport)
    return body is not None


def withheld_body() -> dict:
    return {"refused": "privacy_mark_unavailable",
            "fault": {"code": "privacy_mark_unavailable", "retry": "later"},
            "instruction": ("This tool returns the person's private data, and the privacy mark that "
                            "keeps it out of shared pages could not be recorded, so its answer was "
                            "withheld. Tell the person in one line and try again later.")}
