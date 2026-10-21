"""How did this bot run END, in the one grading customers and analytics read: ``outcome_class``.

A terminal bot run is never just "failed". It is exactly one of five classes:

    ours              a product or infra fault. THE ONLY CLASS THAT IS A FAILURE.
    host_or_platform  not admitted, rejected, evicted, tenant/host policy, URL kinds the bot rejects.
    user              stopped by the caller, malformed request, dead link, deleted by the user.
    normal            the meeting ran (delivered), ended, everyone left, alone, the time cap.
    unknown           unclassifiable from the recorded evidence. Driven to zero, never shown as ours.

plus ``pending`` for a row that is not terminal yet. ``outcome_reason`` is a stable slug inside the
class (``not_admitted``, ``rejected_by_host``, ``tenant_requires_sign_in``, ``delivered_stopped``…).

Why a separate axis from ``completion_reason``: ``completion_reason`` is what the BOT reported, and the
bot reports a Google Meet lobby wait that ran out as ``join_failure`` at ``failure_stage
awaiting_admission`` (#1655). That row is the host not letting us in, not a fault of ours. This module
fixes the LABEL at the outcome level; the bot's own admission reporting is a separate fix.

THE BLOCK BETWEEN THE PORT MARKERS IS A VERBATIM PORT of the canonical classifier the business keeps
(``biz/scripts/bot_outcomes.py``): same class names, same reason slugs, same rule order, same CASES
table. Change the rules there first and re-port; do not fork them here. Below the port sit the
product adapters: ``outcome_for_meeting`` (one meetings row → Outcome) and ``stamp_outcome`` (write
``outcome_class``/``outcome_reason`` into a terminal row's ``data``), used by the two DB writers
(``update_meeting_status``, ``fail_meeting``) and the one-off backfill.

Where it is written: at the DB write of a terminal status (``update_meeting_status``, ``fail_meeting``),
not inside the in-memory FSM sink. The rules need the row's segment count, its ``end_time`` and the
merged ``status_transition`` trail (active minutes from the first ``active`` transition, the last
pre-terminal stage, whether a ``stopping`` was ever requested); the repo is where all three meet.
Every terminal write recomputes it, so the persisted value is the one with the most evidence.
Missing evidence never upgrades a run to ``ours``.
"""

# ── BEGIN PORT of biz/scripts/bot_outcomes.py (classifier core, verbatim) ──
from __future__ import annotations

import datetime as dt
import re
import sys
from typing import Any, Mapping, NamedTuple, Optional

CLASSES = ("ours", "host_or_platform", "user", "normal", "unknown")
WATCHED = frozenset({"ours"})          # only this class is "lost through our fault"
PENDING = "pending"                    # not terminal yet; excluded from every count

TERMINAL = {"completed", "failed", "exited"}
IN_FLIGHT = {"requested", "joining", "awaiting_admission", "active", "needs_help",
             "needs_human_help", "stopping"}
STALE_AFTER = dt.timedelta(hours=24)   # a non-terminal row older than this never got closed
EMPTY_MIN_MINUTES = 3.0                # in the room this long with zero segments = empty transcript
# Before this date the product did not record why a pre-join run ended (join_failure canonicalization,
# vexa 9bcc31a6). A run of that era with no recorded evidence is `unknown/uninstrumented_era`; from this
# date on, `unknown` must be zero.
INSTRUMENTED_FROM = dt.datetime(2026, 6, 6, tzinfo=dt.timezone.utc)
# Before this date no status_transition was recorded either, so "no callback ever reached us" is not
# evidence of anything. From it on, a failed run with no stage and no reason did die unreported.
TRANSITIONS_FROM = dt.datetime(2025, 10, 1, tzinfo=dt.timezone.utc)


class Outcome(NamedTuple):
    cls: str
    reason: str

    @property
    def watched(self) -> bool:
        return self.cls in WATCHED


