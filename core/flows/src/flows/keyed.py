"""Keyed schedules — how "the bot follows the calendar event" works (ADR-0050 decision 3).

A `relative` trigger (`{kind: relative, event: "calendar.event.*", filter, at: "payload.start - 60s",
key: "payload.event_id"}`) does not start a run when its event arrives. It keeps ONE pending fire per
key and person in `keyed_schedule` (see *A row is its person's* below):

* an event that passes the filter UPSERTS the row — a new `fire_at`, `version + 1`. The meeting moved:
  the row moved. Nothing else changes, so a duplicate delivery is a no-op;
* an event that no longer passes (declined, link removed) or a `*.cancelled` CANCELS it;
* the worker fires due rows (`fire_due`) into an ordinary reaction keyed
  `at:<scope>/<flow>/<key>:<fire_at>`, so a slot fires at most once by the reaction table's own
  unique constraint. A row moved AFTER it fired re-arms (the meeting moved later); a row not moved
  never fires twice.

The run's refs are the event's refs as the last upsert saw them (`payload`), plus `uid`, `_key` and
`fire_at`, so a step reads `meeting_url` from `{{payload.video_url}}` the same way it would have read
the event itself. Engine core: stdlib only, the DB is `(sql, params) -> rows`.

ADR-0052 §7 (a guarantee is built on this, so it must not lose a fire quietly):

* a row may carry a DEADLINE (the trigger's `deadline`, e.g. `payload.start + 600s`): it is then
  missed only when `now > deadline`, so an outage from the fire time to the meeting's start still
  fires when the worker returns. Rows without one keep the old rule (`CATCHUP_S` after the slot or
  the last real change). An unchanged re-upsert does not count as a change;
* `fire_due` reads only rows of keyed flows that are loaded, page after page, past rows it skips,
  so no number of skipped rows stops a later due one; a row whose flow has no active version left
  is marked `retired`;
* the ceilings count per flow: a person's rows of one flow never use up another flow's share. A
  flow registered in code with `share: account` has the per-person share only, no company total.
  A person's OWN flows (a `user:<uid>` scope) also share one total across them, so a person with
  many flows holds `per_user` rows, not `per_user` for each flow;
* a deadline lies at most `DEADLINE_MAX_S` after the fire time: a row the daily cap keeps refusing
  is missed within that hour, whatever deadline a submitted trigger names.

A ROW IS ITS PERSON'S (Vexa-ai/vexa#1996 review). The key comes off the event, and for a company flow
the event is anybody's: two people invited to one meeting forward the same calendar UID, and so can a
stranger who copied it. A row is therefore (scope, flow, key, person), `person` being the uid the
event names, else its organizer (`person_of`). Each person's event arms, moves and cancels that
person's row only; two people, two rows, two fires. A person's own flow has one person, its owner.
"""
from __future__ import annotations

import logging
import time

from .clock import Clock
from .db import DB, dumps, loads
from .registry import Flow, Registry

_log = logging.getLogger(__name__)

#: How late a due row may still fire, counted from its slot or from when it was armed if later. Older
#: rows are `missed`: a bot 20 minutes into a meeting it was meant to open is not what the person asked
#: for, unless the invitation itself arrived after the start.
CATCHUP_S = 600.0
#: Pending rows one person may hold in one scope (`keyed_schedules_per_user`) — their own flows, or
#: their share of a company flow — and the ceiling a company flow holds for everyone together.
PER_USER = 500
COMPANY_MAX = 20000
KEYED_EVENT = "keyed.due"
#: Rows `fire_due` reads per page, and how long one pass may keep paging before it leaves the rest
#: (still armed, still due) to the next pass.
PAGE = 200
PASS_BUDGET_S = 5.0
#: How long after its fire time a row's deadline may lie. A refused row stays armed and due until its
#: deadline, and every pass reads it again; a submitted trigger may name any path as the deadline, so
#: without this bound a row the daily cap refuses could sit in front of everyone else's for days.
#: The booked bot's own window (`start - 600 s` → `start + 600 s`, ADR-0052 §2) is a third of it.
DEADLINE_MAX_S = 3600.0
#: The first key of the transaction-scoped advisory lock `upsert` takes per (scope, person); the
#: second is a hash of them. Two-key advisory locks are a space of their own (any fixed number;
#: this one spells "keye").
SHARE_LOCK = 0x6B657965
#: `share` values a trigger may carry. `person` (the default): the per-person share, and for a
#: company flow the company total too. `account`: the per-person share only (ADR-0052 §7, the
#: booked-bot flow, where the account ceiling is the bound). Set only by a flow registered in code:
#: `triggers.check_spec` does not carry it from a submission.
SHARES = ("person", "account")


