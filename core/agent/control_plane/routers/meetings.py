"""routers/meetings.py — The meeting seam: relay health, and the live transcript stream a chat renders beside
the conversation.

Extracted from `api.py`'s `create_app` VERBATIM: the handler bodies below are the same
bytes, with `@app.` rewritten to `@router.` and nothing else. Everything they close over
is handed in by `build()` and rebound to the name it already had, so no body needed a
single identifier changed.
"""
from __future__ import annotations

from control_plane.api_shared import (
    MEETING_STREAM_TRANSCRIPT_REPLAY, _decode_sse_cursor, _encode_sse_cursor, _sse)
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
import json


def build(**d) -> APIRouter:
    """The meetings routes, bound to one app's dependencies."""
    router = APIRouter()
    _meeting_owner_lookup = d['_meeting_owner_lookup']
    live = d['live']
    redis_url = d['redis_url']
    subject_of = d['subject_of']

    @router.get("/api/meeting/relay-health")
    def meeting_relay_health(request: Request):
        """P18 (ADR 0010) — the transcript relay's observable health: is the numeric→native resolve OK,
        and are segments arriving? A stale `VEXA_BOT_API_KEY` (401 on `/meetings`) shows here as a typed
        `native_resolve: {ok:false, kind:'unauthorized', detail:…}` instead of silent dead air."""
        from control_plane import transcription_watcher as _txw
        return _txw.relay_health()
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
                        last.pop(tkey, None)
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
                                last.pop(tkey, None)     # session_end is the last transcript entry
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
