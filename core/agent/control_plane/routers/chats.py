"""routers/chats.py — The conversation surface: the SSE turn and its submit-and-leave twin, the chat's
target, names and order, and sessions. The dispatch doors are `routers/ingress.py`; routines are
`routers/routines.py`.

Extracted from `api.py`'s `create_app` VERBATIM: the handler bodies below are the same
bytes, with `@app.` rewritten to `@router.` and nothing else. Everything they close over
is handed in by `build()` and rebound to the name it already had, so no body needed a
single identifier changed.
"""
from __future__ import annotations

import time

from control_plane import chat_intents
from control_plane import dispatch as dispatch_mod
from control_plane import meeting_mint as meeting_mint_mod
from control_plane import scaffolds as scaffolds_mod
from control_plane import unit_faults
from control_plane import global_layer, model_providers, system_mounts
from control_plane.api_shared import (
    CONTEXT_SENTINEL, GLOBAL_TARGET_NOTE,
    _chat_turn_head, _context_grounding, _has_custom_model_endpoint, _is_slug,
    _model_creds_error_message, _record_chat_turn_head, _sse, _stream_tail_id,
    inbox_pending, inbox_withdraw, logger, meeting_binding, target_preamble, toolbelt_preamble, workspace_focus)
from control_plane.peer_lookups import meeting_access_check
from control_plane.bodies import ChatBody, ChatModelBody, ResetBody
from control_plane.ceiling import refuse_delegated, require_in_ceiling
from control_plane.config_preflight import NOT_CONFIGURED, capability_state
from control_plane.workspace_attach import active_workspaces, shared_active_mounts
from typing import Annotated, Literal

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from fastapi.responses import JSONResponse, StreamingResponse
from shared import chat_label as chat_label_mod
from shared import unit_input, units
from shared.marks import flow_mark
from shared.runtime_fault import RuntimeFault

#: How the terminal names a meeting's own agent session — `meet-<row id>`. The `/api/sessions`
#: docstring below has always said so; #1602 is the first thing on this side to READ it, because a
#: chat born as a meeting's is named by that meeting (and one that merely CREATED a meeting is not —
#: Vexa-ai/vexa#1597).
_MEET_SESSION_PREFIX = "meet-"


class ChatNameBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session: str = Field(min_length=1, max_length=300, description='the chat session to name')
    title: str = Field(max_length=300, description='a concise 3-7 word task title')
    source: Literal['human', 'agent'] = 'human'


class AgentChatNameBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session: str = Field(min_length=1, max_length=300,
                         description='the current chat session, as the turn context states it')
    title: str = Field(max_length=300, description='a concise 3-7 word title naming the actual objective')


class ChatOrderBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    order: list[Annotated[str, StringConstraints(min_length=1, max_length=300)]] = Field(max_length=5000)


