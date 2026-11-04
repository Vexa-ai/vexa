"""Google Meet refusal: join again from a NEW bot pod, on the same meeting (founder ruling 1 Nov 2026).

Meet refuses some anonymous knocks outright: ``CreateMeetingDevice`` answers 403, the page shows "You
can't join this video call", and no request ever reaches the host. The bot knocks again on the same
page (``@vexa/join`` refusal.ts) and gives up after its knocks with ``google_meet_join_refused``. On
prod (seq60, 2 Nov) a refusal held for all 10 knocks of one bot while the same person's next bot, a
new pod, got in on its second knock 35 s later: the refusal sticks to a bot, not to a knock.

So a refused bot is not the end of the meeting. When its terminal callback says
``google_meet_join_refused`` and the meeting is still on its way in:

  * the terminal is NOT applied — the meeting stays ``joining``, nobody is told it failed;
  * the attempt is recorded on ``data.meet_respawn`` and a new pod is due after a short wait
    (``VEXA_MEET_RESPAWN_DELAYS_S``, default 30 s then 60 s);
  * the sweep (``respawn_due``, every ``MEET_RESPAWN_INTERVAL_S``) starts the next bot on the same row
    through ``request_bot(respawn_of=…)`` — a new session, a new workload, preferring a node no
    earlier attempt ran on (``avoidNodes``, never required);
  * after ``VEXA_MEET_RESPAWN_PODS`` pods (default 3), or when the next one would start later than
    ``VEXA_MEET_RESPAWN_WINDOW_S`` (default 900 s) after the first, the last refusal is applied as the
    meeting's terminal, as before.

Only an anonymous Meet bot qualifies: a signed-in bot would rejoin as the same account. The state is
one block on the meeting row, written only here and by the stop route (``cancelled``):

    data.meet_respawn = {
        "spawn": {bot_name, language, allowed_languages, task, transcription_tier, automatic_leave},
        "first_at": ISO, "state": running | pending | spawning | exhausted | cancelled,
        "next_at": ISO | absent,
        "attempts": [{session_uid, workload_id, node, screenshot_key, detail, at}],
    }

There is no durable scheduler in prod (``scheduling/`` is eval-only), so ``next_at`` on the row plus
the single-flight sweep IS the scheduler: a meeting-api restart loses nothing.
"""
from __future__ import annotations
from .machine import PRE_LOBBY_STATUSES

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

from ..obs import log_event

KEY = "meet_respawn"
REFUSAL_MARKER = "google_meet_join_refused"
PLATFORM = "google_meet"

#: Row statuses a respawn may run from: the bot never reached the waiting room. A refused bot's row is
#: `joining`; `awaiting_admission` means a host could see a request, and a new bot over it would be an
#: illegal `joining` edge.

RUNNING, PENDING, SPAWNING, EXHAUSTED, CANCELLED = "running", "pending", "spawning", "exhausted", "cancelled"
#: States in which the row's current workload is a retired attempt (its death is not the meeting's end).
WAITING_STATES = frozenset({PENDING, SPAWNING})

#: The request fields a respawn repeats. Everything else a spawn needs is on the row already
#: (constructed_meeting_url, transcribe/recording flags, webhook, workspace) or re-resolved per spawn
#: (transcription backend, credentials) — no secret is stored here.
SPAWN_FIELDS = ("bot_name", "language", "allowed_languages", "task", "transcription_tier", "automatic_leave")


@dataclass(frozen=True)
class Policy:
    pods: int                   # bot pods per meeting, the first included; 1 turns the respawn off
    delays_s: tuple[float, ...]  # wait before pod 2, pod 3, …; the last value repeats
    window_s: float             # no pod starts later than this after the first spawn

    @property
    def enabled(self) -> bool:
        return self.pods > 1

    def delay_before(self, pod: int) -> float:
        """Wait before pod number ``pod`` (2 = the first respawn)."""
        i = max(0, min(pod - 2, len(self.delays_s) - 1))
        return self.delays_s[i]


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        v = int(raw) if raw else default
    except ValueError:
        return default
    return v if lo <= v <= hi else default


def policy() -> Policy:
    """Read the respawn policy from the environment. Invalid values fall back to the default."""
    pods = _int_env("VEXA_MEET_RESPAWN_PODS", 3, 1, 5)
    window = float(_int_env("VEXA_MEET_RESPAWN_WINDOW_S", 900, 60, 1800))
    delays: tuple[float, ...] = (30.0, 60.0)
    raw = (os.getenv("VEXA_MEET_RESPAWN_DELAYS_S") or "").strip()
    if raw:
        try:
            parsed = tuple(float(x) for x in raw.split(","))
            if parsed and all(0 <= d <= 300 for d in parsed):
                delays = parsed
        except ValueError:
            pass
    return Policy(pods=pods, delays_s=delays, window_s=window)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat()


