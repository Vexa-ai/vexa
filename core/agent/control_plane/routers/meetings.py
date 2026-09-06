"""routers/meetings.py — The meeting seam: relay health, the annotation layer over a transcript, and the
live transcript stream a chat renders beside the conversation.

Extracted from `api.py`'s `create_app` VERBATIM: the handler bodies below are the same
bytes, with `@app.` rewritten to `@router.` and nothing else. Everything they close over
is handed in by `build()` and rebound to the name it already had, so no body needed a
single identifier changed.
"""
from __future__ import annotations

from control_plane import meeting_mint as meeting_mint_mod
from control_plane import meeting_note as meeting_note_mod
from control_plane import meeting_terms as meeting_terms_mod
from control_plane.api_shared import (
    MEETING_STREAM_TRANSCRIPT_REPLAY, _decode_sse_cursor, _encode_sse_cursor, _sse)
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import StreamingResponse
import json


def build(**d) -> APIRouter:
    """The meetings routes, bound to one app's dependencies."""
    router = APIRouter()
    _meeting_note_recorder = d['_meeting_note_recorder']
    _meeting_owner_lookup = d['_meeting_owner_lookup']
    live = d['live']
    redis_url = d['redis_url']
    subject_of = d['subject_of']
    wsr = d['wsr']

    @router.get("/api/meeting/relay-health")
    def meeting_relay_health(request: Request):
        """P18 (ADR 0010) — the transcript relay's observable health: is the numeric→native resolve OK,
        and are segments arriving? A stale `VEXA_BOT_API_KEY` (401 on `/meetings`) shows here as a typed
        `native_resolve: {ok:false, kind:'unauthorized', detail:…}` instead of silent dead air."""
        from control_plane import transcription_watcher as _txw
        return _txw.relay_health()
    @router.get("/api/meeting/note")
    def meeting_note(meeting_id: str, request: Request):
        """WHERE THIS MEETING'S REPORT LIVES ON THE CALLER'S DESK — `{"path", "transcript", "cursor"}`.

        The client asks; it does not spell. `kg/entities/meeting/<meeting-day>-<title-slug>.md` is
        written by `core/flows`' `drop_to_attendees`, the day in the organiser's zone and the slug
        through an allow-list, and the terminal used to point its Minutes tab at
        `kg/entities/meeting/<native>.md` instead — one path, two spellings, in two languages, and
        they never matched. The founder opened a meeting whose report had been written, mailed and
        dropped an hour earlier and was told there was no page there (Vexa-ai/vexa#1588). A chat
        born from a mailed link already gets the path on its scaffold (`refs.note_path`); this is
        the same answer for the chat opened from the rail, which is every meeting after the first.

        `null` IS AN ANSWER, and the ordinary one before the report lands: nothing on this desk
        names this meeting yet. The caller opens one document fewer — never a tab onto a guess.

        OWNER-SCOPED BEFORE ANYTHING IS READ, exactly as `/api/meeting/stream` is one route down and
        for the same reason: row ids are sequential ints, and this answers with a path on a DESK.
        `_meeting_owner_lookup` returns None for an absent row and for another tenant's.

        `transcript` AND `cursor` ARE READ OFF THE PAGE (Vexa-ai/vexa#1598). A meeting doc that
        declares the transcript widget (`<!-- vexa:transcript meeting=… -->`) IS the meeting's one
        page — the live transcript renders inside it — so the room shows no separate Transcript tab.
        Both are `""` for every report written before the widget existed, and that absence is what
        keeps those meetings on the two-page room they have today instead of losing the transcript."""
        subject = subject_of(request)   # 401 if no (gateway-injected) identity — fail closed
        row = _meeting_owner_lookup(subject, meeting_id)
        if row is None:
            raise HTTPException(status_code=403, detail="not authorized for this meeting")
        return meeting_note_mod.describe(wsr.root, subject, row)
    @router.post("/api/meeting/note")
    def mint_meeting_note(request: Request, body: dict = Body(default={})):
        """MINT this meeting's page on the caller's desk if it is not there — `{"path", "created"}`.

        The same act `_binding_watch` performs in-process when a chat sends a bot (Vexa-ai/vexa#1601);
        this is the door for the caller that is not that chat — `core/flows`' upcoming step, which
        mints the page for the organiser at the moment their meeting's row is created, so a meeting
        that arrives from the mailbox has a document before anybody opens it.

        IDEMPOTENT: a page already there is returned untouched, never refreshed. It is written by
        three hands after this — the person, their Expand, and the flow's report — so re-minting
        would delete somebody's writing at the moment the room got busy.

        `path` is a PROPOSAL, not an instruction: the caller may name the page it already knows
        about, and it is honoured only if it is a meeting page (`meeting_note.is_note_path`).

        Owner-scoped before anything is written, exactly as the read above — row ids are sequential
        ints and this creates a file on a DESK."""
        subject = subject_of(request)   # 401 if no (gateway-injected) identity — fail closed
        meeting_id = str((body or {}).get("meeting_id") or (body or {}).get("meeting") or "").strip()
        row = _meeting_owner_lookup(subject, meeting_id)
        if row is None:
            raise HTTPException(status_code=403, detail="not authorized for this meeting")
        return meeting_mint_mod.mint(wsr.root, subject, row,
                                     path=str((body or {}).get("path") or ""),
                                     record=_meeting_note_recorder)
    @router.get("/api/meeting/terms")
    def meeting_terms(meeting_id: str, request: Request):
        """THIS MEETING'S ANNOTATION LAYER — `{"meeting", "cursor", "terms"}` (Vexa-ai/vexa#1595).

        Founder, mid-meeting with Highlight pressed: *"we want transcript being attributed with
        extracted entities when we get highlight — it should attribute the transcript in an
        efficient way (no rewrite)"*. The map is what the canvas draws chips FROM; the transcript
        itself is never touched, and the client re-finds each surface form in the words it is
        already rendering, so every segment after the Highlight is attributed with no model call.

        THE CANVAS ASKS ON OPEN. The chips also arrive live as the chat's `terms` event, and that is
        still the fast path — but an event is a moment, and before this route a reload left the
        transcript plain again because the only copy was that tab's memory. This is the copy that
        survives, and it is on the SERVER rather than in browser storage so it is the same map on
        the phone, the laptop and the reader who was handed the meeting.

        AN EMPTY MAP IS THE ORDINARY ANSWER — nobody has highlighted this meeting — and the caller
        must be able to tell it from a failure: the transcript renders exactly the plain text it did
        before, which is what makes an un-highlighted meeting cost nothing.

        OWNER-SCOPED BEFORE ANYTHING IS READ, exactly as `/api/meeting/note` above and
        `/api/meeting/stream` below, and for the same reason: row ids are sequential ints, and this
        answers with what was said on somebody's DESK."""
        subject = subject_of(request)   # 401 if no (gateway-injected) identity — fail closed
        row = _meeting_owner_lookup(subject, meeting_id)
        if row is None:
            raise HTTPException(status_code=403, detail="not authorized for this meeting")
        return meeting_terms_mod.read(wsr.root, subject, meeting_id)
    @router.post("/api/meeting/terms")
    def publish_meeting_terms(request: Request, body: dict = Body(default={})):
        """One Highlight's publish, ADDED to this meeting's map. Returns the whole map.

        The writer is the ACT — `transcript_terms(..., keep=…)` in the control MCP, the same call
        whose result the harness turns into the chat's `terms` event. One loop, one write surface:
        nothing else composes this file, and the canvas only ever reads it.

        APPEND-ONLY AND IDEMPOTENT (`meeting_terms.merge`): re-running Highlight extends the map,
        the same publish twice changes nothing, and an empty publish is a non-event rather than an
        empty map — an empty map would read as *"and now there are none"* and wipe the chips the
        previous press put on the screen.

        Owner-scoped like the read. `meeting_id` travels in the BODY here, with the terms it
        annotates, so one publish is one object rather than a query string beside a payload."""
        subject = subject_of(request)   # 401 if no (gateway-injected) identity — fail closed
        meeting_id = str((body or {}).get("meeting_id") or (body or {}).get("meeting") or "").strip()
        row = _meeting_owner_lookup(subject, meeting_id)
        if row is None:
            raise HTTPException(status_code=403, detail="not authorized for this meeting")
        terms = (body or {}).get("terms")
        if not isinstance(terms, list):
            raise HTTPException(status_code=422, detail="terms must be a list")
        return meeting_terms_mod.extend(wsr.root, subject, meeting_id, terms,
                                        str((body or {}).get("cursor") or ""))
    @router.get("/api/meeting/stream")
    def meeting_stream(meeting_id: str, session_uid: str, request: Request):
        """SSE feed for a LIVE meeting: the transcript Stream (`tc:meeting:{id}`), and only that.
        Segments as the bot hears them, `retract` when the collector withdraws a draft, and
        `meeting-end` when the session closes. It used to MERGE a second stream — the copilot's
        output (`unit:agent-meet-{sid}:out`: `card`s, `note`s, `model-error`, `message-delta`) —
        and PRD decision 34 removed the producer of all of it, so the merge went with it.

        RESUMABLE: every event carries an SSE ``id:`` = the per-stream redis cursors. On reconnect the
        browser echoes the last one as ``Last-Event-ID``; we resume EXACTLY from there (redis streams are
        durable + id-addressable) instead of re-seeding only the last N entries. Without this, a transient
        disconnect (the 'Live stream disconnected — reconnecting' path) dropped every segment published in
        the gap beyond the bounded replay window from the LIVE view — the real-time transcript-loss bug
        (the durable store kept them, so they only reappeared post-time)."""
        if not redis_url:
            raise HTTPException(status_code=501, detail="redis not wired")

        # P0 (cross-tenant leak fix — SSE sibling of the by-id REST ownership check): OWNER-SCOPE the live
        # feed BEFORE opening any redis stream. `meeting_id` (row id) + `session_uid` arrive from the
        # caller's query params; row ids are sequential ints, so without this an authenticated user B could
        # `EventSource(...?meeting_id=<A_row>&session_uid=<A_native>)` and stream tenant A's live transcript
        # (an ACTIVE, enumerable cross-tenant read). Mirror the WS `/ws` path: derive the
        # caller identity (`subject_of` → 401 on no gateway-injected X-User-Id) and verify the caller OWNS
        # the requested row (meeting-api `GET /meetings/{id}` owner-scopes in SQL: `Meeting.user_id ==
        # user_id` → 404 for a foreign/absent row). Fail CLOSED (403) BEFORE the stream opens.
        # OWNER-ONLY for now (matches the WS path today); a shared-workspace membership grant would extend
        # `_meeting_owner_lookup` — the clean seam — but is intentionally NOT honored here yet.
        subject = subject_of(request)  # 401 if no (gateway-injected) identity — fail closed
        owned = _meeting_owner_lookup(subject, meeting_id)
        if owned is None:
            # Absent row, or a row owned by a DIFFERENT tenant → refuse (404-equivalent, no stream opened).
            raise HTTPException(status_code=403, detail="not authorized for this meeting")
        # `session_uid` is ALSO caller-supplied. The terminal passes the ROW id as `session_uid` for
        # live rows (liveMeetings.ts `session_uid = live ? id : undefined`); the meeting's own native
        # id is accepted for the legacy shape (native==row==session). Bind it to the OWNED row so a
        # caller cannot pair its own row with someone else's key.
        owned_native = str(owned.get("native_meeting_id") or "")
        if session_uid not in (owned_native, str(meeting_id)):
            raise HTTPException(status_code=403, detail="session_uid does not match this meeting")

        resume_t = _decode_sse_cursor(request.headers.get("last-event-id"))

        def gen():
            import redis

            r = redis.from_url(redis_url, decode_responses=True)
            tkey = f"tc:meeting:{meeting_id}"
            # Resume EXACTLY from the client's last-seen cursor when present (gapless reconnect);
            # otherwise seed then live-tail (fresh connect).
            last = {tkey: resume_t or "$"}
            idle = 0
            # A meeting row's transcript stream is REUSED across the meeting's sessions, so a
            # `session_end` is not necessarily the end of the VIEW: a new session can resume on the
            # same key moments later, and closing on the first marker painted "Meeting ended" over a
            # live meeting. So an end ARMS a short drain (one poll) and a `session_start` or any real
            # segment disarms it. This used to be a 45s bounded drain, because the copilot's final
            # beat ran ~10s after session_end and its notes had to reach the view first; with that
            # producer gone (PRD decision 34) the only thing that can still arrive is a resumed
            # session, and one poll is enough to see it.
            ending = False

            def cursor():
                return _encode_sse_cursor(last, tkey)

            def seg_events(payload):
                for seg in payload.get("segments", []):
                    yield ({"type": "transcript", "speaker": seg.get("speaker"),
                            "text": seg.get("text"), "t": seg.get("start"),
                            "tsMs": seg.get("abs_start_ms"),
                            "completed": seg.get("completed", True),
                            "id": seg.get("segment_id")}, cursor())

            def retract_event(payload):
                """A `retract` marker (the collector withdrew superseded/over-extended pending drafts) →
                a `retract` SSE event so the terminal drops those segment ids from the live view."""
                ids = payload.get("segment_ids") or []
                if ids:
                    yield ({"type": "retract", "segment_ids": ids}, cursor())

            if resume_t is None:   # fresh connect → seed the bounded recent transcript tail
                seed_rows = list(reversed(r.xrevrange(tkey, count=MEETING_STREAM_TRANSCRIPT_REPLAY) or []))
                for entry_id, fields in seed_rows:
                    last[tkey] = entry_id
                    payload = json.loads(fields.get("payload", "{}"))
                    if payload.get("type") == "session_end":
                        ending = True
                        # F166: NOT `last.pop(tkey, None)` — `last` holds only this one key, so
                        # popping it emptied the dict and the next `r.xread(last, ...)` (a bare
                        # `{}`) raised redis-py's `DataError: XREAD streams must be a non empty
                        # dict`, crash-looping agent-api on every re-poll of a completed meeting's
                        # stream. Reset to "$" (new-entries-only) instead: same "nothing before this
                        # point matters" semantics, but `last` is never empty going into xread.
                        last[tkey] = "$"
                        continue
                    if payload.get("type") == "retract":
                        yield from retract_event(payload)
                        continue
                    # A real segment AFTER a session_end in the replay tail means a NEW session resumed
                    # on this reused meeting-row stream (tc:meeting:{id} is shared across a meeting's
                    # sessions). The prior end must NOT close the current live view.
                    ending = False
                    yield from seg_events(payload)

            while True:
                resp = r.xread(last, count=500, block=1500 if ending else 15000)
                if not resp:
                    if ending:
                        live.drop(session_uid)  # leaves the terminal's live-meetings feed
                        yield ({"type": "meeting-end"}, cursor())
                        return
                    idle += 15000
                    if idle >= 600000:
                        return
                    yield ({"type": "ping"}, cursor())
                    continue
                idle = 0
                for stream, entries in resp:
                    for entry_id, fields in entries:
                        last[stream] = entry_id
                        if stream == tkey:
                            payload = json.loads(fields.get("payload", "{}"))
                            ptype = payload.get("type")
                            if ptype == "session_end":
                                ending = True            # a resumed session gets one poll to appear
                                last[tkey] = "$"         # F166: keep the key (see seed-loop note above)
                                break
                            if ptype == "retract":
                                yield from retract_event(payload)
                                continue
                            # A `session_start` marker — or any real segment — after a prior
                            # session_end means the meeting is LIVE again on this reused row: clear
                            # the stale arm so the drain never fires a premature meeting-end.
                            ending = False
                            if ptype == "session_start":
                                continue                 # marker only — nothing to render
                            yield from seg_events(payload)

        return StreamingResponse(
            _sse(gen()), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