def held(db: DB, scope: str, flow: str, person: "str | None" = None) -> int:
    """Armed rows of ONE flow — one person's, or everybody's. Rows of other flows never count."""
    sql = ("SELECT COUNT(*) FROM keyed_schedule WHERE scope = :sc AND flow = :f "
           "AND status = 'armed'")
    params = {"sc": scope, "f": flow}
    if person is not None:
        sql += " AND person = :p"
        params["p"] = person
    rows = db.execute(sql, params)
    return int(rows[0][0] or 0) if rows else 0


def held_by(db: DB, scope: str, person: str) -> int:
    """Armed rows one person holds across EVERY flow of one scope: the total a person's own flows
    share (a company flow's share stays per flow, ADR-0052 §7)."""
    rows = db.execute("SELECT COUNT(*) FROM keyed_schedule WHERE scope = :sc AND person = :p "
                      "AND status = 'armed'", {"sc": scope, "p": person})
    return int(rows[0][0] or 0) if rows else 0


def person_named(payload: dict) -> str:
    """The person an event names: its `uid` (the Vexa user), else its `organizer` (how a mailed
    invite names its person, authenticated by the mailbox, before the invite's own steps resolve an
    account). The one rule for whose a key is — a keyed row here, an invite's dedup id in the
    mailbox (`flows_integrations.mailbox.invite_source_id`)."""
    return str((payload or {}).get("uid") or (payload or {}).get("organizer") or "")[:200]


def segment(person: str) -> str:
    """`person` as one segment of a key: `/` and `%` escaped, so no two (person, rest) pairs spell
    the same key."""
    return str(person or "").replace("%", "%25").replace("/", "%2F")


def person_of(flow: Flow, payload: dict) -> str:
    """Whom a pending fire is for: the flow's owner, or for a company flow the person the event names
    (`person_named`). Part of the row's identity: see the module docstring."""
    if flow.owner:
        return flow.owner
    return person_named(payload)


def _full(db: DB, flow: Flow, person: str, per_user: int, share: str) -> bool:
    """Would one more armed row of `person`'s in `flow` pass a ceiling? Logged when it would."""
    if per_user >= 0 and held(db, flow.scope, flow.name, person) >= per_user:
        _log.warning("keyed schedule refused for %s: the person's share of %d is full",
                     flow.ident, per_user)
        return True
    # A PERSON'S OWN FLOWS SHARE ONE TOTAL: per flow alone, a person with N flows would hold
    # N × `per_user` rows, and every pass reads the due ones.
    if per_user >= 0 and flow.owner and held_by(db, flow.scope, person) >= per_user:
        _log.warning("keyed schedule refused for %s: the person's total of %d across their "
                     "own flows is full", flow.ident, per_user)
        return True
    if not flow.owner and share != "account" and held(db, flow.scope, flow.name) >= COMPANY_MAX:
        _log.warning("keyed schedule refused for %s: company ceiling %d reached", flow.ident,
                     COMPANY_MAX)
        return True
    return False