# Detail-text rules, first match wins. Applied to the lowercased concatenation of data.reason,
# data.failure_reason, data.join_evidence.detail, data.last_error.reason, data.error_details and
# the meeting URL. Literals are the bot's own strings (core/meetings/modules/join/src/**).
DETAIL_RULES = (
    # host / platform policy
    (r"teams_auth_redirect|anonymous_join_redirected_to_sign_in", "host_or_platform", "tenant_requires_sign_in"),
    (r"auth_required", "host_or_platform", "host_requires_signed_in_users"),
    (r"zoom_requires_rtms|blocks automated browser joins", "host_or_platform", "platform_blocks_bots"),
    (r"recaptcha|captcha|unusual traffic|bot.detection", "host_or_platform", "platform_captcha"),
    (r"host did not start the meeting", "host_or_platform", "host_never_started"),
    # URL kinds the bot rejects (a supported-surface limit, not a malfunction)
    (r"linuxfoundation\.org", "host_or_platform", "unsupported_url_lfx"),
    (r"invalid zoom meeting url.*/w/", "host_or_platform", "unsupported_url_zoom_webinar"),
    (r"invalid zoom meeting url.*/s/", "host_or_platform", "unsupported_url_zoom_start_link"),
    (r"invalid zoom meeting url|cannot extract meeting id from zoom url", "host_or_platform", "unsupported_url_zoom"),
    (r"invalid jitsi meeting url|cannot extract room", "host_or_platform", "unsupported_url_jitsi"),
    (r"cannot infer platform|unsupported platform", "host_or_platform", "unsupported_url"),
    # caller input
    (r"could not locate.*workspace\.google\.com", "user", "dead_or_expired_link"),
    (r"cannot navigate to invalid url", "user", "malformed_url"),
    (r"passcode_required", "user", "missing_passcode"),
    (r"teams_off_meeting_origin", "user", "not_a_meeting_url"),
    (r"stopped by the user|cancelled by the user|withdrew the join request", "user", "stopped_before_admission"),
    # host never let it in (incl. the #1655 mislabel: a full lobby wait written as join_failure)
    (r"rejected by meeting admin|was rejected from the", "host_or_platform", "rejected_by_host"),
    (r"waiting room after timeout|host did not admit|not admitted within|not admitted into"
     r"|while awaiting admission \(never admitted\)", "host_or_platform", "not_admitted"),
    (r"google meet meeting . no meeting indicators found within timeout", "host_or_platform", "not_admitted_1655"),
    # ours: join automation, page, browser, infra
    (r"could not locate|could not determine teams admission status|input-for-name", "ours", "join_automation"),
    (r"no meeting indicators found after polling", "ours", "join_automation"),
    (r"page\.goto: timeout|net::err|navigation failed|target page, context or browser has been closed"
     r"|target closed|page closed", "ours", "page_or_browser"),
    (r"control_plane_unreachable", "ours", "control_plane_unreachable"),
    (r"workload dead on spawn|before the bot reported \(never started\)", "ours", "bot_start_failed"),
    (r"untracked for .*presumed lost", "ours", "workload_lost"),
    (r"workload stopped", "ours", "infra_stopped_bot"),
    # legacy era (before the 2026-06-06 canonicalization), from status_transition error_details
    (r"callback attempts failed|callback failed: server rejected", "ours", "status_callback_failed"),
    (r"waiting for locator.*(ask to join|your name|join now)", "ours", "join_automation"),
    (r"no active media elements found", "ours", "no_media_capture"),
    (r"execution context was destroyed|element is not attached|page\.fill: timeout|page\.screenshot: timeout"
     r"|launchpersistentcontext|trustedscript", "ours", "page_or_browser"),
    (r"zoom_client_id", "ours", "config_missing"),
    (r"post_join_setup_error", "ours", "died_in_meeting"),
)
_DETAIL_RX = tuple((re.compile(p), c, r) for p, c, r in DETAIL_RULES)


