"""Auto-join sweep — "scheduled" MEANS the bot joins.

One tick scans PLANNED rows in status ``scheduled`` whose ``data.scheduled_at`` has arrived
(within ``lead_s`` before start, up to ``grace_s`` after — never join hours late) and, unless the
per-meeting ``data.auto_join`` toggle is off, spawns the bot through the SAME ``request_bot`` flow
POST /bots runs. The spawn CLAIMS the planned row in place (``create_meeting_guarded``'s claim
branch), so the sweep is idempotent by construction: a claimed row leaves ``scheduled`` and drops
out of the sweep's predicate, and the per-user advisory lock serializes it against a concurrent
manual "Send bot now" (that race surfaces here as ``DuplicateMeeting`` — someone already joined —
counted, never error-stamped).

Defense in depth behind that dedup: before spawning, the tick asks the repo which
(user, platform, native) tuples a bot ALREADY owns (``list_live_meetings`` over ``LIVE_STATUSES``)
and refuses a due row whose room is already covered by a DIFFERENT row — stamping
``data.auto_join_error`` with the holding meeting id. Two Vexa bots in one meeting is never
correct however the two rows came to exist (live 2026-08-17: a manual "Send bot now" row plus a
calendar import of the same Meet that failed to adopt it).

Failures are LOUD, never silent (P18/P10): a cap/quota rejection or spawn failure stamps
``data.auto_join_error`` (+ ``data.auto_join_next_retry`` backoff so one bad row doesn't re-fire
every tick) — the terminal surfaces it on the meeting row.

Every dispatch also stamps ``data.auto_join_last_attempt`` BEFORE it is made. That records the
attempt rather than its outcome, so it outlives outcomes this row never gets to write: a spawn
that succeeds and a bot that then fails to JOIN takes the row terminal, calendar sync creates a
sibling row for the same occurrence, and without the stamp the sibling was due the instant it
existed — one bot every sync interval for the whole grace window (live 2026-08-17, user 13820).
``calendar_sync`` carries the stamp onto the sibling, and ``due_rows`` honours it, so the ceiling
is ONE dispatch per backoff interval per occurrence however many rows that occurrence acquires.

``auto_join`` defaults ON when the key is absent — planning a meeting with a time means the bot
comes, opting out is the explicit act.

The bot that comes is the SAME bot POST /bots sends: recording and transcription resolve through
``env_flags.resolve_spawn_flag``, the one resolver the route uses, so a calendar bot records like a
manual one (#1216). Passing nothing meant inheriting ``request_bot``'s ``recording_enabled=False``
default, and every calendar-joined meeting on stage rev 194 came back unrecorded while manual ones
recorded — a split default nobody chose.

The tick is a pure-ish function over injected ports (repo, runtime, context fetcher, clock) — the
entrypoint (``__main__``) wraps it in the standard poll loop; tests drive single ticks offline.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

# The safeguards live with the start route (ADR-0052 §5): the sweep and the route run the same ones
# from one module. Re-exported under their old names for this module's readers.
from .planned_start import (  # noqa: F401 — re-exports
    DEFAULT_RETRY_BACKOFF_S,
    LIVE_STATUSES,
    calendar_bot_name as _calendar_bot_name,
    live_keys,
    parse_iso as _parse_iso,
    production_transcribe_gate as _production_transcribe_gate,
    start_row,
)

# Sweep cadence/window env vocabulary (config.v1: all optional, sane defaults).
# 120s (#1208): the bot must be STANDING IN THE LOBBY when the meeting starts, not setting out then
# — spawn, browser boot and the join flow all happen inside the lead. Two minutes early plus the
# 15-minute lobby budget (``bot_spawn.service.lobby_budget_ms``) is the pair that makes "the bot is
# already there" true for a host who joins late.
DEFAULT_LEAD_S = 120         # AUTO_JOIN_LEAD_S — join this many seconds BEFORE scheduled_at
DEFAULT_GRACE_S = 600        # AUTO_JOIN_GRACE_S — never join more than this AFTER scheduled_at


def due_rows(rows: list[dict], *, now: datetime,
             lead_s: float = DEFAULT_LEAD_S, grace_s: float = DEFAULT_GRACE_S,
             retry_backoff_s: float = DEFAULT_RETRY_BACKOFF_S) -> list[dict]:
    """The PURE due-filter over ``scheduled`` rows: auto_join on (absent = on), a joinable link,
    ``scheduled_at`` inside [start - lead, start + grace], past any error backoff, and past the
    backoff owed to the LAST DISPATCH ATTEMPT for this occurrence (``auto_join_last_attempt``).

    The last-attempt rule is what bounds the retry storm to one dispatch per backoff interval per
    occurrence. ``auto_join_next_retry`` only ever covered failures the sweep itself saw (a cap
    rejection, a spawn refusal): when the spawn SUCCEEDED and the bot then failed to join, the row
    went terminal carrying no backoff at all, calendar sync recreated a sibling for the same
    occurrence, and the sibling was immediately due — a fresh bot every sync interval until the
    grace window closed (measured live 2026-08-17: meeting 26265 failed 20:06:25, sibling 26267
    created 20:06:35, dispatched 20:06:42). ``auto_join_last_attempt`` records the ATTEMPT rather
    than its outcome, so it survives the outcome — and calendar sync carries it onto the sibling it
    creates for the same occurrence, which is how the backoff outlives the row it was earned on.
    """
    due: list[dict] = []
    for row in rows:
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        # THE USER SAID NO — checked first, before every rate and window rule below, because none of
        # them can express "never" (F1, stage rev 193 row 26306: DELETE on a still-`scheduled`
        # occurrence answered 200 and set `stop_requested`, and this sweep dispatched it anyway at
        # 02:52:16 — the bot joined a meeting the user had already cancelled). `occurrence.
        # disposition` enforces the same rule for TERMINAL rows; a scheduled row never reached it,
        # because a scheduled row is not terminal. Founder ruling 2026-08-17: "explicit stop must be
        # stop, evict."
        #
        # The stop path now terminalizes a planned row outright (``stop_router``), so a flagged
        # `scheduled` row should no longer exist at all. This stays as the standing guarantee: no
        # row carrying the user's stop is EVER due, whichever writer left it in this state.
        if data.get("stop_requested"):
            continue
        if data.get("auto_join") is False:
            continue
        if not row.get("native_meeting_id") or row.get("platform") in (None, "", "unknown"):
            continue
        at = _parse_iso(data.get("scheduled_at"))
        if at is None:
            continue
        if now < at - timedelta(seconds=lead_s) or now > at + timedelta(seconds=grace_s):
            continue
        retry_at = _parse_iso(data.get("auto_join_next_retry"))
        if retry_at is not None and now < retry_at:
            continue
        attempted_at = _parse_iso(data.get("auto_join_last_attempt"))
        if attempted_at is not None and now < attempted_at + timedelta(seconds=retry_backoff_s):
            continue
        due.append(row)
    return due


async def auto_join_tick(
    repo,
    runtime,
    *,
    authority=None,
    fetch_bot_context: Optional[Callable[[int], Awaitable[Optional[dict]]]] = None,
    publish_status: Optional[Callable[..., Awaitable[None]]] = None,
    transcribe_gate: Optional[Callable[[], Optional[str]]] = None,
    now: Optional[datetime] = None,
    lead_s: float = DEFAULT_LEAD_S,
    grace_s: float = DEFAULT_GRACE_S,
    retry_backoff_s: float = DEFAULT_RETRY_BACKOFF_S,
    token_secret: Optional[str] = None,
    redis_url: Optional[str] = None,
    redis_grant=None,
    allow_uncapped: bool = False,
    deployment_block=None,
) -> dict:
    """One sweep: spawn every due scheduled meeting. Returns counters for observability:
    ``{"due": n, "spawned": n, "already": n, "errors": n, "skipped_uncapped": n, ...}``.

    Each due row goes through ``planned_start.start_row`` — the SAME safeguards the start route
    (``POST /internal/meetings/{id}/start``) runs: block lists, the duplicate-room check, the STT
    gate, the owner's context and block list, the last-attempt stamp, the calendar bot name, the
    recording/transcription resolver, and the error stamps.

    ``fetch_bot_context(user_id)`` supplies the per-user spawn context the gateway would have
    injected as headers (including the Calendar default ``bot_name``).
    Three states: the callable is ``None`` (no admin edge configured — the per-user cap is
    UNRESOLVABLE); it returns a dict (use it); it returns ``None`` (identity is configured but
    UNAVAILABLE right now — SKIP the row this tick).

    Fail-closed by default: an unresolvable cap SKIPS the row (never spawns past a cap we cannot
    read). Set ``allow_uncapped=True`` (the deliberate self-host opt-in, env
    ``AUTO_JOIN_ALLOW_UNCAPPED=1``) to spawn uncapped when no admin edge is configured.

    ``publish_status(user_id=…, meeting_id=…, native_id=…, status=…, when=…)`` optionally fans the
    row's frame to ``u:{user}:meetings`` after an error stamp so the terminal refreshes."""
    from ..obs import log_event

    now = now or datetime.now(timezone.utc)
    rows = await repo.list_scheduled_meetings()
    due = due_rows(rows, now=now, lead_s=lead_s, grace_s=grace_s,
                   retry_backoff_s=retry_backoff_s)
    counters = {"due": len(due), "spawned": 0, "already": 0, "errors": 0,
                "skipped_uncapped": 0, "skipped_live": 0, "stopped": 0, "blocked": 0}
    # Duplicate-dispatch guard (defense in depth behind create_meeting_guarded's dedup): a bot
    # already owning this (user, platform, native) on a DIFFERENT row means the meeting is covered.
    live = live_keys(
        await repo.list_live_meetings() if hasattr(repo, "list_live_meetings") else None
    )
    ctx_cache: dict[int, Optional[dict]] = {}

    async def get_context(user_id: int) -> Optional[dict]:
        if user_id not in ctx_cache:
            ctx_cache[user_id] = await fetch_bot_context(user_id)
        return ctx_cache[user_id]

    uncapped_warned = False
    for row in due:
        outcome = await start_row(
            row, repo=repo, runtime=runtime, authority=authority, get_context=get_context,
            publish_status=publish_status, transcribe_gate=transcribe_gate,
            live_holder=live.get((row["user_id"], row.get("platform"),
                                  row.get("native_meeting_id"))),
            now=now, retry_backoff_s=retry_backoff_s, token_secret=token_secret,
            redis_url=redis_url, redis_grant=redis_grant, allow_uncapped=allow_uncapped,
            uncapped=fetch_bot_context is None, deployment_block=deployment_block,
        )
        if outcome.kind == "skipped_uncapped" and not uncapped_warned:
            uncapped_warned = True
            log_event(
                "auto_join_skipped_uncapped", audience="operator", level="warning",
                span="meetings.auto_join", user_id=row["user_id"], meeting_id=str(row["id"]),
                fields={"reason": "no ADMIN_API_URL/INTERNAL_API_SECRET — per-user cap "
                        "unresolvable; refusing uncapped spawn. Set AUTO_JOIN_ALLOW_UNCAPPED=1 "
                        "to opt into uncapped self-host spawns."})
        if outcome.kind in counters:
            counters[outcome.kind] += 1
    return counters
