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


def ensure_meeting_row(uid: str, url: str, title: str | None = None,
                       start_epoch: float | None = None) -> str:
    """The row a person is about to be SHOWN must exist before they are shown it.

    A prepare mail goes out long before dispatch_bot mints the meeting row, so its link used
    to carry the NATIVE id — a Zoom number — and the terminal substituted that number into the
    prep preset as the meeting's name. The agent then held nothing under it, said so, and reached
    for the only meeting it could find. The meeting the invite described was never in the product.

    So plan it here, as the organiser, out of what the invite already knew: the title, the start,
    and the link. POST /meetings writes an INTENT row (scheduled), and dispatch_bot's
    guarded create CLAIMS that same row in place at start − 2 min rather than inserting a second
    (bot_spawn/adapters.py create_meeting_guarded, step 2b) — so the plan, the prep chat and
    the transcript all live on ONE id.

    auto_join is FALSE on purpose. The row carries a time, and the auto-join sweep would
    otherwise dispatch its own bot on that time — a second dispatcher for a meeting this flow
    already dispatches. One loop, one write surface.

    Never raises: a link to a native id is weaker than a link to a row, and both beat no mail.
    """
    import datetime

    native = url.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
    existing = meeting_ref(uid, url)
    if existing != native:
        return existing                      # a row already exists (calendar sync, a retry, us)
    body: dict = {"meeting_url": url, "auto_join": False}
    if title:
        body["title"] = title
    if start_epoch:
        body["scheduled_at"] = datetime.datetime.fromtimestamp(
            float(start_epoch), datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        st, row = http("POST", f"{GATEWAY}/meetings", {"X-API-Key": user_api_key(uid)}, body)
    except StepError:
        return native
    if st in (200, 201) and isinstance(row, dict) and row.get("id") is not None:
        return str(row["id"])
    return meeting_ref(uid, url)             # 409 = someone minted it in the gap; read it back


def meeting_row(uid: str, meeting_id, native: str | None = None):
    """This user's meeting row from the gateway, or None. One read, several callers."""
    try:
        _st, body = http("GET", f"{GATEWAY}/meetings", {"X-API-Key": user_api_key(str(uid))})
    except StepError:
        return None
    rows = body.get("meetings", []) if isinstance(body, dict) else (body if isinstance(body, list) else [])
    found = None
    for m in rows:
        if not isinstance(m, dict):
            continue
        if meeting_id is not None and str(m.get("id")) == str(meeting_id):
            return m
        if native and m.get("native_meeting_id") == native:
            found = m
    return found


class ShareMintError(StepError):
    """A share mint that produced no token, with the HTTP facts INTACT.

    It exists because the previous shape of this call returned ``None`` on any non-2xx, and the
    caller's ``except Exception`` therefore never fired — a 404 is not an exception. The mail went
    out with no capability and nothing anywhere said why. A failure that has to survive a return
    value is a type, not a sentinel: the status and the response body are the only two facts that
    tell an operator what to fix, and both travel on this.

    A ``StepError`` subclass so that a caller which does NOT catch it still fails its step loudly
    rather than continuing — the safe default for a capability that could not be minted.
    """

    def __init__(self, *, meeting_id, identity: str, status, detail: str, retryable: bool):
        self.meeting_id = meeting_id
        self.identity = identity
        self.status = status
        self.detail = detail
        super().__init__(
            f"share mint failed for meeting {meeting_id} as {identity}: HTTP {status} — {detail}",
            retryable=retryable)


def _http_detail(body) -> str:
    """The response body as one readable line. FastAPI puts the reason in ``detail``; anything
    else is shown as-is, because a body we did not anticipate is still evidence."""
    if isinstance(body, dict):
        return str(body.get("detail") or body)[:280]
    return str(body)[:280]


def mint_transcript_share(uid: str, meeting_id, email: str,
                          expires_in_sec: int = 30 * 86400) -> str:
    """A RESTRICTED transcript share grant for ONE attendee — the capability that makes the
    meeting visible to them. Returns the token, or RAISES ``ShareMintError``. Never ``None``.

    The whole mechanism already existed and nothing used it from the mail path:
    ``POST /meetings/{meeting_id}/share`` mints ``data.share_grants[]``,
    ``POST /transcripts/share/accept`` redeems it into ``data.transcript_viewers[]``, the
    meetings list and the single-meeting read already carry a transcript-share access branch
    (``collector/adapters.py:438``), the GIN index for the containment probe already exists, and
    the terminal already redeems ``?tshare=`` silently after sign-in and cleans the URL
    (``clients/terminal/src/app/App.tsx``). The attendee's follow-up link simply never carried
    a token, so every attendee clicked into a chat that could not see the meeting the mail was
    about, and the agent fell back to its new-user greeting.

    ``restricted`` + this attendee's own address, never ``open``: the mail can be forwarded, and
    a forwarded link must grant its new reader nothing. That is the same rule as the fan-out's
    domain allow-list, one layer down.

    Addressed by the ROW id, not by ``(platform, native)``. The pair is not an identity: a meeting
    planned from an invite whose url matched no platform is stored as ``platform='unknown'`` with
    an EMPTY native, and NO pair addresses it — which is exactly what happened to row 97 on
    2026-09-02 (``POST /meetings/unknown/96088138284/share`` → 404, every attendee mailed a link
    with no capability). The row id always exists.

    A 2xx that carries no token is a failure too, and takes the same branch: the caller asked for
    a capability and did not get one, and the reason it did not is worth the same noise.
    """
    st, body = http("POST", f"{GATEWAY}/meetings/{meeting_id}/share",
                    {"X-API-Key": user_api_key(str(uid))},
                    {"mode": "restricted", "allowed_emails": [email],
                     "expires_in_sec": int(expires_in_sec)})
    token = body.get("token") if isinstance(body, dict) else None
    try:
        code = int(st)
    except (TypeError, ValueError):
        code = 0
    if 200 <= code < 300 and token:
        return token
    # 5xx and 429 are the platform having a moment; everything else is a fact about this meeting
    # or this key, and retrying it just delays the mail without changing the answer.
    raise ShareMintError(meeting_id=meeting_id, identity=email, status=st,
                         detail=_http_detail(body),
                         retryable=code == 429 or code >= 500)


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


def transcript_text(uid: str, meeting_id) -> str:
    """The meeting's words, read through the OWNING service's endpoint, for verification only.

    This is deliberately not a return to the copy that was just removed. Nothing here is written
    into an event, a prompt, or a file — it is read, compared, and dropped. The audit's rule is
    that a fact has one producer and is reached through its owner's interface; a service calling
    `GET /transcripts/by-id/{id}` on the gateway is exactly that. What was wrong before was
    COPYING the words into a fact and truncating them to fit.

    Never raises: verification that cannot run must not fail a meeting."""
    try:
        _st, body = http("GET", f"{GATEWAY}/transcripts/by-id/{meeting_id}",
                         {"X-API-Key": user_api_key(str(uid))})
    except StepError:
        return ""
    segs = (body or {}).get("segments") or [] if isinstance(body, dict) else []
    return "\n".join(str(g.get("text") or "") for g in segs)


def _tokens(text: str) -> list:
    """A name as comparable words: lowercase, split on anything that is not a letter or a digit,
    bare numbers dropped. `Anna-Maria Smith` and `anna.maria.smith2` both become the same three."""
    import re as _re
    return [t for t in _re.split(r"[^a-z0-9]+", (text or "").lower()) if t and not t.isdigit()]


def _address_tokens(email: str) -> list:
    """The name hiding in an address: the local part, tokenised. `anna.smith@bank.test` →
    ["anna", "smith"]. The domain is deliberately not included — every colleague shares it, so it
    can only ever create false matches."""
    return _tokens(str(email or "").split("@")[0])


def _match(label: str, participants: list) -> str:
    """One transcript speaker label → ONE invite address, or "" when it is not unambiguous.

    Scored by shared name tokens, best score wins, and a TIE MATCHES NOBODY. That last rule is the
    whole safety property: this list decides whose workspace the post-meeting turn may read, so a
    label that could be either of two people must resolve to neither. A missing mount costs the
    report some context; a wrong one shows one person's workspace to a room.

    At least one shared token must be three characters or longer, so initials and stray particles
    ("de", "van", "j") cannot carry a match on their own."""
    want = _tokens(label)
    if not want:
        return ""
    best, best_score, tied = "", 0, False
    for email in participants:
        common = set(want) & set(_address_tokens(email))
        score = len(common) if any(len(t) >= 3 for t in common) else 0
        if score > best_score:
            best, best_score, tied = email, score, False
        elif score and score == best_score and email != best:
            tied = True
    return "" if (tied or not best_score) else best


def speaking_order(uid: str, meeting_id, participants: list, cap: int = 12) -> list:
    """WHO SPOKE, most-speaking first, capped — the read-mount PROPOSAL for the post-meeting turn.

    Founder, 2026-09-02, on mounting every attendee's workspace: *"need to make sure agent will not
    die if it has 200 folders in it."* So the post-meeting run reads the workspaces of people who
    actually SPOKE, in the order of how much they spoke, and never more than `cap` of them. A
    fifty-person all-hands has five voices in it.

    Flows computes this because flows is where the transcript is reachable. It is a PROPOSAL and
    nothing more: agent-api verifies it against the meeting's real participants and mounts the
    intersection read-only, so this list can only ever NARROW what that side would allow. Nothing
    here mounts anything.

    Speaking time is `end - start` summed per label. When a producer gives no usable timings the
    fallback is characters spoken, which is a proxy for the same thing and keeps the ORDER honest
    even when the seconds are not available; a meeting with neither returns [].

    Never raises: a selection that cannot be computed is an empty proposal, which means the turn
    reads nobody's workspace — the safe direction. It is not a reason to fail a meeting."""
    if not participants:
        return []          # nothing a label could match, so the transcript is not worth reading
    try:
        _st, body = http("GET", f"{GATEWAY}/transcripts/by-id/{meeting_id}",
                         {"X-API-Key": user_api_key(str(uid))})
    except StepError:
        return []
    segs = (body or {}).get("segments") or [] if isinstance(body, dict) else []
    seconds, chars = {}, {}
    for g in segs:
        if not isinstance(g, dict):
            continue
        label = str(g.get("speaker") or "").strip()
        if not label:
            continue
        try:
            dur = float(g.get("end") or 0) - float(g.get("start") or 0)
        except (TypeError, ValueError):
            dur = 0.0
        seconds[label] = seconds.get(label, 0.0) + max(dur, 0.0)
        chars[label] = chars.get(label, 0) + len(str(g.get("text") or ""))
    if not seconds:
        return []
    weight = seconds if any(v > 0 for v in seconds.values()) else chars
    ranked = sorted(weight.items(), key=lambda kv: (-kv[1], kv[0]))
    out = []
    for label, _w in ranked:
        email = _match(label, list(participants or []))
        if email and email not in out:
            out.append(email)
        if len(out) >= max(int(cap), 0):
            break
    return out


def _phrases(text: str, n: int = 6) -> set:
    import re as _re
    ws = _re.findall(r"[a-z0-9']+", (text or "").lower())
    return {" ".join(ws[i:i + n]) for i in range(len(ws) - n + 1)
            if any(len(w) >= 6 for w in ws[i:i + n])}


def grounded_in(note: str, transcript: str) -> bool:
    """Does this note contain words that are actually IN the meeting?

    A six-word run carrying a real content word does not appear in two texts by accident. Short
    windows do: a four-word test matched "do you want to" and would have passed anything.
    An empty transcript answers True — absence of evidence is not evidence of fabrication, and a
    meeting with no captured speech must still be writable."""
    if not transcript.strip():
        return True
    return bool(_phrases(note) & _phrases(transcript))


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