def _parse(v: Any) -> Optional[datetime]:
    if not isinstance(v, str):
        return None
    try:
        t = datetime.fromisoformat(v)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def initial_block(spawn: Mapping[str, Any], *, now: Optional[datetime] = None) -> dict:
    """The block a qualifying first spawn writes on its row."""
    return {
        "spawn": {k: spawn.get(k) for k in SPAWN_FIELDS if spawn.get(k) is not None},
        "first_at": _iso(now or _now()),
        "state": RUNNING,
        "attempts": [],
    }


def qualifies(platform: str, authenticated: bool) -> bool:
    return platform == PLATFORM and not authenticated and policy().enabled


def _raw_evidence(body: Mapping[str, Any]) -> Mapping[str, Any]:
    ev = body.get("join_evidence")
    return ev if isinstance(ev, Mapping) else {}


def is_meet_refusal(body: Mapping[str, Any]) -> bool:
    """A bot's terminal saying Meet refused the join (``@vexa/join`` GoogleJoinRefusedError)."""
    if body.get("status") != "failed":
        return False
    texts = (_raw_evidence(body).get("detail"), body.get("reason"), body.get("error_details"))
    return any(isinstance(t, str) and REFUSAL_MARKER in t for t in texts)


def tried_nodes(block: Mapping[str, Any]) -> list[str]:
    """Nodes earlier attempts ran on, oldest first, deduplicated (the runtime takes at most 10)."""
    out: list[str] = []
    for a in block.get("attempts") or []:
        node = a.get("node") if isinstance(a, Mapping) else None
        if isinstance(node, str) and node and node not in out:
            out.append(node)
    return out[-10:]


def waiting_on_respawn(data: Optional[Mapping[str, Any]]) -> bool:
    """True while the next pod is due or starting (no live bot of this meeting is on its way in)."""
    block = (data or {}).get(KEY)
    return isinstance(block, Mapping) and block.get("state") in WAITING_STATES


def retired_workload(data: Optional[Mapping[str, Any]], workload_id: Optional[str]) -> bool:
    """True when ``workload_id`` is a refused attempt's pod and the next pod is due or starting: its
    end is that attempt's end, not the meeting's."""
    if not workload_id or not waiting_on_respawn(data):
        return False
    attempts = ((data or {}).get(KEY) or {}).get("attempts") or []
    return any(isinstance(a, Mapping) and a.get("workload_id") == workload_id for a in attempts)


async def on_bot_terminal(repo: Any, body: Mapping[str, Any], *, now: Optional[datetime] = None) -> Optional[dict]:
    """Decide what a bot's terminal callback means for its meeting.

    Returns the updated block when a new pod is scheduled — the caller then acknowledges the callback
    WITHOUT applying the terminal. Returns None when the terminal applies as usual (not a Meet refusal,
    not eligible, or the last pod), after recording a final attempt on the block where there is one.
    Best-effort: any failure here returns None, so the terminal applies exactly as before."""
    if not is_meet_refusal(body):
        return None
    getter = getattr(repo, "get_meeting_by_session", None)
    session_uid = body.get("connection_id")
    if getter is None or not session_uid:
        return None
    try:
        row = await getter(session_uid=session_uid)
    except Exception as e:  # noqa: BLE001
        log_event("meet_respawn_lookup_failed", audience="system", level="warning",
                  span="lifecycle.callback", fields={"error": type(e).__name__})
        return None
    if not row:
        return None
    data = row.get("data") or {}
    block = data.get(KEY)
    if (
        row.get("platform") != PLATFORM
        or not isinstance(block, Mapping)
        # SPAWNING too: the new bot may report before the sweep marks its spawn RUNNING.
        or block.get("state") not in (RUNNING, SPAWNING)
        or row.get("status") not in PRE_LOBBY_STATUSES
        or data.get("stop_requested")
        or row.get("latest_session_uid") != session_uid   # an older bot's late report changes nothing
    ):
        return None
    pol = policy()
    now = now or _now()
    ev = _raw_evidence(body)
    attempt = {
        "session_uid": session_uid,
        "workload_id": row.get("bot_container_id"),
        "node": ev.get("node") if isinstance(ev.get("node"), str) else None,
        "screenshot_key": ev.get("screenshot_key") if isinstance(ev.get("screenshot_key"), str) else None,
        "detail": str(ev.get("detail") or body.get("reason") or "")[:300],
        "at": _iso(now),
    }
    attempts = [*list(block.get("attempts") or []), attempt]
    pod_next = len(attempts) + 1
    first_at = _parse(block.get("first_at")) or now
    delay = pol.delay_before(pod_next)
    next_at = now + timedelta(seconds=delay)
    new_block = {**block, "attempts": attempts}
    if not pol.enabled or pod_next > pol.pods or (next_at - first_at).total_seconds() > pol.window_s:
        new_block.update(state=EXHAUSTED)
        new_block.pop("next_at", None)
        await _write(repo, row["id"], new_block)
        log_event("meet_respawn_exhausted", audience="user", level="warning", span="lifecycle.callback",
                  meeting_id=str(row["id"]), fields={"pods": len(attempts), "node": attempt["node"]})
        return None
    new_block.update(state=PENDING, next_at=_iso(next_at))
    if not await _write(repo, row["id"], new_block):
        return None   # unrecorded → apply the terminal, never strand the meeting on a retry nobody runs
    log_event("meet_respawn_scheduled", audience="user", level="warning", span="lifecycle.callback",
              meeting_id=str(row["id"]),
              fields={"pod": pod_next, "of": pol.pods, "delay_s": delay, "refused_on_node": attempt["node"]})
    return new_block