def upsert(db: DB, clock: Clock, flow: Flow, *, key: str, fire_at: float, event_type: str,
           payload: dict, per_user: int = PER_USER, deadline: "float | None" = None,
           share: str = "person") -> str:
    """Arm or move the person's row for ``key`` (the person is `person_of(flow, payload)`). Returns
    what happened: `armed`, `moved`, `rearmed`, `unchanged` or `refused` (the person's share of this
    flow, their total across their own flows, or the company total).

    `deadline`: the last moment the fire is still wanted (None: the `CATCHUP_S` rule), held to at
    most `DEADLINE_MAX_S` after `fire_at`. An unchanged upsert (same time) refreshes the payload and
    the deadline but NOT `updated_at`, so a duplicate delivery never extends the window a row may
    still fire in. A row that is not armed (fired, missed, cancelled, retired) is re-armed only past
    the same checks a new row meets: it is a new pending fire as far as every ceiling is concerned.

    ONE PERSON'S UPSERTS ONE AT A TIME (second re-review). The ceilings are counts, and a count and
    the write it allows were two statements, so concurrent upserts for one person all passed one
    count (share 2, 12 concurrent re-arms: 5 armed, once 12). On a DB that offers `atomic()` (the
    Postgres adapter) the whole upsert is one transaction that first takes a transaction-scoped
    advisory lock on (scope, person), so every statement after it reads what the previous holder
    committed. Different people never wait on each other. The company total is not under it: it
    may be passed by as many people's upserts as run at once, never by one person's."""
    person = person_of(flow, payload)
    atomic = getattr(db, "atomic", None)
    if atomic is None:                    # the sqlite test double: one statement at a time anyway
        return _upsert(db, clock, flow, person, key=key, fire_at=fire_at, event_type=event_type,
                       payload=payload, per_user=per_user, deadline=deadline, share=share)
    with atomic() as tx:
        tx("SELECT pg_advisory_xact_lock(CAST(:ns AS integer), hashtext(CAST(:who AS text)))",
           {"ns": SHARE_LOCK, "who": f"{flow.scope}/{segment(person)}"})
        return _upsert(_Within(tx, db.dialect), clock, flow, person, key=key, fire_at=fire_at,
                       event_type=event_type, payload=payload, per_user=per_user,
                       deadline=deadline, share=share)


class _Within:
    """One transaction's `execute`, seen as a DB."""

    def __init__(self, execute, dialect: str) -> None:
        self.execute, self.dialect = execute, dialect


def _upsert(db: DB, clock: Clock, flow: Flow, person: str, *, key: str, fire_at: float,
            event_type: str, payload: dict, per_user: int, deadline: "float | None",
            share: str) -> str:
    now = clock.now()
    dl = None if deadline is None else min(float(deadline), float(fire_at) + DEADLINE_MAX_S)
    rows = db.execute("SELECT fire_at, status, version, fired_for FROM keyed_schedule "
                      "WHERE scope = :sc AND flow = :f AND key = :k AND person = :who",
                      {"sc": flow.scope, "f": flow.name, "k": key, "who": person})
    if not rows:
        if _full(db, flow, person, per_user, share):
            return "refused"
        db.execute("INSERT INTO keyed_schedule (scope, flow, key, fire_at, status, version, "
                   "event_type, person, payload, fired_for, updated_at, deadline) VALUES (:sc, :f, "
                   ":k, :at, 'armed', 1, :et, :who, :p, NULL, :now, :dl)",
                   {"sc": flow.scope, "f": flow.name, "k": key, "at": float(fire_at),
                    "et": event_type, "who": person, "p": dumps(payload), "now": now, "dl": dl})
        return "armed"
    old_at, status, version, fired_for = rows[0]
    same_time = abs(float(old_at) - float(fire_at)) < 1.0
    if status in ("fired", "armed", "missed") and same_time:
        # NOTHING MOVED: a duplicate delivery, or an update that kept the time. `updated_at` is the
        # moment the row last really changed — the old missed rule counts from it — so it stays. A
        # MISSED row stays missed: re-arming it on a redelivery would fire it late, the very thing
        # `missed` exists to prevent. Only a real move (a new time) re-arms it.
        db.execute("UPDATE keyed_schedule SET payload = :p, event_type = :et, deadline = :dl "
                   "WHERE scope = :sc AND flow = :f AND key = :k AND person = :who",
                   {"p": dumps(payload), "et": event_type, "dl": dl, "sc": flow.scope,
                    "f": flow.name, "k": key, "who": person})
        return "unchanged"
    # RE-ARMING IS ARMING: a cancelled, missed, fired or retired row no longer counts against any
    # ceiling, so moving it back in meets them again (else cancel-and-move holds past the share).
    if status != "armed" and _full(db, flow, person, per_user, share):
        return "refused"
    db.execute("UPDATE keyed_schedule SET fire_at = :at, status = 'armed', version = :v, "
               "payload = :p, event_type = :et, updated_at = :now, deadline = :dl "
               "WHERE scope = :sc AND flow = :f AND key = :k AND person = :who",
               {"at": float(fire_at), "v": int(version or 0) + 1, "p": dumps(payload),
                "et": event_type, "now": now, "dl": dl, "sc": flow.scope, "f": flow.name,
                "k": key, "who": person})
    return "rearmed" if status == "fired" else "moved"


