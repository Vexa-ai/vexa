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


def dispatch_bot(ctx: StepCtx):
    """Spawn the REAL bot via gateway POST /bots (transcribe per deployment; 409 = adopt the
    existing meeting). Prior: ensure_user (for the key) · Effect: bot container
    Result: {meeting_id, native, platform}."""
    uid = ctx.prior["ensure_user"]["uid"]
    key = user_api_key(uid)
    # transcribe_enabled is NOT passed. It used to be hardcoded False here, against this step's
    # own docstring, which is why a standup recorded audio and captured no words. The platform
    # defaults it to True and resolves TRANSCRIBE_ENABLED per deployment — so naming it here
    # could only ever override a correct decision with a worse one. The name is the person's;
    # whether we transcribe at all is the deployment's.
    st, body = http("POST", f"{GATEWAY}/bots", {"X-API-Key": key},
                    {"meeting_url": ctx.refs["url"],
                     "bot_name": setting(uid, "bot_name") or "Vexa"})
    if st == 409:
        st2, existing = http("GET", f"{GATEWAY}/bots", {"X-API-Key": key})
        rows = existing if isinstance(existing, list) else existing.get("meetings", [])
        for m in rows:
            if m.get("native_meeting_id") == ctx.refs["url"].rsplit("/", 1)[1]:
                return Done({"meeting_id": m["id"], "native": m["native_meeting_id"],
                             "platform": m.get("platform", "google_meet")},
                            provider_ref=str(m["id"]))
        raise StepError(f"409 but meeting not found")
    if st not in (200, 201):
        raise StepError(f"spawn failed {st}: {str(body)[:120]}")
    return Done({"meeting_id": body["id"],
                 "native": body.get("native_meeting_id") or ctx.refs["url"].rsplit("/", 1)[1],
                 "platform": body.get("platform", "google_meet")},
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
