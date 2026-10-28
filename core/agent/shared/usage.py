"""usage.py — tokens a person's agent used, by hour, for the rolling-day budget (#1783).

ONE WRITER (R0, 2026-10-16): agent-api's spend ledger, ``control_plane/llm_budget.Budget.record``,
is the record of truth for model usage, and the only caller of :func:`record` — for every turn it
dispatched (each ``done`` / ``llm-usage`` its meter reads) and for its own model calls (the home,
titles, summaries, through ``llm_budget.record_local``). This hash is the TOKEN VIEW of that one
ledger, kept per person per UTC hour for the rolling-day token budgets (``budgets.py``):

    vexa:usage:<subject>:<YYYYMMDDHH>   total · chat · routines · events · home   (expires after 26 h)

READ: ``rolling`` sums the last 24 hourly hashes — a rolling day, not a calendar day, so nobody's
budget resets at midnight in the middle of their work. A Redis that is not there counts nothing and
blocks nothing: a budget is a ceiling, never a dependency of the turn.
"""
from __future__ import annotations

import time
from typing import Optional

KINDS = ("chat", "routines", "events", "home")
TTL_S = 26 * 3600


def key(subject: str, at: float) -> str:
    return f"vexa:usage:{subject}:{time.strftime('%Y%m%d%H', time.gmtime(at))}"


def tokens_of(usage: Optional[dict]) -> int:
    """The tokens a harness ``usage`` object reports, every kind together."""
    if not isinstance(usage, dict):
        return 0
    n = 0
    for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
              "prompt_tokens", "completion_tokens", "tokens"):
        v = usage.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            n += int(v)
    return n


def kind_of_unit(unit_id: str) -> str:
    """A chat turn, a routine's run (the thread's `-auto` sibling) or an event-started run."""
    u = unit_id or ""
    if "-chat-" in u:
        return "routines" if u.endswith("-auto") or "-auto-" in u else "chat"
    return "events"


def subject_of_unit(unit_id: str) -> str:
    """``agent-<subject>-chat-…`` / ``agent-<subject>-<trigger>-<digest>`` → ``<subject>``."""
    u = unit_id or ""
    if not u.startswith("agent-"):
        return ""
    rest = u[len("agent-"):]
    if "-chat-" in rest:
        return rest.split("-chat-", 1)[0]
    parts = rest.rsplit("-", 2)
    return parts[0] if len(parts) == 3 else ""


def record(client, subject: str, kind: str, tokens: int, *, now: Optional[float] = None) -> None:
    if not client or not subject or tokens <= 0:
        return
    k = key(subject, now if now is not None else time.time())
    try:
        pipe = client.pipeline()
        pipe.hincrby(k, "total", int(tokens))
        pipe.hincrby(k, kind if kind in KINDS else "chat", int(tokens))
        pipe.expire(k, TTL_S)
        pipe.execute()
    except Exception:  # noqa: BLE001 — accounting never fails a turn
        pass


def rolling(client, subject: str, *, now: Optional[float] = None) -> dict:
    out = {"total": 0, **{k: 0 for k in KINDS}}
    if not client or not subject:
        return out
    now = now if now is not None else time.time()
    try:
        pipe = client.pipeline()
        for h in range(24):
            pipe.hgetall(key(subject, now - h * 3600))
        for row in pipe.execute():
            for f, v in (row or {}).items():
                f = f.decode() if isinstance(f, bytes) else f
                if f in out:
                    out[f] += int(v)
    except Exception:  # noqa: BLE001
        pass
    return out


def count_run(client, subject: str, what: str, *, now: Optional[float] = None) -> int:
    """Add one run of ``what`` (e.g. ``events``) to today's count for ``subject``; returns the count
    including this one (0 when there is no Redis)."""
    if not client or not subject:
        return 0
    k = f"vexa:runs:{what}:{subject}:{time.strftime('%Y%m%d', time.gmtime(now if now is not None else time.time()))}"
    try:
        n = int(client.incr(k))
        client.expire(k, TTL_S)
        return n
    except Exception:  # noqa: BLE001
        return 0
