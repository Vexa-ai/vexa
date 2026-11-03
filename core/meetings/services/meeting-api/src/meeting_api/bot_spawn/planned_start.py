"""Starting the bot for a PLANNED row — the one place a booked meeting's bot is started (ADR-0052 §5).

A planned row (``POST /meetings`` with a time, a calendar import) becomes a bot here and nowhere else:
the start route, ``POST /internal/meetings/{meeting_id}/start`` (``start_planned_meeting``) — the
service door's call for Flows' ``booked_bot``, which owns the clock (ADR-0046, ADR-0052 §3, §6). It
starts the bot for THAT row id, never "the newest planned row of this room", with the bot's join
time (``join_at`` = start − ``BOOKED_JOIN_LEAD_S``) in its invocation, and answers why when it does
not. The 30 s auto-join sweep that also called ``start_row`` was removed in s2d.

The safeguards (moved here unchanged from the sweep, so nothing is reimplemented in Flows):

1. the operator's block list (``VEXA_AUTO_JOIN_BLOCK``) — checked before anything can spawn;
2. a bot already in this room on another row (``LIVE_STATUSES``) — refused, the holder's id stamped;
3. the STT capability gate POST /bots applies;
4. the owner's spawn context (cap, webhook config, bot name) and the owner's own ``auto_join_block``
   — an unreadable owner list refuses, an unreachable identity defers;
5. ``data.auto_join_last_attempt`` stamped BEFORE the attempt (calendar sync copies it to sibling
   rows, so one occurrence gets at most one dispatch per backoff interval however many rows it gets);
6. the per-calendar-source bot name; recording and transcription resolved through
   ``env_flags.resolve_spawn_flag``, the resolver POST /bots uses;
7. credit, through ``request_bot``'s service-authority admission;
8. every refusal or spawn failure stamped on the row (``data.auto_join_error`` +
   ``data.auto_join_next_retry``) — the terminal shows it — and returned as a reason.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from ..obs import log_event
from ..service_authority import ServiceAuthorityDenied, ServiceAuthorityUnavailable
from .auto_join_block import blocked_reason, parse_block
from .env_flags import resolve_spawn_flag
from .meeting_host import MeetingHostRefused
from .ports import (
    JoinAtRefused,
    MaxBotsExceeded,
    MeetingStopped,
    NotScheduled,
    QuotaExceeded,
    SpawnFailed,
    planned_row_startable,
)
from .service import JOIN_AT_SLACK_S, DuplicateMeeting, booked_pod_lead_s, request_bot

DEFAULT_RETRY_BACKOFF_S = 300   # AUTO_JOIN_RETRY_BACKOFF_S — error-stamped rows wait this long
#: ADR-0052 §2: the bot opens the meeting this long before it starts, and may still be started up to
#: ``BOOKED_GRACE_S`` after. Owned by meeting-api config; Flows receives the computed times.
DEFAULT_BOOKED_JOIN_LEAD_S = 120
DEFAULT_BOOKED_GRACE_S = 600
#: The ``Retry-After`` the route answers on a full ceiling or a transient refusal (ADR-0052 §6).
DEFAULT_RETRY_AFTER_S = 15

# Every status in which a bot OWNS the room. A due row whose (user, platform, native) is held by
# one of these on ANOTHER row must never spawn: two Vexa bots in one meeting is customer-visible
# and never correct, however the two rows came to exist.
LIVE_STATUSES = (
    "requested", "joining", "awaiting_admission", "active",
    "needs_help", "needs_human_help", "stopping",
)


def _env_seconds(key: str, default: int) -> int:
    """A non-negative whole number of seconds from ``key``; an unset, unparseable or negative value
    is the default (never a silently widened or closed window)."""
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(float(raw))
    except ValueError:
        return default
    return value if value >= 0 else default


def booked_join_lead_s() -> int:
    """``BOOKED_JOIN_LEAD_S`` (s), default 120: the bot opens the meeting at start − this."""
    return _env_seconds("BOOKED_JOIN_LEAD_S", DEFAULT_BOOKED_JOIN_LEAD_S)


def booked_grace_s() -> int:
    """``BOOKED_GRACE_S`` (s), default 600: the last moment a bot may be started is start + this."""
    return _env_seconds("BOOKED_GRACE_S", DEFAULT_BOOKED_GRACE_S)


def parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


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


def calendar_bot_name(data: dict) -> Optional[str]:
    """Resolve the bot name from the calendar source that armed this meeting.

    Multi-calendar meetings use the first auto-joining source in stable source order. Legacy rows
    without per-source names fall back to the user-wide bot context.
    """
    raw = data.get("calendar_sources")
    sources = [source for source in raw if isinstance(source, dict)] if isinstance(raw, list) else []
    source = next((item for item in sources if item.get("auto_join", True)), None)
    if source is None and sources:
        source = sources[0]
    value = source.get("bot_name") if source else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def production_transcribe_gate() -> Optional[str]:
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


@dataclass
class Outcome:
    """What one start attempt came to.

    ``kind`` is the sweep's counter name (``spawned``, ``already``, ``stopped``, ``blocked``,
    ``skipped_live``, ``errors``, ``skipped_uncapped``) or a state the sweep does not count
    (``unavailable`` — identity unreachable, try later; ``not_scheduled``; ``past_deadline``;
    ``too_early``; ``backoff``; ``not_found``). ``code`` + ``http`` are the route's answer."""

    kind: str
    code: str
    http: int
    message: str = ""
    meeting: Optional[dict] = None
    retry_after: Optional[int] = None
    extra: dict = field(default_factory=dict)


