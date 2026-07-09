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
import os
import re
import time
import urllib.error
import urllib.request

# Meeting bots are spawned as ``mtg-{meeting_row_id}-{connection_id[:8]}`` (bot_spawn/service.py);
# agent workers as ``agent-…`` (units.dispatch_id). The workload id is the correlation key — the
# runtime.v1 WorkloadStatus carries no labels/env.
_BOT_ID = re.compile(r"^mtg-(?P<meeting>.+)-(?P<conn>[0-9a-zA-Z]{1,8})$")

# The meetings-domain carrier keys (single shared redis). Kept in sync with
# collector/db_writer.py (proc_stream_key / ACTIVE_MEETINGS_KEY) and worker/meeting.py.
ACTIVE_MEETINGS_KEY = "active_meetings"
# ADR-0027 Train 2: meetings finalized but not yet drained through the worker's view_end marker
# (zset: member = meeting row id, score = drain deadline epoch). Healthy = members clear within
# ~2 db-writer ticks of a stop; a member past its deadline = the run-46 S1 signature, live.
PROCESSED_PENDING_KEY = "processed_pending"


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
    """XLEN + last entry id/type of a stream, WRONGTYPE/missing-safe (``{}`` when not a stream).
    ``last_type`` surfaces the entry's ``type`` field when stamped — a proc stream whose tail is
    the worker's ``view_end`` marker completed cleanly (ADR-0027 Train 2)."""
    try:
        length = r.xlen(key)
    except Exception:  # noqa: BLE001 — missing key or a non-stream type: report nothing, don't crash the sweep
        return {}
    out: dict = {"len": length, "last_id": None}
    try:
        tail = r.xrevrange(key, count=1)
        if tail:
            entry_id, fields = tail[0]
            out["last_id"] = entry_id
            decoded = {(k if isinstance(k, str) else k.decode()): (v if isinstance(v, str) else v.decode())
                       for k, v in (fields or {}).items()}
            if decoded.get("type"):
                out["last_type"] = decoded["type"]
    except Exception:  # noqa: BLE001
        pass
    return out


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
    pending: dict[str, float] = {}
    try:
        for member, score in r.zrange(PROCESSED_PENDING_KEY, 0, -1, withscores=True) or []:
            pending[member if isinstance(member, str) else member.decode()] = float(score)
    except Exception:  # noqa: BLE001 — pre-Train-2 stacks have no zset; the column just stays empty
        pass
    ids |= active | set(live_by_id) | set(pending)

    # A live-registry row is keyed under BOTH its numeric row id and its native aliases. When the
    # numeric row exists, an alias id with no real carriers of its own is registry echo, not an S2
    # native-keyed stream — listing it would wear the danger chip for nothing (seen live: meeting
    # 53's `byr-sqng-mek` alias with empty streams). Real S2 rows keep showing: they have content.
    aliased: set[str] = set()
    for m in live_meetings or []:
        if m.get("numeric_meeting_id"):
            for key_field in ("meeting_id", "native_id", "session_uid"):
                v = m.get(key_field)
                if v and str(v) != str(m["numeric_meeting_id"]):
                    aliased.add(str(v))

    rows: list[dict] = []
    for mid in sorted(ids):
        row: dict = {
            "meeting_id": mid,
            "row_keyed": mid.isdigit(),  # False = a native-keyed carrier the db-writer never drains (S2)
            "in_active_meetings": mid in active,
        }
        if mid in pending:
            row["pending_drain"] = {"deadline": pending[mid], "overdue": pending[mid] < time.time()}
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
        if mid in aliased and not (
            proc.get("len") or tc.get("len") or row.get("processing_on") or row.get("copilot_cursor")
            or mid in active or mid in pending
        ):
            continue  # registry echo of a numeric row — no carriers of its own, nothing to report
        live_row = live_by_id.get(mid)
        if live_row:
            # last_seen: stamped by _LiveMeetings.add() since b07ca3ee (P21 silence-demotion) —
            # lets the panel show registry freshness alongside the (now self-demoting) status.
            row["live"] = {k: live_row.get(k) for k in
                           ("native_id", "platform", "title", "status", "unit_id",
                            "numeric_meeting_id", "last_seen")
                           if live_row.get(k) is not None}
        rows.append(row)
    return rows


# ── the transcription-pipeline golden smoke probe ─────────────────────────────────────────────────
# Five stages walk the golden path a live transcript travels: gateway → meeting-api → runtime
# (+ bot_spawn capability) → the redis carriers (a real write/read round-trip on scratch keys) →
# the transcript relay (native resolve + segment recency). Each stage is a typed result — never a
# swallowed exception (P18). "Quiet" is distinguished from "broken": no recent segments is a WARN
# when nothing is live, a FAIL when a live meeting should be producing them.

_PROBE_SCRATCH = "admin:probe:scratch"
_PROBE_STREAM = "admin:probe:stream"
SEGMENT_FRESH_SEC = 120


def _http_health(url: str, *, timeout: float = 5.0) -> tuple[int, dict]:
    """GET a service /health → (latency_ms, parsed body). Raises on transport/HTTP errors."""
    t0 = time.monotonic()
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — internal service URLs
        body = json.loads(r.read() or b"{}")
    return int((time.monotonic() - t0) * 1000), body if isinstance(body, dict) else {}


