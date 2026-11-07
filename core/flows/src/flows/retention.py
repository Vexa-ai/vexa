"""Captured facts expire: the retention pass (PRD 0.13.4, a Fact's lifecycle is admitted → expires).

A fact is admitted with its refs — an inbound mail's body, an invite's attendees, a calendar event
— and those refs, the scratch the steps wrote and every receipt's result are what this deployment
keeps about the people involved. They are kept while the work is live and for `days` after it
finished, then removed:

* a FINISHED reaction (`done`, `failed`, `cancelled`) whose last movement is older than the cutoff,
  with its effect receipts and signals. A reaction still waiting (`admitted`, `retrying`,
  `running`, `blocked`) is never removed, however old: its refs are what it still runs on;
* a quarantined mail (`mail_quarantine`: sender, reason and the meeting facts of a refused invite)
  older than the cutoff. A sender answered with the fixed line is therefore answered again at most
  once per retention period;
* an admitted mail turn (`mail_turn`) older than the cutoff and outside the rate window it is
  counted in;
* the payload of a keyed schedule that has fired, been cancelled or been missed. The row itself
  stays: it is what keeps a redelivered calendar event from arming the same fire again.

`days` is `VEXA_FLOWS_FACT_RETENTION_DAYS`; 0 keeps everything. Each call removes at most `batch`
reactions, so a backlog drains over several passes instead of in one long statement; `more` says
whether one is left. Engine core: it touches only flows' own tables."""
from __future__ import annotations

from .clock import Clock
from .db import DB

DAY_S = 86_400.0
BATCH = 500
FINISHED = ("done", "failed", "cancelled")
KEYED_FINISHED = ("fired", "cancelled", "missed")

_FINISHED_SQL = ", ".join(f"'{s}'" for s in FINISHED)
_KEYED_FINISHED_SQL = ", ".join(f"'{s}'" for s in KEYED_FINISHED)
_EXPIRED = (f"SELECT reaction_id FROM reaction WHERE status IN ({_FINISHED_SQL}) "
            "AND updated_at < :cut ORDER BY updated_at LIMIT :n")


def cutoff(clock: Clock, days: int) -> "float | None":
    """The moment before which a finished fact is expired; None when retention is off (0)."""
    return None if int(days) <= 0 else clock.now() - int(days) * DAY_S


def purge(db: DB, clock: Clock, *, days: int, rate_window_s: float = 0.0,
          batch: int = BATCH) -> dict:
    """One retention pass. Returns how many rows each table lost and whether a backlog remains."""
    cut = cutoff(clock, days)
    if cut is None:
        return {"reactions": 0, "receipts": 0, "signals": 0, "quarantine": 0, "mail_turns": 0,
                "keyed_payloads": 0, "more": False}
    ids = [r[0] for r in db.execute(_EXPIRED, {"cut": cut, "n": int(batch)})]
    signals = receipts = reactions = 0
    if ids:
        names = {f"r{i}": rid for i, rid in enumerate(ids)}
        # re-read in every statement: a reaction an operator retried a moment ago is live again
        still = (f"SELECT reaction_id FROM reaction WHERE reaction_id IN "
                 f"({', '.join(':' + k for k in names)}) AND status IN ({_FINISHED_SQL}) "
                 "AND updated_at < :cut")
        params = {**names, "cut": cut}
        signals = len(db.execute(f"DELETE FROM signal WHERE reaction_id IN ({still}) "
                                 "RETURNING signal_id", params))
        receipts = len(db.execute(f"DELETE FROM effect_receipt WHERE reaction_id IN ({still}) "
                                  "RETURNING effect_key", params))
        reactions = len(db.execute(f"DELETE FROM reaction WHERE reaction_id IN ({still}) "
                                   "RETURNING reaction_id", params))
    quarantine = len(db.execute("DELETE FROM mail_quarantine WHERE at < :cut RETURNING ext_id",
                                {"cut": cut}))
    turn_cut = min(cut, clock.now() - max(float(rate_window_s), 0.0))
    turns = len(db.execute("DELETE FROM mail_turn WHERE at < :cut RETURNING ext_id",
                           {"cut": turn_cut}))
    keyed = len(db.execute(
        f"UPDATE keyed_schedule SET payload = NULL WHERE payload IS NOT NULL AND status IN "
        f"({_KEYED_FINISHED_SQL}) AND updated_at < :cut RETURNING key",
        {"cut": cut}))
    return {"reactions": reactions, "receipts": receipts, "signals": signals,
            "quarantine": quarantine, "mail_turns": turns, "keyed_payloads": keyed,
            "more": len(ids) >= int(batch)}