async def start_row(
    row: dict,
    *,
    repo,
    runtime,
    authority=None,
    get_context: Callable[[int], Awaitable[Optional[dict]]],
    publish_status: Optional[Callable[..., Awaitable[None]]] = None,
    transcribe_gate: Optional[Callable[[], Optional[str]]] = None,
    live_holder: Any = None,
    now: datetime,
    retry_backoff_s: float = DEFAULT_RETRY_BACKOFF_S,
    error_hold_s: Optional[float] = None,
    token_secret: Optional[str] = None,
    redis_url: Optional[str] = None,
    redis_grant=None,
    allow_uncapped: bool = False,
    uncapped: bool = False,
    deployment_block=None,
    join_at: Optional[datetime] = None,
    planned_meeting_id: Optional[int] = None,
) -> Outcome:
    """Run the safeguards over one planned row and, when they pass, start its bot.

    ``get_context(user_id)`` returns the owner's spawn context, or None when identity is configured
    but unreachable (→ ``unavailable``, nothing stamped). ``uncapped`` = no identity edge is
    configured at all: the cap is unresolvable, so the start is refused (``skipped_uncapped``)
    unless the operator opted in with ``allow_uncapped``. ``live_holder`` is the id of a row a bot
    already owns this room on (None when nobody does). A refusal is held for ``error_hold_s``
    (default ``retry_backoff_s``) through ``data.auto_join_next_retry``; the route holds it for its
    own ``Retry-After``, the sweep for its backoff. A refusal that certainly started no pod (cap,
    credit, host, a cancel that won) gives back this attempt's ``auto_join_last_attempt`` stamp:
    that stamp holds an occurrence after a pod was started, not after a refusal."""
    gate = transcribe_gate if transcribe_gate is not None else production_transcribe_gate
    user_id = row["user_id"]

    async def _stamp(message: str, *, kind: str, code: str, http: int,
                     event: str = "auto_join_failed", retry_after: Optional[int] = None,
                     extra: Optional[dict] = None) -> Outcome:
        hold = retry_backoff_s if error_hold_s is None else error_hold_s
        next_retry = (now + timedelta(seconds=hold)).isoformat()
        await repo.merge_meeting_data(row["id"], {
            "auto_join_error": message,
            "auto_join_next_retry": next_retry,
        })
        log_event(event, audience="user", level="warning", span="meetings.auto_join",
                  user_id=user_id, meeting_id=str(row["id"]),
                  fields={"error": message, "next_retry": next_retry})
        if publish_status is not None:
            data = row.get("data") if isinstance(row.get("data"), dict) else {}
            await publish_status(
                user_id=user_id, meeting_id=row["id"],
                native_id=row.get("native_meeting_id"), status=row.get("status"),
                when=data.get("scheduled_at"),
            )
        return Outcome(kind, code, http, message, retry_after=retry_after, extra=extra or {})

    # 1. THE OPERATOR SAID NEVER — checked before anything else can lead to a spawn.
    reason = blocked_reason(row, deployment_block)
    if reason:
        return await _stamp(reason, kind="blocked", code="blocked", http=403,
                            event="auto_join_blocked")
    # 2. A bot is already in this room on another row — the classic shape is a manual "Send bot now"
    #    plus a calendar import of the same link that failed to adopt it (live 2026-08-17: rows
    #    26237 live + 26251 imported). Refused LOUDLY (P18): the row carries the reason.
    if live_holder is not None and live_holder != row.get("id"):
        return await _stamp(
            f"a bot is already in this meeting (meeting {live_holder}) — auto-join skipped so a "
            f"second bot never joins",
            kind="skipped_live", code="already_live", http=409, event="auto_join_skipped_live",
            extra={"holder": live_holder},
        )
    # 3. the STT gate POST /bots applies.
    gate_error = gate()
    if gate_error:
        return await _stamp(gate_error, kind="errors", code="stt_unconfigured", http=503,
                            retry_after=DEFAULT_RETRY_AFTER_S)

    # 4. the owner's spawn context and the owner's own block list.
    if uncapped:
        # No admin edge configured → the per-user cap is unresolvable. Fail closed: refuse to spawn
        # rather than spawn uncapped, unless the operator explicitly opted in.
        if not allow_uncapped:
            return Outcome("skipped_uncapped", "cap_unresolvable", 503,
                           "no identity edge is configured, so the account's bot limit cannot be "
                           "read; set AUTO_JOIN_ALLOW_UNCAPPED=1 to start bots uncapped",
                           retry_after=DEFAULT_RETRY_AFTER_S)
        ctx: dict = {}
    else:
        got = await get_context(user_id)
        if got is None:
            # identity configured but unreachable — try later rather than spawn uncapped
            return Outcome("unavailable", "identity_unavailable", 503,
                           "the account's spawn context could not be read",
                           retry_after=DEFAULT_RETRY_AFTER_S)
        ctx = got
        # THE OWNER SAID NEVER. A list identity sent but this side cannot read is a refusal, never
        # "no list": ignoring it would send the bot exactly where the owner forbade.
        try:
            owner_block = parse_block(ctx.get("auto_join_block") or [], what="auto_join_block")
        except ValueError:
            return await _stamp("auto-join refused: this account's block list could not be read",
                                kind="blocked", code="blocked", http=403,
                                event="auto_join_blocked")
        reason = blocked_reason(row, owner_block)
        if reason:
            return await _stamp(reason, kind="blocked", code="blocked", http=403,
                                event="auto_join_blocked")

    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    prior_attempt = data.get("auto_join_last_attempt")
    # 5. Record the ATTEMPT before making it. Written first so it survives everything the attempt
    #    can do next — including the two outcomes that leave no trace on this row: a spawn that
    #    succeeds and a bot that then fails to join (the row goes terminal, and calendar sync
    #    recreates it), or a process death mid-spawn.
    await repo.merge_meeting_data(row["id"], {"auto_join_last_attempt": now.isoformat()})
    # 6. A calendar bot records and transcribes exactly like a manual one (#1216): the SAME
    #    resolver POST /bots uses, so the two spawners cannot drift.
    recording_enabled = resolve_spawn_flag("RECORDING_ENABLED", default=True)
    transcribe_enabled = resolve_spawn_flag("TRANSCRIBE_ENABLED", default=True)
    extra_kw: dict = {}
    if join_at is not None:
        extra_kw["join_at"] = join_at
    if planned_meeting_id is not None:
        extra_kw["planned_meeting_id"] = planned_meeting_id

    async def _no_pod() -> None:
        # Nothing was started: the occurrence keeps the stamp it had before this attempt.
        await repo.merge_meeting_data(row["id"], {"auto_join_last_attempt": prior_attempt})

    try:
        meeting = await request_bot(
            repo, runtime,
            authority=authority,
            user_id=user_id,
            platform=row["platform"],
            native_meeting_id=row["native_meeting_id"],
            meeting_url=data.get("constructed_meeting_url"),
            bot_name=calendar_bot_name(data) or ctx.get("bot_name"),
            recording_enabled=recording_enabled,
            transcribe_enabled=transcribe_enabled,
            max_concurrent=ctx.get("max_concurrent"),
            webhook_url=ctx.get("webhook_url"),
            webhook_secret=ctx.get("webhook_secret"),
            webhook_events=ctx.get("webhook_events"),
            token_secret=token_secret,
            redis_url=redis_url,
            redis_grant=redis_grant,
            **extra_kw,
        )
    except NotScheduled as e:
        # A cancel, a toggle or a stop committed before this start took the row lock: it wins.
        await _no_pod()
        return Outcome("not_scheduled", "not_scheduled", 409, str(e))
    except DuplicateMeeting as e:
        # a manual "Send bot now" (or a racing sweep) already claimed it — success, not an error
        return Outcome("already", "already_live", 409, str(e) or "a bot is already in this meeting")
    except MeetingStopped:
        # The user stopped it between the read and the spawn fence: the row is terminalized as
        # stopped by the fence and is never offered again. Not an error, not a backoff.
        log_event("auto_join_stopped", audience="user", span="meetings.auto_join",
                  user_id=user_id, meeting_id=str(row["id"]),
                  fields={"reason": "the user stopped this meeting while the bot was starting"})
        return Outcome("stopped", "not_scheduled", 409, "the meeting was stopped")
    except JoinAtRefused as e:
        await _no_pod()
        return Outcome("too_early", "too_early", 409, str(e), retry_after=DEFAULT_RETRY_AFTER_S)
    except (MaxBotsExceeded, QuotaExceeded) as e:
        # The waiting pods count too (ADR-0052 §2): Flows retries until a slot frees or the deadline.
        await _no_pod()
        return await _stamp(str(e) or "bot concurrency limit reached", kind="errors",
                            code="max_bots", http=429, retry_after=DEFAULT_RETRY_AFTER_S)
    except ServiceAuthorityDenied as e:
        await _no_pod()
        return await _stamp(f"service not allowed ({e.reason}; decision {e.decision_id})",
                            kind="errors", code="service_denied", http=402,
                            retry_after=DEFAULT_RETRY_AFTER_S)
    except ServiceAuthorityUnavailable:
        return await _stamp("service authority unavailable", kind="errors",
                            code="authority_unavailable", http=503,
                            retry_after=DEFAULT_RETRY_AFTER_S)
    except SpawnFailed as e:
        return await _stamp(str(e) or "bot workload failed to start", kind="errors",
                            code="spawn_failed", http=503, retry_after=DEFAULT_RETRY_AFTER_S)
    except MeetingHostRefused as e:
        # The link's host is not the platform's, or reaches inside the deployment's network.
        await _no_pod()
        return await _stamp(str(e), kind="errors", code="meeting_host_refused", http=422)
    if data.get("auto_join_error"):
        # a prior failure resolved — clear the stamp so the row reads clean
        await repo.merge_meeting_data(row["id"], {
            "auto_join_error": None, "auto_join_next_retry": None,
        })
    log_event("auto_join_spawned", audience="user", span="meetings.auto_join",
              user_id=user_id, meeting_id=str(row["id"]),
              fields={"platform": row["platform"], "native": row["native_meeting_id"],
                      "join_at": meeting.get("data", {}).get("join_at")
                      if isinstance(meeting.get("data"), dict) else None})
    return Outcome("spawned", "started", 201, meeting=meeting)


