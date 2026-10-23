"""THE WORKER PASS: wake due rows, ask, decide, act, record. Called from ``flows_worker`` every few
seconds while ``VEXA_FLOWS_NUDGES`` is on; never raises into the loop.

Two passes, in this order each time:

* ``run_due``      rows in ``armed`` / ``notified`` whose ``wake_at`` passed: the in-app notice, then
                   after the idle window the ONE email (or the digest, when the budget is spent).
* ``run_digests``  people whose digest is due: re-check every item, drop the seen ones, and send what
                   is left as ONE batched email — one delivery against the budget, however many items.

AT MOST ONCE. Every email is reserved in ``delivery_send`` under a deterministic id before the
transport is called and settled after. A crash between the two leaves a ``sending`` receipt, and the
retry, finding the id taken, sends nothing (P18: the receipt is consulted before acting).
"""
from __future__ import annotations

import logging
from typing import Optional

from . import gate, policy as P, store, templates
from .gate import Attention
from .ports import Ports, card_id

_log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_S = 300.0


class _People:
    """One identity read per person per pass."""

    def __init__(self, ports: Ports, lim: P.Limits) -> None:
        self.ports, self.lim, self.cache = ports, lim, {}

    def get(self, uid: str):
        if uid not in self.cache:
            try:
                raw, tz, addr = self.ports.person(uid)
                self.cache[uid] = (P.normalize(raw, self.lim), tz, addr)
            except Exception as exc:  # noqa: BLE001 — unknown preferences are not a default
                _log.warning("delivery: settings unavailable for a person (%s)", type(exc).__name__)
                self.cache[uid] = None
        return self.cache[uid]


def _fields(row: dict, tmpl: dict) -> dict:
    """The template's whole view of an item: kind, title, who, room, workspace, link."""
    link = row.get("link") or (templates.card_link(card_id(row["ref"])) if card_id(row["ref"]) else "/")
    return {"kind": row.get("kind") or "card", "title": tmpl.get("title", ""), "who": tmpl.get("who", ""),
            "room": tmpl.get("room", ""), "workspace": tmpl.get("workspace", ""), "link": link}


def _attention(ports: Ports, uid: str, rows: list[dict]) -> dict:
    try:
        return ports.attention(uid, rows) or {}
    except Exception as exc:  # noqa: BLE001 — unknown, never a guess
        _log.warning("delivery: attention unavailable (%s)", type(exc).__name__)
        return {}


def run_due(db, clock, ports: Ports, *, lim: Optional[P.Limits] = None, limit: int = 200) -> dict:
    lim = lim or P.limits()
    now = clock.now()
    people = _People(ports, lim)
    counts: dict = {}
    rows = store.due(db, now, limit=limit)
    by_person: dict[str, list[dict]] = {}
    for r in rows:
        by_person.setdefault(r["person"], []).append(r)
    for uid, mine in by_person.items():
        ctx = people.get(uid)
        if ctx is None:
            for r in mine:
                store.move(db, now, uid, r["ref"], stage=r["stage"], wake_at=now + gate.RECHECK_S)
            counts["deferred"] = counts.get("deferred", 0) + len(mine)
            continue
        pol, tz, addr = ctx
        atts = _attention(ports, uid, mine)
        for r in mine:
            got = atts.get(r["ref"])
            att, tmpl = (got if got else (None, {}))
            sent = store.sent_today(db, uid, P.local_day(now, tz))
            d = gate.decide(r, att=att, pol=pol, tz=tz, now=now, sent_today=sent)
            outcome = _act(db, now, ports, r, d, pol, tz, addr, _fields(r, tmpl))
            counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def _act(db, now, ports: Ports, r: dict, d: gate.Decision, pol: dict, tz: str, addr: str, fields: dict) -> str:
    uid, ref = r["person"], r["ref"]
    if d.action == "defer":
        store.move(db, now, uid, ref, stage=r["stage"], wake_at=d.wake_at)
        return "deferred"
    if d.action == "done":
        store.finish(db, now, uid, ref, d.outcome or "done")
        return d.outcome or "done"
    if d.action == "digest":
        store.move(db, now, uid, ref, stage="digest", wake_at=d.wake_at)
        return "to_digest"
    if d.action == "notify":
        # A room item arrives with the notice agent-api already wrote (a mention, a join request):
        # it is not written twice. A card's notice is ours to ask for.
        notice = r.get("notice_id") or ports.in_app(uid, r, fields)
        store.move(db, now, uid, ref, stage="notified", wake_at=d.wake_at, notice_id=notice or None,
                   notified_at=now)
        return "notified"
    if d.action == "email":
        return _email(db, now, ports, r, pol, tz, addr, fields)
    store.move(db, now, uid, ref, stage=r["stage"], wake_at=now + gate.RECHECK_S)
    return "deferred"


