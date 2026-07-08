"""admin_panel.py — read-only infra + meeting-pipeline introspection for the hidden admin panel.

The terminal's server-side ``/api/admin/*`` routes call agent-api's ``GET /api/admin/overview``
(gated by ``X-Internal-Secret``, the same internal-tier edge as the admin-api mirror). agent-api
is the ONE aggregation point because it already holds both seams: the runtime kernel URL
(``settings.runtime_api_url`` — every agent-worker AND meeting-bot container is a runtime.v1
workload) and the shared redis (``settings.redis_url`` — the live pipeline carriers).

Everything here is OBSERVATION ONLY: no spawn/stop/delete, no writes to redis. Partial failure is
typed, not silent (P18): an unreachable kernel yields ``workloads_error`` alongside whatever the
redis sweep produced, so the panel degrades a section instead of blanking.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

# Meeting bots are spawned as ``mtg-{meeting_row_id}-{connection_id[:8]}`` (bot_spawn/service.py);
# agent workers as ``agent-…`` (units.dispatch_id). The workload id is the correlation key — the
# runtime.v1 WorkloadStatus carries no labels/env.
_BOT_ID = re.compile(r"^mtg-(?P<meeting>.+)-(?P<conn>[0-9a-zA-Z]{1,8})$")

# The meetings-domain carrier keys (single shared redis). Kept in sync with
# collector/db_writer.py (proc_stream_key / ACTIVE_MEETINGS_KEY) and worker/meeting.py.
ACTIVE_MEETINGS_KEY = "active_meetings"


def classify_workload(status: dict) -> dict:
    """Stamp a runtime.v1 WorkloadStatus with ``kind`` (bot | agent-worker | other) and, for a
    bot, the ``meeting_id`` parsed out of the ``mtg-{id}-{conn}`` workload id."""
    out = dict(status)
    wid = str(status.get("workloadId") or "")
    if wid.startswith("mtg-"):
        out["kind"] = "bot"
        m = _BOT_ID.match(wid)
        out["meeting_id"] = m.group("meeting") if m else wid[len("mtg-"):]
    elif wid.startswith("agent-"):
        out["kind"] = "agent-worker"
    else:
        out["kind"] = "other"
    return out


def fetch_workloads(runtime_api_url: str, *, timeout: float = 5.0) -> list[dict]:
    """``GET {runtime}/workloads`` — every managed container (agent workers + meeting bots) with
    state/ports/exit info. Raises on transport errors; the route types them into the response."""
    req = urllib.request.Request(f"{runtime_api_url.rstrip('/')}/workloads", method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — internal service URL from Settings
        rows = json.loads(r.read())
    return [classify_workload(s) for s in rows if isinstance(s, dict)]


def _stream_stat(r, key: str) -> dict:
    """XLEN + last entry id of a stream, WRONGTYPE/missing-safe (``{}`` when not a stream)."""
    try:
        length = r.xlen(key)
    except Exception:  # noqa: BLE001 — missing key or a non-stream type: report nothing, don't crash the sweep
        return {}
    last_id = None
    try:
        tail = r.xrevrange(key, count=1)
        if tail:
            last_id = tail[0][0]
    except Exception:  # noqa: BLE001
        pass
    return {"len": length, "last_id": last_id}


def pipeline_snapshot(r, live_meetings: list[dict] | None = None) -> list[dict]:
    """Per-meeting pipeline state, one row per meeting id discovered from ANY carrier: the
    processed-notes stream/flag/cursor (``proc:meeting:*``), the transcript stream
    (``tc:meeting:*``), the db-writer's discovery set (``active_meetings``), and the live
    registry. Non-numeric (native-keyed) proc streams surface too — those are exactly the
    S2 keying bug the panel exists to make visible."""
    live_by_id: dict[str, dict] = {}
    for m in live_meetings or []:
        for key_field in ("numeric_meeting_id", "meeting_id", "native_id"):
            v = m.get(key_field)
            if v:
                live_by_id.setdefault(str(v), m)

    ids: set[str] = set()
    for pattern, strip_suffixes in (("proc:meeting:*", (":on", ":cursor")), ("tc:meeting:*", ())):
        try:
            for raw in r.scan_iter(match=pattern, count=200):
                key = raw if isinstance(raw, str) else raw.decode()
                for suffix in strip_suffixes:
                    if key.endswith(suffix):
                        key = key[: -len(suffix)]
                        break
                ids.add(key.split(":", 2)[2])
        except Exception:  # noqa: BLE001 — a failed scan degrades discovery, not the endpoint
            pass
    active: set[str] = set()
    try:
        active = {m if isinstance(m, str) else m.decode() for m in (r.smembers(ACTIVE_MEETINGS_KEY) or set())}
    except Exception:  # noqa: BLE001
        pass
    ids |= active | set(live_by_id)

    rows: list[dict] = []
    for mid in sorted(ids):
        row: dict = {
            "meeting_id": mid,
            "row_keyed": mid.isdigit(),  # False = a native-keyed carrier the db-writer never drains (S2)
            "in_active_meetings": mid in active,
        }
        try:
            row["processing_on"] = bool(r.get(f"proc:meeting:{mid}:on"))
            row["copilot_cursor"] = r.get(f"proc:meeting:{mid}:cursor")
        except Exception:  # noqa: BLE001
            pass
        proc = _stream_stat(r, f"proc:meeting:{mid}")
        tc = _stream_stat(r, f"tc:meeting:{mid}")
        if proc:
            row["proc_stream"] = proc
        if tc:
            row["transcript_stream"] = tc
        live_row = live_by_id.get(mid)
        if live_row:
            row["live"] = {k: live_row.get(k) for k in
                           ("native_id", "platform", "title", "status", "unit_id", "numeric_meeting_id")
                           if live_row.get(k) is not None}
        rows.append(row)
    return rows