async def start_planned_meeting(
    meeting_id: int,
    *,
    user_id: int,
    repo,
    runtime,
    authority=None,
    fetch_bot_context: Optional[Callable[[int], Awaitable[Optional[dict]]]] = None,
    publish_status: Optional[Callable[..., Awaitable[None]]] = None,
    transcribe_gate: Optional[Callable[[], Optional[str]]] = None,
    now: Optional[datetime] = None,
    retry_backoff_s: float = DEFAULT_RETRY_BACKOFF_S,
    token_secret: Optional[str] = None,
    redis_url: Optional[str] = None,
    redis_grant=None,
    allow_uncapped: bool = False,
    deployment_block=None,
) -> Outcome:
    """The start route's work: start the bot for planned row ``meeting_id`` of person ``user_id``.

    Refuses ``not_found`` (no such row, or not this person's — never says which), ``not_scheduled``
    (no longer ``scheduled``, ``auto_join`` off, stopped, or DELETED by its owner — ADR-0052 §5/§6:
    a cancel is "nothing to do" however it was made; re-checked under the row lock by the repo, so
    a cancel that lands first always wins), ``past_deadline`` (start + ``BOOKED_GRACE_S``
    has passed), ``backoff`` (inside the hold a prior attempt on this occurrence earned —
    ``auto_join_last_attempt`` / ``auto_join_next_retry``; ``Retry-After`` says when). Then the
    shared safeguards and the spawn (``start_row``) with ``join_at`` = start − ``BOOKED_JOIN_LEAD_S``.
    """
    now = now or datetime.now(timezone.utc)
    row = await repo.get_meeting(meeting_id)
    if row is None and await repo.planned_row_deleted(meeting_id, user_id):
        # A hard DELETE of the booking is a cancel too (ADR-0052 §5/§6): "nothing to do", not 404.
        return Outcome("not_scheduled", "not_scheduled", 409, f"meeting {meeting_id} was deleted")
    if not isinstance(row, dict) or row.get("user_id") != user_id:
        return Outcome("not_found", "not_found", 404, "no such planned meeting")
    if not row.get("native_meeting_id") and row.get("platform_specific_id"):
        row = {**row, "native_meeting_id": row["platform_specific_id"]}
    if not planned_row_startable(row, user_id=user_id, platform=row.get("platform"),
                                 native_meeting_id=row.get("native_meeting_id")):
        return Outcome("not_scheduled", "not_scheduled", 409,
                       f"meeting {meeting_id} is not scheduled with auto-join on")
    if not row.get("native_meeting_id") or row.get("platform") in (None, "", "unknown"):
        return Outcome("not_scheduled", "not_scheduled", 409,
                       f"meeting {meeting_id} has no joinable link")
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    start = parse_iso(data.get("scheduled_at"))
    if start is None:
        return Outcome("not_scheduled", "not_scheduled", 409,
                       f"meeting {meeting_id} has no start time")
    if now > start + timedelta(seconds=booked_grace_s()):
        return Outcome("past_deadline", "past_deadline", 409,
                       f"meeting {meeting_id} started more than {booked_grace_s()} s ago")
    join_at = start - timedelta(seconds=booked_join_lead_s())
    # Too early for a waiting pod (ADR-0052 §8): refused before anything is stamped, so an early
    # call earns no hold; ``Retry-After`` is when it may start.
    earliest = join_at - timedelta(seconds=booked_pod_lead_s() + JOIN_AT_SLACK_S)
    if now < earliest:
        return Outcome("too_early", "too_early", 409,
                       f"meeting {meeting_id} may start its bot from {earliest.isoformat()}",
                       retry_after=max(1, int((earliest - now).total_seconds() + 0.999)))
    holds = [t for t in (parse_iso(data.get("auto_join_next_retry")),
                         _plus(parse_iso(data.get("auto_join_last_attempt")), retry_backoff_s))
             if t is not None and now < t]
    if holds:
        wait = max(1, int((max(holds) - now).total_seconds() + 0.999))
        return Outcome("backoff", "backoff", 429,
                       data.get("auto_join_error") or "a start for this meeting was just attempted",
                       retry_after=wait)
    live = live_keys(await repo.list_live_meetings() if hasattr(repo, "list_live_meetings") else None)
    holder = live.get((user_id, row.get("platform"), row.get("native_meeting_id")))

    cache: dict[int, Optional[dict]] = {}

    async def get_context(uid: int) -> Optional[dict]:
        if uid not in cache:
            cache[uid] = await fetch_bot_context(uid) if fetch_bot_context is not None else {}
        return cache[uid]

    return await start_row(
        row, repo=repo, runtime=runtime, authority=authority, get_context=get_context,
        publish_status=publish_status, transcribe_gate=transcribe_gate, live_holder=holder,
        now=now, retry_backoff_s=retry_backoff_s, token_secret=token_secret, redis_url=redis_url,
        redis_grant=redis_grant, allow_uncapped=allow_uncapped, error_hold_s=DEFAULT_RETRY_AFTER_S,
        uncapped=fetch_bot_context is None, deployment_block=deployment_block,
        join_at=join_at, planned_meeting_id=row["id"],
    )


def _plus(at: Optional[datetime], seconds: float) -> Optional[datetime]:
    return at + timedelta(seconds=seconds) if at is not None else None