def _s(v: Any) -> str:
    return str(v or "").strip().lower()


def _when(v: Any) -> Optional[dt.datetime]:
    if v is None or v == "":
        return None
    if isinstance(v, dt.datetime):
        return v if v.tzinfo else v.replace(tzinfo=dt.timezone.utc)
    s = str(v).replace("Z", "+00:00").replace(" ", "T", 1)
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def _detail_match(detail: str) -> Optional[Outcome]:
    for rx, cls, reason in _DETAIL_RX:
        if rx.search(detail):
            return Outcome(cls, reason)
    return None


def classify(row: Mapping[str, Any], now: Optional[dt.datetime] = None) -> Outcome:
    """Classify one meeting row. Keys (all optional except status):

    status, completion_reason, failure_stage, detail, join_evidence_reason, platform,
    segments (int; None = unknown), transcribe_enabled (bool; None = unknown),
    active_minutes (float; time between reaching active and the end; None = unknown),
    stt_fault (truthy), deleted (truthy: user deleted/redacted the artifacts), created_at,
    speech_evidence (truthy: speaker_events or >1 participant recorded),
    last_stage (the last pre-terminal status in data.status_transition), reached_active (bool),
    stop_requested (truthy).
    """
    st = _s(row.get("status"))
    cr = _s(row.get("completion_reason"))
    fs = _s(row.get("failure_stage"))
    ev = _s(row.get("join_evidence_reason"))
    detail = _s(row.get("detail"))
    segs = row.get("segments")
    segs = None if segs in (None, "") else int(segs)
    te = row.get("transcribe_enabled")
    te = None if te in (None, "") else (te if isinstance(te, bool) else _s(te) in ("true", "1", "t"))
    mins = row.get("active_minutes")
    mins = None if mins in (None, "") else float(mins)
    last = _s(row.get("last_stage"))
    fs = fs or last                                   # legacy rows: stage only in status_transition
    ra = row.get("reached_active")
    active = (bool(ra) if ra is not None else mins is not None) or fs == "active" or last == "active"
    stopreq = _s(row.get("stop_requested")) in ("true", "1", "t")
    created = _when(row.get("created_at"))
    legacy = bool(created and created < INSTRUMENTED_FROM)          # completion reasons not canonical
    pre_tr = bool(created and created < TRANSITIONS_FROM)           # no lifecycle record at all

    # 0. not terminal
    if st not in TERMINAL:
        if st == "scheduled":
            return Outcome(PENDING, "scheduled")
        if st == "idle":
            return Outcome(PENDING, "idle_never_dispatched")
        now = now or dt.datetime.now(dt.timezone.utc)
        if st in IN_FLIGHT and created and now - created > STALE_AFTER:
            return Outcome("ours", "never_terminated")        # lifecycle never closed the row
        if st in IN_FLIGHT:
            return Outcome(PENDING, st)
        return Outcome("unknown", f"status:{st or 'none'}")

    # 1. caller errors that are explicit in the reason enum
    if cr == "validation_error":
        return Outcome("user", "malformed_request")

    # 2. completed (incl. legacy 'exited'): the bot got in, or the old era wrote lobby outcomes here
    if st in ("completed", "exited"):
        if cr == "awaiting_admission_rejected":
            return Outcome("host_or_platform", "rejected_by_host")
        if cr == "awaiting_admission_timeout":
            return Outcome("host_or_platform", "not_admitted")
        if cr == "evicted":
            return Outcome("host_or_platform", "evicted_by_host")
        if segs and segs > 0:
            if row.get("stt_fault"):
                return Outcome("normal", "delivered_with_stt_gaps")  # transcript delivered, some chunks lost
            return Outcome("normal", f"delivered_{cr or 'ended'}")
        if row.get("stt_fault"):
            return Outcome("ours", "stt_fault")                       # STT refused and nothing came back
        if te is False:
            return Outcome("normal", "delivered_no_transcription_requested")
        if row.get("deleted"):
            return Outcome("user", "deleted_by_user")
        if cr == "startup_alone":
            return Outcome("normal", "alone_at_startup")
        if segs == 0 and not active:
            # the bot never got in; old eras wrote these as completed/stopped
            if stopreq:
                return Outcome("user", "stopped_before_admission")
            if last == "awaiting_admission":
                return Outcome("host_or_platform", "not_admitted")   # e.g. the old 5-min lobby budget
            if legacy:
                return Outcome("unknown", "uninstrumented_era")
            if last == "needs_human_help":
                return Outcome("user", "needs_help_unanswered")       # the bot asked the caller to act
            return Outcome("ours", "join_ended_unreported")
        if segs == 0 and mins is not None:
            if mins >= EMPTY_MIN_MINUTES:
                # in the room ≥3 min, nothing back. Ours when someone was demonstrably there: the caller
                # stopped it (they expected a transcript) or participants/speakers were recorded.
                if cr == "stopped" or row.get("speech_evidence"):
                    return Outcome("ours", "empty_transcript")
                # left on the silence/alone timer with no sign of anyone: an empty room or a dead capture
                # chain, indistinguishable today (vexa#850). Kept as its own reason so a spike shows.
                return Outcome("normal", "no_speech_heard")
            if cr == "left_alone":
                return Outcome("normal", "everyone_left_early")
            if cr == "max_bot_time_exceeded":
                return Outcome("normal", "max_bot_time")
            return Outcome("user", "stopped_early")
        if segs == 0 and cr == "left_alone":
            return Outcome("normal", "everyone_left_early")
        if segs == 0 and active:
            return Outcome("ours" if row.get("speech_evidence") else "normal",
                           "empty_transcript" if row.get("speech_evidence") else "no_speech_heard")
        return Outcome("unknown", f"completed_no_evidence:{cr or 'none'}")

    # 3. failed, after the bot was inside the meeting
    if fs == "active" or (active and not stopreq):
        if cr == "evicted":
            return Outcome("host_or_platform", "evicted_by_host")
        if cr == "stopped":
            return Outcome("user", "stopped")
        if cr == "left_alone":
            return Outcome("normal", "everyone_left")
        if cr == "max_bot_time_exceeded":
            return Outcome("normal", "max_bot_time")
        if cr == "stopped_with_no_audio":
            return Outcome("ours", "no_audio_captured")
        return Outcome("ours", "died_in_meeting")

    # 4. failed before active: a user stop wins; then explicit detail (more specific than the label)
    if stopreq or _s(row.get("bec_cr")) == "stopped":
        return Outcome("user", "stopped_before_admission")
    hit = _detail_match(detail)
    if hit:
        return hit

    if cr in ("stopped", "stopped_before_admission") or ev == "stopped_while_joining":
        return Outcome("user", "stopped_before_admission")
    if cr == "awaiting_admission_rejected":
        return Outcome("host_or_platform", "rejected_by_host")
    if cr == "awaiting_admission_timeout" or ev == "awaiting_admission_timeout":
        return Outcome("host_or_platform", "not_admitted")
    if cr == "evicted":
        return Outcome("host_or_platform", "evicted_by_host")
    if cr == "left_alone":
        return Outcome("host_or_platform", "meeting_ended_before_admission")
    if cr == "max_bot_time_exceeded":
        return Outcome("normal", "max_bot_time")
    if cr == "auth_session_missing":
        return Outcome("host_or_platform", "host_requires_signed_in_users")
    if cr in ("start_failed", "orphan_browser_session_cleanup"):
        return Outcome("ours", "bot_start_failed")
    if cr == "stopped_with_no_audio":
        return Outcome("ours", "no_audio_captured")
    if cr in ("join_failure", "", "join_meeting_error") and fs == "awaiting_admission":
        return Outcome("host_or_platform", "not_admitted_1655")   # knocked, never let in (#1655)
    if pre_tr:
        return Outcome("unknown", "uninstrumented_era")
    if fs in ("needs_human_help", "needs_help"):
        return Outcome("user", "needs_help_unanswered")
    if not fs and cr == "":
        return Outcome("ours", "bot_never_reported")              # no callback ever reached us
    if fs in ("joining", "requested") and "self_initiated_leave" in detail:
        return Outcome("ours", "join_ended_unreported")
    if fs in ("joining", "requested") and cr in ("", "join_failure"):
        return Outcome("ours", "exited_without_reason")          # bot died while joining, no report
    return Outcome("unknown", f"{cr or 'no_reason'}@{fs or 'no_stage'}")