def cancel(db: DB, clock: Clock, flow: Flow, *, key: str, person: str) -> bool:
    """Cancel ``person``'s pending fire for ``key`` (never another person's row of the same key).
    True when one was pending."""
    rows = db.execute("UPDATE keyed_schedule SET status = 'cancelled', updated_at = :now "
                      "WHERE scope = :sc AND flow = :f AND key = :k AND person = :who "
                      "AND status = 'armed' RETURNING key",
                      {"now": clock.now(), "sc": flow.scope, "f": flow.name, "k": key,
                       "who": person})
    return bool(rows)


def upcoming(db: DB, scope: str, flow_name: str, *, limit: int = 3) -> list[dict]:
    """The next pending fires of one flow, soonest first — what the routine strip shows."""
    rows = db.execute("SELECT key, fire_at, payload FROM keyed_schedule WHERE scope = :sc AND "
                      "flow = :f AND status = 'armed' ORDER BY fire_at LIMIT :n",
                      {"sc": scope, "f": flow_name, "n": int(limit)})
    out = []
    for key, at, payload in rows:
        p = loads(payload) if payload else {}
        out.append({"key": key, "fire_at": float(at),
                    **({"title": str(p.get("title"))[:120]} if p.get("title") else {})})
    return out


def slot_key(flow: Flow, key: str, fire_at: float, person: str = "") -> str:
    """The reaction key a slot fires under. A company flow's slot names its person too, so two
    people's rows of one key and one time are two runs, not one the reaction table dedups away
    (the person is a `segment`, so no two (person, key) pairs spell the same slot)."""
    if flow.owner:
        return f"at:{flow.scope}/{flow.name}/{key}:{int(fire_at)}"
    return f"at:{flow.scope}/{flow.name}/{segment(person)}/{key}:{int(fire_at)}"


def _keyed_flows(registry: Registry) -> dict:
    """(scope, name) → the newest chosen version, for every loaded flow with a relative trigger."""
    from . import triggers
    out: dict = {}
    for bucket in list(registry.by_event.values()) + list(registry.scheduled.values()):
        for f in bucket:
            if triggers.spec_of(f).get("kind") != "relative":
                continue
            have = out.get((f.scope, f.name))
            if have is None or f.version > have.version:
                out[(f.scope, f.name)] = f
    return out


def retire_orphans(db: DB, registry: Registry, clock: Clock) -> int:
    """Mark `retired` every armed row whose flow has no version left to fire it: not chosen in the
    registry and no `active` row in `flow_version` (a flow the company or the person retired). A flow
    still active in the table but not loaded here (a policy refusal, a step this image lacks) keeps
    its rows armed. Returns rows retired."""
    now = clock.now()
    n = 0
    for scope, name in db.execute("SELECT DISTINCT scope, flow FROM keyed_schedule "
                                  "WHERE status = 'armed'"):
        if registry.newest(scope, name) is not None:
            continue
        try:
            active = db.execute("SELECT COUNT(*) FROM flow_version WHERE scope = :sc AND "
                                "name = :n AND status = 'active'", {"sc": scope, "n": name})
        except Exception:  # noqa: BLE001 — a store with no flow table cannot say; leave the rows
            continue
        if active and int(active[0][0] or 0):
            continue
        gone = db.execute("UPDATE keyed_schedule SET status = 'retired', updated_at = :now "
                          "WHERE scope = :sc AND flow = :f AND status = 'armed' RETURNING key",
                          {"now": now, "sc": scope, "f": name})
        if gone:
            _log.warning("keyed schedules of retired flow %s/%s marked retired: %d", scope, name,
                         len(gone))
            n += len(gone)
    return n


