"""credits.py — Vexa credits: what a person's (or a pool's) model calls cost at OUR price, in windows.

Architecture decision G1 (2026-10-16), PLAN-8H 6.2. Behind the ``usage`` feature (off by default).

    credits   = the call's market cost (``llm_budget.cost_usd``: the provider's own figure, else the
                model's market price) x the markup (``credit_markup``, default 1.3). One credit is one
                US dollar, so credits are shown in $.
    windows   = a 5-hour window and a weekly window per PAYER. A window opens at the payer's first call
                and resets its length later; the next call opens a new one. Its fill is reported as a
                percentage of the plan's allowance (``credits_5h``, ``credits_week``: empty = no limit,
                the self-host default).
    payer     = the person for their own chats (today: always the subject). Rooms, the sensor and
                routines will pay from the workspace owner's pool (``ws:<id>``), with rooms (R1).
    extra     = extra usage: a switch the person turns on, plus a top-up amount recorded as DATA ONLY —
                no payment is taken here. With it on, a full window does not refuse; what is used past
                the allowance is counted as ``extra_used``.

ONE WRITER: ``llm_budget.Budget.record`` (the spend ledger) calls :func:`debit` once per model call; it
is the only caller. Every call is journalled (``vexa:credits:journal``, a capped redis stream: payer,
subject, kind, model, market cost, credits). The journal and the windows are the fast path; the
durable per-call record (Postgres, with the price snapshot and the org) is the next piece.
"""
from __future__ import annotations

import datetime as _dt
import time
from typing import Any, Mapping, Optional

PREFIX = "vexa:credits"
JOURNAL = f"{PREFIX}:journal"
JOURNAL_MAXLEN = 200_000
DEFAULT_MARKUP = 1.3
WINDOWS = {"5h": 5 * 3600, "week": 7 * 86400}
ALLOWANCE_KEYS = {"5h": "credits_5h", "week": "credits_week"}
#: The policy keys this module reads from ``_global/POLICIES.md`` (empty: the default).
DEFAULTS = {"credit_markup": "1.3", "credits_5h": "", "credits_week": ""}


def _f(v) -> Optional[float]:
    try:
        x = float(str(v).strip().lstrip("$"))
    except (TypeError, ValueError):
        return None
    return x if x == x and x >= 0 else None


def _policy(policy: Optional[Mapping[str, Any]]) -> dict:
    return {str(k).lower(): v for k, v in (policy or {}).items()}


def markup(policy: Optional[Mapping[str, Any]]) -> float:
    """The markup on market cost: default 1.3. A value below 1.0 (selling below cost) or one that cannot
    be read is refused — the default applies, never the value."""
    m = _f(_policy(policy).get("credit_markup"))
    return m if m is not None and m >= 1.0 else DEFAULT_MARKUP


def allowance(policy: Optional[Mapping[str, Any]], window: str) -> Optional[float]:
    raw = _policy(policy).get(ALLOWANCE_KEYS[window])
    if raw is None or str(raw).strip() == "":
        return None
    a = _f(raw)
    return a if a is not None and a > 0 else None


def _wkey(payer: str, window: str) -> str:
    return f"{PREFIX}:win:{payer}:{window}"


def _xkey(payer: str) -> str:
    return f"{PREFIX}:extra:{payer}"


def _window(r, payer: str, window: str, now: float) -> tuple[float, Optional[float]]:
    """(used, start) of the open window, or (0, None) when none is open."""
    row = r.hgetall(_wkey(payer, window)) or {}
    start = _f(row.get("start"))
    if start is None or now >= start + WINDOWS[window]:
        return 0.0, None
    return _f(row.get("used")) or 0.0, start


