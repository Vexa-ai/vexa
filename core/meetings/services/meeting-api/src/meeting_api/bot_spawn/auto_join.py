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

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from ..obs import log_event
from ..service_authority import (
    ServiceAuthorityDenied,
    ServiceAuthorityUnavailable,
)
from .env_flags import resolve_spawn_flag
from .ports import MaxBotsExceeded, MeetingStopped, QuotaExceeded, SpawnFailed
from .service import DuplicateMeeting, request_bot

# Sweep cadence/window env vocabulary (config.v1: all optional, sane defaults).
# 120s (#1208): the bot must be STANDING IN THE LOBBY when the meeting starts, not setting out then
# — spawn, browser boot and the join flow all happen inside the lead. Two minutes early plus the
# 15-minute lobby budget (``bot_spawn.service.lobby_budget_ms``) is the pair that makes "the bot is
# already there" true for a host who joins late.
DEFAULT_LEAD_S = 120         # AUTO_JOIN_LEAD_S — join this many seconds BEFORE scheduled_at
DEFAULT_GRACE_S = 600        # AUTO_JOIN_GRACE_S — never join more than this AFTER scheduled_at
DEFAULT_RETRY_BACKOFF_S = 300  # AUTO_JOIN_RETRY_BACKOFF_S — error-stamped rows wait this long


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


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


# Every status in which a bot OWNS the room. A due row whose (user, platform, native) is held by
# one of these on ANOTHER row must never spawn: two Vexa bots in one meeting is customer-visible
# and never correct, however the two rows came to exist.
LIVE_STATUSES = (
    "requested", "joining", "awaiting_admission", "active",
    "needs_help", "needs_human_help", "stopping",
)


def live_keys(rows: Optional[list]) -> dict[tuple, Any]:
    """``{(user_id, platform, native_meeting_id): meeting_id}`` over the repo's live rows."""
    out: dict[tuple, Any] = {}
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        key = (row.get("user_id"), row.get("platform"), row.get("native_meeting_id"))
        if key[0] is None or not key[1] or not key[2]:
            continue
        out.setdefault(key, row.get("id"))
    return out


