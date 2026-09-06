"""Meeting-domain steps — gateway HTTP, no imports of meeting code. Stateless: meeting identity
travels in step results (receipts), never in process memory."""
from __future__ import annotations

import time

from flows import Block, Done, StepCtx, StepError, Wait

# `setting` was USED below and never imported — dispatch_bot raised NameError on its first line
# of real work, which the loop reports as "unexpected: NameError(...)" against the gateway rather
# than against this file. Found while adding the prep step, not by a test: nothing exercises
# dispatch_bot outside a live meeting.
from .common import http, meetings_door, setting, user_api_key

# THE FIXTURE-TRANSCRIPT INJECTION IS GONE (PRD decision 18d). When a rehearsed meeting completed
# with no segments, this file used to write seven canned transcript rows into the MEETINGS database
# with `docker exec <a named container> psql` — flows reaching past another domain's API, into
# another domain's tables, through a container belonging to one developer's other stack on one
# host. No deployment surface ever set the capability key that gated it, so the path existed
# only for that machine. A transcript double belongs to the transcription domain that owns
# the words, and reaches flows the same way a real one does: through the gateway.


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
        _st, body = http("GET", f"{meetings_door()}/meetings", {"X-API-Key": user_api_key(uid)})
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
        st, row = http("POST", f"{meetings_door()}/meetings", {"X-API-Key": user_api_key(uid)}, body)
    except StepError:
        return native
    if st in (200, 201) and isinstance(row, dict) and row.get("id") is not None:
        return str(row["id"])
    return meeting_ref(uid, url)             # 409 = someone minted it in the gap; read it back


