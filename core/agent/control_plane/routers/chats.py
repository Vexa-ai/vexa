"""routers/chats.py — The conversation surface: dispatch, the SSE turn, sessions, the artifact-event sink and
the routine schedule that wakes agents on a clock.

Extracted from `api.py`'s `create_app` VERBATIM: the handler bodies below are the same
bytes, with `@app.` rewritten to `@router.` and nothing else. Everything they close over
is handed in by `build()` and rebound to the name it already had, so no body needed a
single identifier changed.
"""
from __future__ import annotations

from control_plane import chat_intents
from control_plane import dispatch as dispatch_mod
from control_plane import routines as routines_mod
from control_plane import scaffolds as scaffolds_mod
from control_plane import workspace_routines as workspace_routines_mod
from control_plane.api_shared import (
    CONTEXT_SENTINEL, ChatBody, ResetBody, RoutineCreate, RoutineEnabledPatch,
    _chat_turn_head, _context_grounding, _has_custom_model_endpoint,
    _model_creds_error_message, _record_chat_turn_head, _sse, _stream_tail_id,
    _truncate_title, logger)
from control_plane.config_preflight import NOT_CONFIGURED, capability_state
from control_plane.events import event_to_invocation
from control_plane.workspace_attach import active_workspaces, shared_active_mounts
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import StreamingResponse
from jsonschema.exceptions import ValidationError
from shared import units