def _calendar_bot_name(data: dict) -> Optional[str]:
    """Resolve the bot name from the calendar source that armed this meeting.

    Multi-calendar meetings use the first auto-joining source in stable source order. Legacy rows
    without per-source names fall back to the user-wide bot context below.
    """
    raw = data.get("calendar_sources")
    sources = [source for source in raw if isinstance(source, dict)] if isinstance(raw, list) else []
    source = next((item for item in sources if item.get("auto_join", True)), None)
    if source is None and sources:
        source = sources[0]
    value = source.get("bot_name") if source else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _production_transcribe_gate() -> Optional[str]:
    """Mirror POST /bots' CC4 fail-loud STT gate: when transcription resolves ON (env default) but
    the ``stt`` capability is not configured, refuse the auto-spawn with the reason string.

    Read through ``env_flag`` for the same reason as router.py: with a bare ``os.getenv`` a
    set-but-empty ``TRANSCRIBE_ENABLED=`` made ``"" != "true"`` true, so this gate returned None and
    refused nothing — the empty value both disabled transcription AND disarmed the alarm meant to
    catch it. That double failure is why the v0.12.5 witness saw silence with no error."""
    from ..config_preflight import CONFIGURED, capability_state, missing_capability_keys
    from .env_flags import env_flag

    if not env_flag("TRANSCRIBE_ENABLED", True):
        return None
    state = capability_state("stt")
    if state != CONFIGURED:
        unset = ", ".join(missing_capability_keys("stt"))
        return f"STT not configured (capability 'stt' is {state}: {unset} unset)"
    return None


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
    allow_uncapped: bool = False,
) -> dict:
    """One sweep: spawn every due scheduled meeting. Returns counters for observability:
    ``{"due": n, "spawned": n, "already": n, "errors": n, "skipped_uncapped": n}``.

    ``fetch_bot_context(user_id)`` supplies the per-user spawn context the gateway would have
    injected as headers (including the Calendar default ``bot_name``).
    Three states: the callable is ``None`` (no admin edge configured — the per-user cap is
    UNRESOLVABLE); it returns a dict (use it); it returns ``None`` (identity is configured but
    UNAVAILABLE right now — SKIP the row this tick).

    Fail-closed by default: an unresolvable cap SKIPS the row (never spawns past a cap we cannot
    read), both when no admin edge is configured (``fetch_bot_context is None``) and when identity
    is unreachable (the fetch returns ``None``). Set ``allow_uncapped=True`` (the deliberate
    self-host opt-in, env ``AUTO_JOIN_ALLOW_UNCAPPED=1``) to spawn uncapped when no admin edge is
    configured — the unsafe mode is then chosen, never defaulted.

    ``publish_status(user_id=…, meeting_id=…, native_id=…, status=…, when=…)`` optionally fans the
    row's frame to ``u:{user}:meetings`` after an error stamp so the terminal refreshes."""
    now = now or datetime.now(timezone.utc)
    gate = transcribe_gate if transcribe_gate is not None else _production_transcribe_gate

    rows = await repo.list_scheduled_meetings()
    due = due_rows(rows, now=now, lead_s=lead_s, grace_s=grace_s,
                   retry_backoff_s=retry_backoff_s)
    counters = {"due": len(due), "spawned": 0, "already": 0, "errors": 0,
                "skipped_uncapped": 0, "skipped_live": 0, "stopped": 0}
    # Duplicate-dispatch guard (defense in depth behind create_meeting_guarded's dedup): a bot
    # already owning this (user, platform, native) on a DIFFERENT row means the meeting is covered.
    live = live_keys(
        await repo.list_live_meetings() if hasattr(repo, "list_live_meetings") else None
    )
    ctx_cache: dict[int, Optional[dict]] = {}
    uncapped_warned = False
    # A calendar bot records and transcribes exactly like a manual one (founder ruling
    # 2026-08-17: the split default is not a policy, it is a bug). `request_bot` defaults
    # `recording_enabled=False` for its own callers' safety, so a sweep that passed nothing spawned
    # every calendar bot with `capture_modes=None` — no recording pipeline, `data.recording_enabled
    # = false`, and a dashboard that says "No audio recording for this meeting" as if that were
    # normal (#1216; stage rev 194 meeting 26353 auto/0 recordings vs 26354 manual/893KB master.webm,
    # 10 of 10 auto-joined rows unrecorded). Resolved through the SAME resolver POST /bots uses, so
    # the two spawners cannot drift again; there is no per-user setting today, and this invents none.
    recording_enabled = resolve_spawn_flag("RECORDING_ENABLED", default=True)
    transcribe_enabled = resolve_spawn_flag("TRANSCRIBE_ENABLED", default=True)

    async def _stamp_error(row: dict, message: str, *, counter: str = "errors",
                           event: str = "auto_join_failed") -> None:
        counters[counter] += 1
        next_retry = (now + timedelta(seconds=retry_backoff_s)).isoformat()
        await repo.merge_meeting_data(row["id"], {
            "auto_join_error": message,
            "auto_join_next_retry": next_retry,
        })
        log_event(event, audience="user", level="warning", span="meetings.auto_join",
                  user_id=row["user_id"], meeting_id=str(row["id"]),
                  fields={"error": message, "next_retry": next_retry})
        if publish_status is not None:
            data = row.get("data") if isinstance(row.get("data"), dict) else {}
            await publish_status(
                user_id=row["user_id"], meeting_id=row["id"],
                native_id=row.get("native_meeting_id"), status=row.get("status"),
                when=data.get("scheduled_at"),
            )

    for row in due:
        user_id = row["user_id"]
        holder = live.get((user_id, row.get("platform"), row.get("native_meeting_id")))
        if holder is not None and holder != row.get("id"):
            # A bot is already in this room on another row — the classic shape is a manual
            # "Send bot now" plus a calendar import of the same link that failed to adopt it
            # (live 2026-08-17: rows 26237 live + 26251 imported, native mjm-dycn-qdp). Refuse
            # LOUDLY (P18): the row carries the reason the terminal renders, never a silent skip.
            await _stamp_error(
                row,
                f"a bot is already in this meeting (meeting {holder}) — auto-join skipped so a "
                f"second bot never joins",
                counter="skipped_live", event="auto_join_skipped_live",
            )
            continue
        gate_error = gate()
        if gate_error:
            await _stamp_error(row, gate_error)
            continue

        ctx: Optional[dict]
        if fetch_bot_context is None:
            # No admin edge configured → the per-user cap is unresolvable. Fail closed: refuse to
            # spawn rather than spawn uncapped, unless the operator explicitly opted in.
            if not allow_uncapped:
                counters["skipped_uncapped"] += 1
                if not uncapped_warned:
                    uncapped_warned = True
                    log_event(
                        "auto_join_skipped_uncapped", audience="operator", level="warning",
                        span="meetings.auto_join", user_id=user_id, meeting_id=str(row["id"]),
                        fields={"reason": "no ADMIN_API_URL/INTERNAL_API_SECRET — per-user cap "
                                "unresolvable; refusing uncapped spawn. Set AUTO_JOIN_ALLOW_UNCAPPED=1 "
                                "to opt into uncapped self-host spawns."})
                continue
            ctx = {}
        else:
            if user_id not in ctx_cache:
                ctx_cache[user_id] = await fetch_bot_context(user_id)
            ctx = ctx_cache[user_id]
            if ctx is None:
                # identity configured but unreachable — skip this tick rather than spawn uncapped
                continue

        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        # Record the ATTEMPT before making it. Written first so it survives everything the attempt
        # can do next — including the two outcomes that leave no trace on this row: a spawn that
        # succeeds and a bot that then fails to join (the row goes terminal, and calendar sync
        # recreates it), or a process death mid-spawn. The stamp is what ``due_rows`` and calendar
        # sync both read to hold the next dispatch for one backoff interval.
        await repo.merge_meeting_data(row["id"], {"auto_join_last_attempt": now.isoformat()})
        try:
            await request_bot(
                repo, runtime,
                authority=authority,
                user_id=user_id,
                platform=row["platform"],
                native_meeting_id=row["native_meeting_id"],
                meeting_url=data.get("constructed_meeting_url"),
                bot_name=_calendar_bot_name(data) or ctx.get("bot_name"),
                recording_enabled=recording_enabled,
                transcribe_enabled=transcribe_enabled,
                max_concurrent=ctx.get("max_concurrent"),
                webhook_url=ctx.get("webhook_url"),
                webhook_secret=ctx.get("webhook_secret"),
                webhook_events=ctx.get("webhook_events"),
                token_secret=token_secret,
                redis_url=redis_url,
            )
        except DuplicateMeeting:
            # a manual "Send bot now" (or a racing sweep) already claimed it — success, not an error
            counters["already"] += 1
            continue
        except MeetingStopped:
            # The user stopped it between this tick's read and the spawn fence. Not an error and not
            # a backoff-worthy failure: the row is already terminalized as stopped by the fence, and
            # `due_rows` will never offer it again. Counted so the sweep's numbers stay honest.
            counters["stopped"] += 1
            log_event("auto_join_stopped", audience="user", span="meetings.auto_join",
                      user_id=user_id, meeting_id=str(row["id"]),
                      fields={"reason": "the user stopped this meeting while the bot was starting"})
            continue
        except (MaxBotsExceeded, QuotaExceeded) as e:
            await _stamp_error(row, str(e) or "bot concurrency limit reached")
            continue
        except ServiceAuthorityDenied as e:
            await _stamp_error(
                row,
                f"service not allowed ({e.reason}; decision {e.decision_id})",
            )
            continue
        except ServiceAuthorityUnavailable:
            await _stamp_error(row, "service authority unavailable")
            continue
        except SpawnFailed as e:
            await _stamp_error(row, str(e) or "bot workload failed to start")
            continue
        counters["spawned"] += 1
        if data.get("auto_join_error"):
            # a prior failure resolved — clear the stamp so the row reads clean
            await repo.merge_meeting_data(row["id"], {
                "auto_join_error": None, "auto_join_next_retry": None,
            })
        log_event("auto_join_spawned", audience="user", span="meetings.auto_join",
                  user_id=user_id, meeting_id=str(row["id"]),
                  fields={"platform": row["platform"], "native": row["native_meeting_id"]})

    return counters
