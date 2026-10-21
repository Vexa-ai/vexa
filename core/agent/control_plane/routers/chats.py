"""routers/chats.py — The conversation surface: the SSE turn and its submit-and-leave twin, the chat's
target, names and order, and sessions. The dispatch doors are `routers/ingress.py`; routines are
`routers/routines.py`.

Extracted from `api.py`'s `create_app` VERBATIM: the handler bodies below are the same
bytes, with `@app.` rewritten to `@router.` and nothing else. Everything they close over
is handed in by `build()` and rebound to the name it already had, so no body needed a
single identifier changed.
"""
from __future__ import annotations

from control_plane.workspace_reader import home_of

import hmac
import json
import re
import stat as _stat_mod
import threading
import time

from control_plane import chat_folders
from control_plane import identity_token
from control_plane import workspace_membership as _membership_mod
from control_plane import workspace_names as _names_mod
from control_plane.routers.workspace_states import apply_profile, default_target_for, first_turn_profile
from control_plane import workspace_attach as _attach_mod
from control_plane import chat_intents
from control_plane import dispatch as dispatch_mod
from control_plane import llm_budget as llm_budget_mod
from control_plane import meeting_mint as meeting_mint_mod
from control_plane import scaffolds as scaffolds_mod
from control_plane import session_store as session_store_mod
from control_plane import session_sync, unit_faults
from control_plane import admin_account_scope, global_layer, model_providers, system_mounts
from control_plane.api_shared import (
    CONTEXT_SENTINEL, GLOBAL_TARGET_NOTE,
    _chat_turn_head, _context_grounding, _has_custom_model_endpoint, _is_slug,
    _model_creds_error_message, _record_chat_turn_head, _sse, _stream_tail_id,
    inbox_pending, inbox_send_now, inbox_withdraw, stop_turns, logger, turn_outcome, turns_taken_after, running_turn_from, meeting_binding, target_preamble, toolbelt_preamble,
    pinned_pages_preamble, qualify_artifact, unit_busy, workspace_focus)
from control_plane.peer_lookups import meeting_access_check
from control_plane.bodies import CHAT_SESSION_PATTERN, ChatBody, ChatContextBody, ChatModelBody, DefaultTargetBody, ResetBody, SessionId
from control_plane.ceiling import reads_within, refuse_delegated, require_in_ceiling
from control_plane.config_preflight import NOT_CONFIGURED, capability_state
from control_plane.workspace_attach import active_workspaces, shared_active_mounts
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from fastapi.responses import JSONResponse, StreamingResponse
from shared import attachments as attachments_mod
from shared import chat_label as chat_label_mod
from control_plane import display_name as display_name_mod
from control_plane import chat_autotitle
from shared import unit_input, units
from shared.marks import flow_mark, message_mark
from shared.runtime_fault import RuntimeFault

#: How the terminal names a meeting's own agent session — `meet-<row id>`. The `/api/sessions`
#: docstring below has always said so; #1602 is the first thing on this side to READ it, because a
#: chat born as a meeting's is named by that meeting (and one that merely CREATED a meeting is not —
#: Vexa-ai/vexa#1597).
_MEET_SESSION_PREFIX = "meet-"

#: Told to a turn other people read when the meeting it is about stays out of it (`_MeetingFold`).
#: Part of the shared transcript, so it never names the meeting.
MEETING_WITHHELD_LINE = ("[The meeting this message is about is not readable by everyone in this "
                         "conversation, so its transcript is not in this turn. If the question needs "
                         "it, say you can't use it here and offer to answer in the speaker's private "
                         "chat.]\n")


class ChatNameBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session: SessionId = Field(min_length=1, description='the chat session to name')
    title: str = Field(max_length=300, description='a concise 3-7 word task title')
    source: Literal['human', 'agent'] = 'human'


class AgentChatNameBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session: SessionId = Field(min_length=1,
                               description='the current chat session, as the turn context states it')
    title: str = Field(max_length=300, description='a concise 3-7 word title naming the actual objective')


#: A RAIL ROW KEY, as the terminal writes it: ``c:<chat session>`` or ``m:<meeting id>`` (a bare
#: session id, as older clients sent, is still one). The part after the prefix is held to the same
#: bound as every session id (``bodies.CHAT_SESSION_PATTERN``), so the stored order can never carry
#: a path, a separator or anything long.
RAIL_KEY_PATTERN = r"^(?:[cm]:)?[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
RailKey = Annotated[str, StringConstraints(pattern=RAIL_KEY_PATTERN)]


class PinnedPage(BaseModel):
    """One pinned page: where it lives and what its tab says."""
    model_config = ConfigDict(extra='forbid')
    slug: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9_][\w.-]{0,127}$")
    path: str = Field(min_length=1, max_length=512, description='workspace-relative page path')
    label: str = Field(default='', max_length=200)


class PinnedPagesBody(BaseModel):
    """The person's pinned pages, in order — the whole list (#1783)."""
    model_config = ConfigDict(extra='forbid')
    pages: list[PinnedPage] = Field(max_length=50)


#: SETTINGS → APPEARANCE: the vocabulary the terminal draws (`platform/appearance.ts`). A field set to
#: null goes back to the company default (`appearance_*` in `_global/POLICIES.md`).
APPEARANCE_ACCENTS = ("clay", "blue", "indigo", "violet", "teal", "green", "amber", "rose", "graphite")
APPEARANCE_CHOICES = {
    "font_ui": ("geist", "system", "inter", "plex"),
    "font_reading": ("ui", "inter", "plex", "source-serif"),
    "font_mono": ("geist", "plex-mono", "jetbrains"),
    "density": ("comfortable", "compact"),
    "text_size": ("default", "large"),
}


def appearance_value(field: str, value) -> "str | None":
    """The value if this vocabulary knows it, else None (an unknown value is never stored)."""
    v = str(value or "").strip().lower()
    if field == "accent":
        return v if v in APPEARANCE_ACCENTS or re.fullmatch(r"#[0-9a-f]{6}", v) else None
    return v if v in APPEARANCE_CHOICES.get(field, ()) else None


class AppearanceBody(BaseModel):
    """A change to the person's appearance: the fields named change; null = the company default."""
    model_config = ConfigDict(extra='forbid')
    accent: Optional[str] = Field(default=None, max_length=16)
    font_ui: Optional[str] = Field(default=None, max_length=24)
    font_reading: Optional[str] = Field(default=None, max_length=24)
    font_mono: Optional[str] = Field(default=None, max_length=24)
    density: Optional[str] = Field(default=None, max_length=16)
    text_size: Optional[str] = Field(default=None, max_length=16)


class ChatOrderBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    order: list[RailKey] = Field(max_length=5000)


class ChatMeetingBindBody(BaseModel):
    """Bind a meeting started from a chat to that chat."""
    model_config = ConfigDict(extra='forbid')
    session: str = Field(pattern=CHAT_SESSION_PATTERN, description='the chat the meeting was started from')
    meeting: str = Field(pattern=r'^[0-9]{1,18}$', description='the meeting row id')


class ChatStopBody(BaseModel):
    """Stop this chat's running reply (its turn and every tool process under it)."""
    model_config = ConfigDict(extra='forbid')
    session: str = Field(default=units.DEFAULT_CHAT_SESSION, pattern=CHAT_SESSION_PATTERN,
                         description='the chat whose reply to stop')


class ChatSendNowBody(BaseModel):
    """Run one queued message of this chat now (the running turn stops)."""
    model_config = ConfigDict(extra='forbid')
    session: str = Field(default=units.DEFAULT_CHAT_SESSION, pattern=CHAT_SESSION_PATTERN,
                         description='the chat the queued message is in')
    id: str = Field(min_length=1, max_length=200, description="the queued row's id")


class ChatMarkBody(BaseModel):
    """Pin or hide ONE row of the caller's own rail. A flag that is absent is left as it is."""
    model_config = ConfigDict(extra='forbid')
    row: RailKey = Field(description='the rail row: `c:<chat session>` or `m:<meeting id>`')
    pinned: bool | None = Field(default=None, description='keep the row at the top of the list')
    hidden: bool | None = Field(default=None, description='take the row out of the list, reversibly')


#: A folder of the caller's own, or a built-in section: ``pinned`` · ``ungrouped`` · ``hidden``.
FolderId = Annotated[str, StringConstraints(pattern=r"^(?:pinned|shared|hidden|ungrouped|routines|f-[0-9a-f]{12})$")]


class ChatFolderCreateBody(BaseModel):
    """Make one of the person's own chat folders (or find the one they already have by that name)."""
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=60, description='the folder name the person used, e.g. "Clients"')


class ChatFolderUpdateBody(BaseModel):
    """Rename, collapse or reorder one folder. A field that is absent is left as it is."""
    model_config = ConfigDict(extra='forbid')
    folder: FolderId = Field(description='the folder id from chat_folders_list')
    name: str | None = Field(default=None, min_length=1, max_length=60, description='a new name')
    collapsed: bool | None = Field(default=None, description='fold the section shut (true) or open it (false)')
    position: int | None = Field(default=None, ge=0, le=500, description='0 puts it first among the person\'s folders')


class ChatFolderDeleteBody(BaseModel):
    """Dissolve one folder; its chats move to Ungrouped and are never deleted."""
    model_config = ConfigDict(extra='forbid')
    folder: FolderId = Field(description='the folder id from chat_folders_list')


class ChatMoveBody(BaseModel):
    """File chats in one place on the person's own rail."""
    model_config = ConfigDict(extra='forbid')
    rows: list[RailKey] = Field(min_length=1, max_length=200,
                                description='rail rows from chat_folders_list: `c:<chat session>` or `m:<meeting id>`')
    to: FolderId = Field(description='a folder id, or `pinned`, `ungrouped` or `hidden` (hiding a chat is moving it here)')
    before: RailKey | None = Field(default=None, description='place them ahead of this row (the manual order of Pinned)')


def _read_view(reader, unit_id: str, resume, *, fresh: bool):
    """The view's events, read through the ``StreamReader`` port as declared: ``fresh`` marks the
    view of a turn this request just dispatched."""
    return reader.read(unit_id, resume=resume, fresh=fresh)
# ── A MESSAGE WAITS BEHIND ITS OWN CHAT'S TURN (live app.dev 2026-10-15) ───────────────────────
# The post-meeting recap ran in the call chat's `-auto` sibling and held the conversation's head; the
# founder's "finalize the meeting" was answered 409 three times in the 52 seconds it ran. A turn of
# the SAME chat is not another participant: the person's message is queued behind it and runs the
# moment the head is free, for up to `session_sync.QUEUE_WAIT_S`, then is refused as before.
QUEUE_POLL_S = 0.5
_QUEUED: dict = {}
_QUEUED_LOCK = threading.Lock()


class QueuedTurn:
    """One message waiting for its chat's head. ``done`` is set once it was dispatched (``unit_id``
    and ``start``, the out-stream tail just before) or refused (``error``, a stream error event)."""

    def __init__(self, unit_id: str) -> None:
        self.unit_id = unit_id
        self.done = threading.Event()
        self.start: Optional[str] = None
        self.error: Optional[dict] = None


def _queue_error(exc: BaseException, session: str) -> dict:
    """What a queued message that could not be dispatched says, on the stream, typed."""
    if isinstance(exc, model_providers.ModelChoiceFault):
        return {"type": "error", "message": exc.sentence(), "fault": exc.as_dict()}
    if isinstance(exc, llm_budget_mod.BudgetExhausted):
        return {"type": "error", "message": exc.sentence(), "fault": exc.fault()}
    if isinstance(exc, RuntimeFault):
        a = unit_faults.answer(exc)
        return {"type": "error", "message": a.get("detail", ""), "fault": a.get("fault")}
    if isinstance(exc, session_sync.SessionBusy):
        a = session_sync.answer(exc.kind, session, status=409)
        return {"type": "error", "message": a["detail"], "fault": a["fault"]}
    return {"type": "error", "message": "That message did not reach your agent — nothing was lost "
                                        "on your side, please send it again."}


def queue_behind(r, busy: "session_sync.SessionBusy", unit_id: str, do_dispatch, tail, *,
                 session: str, key=None, wait_s: Optional[float] = None,
                 poll_s: float = QUEUE_POLL_S) -> QueuedTurn:
    """Wait (on a thread of its own, so a dropped stream does not drop the message) for the head of
    ``busy.session`` to be free, then ``do_dispatch()``. ``tail()`` snapshots the unit's out-stream
    right before the dispatch. Registered under ``key`` while waiting so a retry of the same turn
    attaches instead of queueing a second copy."""
    waiter = QueuedTurn(unit_id)
    deadline = time.time() + (session_sync.QUEUE_WAIT_S if wait_s is None else wait_s)

    def run() -> None:
        try:
            while time.time() < deadline:
                if session_sync.lease_free(r, busy.session):
                    start = tail()
                    try:
                        waiter.unit_id = do_dispatch() or unit_id
                    except session_sync.SessionBusy as again:
                        if again.queueable_for(unit_id):
                            time.sleep(poll_s)       # another of its own turns took it first
                            continue
                        waiter.error = _queue_error(again, session)
                        return
                    except Exception as exc:  # noqa: BLE001 — said on the stream, typed
                        logger.warning("queued chat turn for session=%s refused: %s", session,
                                       type(exc).__name__)
                        waiter.error = _queue_error(exc, session)
                        return
                    waiter.start = start
                    return
                time.sleep(poll_s)
            a = session_sync.answer("session_conflict", session, status=409,
                                    waited_s=int(session_sync.QUEUE_WAIT_S if wait_s is None else wait_s))
            a["detail"] = a["fault"]["detail"] = session_sync.QUEUE_TIMEOUT_SENTENCE
            waiter.error = {"type": "error", "message": a["detail"], "fault": a["fault"]}
        finally:
            if key is not None:
                with _QUEUED_LOCK:
                    if _QUEUED.get(key) is waiter:
                        _QUEUED.pop(key, None)
            waiter.done.set()

    if key is not None:
        with _QUEUED_LOCK:
            _QUEUED[key] = waiter
    threading.Thread(target=run, daemon=True, name=f"chat-queue-{unit_id}").start()
    return waiter


def queued_events(waiter: QueuedTurn, holder_kind: str, follow, *, tick_s: float = 5.0):
    """The stream of a queued message: what it waits behind, keepalives while it waits, then the
    turn itself (``follow(unit_id, start)``) or the typed refusal."""
    yield {"type": "queued", "holder_kind": holder_kind or "reply",
           "message": session_sync.queued_sentence(holder_kind)}
    while not waiter.done.wait(tick_s):
        yield None
    if waiter.error is not None:
        yield waiter.error
        yield {"type": "turn-complete"}
        return
    yield from follow(waiter.unit_id, waiter.start)