# ── helpers for callers ───────────────────────────────────────────────────────

def row_from_db(r: Mapping[str, Any]) -> dict:
    """Map a meetings row (+ `segments` count, `data` JSONB as dict) to classify() input."""
    d = r.get("data") or {}
    je = d.get("join_evidence") or {}
    le = d.get("last_error") or {}
    tr = d.get("status_transition") if isinstance(d.get("status_transition"), list) else []
    tr = sorted((e for e in tr if isinstance(e, dict)), key=lambda e: str(e.get("timestamp") or ""))
    term = [e for e in tr if e.get("to") in ("failed", "completed")]
    last_term = (f"{term[-1].get('reason') or ''} {term[-1].get('error_details') or ''}") if term else None
    detail = " | ".join(str(x) for x in (d.get("reason"), d.get("failure_reason"), je.get("detail"),
                                          le.get("reason"), str(d.get("error_details") or "")[:300],
                                          last_term, d.get("constructed_meeting_url"),
                                          d.get("meeting_url")) if x)
    pre = [e.get("to") for e in tr if e.get("to") in IN_FLIGHT and e.get("to") != "stopping"]
    stopping_seen = any(e.get("to") == "stopping" for e in tr)      # a stop was requested
    active_at = next((_when(e.get("timestamp")) for e in tr if e.get("to") == "active"), None)
    start, end = active_at, _when(r.get("end_time"))   # start_time is set at creation on legacy rows
    return dict(status=r.get("status"), completion_reason=d.get("completion_reason"),
                failure_stage=d.get("failure_stage"), join_evidence_reason=je.get("reason"),
                detail=detail, platform=r.get("platform"), segments=r.get("segments"),
                transcribe_enabled=d.get("transcribe_enabled"),
                active_minutes=(end - start).total_seconds() / 60 if start and end else None,
                stt_fault=d.get("stt_fault"), deleted=bool(d.get("redacted") or d.get("artifact_deletion")),
                created_at=r.get("created_at"), last_stage=pre[-1] if pre else None,
                reached_active=active_at is not None,
                speech_evidence=bool(d.get("speaker_events")) or len(d.get("participants") or []) > 1, stop_requested=d.get("stop_requested") or stopping_seen,
                bec_cr=(d.get("bot_exit_classification") or {}).get("completion_reason"))