def _email(db, now, ports: Ports, r: dict, pol: dict, tz: str, addr: str, fields: dict) -> str:
    uid, ref = r["person"], r["ref"]
    if not addr or "@" not in addr:
        store.finish(db, now, uid, ref, "no_address")
        return "no_address"
    nxt = gate.after_email(now, pol, tz)
    send_id = f"{uid}|{ref}|single|v{r.get('version') or 1}|a{r.get('attempts') or 0}"
    try:
        subject, body, url = templates.render_single(fields, ui=ports.ui, budget=pol["budget"])
    except (templates.FreeTextRefused, ValueError) as exc:
        _log.warning("delivery: nudge not rendered (%s)", exc)
        store.finish(db, now, uid, ref, "failed")
        return "failed"
    if not store.reserve(db, now, send_id=send_id, person=uid, day=P.local_day(now, tz),
                         kind="single", refs=[ref]):
        # this exact email was already attempted (a crash after the reservation): never twice
        _next(db, now, uid, ref, nxt, emailed_at=now)
        return "already_sent"
    try:
        handle = ports.send(addr, subject, body, url)
    except Exception as exc:  # noqa: BLE001 — typed below; a transport failure is a receipt, not a crash
        store.settle(db, send_id, "failed", reason=f"{type(exc).__name__}: {exc}")
        attempts = int(r.get("attempts") or 0) + 1
        retryable = bool(getattr(exc, "retryable", True))
        if retryable and attempts < MAX_ATTEMPTS:
            store.move(db, now, uid, ref, stage=r["stage"], wake_at=now + RETRY_S, attempts=attempts)
            return "retrying"
        store.move(db, now, uid, ref, stage="done", wake_at=now, attempts=attempts, outcome="failed")
        try:
            ports.failed(r.get("owner") or "", r, fields)
        except Exception:  # noqa: BLE001 — the fault notice is a publish
            pass
        _log.warning("delivery_failed: %s", type(exc).__name__)
        return "failed"
    store.settle(db, send_id, "sent", handle=str(handle or ""))
    _next(db, now, uid, ref, nxt, emailed_at=now)
    return "emailed"


def _next(db, now, uid, ref, nxt: gate.Decision, **cols) -> None:
    if nxt.action == "digest":
        store.move(db, now, uid, ref, stage="digest", wake_at=nxt.wake_at, **cols)
    else:
        store.move(db, now, uid, ref, stage="done", wake_at=now, outcome=nxt.outcome or "emailed", **cols)


