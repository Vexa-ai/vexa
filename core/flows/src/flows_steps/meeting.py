"""Meeting-domain steps — gateway HTTP, no imports of meeting code. Stateless: meeting identity
travels in step results (receipts), never in process memory."""
from __future__ import annotations

import subprocess
import time

from flows import Done, StepCtx, StepError, Wait

# `setting` was USED below and never imported — dispatch_bot raised NameError on its first line
# of real work, which the loop reports as "unexpected: NameError(...)" against the gateway rather
# than against this file. Found while adding the prep step, not by a test: nothing exercises
# dispatch_bot outside a live meeting.
from .common import FIXTURE_TRANSCRIPT, GATEWAY, http, setting, user_api_key

FIXTURE_LINES = [
    (0.0, 6.0, "Anna", "Alright, quick sync on the pilot. Two decisions today."),
    (6.5, 14.0, "Ben", "First: we go with the phased rollout — pilot group is treasury, four weeks."),
    (14.5, 22.0, "Anna", "Agreed. Decision one: phased rollout, treasury first, four weeks starting Monday."),
    (22.5, 30.0, "Ben", "Second: Anna owns the security review. I need it before the pilot starts."),
    (30.5, 38.0, "Anna", "I commit to the security review by Friday. Send me the checklist today."),
    (38.5, 44.0, "Ben", "Will do — checklist to you by end of day. That's a commitment."),
    (44.5, 50.0, "Anna", "Open question for next time: do we invite risk and compliance to the pilot?"),
]


def meeting_ref(uid: str, url: str) -> str:
    """What a terminal deeplink calls this meeting.

    The platform ROW id when the row exists; the NATIVE id when it does not yet — the row is
    minted at bot dispatch (start − 2 min) and a prepare link is sent long before that. The
    terminal resolves either against the person's own meeting list, so the earlier link is not a
    worse link once the row lands. A lookup that fails degrades to the native id rather than
    raising: a mail with a slightly weaker link beats no mail.
    """
    native = url.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
    try:
        _st, body = http("GET", f"{GATEWAY}/meetings", {"X-API-Key": user_api_key(uid)})
    except StepError:
        return native
    rows = body.get("meetings", []) if isinstance(body, dict) else (body if isinstance(body, list) else [])
    ids = [int(m["id"]) for m in rows
           if isinstance(m, dict) and m.get("native_meeting_id") == native and m.get("id") is not None]
    return str(max(ids)) if ids else native


def meeting_start(uid: str, meeting_id, native: str | None = None):
    """The meeting row's OWN start epoch, or None.

    Used when the event's refs carry no `start` — a meeting created by the terminal or seeded
    from a fixture rather than admitted from an invite.

    Order: `start_time` (when it actually ran) → `scheduled_at` (when it was meant to) →
    `created_at` (when the row appeared). `scheduled_at` was the missing rung and it is the one a
    seeded meeting has: `meeting_seed` sets it from the fixture's own occurrence while
    `start_time` stays NULL, so without this the stamp fell through to `created_at` — today — and
    several occurrences of one recurring series collapsed into a single note file. Never raises:
    a date we cannot resolve degrades to today, which is the behaviour this replaces.
    """
    import datetime
    try:
        _st, body = http("GET", f"{GATEWAY}/meetings", {"X-API-Key": user_api_key(str(uid))})
    except StepError:
        return None
    rows = body.get("meetings", []) if isinstance(body, dict) else (body if isinstance(body, list) else [])
    row = None
    for m in rows:
        if not isinstance(m, dict):
            continue
        if meeting_id is not None and str(m.get("id")) == str(meeting_id):
            row = m
            break
        if native and m.get("native_meeting_id") == native:
            row = m
    if not row:
        return None
    for key in ("start_time", "scheduled_at", "created_at"):
        v = row.get(key)
        if not v:
            continue
        try:
            return datetime.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
        except Exception:  # noqa: BLE001
            continue
    return None


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
        # The transcript is NOT returned. It used to come back capped at 8,000 characters so it
        # could ride inside meeting.completed — a copy of a fact the transcription domain owns,
        # and a cap that decided how much of an hour the agent would ever see. Segment count is a
        # receipt; the words are read through the MCP by whoever needs them.
        return Done({"segments": len(segs)})
    if s == "failed":
        raise StepError(f"meeting failed: {m.get('completion_reason')}", retryable=False)
    return Wait(seconds=6)