def meeting_row(uid: str, meeting_id, native: str | None = None):
    """This user's meeting row from the gateway, or None. One read, several callers."""
    try:
        _st, body = http("GET", f"{meetings_door()}/meetings", {"X-API-Key": user_api_key(str(uid))})
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
    st, body = http("POST", f"{meetings_door()}/meetings/{meeting_id}/share",
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
        _st, body = http("GET", f"{meetings_door()}/meetings", {"X-API-Key": user_api_key(str(uid))})
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


def transcript_text(uid: str, meeting_id) -> str | None:
    """The meeting's words, read through the OWNING service's endpoint, for verification only —
    or **None** when they could not be read at all.

    This is deliberately not a return to the copy that was just removed. Nothing here is written
    into an event, a prompt, or a file — it is read, compared, and dropped. The audit's rule is
    that a fact has one producer and is reached through its owner's interface; a service calling
    `GET /transcripts/by-id/{id}` on the gateway is exactly that. What was wrong before was
    COPYING the words into a fact and truncating them to fit.

    THE TWO EMPTIES ARE NOT THE SAME EMPTY, and collapsing them switched the grounding gate off
    exactly when it was most needed (R-B19). This returned `""` for *the meeting had no speech*
    and also for *we could not read it* — a 404 on an id that addresses nothing, a token that
    would not mint, the gateway restarting — and `grounded_in("")` answers True by design. So on
    precisely the broken-identity meetings the gate exists for, it passed silently and mailed a
    report nobody could trace. `None` is now the unreadable case and the caller decides; `""`
    still means a meeting with no captured speech, which must still be writable.
    """
    try:
        _st, body = http("GET", f"{meetings_door()}/transcripts/by-id/{meeting_id}",
                         {"X-API-Key": user_api_key(str(uid))})
    except StepError:
        return None
    if not isinstance(body, dict) or "segments" not in body:
        return None
    segs = body.get("segments") or []
    return "\n".join(str(g.get("text") or "") for g in segs)


def transcript_segment_count(uid: str, meeting_id) -> int | None:
    """HOW MANY SEGMENTS this meeting captured — or **None** when it could not be read at all.

    The same read `transcript_text` does, through the same owning-service endpoint, answering the
    one question a caller wants when it does not want the words: *did this meeting transcribe?*
    Counting the lines of `transcript_text` would not answer it, because that function joins the
    segment texts and a meeting that captured nothing and a read that failed both come back falsy
    — the exact collapse of two empties R-B19 was about, one level up.

    THE THREE ANSWERS ARE THREE: `None` unreadable, `0` a meeting that captured nothing, `n` a
    meeting that did. A caller deciding *has this person seen Vexa work yet* must not read a
    gateway restart as a person who never tried."""
    try:
        _st, body = http("GET", f"{meetings_door()}/transcripts/by-id/{meeting_id}",
                         {"X-API-Key": user_api_key(str(uid))})
    except StepError:
        return None
    if not isinstance(body, dict) or "segments" not in body:
        return None
    return len(body.get("segments") or [])


def _tokens(text: str) -> list:
    """A name as comparable words: lowercase, split on anything that is not a letter or a digit,
    bare numbers dropped. `Anna-Maria Smith` and `anna maria smith` become the same three."""
    import re as _re
    return [t for t in _re.split(r"[^a-z0-9]+", (text or "").lower()) if t and not t.isdigit()]


def _match(label: str, names: dict) -> str:
    """One transcript speaker label -> ONE invite address, or "" when it is not unambiguous.

    Matched against the invite's own `CN=` DISPLAY NAMES, never against an email local part. That
    is the whole reason `participant_names` exists: deriving "Anna Smith" from `a.smith@` is a
    guess, and a guess here silently reorders whose desk gets read first.

    Scored by shared name tokens, best score wins, and A TIE MATCHES NOBODY — at least one shared
    token must be three characters or longer, so initials and particles cannot carry a match.

    Note what this decides and what it does not: it decides ORDER. Membership is the invite, so a
    label that matches nobody costs that person nothing — they are still in the room, just further
    down the list. This function can never remove anyone.
    """
    want = _tokens(label)
    if not want:
        return ""
    best, best_score, tied = "", 0, False
    for email, display in (names or {}).items():
        common = set(want) & set(_tokens(display))
        score = len(common) if any(len(t) >= 3 for t in common) else 0
        if score > best_score:
            best, best_score, tied = email, score, False
        elif score and score == best_score and email != best:
            tied = True
    return "" if (tied or not best_score) else best


def speaking_seconds(uid: str, meeting_id) -> dict:
    """`{speaker label: seconds}` for one meeting, read through the transcript's owning endpoint.

    `end - start` summed per label. When a producer gives no usable timings the fallback is
    CHARACTERS spoken — a proxy for the same thing, which keeps the ORDER honest even when the
    seconds are not available.

    Never raises: an unreadable transcript is `{}`, which means nobody is prioritised and the
    invite's own order stands. It is not a reason to fail a meeting."""
    try:
        _st, body = http("GET", f"{meetings_door()}/transcripts/by-id/{meeting_id}",
                         {"X-API-Key": user_api_key(str(uid))})
    except StepError:
        return {}
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
        return {}
    return seconds if any(v > 0 for v in seconds.values()) else {k: float(v)
                                                                 for k, v in chars.items()}


def room_order(uid: str, meeting_id, participants: list, names: dict,
               cap: int = 0) -> list:
    """The invite's addresses, PRIORITISED by speaking time — and by default cut by NOBODY.

    THE CAP MOVED, and the default inverted with it (R-B17). This function used to cut the list
    to twelve ADDRESSES while agent-api, handed the same number, deliberately capped MOUNTED DESKS
    instead — *"capping before resolution would silently under-fill the room"*, in its own words.
    Both cuts ran. Twelve addresses of which nine had no desk produced a three-desk room, and `0`
    meant "no cut" here, "unset, use twelve" in the flow param, and "mount nobody" nowhere. One
    enforcement, at the end, by the service that can tell a desk from an address: this function
    now ORDERS and the cap is agent-api's, unless a caller explicitly asks for one.

    Founder, 2026-09-02, in two halves that have to stay separate:

      MEMBERSHIP is the invite. Everybody on it is eligible — being quiet in a meeting you were in
      does not remove your desk from the room, and it never could: the point of reading a desk is
      to understand what somebody meant, and the quiet ones are exactly the people whose context is
      not in the transcript.
      SPEAKING only ORDERS. Matched participants first, by how much they spoke; everyone else
      after them in invite order; cut at `cap` (`room_read_max`, default 12) so a fifty-person
      all-hands does not hand the run fifty desks.

    **A failed match degrades to invite order, NEVER to an empty room.** No transcript, no
    timings, no `CN=` names, nothing matching anything — the answer is still the first `cap`
    addresses on the invite. An empty room from a matcher that could not do its job is a silent
    loss of the whole feature, and it would look exactly like a meeting where nobody spoke.

    Addresses, not subject ids: agent-api resolves identity and mounts only the people who already
    have a desk, so a stranger on the invite is skipped THERE and no account is minted anywhere."""
    invite = [str(a).strip().lower() for a in (participants or []) if str(a).strip()]
    seen, ordered = set(), []
    for a in invite:                       # dedupe, keep the invite's own order
        if a not in seen:
            seen.add(a)
            ordered.append(a)
    if not ordered:
        return []
    seconds = speaking_seconds(uid, meeting_id)
    rank = {}
    for label, weight in seconds.items():
        email = _match(label, names or {})
        if email in seen:
            rank[email] = rank.get(email, 0.0) + float(weight)
    # matched-and-spoke first (most first), then everybody else in the invite's own order
    spoke = sorted(rank, key=lambda a: (-rank[a], ordered.index(a)))
    rest = [a for a in ordered if a not in rank]
    limit = max(int(cap), 0) or len(ordered)
    return (spoke + rest)[:limit]


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
    # NO bot_name. The person's default is a fact about the BOT, so the domain that owns the bot
    # resolves it — meeting-api reads it from identity's bot-context on every spawn path now. This
    # step used to read a `bot_name` key out of `.settings.json` in the AGENT domain, which is how
    # one fact came to have three stores and why the same person's bot showed up under one name
    # when a calendar armed it and another when a flow did (founder ruling, 2026-09-02).
    st, body = http("POST", f"{meetings_door()}/bots", {"X-API-Key": key},
                    {"meeting_url": ctx.refs["url"]})
    if st == 409:
        st2, existing = http("GET", f"{meetings_door()}/bots", {"X-API-Key": key})
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


#: HOW LONG THE TEARDOWN MAY TAKE before it stops being a slow stop and starts being a bot that
#: will not leave (P22). It is generous — a bot in a call that is winding down legitimately takes
#: tens of seconds — and it is finite, which is the whole point: the poll below used to have no
#: deadline at all, so a stop that never completed sat on `Wait(4)` for the life of the worker,
#: recording, while the reaction stayed `retrying` and nothing anywhere said the word "stuck".
STOP_DEADLINE_S = 10 * 60
#: How many DELETEs may be answered non-2xx before the step stops asking. A stop is idempotent and
#: the platform may be restarting, so one refusal is not an answer; three in a row is.
STOP_ATTEMPTS_MAX = 3
#: The same bound on the READ. `_status` maps a non-200 to a named branch instead of an unknown
#: status string, and an unreadable status is retried a bounded number of times rather than
#: forever — a 404 on a meeting that does not exist never becomes readable by asking again.
STATUS_UNREADABLE = "unreadable"
STATUS_READ_ATTEMPTS_MAX = 10


def _status(ctx: StepCtx) -> dict:
    """The bot's own status document, or a NAMED unreadable answer.

    It used to return `{"status": f"http-{st}"}` for every non-200, and no branch in `run_meeting`
    matched a string of that shape — so a 404, a 500 and a gateway restart all fell through to the
    bottom `Wait(6)` and were retried, identically and forever, as if the meeting were simply in a
    state this function had not heard of. Two different things were hidden in that one string: a
    status we do not recognise (a genuine unknown, worth waiting on) and a read that did not
    happen (worth counting, and worth giving up on). `unreadable` is the second, and it carries the
    code so an operator reading the reason knows which door said what."""
    d = ctx.prior["dispatch_bot"]
    key = user_api_key(ctx.prior["ensure_user"]["uid"])
    try:
        st, body = http("GET", f"{meetings_door()}/transcripts/{d['platform']}/{d['native']}",
                        {"X-API-Key": key})
    except StepError as e:
        return {"status": STATUS_UNREADABLE, "http": None, "detail": str(e)[:200]}
    if st == 200 and isinstance(body, dict):
        return body
    return {"status": STATUS_UNREADABLE, "http": st, "detail": _http_detail(body)}


def _stop_bot(ctx: StepCtx, d: dict):
    """Ask the bot to leave, and CHECK THAT IT WAS ASKED (P22, E2).

    The DELETE's return used to be discarded entirely: `http("DELETE", …)` on its own line, no
    status, no branch. A teardown that 403s or 404s is then indistinguishable from one that worked,
    and the step goes on to poll for a `stopping` that will never arrive — a bot that will not
    leave a call records unboundedly and surfaces nowhere.

    404/409 ARE SUCCESS HERE. A stop is idempotent and both of those mean the bot is already gone
    (a retry after a partial run, or the platform's own reaper got there first) — treating them as
    failures would escalate the one case that is already the outcome we want.

    Anything else is counted in the reaction's durable scratch, so the count survives the worker:
    once, the platform may be restarting; `STOP_ATTEMPTS_MAX` times running is a fact about this
    meeting, and the caller escalates."""
    key = user_api_key(ctx.prior["ensure_user"]["uid"])
    try:
        st, body = http("DELETE", f"{meetings_door()}/bots/{d['platform']}/{d['native']}",
                        {"X-API-Key": key})
    except StepError as e:
        st, body = None, str(e)[:200]
    try:
        code = int(st)
    except (TypeError, ValueError):
        code = 0
    if 200 <= code < 300 or code in (404, 409):
        ctx.scratch["stop_failures"] = 0
        return None
    n = int(ctx.scratch.get("stop_failures") or 0) + 1
    ctx.scratch["stop_failures"] = n
    ctx.scratch["stop_last"] = f"HTTP {st} — {_http_detail(body)}"
    return n


def run_meeting(ctx: StepCtx):
    """Poll-composite until completed, over the transcribe window."""
    d = ctx.prior["dispatch_bot"]
    m = _status(ctx)
    s = m.get("status") or "?"
    if s == STATUS_UNREADABLE:
        # THE READ ITSELF FAILED, and that is not a meeting state. Bounded, because a 404 on a
        # meeting this key cannot address never becomes readable by asking again — and unbounded
        # retries against it are exactly how a reaction disappears into `retrying` with a reason
        # that names no cause.
        n = int(ctx.scratch.get("status_unreadable") or 0) + 1
        ctx.scratch["status_unreadable"] = n
        if n > STATUS_READ_ATTEMPTS_MAX:
            raise StepError(
                f"the bot's status has been unreadable {n} times for "
                f"{d['platform']}/{d['native']}: {m.get('http')} — {m.get('detail')}",
                retryable=False)
        return Wait(seconds=6)
    ctx.scratch["status_unreadable"] = 0
    if s in ("requested", "joining", "awaiting_admission"):
        return Wait(seconds=6)
    if s == "active":
        window = ctx.refs.get("transcribe_s", 45.0)
        # The window is measured from the meeting's own start rather than from the moment the bot
        # went active: this step is stateless across ticks by design, and the start is a ref.
        if ctx.clock_now - ctx.refs["start"] < window:
            return Wait(seconds=8)
        failures = _stop_bot(ctx, d)
        if failures and failures >= STOP_ATTEMPTS_MAX:
            return Block(
                reason=(f"the bot for {d['platform']}/{d['native']} would not stop after "
                        f"{failures} attempts: {ctx.scratch.get('stop_last')}. It may still be in "
                        f"the call and recording."),
                deadline_s=STOP_DEADLINE_S)
        return Wait(seconds=5)
    if s == "stopping":
        # A CEILING ON THE STOP. Without one this is `Wait(4)` for the life of the worker: the
        # meeting never completes, the reaction never fails, and the bot that will not leave the
        # call is visible to nobody. The deadline turns it into a blocked row with a reason, which
        # is the surface a person actually reads.
        since = ctx.scratch.setdefault("stopping_since", ctx.clock_now)
        if ctx.clock_now - since > STOP_DEADLINE_S:
            return Block(
                reason=(f"the bot for {d['platform']}/{d['native']} has been stopping for "
                        f"{int(ctx.clock_now - since)}s and has not left the call."),
                deadline_s=STOP_DEADLINE_S)
        return Wait(seconds=4)
    if s == "completed":
        segs = m.get("segments") or []
        # The transcript is NOT returned. It used to come back capped at 8,000 characters so it
        # could ride inside meeting.completed — a copy of a fact the transcription domain owns,
        # and a cap that decided how much of an hour the agent would ever see. Segment count is a
        # receipt; the words are read through the MCP by whoever needs them.
        return Done({"segments": len(segs)})
    if s == "failed":
        raise StepError(f"meeting failed: {m.get('completion_reason')}", retryable=False)
    return Wait(seconds=6)
