"""schedule_digest — the AMBIENT terminal-state context: the user's meeting schedule as a
compact prompt block (terminal-state context bundle, slice 1).

A chat turn from a meetings-relevant surface (Meetings list / Today tab / a meeting or prep
tab) carries a ~15-line ``<schedule>`` digest so the agent knows *when now is* and what
surrounds it — the live meeting, today's remaining schedule, the next few upcoming, the last
few past. SERVER-DERIVED: agent-api fetches the caller's meetings from meeting-api (the source
of truth) — the client sends only its timezone + surface; nothing about the schedule is
client-asserted.

Seams (mirrors ``_http_meeting_owner_lookup``): ``fetch_user_meetings`` is the raw HTTP hop;
``digest_source`` wraps it in a per-subject TTL cache and NEVER raises (a failed fetch degrades
to "no digest", it must not fail the chat turn); ``build_schedule_digest`` is pure and renders
rows → the block. The same rows also feed the meeting-focus enrichment in api.py (server values
for status/title/scheduled_at/workspace_id beat the client's — a cold client store must not
ground a planned meeting as live).
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

logger = logging.getLogger("agent_api.schedule_digest")

# Section caps — the digest is a DIGEST: bounded, scannable, ~15 lines.
MAX_LIVE = 2
MAX_TODAY = 6
MAX_TODAY_FULL = 12
MAX_UPCOMING = 5
MAX_RECENT = 3
TITLE_MAX = 60
DIGEST_CHAR_CAP = 1600  # hard stop — a digest must never blow the prompt budget

# statuses (mirrors meeting_steering's phase model — kept literal here so this module stays
# importable without it)
_INTENT = {"idle", "scheduled"}
_LIVE = {"requested", "joining", "awaiting_admission", "active", "needs_help", "stopping"}
_PAST = {"completed", "failed", "stopped"}


# ── fetch (raw HTTP hop to meeting-api, gateway-style identity headers) ──────────────────────
def fetch_user_meetings(meeting_api_url: str, user_id: str,
                        member_workspaces: "list[str] | None" = None,
                        *, timeout_s: float = 3.0) -> "list[dict]":
    """The caller's meetings from meeting-api — three bounded queries merged by row id.

    Why three: ``GET /meetings`` orders ``created_at DESC``, so one page can miss future
    scheduled rows created long ago; per-status queries make the schedule sections reliable.
    Raises on transport errors — ``digest_source`` is the layer that degrades."""
    base = (meeting_api_url or "").rstrip("/")
    if not base:
        return []
    headers = {"X-User-Id": str(user_id)}
    if member_workspaces:
        headers["X-User-Workspaces"] = ",".join(member_workspaces)

    def _page(query: str) -> "list[dict]":
        req = urllib.request.Request(f"{base}/meetings?{query}", headers=headers)
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 — internal service URL
            body = json.loads(resp.read().decode("utf-8"))
        rows = body.get("meetings") if isinstance(body, dict) else None
        return rows if isinstance(rows, list) else []

    merged: "dict[int, dict]" = {}
    for q in ("status=scheduled&limit=100", "status=idle&limit=50",
              "status=active&limit=20", "limit=30"):
        for row in _page(q):
            rid = row.get("id")
            if isinstance(rid, int):
                merged[rid] = row
    return list(merged.values())


def digest_source(meeting_api_url: str,
                  membership_lister: "Optional[Callable[[str], list]]" = None,
                  *, ttl_s: float = 30.0) -> "Callable[[str], list[dict]]":
    """Per-subject TTL-cached, NEVER-raising rows source — the ``schedule_source`` seam
    ``create_app`` wires (injectable in tests). Failure → ``[]`` now, retried after a short
    (5s) cool-off rather than the full TTL."""
    cache: "dict[str, tuple[float, list[dict]]]" = {}
    FAIL_COOLOFF_S = 5.0

    def _workspaces(subject: str) -> "list[str]":
        if membership_lister is None:
            return []
        try:
            entries = membership_lister(subject) or []
            out = []
            for e in entries:
                wid = e.get("workspace_id") if isinstance(e, dict) else getattr(e, "workspace_id", None)
                if wid:
                    out.append(str(wid))
            return out
        except Exception:  # noqa: BLE001 — membership lookup is an enrichment, never a gate
            return []

    def _source(subject: str) -> "list[dict]":
        now = time.monotonic()
        hit = cache.get(subject)
        if hit and now - hit[0] < ttl_s and hit[1] is not None:
            return hit[1]
        try:
            rows = fetch_user_meetings(meeting_api_url, subject, _workspaces(subject))
        except Exception as exc:  # noqa: BLE001 — a digest must never fail the chat turn
            logger.warning("schedule fetch failed subject=%s (%s) — no digest this turn", subject, exc)
            cache[subject] = (now - ttl_s + FAIL_COOLOFF_S, [])
            return []
        cache[subject] = (now, rows)
        return rows

    return _source


# ── pure rendering ───────────────────────────────────────────────────────────────────────────
def _parse_dt(value) -> "Optional[datetime]":
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _tzinfo(tz: "str | None"):
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(tz) if tz else timezone.utc
    except Exception:  # noqa: BLE001 — an invalid client tz falls back to UTC, honestly labeled
        return timezone.utc


def _title(row: dict) -> str:
    data = row.get("data") or {}
    t = str(data.get("title") or "").strip() or str(row.get("native_meeting_id") or "").strip() or "Untitled meeting"
    return t[:TITLE_MAX] + "…" if len(t) > TITLE_MAX else t


def _line(row: dict, *, tzi, kind: str) -> str:
    data = row.get("data") or {}
    rid = row.get("id")
    title = _title(row)
    parts: "list[str]" = []
    if kind == "live":
        native = row.get("native_meeting_id")
        loc = f" ({row.get('platform')}/{native})" if native else ""
        return f'- [meeting {rid}] "{title}"{loc} — bot {row.get("status")}'
    if kind in ("today", "upcoming", "ended"):
        when = _parse_dt(data.get("scheduled_at"))
        if when is None:
            stamp = "unscheduled"
        elif kind == "today" or kind == "ended":
            stamp = when.astimezone(tzi).strftime("%H:%M")
        else:
            stamp = when.astimezone(tzi).strftime("%a %d %b %H:%M")
        ws = str(data.get("workspace_id") or "").strip()
        parts.append(f"prep workspace: {ws}" if ws else "no prep workspace")
        if data.get("auto_join") is False:
            parts.append("auto-join off")
        return f'- {stamp} [meeting {rid}] "{title}" — {" · ".join(parts)}'
    # recent
    ended = _parse_dt(row.get("end_time")) or _parse_dt(row.get("start_time")) or _parse_dt(row.get("updated_at"))
    stamp = ended.astimezone(tzi).strftime("%a %d %b") if ended else ""
    notes = bool(((data.get("processed") or {}).get("views")))
    tail = "notes ready" if notes else "transcript only"
    if row.get("status") == "failed":
        tail = "bot failed — no record"
    return f'- {stamp} [meeting {rid}] "{title}" — {tail}'.replace("-  [", "- [")


def build_schedule_digest(rows: "list[dict]", *, tz: "str | None" = None,
                          now: "Optional[datetime]" = None, full_day: bool = False) -> str:
    """Rows → the ``<schedule>`` block (or ``""`` for no rows at all). Pure; times render in
    the user's tz (invalid/absent → UTC, and the block says which). ``full_day=True`` (the
    Today focus) additionally lists today's already-ended meetings and raises the today cap."""
    tzi = _tzinfo(tz)
    moment = (now or datetime.now(timezone.utc)).astimezone(tzi)
    day_start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    live: "list[dict]" = []
    today: "list[tuple[datetime, dict]]" = []
    ended_today: "list[tuple[datetime, dict]]" = []
    upcoming: "list[tuple[Optional[datetime], dict]]" = []
    recent: "list[tuple[datetime, dict]]" = []

    for row in rows:
        status = str(row.get("status") or "").strip().lower()
        data = row.get("data") or {}
        when = _parse_dt(data.get("scheduled_at"))
        if status in _LIVE:
            live.append(row)
        elif status in _INTENT:
            if when is not None and day_start <= when.astimezone(tzi) < day_end and when >= moment.astimezone(timezone.utc):
                today.append((when, row))
            elif when is not None and when >= moment.astimezone(timezone.utc):
                upcoming.append((when, row))
            elif when is None:
                upcoming.append((None, row))
            elif full_day and day_start <= when.astimezone(tzi) < day_end:
                ended_today.append((when, row))
            # a stale scheduled row from a past day is noise — dropped
        elif status in _PAST:
            stamp = _parse_dt(row.get("end_time")) or _parse_dt(row.get("start_time")) or _parse_dt(row.get("updated_at"))
            if stamp is not None:
                if full_day and day_start <= stamp.astimezone(tzi) < day_end:
                    ended_today.append((stamp, row))
                recent.append((stamp, row))

    if not (live or today or upcoming or recent or ended_today):
        return ""

    tz_label = tz if tz and _tzinfo(tz) is not timezone.utc else (tz or "UTC")
    lines = [f'<schedule tz="{tz_label}" now="{moment.strftime("%a %Y-%m-%d %H:%M")}">']
    if live:
        lines.append("live:")
        lines += [_line(r, tzi=tzi, kind="live") for r in live[:MAX_LIVE]]
    if today:
        today.sort(key=lambda p: p[0])
        cap = MAX_TODAY_FULL if full_day else MAX_TODAY
        lines.append("today:")
        lines += [_line(r, tzi=tzi, kind="today") for _, r in today[:cap]]
    if full_day and ended_today:
        ended_today.sort(key=lambda p: p[0])
        lines.append("ended today:")
        lines += [_line(r, tzi=tzi, kind="ended") for _, r in ended_today[:MAX_TODAY_FULL]]
    if upcoming:
        upcoming.sort(key=lambda p: (p[0] is None, p[0] or datetime.max.replace(tzinfo=timezone.utc)))
        lines.append("upcoming:")
        lines += [_line(r, tzi=tzi, kind="upcoming") for _, r in upcoming[:MAX_UPCOMING]]
    if recent:
        recent.sort(key=lambda p: p[0], reverse=True)
        lines.append("recent:")
        lines += [_line(r, tzi=tzi, kind="recent") for _, r in recent[:MAX_RECENT]]
    lines.append("</schedule>")
    block = "\n".join(lines)
    if len(block) > DIGEST_CHAR_CAP:  # trim whole lines from the tail, keep the closing tag
        kept = []
        size = len("</schedule>") + 1
        for ln in lines[:-1]:
            if size + len(ln) + 1 > DIGEST_CHAR_CAP:
                break
            kept.append(ln)
            size += len(ln) + 1
        block = "\n".join(kept + ["</schedule>"])
    return block + "\n\n"


def find_row(rows: "list[dict]", *, meeting_id=None, platform: "str | None" = None,
             native_id: "str | None" = None) -> "Optional[dict]":
    """Locate the focused meeting's SERVER row (for the focus-enrichment fix): by row id first,
    else by (platform, native) among non-terminal rows — mirrors the meetings-domain identity."""
    if meeting_id is not None:
        try:
            mid = int(meeting_id)
        except (TypeError, ValueError):
            mid = None
        if mid is not None:
            for r in rows:
                if r.get("id") == mid:
                    return r
    if native_id:
        candidates = [r for r in rows
                      if str(r.get("native_meeting_id") or "") == str(native_id)
                      and (not platform or str(r.get("platform") or "") == str(platform))]
        if candidates:
            active = [r for r in candidates if str(r.get("status")) not in _PAST]
            return (active or candidates)[0]
    return None