def queued_waiter(key) -> Optional[QueuedTurn]:
    with _QUEUED_LOCK:
        return _QUEUED.get(key)


def _queued_display(prompt: str) -> str:
    """WHAT A QUEUED ROW SAYS: the person's words, never the narration the terminal folds in front of
    them (the active-context line). The human half after the last sentinel, then the rail's own
    rule (`chat_label.human_head`); a prompt that is machinery end to end keeps its text."""
    text = str(prompt or "")
    i = text.rfind(CONTEXT_SENTINEL)
    if i >= 0:
        text = text[i + len(CONTEXT_SENTINEL):].strip()
    return chat_label_mod.human_head(text) or text


#: How long an idle incognito chat is kept before it is deleted, unless `_global/POLICIES.md` says.
INCOGNITO_RETENTION_DAYS = 7


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

    def _flow_turn(request: Request, session: str = "") -> "dict | None":
        """A ROUTINE'S RUN, posted by flows into its chat thread (ADR-0046 decision 12) — or None.

        Only the internal tier may say a turn is a routine's: it changes the turn's regime, and a
        person's own credential claiming `autonomous` would be claiming to be a schedule. The
        headers name the flow; the regime header is the switch."""
        if (request.headers.get("x-vexa-flow-regime") or "").strip().lower() != "autonomous":
            return None
        secret = settings.internal_api_secret.get_secret_value() if settings is not None else ""
        provided = request.headers.get("x-internal-secret", "")
        if not secret or not provided or not hmac.compare_digest(provided, secret):
            raise HTTPException(status_code=403, detail={
                "refused": "flow_turn_needs_internal_tier",
                "note": "a routine's turn is posted by flows over the internal tier"})
        name = (request.headers.get("x-vexa-flow-name") or "").strip()
        scope = (request.headers.get("x-vexa-flow-scope") or "").strip()
        try:
            version = int(request.headers.get("x-vexa-flow-version") or 0)
        except ValueError:
            version = 0
        turn = {"flow": name or (request.headers.get("x-vexa-flow") or "").strip(),
                "scope": scope or "instance", "version": version,
                "step": (request.headers.get("x-vexa-flow-step") or "").strip()}
        # A ROUTINE'S DELEGATED AUTHORITY (flows phase 0): the unattended verbs its person granted
        # it at creation, as flows read them off the flow version and capped by policy. Only the
        # internal tier reaches this line, so the header is flows' word, never a person's.
        grants = [g.strip() for g in (request.headers.get("x-vexa-flow-grants") or "").split(",")
                  if g.strip()]
        if grants and turn["step"] == "agent_run" and turn["scope"].startswith("user:"):
            turn["grants"] = grants
        # AN EVENT AGENT'S OCCURRENCE (ADR-0046 decision 12): the concern this chat holds.
        concern = (request.headers.get("x-vexa-concern") or "").strip()[:160]
        if concern:
            turn["concern"] = concern
        _kind = (request.headers.get("x-vexa-flow-kind") or "").strip()
        # `mail`: a turn an inbound email started (D25) — it reads, writes its reply file, proposes.
        if _kind in ("event_agent", "sink", "mail"):
            turn["kind"] = _kind
        stage = (request.headers.get("x-vexa-concern-stage") or "").strip()
        if stage:
            turn["stage"] = stage
        # ANOTHER AGENT'S MESSAGE (ADR-0046 decision 13), delivered by flows' `deliver_message`.
        sender = (request.headers.get("x-vexa-message-from-uid") or "").strip()
        if sender:
            # ACROSS TWO PEOPLE, ONLY INTO A CONVERSATION BOTH TAKE PART IN — the same rule
            # `message_agent` checked at send, held again where the turn is started.
            receiver = (request.headers.get("x-user-id") or "").strip()
            if sender != receiver:
                from control_plane.routers import agent_messages as _am  # noqa: PLC0415
                if not _am.cross_person_deliverable(session_store, sender, receiver, session):
                    raise HTTPException(status_code=403, detail=dict(_am.CROSS_PERSON_REFUSAL))
            try:
                origin = json.loads(request.headers.get("x-vexa-message-origin") or "[]")
            except ValueError:
                origin = []
            turn["message"] = {
                "uid": sender,
                "thread": (request.headers.get("x-vexa-message-from-thread") or "").strip(),
                "label": (request.headers.get("x-vexa-message-from-label") or "").strip(),
                "origin": [str(x) for x in origin] if isinstance(origin, list) else []}
        return turn

    def _thread_busy(subject: str, session: str, *, for_routine: bool) -> bool:
        """IS THE OTHER HALF OF THIS THREAD MID-TURN? One thread never runs two turns at once
        (ADR-0046 decision 12): a routine's run checks the person's unit, a person's turn checks the
        routine's — each unit already serialises its own inbox."""
        try:
            gen = sess.mount_gen(subject, session)
        except Exception:  # noqa: BLE001 — an unreadable generation is the id it always had
            gen = 0
        other = (units.chat_unit_id(subject, session, gen) if for_routine
                 else units.routine_unit_id(subject, session, gen))
        return unit_busy(redis_url, other, _unit_key(other))

    def _unit_key(unit_id: str) -> str:
        """The unit's input key, the one the dispatcher signs that unit's entries with."""
        secret = settings.internal_api_secret if settings is not None else None
        return unit_input.unit_key(secret.get_secret_value() if secret else "", unit_id)

    def _toolbelt_configured() -> bool:
        return bool(settings is not None and (settings.mcp_url or "").strip()
                    and settings.mcp_delegation_secret.get_secret_value())
    stream_reader = d['stream_reader']
    session_store = d.get('session_store')
    notifier = d.get('notifier')
    subject_of = d['subject_of']
    workspace_registry = d['workspace_registry']
    wsr = d['wsr']

    def _person_rows(subject: str) -> list:
        """Every workspace the person has, with its state — what the default target is checked against."""
        rows, _ = _membership_mod.reconciled_memberships(wsr.root, subject, mindex.list)
        return _names_mod.listing(wsr.root, subject, registry=workspace_registry, index_list=lambda _s: rows)
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
        # A CHAT WITH ITS OWN READS names exactly those (Vexa-ai/vexa#1783) — the line must not
        # tell the agent about a workspace this turn does not mount.
        try:
            own = sess.reads(subject, session)
        except Exception:  # noqa: BLE001
            own = None
        if own is not None:
            mounts = [w for w in own if w not in ("_global", "_system", "personal")]
        desk = _ws_name(str(subject), fallback="your personal workspace")
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
        if target and desk not in others and (own is None or "personal" in own):
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
            # the one name rule (UI decisions 14): title · people · "Call with Vexa" — never an id
            out[rid] = display_name_mod.meeting_display_name(r)
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
            # A stored name is cleaned ON READ, never rewritten: a `named_title` an agent sent with a
            # marker in it (Vexa-ai/vexa#1641) shows as the words around the marker.
            named = chat_label_mod.strip_markers(r.get("named_title") or "")
            out.append({**r, "label": named or chat_label_mod.chat_label(
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
            # INSIDE THE STREAM, ON PURPOSE (above): the page and its commit are durable in the
            # workspace store before the event that announces them is yielded, so that event is
            # this write's acknowledgement (WS-S3 `durable_in_stream`).
            from workspaces.store.observer import durable_in_stream  # noqa: PLC0415

            with durable_in_stream():
                out = meeting_mint_mod.mint(wsr.root, subject, row, record=_meeting_note_recorder)
            logger.info("meeting %s page for subject=%s: %s (%s)", meeting_id, subject,
                        out.get("path"), "minted" if out.get("created") else "already there")
        except Exception:  # noqa: BLE001 — the turn outranks its own furniture
            logger.exception("minting the page for meeting %s (subject=%s) failed",
                             meeting_id, subject)

    def _qualify_artifact(ev, subject: str, session: str) -> None:
        """The `artifact` gains its qualified ``ref`` (`api_shared.qualify_artifact`); a write that
        named no workspace landed in this chat's target, which only agent-api knows."""
        if not isinstance(ev, dict) or ev.get("type") != "artifact" or ev.get("ref"):
            return
        try:
            qualify_artifact(ev, target=sess.target(subject, session), desk=str(subject))
        except Exception:  # noqa: BLE001 — an unqualified artifact still opens its tab
            logger.warning("could not qualify an artifact for session=%s", session)

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
            if isinstance(ev, dict) and ev.get("type") == "session-bundle":
                continue    # ADR-0048: the conversation goes to the store, never to a browser
            bound = meeting_binding(ev)
            if bound is not None:
                try:
                    sess.upsert(subject, session, meeting=bound[0], meeting_native=bound[1] or None)
                    sess.bind_meeting_chat(subject, bound[0], session)
                except Exception:  # noqa: BLE001 — the turn outranks its own bookkeeping
                    logger.exception("binding meeting %s to subject=%s session=%s failed",
                                     bound[0], subject, session)
                _mint_meeting_page(subject, bound[0])
            _qualify_artifact(ev, subject, session)
            focused = workspace_focus(ev)
            if focused is not None:
                try:
                    sess.add_workspace(subject, session, focused)
                    sess.set_target(subject, session, focused)
                except Exception:  # noqa: BLE001 — the turn outranks its own bookkeeping
                    logger.exception("focusing workspace %s on subject=%s session=%s failed",
                                     focused, subject, session)
            yield item

    class _MeetingFold:
        """THE MEETING A TURN FOLDS IS ONE EVERY READER OF THE TURN MAY READ (ADR-0048 decision 4).
        In a chat only the speaker reads, the speaker's own access decides. In a turn other people
        read — a shared chat, a room, a routine's run there — a meeting folds only when each of them
        passes the same access decision too, or when it is this chat's own call; otherwise nothing
        of it is folded and the turn is told so (``withheld``)."""

        def __init__(self, subject: str, plan) -> None:
            self.subject = subject
            self.plan = plan if plan is not None and plan.scope is not None else None
            self.withheld = False

        def _own_call(self, meeting_id: str) -> bool:
            try:
                call = session_store.active_call(self.plan.row["id"]) if session_store is not None else None
            except Exception:  # noqa: BLE001 — a store that cannot answer names no call of this chat
                return False
            return bool(call) and str(call.get("meeting_id")) == str(meeting_id)

        def access(self, meeting_id: str) -> "dict | None":
            row = _meeting_access(self.subject, meeting_id)
            if row is None or self.plan is None or self._own_call(meeting_id):
                return row
            for other in self.plan.humans:
                if other == self.subject:
                    continue
                try:
                    readable = _meeting_access(other, meeting_id) is not None
                except Exception:  # noqa: BLE001 — an access check that cannot answer refuses
                    readable = False
                if not readable:
                    self.withheld = True
                    return None
            return row

    def _with_call_focus(body: ChatBody, subject: str, session: str) -> ChatBody:
        """The body with the session's live call — or the meeting this chat started (a bot it sent,
        founder 2026-10-13) — as its meeting focus, when it names no focus of its own."""
        focus = body.context.focus if body.context is not None else body.active
        if focus:
            return body
        meeting_id = ""
        if session_store is not None:
            try:
                from control_plane import session_sync  # noqa: PLC0415

                row = session_sync.resolve(session_store, subject, session, create=False)
                call = session_store.active_call(row["id"]) if row else None
                if call:
                    meeting_id = str(call["meeting_id"])
            except Exception:  # noqa: BLE001 — a store that cannot answer grounds nothing extra
                pass
        if not meeting_id:
            try:
                meeting_id = str((sess.meta(subject, session) or {}).get("meeting") or "")
            except Exception:  # noqa: BLE001
                meeting_id = ""
        if not meeting_id:
            return body
        meeting = {"kind": "meeting", "meeting_id": meeting_id, "status": "active"}
        ctx = (body.context.model_copy(update={"focus": meeting}) if body.context is not None
               else ChatContextBody(focus=meeting))
        return body.model_copy(update={"context": ctx})

    # ── THE SESSION, WHEN CONVERSATIONS ARE STORED APART (ADR-0048) ───────────────────────────
    def _session_plan(request: Request, body: ChatBody, subject: str, session: str):
        """What this message does in its session — or None when the store is off (or, for a PRIVATE
        thread only, cannot answer: the chat then runs exactly as it always did). A refusal of the
        SESSION gate is an HTTP error; a store that cannot name a shared turn's readers is a typed
        503 (returned as the response)."""
        if session_store is None or body.room_meeting_id:
            return None
        from control_plane import session_turn, workspace_membership  # noqa: PLC0415

        def _is_member(ws: str, who: str) -> bool:
            try:
                return workspace_membership.is_member(wsr.root, ws, who) is not None
            except Exception:  # noqa: BLE001 — an unreadable member list is not a membership
                return False

        try:
            mounts = (active_workspaces(wsr.root, subject)
                      + shared_active_mounts(wsr.root, subject, mindex.list(subject)))
            candidates = [m.slug for m in mounts] + [system_mounts.GLOBAL_SLUG, "_system", str(subject)]
        except Exception:  # noqa: BLE001 — with no candidates the shared scope is _global alone
            candidates = [system_mounts.GLOBAL_SLUG]
        names = {c: _ws_name(c, fallback=c) for c in candidates}
        shared = _shared_items(request, body.share)
        try:
            return session_turn.plan_turn(
                session_store, subject=subject, session=session, text=body.prompt or "",
                candidates=candidates, is_member=_is_member, global_root=_global_root(),
                names=names, shared_items=shared,
                speaker_name=(request.headers.get("x-user-email") or "").strip(),
                # A BUTTON IS ADDRESSED TO THE AGENT (live app.dev: "Update notes" in a shared meeting
                # chat became a room line carrying its whole internal instruction). A turn with an
                # intent was composed by a press, never typed to the people in the chat.
                addressed=(bool(body.to_agent) or bool(body.intent)
                           or (bool(body.reply_to) and _rooms_on(subject))
                           or _routine_run(request)),
                is_room=_room_check(subject))
        except session_turn.SessionDenied as exc:
            if exc.reason == "not_a_participant":
                raise HTTPException(status_code=404, detail="session not found") from None
            raise HTTPException(status_code=403, detail=exc.reason) from None
        except session_sync.SessionUnavailable as exc:
            # a store id, or a thread other people read: refused, never run wide open (S6)
            logger.warning("session store could not plan a shared turn (%s); refused",
                           type(exc.__cause__).__name__ if exc.__cause__ else "error")
            return JSONResponse(status_code=503, content=session_sync.answer(exc.kind, session))
        except Exception as exc:  # noqa: BLE001 — the store is furniture to a PRIVATE chat only
            logger.warning("session store could not plan this turn (%s); the chat runs as before",
                           type(exc).__name__)
            return None

    def _stored_flow(subject: str, session: str) -> "dict | None":
        """The flow a stored session is bound to (ADR-0046 decision 12 over ADR-0048), or None —
        also None when the store is off or cannot answer."""
        if session_store is None:
            return None
        from control_plane import session_sync  # noqa: PLC0415
        try:
            row = session_sync.resolve(session_store, subject, session, create=False)
        except Exception:  # noqa: BLE001 — an unreadable store binds nothing
            return None
        return session_store_mod.read_flow((row or {}).get("flow"))

    def _bind_flow(subject: str, session: str, flow_turn: dict) -> None:
        """A ROUTINE'S THREAD IS A SESSION BOUND TO ITS FLOW (ADR-0048 decision 1, ADR-0046
        decision 12). The run's own session row — the one its transcript is saved to — carries
        the flow and is titled with the flow's name. The binding is written once and never moved:
        a thread that already belongs to a flow keeps it."""
        if session_store is None or (flow_turn or {}).get("step") != "agent_run":
            return
        from control_plane import session_sync  # noqa: PLC0415
        try:
            row = session_sync.resolve(session_store, subject, session)
            if row is None or row.get("flow"):
                return
            session_store.update(row["id"], flow=session_store_mod.flow_ref(flow_turn["scope"], flow_turn["flow"]),
                                 title=(row.get("title") or flow_turn["flow"]) or None)
        except Exception as exc:  # noqa: BLE001 — the run outranks its own bookkeeping
            logger.warning("session store could not bind this routine thread (%s)", type(exc).__name__)

    def _mention_block(subject: str, mentioned: list, plan) -> str:
        """Who this message @-mentions, by account id — only people the speaker shares a workspace
        with (``people_directory``)."""
        from control_plane import people_directory  # noqa: PLC0415

        try:
            people = people_directory.mentionable(wsr.root, subject, mindex.list)
            in_chat = ([p["principal"] for p in session_store.participants(plan.row["id"])]
                       if plan is not None and session_store is not None else [])
        except Exception:  # noqa: BLE001 — a mention the server cannot check is not passed on
            return ""
        return people_directory.mention_block(people, mentioned, in_chat=in_chat)

    def _room_principal_for(subject: str, plan, session: str = ""):
        """The room principal for this turn's session (rooms on, a room with a workspace), else None.
        A turn with no session plan (the post-meeting recap names its meeting) is resolved by the
        session it lands in."""
        if not _rooms_on(subject) or session_store is None:
            return None
        from control_plane import room_principal as _rp  # noqa: PLC0415
        try:
            sid = plan.row["id"] if plan is not None else ""
            if not sid and session:
                found = session_sync.resolve(session_store, str(subject), session, create=False)
                sid = (found or {}).get("id") or ""
            # the room's grant and audience, read afresh this turn — its live call's people included
            # (D27); a changed grant is a different unit (`RoomPrincipal.grant_hash`)
            p = _rp.resolve(session_store, wsr.root, sid, speaker=str(subject),
                            call_lookup=(lambda mid: _meeting_owner_lookup(str(subject), mid))
                            if _meeting_owner_lookup is not None else None) if sid else None
            # THE OWNER'S ACCOUNT HAS ROOMS TOO: the turn runs on the owner's authority and pool, so
            # the per-account switch is read for them as well as for the speaker.
            if p is not None and not _rooms_on(p.owner):
                return None
            return p
        except Exception:  # noqa: BLE001 — a room that cannot be resolved runs as the speaker, narrowed
            logger.warning("room principal not resolved for %s", session)
            return None

    def _call_changes_block(meeting_id) -> str:
        """The changes the room's agent made during this meeting's call, for its recap."""
        try:
            call = session_store.call_by_meeting(int(meeting_id)) if session_store is not None else None
            changes = session_store.call_changes(call["room"]) if call else []
        except Exception:  # noqa: BLE001 — a recap without the list is still a recap
            return ""
        if not changes:
            return ""
        lines = ["Changes you made in the workspace during the call — list them in the recap; each can "
                 "be undone from the call's change list:"]
        for c in changes:
            state = f" (undone by {c['undone_by']})" if c["undone_at"] is not None else ""
            lines.append(f"- {c['workspace']}/{c['path']}{state}")
        return "\n".join(lines)

    def _room_check(subject: str = ""):
        """``is_room(session_id)`` while the rooms feature is on, else None (nothing changes)."""
        if not _rooms_on(subject) or session_store is None:
            return None
        from control_plane import rooms as _rooms  # noqa: PLC0415
        return lambda sid: _rooms.is_room(session_store, sid)

    def _rooms_on(subject: str = "") -> bool:
        from control_plane import rooms as _rooms  # noqa: PLC0415
        return _rooms.enabled(_global_root, str(subject or ""))

    def _speaker_name(request: Request, subject: str) -> str:
        """How the people in a room name the speaker (their roster name), "" when nobody can say."""
        from control_plane import people_directory  # noqa: PLC0415
        try:
            return people_directory.name_of(wsr.root, subject, mindex.list,
                                            request.headers.get("x-user-email") or "") or ""
        except Exception:  # noqa: BLE001 — a name is a courtesy
            return ""

    def _notify_turn(request: Request, subject: str, body: ChatBody, plan, flow_turn) -> None:
        """The two notifications a message emits (#1783), from here and nowhere else:
        ``chat_mentioned`` to each @-mentioned person who can read this chat, and
        ``routine_posted`` to the other people in a shared routine chat when its run lands. The
        text names the speaker (or the routine) and the chat only — never the message."""
        if notifier is None or plan is None or session_store is None:
            return
        from control_plane import people_directory  # noqa: PLC0415

        try:
            row = plan.row
            title = row.get("title") or "a chat"
            humans = set(session_store.humans(row["id"]))
            if flow_turn is not None:
                # a routine's scheduled run only — another agent's message is not a run
                others = [h for h in humans if h != subject] if flow_turn.get("step") == "agent_run" else []
                if others:
                    notifier.emit(others, kind="routine_posted", actor=f"flow:{flow_turn.get('flow')}",
                                  actor_name=str(flow_turn.get("flow") or "routine"),
                                  object_kind="chat", object_id=row["id"], object_name=title)
                return
            mentioned = [str(m) for m in (body.mentions or []) if str(m) in humans and str(m) != subject]
            if mentioned:
                name = people_directory.name_of(wsr.root, subject, mindex.list,
                                                request.headers.get("x-user-email") or "")
                # the message itself is the anchor a click opens at — its turn id, never its text
                anchor = f"?turn={body.turn_id}" if getattr(body, "turn_id", None) else None
                notifier.emit(mentioned, kind="chat_mentioned", actor=subject, actor_name=name,
                              object_kind="chat", object_id=row["id"], object_name=title, url=anchor)
        except Exception as exc:  # noqa: BLE001 — a notification never fails the turn
            logger.warning("turn notifications not sent (%s)", type(exc).__name__)

    def _routine_run(request: Request) -> bool:
        """A routine's run, posted by flows over the internal tier (ADR-0046 decision 12): the agent
        answers it in a shared chat without being mentioned. Only the internal tier may say so."""
        if (request.headers.get("x-vexa-flow-regime") or "").strip().lower() != "autonomous":
            return False
        import hmac as _hmac  # noqa: PLC0415

        secret = settings.internal_api_secret.get_secret_value() if settings is not None else ""
        provided = request.headers.get("x-internal-secret", "")
        return bool(secret and provided and _hmac.compare_digest(provided, secret))

    def _shared_items(request: Request, refs) -> list:
        """The items the speaker explicitly shares for this turn (``workspace/path``), read with the
        SPEAKER's own access. An item they cannot read is refused, never silently dropped."""
        from workspaces.shared import workspace_paths as _wpaths  # noqa: PLC0415

        out = []
        for ref in refs or []:
            slug, _, rel = str(ref).partition("/")
            if not slug or not rel:
                raise HTTPException(status_code=422, detail="share names workspace/path")
            try:
                base = _read_target(request, slug)
                text = _wpaths.read_text_inside(base, rel, max_bytes=64 << 10)
            except HTTPException:
                raise
            except Exception:  # noqa: BLE001
                text = None
            if text is None:
                raise HTTPException(status_code=404, detail=f"cannot share {ref}: not readable")
            out.append({"label": f"{_ws_name(slug, fallback=slug)} / {rel}", "text": text})
        return out

    def _attachment_dir(subject: str, ws: str):
        """The directory of workspace ``ws`` for ``subject``: one of their own slots, or a shared
        workspace they are a member of — else None (not readable any more)."""
        from pathlib import Path as _P  # noqa: PLC0415
        from control_plane import workspace_attach as _wa  # noqa: PLC0415
        from control_plane import workspace_membership as _wm  # noqa: PLC0415
        try:
            st = _wa._load_state(_wa._store(_P(wsr.root), subject))
            if ws in (st.get("slots") or {}):
                return _wa.workspace_slot_dir(wsr.root, subject, ws)
            if (_P(wsr.root) / ws).is_dir() and _wm.is_member(_P(wsr.root), ws, subject):
                return _P(wsr.root) / ws
        except Exception:  # noqa: BLE001
            return None
        return None

    def _vouch_attachments(body: ChatBody, subject: str):
        """``body`` with its attachments mark rewritten to the files that are really the speaker's,
        and those files. A body with no mark comes back untouched."""
        text = body.prompt or ""
        if attachments_mod.MARK_OPEN not in text:
            return body, []
        try:
            desk = home_of(wsr, subject, for_read=True)   # where `ws_upload` put them (#1783)
        except ValueError:
            return body.model_copy(update={"prompt": attachments_mod.strip(text)}), []
        # WHERE EACH FILE WAS STORED for this chat (Vexa-ai/vexa#1783): its target workspace, the
        # `_system` tier (incognito), or the desk.
        _session = str(body.session or "").strip()
        try:
            _homes = sess.attachments_of(str(subject), _session) if _session else {}
        except Exception:  # noqa: BLE001
            _homes = {}

        def _dir(rel: str):
            ws = _homes.get(rel) or ""
            if ws == system_mounts.SYSTEM_SLUG:
                return system_mounts.ensure_system_workspace(wsr.root, str(subject))
            if ws:
                return _attachment_dir(str(subject), ws)
            return desk

        def _stat(rel: str):
            from workspaces.shared import workspace_paths as _wpaths  # noqa: PLC0415
            base = _dir(rel)
            if base is None:
                return None
            try:
                st = _wpaths.stat_inside(base, rel)
            except Exception:  # noqa: BLE001 — a refused path is not an attachment
                return None
            return int(st.st_size) if st is not None and _stat_mod.S_ISREG(st.st_mode) else None

        prompt, kept = attachments_mod.normalise(text, owner=str(subject), stat=_stat,
                                                 where=lambda rel: _homes.get(rel) or "")
        return body.model_copy(update={"prompt": prompt}), kept

    @router.get("/api/sessions/{session}/attachment")
    def session_attachment(session: Annotated[str, Path(pattern=CHAT_SESSION_PATTERN)], path: str,
                           request: Request,
                           if_none_match: Optional[str] = Header(default=None, alias="If-None-Match")):
        """ONE FILE ATTACHED TO A MESSAGE IN THIS CONVERSATION, AS ITSELF — the thumbnail in the
        bubble and the picture its lightbox opens.

        WHO MAY READ IT is the SESSION's question (ADR-0048 decision 4), never a workspace's: a
        participant reads what was posted here, from the poster's desk, because posting it into the
        conversation was the poster's explicit share. Nothing else on that desk opens through this
        door — only a path recorded against this session. The caller's own uploads always open
        (a private chat, or a deployment without the session store). Anyone else gets the answer a
        file that does not exist gets."""
        from workspaces.shared import workspace_paths as _wpaths  # noqa: PLC0415

        subject = subject_of(request)
        if not attachments_mod.STORED.fullmatch(path or ""):
            raise HTTPException(status_code=404, detail="not found")
        poster = None
        if session_store is not None:
            from control_plane import session_sync  # noqa: PLC0415
            try:
                row = session_sync.resolve(session_store, subject, session, create=False)
            except Exception:  # noqa: BLE001 — an unreadable store opens nothing but the caller's own
                row = None
            if row is not None:
                poster = session_store.attachment_poster(row["id"], path, prefer=str(subject))
        if poster is None:
            poster = str(subject)   # the caller's own desk, which they may always read
        try:
            # the poster's HOME, where uploads land with no chat recorded (#1783)
            desk = home_of(wsr, poster, for_read=True)
            # STORED WHERE THE CHAT WORKED (Vexa-ai/vexa#1783): the poster's record of this chat
            # names the workspace (or `_system` for incognito); unrecorded = the desk, as before.
            try:
                home = (sess.attachments_of(poster, session) or {}).get(path, "")
            except Exception:  # noqa: BLE001
                home = ""
            if home == system_mounts.SYSTEM_SLUG:
                desk = system_mounts.ensure_system_workspace(wsr.root, poster)
            elif home:
                desk = _attachment_dir(poster, home) or desk
            data = _wpaths.read_bytes_inside(desk, path)
            if data is None and desk != wsr.workspace_dir(poster):
                desk = wsr.workspace_dir(poster)
                data = _wpaths.read_bytes_inside(desk, path)
            st = _wpaths.stat_inside(desk, path) if data is not None else None
        except (ValueError, _wpaths.PathRefused):
            data, st = None, None
        if data is None or st is None:
            raise HTTPException(status_code=404, detail="not found")
        etag = f'"{st.st_size:x}-{st.st_mtime_ns:x}"'
        name = attachments_mod.original_name(path)
        headers = {
            "ETag": etag,
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Content-Disposition": f'inline; filename="{name}"',
        }
        if if_none_match and etag in [t.strip() for t in if_none_match.split(",")]:
            return Response(status_code=304, headers=headers)
        from shared.asset_source import media_type_for  # noqa: PLC0415
        return Response(content=data, media_type=media_type_for(path), headers=headers)

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

    @router.post("/api/chat/meeting")
    def chat_bind_meeting(request: Request, body: ChatMeetingBindBody = Body(...)):
        """BIND A MEETING STARTED FROM THIS CHAT TO IT (founder, 2026-10-13): a voice note or a call
        the person started here. The same binding a bot sent from the chat makes; a meeting the
        person cannot read is 404."""
        subject = subject_of(request)
        if _meeting_access(subject, body.meeting) is None:
            raise HTTPException(status_code=404, detail="meeting not found")
        sess.upsert(subject, body.session, meeting=body.meeting)
        sess.bind_meeting_chat(subject, body.meeting, body.session)
        return {"ok": True, "session": body.session, "meeting": body.meeting}

    @router.post("/api/chat/pending/now")
    def chat_pending_now(request: Request, body: ChatSendNowBody = Body(...)):
        """RUN THIS QUEUED MESSAGE NOW: the running turn stops (said in the chat) and this one runs
        next, once. 404 when it is no longer queued."""
        subject = subject_of(request)
        session, item = body.session, body.id
        try:
            gen = sess.mount_gen(subject, session)
        except Exception:  # noqa: BLE001
            gen = 0
        unit_id = units.chat_unit_id(subject, session, gen)
        entry = inbox_send_now(redis_url, unit_id, _unit_key(unit_id), item)
        if not entry:
            raise HTTPException(status_code=404, detail="that message is no longer queued")
        return {"ok": True, "id": item}

    @router.post("/api/chat/stop")
    def chat_stop(request: Request, body: ChatStopBody = Body(...)):
        """STOP THE RUNNING REPLY (live app.dev 2026-10-15). The browser's Stop used to close only its
        own request: the turn — and a 14-minute `grep` under it — ran on, held the conversation, and
        every next message was refused (409). This reaches the worker: its stop watcher kills the
        running turn's CLI and every process the turn started, and the turn ends with a
        `turn-truncated` the chat shows. The unit holding the conversation's head is stopped too, so
        the 409's "Stop it" works from any device. A viewer of a shared chat may not stop it (403)."""
        subject = subject_of(request)
        session = body.session
        try:
            gen = sess.mount_gen(subject, session)
        except Exception:  # noqa: BLE001
            gen = 0
        session_id = ""
        if session_store is not None:
            try:
                row = session_sync.resolve(session_store, subject, session, create=False)
            except Exception:  # noqa: BLE001 — a store that cannot answer still stops this person's own unit
                row = None
            if row is not None:
                if not session_store.can_write(row["id"], subject):
                    raise HTTPException(status_code=403, detail="only a participant who can post may stop this reply")
                session_id = row["id"]
        stopped = stop_turns(redis_url, [units.chat_unit_id(subject, session, gen)], session_id=session_id)
        return {"ok": True, "stopped": stopped}

    @router.post("/api/chat/pending/withdraw")
    def chat_pending_withdraw(request: Request, body: ChatSendNowBody = Body(...)):
        """TAKE BACK A QUEUED MESSAGE before the agent reaches it (Remove, and Edit — which puts the
        words back in the box). Only an entry the worker has not taken is touched; `withdrawn` is
        how many were (0 when it already ran or was never queued)."""
        subject = subject_of(request)
        try:
            gen = sess.mount_gen(subject, body.session)
        except Exception:  # noqa: BLE001
            gen = 0
        unit_id = units.chat_unit_id(subject, body.session, gen)
        return {"withdrawn": inbox_withdraw(redis_url, unit_id, _unit_key(unit_id), body.id)}

    @router.get("/api/chat/pending")
    def chat_pending(request: Request,
                     session: Annotated[str | None, Query(pattern=CHAT_SESSION_PATTERN)] = None,
                     after: Annotated[str | None, Query(pattern=r"^\d{1,20}-\d{1,20}$")] = None):
        """WHAT THIS CHAT HAS SUBMITTED AND ITS AGENT HAS NOT TAKEN YET (Vexa-ai/vexa#1610).

        The inbox is the in-topic and the worker publishes how far it has read, so this is a read of
        two facts neither of which this route owns. That is what makes a reload, a second device and
        a swapped terminal container show the SAME pending list: none of them is remembering it.

        `cursor` is the output Stream's tail right now — where a client with nothing open should
        attach to watch the queue run. A client that already has a stream has an exact cursor of its
        own and should use that one instead.

        `after` — that exact cursor, when the client has one — adds `taken`: how many turns the
        worker has started since it. A message submitted mid-turn is taken the instant the turn in
        front of it ends, so by the time a client whose view just closed asks, it is no longer
        pending; `taken` is what tells that client there is an answer running that it has not seen,
        and that it should attach from its cursor and watch it."""
        subject = subject_of(request)
        session = sess.chat_for(subject_of(request), session or units.DEFAULT_CHAT_SESSION)
        try:
            gen = sess.mount_gen(subject, session)
        except Exception:  # noqa: BLE001 — an unreadable generation is the id it always had
            gen = 0
        unit_id = units.chat_unit_id(subject, session, gen)
        pending = inbox_pending(redis_url, unit_id, _unit_key(unit_id))
        # WHY THE QUEUE IS NOT MOVING, when something is (P18): the typed fault each blocked row
        # already carries, once more at the top for a client that draws one banner, not N rows.
        blocked = next((p["blocked"] for p in pending if p.get("blocked")), None)
        # BUSY ACROSS BOTH HALVES OF THE THREAD (ADR-0046 decision 12): the person's unit and, for
        # a routine's thread, the routine's sibling. Flows asks this before it posts a run.
        auto = units.routine_unit_id(subject, session, gen)
        busy = bool(pending) or unit_busy(redis_url, unit_id, _unit_key(unit_id)) or unit_busy(
            redis_url, auto, _unit_key(auto))
        return {"pending": pending, "busy": busy,
                "cursor": _stream_tail_id(redis_url, units.output_topic(unit_id)) or "",
                # where a routine run's turn will start on the thread's routine unit: flows reads it
                # before it posts, and asks /api/chat/turn-outcome for the first turn after it
                "routine_cursor": _stream_tail_id(redis_url, units.output_topic(auto)) or "",
                **({"taken": turns_taken_after(redis_url, unit_id, after)} if after else {}),
                # a turn running right now, from its start: a reload attaches here and keeps the reply
                **({"running_from": rf} if (rf := running_turn_from(redis_url, unit_id)) else {}),
                **({"fault": blocked} if blocked else {})}

    @router.get("/api/chat/turn-outcome")
    def chat_turn_outcome(request: Request,
                          session: Annotated[str, Query(pattern=CHAT_SESSION_PATTERN)],
                          turn_id: Annotated[str, Query(min_length=1, max_length=128)],
                          after: Annotated[str | None, Query(pattern=r"^\d{1,20}-\d{1,20}$")] = None):
        """HOW A ROUTINE'S TURN ENDED (flows phase 0). Flows asks this after it posted a routine's
        run, so the run ends `failed` when its acts were refused instead of `done` on the post.
        Reads the thread's routine unit (`-auto` sibling) first, then the person's own unit:
        `{state: unknown|running|done, ok, refused, acted, failed}` (`api_shared.turn_outcome`)."""
        subject = subject_of(request)
        session = sess.chat_for(subject, session)
        try:
            gen = sess.mount_gen(subject, session)
        except Exception:  # noqa: BLE001 — an unreadable generation is the id it always had
            gen = 0
        out = turn_outcome(redis_url, units.routine_unit_id(subject, session, gen), turn_id,
                           after=after or "")
        if out.get("state") == "unknown" and not after:
            out = turn_outcome(redis_url, units.chat_unit_id(subject, session, gen), turn_id)
        from control_plane.identity_token import SHARED_REFUSAL  # noqa: PLC0415
        if ((out.get("refused") or {}).get("reason") == SHARED_REFUSAL["reason"]
                and session_store is not None):
            # WHO THE CHAT IS SHARED WITH, so a routine that pauses for it can say so in its chat.
            from control_plane import people_directory, session_access  # noqa: PLC0415
            out["shared_with"] = session_access.shared_with(
                session_store, subject, session,
                lambda who: people_directory.name_of(wsr.root, who, mindex.list if mindex else (lambda _s: [])))
        return out

    def _chat(body: ChatBody, request: Request, *, stream: bool):
        _refuse_delegated(request)
        if not (request.headers.get("x-vexa-flow-step") or "").strip():
            # THE PERSON'S OWN WORDS confirm a held write (`private_taint`): only here, never from
            # a delegated caller, so an agent cannot confirm its own write.
            from control_plane import private_taint  # noqa: PLC0415
            private_taint.confirm_from_text(str(subject_of(request)), body.prompt or "")
            # …and a FILE write the worker held is written by the platform now, before the turn
            # runs: the held content lives encrypted in agent-api, never on the worker's desk (D6).
            try:
                private_taint.release_confirmed_files(str(subject_of(request)), body.prompt or "", wsr.root)
            except Exception:  # noqa: BLE001 — a release that fails stays held; the turn still runs
                logger.warning("held file release failed", exc_info=True)
        if (request.headers.get("x-vexa-flow-step") or "").strip() == "stage":
            # A CONCERN CHAT MOVES STAGE (opened → active → waiting → resolved → archived): stored on
            # the chat (the rail and the home read it) and said in one line. Internal tier only.
            _session = body.session or units.DEFAULT_CHAT_SESSION
            _move = _flow_turn(request, _session)
            if _move is not None:
                from control_plane.api_shared import CONCERN_STAGES  # noqa: PLC0415
                if _move.get("stage") not in CONCERN_STAGES:
                    raise HTTPException(status_code=422, detail={"refused": "unknown_stage"})
                _who = subject_of(request)
                sess.upsert(_who, _session, concern={"stage": _move["stage"]})
                from control_plane import routine_flows as _rf  # noqa: PLC0415
                _rf.post_to_routine_chat(session_store, _who, "concern", (body.prompt or "").strip(),
                                         session=_session, bind=False)
                return {"ok": True, "session": _session, "stage": _move["stage"]}
        if (request.headers.get("x-vexa-flow-step") or "").strip() == "notice":
            # A LINE FROM FLOWS, NOT A TURN: why a routine did not run (its daily cap, a refusal),
            # written into the routine's chat for the person and the next run to read. Same
            # internal-tier check as a routine's turn (`_flow_turn`); nothing is dispatched.
            _session = body.session or units.DEFAULT_CHAT_SESSION
            _notice = _flow_turn(request, _session)
            if _notice is not None:
                from control_plane import routine_flows as _rf  # noqa: PLC0415
                _who = subject_of(request)
                noted = _rf.post_to_routine_chat(session_store, _who, _notice["flow"],
                                                 (body.prompt or "").strip(), session=_session)
                # …and the chat is marked as the routine's at once — the rail's label and the strip
                # appear now, not at the routine's next run.
                try:
                    if _notice.get("kind") == "sink":
                        raise LookupError        # an event card is not a routine's mark
                    sess.upsert(_who, _session, routine={"flow": _notice["flow"],
                                                         "scope": _notice["scope"],
                                                         "version": _notice.get("version") or 1})
                except LookupError:
                    pass
                except Exception:  # noqa: BLE001 — the line was the point; the marker follows next run
                    logger.warning("could not mark chat %s as a routine's", _session)
                return {"ok": True, "noted": noted, "session": _session}
        if stream_reader is None:
            raise HTTPException(status_code=501, detail="stream relay not wired")
        subject = subject_of(request)  # server-derived (P20); body.subject is ignored
        # A MEETING STARTED FROM A CHAT IS THAT CHAT (founder, 2026-10-13): `meet-<row>` lands there.
        session = sess.chat_for(subject, body.session or units.DEFAULT_CHAT_SESSION)
        from control_plane.routers.asks import bind_answer_marks, carries_answer_mark  # noqa: PLC0415
        if body.prompt and carries_answer_mark(body.prompt):
            # A CARD ANSWER IS A RECORD, NOT A STRING (D13, 7g): a `#<pending>` mark the server cannot
            # match to this speaker's own press on this chat's card reaches the turn as typed — in
            # whatever case, spacing or width it was typed.
            try:
                _arow = session_store.get(session) or session_store.by_name(subject, session) if session_store else None
            except Exception:  # noqa: BLE001 — no chat row: nothing to vouch against
                _arow = None
            body = body.model_copy(update={"prompt": bind_answer_marks(
                session_store, str(subject), _arow["id"] if _arow else None, body.prompt)})
        # A ROUTINE'S RUN (ADR-0046 decision 12): the internal tier posting a scheduled turn into
        # the routine's own thread. It runs `autonomous`, on the thread's sibling unit.
        flow_turn = _flow_turn(request, session)
        _stored_routine = None
        if flow_turn is None:
            _stored_routine = next((r.get("routine") for r in sess.list(subject)
                                    if r.get("session") == session), None)
            if not _stored_routine:
                _stored_routine = _stored_flow(subject, session)
        if (flow_turn is not None or _stored_routine) and not (
                stream and request.headers.get("last-event-id")):
            if _thread_busy(subject, session, for_routine=flow_turn is not None):
                raise HTTPException(status_code=409, detail={
                    "refused": "thread_busy",
                    "note": ("this routine's thread is running a turn — send it again when that "
                             "turn ends" if flow_turn is None else
                             "the person's turn in this thread is still running")})
        # THE COMPANY'S TOKEN BUDGET (#1783, `budgets`), read live from `_global/POLICIES.md`: a
        # routine's run pauses with a visible reason; a person's turn is warned at 80%, stopped at 100%.
        if not (stream and request.headers.get("last-event-id")):
            from control_plane import budgets as _budgets  # noqa: PLC0415
            from shared import usage as _usage  # noqa: PLC0415
            try:
                _desk = home_of(wsr, subject, for_read=True)   # where narrow_mine writes the limits (#1783)
            except Exception:  # noqa: BLE001
                _desk = None
            _pol = _budgets.effective(_global_root() if _global_root else None, _desk)
            _row = None
            if flow_turn is not None and session_store is not None:
                try:
                    _row = session_store.get(session) or session_store.by_name(subject, session)
                except Exception:  # noqa: BLE001
                    _row = None
            _stop = _budgets.gate(
                subject, background=flow_turn is not None, policy=_pol,
                used=_usage.rolling(_budgets.usage_client(redis_url), subject), notifier=notifier,
                note=(lambda t: session_store.add_note(_row["id"], "vexa", t)) if _row else None)
            if _stop:
                raise HTTPException(status_code=423 if flow_turn is not None else 429, detail=_stop)
        # THE PERSON'S OWN WORDS, CAPTURED BEFORE ANYTHING COMPOSES OVER THEM (Vexa-ai/vexa#1610).
        # Every branch below may replace `body.prompt` — with a scaffold's opening, a preset's ask, a
        # mark — and a queued row has to say what the person submitted, not what the server made of
        # it. One read, at the top, where the sentence still exists.
        # WHAT WAS ATTACHED, AS THE SERVER VOUCHES FOR IT. The terminal names files in one mark
        # (`shared/attachments.py`); only files the speaker's own upload stored on their own desk
        # survive, and whose desk, the size and the type are written here, never taken from the body.
        body, _attached = _vouch_attachments(body, subject)
        _submitted = attachments_mod.strip(body.prompt) if _attached else body.prompt
        # ADR-0048: the session gate, when the agent speaks, and the leak rule — one decision.
        _plan = _session_plan(request, body, subject, session)
        if isinstance(_plan, JSONResponse):
            return _plan        # a typed session refusal (S6), before anything ran
        if _plan is not None and _plan.attachments:
            body = body.model_copy(update={"prompt": attachments_mod.merge(body.prompt or "", _plan.attachments)})
        if _attached and _plan is not None:
            try:
                session_store.add_attachments(_plan.row["id"], subject, _attached)
            except Exception as exc:  # noqa: BLE001 — the turn outranks its bookkeeping
                logger.warning("session store did not record this turn's attachments (%s)",
                               type(exc).__name__)
        _notify_turn(request, subject, body, _plan, flow_turn)
        if flow_turn is not None:
            _bind_flow(subject, session, flow_turn)
            if flow_turn.get("concern"):
                try:
                    _known = (sess.meta(subject, session) or {}).get("concern")
                    sess.upsert(subject, session, concern={
                        "key": flow_turn["concern"], "flow": flow_turn["flow"],
                        "stage": "active" if _known else "opened"})
                except Exception:  # noqa: BLE001 — the run outranks its own bookkeeping
                    logger.warning("concern stage not recorded for %s", session)
            # A ROUTINE REFUSED THE SAME WAY THREE RUNS IN A ROW IS PAUSED (`routine_flow_refusals`).
            from control_plane import routine_flow_refusals as _rfr  # noqa: PLC0415
            try:
                _gen = sess.mount_gen(subject, session)
            except Exception:  # noqa: BLE001 — an unreadable generation is the id it always had
                _gen = 0
            _paused = _rfr.before_run(subject, flow_turn, units.routine_unit_id(subject, session, _gen),
                                      read=_rfr.redis_reader(redis_url), workspaces_dir=wsr.root,
                                      store=session_store, session=session)
            if _paused:
                raise HTTPException(status_code=423, detail=_paused)
        if _plan is not None and not _plan.speak:
            # Not addressed to the agent: a message every participant reads, heard on the next turn.
            # the person's words only: the terminal's context line before the sentinel is for the agent
            _said = str(body.prompt or "")
            _cut = _said.rfind(CONTEXT_SENTINEL)
            note = session_store.add_note(_plan.row["id"], subject,
                                          _said[_cut + len(CONTEXT_SENTINEL):].strip() if _cut >= 0 else _said)
            # A ROOM'S MESSAGE IS FOR ITS SENSOR, NOT A TURN (rooms, 2026-10-15): on the room's
            # stream, which the signals service's chat source reads; it wakes the agent when it
            # matters. agent-api is the stream's one writer.
            if _rooms_on(subject):
                from control_plane import rooms as _rooms  # noqa: PLC0415
                _rooms.publish(_rooms.redis_for(redis_url), _plan.row, note,
                               author_name=_speaker_name(request, subject))
            if not stream:
                return {"ok": True, "spoken": False, "id": body.turn_id or "", "session": session,
                        "note": note}
            return StreamingResponse(
                _sse([{"type": "session-note", **note}, {"type": "turn-complete"}]),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                         "X-Chat-Session": session})
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
        # A MEETING'S NOTES ARE WRITTEN ONLY FOR A MEETING THE CALLER CAN READ (the one meeting view,
        # 2026-10-11). Update notes / Create notes on the meeting view is the Extend act naming a
        # MEETING (`extend-meeting`): it reads that meeting's transcript and rewrites its notes. The
        # meeting id rides the body, so it is checked here — the same access decision the transcript
        # stream and `/api/meeting/note` make — before any preset is read or any turn is queued. A
        # refusal costs a 403, never a partly-run turn; another person's meeting gets the same answer
        # as one that does not exist.
        _intent_meeting = chat_intents.page_meeting(body.intent)
        if _intent_meeting and _meeting_access(subject, _intent_meeting) is None:
            raise HTTPException(status_code=403, detail="not authorized for this meeting")
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
        _msg = (flow_turn or {}).get("message")
        _turn_mark = _job_mark or ("" if scaffold_view is not None else (
            (message_mark(_msg["uid"], _msg["thread"], _msg["label"]) if _msg else "")
            or chat_intents.act_prefix(body.intent)
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
        _fresh_turn = False   # set when THIS request dispatched the turn its view will watch
        # Ground the chat in the terminal's ACTIVE meeting (if any): agent-api folds the live transcript
        # from the meeting's redis Stream (tc:meeting:{row} — the SAME stream the live view renders) into
        # the prompt, fresh on every turn. The transcript stays inside the trusted control plane and
        # rides the prompt to the worker — no file, no cross-domain HTTP, no user key in the worker (P15).
        # The meeting the client names is checked with the live stream's own access decision before
        # anything is folded; a meeting this caller cannot read grounds nothing.
        # A CALL IN THIS SESSION (ADR-0048 decision 6): the shared chat follows the call's live
        # transcript. When the client names no meeting, the session's live call is the focus; the
        # access check below still decides, so a participant who has not joined grounds nothing.
        body = _with_call_focus(body, subject, session)
        _fold = _MeetingFold(subject, _plan)
        ctx, tools, prompt = _context_grounding(
            body, session, redis_url,
            schedule_rows=lambda: _schedule_source(subject),
            workspace_mounts=lambda: (active_workspaces(wsr.root, subject)
                                      + shared_active_mounts(wsr.root, subject, mindex.list(subject))),
            meeting_access=_fold.access,
        )
        if _fold.withheld:
            prompt = MEETING_WITHHELD_LINE + prompt
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
        #
        # …AND A SUBMISSION READS TOO. It is sent while a turn is running (that is the only time the
        # terminal submits instead of streaming), so stepping the generation here would put it on a
        # unit nobody spawned — a second worker, a second output Stream, and an answer written where
        # the view the person is watching never looks. A model change or a new workspace made
        # mid-turn therefore applies from the next turn the person starts on an idle chat; the
        # submission queues behind the running turn on the unit that is running it.
        try:
            _gen = (sess.mount_gen(subject, session) if (resume or not stream)
                    else sess.take_mount_generation(subject, session))
        except Exception:  # noqa: BLE001
            logger.warning("mount generation unreadable for subject=%s session=%s — this turn keeps "
                           "the warm unit it would have used", subject, session)
            _gen = 0
        if _gen:
            ctx = {**ctx, "mount_gen": _gen}
        if _plan is not None and _plan.gen:
            # a shared session's narrowed mounts live on their own warm unit, one per intersection
            ctx = {**ctx, "mount_gen": f"{_gen or 0}{_plan.gen}"}
        if (flow_turn or {}).get("kind") == "mail":
            # AN EMAIL'S TURN RUNS ON ITS OWN READ-ONLY SIBLING (D25, `units.mail_unit_id`)
            ctx = {**ctx, "flow_kind": "mail"}
        # Read ONCE, fail-soft, and used twice: it names the target in the prompt below and it is
        # what `build_unit_env` points the cwd and the delegation token at. An index that cannot
        # answer costs the turn its target line, never the turn.
        try:
            _target = sess.target(subject, session)
        except Exception:  # noqa: BLE001
            logger.warning("target workspace unreadable for subject=%s session=%s — this turn "
                           "writes where it always did", subject, session)
            _target = ""
        # A NEW CHAT WRITES TO THE PERSON'S DEFAULT (Vexa-ai/vexa#1783, founder 2026-10-14:
        # "default set to biz but it's writing in personal"). Applied HERE, on the chat's first turn,
        # by the one writer of the chat's target — the client used to apply it to a draft and could
        # lose the race. Kept as the chat's own once the record exists (stored below, after the
        # record is made, so the first prompt still names the chat).
        _default_applied = False
        _default_profile = None
        try:
            if not sess.has_record(subject, session):
                _default_profile = first_turn_profile(sess, settings, wsr.root, subject,
                                                      _person_rows(subject))
                if _default_profile is not None:
                    _target = _default_profile[1][0]
        except Exception:  # noqa: BLE001 — a profile that cannot be read is no profile
            logger.info("default profile not applied for subject=%s session=%s", subject, session)
        if not _target and _default_profile is None:
            try:
                if not sess.has_record(subject, session):
                    _wid, _ = default_target_for(sess, subject, _person_rows(subject))
                    if _wid:
                        _check_target(request, subject, _wid)
                        _target, _default_applied = _wid, True
            except Exception:  # noqa: BLE001 — a default that cannot be used is Personal
                logger.info("default target not applied for subject=%s session=%s", subject, session)
        # THE CHAT'S MODEL — its own pick from the operator's catalog, "" for the person's
        # default. Read once, fail-soft like the target: an index that cannot answer costs the
        # turn its pick, and the dispatch runs it on the person's default.
        try:
            _model = sess.model(subject, session)
            _effort = sess.effort(subject, session)
        except Exception:  # noqa: BLE001
            logger.warning("chat model unreadable for subject=%s session=%s — this turn runs on "
                           "the person's default", subject, session)
            _model, _effort = "", ""
        if not _model and not _effort:
            _model, _effort = _hold_default(subject, session)
        # THE TARGET WORKSPACE, IN FRONT OF THE ASK (Vexa-ai/vexa#1611). Every turn, including the
        # ones nobody typed — a flow's kick and a routine's wake write somewhere too, and the
        # founder's failure was a turn that did not know where. It goes in FRONT of the grounding
        # and therefore in front of the sentinel below, so the person's half stays exactly their
        # words (F47): this is machinery, and machinery never renders as somebody's speech.
        try:
            _reads = sess.reads(subject, session)
            _no_target = sess.no_target(subject, session)
        except Exception:  # noqa: BLE001 — an unreadable record is a chat on the defaults
            _reads, _no_target = None, False
        if _default_profile is not None:
            _target, _reads, _no_target = _default_profile[1]
        if _no_target:
            _target = ""
            prompt = ("[this chat has NO target workspace: the person chose \"no workspaces\". Answer "
                      "in the chat; do not write to any workspace unless they name one for that write.]\n"
                      + prompt)
        else:
            prompt = _target_line(subject, session, _target) + prompt
        # THE PERSON'S PINNED PAGES (#1783), named and addressed, never read: in their own chats only
        # (a shared chat's other readers are not shown what one person keeps pinned).
        if _plan is None:
            try:
                prompt = pinned_pages_preamble(sess.pinned_pages(subject)) + prompt
            except Exception:  # noqa: BLE001 — a turn outranks its own bookkeeping
                logger.warning("pinned pages unreadable subject=%s", subject)
        if _plan is not None and _plan.preamble:
            prompt = _plan.preamble + prompt
        if body.mentions:
            prompt = _mention_block(subject, body.mentions, _plan) + prompt
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
            subject=subject, trigger="scheduled" if flow_turn is not None else "message",
            start=units.entrypoint(inline=prompt), context=ctx, tools=tools,
            principal={"name": _email} if (_email and flow_turn is None) else None,
        )
        # A ROOM'S TURN RUNS AS ITS WORKSPACE'S AGENT (C1, rooms on): one worker for the room, its
        # grant the room's workspace (+ connected open ones), its level the room's — never the
        # speaker's own reach, target or model. Named here, before any unit id is read.
        # …the post-meeting recap of a call in a room included (R1 1.2): it is the room's agent that
        # writes it, under the room's grant — never the starter's desk, never the attendees' desks.
        _room_p = _room_principal_for(subject, _plan, session)
        if _room_p is not None and flow_turn is not None and body.room_meeting_id:
            # THE RECAP LISTS WHAT VEXA CHANGED DURING THE CALL (R1 1.5), each still undoable.
            _changes = _call_changes_block(body.room_meeting_id)
            if _changes:
                _ep = inv["start"].get("entrypoint") or {}
                inv = {**inv, "start": {**inv["start"], "entrypoint": {
                    **_ep, "inline": str(_ep.get("inline") or "") + "\n\n" + _changes}}}
        if _room_p is not None:
            inv = {**inv, "context": {**(inv.get("context") or {}),
                                      "room_unit": _room_p.unit + (units.ROUTINE_UNIT_SUFFIX
                                                                   if flow_turn is not None else "")}}
        _running = _want = None
        if resume:
            # Re-attach only — the warm unit id is deterministic from (subject, session); resume reads
            # its durable output Stream from the cursor. No new turn, no session re-title.
            unit_id = units.dispatch_id(inv)
        else:
            # A PICK APPLIES ON THE NEXT TURN (0.13.3). A worker keeps the model it was started on
            # for its whole warm window, so a fresh turn whose chat now runs on another model goes to
            # a fresh unit — decided from the model the running worker was started on, not from the
            # stale flag alone (which a submission or a held default can leave down). A submission
            # queues behind the running turn on the unit that runs it, and says the switch is next.
            if flow_turn is None:
                _running = dispatcher.running_model(units.dispatch_id(inv), alive=True)
                _want = dispatcher.turn_model(subject, _model, last_pick=_last_pick(subject)) if _running else None
            if stream and _running and _want and _running != _want:
                try:
                    _new = sess.step_generation(subject, session)
                    _mg = f"{_new}{_plan.gen}" if (_plan is not None and _plan.gen) else _new
                    inv = {**inv, "context": {**(inv.get("context") or {}), "mount_gen": _mg}}
                    logger.info("chat %s: worker runs %s, the chat now runs %s — a fresh unit", session,
                                _running, _want)
                except Exception:  # noqa: BLE001 — the warm unit answers rather than no unit
                    logger.warning("model switch not applied for subject=%s session=%s", subject, session)
            unit_id = units.dispatch_id(inv)
            retry_from = _chat_turn_head(redis_url, unit_id, body.turn_id) if body.turn_id else None
            _waiting = queued_waiter((unit_id, body.turn_id)) if (stream and body.turn_id) else None
            if retry_from is None and _waiting is not None:
                # A RETRY OF A MESSAGE STILL QUEUED behind its chat's turn attaches to that wait —
                # it is never queued a second time.
                def _follow_again(uid, st, _tid=body.turn_id):
                    if st is not None:
                        _record_chat_turn_head(redis_url, uid, _tid, st)
                    return _binding_watch(_read_view(stream_reader, uid, st, fresh=True), subject, session)
                return StreamingResponse(
                    _sse(queued_events(_waiting, "", _follow_again)),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                             "X-Unit-Id": unit_id, "X-Chat-Session": session})
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
                _fresh_turn = True
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
                if (flow_turn or {}).get("step") == "agent_run":
                    # A routine's thread is named by its flow (ADR-0046 decision 12) — never by
                    # the instruction, which is machinery and not the person's words.
                    _title = flow_turn["flow"] or _title
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
                # …AND A RECORD NO TURN EVER TITLED (#1783): a draft's scope written before its first
                # message (its target, its mounts) made the record, titled with its own id.
                _stored_title = str((_stored or {}).get("title") or "").strip()
                _retitle = (is_new or _stored_title in ("", session)
                            or chat_label_mod.is_machinery_label(_stored_title))
                sess.upsert(subject, session, title=_title if (_retitle and _title) else None,
                            workspaces=((scaffold_view or {}).get("workspaces") or None),
                            scaffold=({"kind": scaffold_view.get("kind"), "id": scaffold_view.get("id")}
                                      if scaffold_view is not None else None),
                            touched=(flow_turn is None and scaffold_view is None
                                     and not chat_intents.is_silent(body.intent)),
                            routine=(flow_turn if (flow_turn or {}).get("step") == "agent_run"
                                     else None))
                if _default_profile is not None:
                    apply_profile(sess, subject, session, _default_profile[0], _default_profile[1])
                    sess.settle_mounts(subject, session)   # this turn's worker already runs on it
                elif _default_applied:
                    sess.set_target(subject, session, _target, restart=False)
                if flow_turn is not None and flow_turn.get("message"):
                    # the conversation's hops so far, so a reply from this thread carries them on
                    msg = flow_turn["message"]
                    sess.set_agent_chain(subject, session, msg["origin"])
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
                _dispatch_kwargs = dict(
                        room=room,
                        scaffold_workspaces=(scaffold_view or {}).get("workspaces") or None,
                        # WHERE THIS CHAT WRITES (Vexa-ai/vexa#1611) — a dispatcher argument like
                        # `room` and `scaffold_workspaces`, never a field of the sealed unit.v1
                        # envelope, and never anything a request body asserted. It decides the
                        # turn's cwd and rides the delegation token as the tools' default `slug`.
                        target=_target,
                        # WHAT THIS CHAT READS (Vexa-ai/vexa#1783) — its own reads, or None for the
                        # person's default active set. Read once, fail-soft to the default.
                        reads=_reads, no_target=_no_target,
                        # WHICH MODEL — the chat's pick, a catalog id or "" — resolved through the
                        # provider port inside the dispatch, the one place a route is decided.
                        model=_model,
                        # …AT WHICH EFFORT — the chat's pick, checked again against the model
                        # in the dispatch (a catalog may have changed since it was stored).
                        effort=_effort,
                        shared_scope=(_plan.scope if _plan is not None else None),
                        # AN EVENT AGENT'S TURN reads and proposes, nothing more (ADR-0046 d12).
                        # …AND SO DOES A TURN AN INBOUND EMAIL STARTED (D25): it writes one page,
                        # the thread's reply file flows mails back (`signal_scope`), nothing else.
                        least_privilege=(flow_turn or {}).get("kind") in ("event_agent", "mail"),
                        **({"signal_page": {"meeting": "", "workspace": "",
                                            "path": f"mail_outbox/{session}.md"}}
                           if (flow_turn or {}).get("kind") == "mail" else {}),
                        **({"routine_grants": (f"{flow_turn['scope']}/{flow_turn['flow']}",
                                               flow_turn["grants"])}
                           if (flow_turn or {}).get("grants") else {}),
                        # WHAT A QUEUED ROW SHOWS (Vexa-ai/vexa#1610), stamped on the inbox entry
                        # itself so the record a person reads and the record the worker takes are
                        # the same object. Written for every turn, not only a submission: a chat
                        # open on a second device has to be able to see the turn this one is about
                        # to run, and the row disappears the moment the worker takes it.
                        inbox={"id": body.turn_id or "", "display": _queued_display(_submitted),
                               "kind": _intent_kind, "target": _intent_target,
                               "at": round(time.time(), 3)})
                if (flow_turn or {}).get("kind") == "mail":
                    # the reply file lives on the person's own desk, where flows reads it back: no
                    # chat target may redirect the one write this turn is allowed (D25)
                    _dispatch_kwargs.update(target="")
                if _room_p is not None:
                    _dispatch_kwargs.update(room_principal=_room_p, shared_scope=None, reads=None, room=None,
                                            no_target=not _room_p.writable,
                                            target=_room_p.workspace if _room_p.writable else None,
                                            model=_room_p.level or _model)
                try:
                    unit_id = dispatcher.dispatch(inv, **_dispatch_kwargs)  # spawn-or-touch the warm chat unit
                    if _room_p is not None:
                        from control_plane import rooms as _rooms_sp  # noqa: PLC0415
                        _rooms_sp.note_speaker(_rooms_sp.redis_for(redis_url), _room_p.session,
                                               _room_p.on_behalf_of or str(subject))
                except model_providers.ModelChoiceFault as fault:
                    # THE CHAT'S MODEL CANNOT RUN (P18): gone from the catalog, admins-only, an own
                    # endpoint not set or not allowed, a provider secret missing. Refused before
                    # anything was spawned or delivered, as a typed fault naming the model and the
                    # provider — never a turn run on a model the person did not pick.
                    return JSONResponse(status_code=fault.http_status,
                                        content={"detail": fault.sentence(),
                                                 "fault": fault.as_dict()})
                except llm_budget_mod.BudgetExhausted as refusal:
                    # THE SPEND CAP (llm_budget.py): the person or the deployment has spent its AI
                    # budget for the period, or the operator switched model calls off. Refused
                    # before anything was spawned. A person watching the stream reads the sentence
                    # ON the stream, as the typed fault the chat already renders; a routine's turn
                    # and a submission get it as the JSON answer (402/403: not retried).
                    if stream and flow_turn is None:
                        return StreamingResponse(
                            _sse([{"type": "error", "message": refusal.sentence(),
                                   "fault": refusal.fault()}, {"type": "turn-complete"}]),
                            media_type="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                     "X-Chat-Session": session})
                    return JSONResponse(status_code=refusal.http_status, content=refusal.as_dict())
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
                except session_sync.SessionUnavailable as exc:
                    # ADR-0048 (S6): a shared turn whose conversation the store cannot hand over is
                    # refused, typed — never run on the old layout where no other participant reads.
                    return JSONResponse(status_code=503,
                                        content=session_sync.answer(exc.kind, session))
                except session_sync.SessionBusy as exc:
                    # THIS CHAT'S OWN TURN HOLDS THE HEAD (its `-auto` sibling running the meeting
                    # recap or a routine, or another generation of its unit): the message waits
                    # behind it and runs next, said on the stream — never a 409 for the person to
                    # retry by hand (live app.dev 2026-10-15).
                    if stream and flow_turn is None and exc.queueable_for(unit_id):
                        _unit, _inv, _kw, _tid = unit_id, inv, _dispatch_kwargs, body.turn_id

                        def _follow(uid, st, _tid=_tid):
                            if _tid and st is not None:
                                _record_chat_turn_head(redis_url, uid, _tid, st)
                            return _binding_watch(_read_view(stream_reader, uid, st, fresh=True),
                                                  subject, session)

                        waiter = queue_behind(
                            session_sync._redis_for(redis_url), exc, _unit,
                            lambda: dispatcher.dispatch(_inv, **_kw),
                            lambda: _stream_tail_id(redis_url, units.output_topic(_unit)) or None,
                            session=session, key=(_unit, _tid) if _tid else None)
                        logger.info("chat %s: queued behind its own %s turn", session,
                                    exc.holder_kind or "reply")
                        return StreamingResponse(
                            _sse(queued_events(waiter, exc.holder_kind, _follow)),
                            media_type="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                     "X-Unit-Id": unit_id, "X-Chat-Session": session})
                    # ADR-0048 (S5): another participant's turn holds this session's head. Refused
                    # before anything ran, typed, so the client says "send it again" — a reply is
                    # never produced only to be refused at save.
                    return JSONResponse(status_code=409,
                                        content=session_sync.answer(exc.kind, session))
                except session_sync.SessionDenied as exc:
                    raise HTTPException(status_code=403, detail=exc.reason) from None
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
            out = {"ok": True, "id": body.turn_id or "", "session": session, "unit": unit_id,
                   "pending": inbox_pending(redis_url, unit_id, _unit_key(unit_id)),
                   "cursor": _stream_tail_id(redis_url, units.output_topic(unit_id)) or ""}
            if _running:
                out["running"] = _running
                if _want and _want != _running:
                    out["model_pending"] = _want     # the composer: "switching to … on your next message"
            return out
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
            _sse(_binding_watch(_read_view(stream_reader, unit_id, resume, fresh=_fresh_turn), subject, session)),
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
        if not re.fullmatch(CHAT_SESSION_PATTERN, session):
            raise HTTPException(status_code=422, detail="not a chat session id")
        wid = str(body.get("workspace") or "").strip()
        require_in_ceiling(request, wid)
        # THE PERSON'S CLICK, NEVER THE AGENT (UI decision 6b): a worker acting for a chat may
        # propose a target (`propose_write_target`) and is refused here.
        if identity_token.is_delegated(request.headers):
            raise HTTPException(status_code=403, detail=(
                "Only the person switches where this chat writes — call propose_write_target instead."))
        _check_target(request, subject, wid)
        changed = sess.set_target(subject, session, wid)
        return {"ok": True, "session": session, "target": wid or None, "changed": changed}

    def _check_target(request: Request, subject: str, wid: str) -> None:
        """May ``subject`` aim writes at ``wid``? Raises the refusal the chip shows; ``""`` (their
        own desk) always may. ONE check for a chat's target and for the default new chats take."""
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
            # Since 2026-10-11 that test is the ADMIN ACCOUNT (global_layer.is_admin_account): the
            # deployment's dedicated account, never a person's everyday one — the instance admin's
            # included. An everyday chat can never be aimed at Company.
            if not global_layer.is_admin_account(settings, str(subject)):
                raise HTTPException(status_code=403,
                                    detail="only the admin account may write into _global — sign in "
                                           "as the admin account to work on Company")
        elif global_layer.is_admin_account(settings, str(subject)):
            # …AND THE ADMIN ACCOUNT TARGETS NOTHING ELSE: `_global`, or "" (which its dispatch reads
            # as `_global` too — `dispatch.build_unit_env`). It has no desk and no workspaces.
            if wid:
                raise HTTPException(status_code=403, detail=admin_account_scope.REFUSAL)
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

    @router.get("/api/chat/default-target")
    def chat_default_target(request: Request):
        """The workspace this person's NEW chats write to (`target`), or null for Personal.

        RE-CHECKED ON EVERY READ, by the check a chat's target gets: a stored default the person can
        no longer write — removed from it, the workspace gone, the role lowered to reader — answers
        null, and the workspace is not named, because they may no longer be able to see it.
        Existing chats never read this; each keeps its own target."""
        subject = subject_of(request)
        wid = ""
        try:
            wid = sess.default_target(subject)
        except Exception:  # noqa: BLE001 — an unreadable default is Personal
            logger.warning("default target unreadable for subject=%s", subject)
        if not wid:
            return {"target": None}
        try:
            _check_target(request, subject, wid)
        except Exception:  # noqa: BLE001 — not writable any more: Personal, silently
            return {"target": None}
        return {"target": wid}

    @router.put("/api/chat/default-target")
    def set_chat_default_target(request: Request, body: DefaultTargetBody):
        """Make `workspace` the one this person's new chats write to — the "Writes to …" menu's
        "Make default". Only a workspace they can write is accepted (403 otherwise, nothing stored);
        `""` puts new chats back on Personal. No existing chat moves."""
        _refuse_delegated(request)
        subject = subject_of(request)
        wid = str(body.workspace or "").strip()
        require_in_ceiling(request, wid)
        _check_target(request, subject, wid)
        sess.set_default_target(subject, wid)
        return {"target": wid or None}
    def _last_pick(subject):
        """The person's last pick in any chat — what a new chat starts on. Unreadable = none."""
        try:
            return sess.remembered_pick(subject)
        except Exception:  # noqa: BLE001 — an unreadable memory is the default
            return ("", "")

    def _hold_default(subject: str, session: str) -> tuple[str, str]:
        """A CHAT WITH NO PICK OF ITS OWN TAKES THE PERSON'S DEFAULT AT ITS TURN, AND KEEPS IT.

        The default is the person's last pick in any chat, so it moves; a chat that merely followed
        it would move too, and the founder's rule is that an existing chat keeps the model and
        effort it was last used with. So the turn resolves the default now — the last pick if the
        catalog still offers it to this person (and its effort if the model still takes it), else
        the next default, silently — and stores it as this chat's own, without a restart: the
        worker this turn starts already runs exactly that. ``("", "")`` (no catalog, or nothing
        open) leaves the dispatch to decide and refuse as it always has."""
        catalog = dispatcher.catalog
        if catalog.empty:
            return "", ""
        try:
            ctx = dispatch_mod.route_context(dispatcher.resolve_model_config(subject) or {},
                                             last_pick=_last_pick(subject))
            chosen, _level = catalog.choose_with_effort("", "", ctx,
                                                        admin=lambda: dispatcher.is_admin(subject),
                                                        person=lambda: dispatcher.identities(subject))
            held = (chosen["id"], catalog.remembered_effort(chosen, ctx))
            sess.set_model(subject, session, held[0], held[1], restart=False)
            return held
        except model_providers.ModelChoiceFault:
            return "", ""
        except Exception:  # noqa: BLE001 — holding is a convenience; the turn runs on the default
            logger.warning("default model not held for subject=%s session=%s", subject, session)
            return "", ""

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
        effort = str(body.effort or "").strip()
        catalog = dispatcher.catalog
        if mid or effort:
            if catalog.empty:
                raise HTTPException(status_code=409, detail="this deployment offers no model "
                                                            "catalog; its model is set by the operator")
            ctx = dispatch_mod.route_context(dispatcher.resolve_model_config(subject) or {},
                                             last_pick=_last_pick(subject))
            try:
                # The effort is checked against the model it will run on — the pick, else the
                # person's default — so a stored effort is always one the next turn can send.
                chosen, _level = catalog.choose_with_effort(
                    mid, effort, ctx, admin=lambda: dispatcher.is_admin(subject),
                    person=lambda: dispatcher.identities(subject))
            except model_providers.ModelChoiceFault as fault:
                return JSONResponse(status_code=fault.http_status,
                                    content={"detail": fault.sentence(), "fault": fault.as_dict()})
        changed = sess.set_model(subject, session, mid, effort)
        if mid or effort:
            # THE LAST PICK IS THE PERSON'S DEFAULT FOR NEW CHATS (founder 2026-10-13): the model
            # this chat now runs on and the effort picked with it. "Follow my default" (both
            # empty) picks nothing, so it leaves the memory as it was.
            try:
                sess.remember_pick(subject, chosen["id"], effort)
            except Exception:  # noqa: BLE001 — the chat's pick is stored; the memory is a convenience
                logger.warning("last model pick not stored for subject=%s", subject)
        return {"ok": True, "session": session, "model": mid or None, "effort": effort or None,
                "changed": changed}
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
        title = chat_label_mod.strip_markers(title)
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

    def _row_visible(subject: str, row: str, request: Request) -> bool:
        """May this person mark this row? Only a row their own rail can show (P20, default-deny).

        A chat is theirs when the session is in their own index, or when it is a meeting's own
        chat (`meet-<id>`) on a meeting they can read — that chat exists in the browser before its
        first turn reaches the index. A meeting is theirs when THE ONE meeting access decision
        (owner, share recipient, member of its workspace) returns the row. Any failure denies."""
        kind, _, ref = row.partition(':')
        try:
            if kind == 'c':
                if any(r['session'] == ref for r in sess.list(subject)):
                    return True
                # a shared session someone added this person to (ADR-0048) is on their rail too
                if session_store is not None and session_store.can_read(ref, subject):
                    return True
                ref = ref[len(_MEET_SESSION_PREFIX):] if ref.startswith(_MEET_SESSION_PREFIX) else ''
            if not ref.isdigit():
                return False
            return _meeting_access(subject, ref, within=lambda slug: reads_within(request, slug)) is not None
        except Exception:  # noqa: BLE001 — a lookup that fails narrows to deny, never opens
            logger.warning("rail mark visibility check failed subject=%s row=%s", subject, row)
            return False

    def _legacy_marks(subject: str):
        """The person's old pin/hide marks and rail order, which their folders take in once."""
        return lambda: (sess.rail_marks(subject), sess.rail_order(subject))

    def _folders_store():
        if session_store is None:
            raise HTTPException(503, detail={'refused': 'folders_unavailable',
                                             'message': 'Chat folders need the session store, which this deployment has off.'})
        return session_store

    def _folder_refusal(exc: "chat_folders.FolderError"):
        code = 404 if exc.reason == 'no_such_folder' else 422
        return HTTPException(code, detail={'refused': exc.reason, 'message': str(exc)})

    def _mark(subject: str, row: str, *, pinned=None, hidden=None) -> dict:
        """Pin/hide one row: over the folders when the session store is on, else the old marks."""
        if session_store is None:
            return sess.mark_row(subject, row, pinned=pinned, hidden=hidden)
        return chat_folders.set_mark(session_store, subject, row, pinned=pinned, hidden=hidden,
                                     legacy=_legacy_marks(subject))

    @router.get("/api/user/pinned-pages")
    def read_pinned_pages(request: Request):
        """The person's pinned pages, in order: tabs in every one of their chats (#1783)."""
        return {'pages': sess.pinned_pages(subject_of(request))}

    @router.put("/api/user/pinned-pages")
    def save_pinned_pages(request: Request, body: PinnedPagesBody):
        """Replace the person's pinned pages. A path is workspace-relative: no `..`, no leading `/`."""
        seen, pages = set(), []
        for p in body.pages:
            parts = p.path.split('/')
            if p.path.startswith('/') or any(x in ('', '.', '..') for x in parts):
                raise HTTPException(422, 'A pinned page is a workspace-relative path')
            key = (p.slug or '', p.path)
            if key in seen:
                continue
            seen.add(key)
            pages.append({'slug': p.slug or None, 'path': p.path, 'label': p.label})
        return {'pages': sess.pinned_pages(subject_of(request), pages)}

    def _company_appearance() -> dict:
        try:
            from control_plane import budgets as _budgets  # noqa: PLC0415
            pol = _budgets.read(_global_root() if _global_root else None)
        except Exception:  # noqa: BLE001 — no company layer: the product's own defaults
            pol = {}
        out = {}
        for field, key in (("accent", "appearance_accent"), ("font_ui", "appearance_font_ui"), ("font_reading", "appearance_font_reading")):
            v = appearance_value(field, pol.get(key))
            if v:
                out[field] = v
        return out

    def _appearance_answer(subject: str) -> dict:
        user = {k: v for k, v in sess.appearance(subject).items() if appearance_value(k, v)}
        company = _company_appearance()
        return {"user": user, "company": company, "effective": {**company, **user}}

    @router.get("/api/user/appearance")
    def read_appearance(request: Request):
        """The person's appearance (Settings → Appearance): their own choice over the company default
        in `_global/POLICIES.md`. `effective` is what to paint; a field missing there is the
        product's default."""
        return _appearance_answer(subject_of(request))

    @router.put("/api/user/appearance")
    def save_appearance(request: Request, body: AppearanceBody):
        """Change the person's appearance: the fields sent change, null returns one to the company
        default. A value outside the vocabulary is refused, never stored."""
        subject = subject_of(request)
        cur = sess.appearance(subject)
        for field in body.model_fields_set:
            raw = getattr(body, field)
            if raw is None:
                cur.pop(field, None)
                continue
            v = appearance_value(field, raw)
            if v is None:
                raise HTTPException(422, detail=f"{field}: not one of the choices")
            cur[field] = v
        sess.appearance(subject, cur)
        return _appearance_answer(subject)

    @router.get("/api/chat/marks")
    def read_chat_marks(request: Request):
        """The caller's pinned and hidden rail rows: `{pinned: [row], hidden: [row]}`."""
        subject = subject_of(request)
        if session_store is None:
            return sess.rail_marks(subject)
        return chat_folders.marks(session_store, subject, legacy=_legacy_marks(subject))

    @router.get("/api/chat/folders")
    def chat_folders_list(request: Request):
        """List the person's chat folders and where each of their chats is filed.

        `folders` are the rail's sections in drawing order: `pinned`, `shared` (chats other people
        added the person to), the person's own folders (`routines` is the automatic one for routine
        chats), `ungrouped`, `hidden`. `chats` is every
        chat on the person's rail with its `row` (what `chat_move` takes), its `session` (what `chat_read`
        reads), its `label` and the `folder`
        it shows in. Folders are the person's own: on a shared chat each participant files it in
        their own folders, and nobody sees another's. Use it before `chat_move` to find rows by
        name, e.g. every chat whose label mentions a client."""
        subject = subject_of(request)
        store = _folders_store()
        got = chat_folders.listing(store, subject, legacy=_legacy_marks(subject))
        have = {f['id'] for f in got['folders']}
        chats = []
        try:
            rows = _with_stored(subject, _labelled(subject, sess.list(subject)))
        except Exception:  # noqa: BLE001 — the folders still answer without the chat names
            logger.warning("chat folders: listing the rail failed subject=%s", subject)
            rows = []
        for r in rows:
            sid = str(r.get('session') or '')
            if not sid:
                continue
            key = f'c:{sid}'
            folder = got['filings'].get(key) or (
                chat_folders.SHARED if r.get('owner') and r.get('owner') != subject and chat_folders.SHARED in have
                else chat_folders.ROUTINES if r.get('routine') and chat_folders.ROUTINES in have
                else chat_folders.UNGROUPED)
            # `session` is the id `chat_read` takes (it also takes the `row`)
            chats.append({'row': key, 'session': sid, 'label': r.get('label') or r.get('title') or '',
                          'folder': folder, 'last_active': r.get('last_active')})
        return {**got, 'chats': chats}

    @router.post("/api/chat/folders")
    def chat_folder_create(request: Request, body: ChatFolderCreateBody):
        """Make a chat folder on the person's own rail, named as they said it. Asking for a name they
        already have returns that folder (`created: false`), so it is safe to call before filing.
        Then file chats into it with `chat_move`, and confirm in one line."""
        subject = subject_of(request)
        try:
            return chat_folders.create(_folders_store(), subject, body.name, legacy=_legacy_marks(subject))
        except chat_folders.FolderError as exc:
            raise _folder_refusal(exc)

    @router.post("/api/chat/folders/update")
    def chat_folder_update(request: Request, body: ChatFolderUpdateBody):
        """Rename a folder, fold it shut or open, or move it (`position` 0 is first). Pinned,
        Ungrouped and Hidden only fold."""
        try:
            return chat_folders.update(_folders_store(), subject_of(request), body.folder, name=body.name,
                                       collapsed=body.collapsed, position=body.position)
        except chat_folders.FolderError as exc:
            raise _folder_refusal(exc)

    @router.post("/api/chat/folders/delete")
    def chat_folder_delete(request: Request, body: ChatFolderDeleteBody):
        """Dissolve one of the person's folders. Its chats move to Ungrouped; no chat is deleted.
        Returns the rows that moved."""
        try:
            return {'ok': True, 'moved': chat_folders.delete(_folders_store(), subject_of(request), body.folder)}
        except chat_folders.FolderError as exc:
            raise _folder_refusal(exc)

    @router.post("/api/chat/move")
    def chat_move(request: Request, body: ChatMoveBody):
        """Move chats on the person's own rail: into a folder, to `pinned`, back to `ungrouped`,
        or to `hidden` (that is how a chat is hidden; moving it out un-hides it). A chat lives in one
        place, so it leaves wherever it was. `rows` come from `chat_folders_list`. Only rows the
        person's rail can show may be filed (`404` otherwise); moving to `ungrouped` always works."""
        subject = subject_of(request)
        store = _folders_store()
        rows = [r if ':' in r else f'c:{r}' for r in body.rows]
        if body.to != chat_folders.UNGROUPED:
            hidden = [r for r in rows if not _row_visible(subject, r, request)]
            # A CHAT THIS PERSON STARTED THAT HAS HAD NO TURN YET is on their rail (the browser keeps
            # it) but not in the server's index, so it was refused — "Not a chat on this person's
            # rail" on a chat that plainly was (#1783). Filing it indexes it for this person: their
            # own index, their own preference, nothing read or granted. A meeting's chat (`meet-<id>`)
            # still needs the meeting to be theirs, and a `m:` row is never indexed this way.
            for r in list(hidden):
                kind, _, ref = r.partition(':')
                if kind == 'c' and not ref.startswith(_MEET_SESSION_PREFIX):
                    try:
                        sess.upsert(subject, ref)
                        hidden.remove(r)
                    except Exception:  # noqa: BLE001 — stays refused, said below
                        logger.warning("chat move: indexing an unindexed chat failed subject=%s", subject)
            if hidden:
                raise HTTPException(404, detail={'refused': 'row_not_found', 'rows': hidden,
                                                 'message': 'That chat could not be found. Reload the page and try again.'})
        try:
            return chat_folders.file_rows(store, subject, rows, body.to, before=body.before,
                                     legacy=_legacy_marks(subject))
        except chat_folders.FolderError as exc:
            raise _folder_refusal(exc)

    @router.post("/api/chat/marks")
    def mark_chat_row(request: Request, body: ChatMarkBody):
        """Pin, unpin, hide or unhide one row of the caller's own rail and return their marks.

        Setting a mark needs a row the caller can see (`404` otherwise). Clearing one does not: it
        only ever removes the caller's own mark, for a row that may have gone since."""
        if body.pinned is None and body.hidden is None:
            raise HTTPException(422, 'Name a mark to change: pinned or hidden')
        subject = subject_of(request)
        row = body.row if ':' in body.row else f'c:{body.row}'
        if (body.pinned or body.hidden) and not _row_visible(subject, row, request):
            raise HTTPException(404, 'Row not found')
        try:
            return _mark(subject, row, pinned=body.pinned, hidden=body.hidden)
        except chat_folders.FolderError as exc:
            raise _folder_refusal(exc)
        except OverflowError:
            raise HTTPException(422, 'Too many marked rows')

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
        # one membership read scope for the whole listing: the people it names share their scans
        with _membership_mod.read_scope():
            return _list_sessions(subject)

    def _list_sessions(subject: str) -> dict:
        # the index is read ONCE when the purge removes nothing (2026-10-18: it was read twice)
        listed, attempted = sess.list(subject), []
        _purge_incognito(subject, rows=listed, attempted=attempted)
        if attempted:
            listed = sess.list(subject)
        labelled = _labelled(subject, listed)
        turns_of = _listing_turns_of(subject, [r for r in labelled if _maybe_empty(r)])
        rows = [r for r in labelled if not _is_empty_chat(subject, r, turns_of=turns_of)]
        # A CHAT NOBODY NAMED IS NAMED FROM WHAT WAS SAID (founder 2026-10-15): a few per listing, in
        # the background, by the cheap model within the spend cap; never over the person's own name.
        try:
            chat_autotitle.schedule(subject, rows, turns_of=lambda s: _turns_of(subject, s),
                                    name=lambda s, t, n=None, over_token=False: sess.name(subject, s, t, human=bool(over_token), turns=n),
                                    complete=_title_model(subject))
        except Exception:  # noqa: BLE001 — a name is furniture; the rail outranks it
            logger.warning("auto-title scheduling failed for subject=%s", subject)
        return {"sessions": _with_stored(subject, rows)}

    def _maybe_empty(row: dict) -> bool:
        """The cheap half of ``_is_empty_chat``: could this row be empty at all?"""
        sid = str(row.get("session") or "")
        # a record no turn ever titled carries its own id as its title (a turn titles it, #1783)
        if not sid or sid.startswith(_MEET_SESSION_PREFIX):
            return False
        if str(row.get("title") or "").strip() not in ("", sid) or (row.get("named_title") or "").strip():
            return False
        return not (row.get("meeting") or row.get("routine") or row.get("scaffold") or row.get("concern"))

    def _listing_turns_of(subject: str, rows: "list[dict]"):
        """``_turns_of`` for the rows of ONE listing, with what every row would read alone read once:
        the session store's records for all of their names in one query, and the folders a thread's
        pointer could be in listed once (``WorkspaceReader.pointer_names``). A row with no store
        version and no pointer anywhere has no turns — the answer ``_turns_of`` gives it after
        sweeping every workspace folder at the root, which is what made a listing with many
        nameless chats slow (live app.dev 2026-10-18). Anything else is read exactly as before."""
        names = [str(r.get("session") or "") for r in rows]
        names = {n for n in names if n and not n.startswith(session_sync.STORE_ID_PREFIX)}
        stored: "dict | None" = None
        if session_store is not None and names:
            try:
                stored = session_store.by_names(subject, names)
            except Exception:  # noqa: BLE001 — read one by one, as before
                stored = None
        pointers: list = []

        def turns_of(sid: str) -> list:
            if stored is None or sid.startswith(session_sync.STORE_ID_PREFIX) or sid not in names:
                return _turns_of(subject, sid)
            row = stored.get(sid)
            if row is not None:
                got = session_sync.history(session_store, row)
                if got is not None:
                    return got
            if sid != "main":
                if not pointers:
                    pointers.append(wsr.pointer_names(subject))
                if pointers[0] is not None and f"{sid}.session" not in pointers[0]:
                    return []
            return wsr.history(subject, sid)
        return turns_of if session_store is not None else (lambda sid: _turns_of(subject, sid))

    def _is_empty_chat(subject: str, row: dict, turns_of=None) -> bool:
        """A CHAT NOBODY WROTE IN IS NOT A ROW (#1783, founder 2026-10-15: "empty new chats after every
        new attempt to do something in the desktop"). Writing a draft's scope — its target, mounts or
        reads — creates its index record before anybody said anything; such a record is not listed.

        Empty = nothing it was made for (no meeting, routine, ask or concern), no name recoverable from
        a first message (its stored title is empty or its own id — a turn titles it), no file attached in it, and no turn in its history. Only rows
        that pass the cheap tests have their history read, so a listing reads no history for a chat
        that has a name. Fail-open: a history that cannot be read keeps the row."""
        if not _maybe_empty(row):
            return False
        sid = str(row.get("session") or "")
        try:
            if sess.attachments_of(subject, sid):
                return False
            return not (turns_of(sid) if turns_of is not None else _turns_of(subject, sid))
        except Exception:  # noqa: BLE001 — unknown is not empty
            return False

    def _turns_of(subject: str, sid: str) -> list:
        """A chat's turns: the session store's conversation, else the workspace layout's."""
        if session_store is not None:
            row = session_sync.resolve(session_store, subject, sid, create=False)
            if row is not None:
                got = session_sync.history(session_store, row)
                if got is not None:
                    return got
        return wsr.history(subject, sid)

    def _title_model(subject: str):
        """The cheap model call for a chat's title — None when the person's budget says stop or no
        model is configured. Every call is counted (`usage` kind `title`)."""
        from control_plane import budgets, home_model  # noqa: PLC0415
        from shared import usage as usage_mod  # noqa: PLC0415
        catalog = getattr(dispatcher, "catalog", None)
        if catalog is None or getattr(catalog, "empty", True):
            return None

        def complete(system: str, user: str):
            gdir = _global_root()
            pol = budgets.read(gdir)
            ucli = budgets.usage_client(redis_url)
            if budgets.verdict(pol, usage_mod.rolling(ucli, subject), background=True).get("stop"):
                return None
            out = home_model.complete_json(catalog, system, user, named=pol.get("model_home", "cheapest"),
                                           allowed=budgets.allowed_list(pol))
            if out and out.get("_tokens"):
                llm_budget_mod.record_local(subject, "title", out.get("_usage") or {"prompt_tokens": int(out["_tokens"])},
                                            fallback_client=ucli)
            return out
        return complete

    def _incognito_days() -> int:
        """`incognito_retention_days` in `_global/POLICIES.md` (Vexa-ai/vexa#1783); 7 when unset."""
        try:
            from workspaces.shared import workspace_paths as _wpaths  # noqa: PLC0415
            text = _wpaths.read_text_inside(system_mounts.global_root(settings, wsr.root), "POLICIES.md",
                                            max_bytes=1_000_000) or ""
            m = re.search(r"^incognito_retention_days:\s*(\d+)\s*$", text, re.M)
            return max(0, int(m.group(1))) if m else INCOGNITO_RETENTION_DAYS
        except Exception:  # noqa: BLE001 — no policy file: the default
            return INCOGNITO_RETENTION_DAYS

    def _purge_incognito(subject: str, now: "float | None" = None, rows: "list | None" = None,
                         attempted: "list | None" = None) -> list:
        """INCOGNITO CHATS ARE DELETED ON SCHEDULE: one idle longer than the company's retention is
        removed the next time the person's rail is read — conversation, index row and marks, as a
        person's own delete would. Fail-soft: a purge that cannot run costs nothing else.

        ``rows`` is ``sess.list(subject)`` when the caller already read it; every chat the purge
        started on is appended to ``attempted`` (then the caller's ``rows`` are stale)."""
        cutoff = (now if now is not None else time.time()) - _incognito_days() * 86400
        gone = []
        if rows is None:
            try:
                rows = sess.list(subject)
            except Exception:  # noqa: BLE001
                return gone
        # ONE round trip for every chat's profile, not one per chat (2026-10-18)
        try:
            profile = sess.profiles(subject, [r.get("session") or "" for r in rows])
        except Exception:  # noqa: BLE001 — asked one by one below
            profile = {}
        for row in rows:
            s_id = row.get("session") or ""
            try:
                incognito = (profile[s_id] == "incognito") if s_id in profile else sess.is_incognito(subject, s_id)
                if not incognito or float(row.get("last_active") or 0) > cutoff:
                    continue
                if attempted is not None:
                    attempted.append(s_id)
                wsr.drop_history(subject, s_id, extra_roots=[])
                # ITS ATTACHMENTS GO WITH IT: an incognito chat's files live in `_system` only.
                for _path, _ws in (sess.attachments_of(subject, s_id) or {}).items():
                    if _ws == system_mounts.SYSTEM_SLUG:
                        from workspaces.shared import workspace_paths as _wp  # noqa: PLC0415
                        try:
                            _wp.unlink_inside(system_mounts.ensure_system_workspace(wsr.root, subject), _path)
                        except Exception:  # noqa: BLE001
                            pass
                if session_store is not None:
                    # THE SESSION STORE'S COPY GOES TOO (ADR-0048): incognito is "not retained", and
                    # once chats live in the store that is where the conversation is.
                    _row = session_sync.resolve(session_store, subject, s_id, create=False)
                    if _row is not None:
                        session_store.delete(_row["id"])
                sess.drop(subject, s_id)
                sess.mark_row(subject, f"c:{s_id}", pinned=False, hidden=False)
                gone.append(s_id)
            except Exception:  # noqa: BLE001
                logger.warning("incognito purge skipped one chat for subject=%s", subject)
        if gone:
            logger.info("incognito purge subject=%s removed=%d", subject, len(gone))
        return gone
    @router.delete("/api/sessions/{session}")
    def delete_session(session: Annotated[str, Path(pattern=CHAT_SESSION_PATTERN)], request: Request):
        """DELETE ONE OF YOUR CHATS, for good (`DELETE /api/sessions/{session_id}`, api.v1).

        Removes the session from your index, its conversation (the harness transcript and its
        continuity pointer) and your own marks and place in the rail order. Pages and files the chat
        wrote into a workspace stay: they belong to the workspace. A meeting the chat was about is not
        deleted — that is the meeting owner's act — but its row is hidden on YOUR rail so the meeting
        does not come straight back in the chat's place; unhide it from the Hidden view.

        `404` for a session that is not in your index; `409` while something you submitted to it is
        still queued, because the queued turn would bring the chat back; `403` for an agent acting
        for the person — deleting a conversation is the person's own act."""
        refuse_delegated(request, reason="delegated_delete",
                         instruction="Deleting a chat is the person's own act. Tell them which chat and stop.")
        subject = subject_of(request)
        row = next((r for r in sess.list(subject) if r['session'] == session), None)
        if row is None:
            raise HTTPException(404, 'Chat not found')
        try:
            gen = sess.mount_gen(subject, session)
        except Exception:  # noqa: BLE001
            gen = 0
        unit_id = units.chat_unit_id(subject, session, gen)
        if inbox_pending(redis_url, unit_id, _unit_key(unit_id)):
            raise HTTPException(409, 'This chat still has queued messages. Wait for them to run, then delete it.')
        extra: list = []
        try:
            ms = active_workspaces(wsr.root, subject) + shared_active_mounts(wsr.root, subject, mindex.list(subject))
            extra = [m.path for m in ms]
        except Exception:  # noqa: BLE001 — narrows the sweep to the anchored roots, never widens it
            logger.warning("mount resolution for delete failed subject=%s", subject)
        try:
            files = wsr.drop_history(subject, session, extra_roots=extra)
        except Exception:  # noqa: BLE001
            logger.exception("deleting chat history failed subject=%s session=%s", subject, session)
            raise HTTPException(500, 'Could not delete the conversation. Nothing was removed from your list.')
        sess.drop(subject, session)
        sess.mark_row(subject, f'c:{session}', pinned=False, hidden=False)
        if session_store is not None:
            try:
                chat_folders.forget(session_store, subject, f'c:{session}')
            except Exception:  # noqa: BLE001 — the chat is gone; a stale filing names nothing
                logger.warning("chat delete: dropping the folder filing failed subject=%s", subject)
        order = sess.rail_order(subject)
        if f'c:{session}' in order or session in order:
            sess.rail_order(subject, [k for k in order if k not in (f'c:{session}', session)])
        meeting = (session[len(_MEET_SESSION_PREFIX):] if session.startswith(_MEET_SESSION_PREFIX)
                   else str(row.get('meeting') or ''))
        hidden_meeting = None
        if meeting.isdigit():
            hidden_meeting = f'm:{meeting}'
            _mark(subject, hidden_meeting, hidden=True)
        return {'ok': True, 'session': session, 'files_removed': files, 'hidden_meeting': hidden_meeting}


    def _with_stored(subject: str, rows: "list[dict]") -> "list[dict]":
        """ADR-0048: the rail reads sessions from the store too. A thread of the caller's own gains
        `session_id` and `participants`; a session somebody ADDED them to joins the rail under its
        store id, marked `shared`, with the caller's `role`. Store off or unreadable: unchanged."""
        if session_store is None:
            return rows
        try:
            stored = session_store.list_for(subject)
        except Exception as exc:  # noqa: BLE001 — the rail outranks its own enrichment
            logger.warning("session store unreadable for the rail (%s)", type(exc).__name__)
            return rows
        by_name = {r["session"]: r for r in rows}
        out = list(rows)
        # ONE READ PER KIND, NOT ONE PER SESSION (live app.dev 2026-10-18: 262 chats, three store
        # reads each — participants, room, room again — were ~0.5 s of every listing).
        people_of = session_store.participants_of([st["id"] for st in stored])
        for st in stored:
            count = len(people_of.get(st["id"], ()))
            bound = session_store_mod.read_flow(st.get("flow"))
            if st["owner"] == subject and st["name"] in by_name:
                by_name[st["name"]].update(session_id=st["id"], participants=count, role="owner",
                                           shared=count > 1)
                if bound and not by_name[st["name"]].get("routine"):
                    # the store remembers a routine thread the index row has lost the mark of
                    by_name[st["name"]]["routine"] = {**bound, "step": "agent_run"}
                continue
            if st["owner"] == subject and not st["name"].startswith("s-"):
                continue     # an own thread the index no longer lists (dropped): not resurrected
            if st["id"] in by_name:
                # a shared session this person has posted in is in their own index too: one row
                by_name[st["id"]].update(session_id=st["id"], participants=count, shared=True,
                                         role=st.get("role"), owner=st["owner"])
                continue
            out.append({"session": st["id"], "session_id": st["id"], "title": st["title"] or "",
                        "label": st["title"] or "", "created": st["created"],
                        "last_active": st["last_active"], "workspaces": [], "scaffold": None,
                        "touched": True, "target": None, "meeting": None, "owner": st["owner"],
                        "role": st.get("role"), "participants": count, "shared": True})
        # WHOSE IT IS (#1783): a chat somebody else owns carries their name, so the rail can show
        # their initials on its "shared" chip. Named the way the @-mention picker names people.
        from control_plane import people_directory, pair_rooms  # noqa: PLC0415
        names: dict = {}

        def name_of(principal: str) -> str:
            """A person's name, read once per listing however many rows carry them."""
            if principal not in names:
                try:
                    names[principal] = people_directory.name_of(wsr.root, principal, mindex.list)
                except Exception:  # noqa: BLE001 — a nameless person is still a person
                    names[principal] = None
            return names[principal]

        for r in out:
            owner = r.get("owner")
            if owner and owner != subject:
                r["owner_name"] = name_of(owner) or ""
        # A ROOM'S ROW SAYS WHAT KIND OF ROOM IT IS (S4 x UI-1, integration): its `visibility`, and for a
        # room of two (`direct`) the OTHER person and the caller's unread count — so the sidebar names
        # it by that person without one members read per room.
        try:
            rooms = session_store.rooms([r["session_id"] for r in out if r.get("session_id")])
        except Exception:  # noqa: BLE001 — rows without their room facts are still rows
            rooms = {}
        pairs: dict = {}
        for r in out:
            sid = r.get("session_id")
            room = rooms.get(sid) if sid else None
            if not room:
                continue
            ws = room.get("workspace")
            if ws and ws not in pairs:
                try:
                    pairs[ws] = bool(pair_rooms.is_pair(wsr.root, ws))
                except Exception:  # noqa: BLE001
                    pairs[ws] = False
            pair = bool(ws) and pairs[ws]
            r["visibility"] = "direct" if pair else room.get("visibility")
            if pair:
                try:
                    humans = [p["principal"] for p in people_of.get(sid, ()) if p["kind"] == "human"] \
                        if sid in people_of else session_store.humans(sid)
                    other = next((h for h in humans if str(h) != str(subject)), None)
                    if other:
                        r["other_subject"] = str(other)
                        got = name_of(str(other))
                        if got is None:
                            raise RuntimeError("name unreadable")
                        r["other_name"] = got
                    r["unread"] = int(session_store.unread(sid, str(subject)))
                except Exception:  # noqa: BLE001
                    pass
        out.sort(key=lambda r: float(r.get("last_active") or 0), reverse=True)
        return out

    def _qualified(turns: list, owner: str) -> list:
        """Old messages' bare file references, qualified against the one workspace their session
        wrote into (`workspace_reader.qualified_history`) — never fatal: the turns as stored on error.
        ``owner`` is whose desk `personal` named when those writes ran."""
        from control_plane.workspace_reader import qualified_history  # noqa: PLC0415
        try:
            return qualified_history(turns, str(owner))
        except Exception:  # noqa: BLE001 — a reference pass outranks nothing; show what is stored
            logger.exception("qualifying history references failed")
            return turns

    @router.get("/api/sessions/{session}/history")
    def session_history(session: Annotated[str, Path(pattern=CHAT_SESSION_PATTERN)],
                        request: Request):
        """The session's prior conversation, as simplified turns the terminal can render (so clicking a
        saved chat re-opens its history). A session the caller has no thread for is 404; a thread whose
        transcript is missing or empty returns ``{turns: []}``; an invalid subject/session never 500s."""
        subject = subject_of(request)
        session = sess.chat_for(subject, session)      # a meeting started from a chat reads that chat
        # ADR-0048: a stored conversation is read from the store; the workspace layout below is the
        # one-release fallback for a thread the store holds no version of yet.
        if session_store is not None:
            try:
                row = session_sync.resolve(session_store, subject, session, create=False)
            except Exception as exc:  # noqa: BLE001
                logger.warning("session store unreadable for history (%s)", type(exc).__name__)
                row = None
            if row is None and session.startswith(session_sync.STORE_ID_PREFIX):
                raise HTTPException(status_code=404, detail="session not found")
            if row is not None:
                try:
                    stored_turns = session_sync.history(session_store, row)
                    messages = session_store.notes(row["id"])
                except Exception:  # noqa: BLE001 — history is best-effort, as below
                    logger.exception("reading the stored conversation failed session=%s", row["id"])
                    stored_turns, messages = None, []
                # A reply the store refused (a version on a stale head) is SAID here, typed, until
                # the next save lands — it never just goes missing (S5).
                try:
                    refused = session_sync.recorded_fault(session_sync._redis_for(redis_url), row["id"])
                except Exception:  # noqa: BLE001
                    refused = None
                if stored_turns is not None or row["owner"] != subject:
                    out = {"turns": _qualified(stored_turns or [], row["owner"]), "messages": messages,
                           "session_id": row["id"]}
                    if refused:
                        out["fault"] = session_sync.shown_fault(refused)
                    return out
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
        return {"turns": _qualified(turns, subject)}

    return router