def run_digests(db, clock, ports: Ports, *, lim: Optional[P.Limits] = None) -> dict:
    lim = lim or P.limits()
    now = clock.now()
    people = _People(ports, lim)
    counts: dict = {}
    for uid in store.digest_people(db, now):
        rows = store.digest_rows(db, now, uid)
        ctx = people.get(uid)
        if ctx is None or not rows:
            for r in rows:
                store.move(db, now, uid, r["ref"], stage="digest", wake_at=now + gate.RECHECK_S)
            continue
        pol, tz, addr = ctx
        atts = _attention(ports, uid, rows)
        keep: list[tuple[dict, dict]] = []
        for r in rows:
            got = atts.get(r["ref"])
            if not got:
                store.move(db, now, uid, r["ref"], stage="digest", wake_at=now + gate.RECHECK_S)
                continue
            att, tmpl = got
            outcome = ("closed" if not att.open else "acted" if att.acted else "seen" if att.seen else None)
            if outcome is None and not P.interrupts(r.get("kind") or "", P.level_for(
                    pol, room=r.get("room") or "", workspace=r.get("workspace") or "")):
                outcome = "muted"
            if outcome:
                store.finish(db, now, uid, r["ref"], outcome)
                counts[outcome] = counts.get(outcome, 0) + 1
                continue
            keep.append((r, _fields(r, tmpl)))
        if not keep:
            continue
        d = gate.digest_gate(pol=pol, tz=tz, now=now, sent_today=store.sent_today(db, uid, P.local_day(now, tz)))
        if d.action == "defer":
            for r, _ in keep:
                store.move(db, now, uid, r["ref"], stage="digest", wake_at=d.wake_at)
            counts["deferred"] = counts.get("deferred", 0) + len(keep)
            continue
        if d.action == "done" or not addr or "@" not in addr:
            for r, _ in keep:
                store.finish(db, now, uid, r["ref"], d.outcome or "no_address")
            continue
        slot = int(min(float(r["wake_at"]) for r, _ in keep))
        send_id = f"{uid}|digest|{slot}"
        refs = [r["ref"] for r, _ in keep]
        try:
            subject, body = templates.render_digest([f for _, f in keep], ui=ports.ui)
        except (templates.FreeTextRefused, ValueError) as exc:
            _log.warning("delivery: digest not rendered (%s)", exc)
            for r, _ in keep:
                store.finish(db, now, uid, r["ref"], "failed")
            continue
        if not store.reserve(db, now, send_id=send_id, person=uid, day=P.local_day(now, tz),
                             kind="digest", refs=refs):
            for r, _ in keep:
                store.move(db, now, uid, r["ref"], stage="done", wake_at=now, outcome="digested",
                           digested_at=now)
            continue
        try:
            handle = ports.send(addr, subject, body, "")
        except Exception as exc:  # noqa: BLE001
            store.settle(db, send_id, "failed", reason=f"{type(exc).__name__}: {exc}")
            for r, _ in keep:
                attempts = int(r.get("attempts") or 0) + 1
                if attempts < MAX_ATTEMPTS and bool(getattr(exc, "retryable", True)):
                    store.move(db, now, uid, r["ref"], stage="digest", wake_at=now + RETRY_S, attempts=attempts)
                else:
                    store.move(db, now, uid, r["ref"], stage="done", wake_at=now, attempts=attempts,
                               outcome="failed")
            counts["failed"] = counts.get("failed", 0) + 1
            continue
        store.settle(db, send_id, "sent", handle=str(handle or ""))
        for r, _ in keep:
            store.move(db, now, uid, r["ref"], stage="done", wake_at=now, outcome="digested", digested_at=now)
        counts["digests"] = counts.get("digests", 0) + 1
    return counts


def tick(db, clock, ports: Optional[Ports] = None) -> dict:
    """Both passes, for the worker loop. Never raises."""
    try:
        if ports is None:
            from .ports import HttpPorts
            ports = HttpPorts()
        lim = P.limits()
        out = run_due(db, clock, ports, lim=lim)
        dig = run_digests(db, clock, ports, lim=lim)
        return {**out, **{f"digest_{k}": v for k, v in dig.items()}}
    except Exception as exc:  # noqa: BLE001 — a delivery pass never stops the loop
        _log.warning("delivery pass failed: %s", type(exc).__name__)
        return {"error": type(exc).__name__}


__all__ = ["run_due", "run_digests", "tick", "Attention"]