def debit(r, payer: str, *, cost_usd: float, now: Optional[float] = None,
          policy: Optional[Mapping[str, Any]] = None, subject: str = "", kind: str = "chat",
          model: str = "") -> float:
    """Debit one call: its credits into both windows, a journal row. Returns the credits."""
    if r is None or not payer or not cost_usd or cost_usd <= 0:
        return 0.0
    now = time.time() if now is None else now
    credits = round(float(cost_usd) * markup(policy), 8)
    extra = (r.hget(_xkey(payer), "on") or "") == "1"
    over = 0.0
    for window, length in WINDOWS.items():
        used, start = _window(r, payer, window, now)
        key = _wkey(payer, window)
        if start is None:                      # open a new window at this call
            start, used = now, 0.0
            r.delete(key)
            r.hset(key, mapping={"start": repr(now), "used": "0"})
        new = float(r.hincrbyfloat(key, "used", credits))
        # kept past its end by a margin; whether it is still open is decided by `start`, not the TTL
        r.expire(key, int(max(1.0, start + length - now)) + 3600)
        cap = allowance(policy, window)
        if extra and cap is not None:
            over = max(over, new - max(used, cap))
    if over > 0:
        r.hincrbyfloat(_xkey(payer), "used", round(over, 8))
    try:
        r.xadd(JOURNAL, {"ts": repr(now), "payer": payer, "subject": subject or payer, "kind": kind,
                         "model": model or "", "cost_usd": repr(float(cost_usd)), "credits": repr(credits)},
               maxlen=JOURNAL_MAXLEN, approximate=True)
    except Exception:  # noqa: BLE001 — the windows are the enforcement; the journal is the record
        pass
    return credits


def set_extra(r, payer: str, on: bool, *, now: Optional[float] = None,
              topup_credits: Optional[float] = None) -> dict:
    """Turn extra usage on or off for a payer; optionally record a top-up amount (data only)."""
    now = time.time() if now is None else now
    mapping = {"on": "1" if on else "0", "at": repr(now)}
    if topup_credits is not None and topup_credits >= 0:
        mapping["topup"] = repr(float(topup_credits))
    r.hset(_xkey(payer), mapping=mapping)
    return mapping


def state(r, payer: str, policy: Optional[Mapping[str, Any]], *, now: Optional[float] = None) -> dict:
    """What the usage meter shows: each window's fill as a % of the allowance, when it resets, and
    extra usage. A window with no allowance reports 0% (no limit)."""
    now = time.time() if now is None else now
    out: dict = {"markup": markup(policy)}
    for window, name in (("5h", "five_h"), ("week", "week")):
        used, start = _window(r, payer, window, now) if r is not None else (0.0, None)
        cap = allowance(policy, window)
        out[name] = {"pct": round(100.0 * used / cap, 2) if cap else 0.0, "used": round(used, 6),
                     "allowance": cap, "resets_at": (start + WINDOWS[window]) if start is not None else None}
    x = (r.hgetall(_xkey(payer)) if r is not None else {}) or {}
    out["extra"] = x.get("on") == "1"
    out["topup_credits"] = _f(x.get("topup")) or 0.0
    out["extra_used"] = round(_f(x.get("used")) or 0.0, 6)
    return out


def admit(r, payer: str, policy: Optional[Mapping[str, Any]], *, now: Optional[float] = None) -> None:
    """Raise ``llm_budget.BudgetExhausted`` (kind ``usage_window``) when a window is full and extra
    usage is off."""
    if r is None:
        return
    s = state(r, payer, policy, now=now)
    if s["extra"]:
        return
    from control_plane.llm_budget import BudgetExhausted  # noqa: PLC0415
    for window, name in (("5h", "five_h"), ("week", "week")):
        w = s[name]
        if w["allowance"] and w["used"] >= w["allowance"]:
            # "17:00 UTC" for the 5-hour window, "Sat 17:00 UTC" for the week: read in a sentence
            fmt = "%H:%M UTC" if window == "5h" else "%a %H:%M UTC"
            at = _dt.datetime.fromtimestamp(w["resets_at"], tz=_dt.timezone.utc).strftime(fmt)
            raise BudgetExhausted("usage_window", scope="user", period=window, cap=w["allowance"],
                                  spent=w["used"], resets_at=at)
