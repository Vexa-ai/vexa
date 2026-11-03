"""Unit streams are transport, so they stay transport-sized.

app.dev 2026-10-16: redis reached 10.7 GB. Every chat turn put the session's whole transcript bundle
(ADR-0048) inline on ``unit:<id>:in`` and the worker handed it back inline on ``unit:<id>:out`` —
~22 MB per entry, growing every turn — and no unit stream was ever trimmed or expired. A cold worker
then read its whole in-topic with ``XRANGE - +``, so spawns timed out behind 126 ms scans.

The conversation's durable home is the session store (postgres + object store); the streams only
carry it. So:

- a bundle travels BY REFERENCE: a sibling key with a TTL, the stream entry names it;
- every XADD is capped with an approximate MAXLEN, and the stream's TTL is refreshed on write, so an
  ended unit's streams expire on their own;
- the worker's boot read starts after its published cursor and is counted.
"""
from __future__ import annotations

import json
import uuid
from typing import Callable, Optional

#: How many entries a unit's in-topic keeps. A worker takes them within seconds; far more than any
#: realistic queue of unsent messages.
IN_MAXLEN = 200
#: How many entries a unit's out-topic keeps: several turns of deltas (``turn_outcome`` scans 4000).
OUT_MAXLEN = 5000
#: How long a handed bundle waits for its reader (the worker on IN, the session saver on OUT).
BUNDLE_TTL_S = 6 * 3600
#: An idle unit's streams expire this long after their last write — an ended unit cleans itself up.
UNIT_STREAM_TTL_S = 3 * 86400
#: The most entries a worker's boot read takes in one go.
BOOT_SCAN_MAX = 500


def bundle_key(unit_id: str, tag: str) -> str:
    return f"unit:{unit_id}:bundle:{tag}"


def handback_key(unit_id: str, tag: str) -> str:
    return f"unit:{unit_id}:handback:{tag}"


def _tag(tag: str = "") -> str:
    clean = "".join(c for c in str(tag or "") if c.isalnum() or c in "-_.")[:64]
    return f"{clean}-{uuid.uuid4().hex[:8]}" if clean else uuid.uuid4().hex


def attach_in_bundle(r, unit_id: str, entry: dict, bundle: dict) -> dict:
    """Store ``bundle`` beside the unit's in-topic and name it on ``entry`` (mutated, returned)."""
    key = bundle_key(unit_id, _tag(str(entry.get("nonce") or "").rsplit(":", 1)[-1]))
    r.set(key, json.dumps(bundle), ex=BUNDLE_TTL_S)
    entry.pop("session_bundle", None)
    entry["session_bundle_ref"] = key
    return entry


def resolve_in_bundle(getter: Optional[Callable], msg: dict) -> dict:
    """Put the referenced bundle back on ``msg`` as ``session_bundle``. An inline bundle (an older
    agent-api) is left alone; an expired or unreadable reference leaves none — the turn then runs on
    the workspace layout, as it does when no bundle was handed at all."""
    ref = msg.get("session_bundle_ref") if isinstance(msg, dict) else None
    if not ref or msg.get("session_bundle") is not None or getter is None:
        return msg
    try:
        raw = getter(ref)
        msg["session_bundle"] = json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001 — a bundle that cannot be read never blocks the turn
        msg["session_bundle"] = None
    return msg


def handback_by_ref(r, unit_id: str, ev: dict, tag: str = "") -> dict:
    """The ``session-bundle`` event with its files moved to a sibling key; the event names it."""
    key = handback_key(unit_id, _tag(tag))
    r.set(key, json.dumps(ev.get("files") or {}), ex=BUNDLE_TTL_S)
    small = {k: v for k, v in ev.items() if k != "files"}
    small["ref"] = key
    return small


def resolve_handback(getter: Optional[Callable], ev: dict) -> dict:
    """The event with its files read back from ``ref`` (an inline event is returned as it is)."""
    ref = ev.get("ref")
    if not ref or "files" in ev or getter is None:
        return ev
    raw = getter(ref)
    if not raw:
        raise KeyError("session bundle hand-back expired")
    return {**ev, "files": json.loads(raw)}


def xadd_capped(r, topic: str, fields: dict, maxlen: int) -> str:
    """XADD with an approximate MAXLEN, then refresh the stream's idle expiry."""
    try:
        eid = r.xadd(topic, fields, maxlen=maxlen, approximate=True)
    except TypeError:     # a facade without MAXLEN (test fakes): delivery outranks the cap
        eid = r.xadd(topic, fields)
    try:
        r.expire(topic, UNIT_STREAM_TTL_S)
    except Exception:  # noqa: BLE001 — retention is housekeeping, never the delivery
        pass
    return eid


class BoundedStream:
    """The worker's redis client with every XADD capped (the worker may not EXPIRE: agent-api's
    write to the in-topic and the expiry it refreshes there keep the unit's keys bounded)."""

    def __init__(self, client, maxlen: int = OUT_MAXLEN) -> None:
        self._c = client
        self._maxlen = maxlen

    def xadd(self, name, fields, *args, **kw):
        kw.setdefault("maxlen", self._maxlen)
        kw.setdefault("approximate", True)
        return self._c.xadd(name, fields, *args, **kw)

    def __getattr__(self, name):
        return getattr(self._c, name)


def boot_window(xrange: Callable, in_topic: str, prior: str = "", count: int = BOOT_SCAN_MAX) -> list:
    """What a booting worker reads of its in-topic: only after the cursor a previous worker
    published, and at most ``count`` entries — never ``XRANGE - +``."""
    lo = f"({prior}" if prior else "-"
    return xrange(in_topic, min=lo, max="+", count=count) or []
