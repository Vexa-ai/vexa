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
from control_plane import meeting_mint as meeting_mint_mod
from control_plane import routines as routines_mod
from control_plane import scaffolds as scaffolds_mod
from control_plane import workspace_routines as workspace_routines_mod
from control_plane.api_shared import (
    CONTEXT_SENTINEL, ChatBody, ResetBody, RoutineCreate, RoutineEnabledPatch,
    _chat_turn_head, _context_grounding, _has_custom_model_endpoint,
    _model_creds_error_message, _record_chat_turn_head, _sse, _stream_tail_id,
    logger, meeting_binding, workspace_focus)
from control_plane.config_preflight import NOT_CONFIGURED, capability_state
from control_plane.events import event_to_invocation
from control_plane.workspace_attach import active_workspaces, shared_active_mounts
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import StreamingResponse
from jsonschema.exceptions import ValidationError
from shared import chat_label as chat_label_mod
from shared import units

#: How the terminal names a meeting's own agent session — `meet-<row id>`. The `/api/sessions`
#: docstring below has always said so; #1602 is the first thing on this side to READ it, because a
#: chat born as a meeting's is named by that meeting (and one that merely CREATED a meeting is not —
#: Vexa-ai/vexa#1597).
_MEET_SESSION_PREFIX = "meet-"


