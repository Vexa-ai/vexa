"""Meeting-domain steps — gateway HTTP, no imports of meeting code. Stateless: meeting identity
travels in step results (receipts), never in process memory."""
from __future__ import annotations

import subprocess
import time

from flows import Done, StepCtx, StepError, Wait

from .common import FIXTURE_TRANSCRIPT, GATEWAY, http, user_api_key

FIXTURE_LINES = [
    (0.0, 6.0, "Anna", "Alright, quick sync on the pilot. Two decisions today."),
    (6.5, 14.0, "Ben", "First: we go with the phased rollout — pilot group is treasury, four weeks."),
    (14.5, 22.0, "Anna", "Agreed. Decision one: phased rollout, treasury first, four weeks starting Monday."),
    (22.5, 30.0, "Ben", "Second: Anna owns the security review. I need it before the pilot starts."),
    (30.5, 38.0, "Anna", "I commit to the security review by Friday. Send me the checklist today."),
    (38.5, 44.0, "Ben", "Will do — checklist to you by end of day. That's a commitment."),
    (44.5, 50.0, "Anna", "Open question for next time: do we invite risk and compliance to the pilot?"),
]


def await_start(ctx: StepCtx):
    """Sleep until start − 2 min — time is a column (Wait until), zero cost while parked.
    Reads: refs.start."""
    if ctx.clock_now < ctx.refs["start"] - 120:
        return Wait(until=ctx.refs["start"] - 120)
    return Done({})


#: The platforms this lane dispatches at — the constructible set from the product's
#: ``bot_spawn/service.py::_URL_TEMPLATES`` (google_meet, teams). Anything else joins only on a
#: caller-vouched ``meeting_url``, and an unattended invite lane cannot vouch for a link that
#: arrived in someone else's calendar entry. Mirrors flows_integrations.meeting_link, which is
#: the intake half of the same rule (integrations are a separate process; no import either way).
SUPPORTED_PLATFORMS = ("google_meet", "teams")


def check_platform(ctx: StepCtx):
    """THE HONEST REFUSAL. Fail TYPED before anything irreversible when the invite's platform is
    one we cannot join, and tell the organizer which platform we saw.

    Runs BEFORE rsvp_accept on purpose: accepting in the organizer's calendar is a promise to
    show up, and a bank reading "Vexa — Yes" in the guest list of a meeting no bot will ever
    join is worse than a plain no. One email (the existing ack idiom, not a new channel), then
    a non-retryable StepError so the reaction lands in `failed` with the reason on the row —
    never a bot dispatched at a platform that cannot be joined.

    Reads: refs.{platform, native_meeting_id, url, organizer, title} · Effect: at most one email
    Result: {platform}."""
    from . import emailx as mx                   # lazy: keeps the module's import surface flat
    if "platform" not in ctx.refs:
        # A reaction admitted BEFORE this change carries no platform fact. Those are Meet by
        # construction (the old intake matched nothing else), so absence is not "unknown" —
        # refusing them here would email organizers about invites we can and did join.
        return Done({"platform": "google_meet", "legacy_refs": True})
    platform = ctx.refs.get("platform") or "unknown"
    if ctx.refs.get("platform_supported", platform in SUPPORTED_PLATFORMS):
        return Done({"platform": platform})
    named = {"zoom": "Zoom", "jitsi": "Jitsi", "unknown": "that platform"}.get(
        platform, platform)
    if not ctx.scratch.get("told"):
        mx.send(ctx.refs["organizer"], f"Vexa can't join: {ctx.refs.get('title', 'your meeting')}",
                f"Vexa didn't accept this invitation — the meeting is on {named}, and Vexa "
                "joins Google Meet and Microsoft Teams today.\n\n"
                "Re-send the invitation with a Meet or Teams link and it will be picked up "
                "automatically.")
        ctx.scratch["told"] = True
    raise StepError(f"unsupported meeting platform '{platform}' "
                    f"(this lane joins {', '.join(SUPPORTED_PLATFORMS)}); organizer notified",
                    retryable=False)


