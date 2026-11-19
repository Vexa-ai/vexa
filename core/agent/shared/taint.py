"""taint.py — the persisted privacy mark on an agent session, and the label a read carries (D6, SC4).

WHAT IT IS. A session that read the person's private data — CRM fields, a transcript, mail, the
calendar, a connection, a person-wide route, a card's private fields — is MARKED. While it is marked,
anything it writes into a surface other people read (a page, a filename, a commit body, a message
into a shared session, a room message, a notice, a card or proposal field, a CRM write, a nudge) is
HELD for the person. This module is the one spelling of where that mark lives and how a read says
it is private; `control_plane/private_taint.py` (agent-api), `llm/private_writes.py` (the worker)
and the gateway's `taint.py` read and write it.

A LABEL ON DATA, NOT A LIST OF TOOL NAMES. The service that answers a read says what it returned:
``X-Vexa-Privacy: private; class=crm`` or ``X-Vexa-Privacy: none``. A response WITHOUT the header is
treated as private (``class=unlabelled``): a route nobody classified fails closed. The gateway edge
reads the label off every delegated (worker) response and writes the mark; the MCP edge does the
same for the tools it calls directly (crm, flows) through the gateway's ``/internal/mcp/taint``.

PERSISTED, NOT IN A PROCESS. The mark is a Redis hash per unit AND per conversation session, with a
TTL, so it survives an agent-api restart, a second replica and a worker idle exit, and follows the
conversation to a sibling unit (``-auto``, ``-signal``) or a new mount generation (``-g<n>``) — the
model's context travels with the session, so the mark must too.

FAILS CLOSED. A store that cannot be read is answered as ``UNVERIFIABLE``: the session counts as
held. A mark that cannot be written refuses the read that caused it (the edge answers 503
``privacy_mark_unavailable`` instead of the data), so private data never reaches an unmarked unit.

Keys (one writer each):

* ``vexa:taint:unit:<unit>``       — hash source → epoch; written by the gateway and agent-api.
* ``vexa:taint:session:<session>`` — the same, keyed by the conversation the unit belongs to.
* ``vexa:taint:wsession:<session>`` — the worker's OWN observations (a personal MCP connector the
  edges never see); the only taint key a worker's Redis user may write (``workload_redis``).
"""
from __future__ import annotations

import re
import time
from typing import Iterable, Optional

#: How long a session stays marked after its last private read (what the model read stays in its
#: context for later turns of the same conversation).
TTL_SEC = 12 * 3600

PREFIX = "vexa:taint"
#: The response header a read carries (`label`).
HEADER = "X-Vexa-Privacy"
#: The header the gateway forwards to a service with a worker's request: the session's current mark
#: (``none``, a comma list of sources, or ``unverifiable``), so a service with no Redis can hold.
STATE_HEADER = "X-Vexa-Taint"
#: The source name a session is held under when the store cannot be read.
UNVERIFIABLE = "unverifiable"
#: The source name an unlabelled read is marked with.
UNLABELLED = "unlabelled"

_SESSION = re.compile(
    r"^(?P<base>agent-.+?-(?:chat|room)-.+?)(?:-g[0-9A-Za-z]+)?(?:-read)?(?:-auto|-signal)?$")


class StoreUnavailable(RuntimeError):
    """The mark store could not be read or written. Callers fail closed."""


def unit_key(unit: str) -> str:
    return f"{PREFIX}:unit:{unit}"


def session_key(session: str) -> str:
    return f"{PREFIX}:session:{session}"


def worker_key(session: str) -> str:
    return f"{PREFIX}:wsession:{session}"


def session_of_unit(unit: str) -> str:
    """The conversation a unit belongs to: a chat or room unit without its mount generation and its
    sibling suffix; any other unit is its own session."""
    unit = str(unit or "").strip()
    m = _SESSION.match(unit)
    return m.group("base") if m else unit


def keys_for(unit: str) -> tuple[str, str, str]:
    s = session_of_unit(unit)
    return unit_key(unit), session_key(s), worker_key(s)


# ── the label a read carries ───────────────────────────────────────────────────────────────────────

