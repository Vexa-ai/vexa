"""The start-and-stop alarm (founder, 4 Nov 2026: abuse protections for ad-hoc bots).

An account whose bots are mostly stopped within seconds of starting is either testing something or
cycling bots through the cluster. Once an hour, over the last day of bot runs (``meeting_sessions``
by start, joined to their meeting's end and stop flag — existing rows, no new table), every account
with at least ``MIN_RUNS`` runs and more than ``QUICK_STOP_SHARE`` of them stopped within
``QUICK_STOP_S`` of starting is reported to ops as a ``bot_churn_suspected`` system error event.

Measured on hosted prod (30 days to 4 Nov 2026, read-only): 48 of 3,155 bot runs were stopped
within 30 s, and one account with 5 or more runs had more than half of them stopped that fast.
"""
from __future__ import annotations

from typing import Any

QUICK_STOP_S = 30
QUICK_STOP_SHARE = 0.5
MIN_RUNS = 5
WINDOW_S = 86_400
INTERVAL_S = 3_600


async def churn_alarm(repo: Any, *, log_event, window_s: int = WINDOW_S) -> list[dict]:
    """One pass: the accounts to report, each ``{user_id, runs, quick_stops}``. Never raises."""
    if repo is None or not hasattr(repo, "bot_run_stop_counts"):
        return []
    try:
        counts = await repo.bot_run_stop_counts(window_s=window_s, quick_stop_s=QUICK_STOP_S)
    except Exception:  # noqa: BLE001 — an alarm that fails must not take its loop down
        return []
    flagged = [c for c in counts
               if c["runs"] >= MIN_RUNS and c["quick_stops"] / c["runs"] > QUICK_STOP_SHARE]
    for c in flagged:
        log_event("bot_churn_suspected", audience="system", level="error", span="alarm.churn",
                  user_id=c["user_id"],
                  fields={"runs": c["runs"], "quick_stops": c["quick_stops"],
                          "window_s": window_s, "quick_stop_s": QUICK_STOP_S})
    return flagged