def build(**d) -> APIRouter:
    """The chats routes, bound to one app's dependencies."""
    router = APIRouter()
    _global_root = d['_global_root']
    _meeting_note_recorder = d['_meeting_note_recorder']
    _meeting_owner_lookup = d['_meeting_owner_lookup']
    _read_target = d['_read_target']
    _resolve_room = d['_resolve_room']
    _scaffold_is_for = d['_scaffold_is_for']
    _scaffold_view = d['_scaffold_view']
    _schedule_source = d['_schedule_source']
    dispatcher = d['dispatcher']
    mindex = d['mindex']
    redis_url = d['redis_url']
    scaffolds = d['scaffolds']
    sess = d['sess']
    settings = d['settings']

    def _refuse_delegated(request: Request) -> None:
        """A chat turn is started by a person, never by a worker acting for one: a delegated
        identity, whatever its regime, dispatches nothing here."""
        refuse_delegated(request, reason="delegated_dispatch",
                         instruction="A chat turn is started by the person, not by an agent acting for "
                                     "them. Say what you would ask and stop; do not retry it another way.")

    def _unit_key(unit_id: str) -> str:
        """The unit's input key, the one the dispatcher signs that unit's entries with."""
        secret = settings.internal_api_secret if settings is not None else None
        return unit_input.unit_key(secret.get_secret_value() if secret else "", unit_id)

    def _toolbelt_configured() -> bool:
        return bool(settings is not None and (settings.mcp_url or "").strip()
                    and settings.mcp_delegation_secret.get_secret_value())
    stream_reader = d['stream_reader']
    subject_of = d['subject_of']
    workspace_registry = d['workspace_registry']
    wsr = d['wsr']
    # The same access decision the live transcript stream makes (routers/meetings.py): a chat may
    # fold a meeting's transcript only when its caller could watch that transcript.
    _meeting_access = meeting_access_check(_meeting_owner_lookup, getattr(wsr, "root", None))

    # ── THE TARGET WORKSPACE, BY NAME (Vexa-ai/vexa#1611) ────────────────────────────────────
    #
    # #1585/#1602's rule, applied to the one thing this issue puts in every prompt: a workspace is
    # named, never slugged. The registry is the one place a slug becomes a name, and it is asked
    # here rather than in `api_shared.target_preamble` so the composer stays pure and testable.

    def _ws_name(slug: str, *, fallback: str) -> str:
        """A workspace's human name, or ``fallback``. Never raises and never invents: a registry
        that cannot answer costs the line a name, not the turn."""
        try:
            rec = workspace_registry.by_slug(str(slug or ""))
        except Exception:  # noqa: BLE001 — a naming lookup is furniture
            rec = None
        return str((rec or {}).get("name") or "").strip() or fallback

    def _target_line(subject: str, session: str, target: str) -> str:
        """This turn's target line — the target's name, and the names of what it may only read.

        THE DEFAULT IS THE PERSON'S OWN DESK, and it is stated rather than left implicit: a chat
        with no target still writes somewhere, and the founder's failure was exactly a chat that
        did not say where. The desk's own registry row names it; without one it is "your own desk",
        which is what a person calls it anyway.

        `others` comes from the session's mount set, minus the system tiers nobody chooses. An
        empty set is honest — most chats have one — and `target_preamble` drops the clause."""
        try:
            row = next((r for r in sess.list(subject) if r["session"] == session), None)
        except Exception:  # noqa: BLE001 — the index is furniture; the turn is not
            row = None
        mounts = [str(w) for w in ((row or {}).get("workspaces") or [])
                  if str(w) not in ("_global", "_system")]
        desk = _ws_name(str(subject), fallback="your own desk")
        # The registry names `_global` too — `The organisation` until the setup conversation
        # writes the company's own — so this fallback is only reached when the lookup FAILS, and
        # its whole job is to keep the slug out of the one line #1585/#1602 exist to keep
        # slug-free. `Company` is what its own README calls it, and what the chip shows (#1616).
        global_target = target == system_mounts.GLOBAL_SLUG
        target_name = _ws_name(target, fallback="Company" if global_target else target) if target else desk
        others = [_ws_name(w, fallback=w) for w in mounts if w != target]
        # THE DESK IS ALWAYS READABLE, and saying so is the other half of the founder's rule — the
        # one that stops the fix from becoming a new failure. "Note this on my desk" must still
        # work from a chat targeting a customer's workspace; a line that named only the target
        # would teach the agent it has nowhere else to write.
        if target and desk not in others:
            others.append(desk)
        # WHAT THE COMPANY LAYER IS FOR, said to the one turn that can write there (#1616). An
        # admin can now aim any chat at `_global`; a turn told only "writes go here" would fill
        # the organisation tier with meeting notes, which its own README says it is not for.
        return target_preamble(target_name, others,
                               note=GLOBAL_TARGET_NOTE if global_target else "")

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
            out.append({**r, "label": r.get("named_title") or chat_label_mod.chat_label(
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

        THE TURN'S OWN STREAM IS WHERE THIS IS KNOWN. `request_meeting_bot` is served by the vexa MCP, which is
        stateless by design and has never been told which chat is calling it; the worker knows the
        result but not that a chat is a rail row; agent-api knows the subject and the session because
        it opened this response. So the one place holding both halves of *"this chat made that
        meeting"* is right here, on the way past.

        A READ, NOT A REROUTE. Every event still reaches the client, byte-for-byte and in order —
        the client binds off the same event for the render it is doing now, and this is what makes
        the binding survive a reload, a second window and a second machine.

        NO OWNERSHIP RE-CHECK, deliberately. The row came back from a send this subject's own
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

        …AND IT BECOMES THE ONE THIS CHAT WRITES TO (Vexa-ai/vexa#1611). A `focus` event says *"this
        workspace is where this conversation is working"*, which is two consequences of one fact: it
        joins the mount set (`add_workspace`) and it becomes the target (`set_target`). That is why
        `workspace_target` — the verb an agent calls when the person says *"work in the Example Bank
        workspace"* — emits the SAME event rather than a second kind: a workspace already in the set
        adds nothing and moves the target, a brand-new one does both, and there is one vocabulary
        for "where are we working" instead of two that can drift apart.

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
                    sess.set_target(subject, session, focused)
                except Exception:  # noqa: BLE001 — the turn outranks its own bookkeeping
                    logger.exception("focusing workspace %s on subject=%s session=%s failed",
                                     focused, subject, session)
            yield item

    @router.post("/api/chat")
    def chat(body: ChatBody, request: Request):
        """A chat *now*-dispatch: spawn the isolated container, stream its Stream back as SSE.

        RESUMABLE (mirrors /api/meeting/stream): every SSE event carries an ``id:`` = the unit output
        Stream cursor. A dropped view (per-dispatch worker cold-start races the SSE, a transient proxy
        drop) reconnects with ``Last-Event-ID`` — we then RE-ATTACH to the SAME warm unit and resume the
        read from that cursor (gapless) WITHOUT dispatching a second turn. The turn was never lost (the
        worker completes + commits regardless); resume just re-shows the output the client missed."""
        return _chat(body, request, stream=True)

    @router.post("/api/chat/submit")
    def chat_submit(body: ChatBody, request: Request):
        """SUBMIT WITHOUT WATCHING — the same turn, delivered, with no SSE (Vexa-ai/vexa#1610).

        The founder, dropping acts onto a page while a job ran: *"i drop new tasks to that chat, can
        i be sure everything submitted there is actually processed?"* One half of why the answer was
        no: the terminal could open exactly one stream, so anything typed or pressed mid-turn was
        held in THAT BROWSER's localStorage until the turn ended. Another device never saw it; a
        cleared browser never sent it; and nothing anywhere recorded that it existed.

        This route is the fix, and it is deliberately not a new pipeline: it runs the identical
        composition and dispatch `/api/chat` runs — the same presets, the same marks, the same
        grounding, the same session index, the same pre-delivery onto the in-topic — and then
        returns instead of streaming. The submission is on the server from the moment it is made,
        the worker will take it in order like any other, and the answer carries the session's PENDING
        LIST so the chat draws its queued rows from the server's view rather than from what one
        browser remembers.

        The browser keeps at most an unsent copy across the POST itself, for a network gap, and
        clears it on this response."""
        return _chat(body, request, stream=False)

    @router.get("/api/chat/pending")
    def chat_pending(request: Request, session: str | None = None):
        """WHAT THIS CHAT HAS SUBMITTED AND ITS AGENT HAS NOT TAKEN YET (Vexa-ai/vexa#1610).

        The inbox is the in-topic and the worker publishes how far it has read, so this is a read of
        two facts neither of which this route owns. That is what makes a reload, a second device and
        a swapped terminal container show the SAME pending list: none of them is remembering it.

        `cursor` is the output Stream's tail right now — where a client with nothing open should
        attach to watch the queue run. A client that already has a stream has an exact cursor of its
        own and should use that one instead."""
        subject = subject_of(request)
        session = session or units.DEFAULT_CHAT_SESSION
        try:
            gen = sess.mount_gen(subject, session)
        except Exception:  # noqa: BLE001 — an unreadable generation is the id it always had
            gen = 0
        unit_id = units.chat_unit_id(subject, session, gen)
        pending = inbox_pending(redis_url, unit_id, _unit_key(unit_id))
        # WHY THE QUEUE IS NOT MOVING, when something is (P18): the typed fault each blocked row
        # already carries, once more at the top for a client that draws one banner, not N rows.
        blocked = next((p["blocked"] for p in pending if p.get("blocked")), None)
        return {"pending": pending,
                "cursor": _stream_tail_id(redis_url, units.output_topic(unit_id)) or "",
                **({"fault": blocked} if blocked else {})}

    def _chat(body: ChatBody, request: Request, *, stream: bool):
        _refuse_delegated(request)
        if stream_reader is None:
            raise HTTPException(status_code=501, detail="stream relay not wired")
        subject = subject_of(request)  # server-derived (P20); body.subject is ignored
        session = body.session or units.DEFAULT_CHAT_SESSION
        # THE PERSON'S OWN WORDS, CAPTURED BEFORE ANYTHING COMPOSES OVER THEM (Vexa-ai/vexa#1610).
        # Every branch below may replace `body.prompt` — with a scaffold's opening, a preset's ask, a
        # mark — and a queued row has to say what the person submitted, not what the server made of
        # it. One read, at the top, where the sentence still exists.
        _submitted = body.prompt
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
        # …AND THE TWO FACTS A QUEUED ROW NEEDS ABOUT AN ACT (Vexa-ai/vexa#1610): what was pressed
        # and on what. Read from the SAME functions the mark is composed from, so the row a person
        # counts and the job the worker refuses-or-queues name the same thing by construction.
        _intent_kind = str((body.intent or {}).get("kind") or "").strip().lower()
        _intent_target = chat_intents.act_target(body.intent) if body.intent else ""
        # AND A TURN NOBODY TYPED NEVER RENDERS AS THEIR WORDS (Vexa-ai/vexa#1605). The founder
        # opened a held meeting's chat and read the whole `process-meeting` kick back as his own
        # grey bubble: a FLOW composed that turn, in another process, and it reached this route
        # carrying nothing that said so. The mark is what makes the label derivable from the RECORD
        # instead of guessed from prose, and it is written HERE for the same reason the job mark and
        # SILENT_PREFIX are — a caller able to compose the marks could compose any of them.
        #
        # THE ORDER IS THE PRECEDENCE. A job mark already says everything an act mark would and
        # #1588 ruled what it renders as. A SCAFFOLDED opening is machinery-marked by
        # `scaffolds.turn_prompt` and the client hides that bubble whole, so it is left alone: two
        # marks on one turn would be two answers to one question.
        _turn_mark = _job_mark or ("" if scaffold_view is not None else (
            chat_intents.act_prefix(body.intent)
            or flow_mark(request.headers.get("x-vexa-flow") or "",
                         request.headers.get("x-vexa-flow-step") or "")))
        if _turn_mark:
            body = body.model_copy(update={"prompt": _turn_mark + body.prompt})
        # A reconnect carries Last-Event-ID (the last Stream cursor the client rendered). On resume we
        # DON'T re-dispatch — we re-attach to the existing warm unit and read from the cursor onward.
        #
        # A SUBMISSION IS NEVER A RECONNECT (Vexa-ai/vexa#1610): it exists to put something new on
        # the server, so it takes the dispatch path whatever a stale header says.
        resume = (request.headers.get("last-event-id") or None) if stream else None
        # Ground the chat in the terminal's ACTIVE meeting (if any): agent-api folds the live transcript
        # from the meeting's redis Stream (tc:meeting:{row} — the SAME stream the live view renders) into
        # the prompt, fresh on every turn. The transcript stays inside the trusted control plane and
        # rides the prompt to the worker — no file, no cross-domain HTTP, no user key in the worker (P15).
        # The meeting the client names is checked with the live stream's own access decision before
        # anything is folded; a meeting this caller cannot read grounds nothing.
        ctx, tools, prompt = _context_grounding(
            body, session, redis_url,
            schedule_rows=lambda: _schedule_source(subject),
            workspace_mounts=lambda: (active_workspaces(wsr.root, subject)
                                      + shared_active_mounts(wsr.root, subject, mindex.list(subject))),
            meeting_access=lambda meeting_id: _meeting_access(subject, meeting_id),
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
        # Read ONCE, fail-soft, and used twice: it names the target in the prompt below and it is
        # what `build_unit_env` points the cwd and the delegation token at. An index that cannot
        # answer costs the turn its target line, never the turn.
        try:
            _target = sess.target(subject, session)
        except Exception:  # noqa: BLE001
            logger.warning("target workspace unreadable for subject=%s session=%s — this turn "
                           "writes where it always did", subject, session)
            _target = ""
        # THE CHAT'S MODEL — its own pick from the operator's catalog, "" for the person's
        # default. Read once, fail-soft like the target: an index that cannot answer costs the
        # turn its pick, and the dispatch runs it on the person's default.
        try:
            _model = sess.model(subject, session)
        except Exception:  # noqa: BLE001
            logger.warning("chat model unreadable for subject=%s session=%s — this turn runs on "
                           "the person's default", subject, session)
            _model = ""
        # THE TARGET WORKSPACE, IN FRONT OF THE ASK (Vexa-ai/vexa#1611). Every turn, including the
        # ones nobody typed — a flow's kick and a routine's wake write somewhere too, and the
        # founder's failure was a turn that did not know where. It goes in FRONT of the grounding
        # and therefore in front of the sentinel below, so the person's half stays exactly their
        # words (F47): this is machinery, and machinery never renders as somebody's speech.
        prompt = _target_line(subject, session, _target) + prompt
        # The naming ask and the clock name TOOLS, so they ride only a turn whose worker gets the
        # toolbelt that serves them (`chat_name` and `current_time` on the assembled MCP — the
        # `worker_toolbelt` capability). A turn without one is never told to call something it
        # does not have.
        if _toolbelt_configured():
            prompt = toolbelt_preamble(session) + prompt
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
                # A MODEL CATALOG CARRIES ITS OWN CREDENTIALS: every provider's secret_ref was
                # resolved at boot, so the deployment-credential question is not this turn's.
                if (capability_state("model_inference") == NOT_CONFIGURED
                        and dispatcher.catalog.empty):
                    cfg = dispatcher.resolve_model_config(subject)
                    if cfg is not None and not _has_custom_model_endpoint(cfg):
                        if not stream:
                            # A SUBMISSION THAT CANNOT RUN IS REFUSED OUT LOUD. It has no stream to
                            # fold the frame into, and answering 200 would put a queued row on
                            # screen for work nothing will ever take.
                            raise HTTPException(status_code=503,
                                                detail=_model_creds_error_message())
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
                # A RETRY UNDER A ROW'S OWN ID RUNS ONCE (P18). A submission a runtime fault blocked
                # is still on the inbox; the client retries it under the same id, so the held copy
                # is withdrawn before the new one goes on — the next worker runs it once, not twice.
                if body.turn_id:
                    inbox_withdraw(redis_url, unit_id, _unit_key(unit_id), body.turn_id)
                try:
                    unit_id = dispatcher.dispatch(  # spawn-or-touch the thread's warm chat unit
                        inv, room=room,
                        scaffold_workspaces=(scaffold_view or {}).get("workspaces") or None,
                        # WHERE THIS CHAT WRITES (Vexa-ai/vexa#1611) — a dispatcher argument like
                        # `room` and `scaffold_workspaces`, never a field of the sealed unit.v1
                        # envelope, and never anything a request body asserted. It decides the
                        # turn's cwd and rides the delegation token as the tools' default `slug`.
                        target=_target,
                        # WHICH MODEL — the chat's pick, a catalog id or "" — resolved through the
                        # provider port inside the dispatch, the one place a route is decided.
                        model=_model,
                        # WHAT A QUEUED ROW SHOWS (Vexa-ai/vexa#1610), stamped on the inbox entry
                        # itself so the record a person reads and the record the worker takes are
                        # the same object. Written for every turn, not only a submission: a chat
                        # open on a second device has to be able to see the turn this one is about
                        # to run, and the row disappears the moment the worker takes it.
                        inbox={"id": body.turn_id or "", "display": _submitted,
                               "kind": _intent_kind, "target": _intent_target,
                               "at": round(time.time(), 3)})
                except model_providers.ModelChoiceFault as fault:
                    # THE CHAT'S MODEL CANNOT RUN (P18): gone from the catalog, admins-only, an own
                    # endpoint not set or not allowed, a provider secret missing. Refused before
                    # anything was spawned or delivered, as a typed fault naming the model and the
                    # provider — never a turn run on a model the person did not pick.
                    return JSONResponse(status_code=fault.http_status,
                                        content={"detail": fault.sentence(),
                                                 "fault": fault.as_dict()})
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
                except RuntimeFault as fault:
                    # THE RUNTIME SAID NO, OR WAS NOT THERE (P18). This used to climb out of here as
                    # a bare `HTTPError` and reach the person as "Internal Server Error". It is a
                    # typed refusal now: who failed (`source`), how (`kind`), a sentence that names
                    # it without repeating the runtime's own text, and what to do. The words this
                    # turn pre-delivered were withdrawn by the dispatcher, so a retry runs it once.
                    logger.warning("chat turn refused by the runtime for subject=%s session=%s: %s",
                                   subject, session, fault.kind)
                    return JSONResponse(status_code=fault.http_status,
                                        content=unit_faults.answer(fault))
                # ONLY A WATCHED TURN RECORDS THE HEAD. The record is one key per unit meaning "the
                # turn currently being streamed", and a submission is by definition not that — the
                # person is watching something else. Overwriting it would leave the streaming turn's
                # own no-cursor retry unable to recognise itself, and a retry that does not
                # recognise itself DISPATCHES A SECOND COPY. That is the failure this whole issue is
                # about, arriving from the other direction.
                if stream and body.turn_id and start is not None:
                    _record_chat_turn_head(redis_url, unit_id, body.turn_id, start)
                resume = start
        if not stream:
            # A SUBMISSION'S WHOLE ANSWER IS "IT IS ON THE SERVER NOW", plus the server's own view of
            # what is still queued — including this one until a worker takes it. The client draws its
            # rows from this list rather than from what it remembers, which is what makes a reload,
            # another device and a swapped container agree.
            return {"ok": True, "id": body.turn_id or "", "session": session, "unit": unit_id,
                    "pending": inbox_pending(redis_url, unit_id, _unit_key(unit_id)),
                    "cursor": _stream_tail_id(redis_url, units.output_topic(unit_id)) or ""}
        # AN ATTACH TO A UNIT NOTHING WILL RUN ON (P18). When the last spawn for this chat failed
        # and the worker has taken nothing since, a view that attaches would wait out its whole
        # timeout for events no worker is going to write. The relay answers with the recorded fault
        # instead — on THIS stream, composed here, because the unit's out-stream has one writer and
        # it is the worker (P23). Only when nothing has flowed past the cursor: a stream that has
        # output to give is read as usual.
        _fault = unit_faults.live_at(redis_url, unit_id) if resume else None
        if _fault is not None and (_stream_tail_id(redis_url, units.output_topic(unit_id)) or "") in (
                resume, "0-0", ""):
            return StreamingResponse(
                _sse([unit_faults.error_event(_fault), {"type": "turn-complete"}]),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                         "X-Unit-Id": unit_id, "X-Chat-Session": session},
            )
        return StreamingResponse(
            _sse(_binding_watch(stream_reader.read(unit_id, resume=resume), subject, session)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                     "X-Unit-Id": unit_id, "X-Chat-Session": session},
        )
    @router.post("/api/chat/target")
    def chat_target(request: Request, body: dict = Body(...)):
        """SET this chat's target workspace — the person clicking a chip in the header
        (Vexa-ai/vexa#1611).

        The AGENT does not come through here. When the person says *"work in the Example Bank workspace"*
        the agent calls `workspace_target`, whose result the harness turns into a `focus` event, and
        `_binding_watch` writes it on the way past — the same one writer that records a created
        workspace. Two routes to one field would be two writers, and the chip and the record would
        disagree the first time one of them lost a race.

        `workspace: ""` means the person's own desk, which is the default rather than a second name
        for it. A slug this subject cannot reach is REFUSED here rather than stored and discovered
        at the first write: the whole point of the field is that the agent may trust it."""
        subject = subject_of(request)
        session = str(body.get("session") or "").strip() or units.DEFAULT_CHAT_SESSION
        wid = str(body.get("workspace") or "").strip()
        require_in_ceiling(request, wid)
        if wid and not _is_slug(wid):
            raise HTTPException(status_code=400, detail="not a workspace slug")
        if wid == system_mounts.GLOBAL_SLUG:
            # THE COMPANY LAYER IS A TARGET FOR THE ADMIN AND NOBODY ELSE (Vexa-ai/vexa#1616).
            # `_read_target` cannot make this call: it answers `_global` to EVERY subject on
            # purpose, because the tier is mounted read-only into every worker and the read API
            # mirrors that — `write=True` never narrowed it. The admin test is the one the file
            # and entity routes already run, asked here for the same reason a target is checked
            # at all: the field exists so the agent may trust it, and a chat pointed at a
            # workspace its person cannot write is a promise the first write breaks.
            if not global_layer.is_admin(settings, str(subject)):
                raise HTTPException(status_code=403,
                                    detail="only an org admin may write into _global")
        elif wid:
            # WRITABLE, ASKED OF THE THING THAT KNOWS. `write=True` is the point: a target is where
            # writes go, so a workspace this subject may only READ is not one — the same seam, and
            # the same answer, the write route itself would give. A forwarded or stale id therefore
            # cannot point somebody's chat at a workspace they do not have.
            try:
                _read_target(request, wid, write=True)
            except HTTPException:
                raise
            except Exception:  # noqa: BLE001
                raise HTTPException(status_code=403, detail="not a workspace you can write")
        changed = sess.set_target(subject, session, wid)
        return {"ok": True, "session": session, "target": wid or None, "changed": changed}
    @router.post("/api/chat/model")
    def chat_model(body: ChatModelBody, request: Request):
        """Pick which of the deployment's models this chat runs on — the model picker.

        `model` is an id from `GET /api/models/catalog`; `""` puts the chat back on your default.
        A model the catalog does not have is refused (422), and so is one you may not use (403):
        the pick is stored only once it is one the next turn can run, because a stored pick is what
        that turn runs on. The change takes effect on the chat's next turn — a fresh worker, since
        a running one keeps the model it started with."""
        _refuse_delegated(request)
        subject = subject_of(request)
        session = str(body.session or "").strip() or units.DEFAULT_CHAT_SESSION
        mid = str(body.model or "").strip()
        catalog = dispatcher.catalog
        if mid:
            if catalog.empty:
                raise HTTPException(status_code=409, detail="this deployment offers no model "
                                                            "catalog; its model is set by the operator")
            ctx = dispatch_mod.route_context(dispatcher.resolve_model_config(subject) or {},
                                             allowlist=settings.model_allowlist)
            try:
                catalog.choose(mid, ctx, admin=lambda: dispatcher.is_admin(subject))
            except model_providers.ModelChoiceFault as fault:
                return JSONResponse(status_code=fault.http_status,
                                    content={"detail": fault.sentence(), "fault": fault.as_dict()})
        changed = sess.set_model(subject, session, mid)
        return {"ok": True, "session": session, "model": mid or None, "changed": changed}
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
    def _name(request: Request, session: str, title: str, *, human: bool) -> dict:
        subject = subject_of(request)
        title = ' '.join(title.split())
        if not title or len(title) > 100:
            raise HTTPException(422, 'Use a title between 1 and 100 characters')
        if not any(r['session'] == session for r in sess.list(subject)):
            raise HTTPException(404, 'Chat not found')
        changed = sess.name(subject, session, title, human=human)
        row = next(r for r in _labelled(subject, sess.list(subject)) if r['session'] == session)
        return {'changed': changed, 'label': row['label'], 'name_source': row.get('name_source')}

    @router.post("/api/chat/name")
    def name_chat(request: Request, body: ChatNameBody):
        """Name a chat. A name from the person (`source: human`, the default) is protected from the
        agent's renames; `source: agent` is the agent's suggestion and never overwrites one."""
        return _name(request, body.session, body.title, human=body.source != 'agent')

    @router.post("/api/chat/name/agent")
    def name_chat_as_agent(request: Request, body: AgentChatNameBody):
        """Name the current chat with a concise 3–7 word task title once its objective is clear.
        Use the current chat session supplied in the turn context. Avoid raw prompts, secrets,
        generic names and status words. Human-chosen names cannot be overwritten."""
        return _name(request, body.session, body.title, human=False)

    @router.get("/api/chat/order")
    def read_chat_order(request: Request):
        return {'order': sess.rail_order(subject_of(request))}

    @router.put("/api/chat/order")
    def save_chat_order(request: Request, body: ChatOrderBody):
        if len(body.order) != len(set(body.order)):
            raise HTTPException(422, 'Invalid chat order')
        return {'order': sess.rail_order(subject_of(request), body.order)}

    @router.get("/api/sessions")
    def list_sessions(request: Request):
        """THE RAIL, FOR THIS PERSON, WHEREVER THEY SIGN IN (Vexa-ai/vexa#1591).

        Most-recently-active first. Each row is `session` · `title` · `created` · `last_active` and,
        since the rail started deriving from here, `workspaces` · `scaffold` (`{kind, id}` or null) ·
        `touched`. The four original names are unchanged, so every existing consumer reads what it
        always read.

        `target` (Vexa-ai/vexa#1611) is the ONE of those workspaces this chat WRITES to; the rest
        are mounted to read. Null means the person's own desk — the default — and a server that
        predates the field sends null for the same reason, which is the only case where "absent"
        and "the desk" are worth being able to tell apart later.

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
        saved chat re-opens its history). A session the caller has no thread for is 404; a thread whose
        transcript is missing or empty returns ``{turns: []}``; an invalid subject/session never 500s."""
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
        # Only the caller's own trees and the workspaces their membership mounts are searched; a
        # session found nowhere there is not theirs, whoever else has a thread of that name.
        if wsr.locate_session(subject, session, extra_roots=extra) is None:
            raise HTTPException(status_code=404, detail="session not found")
        try:
            turns = wsr.history(subject, session, extra_roots=extra)
        except Exception:  # noqa: BLE001 — history is best-effort; a bad path → empty, never an error
            logger.exception("loading session history failed subject=%s session=%s", subject, session)
            turns = []
        return {"turns": turns}

    return router
