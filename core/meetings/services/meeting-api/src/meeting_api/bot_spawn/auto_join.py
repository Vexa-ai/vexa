"""Auto-join sweep — "scheduled" MEANS the bot joins.

One tick scans PLANNED rows in status ``scheduled`` whose ``data.scheduled_at`` has arrived
(within ``lead_s`` before start, up to ``grace_s`` after — never join hours late) and, unless the
per-meeting ``data.auto_join`` toggle is off, spawns the bot through the SAME ``request_bot`` flow
POST /bots runs. The spawn CLAIMS the planned row in place (``create_meeting_guarded``'s claim
branch), so the sweep is idempotent by construction: a claimed row leaves ``scheduled`` and drops
out of the sweep's predicate, and the per-user advisory lock serializes it against a concurrent
manual "Send bot now" (that race surfaces here as ``DuplicateMeeting`` — someone already joined —
counted, never error-stamped).

Failures are LOUD, never silent (P18/P10): a cap/quota rejection or spawn failure stamps
``data.auto_join_error`` (+ ``data.auto_join_next_retry`` backoff so one bad row doesn't re-fire
every tick) — the terminal surfaces it on the meeting row.

``auto_join`` defaults ON when the key is absent — planning a meeting with a time means the bot
comes, opting out is the explicit act.

The tick is a pure-ish function over injected ports (repo, runtime, context fetcher, clock) — the
entrypoint (``__main__``) wraps it in the standard poll loop; tests drive single ticks offline.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from ..obs import log_event
from .ports import MaxBotsExceeded, QuotaExceeded, SpawnFailed
from .service import DuplicateMeeting, request_bot
from .url_validation import UnsafeMeetingUrl

# Sweep cadence/window env vocabulary (config.v1: all optional, sane defaults).
DEFAULT_LEAD_S = 60          # AUTO_JOIN_LEAD_S — join this many seconds BEFORE scheduled_at
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
             lead_s: float = DEFAULT_LEAD_S, grace_s: float = DEFAULT_GRACE_S) -> list[dict]:
    """The PURE due-filter over ``scheduled`` rows: auto_join on (absent = on), a joinable link,
    ``scheduled_at`` inside [start - lead, start + grace], and past any error backoff."""
    due: list[dict] = []
    for row in rows:
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
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
        due.append(row)
    return due


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
    injected as headers (``{"max_concurrent", "webhook_url", "webhook_secret", "webhook_events"}``).
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
    due = due_rows(rows, now=now, lead_s=lead_s, grace_s=grace_s)
    counters = {"due": len(due), "spawned": 0, "already": 0, "errors": 0, "skipped_uncapped": 0}
    ctx_cache: dict[int, Optional[dict]] = {}
    uncapped_warned = False

    async def _stamp_error(row: dict, message: str) -> None:
        counters["errors"] += 1
        next_retry = (now + timedelta(seconds=retry_backoff_s)).isoformat()
        await repo.merge_meeting_data(row["id"], {
            "auto_join_error": message,
            "auto_join_next_retry": next_retry,
        })
        log_event("auto_join_failed", audience="user", level="warning", span="meetings.auto_join",
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
        try:
            await request_bot(
                repo, runtime,
                user_id=user_id,
                platform=row["platform"],
                native_meeting_id=row["native_meeting_id"],
                meeting_url=data.get("constructed_meeting_url"),
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
        except (MaxBotsExceeded, QuotaExceeded) as e:
            await _stamp_error(row, str(e) or "bot concurrency limit reached")
            continue
        except (SpawnFailed, UnsafeMeetingUrl) as e:
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