def build(**d) -> APIRouter:
    """The chats routes, bound to one app's dependencies."""
    router = APIRouter()
    _global_root = d['_global_root']
    _resolve_room = d['_resolve_room']
    _scaffold_is_for = d['_scaffold_is_for']
    _scaffold_view = d['_scaffold_view']
    _schedule_source = d['_schedule_source']
    dispatcher = d['dispatcher']
    invocations_url = d['invocations_url']
    mindex = d['mindex']
    redis_url = d['redis_url']
    scaffolds = d['scaffolds']
    scheduler = d['scheduler']
    sess = d['sess']
    stream_reader = d['stream_reader']
    subject_of = d['subject_of']
    wsr = d['wsr']

    @router.post("/invocations", status_code=202)
    def invocations(invocation: dict = Body(...)):
        """The dispatcher sink — any trigger source POSTs a unit.v1 dispatch here."""
        try:
            workload_id = dispatcher.dispatch(invocation)
        except ValidationError as e:  # non-conformant unit.v1 envelope — fail loud (P18)
            raise HTTPException(status_code=400, detail=f"invalid unit.v1 dispatch: {e.message}")
        return {"workload_id": workload_id}
    @router.post("/api/chat")
    def chat(body: ChatBody, request: Request):
        """A chat *now*-dispatch: spawn the isolated container, stream its Stream back as SSE.

        RESUMABLE (mirrors /api/meeting/stream): every SSE event carries an ``id:`` = the unit output
        Stream cursor. A dropped view (per-dispatch worker cold-start races the SSE, a transient proxy
        drop) reconnects with ``Last-Event-ID`` — we then RE-ATTACH to the SAME warm unit and resume the
        read from that cursor (gapless) WITHOUT dispatching a second turn. The turn was never lost (the
        worker completes + commits regardless); resume just re-shows the output the client missed."""
        if stream_reader is None:
            raise HTTPException(status_code=501, detail="stream relay not wired")
        subject = subject_of(request)  # server-derived (P20); body.subject is ignored
        session = body.session or units.DEFAULT_CHAT_SESSION
        # THE MEETING ROOM (post-meeting run). Resolved and AUTHORISED here, before anything else
        # happens on this request — a refusal must cost the caller a 403, never a partially-run turn.
        # ``room`` never comes from the body: the body names a MEETING (and may PROPOSE a narrowing);
        # every subject in the returned room came from meeting-api. See ``_resolve_room``.
        room = None
        if body.room_meeting_id:
            room = _resolve_room(request, subject, body.room_meeting_id,
                                 participants=body.room_participants,
                                 names=body.room_participant_names,
                                 speakers=body.room_speakers,
                                 read_max=body.room_read_max)
        # THE SCAFFOLD (PRD 5.5). When the turn names one and it is THIS subject's, the record
        # — not the client — decides two things: which workspaces this chat mounts, and what
        # the opening ask is. Both used to be composed client-side, and that is precisely why
        # the panel and the agent disagreed about the same click. A scaffold that is not
        # this subject's is IGNORED rather than refused: a forwarded or stale id must not be
        # able to widen anybody's mounts, and must not break their turn either.
        scaffold_view = None
        if body.scaffold_id:
            rec = scaffolds.get(body.scaffold_id)
            if rec is not None and _scaffold_is_for(rec, request, subject):
                rec = scaffolds.redeem(body.scaffold_id, subject) or rec
                try:
                    scaffold_view = _scaffold_view(rec, subject)
                except scaffolds_mod.ScaffoldError:
                    logger.warning("scaffold %s cannot be rendered (its preset is gone) — the "
                                   "turn runs without it", body.scaffold_id)
            else:
                logger.warning("scaffold %s ignored on a turn by subject=%s (unknown or not "
                               "theirs)", body.scaffold_id, subject)
        if scaffold_view is not None:
            # THE OPENING IS THE RECORD'S, SUBSTITUTED HERE. The terminal composes no text —
            # it sends the id. The facts ride in front of the ask so the agent's first turn
            # can name the meeting, the time, the room and the person's state without
            # fetching anything, and the whole block is machinery-marked so the human never
            # sees it as their own message (ledger F7).
            body = body.model_copy(update={"prompt": scaffolds_mod.turn_prompt(scaffold_view)})
        # THE INTENT'S PRESET (decision 32.2 / 35.3). Same rule as the scaffold above and for the
        # same reason: the words are admin-owned content in `_global/asks/`, the wire carries a kind
        # and its arguments, and the server is the only thing that puts the two together.
        #
        # DEGRADES, NEVER REFUSES. `preset_for` returns None for a kind this deployment does not
        # know and `read_preset` raises when the file is not there; both leave `body.prompt` — the
        # client's plain fallback sentence — exactly as it arrived. A preset library that is one
        # release behind the terminal costs the turn its phrasing, not its meaning.
        elif body.intent:
            _preset = chat_intents.preset_for(body.intent)
            if _preset:
                try:
                    _fm, _ask = scaffolds_mod.read_preset(_global_root(), _preset)
                    _text = scaffolds_mod.substitute(_ask, chat_intents.tokens_for(body.intent))
                    # THE PERSON'S OWN LINE, GUARANTEED (Vexa-ai/vexa#1593). A preset that carries
                    # `{{instruction}}` places it; one that does not gets it appended, attributed.
                    # `_global/asks/` is admin-owned and a deploy never overwrites it
                    # (`preset_library.top_up` is additive), so "the preset knows the token" is not
                    # something this route may assume — and a dropped instruction is invisible to
                    # everyone including the person who typed it.
                    _text = chat_intents.with_instruction(_text, _ask, body.intent)
                    # A SILENT KIND IS MACHINERY END TO END (decision 35). The marks ride the prompt
                    # itself — the same carrier the write-back phase uses — so `workspace_reader.
                    # history` drops this turn and every agent turn after it until the person speaks
                    # again. Nothing downstream needs a new field, and a deployment whose reader is
                    # older simply renders a marked turn it does not yet hide, rather than breaking.
                    if chat_intents.is_silent(body.intent):
                        _text = chat_intents.SILENT_PREFIX + _text
                    body = body.model_copy(update={"prompt": _text})
                except scaffolds_mod.ScaffoldError as e:
                    logger.warning("intent %s has no preset here (%s) — the turn runs on the "
                                   "client's fallback sentence", _preset, e)
        # AND A LONG ACT DOES NOT HOLD THE CHAT (Vexa-ai/vexa#1584). Create and Extend are marked
        # here, on the same carrier and for the same reason as SILENT_PREFIX above: the worker reads
        # the mark and runs the act as a background job, so the turn returns one line at once and
        # the composer stays answerable.
        #
        # OUTSIDE the preset branch on purpose. Whether this act blocks the chat must not depend on
        # whether the preset library is current — a deployment one release behind the terminal falls
        # back to the client's plainer sentence (the branch above says so), and the fallback wording
        # is exactly as long to run as the preset's. The mark rides whichever words won.
        # A deployment whose WORKER is older simply runs a marked prompt inline, as it does today.
        _job_mark = chat_intents.job_prefix(body.intent)
        if _job_mark:
            body = body.model_copy(update={"prompt": _job_mark + body.prompt})
        # A reconnect carries Last-Event-ID (the last Stream cursor the client rendered). On resume we
        # DON'T re-dispatch — we re-attach to the existing warm unit and read from the cursor onward.
        resume = request.headers.get("last-event-id") or None
        # Ground the chat in the terminal's ACTIVE meeting (if any): agent-api folds the live transcript
        # from the meeting's redis Stream (tc:meeting:{native} — the SAME stream the live view renders) into
        # the prompt, fresh on every turn. The transcript stays inside the trusted control plane and
        # rides the prompt to the worker — no file, no cross-domain HTTP, no user key in the worker (P15).
        ctx, tools, prompt = _context_grounding(
            body, session, redis_url,
            schedule_rows=lambda: _schedule_source(subject),
            workspace_mounts=lambda: (active_workspaces(wsr.root, subject)
                                      + shared_active_mounts(wsr.root, subject, mindex.list(subject))),
        )
        # Mark the grounding→user boundary. Every branch returns `<grounding> + body.prompt`, so the
        # user's words are the exact suffix; the sentinel goes right before them.
        #
        # ⚠ IT USED TO SKIP THE TURNS THAT NEEDED IT MOST. The condition carried `len(prompt) >
        # len(body.prompt)` — "only mark it when I actually folded something" — which is wrong twice
        # over: the WORKER prepends its own preambles (voice, kg-links, mount stack, entity index,
        # global context) AFTER this function returns, so a turn this function folded nothing into
        # still reaches the transcript with several screens of machinery in front of the sentence.
        # That is the exact shape of the 2026-09-02 regression: the founder's turns had no meeting,
        # no schedule and no workspace grounding, so no sentinel was written, so the terminal fell
        # through to its regexes, which no longer matched the preambles — and his whole machinery
        # prompt rendered as a grey USER bubble. A boundary marker that is present only sometimes is
        # a boundary marker nobody can rely on. Now: any turn carrying the person's words carries it.
        if body.prompt and prompt.endswith(body.prompt):
            prompt = prompt[: len(prompt) - len(body.prompt)] + CONTEXT_SENTINEL + body.prompt
        # Attribute this turn's commits to the human editor by EMAIL (gateway-injected, trusted) rather
        # than the bare subject id — the git author NAME becomes the email; the synthetic author email
        # (<subject>@vexa.local) stays for the you/member classification (workspace_reader.git_state_at).
        _email = (request.headers.get("x-user-email") or "").strip()
        inv = units.make_dispatch(
            subject=subject, trigger="message",
            start=units.entrypoint(inline=prompt), context=ctx, tools=tools,
            principal={"name": _email} if _email else None,
        )
        if resume:
            # Re-attach only — the warm unit id is deterministic from (subject, session); resume reads
            # its durable output Stream from the cursor. No new turn, no session re-title.
            unit_id = units.dispatch_id(inv)
        else:
            unit_id = units.dispatch_id(inv)
            retry_from = _chat_turn_head(redis_url, unit_id, body.turn_id) if body.turn_id else None
            if retry_from is not None:
                # No-cursor RETRY of the current turn (the stream dropped before the client saw any
                # ``id:``): re-attach from the turn's recorded start — the whole turn replays, including
                # a terminal event the worker wrote while the client was gone. NO second dispatch.
                resume = retry_from
            else:
                # Fresh turn — credential preflight FIRST (config.v1 ``model_inference``, the
                # request-path oracle): with no deployment credential AND no per-user custom
                # endpoint, the worker's claude CLI can only fail with its own "Not logged in ·
                # Please run /login" — an adapter internal that means nothing to an API consumer.
                # Refuse HERE with an actionable frame instead: no worker spawn, no ghost session
                # entry. A FAILED config lookup (None) fails OPEN — a down identity service must
                # never block a turn; the worker-side auth taxonomy still catches it cleanly.
                if capability_state("model_inference") == NOT_CONFIGURED:
                    cfg = dispatcher.resolve_model_config(subject)
                    if cfg is not None and not _has_custom_model_endpoint(cfg):
                        return StreamingResponse(
                            _sse([{"type": "error", "message": _model_creds_error_message()},
                                  {"type": "turn-complete"}]),
                            media_type="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                     "X-Unit-Id": unit_id, "X-Chat-Session": session},
                        )
                # Snapshot the out-Stream tail BEFORE dispatching and attach the reader from
                # it — attaching at ``$`` raced the worker (events written between dispatch and attach,
                # or a whole turn that finished in the gap, were invisible: the 'Reconnecting' hang).
                # The thread's Stream holds PRIOR turns too, so the snapshot (not stream start) is the
                # earliest safe attach point — the client appends and stops on any ``turn-complete``.
                start = _stream_tail_id(redis_url, units.output_topic(unit_id)) or None
                # Upsert the durable index on first use of a thread: a new thread is titled by its first
                # prompt; an existing one just bumps last_active (title preserved).
                is_new = not any(r["session"] == session for r in sess.list(subject))
                # A SCAFFOLDED chat is titled by its record, never by its opening: the opening
                # is machinery, and titling a rail row with the first 60 characters of an
                # instruction block is the same defect as painting it as the person's message.
                _title = ((scaffold_view["header"]["title"] or scaffold_view["opening_label"])
                          if scaffold_view is not None else _truncate_title(body.prompt))
                # WHAT THE RAIL NEEDS, RECORDED WHERE IT IS KNOWN (Vexa-ai/vexa#1591). The chat list
                # is derived from this index now, so a row has to carry what a row shows: the mount
                # set, the record the chat was composed from, and whether a PERSON has written here.
                #
                # `touched` is decided on the same rule the client uses — a user's own words, or an
                # act they asked for — which here is "not a scaffold opening and not a silent
                # intent". A job mark (Extend, Create) IS a touch: somebody pressed it. The client
                # could only ever see the turns typed in one browser; this sees all of them.
                sess.upsert(subject, session, title=_title if is_new else None,
                            workspaces=((scaffold_view or {}).get("workspaces") or None),
                            scaffold=({"kind": scaffold_view.get("kind"), "id": scaffold_view.get("id")}
                                      if scaffold_view is not None else None),
                            touched=(scaffold_view is None
                                     and not chat_intents.is_silent(body.intent)))
                # ``room`` applies AT SPAWN: the mount table is fixed when the container is created,
                # so a WARM unit (this thread already has a live worker) keeps the stack it booted
                # with and a room named on a later turn of the same thread does not retro-mount. The
                # post-meeting run uses its own per-meeting session, so it spawns cold and gets the
                # room; a turn that needs a different room needs a different session.
                try:
                    unit_id = dispatcher.dispatch(  # spawn-or-touch the thread's warm chat unit
                        inv, room=room,
                        scaffold_workspaces=(scaffold_view or {}).get("workspaces") or None)
                except dispatch_mod.WarmDeliveryFailed as exc:
                    # THE TURN IS REFUSED, NOT DROPPED. For a warm unit the pre-delivery is the only
                    # delivery, so a failure here means the person's words reached nobody. Answering
                    # 200 and streaming the turn already in flight is what made this invisible: they
                    # watch a reply appear and reasonably believe it is to what they just sent.
                    # 503 is deliberate — this is transient by nature (redis blip, a worker in the
                    # idle-exit race) and the honest instruction is "send it again".
                    logger.warning("chat turn refused for subject=%s session=%s: %s",
                                   subject, session, exc)
                    raise HTTPException(
                        status_code=503,
                        detail="That message did not reach your agent — nothing was lost on your "
                               "side, please send it again.") from exc
                if body.turn_id and start is not None:
                    _record_chat_turn_head(redis_url, unit_id, body.turn_id, start)
                resume = start
        return StreamingResponse(
            _sse(stream_reader.read(unit_id, resume=resume)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                     "X-Unit-Id": unit_id, "X-Chat-Session": session},
        )
    @router.post("/api/chat/reset")
    def chat_reset(body: ResetBody, request: Request):
        """Drop a conversation thread: remove it from the index AND delete its continuity file so a
        future turn on the same name starts a fresh conversation (not a resume of the old one)."""
        subject = subject_of(request)
        session = body.session or units.DEFAULT_CHAT_SESSION
        sess.drop(subject, session)
        try:
            wsr.drop_session(subject, session)
        except Exception:  # noqa: BLE001 — index drop is the contract; the file delete is best-effort
            logger.exception("dropping continuity file failed subject=%s session=%s", subject, session)
        return {"ok": True}
    @router.get("/api/sessions")
    def list_sessions(request: Request):
        """THE RAIL, FOR THIS PERSON, WHEREVER THEY SIGN IN (Vexa-ai/vexa#1591).

        Most-recently-active first. Each row is `session` · `title` · `created` · `last_active` and,
        since the rail started deriving from here, `workspaces` · `scaffold` (`{kind, id}` or null) ·
        `touched`. The four original names are unchanged, so every existing consumer reads what it
        always read; the client's own `meeting` ref is NOT here on purpose — `meet-<row>` is the
        terminal's own naming of a meeting's session, and one convention with one owner beats a
        second copy of it on the wire."""
        return {"sessions": sess.list(subject_of(request))}
    @router.get("/api/sessions/{session}/history")
    def session_history(session: str, request: Request):
        """The session's prior conversation, as simplified turns the terminal can render (so clicking a
        saved chat re-opens its history). Tolerant: a missing/empty transcript returns ``{turns: []}``;
        an invalid subject/session never 500s."""
        subject = subject_of(request)
        # The turn's cwd FOLLOWS the active set (flat model), so a thread's continuity may sit under
        # any currently-mounted workspace dir — hand the reader those candidates. Best-effort: a
        # failing mount resolution only narrows the search to _system + home.
        extra: list = []
        try:
            ms = active_workspaces(wsr.root, subject) + shared_active_mounts(wsr.root, subject, mindex.list(subject))
            extra = [m.path for m in ms]
        except Exception:  # noqa: BLE001
            logger.warning("mount resolution for history failed subject=%s — searching anchored roots only", subject)
        try:
            turns = wsr.history(subject, session, extra_roots=extra)
        except Exception:  # noqa: BLE001 — history is best-effort; a bad path → empty, never an error
            logger.exception("loading session history failed subject=%s session=%s", subject, session)
            turns = []
        return {"turns": turns}
    @router.post("/api/routines", status_code=201)
    def create_routine(body: RoutineCreate, request: Request):
        if scheduler is None or not invocations_url:
            raise HTTPException(status_code=501, detail="scheduler not wired")
        try:
            routine = routines_mod.make_routine(
                subject=subject_of(request), name=body.name, cron=body.cron, prompt=body.prompt,
            )
            job_spec = routines_mod.compile_to_job(routine, invocations_url=invocations_url)
        except (ValueError, ValidationError) as e:  # bad cron form / non-conformant routine — fail loud
            raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))
        job = scheduler.schedule(job_spec)
        ran_now = False
        if body.run_now:
            # Fire one immediate run via the dispatcher (no HTTP hop) so the author sees a result now.
            try:
                dispatcher.dispatch(job_spec["request"]["body"])
                ran_now = True
            except Exception:  # noqa: BLE001 — the routine is still scheduled even if the demo run fails
                ran_now = False
        return {"routine": routine, "job_id": job.get("job_id"), "ran_now": ran_now}
    @router.get("/api/routines")
    def list_routines(request: Request):
        if scheduler is None:
            return {"routines": []}
        cards = workspace_routines_mod.routine_cards_for_subject(
            subject_of(request),
            jobs=scheduler.list_jobs(limit=1000),
            workspaces_dir=wsr.root,
        )
        return {"routines": cards}
    @router.patch("/api/routines/{name}/enabled")
    def set_routine_enabled(name: str, body: RoutineEnabledPatch, request: Request):
        if scheduler is None or not invocations_url:
            raise HTTPException(status_code=501, detail="scheduler not wired")
        subject = subject_of(request)
        try:
            workspace_routines_mod.set_routine_file_enabled(
                subject,
                name,
                enabled=body.enabled,
                workspaces_dir=wsr.root,
            )
            result = workspace_routines_mod.reconcile_workspace_routines(
                subject,
                scheduler=scheduler,
                invocations_url=invocations_url,
                workspaces_dir=wsr.root,
            )
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="unknown routine")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {
            "ok": True,
            "name": name,
            "enabled": body.enabled,
            "reconcile": result.__dict__,
        }
    @router.delete("/api/routines/{routine_id}")
    def delete_routine(routine_id: str, request: Request):
        if scheduler is None:
            raise HTTPException(status_code=501, detail="scheduler not wired")
        subject = subject_of(request)
        for job in scheduler.list_jobs():
            meta = job.get("metadata") or {}
            if meta.get("routine_id") == routine_id and meta.get("owner") == subject:
                scheduler.cancel_job(job["job_id"])
                return {"ok": True, "routine_id": routine_id}
        raise HTTPException(status_code=404, detail="unknown routine")
    @router.post("/events", status_code=202)
    def events(event: dict = Body(...)):
        try:
            invocation = event_to_invocation(event)
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=f"invalid event.v1: {e.message}")
        except ValueError as e:  # no plan carried — fail loud (P18)
            raise HTTPException(status_code=422, detail=str(e))
        workload_id = dispatcher.dispatch(invocation)
        return {"workload_id": workload_id, "trigger": invocation["trigger"]}

    return router