def dispatch_bot(ctx: StepCtx):
    """Spawn the REAL bot via gateway POST /bots (transcribe per deployment; 409 = adopt the
    existing meeting).

    The ADDRESSING KEY travels as facts from the invite — ``platform`` + ``native_meeting_id``
    (+ ``passcode`` when the link carried one) — and is sent EXPLICITLY alongside the URL. The
    gateway treats a supplied native_meeting_id as authoritative and only derives one from the
    URL when it is absent (bot_spawn/router.py), so this makes intake and dispatch agree by
    construction instead of by two parsers hoping to. The URL rides along as the join
    passthrough, which is what makes a Teams SHORT link joinable: its id has no URL template.

    Prior: ensure_user (for the key) · Effect: bot container
    Result: {meeting_id, native, platform}."""
    key = user_api_key(ctx.prior["ensure_user"]["uid"])
    platform = ctx.refs.get("platform") or "google_meet"
    # A reaction admitted BEFORE this change carries no id fact. Those are Meet by construction
    # (the old intake matched nothing else), so the old URL-tail derivation is the right — and
    # only correct — fallback for them, and is never reached for a Teams invite.
    native = ctx.refs.get("native_meeting_id") or (
        ctx.refs["url"].rstrip("/").rsplit("/", 1)[-1].split("?")[0]
        if "meet.google.com" in ctx.refs["url"] else "")
    payload = {"meeting_url": ctx.refs["url"], "bot_name": "Vexa",
               "transcribe_enabled": False}
    if native:
        payload["platform"] = platform
        payload["native_meeting_id"] = native
    if ctx.refs.get("passcode"):
        payload["passcode"] = ctx.refs["passcode"]
    st, body = http("POST", f"{GATEWAY}/bots", {"X-API-Key": key}, payload)
    if st == 409:
        st2, existing = http("GET", f"{GATEWAY}/bots", {"X-API-Key": key})
        rows = existing if isinstance(existing, list) else existing.get("meetings", [])
        for m in rows:
            if native and m.get("native_meeting_id") == native:
                return Done({"meeting_id": m["id"], "native": m["native_meeting_id"],
                             "platform": m.get("platform", platform)},
                            provider_ref=str(m["id"]))
        raise StepError(f"409 but meeting not found")
    if st not in (200, 201):
        raise StepError(f"spawn failed {st}: {str(body)[:120]}")
    return Done({"meeting_id": body["id"],
                 "native": body.get("native_meeting_id") or native,
                 "platform": body.get("platform", platform)},
                provider_ref=str(body["id"]))


def _status(ctx: StepCtx) -> dict:
    d = ctx.prior["dispatch_bot"]
    key = user_api_key(ctx.prior["ensure_user"]["uid"])
    st, body = http("GET", f"{GATEWAY}/transcripts/{d['platform']}/{d['native']}", {"X-API-Key": key})
    return body if st == 200 else {"status": f"http-{st}"}


def run_meeting(ctx: StepCtx):
    """Poll-composite until completed. Transcribe window + (declared double) fixture injection."""
    d = ctx.prior["dispatch_bot"]
    m = _status(ctx)
    s = m.get("status") or "?"
    if s in ("requested", "joining", "awaiting_admission"):
        return Wait(seconds=6)
    if s == "active":
        window = ctx.refs.get("transcribe_s", 45.0)
        started = ctx.prior.get("run_meeting_active", {}).get("at")
        if started is None:
            # record activation moment as its own receipt-backed marker via a sub-effect result
            from flows import receipts as _r  # engine receipt store — same db handle via closure
        # simpler stateless marker: stash activation in the reaction's own receipt via Wait bookkeeping:
        # we approximate the window from meeting start_time instead of activation to stay stateless
        if ctx.clock_now - ctx.refs["start"] < window:
            return Wait(seconds=8)
        if FIXTURE_TRANSCRIPT and not (m.get("segments") or []):
            sql = "; ".join(
                "INSERT INTO transcriptions (meeting_id,start_time,end_time,text,speaker,language,session_uid,segment_id,created_at) "
                f"VALUES ({d['meeting_id']},{a},{b},'{t}','{sp}','en','flows-{d['meeting_id']}','fix-{i}',now()) ON CONFLICT DO NOTHING"
                for i, (a, b, sp, t) in enumerate(FIXTURE_LINES))
            subprocess.run(["docker", "exec", "vexa-v012-postgres-1", "psql", "-U", "postgres",
                            "-d", "vexa", "-c", sql], check=True, capture_output=True)
        key = user_api_key(ctx.prior["ensure_user"]["uid"])
        http("DELETE", f"{GATEWAY}/bots/{d['platform']}/{d['native']}", {"X-API-Key": key})
        return Wait(seconds=5)
    if s == "stopping":
        return Wait(seconds=4)
    if s == "completed":
        segs = m.get("segments") or []
        transcript = "\n".join(f"{x.get('speaker','?')}: {x.get('text','')}" for x in segs)
        return Done({"segments": len(segs), "transcript": transcript[:8000]})
    if s == "failed":
        raise StepError(f"meeting failed: {m.get('completion_reason')}", retryable=False)
    return Wait(seconds=6)