def summarize(outcomes) -> dict:
    """Counts per class over terminal runs, with the one line a founder-facing text may carry."""
    from collections import Counter
    c = Counter(o.cls for o in outcomes if o.cls != PENDING)
    total = sum(c.values())
    return dict(total=total, **{k: c.get(k, 0) for k in CLASSES},
                line=(f"{c.get('ours', 0)} of {total} lost through our fault"
                      f" · not admitted / host / platform {c.get('host_or_platform', 0)}"
                      f" · user {c.get('user', 0)} · normal {c.get('normal', 0)}"
                      f" · unknown {c.get('unknown', 0)}"))


# ── self-test: one case per rule; the SQL view is checked against the same table ──

CASES = [
    (dict(status="failed", completion_reason="join_failure", failure_stage="awaiting_admission",
          detail="Bot failed to join the Google Meet meeting — no meeting indicators found within timeout"),
     ("host_or_platform", "not_admitted_1655")),
    (dict(status="failed", completion_reason="join_failure", failure_stage="awaiting_admission"),
     ("host_or_platform", "not_admitted_1655")),
    (dict(status="failed", completion_reason="awaiting_admission_timeout", failure_stage="awaiting_admission"),
     ("host_or_platform", "not_admitted")),
    (dict(status="failed", completion_reason="awaiting_admission_rejected", failure_stage="joining"),
     ("host_or_platform", "rejected_by_host")),
    (dict(status="failed", completion_reason="join_failure", failure_stage="joining",
          detail="teams_auth_redirect: the anonymous Teams join was redirected"),
     ("host_or_platform", "tenant_requires_sign_in")),
    (dict(status="failed", completion_reason="auth_session_missing", failure_stage="joining",
          detail="[Zoom Web] auth_required: meeting host has restricted entry"),
     ("host_or_platform", "host_requires_signed_in_users")),
    (dict(status="failed", completion_reason="join_failure", failure_stage="joining",
          detail="Error: Invalid Zoom meeting URL: https://x.zoom.us/w/123?tk=a"),
     ("host_or_platform", "unsupported_url_zoom_webinar")),
    (dict(status="failed", completion_reason="join_failure", failure_stage="joining",
          detail="timeout waiting for #input-for-name | https://zoom-lfx.platform.linuxfoundation.org/meeting/9"),
     ("host_or_platform", "unsupported_url_lfx")),
    (dict(status="failed", completion_reason="join_failure", failure_stage="joining",
          detail="Could not locate join button after 30000ms | url=https://workspace.google.com/products/meet/"),
     ("user", "dead_or_expired_link")),
    (dict(status="failed", completion_reason="join_failure", failure_stage="joining",
          detail="Could not locate join button by any locale-agnostic or English selector after 30000ms"),
     ("ours", "join_automation")),
    (dict(status="failed", completion_reason="join_failure", failure_stage="joining",
          detail="page.goto: Timeout 30000ms exceeded"),
     ("ours", "page_or_browser")),
    (dict(status="failed", completion_reason="start_failed", failure_stage="requested",
          detail="workload dead on spawn: OOMKilled"), ("ours", "bot_start_failed")),
    (dict(status="failed", completion_reason="", failure_stage="active"), ("ours", "died_in_meeting")),
    (dict(status="failed", completion_reason="evicted", failure_stage="active"),
     ("host_or_platform", "evicted_by_host")),
    (dict(status="failed", completion_reason="stopped", failure_stage="joining"),
     ("user", "stopped_before_admission")),
    (dict(status="failed", completion_reason="validation_error"), ("user", "malformed_request")),
    (dict(status="completed", completion_reason="stopped", segments=40, active_minutes=30),
     ("normal", "delivered_stopped")),
    (dict(status="completed", completion_reason="left_alone", segments=0, active_minutes=25),
     ("normal", "no_speech_heard")),
    (dict(status="completed", completion_reason="left_alone", segments=0, active_minutes=25, speech_evidence=True),
     ("ours", "empty_transcript")),
    (dict(status="completed", completion_reason="stopped", segments=0, active_minutes=30),
     ("ours", "empty_transcript")),
    (dict(status="completed", completion_reason="stopped", segments=266, active_minutes=57, stt_fault="{}"),
     ("normal", "delivered_with_stt_gaps")),
    (dict(status="completed", completion_reason="stopped", segments=0, active_minutes=1),
     ("user", "stopped_early")),
    (dict(status="completed", completion_reason="stopped", segments=0, active_minutes=20, deleted=True),
     ("user", "deleted_by_user")),
    (dict(status="completed", completion_reason="max_bot_time_exceeded", segments=12),
     ("normal", "delivered_max_bot_time_exceeded")),
    (dict(status="completed", completion_reason="awaiting_admission_timeout"),
     ("host_or_platform", "not_admitted")),
    (dict(status="requested", created_at="2026-04-01T00:00:00Z"), ("ours", "never_terminated")),
    (dict(status="failed", completion_reason="join_failure", failure_stage="joining", created_at="2026-09-01"),
     ("ours", "exited_without_reason")),
    (dict(status="failed", last_stage="joining", created_at="2025-09-01"), ("unknown", "uninstrumented_era")),
    (dict(status="failed", created_at="2026-04-21"), ("ours", "bot_never_reported")),
    (dict(status="failed", last_stage="joining", created_at="2026-06-20",
          detail="All 3 callback attempts failed for joining status change"), ("ours", "status_callback_failed")),
    (dict(status="failed", last_stage="joining", created_at="2026-06-20", stop_requested="true",
          detail="joining callback failed: server rejected after 3 attempts"), ("user", "stopped_before_admission")),
    (dict(status="completed", completion_reason="stopped", segments=0, last_stage="awaiting_admission",
          created_at="2026-02-01"), ("host_or_platform", "not_admitted")),
    (dict(status="failed", last_stage="active", reached_active=True, created_at="2026-07-01"),
     ("ours", "died_in_meeting")),
    (dict(status="idle"), ("pending", "idle_never_dispatched")),
    # the FSM synthesizes "reason: None" when the bot sent none; it must not override the lobby stage
    (dict(status="failed", completion_reason="join_failure", failure_stage="awaiting_admission",
          created_at="2026-10-01", detail="Bot exited with code 1; reason: None"),
     ("host_or_platform", "not_admitted_1655")),
    (dict(status="failed", completion_reason="join_failure", failure_stage="joining",
          created_at="2026-10-01", detail="Bot exited with code 1; reason: None"),
     ("ours", "exited_without_reason")),
    (dict(status="failed", created_at="2026-06-20"), ("ours", "bot_never_reported")),
    (dict(status="failed", last_stage="needs_human_help", created_at="2026-07-01"), ("user", "needs_help_unanswered")),
]