def fire_due(db: DB, registry: Registry, clock: Clock, *, catchup_s: float = CATCHUP_S,
             admissible=None, page: int = PAGE, budget_s: float = PASS_BUDGET_S,
             **limits) -> int:
    """Fire every armed row whose time has come into one reaction on the flow's newest chosen version.
    ``admissible(flow, refs) -> bool`` is the daily-cap check (the caller's), so a keyed fire counts
    like any other run. Returns reactions created.

    Reads only rows of loaded keyed flows, in pages of `page` ordered by the row's identity behind
    its time (fire_at, scope, flow, key, person), past every row it skips (refused by
    `admissible`), until no due row is left or `budget_s` of wall time is spent; what is left is
    still armed and due for the next pass."""
    from .admission import insert
    now = clock.now()
    retire_orphans(db, registry, clock)
    flows = _keyed_flows(registry)
    if not flows:
        return 0
    names = sorted({name for _, name in flows})
    params = {f"n{i}": n for i, n in enumerate(names)}
    in_names = ", ".join(f":n{i}" for i in range(len(names)))
    sql = ("SELECT scope, flow, key, person, fire_at, event_type, payload, updated_at, deadline "
           "FROM keyed_schedule WHERE status = 'armed' AND fire_at <= :now "
           f"AND flow IN ({in_names}) "
           "AND (fire_at > :a OR (fire_at = :a AND (scope > :s OR (scope = :s AND "
           "(flow > :f OR (flow = :f AND (key > :k OR (key = :k AND person > :p)))))))) "
           "ORDER BY fire_at, scope, flow, key, person LIMIT :lim")
    cursor = (-1e18, "", "", "", "")
    started = time.monotonic()
    created = 0
    while True:
        rows = db.execute(sql, {**params, "now": now, "a": cursor[0], "s": cursor[1],
                                "f": cursor[2], "k": cursor[3], "p": cursor[4], "lim": int(page)})
        for scope, name, key, who, at, event_type, payload, armed_at, deadline in rows:
            cursor = (float(at), scope, name, key, who)
            flow = flows.get((scope, name))
            if flow is None:
                continue              # another scope's flow of the same name, not loaded here
            # MISSED. With a deadline (ADR-0052 §7) only past it: the meeting decides, not the alarm.
            # Without one, lateness counts from when the worker could first have fired it: the slot,
            # or the moment the row was armed or moved if that came after (an invite forwarded
            # twenty minutes into a meeting arms a row whose time has passed; that is due now).
            if deadline is not None:
                late = now > float(deadline)
            else:
                late = now - max(float(at), float(armed_at or 0)) > catchup_s
            if late:
                db.execute("UPDATE keyed_schedule SET status = 'missed', updated_at = :now WHERE "
                           "scope = :sc AND flow = :f AND key = :k AND person = :who "
                           "AND status = 'armed'",
                           {"now": now, "sc": scope, "f": name, "k": key, "who": who})
                _log.warning("keyed fire %s/%s missed by %.0fs", flow.ident, key, now - float(at))
                continue
            refs = {**(loads(payload) if payload else {}), "_key": key, "fire_at": float(at),
                    "_event_type": event_type}
            if flow.owner:
                refs["uid"] = flow.owner
            if admissible is not None and not admissible(flow, refs):
                continue              # stays armed; the page goes on past it
            made = insert(db, clock, flow,
                          key=f"{slot_key(flow, key, float(at), who)}::{flow.ident}",
                          event_type=KEYED_EVENT, subject_refs=refs)
            db.execute("UPDATE keyed_schedule SET status = 'fired', fired_for = :at, updated_at = :now "
                       "WHERE scope = :sc AND flow = :f AND key = :k AND person = :who "
                       "AND fire_at = :at",
                       {"at": float(at), "now": now, "sc": scope, "f": name, "k": key, "who": who})
            created += len(made or [])
        if len(rows) < int(page) or time.monotonic() - started > budget_s:
            return created