def build(**d) -> APIRouter:
    """The chats routes, bound to one app's dependencies."""
    router = APIRouter()
    _global_root = d['_global_root']
    _meeting_note_recorder = d['_meeting_note_recorder']
    _meeting_owner_lookup = d['_meeting_owner_lookup']
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

    # ── THE RAIL'S NAME FOR A ROW (Vexa-ai/vexa#1602) ────────────────────────────────────────
    #
    # The founder's rail, 2026-09-06 12:50Z: four rows reading `Active context: the u…`, plus
    # `[vexa-job:extend…`, `[minutes-review…` and `[prep] They click…`. A row was labelled with the
    # session's first user text, and the first user text of most sessions is machinery.
    #
    # `shared/chat_label.py` holds the RULE and reads nothing; these three resolve the facts it is
    # given — the ask library, the meetings domain — because those live behind this control plane.

    def _preset_label(name: str, cache: "dict[str, str]") -> str:
        """`label:` from an ask's frontmatter, memoised for this request.

        BEST-EFFORT BY CONSTRUCTION: a name this library does not hold has no label, the rule falls
        through to its next clause, and nobody's rail 500s because a preset was renamed."""
        if not name:
            return ""
        if name not in cache:
            try:
                fm, _ = scaffolds_mod.read_preset(_global_root(), name)
                cache[name] = str(fm.get("label") or "")
            except scaffolds_mod.ScaffoldError:
                cache[name] = ""
        return cache[name]

    def _meeting_titles(subject: str, wanted: "set[str]") -> "dict[str, str]":
        """`{row id: the title a person gave it}`, for the meetings the rail actually needs.

        LAZY, AND NEVER FATAL. No row is a meeting's ⇒ no lookup at all; `_schedule_source` is
        TTL-cached and returns `[]` rather than raising, so a meetings domain that is down costs the
        rail its meeting names and nothing else. Only `data.title` — the title a PERSON gave the
        meeting — is a name; the platform-and-code fallback is a rendering each client already has."""
        if not wanted:
            return {}
        out: "dict[str, str]" = {}
        try:
            rows = _schedule_source(subject) or []
        except Exception:  # noqa: BLE001 — a row's name is furniture; the rail outranks it
            logger.warning("meeting titles for the rail could not be read for subject=%s", subject)
            return out
        for r in rows:
            rid = str((r or {}).get("id") or "")
            if rid not in wanted:
                continue
            data = r.get("data") if isinstance(r.get("data"), dict) else {}
            title = str((data or {}).get("title") or "")
            if title:
                out[rid] = title
        return out

    def _labelled(subject: str, rows: "list[dict]") -> "list[dict]":
        """Every session row with the `label` a client should show it under.

        COMPUTED HERE RATHER THAN IN EACH CLIENT, which is the whole point: the rail, a second
        window and anything else reading this route agree by construction instead of by three
        implementations of one rule. `title` is untouched — it is what the index stores, every
        existing consumer still reads what it always read, and `label` is what it MEANS.

        The scaffold clause reads the `[kind]` an ask's body opens with rather than asking the
        scaffold store, and that is deliberate: a row minted with its record's id already carries
        that record's title (the mint path below writes it), while a row minted BEFORE the terminal
        rode the id onto the first turn carries nothing but the composed opening — and the opening
        names its own ask in its first bracket. One read of a small file per distinct ask, memoised,
        against one store round-trip per row for an answer that is already in the title."""
        wanted = {str(r.get("session") or "")[len(_MEET_SESSION_PREFIX):]
                  for r in rows
                  if str(r.get("session") or "").startswith(_MEET_SESSION_PREFIX)}
        titles = _meeting_titles(subject, wanted)
        cache: "dict[str, str]" = {}
        out: "list[dict]" = []
        for r in rows:
            sid, title = str(r.get("session") or ""), str(r.get("title") or "")
            meeting_title = (titles.get(sid[len(_MEET_SESSION_PREFIX):], "")
                             if sid.startswith(_MEET_SESSION_PREFIX) else "")
            out.append({**r, "label": chat_label_mod.chat_label(
                title, meeting_title=meeting_title,
                scaffold_label=_preset_label(chat_label_mod.preset_kind(title), cache))})
        return out

    def _mint_meeting_page(subject: str, meeting_id: str) -> None:
        """THE MEETING DOC EXISTS FROM THE SEND (Vexa-ai/vexa#1601).

        Founder, 2026-09-06, in a live Meet he had started from a chat, transcript pinned beside it
        and nothing on the right: *"where is it?"*. The page was written when the call ENDED
        (`drop_to_attendees`), so #1598's one-page room had no page to be for the whole meeting.

        BEFORE THE EVENT IS YIELDED, and that ordering is the feature. The client binds off this
        same `artifact` and immediately asks `/api/meeting/note` where the page is; minting after
        the yield would race its own consumer and answer `null` on the send that just created it.

        IT COSTS ONE ROW LOOKUP INSIDE A LIVE SSE, which `_binding_watch` declines to spend on
        re-checking ownership — and the difference is what is being bought. A session-index write
        needs no facts; a page on a DESK needs the meeting's title, day and native id, and there is
        nowhere else to read them. The lookup fails closed and this returns.

        NEVER FATAL, exactly as the binding is: the turn is what the person is waiting for."""
        try:
            row = _meeting_owner_lookup(subject, meeting_id)
            if row is None:
                logger.warning("meeting %s not readable for subject=%s — its page is not minted "
                               "here; the flow writes one when the meeting ends", meeting_id, subject)
                return
            out = meeting_mint_mod.mint(wsr.root, subject, row, record=_meeting_note_recorder)
            logger.info("meeting %s page for subject=%s: %s (%s)", meeting_id, subject,
                        out.get("path"), "minted" if out.get("created") else "already there")
        except Exception:  # noqa: BLE001 — the turn outranks its own furniture
            logger.exception("minting the page for meeting %s (subject=%s) failed",
                             meeting_id, subject)

    def _binding_watch(events, subject: str, session: str):
        """Pass the turn's events through, and record what the turn made this chat BE — the meeting
        any send in it created (Vexa-ai/vexa#1597), and any workspace it created
        (Vexa-ai/vexa#1603).

        THE TURN'S OWN STREAM IS WHERE THIS IS KNOWN. `bot_send` is served by the vexa MCP, which is
        stateless by design and has never been told which chat is calling it; the worker knows the
        result but not that a chat is a rail row; agent-api knows the subject and the session because
        it opened this response. So the one place holding both halves of *"this chat made that
        meeting"* is right here, on the way past.

        A READ, NOT A REROUTE. Every event still reaches the client, byte-for-byte and in order —
        the client binds off the same event for the render it is doing now, and this is what makes
        the binding survive a reload, a second window and a second machine.

        NO OWNERSHIP RE-CHECK, deliberately. The row came back from a `bot_send` this subject's own
        worker made with this subject's own credential, so re-asking meeting-api would add latency
        inside a live SSE and no authority. Every READ of a meeting is owner-scoped where it matters
        regardless — `/api/meeting/note` and `/api/meeting/stream` both refuse a row this caller does
        not own, whatever a session record says.

        AND THE MEETING'S PAGE IS MINTED HERE TOO (Vexa-ai/vexa#1601) — see `_mint_meeting_page`.
        The chat is the meeting's chat from this event, so the meeting's document exists from it as
        well: one send, one row, one page, and the room opens it pinned in the same turn.

        AND THE SAME SEAM PUTS A CREATED WORKSPACE IN THE CHAT'S FOCUS (Vexa-ai/vexa#1603), for the
        same reason and with the same discipline. `workspace_new` is served by the vexa MCP, which
        knows nothing of chats; the worker knows the result but not that it is in one; agent-api
        opened this response and holds the subject and the session. So this is again the one place
        that holds both halves of *"this chat made that place"* — and making it a second writer
        somewhere else is what would let the chip, the record and the mount disagree.

        ONE WRITER, TWO READERS: the event still reaches the client byte-for-byte, which is what
        updates the chip and mounts the panel NOW; this write is what makes the focus survive the
        reload, the second window and — through the session's mount generation — the next turn's
        container.

        NEVER FATAL. The binding is furniture; the turn is what the person is waiting for, so a
        failed index write is logged and the stream goes on."""
        for item in events:
            ev = item[0] if isinstance(item, tuple) else item
            bound = meeting_binding(ev)
            if bound is not None:
                try:
                    sess.upsert(subject, session, meeting=bound[0], meeting_native=bound[1] or None)
                except Exception:  # noqa: BLE001 — the turn outranks its own bookkeeping
                    logger.exception("binding meeting %s to subject=%s session=%s failed",
                                     bound[0], subject, session)
                _mint_meeting_page(subject, bound[0])
            focused = workspace_focus(ev)
            if focused is not None:
                try:
                    sess.add_workspace(subject, session, focused)
                except Exception:  # noqa: BLE001 — the turn outranks its own bookkeeping
                    logger.exception("focusing workspace %s on subject=%s session=%s failed",
                                     focused, subject, session)
            yield item

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
        # The intent preset's own frontmatter, kept for the row's NAME (Vexa-ai/vexa#1602) — the
        # loop below reads it to substitute the ask, and `label:` is the same record's answer to
        # "what did this person open".
        preset_fm = None
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
            # MOST SPECIFIC FIRST (Vexa-ai/vexa#1598). `presets_for` returns a CHAIN — the meeting-doc
            # variant of Extend, then plain Extend — and the first that reads wins. A library that
            # predates the variant therefore degrades to the ordinary ask rather than all the way to
            # the client's fallback sentence: `_global/asks/` is admin-owned and top-up is additive,
            # so "the file is there" is not something this route may assume of any preset.
            for _preset in chat_intents.presets_for(body.intent):
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
                    preset_fm = _fm
                    # A SILENT KIND IS MACHINERY END TO END (decision 35). The marks ride the prompt
                    # itself — the same carrier the write-back phase uses — so `workspace_reader.
                    # history` drops this turn and every agent turn after it until the person speaks
                    # again. Nothing downstream needs a new field, and a deployment whose reader is
                    # older simply renders a marked turn it does not yet hide, rather than breaking.
                    if chat_intents.is_silent(body.intent):
                        _text = chat_intents.SILENT_PREFIX + _text
                    body = body.model_copy(update={"prompt": _text})
                    break
                except scaffolds_mod.ScaffoldError as e:
                    logger.warning("intent preset %s is not in this library (%s) — trying the next "
                                   "in the chain, and the client's fallback sentence after that",
                                   _preset, e)
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
        # THE CHAT'S MOUNT GENERATION rides the dispatch (Vexa-ai/vexa#1603). An agent-api routing
        # hint, exactly like `context.session` beside it: `dispatch_id` reads it off the in-memory
        # dispatch and `_without_chat_session` strips it before the sealed unit.v1 check. It is what
        # makes the turn AFTER a `workspace_create` address a fresh warm unit — and therefore be
        # spawned with a mount table that has the new workspace in it, read-write, like every other
        # workspace in the focus. Generation 0 changes no id, so nothing that never created a
        # workspace is disturbed. Fail-soft: an index that cannot answer costs the cold start, never
        # the turn.
        #
        # A RESUME READS, A FRESH TURN TAKES. Moving the id under a turn that is already streaming
        # would strand its own reconnect on a unit nobody spawned, so a reconnect (`Last-Event-ID`)
        # reads the generation as it stands and re-attaches to the unit it was watching; only a
        # fresh turn lowers the stale-mounts flag and steps the generation.
        try:
            _gen = (sess.mount_gen(subject, session) if resume
                    else sess.take_mount_generation(subject, session))
        except Exception:  # noqa: BLE001
            logger.warning("mount generation unreadable for subject=%s session=%s — this turn keeps "
                           "the warm unit it would have used", subject, session)
            _gen = 0
        if _gen:
            ctx = {**ctx, "mount_gen": _gen}
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
                _stored = next((r for r in sess.list(subject) if r["session"] == session), None)
                is_new = _stored is None
                # A SCAFFOLDED chat is titled by its record, never by its opening: the opening
                # is machinery, and titling a rail row with the first 60 characters of an
                # instruction block is the same defect as painting it as the person's message.
                #
                # AND SO IS EVERY OTHER CHAT (Vexa-ai/vexa#1602). The `else` branch here used to be
                # `_truncate_title(body.prompt)` — the first 60 characters of whatever reached this
                # route — which is the person's sentence only when nothing composed anything in
                # front of it. It rarely is: the terminal prepends "Active context: the user is
                # viewing…", an act rides a job mark, an ask opens with its own `[kind]`. The rule
                # is `shared/chat_label.py` and it runs on the WHOLE prompt, before the cut, which
                # is the only place the person's words still exist in full.
                #
                # The ask's `label:` is passed as the scaffold clause for an intent-composed turn —
                # the same record's name for the same thing — but NOT when the turn carries a job
                # mark, because #1588 already ruled what an act is called and `Extend: <path>` says
                # more than `extend` does.
                _scaffold_label = ""
                if scaffold_view is not None:
                    _scaffold_label = (scaffold_view["header"]["title"]
                                       or scaffold_view["opening_label"] or "")
                elif preset_fm is not None and not _job_mark:
                    _scaffold_label = str(preset_fm.get("label") or "")
                _title = chat_label_mod.chat_label(body.prompt, scaffold_label=_scaffold_label)
                # WHAT THE RAIL NEEDS, RECORDED WHERE IT IS KNOWN (Vexa-ai/vexa#1591). The chat list
                # is derived from this index now, so a row has to carry what a row shows: the mount
                # set, the record the chat was composed from, and whether a PERSON has written here.
                #
                # `touched` is decided on the same rule the client uses — a user's own words, or an
                # act they asked for — which here is "not a scaffold opening and not a silent
                # intent". A job mark (Extend, Create) IS a touch: somebody pressed it. The client
                # could only ever see the turns typed in one browser; this sees all of them.
                # A MACHINERY TITLE IS NOT A TITLE. The index names a thread once, on its first
                # turn, which is exactly right for a name and exactly wrong for the rows #1602 is
                # about: they read `Active context: the u…` because the rule above did not exist
                # when they were minted. So a stored title the rule REFUSES is replaced by this
                # turn's — and a title anybody's rule accepted, including a person's own rename, is
                # still written once and never again.
                _retitle = is_new or chat_label_mod.is_machinery_label(
                    str((_stored or {}).get("title") or ""))
                sess.upsert(subject, session, title=_title if (_retitle and _title) else None,
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
            _sse(_binding_watch(stream_reader.read(unit_id, resume=resume), subject, session)),
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
        always read.

        `meeting` (+ `meeting_native`) IS here now, and was deliberately not before
        (Vexa-ai/vexa#1597). The old rule — *"`meet-<row>` is the terminal's own naming of a
        meeting's session, so it reads the ref back off the id"* — is still true and still enough
        for the meeting somebody OPENED from the rail. It says nothing about the chat that CREATED
        the meeting from itself: that chat has an ordinary `pchat-…` id, so its meeting exists
        nowhere but in this index, and without it the rail showed one meeting as two rows. Null is
        the ordinary answer, and a `meet-<row>` session still needs no field at all.

        `label` IS THE NAME (Vexa-ai/vexa#1602). `title` is what the index stored — for a row minted
        before the rule, the first 60 characters of a composed prompt — and `label` is the one rule
        applied to it: the meeting's title, the scaffold's label, the act's label, or the person's
        own first words with every machinery preamble stripped. Empty means no name is recoverable,
        never a name of ours: "Chat" is the client's placeholder and a server that shipped it would
        outrank the reader's own rename in the merge."""
        subject = subject_of(request)
        return {"sessions": _labelled(subject, sess.list(subject))}
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