# ── END PORT ──


# ── product adapters ─────────────────────────────────────────────────────────

#: The two keys this module owns in ``meetings.data``.
OUTCOME_KEYS = ("outcome_class", "outcome_reason")


def outcome_for_meeting(
    *,
    status: Any,
    data: Optional[Mapping[str, Any]],
    platform: Any = None,
    start_time: Any = None,
    end_time: Any = None,
    created_at: Any = None,
    segments: Optional[int] = None,
    now: Optional[dt.datetime] = None,
) -> Outcome:
    """Classify one meetings row as meeting-api holds it.

    ``segments`` defaults to ``data.segments_captured`` (the count the repo persists at the terminal
    write, #807); pass the live transcription count when that key is absent. Everything else
    (``status_transition``, ``artifact_deletion``, ``stop_requested``…) is read from ``data`` by the
    ported ``row_from_db``. NEVER raises: an internal fault yields ``unknown``, because this is a
    report about a run that already ended.
    """
    try:
        d = dict(data or {})
        segs = d.get("segments_captured") if segments is None else segments
        row = {
            "status": status, "data": d, "platform": platform,
            "start_time": start_time, "end_time": end_time, "created_at": created_at,
            "segments": segs,
        }
        return classify(row_from_db(row), now=now)
    except Exception:  # noqa: BLE001 — the grading must never break a terminal write
        return Outcome("unknown", "classifier_error")


def stamp_outcome(data: dict, **row: Any) -> dict:
    """Write ``outcome_class``/``outcome_reason`` into ``data`` (in place) for a TERMINAL row.

    ``row`` is ``outcome_for_meeting``'s keyword arguments minus ``data``. A non-terminal status is a
    no-op (the outcome is only ever written at the terminal transition). Returns ``data``.
    """
    if _s(row.get("status")) not in TERMINAL:
        return data
    o = outcome_for_meeting(data=data, **row)
    data["outcome_class"] = o.cls
    data["outcome_reason"] = o.reason
    return data
