"""taint.py — the edge MARKS a worker's session when a read it forwarded returned private data (D6).

The service that answers a read labels it (``X-Vexa-Privacy: private; class=crm`` or ``none``). For a
worker's request (a delegation token naming a unit) the edge writes the mark into Redis — the unit and
its conversation, with a TTL — before the answer leaves. An answer with no label is private: a route
nobody classified fails closed. A mark that cannot be written REFUSES the answer (503
``privacy_mark_unavailable``): private data never reaches a session the hold cannot see.

The MCP calls some domains directly (crm, flows) rather than through this edge; for those it reports
the label here, on ``POST /internal/mcp/taint``, admitted only on its signed re-entry — the unit is
read from the identity this edge signed, never from the MCP.

The edge also forwards the session's current mark to the service (``X-Vexa-Taint``), so a service
with no Redis of its own (crm-api) can hold a write into a multi-reader surface.

The edge only MARKS and reports; every hold is enforced by the service that owns the surface.

Key spelling is ``core/agent/shared/taint.py``'s, word for word (this package cannot import it).
"""
from __future__ import annotations

import re
import time
from typing import Iterable, Optional

TTL_SEC = 12 * 3600
LABEL_HEADER = "x-vexa-privacy"
STATE_HEADER = "x-vexa-taint"
UNLABELLED = "unlabelled"
UNVERIFIABLE = "unverifiable"
MARK_UNAVAILABLE = "privacy_mark_unavailable"
#: Domains whose answers mark a worker's session. The MCP front door itself is not one: its answers
#: are the tools' answers, and each tool's underlying read is marked where it happened.
MARKED_DOMAINS = frozenset({"agent", "crm", "meetings"})

_SESSION = re.compile(
    r"^(?P<base>agent-.+?-(?:chat|room)-.+?)(?:-g[0-9A-Za-z]+)?(?:-read)?(?:-auto|-signal)?$")


class MarkUnavailable(RuntimeError):
    pass


def session_of_unit(unit: str) -> str:
    unit = str(unit or "").strip()
    m = _SESSION.match(unit)
    return m.group("base") if m else unit


def keys_for(unit: str) -> tuple:
    s = session_of_unit(unit)
    return (f"vexa:taint:unit:{unit}", f"vexa:taint:session:{s}", f"vexa:taint:wsession:{s}")


def _clean(s) -> str:
    return re.sub(r"[^a-z0-9_.:() -]", "", str(s or "").strip().lower())[:64]


def parse_label(value: Optional[str]) -> frozenset:
    raw = str(value or "").strip().lower()
    if raw in ("none", "shareable"):
        return frozenset()
    if not raw.startswith("private"):
        return frozenset({UNLABELLED})
    _, _, rest = raw.partition(";")
    classes = set()
    for part in rest.split(";"):
        k, _, v = part.strip().partition("=")
        if k.strip() == "class":
            classes |= {_clean(c) for c in v.split(",") if _clean(c)}
    return frozenset(classes or {"private"})


def mark_unavailable_body() -> dict:
    return {"refused": MARK_UNAVAILABLE, "fault": {"code": MARK_UNAVAILABLE, "retry": "later"},
            "instruction": ("This read returns the person's private data, and the privacy mark that "
                            "keeps it out of shared pages could not be recorded right now, so it was "
                            "not returned. Tell the person in one line and try again later.")}


class TaintMarker:
    """The mark over a ``redis.asyncio`` client."""

    def __init__(self, client, *, ttl: int = TTL_SEC) -> None:
        self.client = client
        self.ttl = int(ttl)

    async def mark(self, unit: str, sources: Iterable[str]) -> None:
        found = {_clean(s) for s in sources if _clean(s)}
        unit = str(unit or "").strip()
        if not unit or not found:
            return
        now = str(int(time.time()))
        try:
            pipe = self.client.pipeline(transaction=False)
            for key in keys_for(unit)[:2]:
                pipe.hset(key, mapping={s: now for s in found})
                pipe.expire(key, self.ttl)
            await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            raise MarkUnavailable(type(exc).__name__) from exc

    async def read(self, unit: str) -> set:
        cutoff = time.time() - self.ttl
        out: set = set()
        try:
            for key in keys_for(unit):
                for src, at in (await self.client.hgetall(key) or {}).items():
                    src = src.decode() if isinstance(src, bytes) else str(src)
                    try:
                        if float(at) >= cutoff:
                            out.add(src)
                    except (TypeError, ValueError):
                        out.add(src)
        except Exception as exc:  # noqa: BLE001
            raise MarkUnavailable(type(exc).__name__) from exc
        return out


async def state_for(marker: Optional[TaintMarker], unit: str) -> str:
    """The ``X-Vexa-Taint`` value for a worker's request: ``none``, the sources, or ``unverifiable``."""
    if marker is None:
        return UNVERIFIABLE
    try:
        found = await marker.read(unit)
    except MarkUnavailable:
        return UNVERIFIABLE
    return ",".join(sorted(found)) if found else "none"


async def mark_from_response(marker: Optional[TaintMarker], unit: str, status: int,
                             label: Optional[str]) -> bool:
    """Mark ``unit`` from a forwarded answer. Returns False when the answer must be refused because
    it is private and the mark could not be written."""
    if not unit or not (200 <= int(status) < 300):
        return True
    found = parse_label(label)
    if not found:
        return True
    if marker is None:
        return False
    try:
        await marker.mark(unit, found)
    except MarkUnavailable:
        return False
    return True