async def _write(repo: Any, meeting_id: Any, block: Mapping[str, Any]) -> bool:
    try:
        await repo.merge_meeting_data(meeting_id, {KEY: dict(block)})
        return True
    except Exception as e:  # noqa: BLE001
        log_event("meet_respawn_write_failed", audience="system", level="warning",
                  span="meetings.meet_respawn", meeting_id=str(meeting_id),
                  fields={"error": type(e).__name__})
        return False


async def respawn_due(
    repo: Any,
    runtime: Any,
    *,
    authority: Any = None,
    token_secret: Optional[str] = None,
    redis_url: Optional[str] = None,
    redis_grant: Any = None,
    now: Optional[datetime] = None,
) -> int:
    """Start the next pod for every meeting whose respawn is due. Returns how many were started.

    Run single-flight across replicas (``__main__``'s ``_guarded``). A row is marked ``spawning``
    before its spawn, so a slow spawn is never doubled by the next tick; a spawn that cannot start
    lands the row on its terminal through ``request_bot``'s own failure path."""
    from ..bot_spawn.service import MeetingStopped, request_bot

    lister = getattr(repo, "list_meet_respawns", None)
    if lister is None:
        return 0
    now = now or _now()
    started = 0
    for item in await lister():
        block = item.get("block") or {}
        due = _parse(block.get("next_at"))
        if block.get("state") != PENDING or due is None or due > now:
            continue
        row = await repo.get_meeting(item["id"])
        if not row or row.get("status") not in PRE_LOBBY_STATUSES:
            continue
        data = row.get("data") or {}
        block = data.get(KEY) or {}
        if block.get("state") != PENDING:
            continue
        pod = len(block.get("attempts") or []) + 1
        if not await _write(repo, row["id"], {**block, "state": SPAWNING}):
            continue
        spawn = dict(block.get("spawn") or {})
        try:
            await request_bot(
                repo, runtime,
                authority=authority,
                user_id=row["user_id"],
                platform=row["platform"],
                native_meeting_id=row["native_meeting_id"],
                meeting_url=data.get("constructed_meeting_url"),
                recording_enabled=bool(data.get("recording_enabled", False)),
                transcribe_enabled=bool(data.get("transcribe_enabled", True)),
                token_secret=token_secret,
                redis_url=redis_url,
                redis_grant=redis_grant,
                respawn_of=row,
                avoid_nodes=tried_nodes(block) or None,
                **{k: spawn[k] for k in SPAWN_FIELDS if k in spawn},
            )
        except MeetingStopped:
            continue   # the spawn fence terminalized it as stopped
        except Exception as e:  # noqa: BLE001 — request_bot already failed the row where it could
            log_event("meet_respawn_spawn_failed", audience="user", level="error",
                      span="meetings.meet_respawn", meeting_id=str(row["id"]),
                      fields={"pod": pod, "error": type(e).__name__, "detail": str(e)[:200]})
            fresh = await repo.get_meeting(row["id"])
            if fresh and fresh.get("status") in PRE_LOBBY_STATUSES:
                await repo.fail_meeting(
                    meeting_id=row["id"],
                    reason=f"Google Meet refused the join; the next bot could not start: {str(e)[:200]}",
                    failure_stage="joining",
                    completion_reason=REFUSED_COMPLETION_REASON,
                    data={KEY: {**block, "state": EXHAUSTED}},
                )
            continue
        fresh = await repo.get_meeting(row["id"])
        fresh_block = ((fresh or {}).get("data") or {}).get(KEY) or block
        if fresh_block.get("state") == SPAWNING:
            nb = {**fresh_block, "state": RUNNING}
            nb.pop("next_at", None)
            await _write(repo, row["id"], nb)
        started += 1
        log_event("meet_respawn_started", audience="user", span="meetings.meet_respawn",
                  meeting_id=str(row["id"]), fields={"pod": pod, "avoid_nodes": tried_nodes(block)})
    return started


#: The completion reason a meeting lands on when Meet refused every pod.
REFUSED_COMPLETION_REASON = "platform_join_refused"


def as_final_refusal(body: Mapping[str, Any]) -> dict:
    """The terminal a Meet refusal lands as: ``platform_join_refused``, graded ``system_fault`` —
    whether or not new pods were tried, and whatever reason an older bot stamped on it."""
    out = {**body, "completion_reason": REFUSED_COMPLETION_REASON}
    ev = body.get("join_evidence")
    if isinstance(ev, Mapping):
        out["join_evidence"] = {**ev, "reason": REFUSED_COMPLETION_REASON, "attribution": "system_fault"}
    return out