def _stage(sid: str, label: str, status: str, latency_ms: int | None = None, detail: str | None = None) -> dict:
    out: dict = {"id": sid, "label": label, "status": status}
    if latency_ms is not None:
        out["latency_ms"] = latency_ms
    if detail:
        out["detail"] = detail
    return out


def _health_stage(sid: str, label: str, url: str, http_health) -> dict:
    try:
        ms, body = http_health(url)
    except Exception as e:  # noqa: BLE001 — typed stage failure, not a 500
        return _stage(sid, label, "fail", detail=f"{type(e).__name__}: {e}")
    status = "pass" if body.get("status") == "ok" else "warn"
    return _stage(sid, label, status, ms, None if status == "pass" else f"status={body.get('status')}")


def run_probe(settings, r, live_meetings: list[dict] | None = None, *,
              relay_health: dict | None = None, http_health=_http_health,
              workloads: list[dict] | None = None) -> dict:
    """Run the golden smoke probe. ``r`` may be None (redis stage reports fail).
    Overall: fail > warn > pass across stages. ``workloads`` (when known) cross-checks the
    in-memory live registry: a registry entry can outlive its meeting (stale live flag — seen
    live on meeting 53), so quiet-with-"live" only FAILS when a bot container actually runs."""
    t0 = time.monotonic()
    stages: list[dict] = []

    gw = os.environ.get("VEXA_GATEWAY_URL", "http://gateway:8000").rstrip("/")
    stages.append(_health_stage("gateway", "gateway", f"{gw}/health", http_health))
    stages.append(_health_stage("meeting-api", "meeting-api",
                                f"{settings.meeting_api_url.rstrip('/')}/health", http_health))

    try:
        ms, body = http_health(f"{settings.runtime_api_url.rstrip('/')}/health")
        caps = body.get("capabilities") or {}
        bot = caps.get("bot_spawn") if isinstance(caps, dict) else None
        bot_state = (bot or {}).get("status") if isinstance(bot, dict) else bot
        if body.get("status") != "ok":
            stages.append(_stage("runtime", "runtime + bot_spawn", "warn", ms, f"status={body.get('status')}"))
        elif bot_state not in (None, "ok", "configured", True):
            stages.append(_stage("runtime", "runtime + bot_spawn", "warn", ms, f"bot_spawn={bot_state}"))
        else:
            stages.append(_stage("runtime", "runtime + bot_spawn", "pass", ms))
    except Exception as e:  # noqa: BLE001
        stages.append(_stage("runtime", "runtime + bot_spawn", "fail", detail=f"{type(e).__name__}: {e}"))

    if r is None:
        stages.append(_stage("redis", "redis carriers", "fail", detail="no redis configured"))
    else:
        try:
            rt0 = time.monotonic()
            r.set(_PROBE_SCRATCH, "1", ex=30)
            ok = r.get(_PROBE_SCRATCH) is not None
            r.delete(_PROBE_SCRATCH)
            r.xadd(_PROBE_STREAM, {"probe": "1"})
            r.xlen(_PROBE_STREAM)
            r.delete(_PROBE_STREAM)
            ms = int((time.monotonic() - rt0) * 1000)
            stages.append(_stage("redis", "redis carriers", "pass" if ok else "fail", ms,
                                 None if ok else "scratch read-back missing"))
        except Exception as e:  # noqa: BLE001
            stages.append(_stage("redis", "redis carriers", "fail", detail=f"{type(e).__name__}: {e}"))

    rh = relay_health or {}
    resolve = rh.get("native_resolve") or {}
    ingest = rh.get("ingest") or {}
    registry_live = any(m.get("status") == "live" for m in (live_meetings or []))
    bot_running = (None if workloads is None else
                   any(w.get("kind") == "bot" and w.get("state") == "running" for w in workloads))
    live_now = registry_live and bot_running is not False
    stale_live = registry_live and bot_running is False
    if resolve and not resolve.get("ok", True):
        stages.append(_stage("relay", "transcript relay", "fail",
                             detail=f"native resolve: {resolve.get('kind')}: {resolve.get('detail')}"))
    else:
        last_at = ingest.get("last_segment_at")
        age = (time.time() - last_at) if last_at else None
        if age is not None and age <= SEGMENT_FRESH_SEC:
            stages.append(_stage("relay", "transcript relay", "pass", detail=f"segments flowing ({int(age)}s ago)"))
        else:
            quiet = f"no segments for {int(age / 60)}m" if age is not None else "no segments seen since start"
            # quiet ≠ broken: with a live meeting the silence is a fault; idle it's just idle.
            # A registry-live entry with NO running bot is a stale flag, not a live meeting.
            if live_now:
                suffix = " with a LIVE meeting"
            elif stale_live:
                suffix = " (registry says live but no bot container runs — stale live flag)"
            else:
                suffix = " (nothing live)"
            stages.append(_stage("relay", "transcript relay", "fail" if live_now else "warn", detail=quiet + suffix))

    worst = "pass"
    if any(s["status"] == "warn" for s in stages):
        worst = "warn"
    if any(s["status"] == "fail" for s in stages):
        worst = "fail"
    return {"status": worst, "stages": stages,
            "duration_ms": int((time.monotonic() - t0) * 1000), "at": time.time()}