def format_label(classes: Optional[Iterable[str]]) -> str:
    """The header value: ``none`` for nothing private, else ``private; class=a,b``."""
    found = sorted({_clean(c) for c in (classes or ()) if _clean(c)})
    return "private; class=" + ",".join(found) if found else "none"


def parse_label(value: Optional[str]) -> frozenset:
    """The private classes a response's label names. Empty = nothing private (``none``). A missing,
    empty or unreadable label is private (``unlabelled``): fail closed."""
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


def format_state(sources: Iterable[str]) -> str:
    found = sorted({_clean(s) for s in sources if _clean(s)})
    return ",".join(found) if found else "none"


def state_is_clean(value: Optional[str]) -> bool:
    """A forwarded ``X-Vexa-Taint`` that says nothing private. Missing or anything else is held."""
    return str(value or "").strip().lower() == "none"


def _clean(s) -> str:
    return re.sub(r"[^a-z0-9_.:() -]", "", str(s or "").strip().lower())[:64]


# ── the store ──────────────────────────────────────────────────────────────────────────────────────

class TaintStore:
    """The mark over a redis-py client (sync). Every failure raises ``StoreUnavailable``."""

    def __init__(self, client, *, ttl: int = TTL_SEC) -> None:
        self.client = client
        self.ttl = int(ttl)

    def mark(self, unit: str, sources: Iterable[str], *, worker: bool = False) -> None:
        unit = str(unit or "").strip()
        found = {_clean(s) for s in sources if _clean(s)}
        if not unit or not found:
            return
        now = str(int(time.time()))
        ukey, skey, wkey = keys_for(unit)
        targets = (wkey,) if worker else (ukey, skey)
        try:
            pipe = self.client.pipeline(transaction=False)   # no MULTI: a worker's user has no grant for it
            for key in targets:
                pipe.hset(key, mapping={s: now for s in found})
                pipe.expire(key, self.ttl)
            pipe.execute()
        except Exception as exc:  # noqa: BLE001 — any store error is the same answer
            raise StoreUnavailable(type(exc).__name__) from exc

    def read(self, unit: str) -> set:
        unit = str(unit or "").strip()
        if not unit:
            return set()
        cutoff = time.time() - self.ttl
        out: set = set()
        try:
            for key in keys_for(unit):
                for src, at in (self.client.hgetall(key) or {}).items():
                    src = src.decode() if isinstance(src, bytes) else str(src)
                    at = at.decode() if isinstance(at, bytes) else at
                    try:
                        if float(at) >= cutoff:
                            out.add(src)
                    except (TypeError, ValueError):
                        out.add(src)          # an unreadable stamp is still a mark
        except Exception as exc:  # noqa: BLE001
            raise StoreUnavailable(type(exc).__name__) from exc
        return out

    def clear(self, unit: str) -> None:
        """Tests and the person's explicit 'forget this chat' only."""
        try:
            self.client.delete(*keys_for(unit))
        except Exception as exc:  # noqa: BLE001
            raise StoreUnavailable(type(exc).__name__) from exc


_STORE: list = []


def configure(store: Optional[TaintStore]) -> None:
    """Install the process's store (agent-api at boot, the worker at start, a test per test). Lives
    in this module so a reload of the modules that use it never forgets it."""
    _STORE.clear()
    if store is not None:
        _STORE.append(store)


def current() -> Optional[TaintStore]:
    return _STORE[0] if _STORE else None


def sources(unit: str) -> set:
    """What ``unit``'s session read that was private, through the installed store. No store, or a
    store that cannot answer, is ``{UNVERIFIABLE}``: held."""
    store = current()
    if store is None:
        return {UNVERIFIABLE}
    try:
        return store.read(unit)
    except StoreUnavailable:
        return {UNVERIFIABLE}


def mark(unit: str, sources_: Iterable[str]) -> None:
    """Mark ``unit``. Raises ``StoreUnavailable`` when there is no store or it cannot be written, so
    the caller can refuse the read rather than hand private data to an unmarked session."""
    store = current()
    if store is None:
        raise StoreUnavailable("no privacy store configured")
    store.mark(unit, sources_)
