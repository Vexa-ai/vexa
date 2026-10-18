"""routers/ingress.py — The two internal doors a dispatch arrives through: `/invocations`, where the
internal tier hands over a unit.v1 dispatch, and `/events`, where
the internal tier hands over an event.v1 event that becomes one. Both bodies name the person the
turn runs as, so the CALLER is authenticated before either is read (`dispatch_sink.py`).

Moved out of `routers/chats.py` byte for byte; `build()` rebinds each dependency to the name the
handlers already used.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse
from jsonschema.exceptions import ValidationError

from control_plane import dispatch_sink
from control_plane import llm_budget as llm_budget_mod
from control_plane.ceiling import refusal, refused
from control_plane.events import event_to_invocation
from shared import units


#: what the chat reads when a meeting or room reaches its wake cap — instead of silence
PAUSED_LINE = "Vexa paused listening: limit reached"


def wake_count(cli, subject: str, key: str, now: float) -> int:
    """This meeting's (or room's) wakes in the current HOUR of meeting, counted from its first wake —
    the cap is a cost guard per hour, not a lifetime budget (app.dev 2026-10-16: 12 wakes went in
    four minutes and the sensor was silent for the rest of the call)."""
    start_key = f"signals:wakes:{subject}:{key}:start"
    cli.set(start_key, int(now), nx=True, ex=24 * 3600)
    try:
        start = float(cli.get(start_key) or now)
    except (TypeError, ValueError):
        start = now
    k = f"signals:wakes:{subject}:{key}:h{int(max(0.0, now - start) // 3600)}"
    n = int(cli.incr(k))
    if n == 1:
        cli.expire(k, 2 * 3600)
    return n


def say_paused(store, r, subject: str, session: str) -> None:
    """One quiet line in the chat (and on a room's stream, as the agent's — context, never scored)."""
    if store is None or not session:
        return
    from control_plane import rooms as _rooms, session_sync as _ss  # noqa: PLC0415
    try:
        row = _ss.resolve(store, str(subject), session, create=False)
        if row is None:
            return
        note = store.add_note(row["id"], "vexa", PAUSED_LINE)
        if _rooms.is_room(store, row["id"]):
            _rooms.publish(r, row, note, author_name="Vexa", author_kind="agent")
    except Exception:  # noqa: BLE001 — the line is a courtesy; the refusal stands without it
        pass


def note_instruction(page: dict) -> str:
    """What a meeting's signal turn does with its meeting's NOTE — the one page it may write."""
    where = f"workspace `{page['workspace']}`" if page.get("workspace") else "the person's home"
    return (
        f"\n\nTHE MEETING NOTE is `{page['path']}` in {where} — this meeting's own page, and the only "
        "page you may write; everything else you propose. If the lines above hold something worth "
        "keeping — a decision, a commitment (who does what, by when), an open question, or a key "
        "fact — read the note, then add each NEW item as one `- ` line inside its region: "
        "`<!-- meeting:decisions:start -->` … `<!-- meeting:decisions:end -->` under `## Decisions`, "
        "`<!-- meeting:commitments:start -->` … `end` under `## Commitments`, "
        "`<!-- meeting:questions:start -->` … `end` under `## Open questions`, key facts in "
        "`<!-- meeting:about:start -->` … `end` under `## What this is about`. A missing region is "
        "appended at the end with its heading. Never repeat an item already there (an item said "
        "again is updated in place), and leave every other line exactly as it is. If there is "
        "nothing worth keeping, do not touch the note.")


def build(**d) -> APIRouter:
    """The ingress routes, bound to one app's dependencies."""
    router = APIRouter()
    dispatcher = d['dispatcher']
    settings = d['settings']
    _global_root = d.get('_global_root')
    redis_url = d.get('redis_url')
    notifier = d.get('notifier')
    # TRANSCRIPT SIGNALS: a `meeting.signal` with `target.chat` runs in that chat. `.get` throughout
    # so a harness that mounts this router alone still serves the doors it always served.
    sess = d.get('sess')
    session_store = d.get('session_store')
    wsr = d.get('wsr')
    mindex = d.get('mindex')
    _meeting_owner_lookup = d.get('_meeting_owner_lookup')

    def _call_row(subject: str):
        """``meeting_id -> meetings row`` as ``subject`` may read it — how a room's live call is
        read for its audience (D27). ``None`` without a lookup: an unknown call, cross_org."""
        if _meeting_owner_lookup is None:
            return None
        return lambda mid: _meeting_owner_lookup(subject, mid)
    _meeting_note_recorder = d.get('_meeting_note_recorder')

    def _internal_secret() -> str:
        return settings.internal_api_secret.get_secret_value() if settings is not None else ""

    def _sink_caller(request: Request) -> str:
        """``"internal"`` for the internal tier, else ``""`` — refused. See `dispatch_sink.py`."""
        if dispatch_sink.internal_caller(_internal_secret(), request.headers.get("x-internal-secret", "")):
            return "internal"
        return ""

    def _refuse(request: Request, status: int, reason: str, instruction: str) -> HTTPException:
        """A refusal in the shape every other one has, logged once (`ceiling.refused`)."""
        return refused(request, status, refusal(reason, instruction), reason=reason)

    @router.post("/invocations", status_code=202)
    def invocations(request: Request, invocation: dict = Body(...)):
        """The dispatcher sink — the internal tier POSTs a unit.v1 dispatch here. The body names the
        person the turn runs as, so the CALLER is authenticated before the body is read."""
        if not _sink_caller(request):
            raise _refuse(request, 401, "internal_tier_required",
                          "the dispatch sink takes the internal tier")
        try:
            workload_id = dispatcher.dispatch(invocation)
        except ValidationError as e:  # non-conformant unit.v1 envelope — fail loud (P18)
            raise HTTPException(status_code=400, detail=f"invalid unit.v1 dispatch: {e.message}")
        except llm_budget_mod.BudgetExhausted as refusal:  # the spend cap (llm_budget.py)
            return JSONResponse(status_code=refusal.http_status, content=refusal.as_dict())
        return {"workload_id": workload_id}
    @router.post("/events", status_code=202)
    def events(request: Request, event: dict = Body(...)):
        # The event names the person it is about; only the internal tier may say who that is.
        if _sink_caller(request) != "internal":
            raise _refuse(request, 401, "internal_tier_required", "the event sink takes the internal tier")
        try:
            invocation = event_to_invocation(event)
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=f"invalid event.v1: {e.message}")
        except ValueError as e:  # no plan carried — fail loud (P18)
            raise HTTPException(status_code=422, detail=str(e))
        _event_limits(invocation)
        if invocation["trigger"] == "signal":
            return _signal_turn(request, event, invocation)
        try:
            workload_id = dispatcher.dispatch(invocation)
        except llm_budget_mod.BudgetExhausted as refusal:  # the spend cap (llm_budget.py)
            return JSONResponse(status_code=refusal.http_status, content=refusal.as_dict())
        return {"workload_id": workload_id, "trigger": invocation["trigger"]}

    def _event_limits(invocation: dict) -> None:
        """THE COMPANY'S LIMITS ON EVENT-STARTED RUNS (#1783, `budgets`): runs a day, and the token
        budget's background share. Read live; a run over either is refused, said on the bell."""
        from control_plane import budgets as _budgets  # noqa: PLC0415
        from shared import usage as _usage  # noqa: PLC0415
        subject = str(((invocation.get("identity") or {}).get("subject")) or "")
        if subject:
            pol = _budgets.read(_global_root() if callable(_global_root) else None)
            cli = _budgets.usage_client(redis_url)
            stop = _budgets.gate(subject, background=True, policy=pol, used=_usage.rolling(cli, subject),
                                 notifier=notifier)
            # a meeting signal's wakes are capped per meeting instead (`signals_wakes_per_meeting`)
            if (stop is None and invocation.get("trigger") != "signal"
                    and _usage.count_run(cli, subject, "events") > pol["event_agent_runs_per_day"] and cli):
                stop = {"refused": "event_runs_per_day", "note": f"more than {pol['event_agent_runs_per_day']} event-started runs today"}
            if stop:
                raise HTTPException(status_code=429, detail=stop)

    # ── A MEETING SIGNAL WAKES THE AGENT IN THE MEETING'S CHAT (transcript signals) ─────────────

    def _signal_policy(subject: Optional[str] = None) -> dict:
        from control_plane import home as _home, meeting_listen  # noqa: PLC0415
        try:
            root = _global_root() if callable(_global_root) else None
        except Exception:  # noqa: BLE001 — an unreadable company layer is the defaults (off)
            root = None
        return meeting_listen.policy(_home.read_global(root, "POLICIES.md"), subject=subject)

    def _wake_count(subject: str, row: str) -> int:
        """This meeting's wakes this hour of meeting (`wake_count`), counted here as well as in `signals`:
        the cap holds even when the service that should enforce it does not."""
        from control_plane import budgets as _budgets  # noqa: PLC0415
        cli = _budgets.usage_client(redis_url)
        if cli is None:
            return 0
        try:
            import time as _time  # noqa: PLC0415
            return wake_count(cli, str(subject), str(row), _time.time())
        except Exception:  # noqa: BLE001 — a count that cannot be kept refuses nothing
            return 0

    def _heard(chat: str, data: dict) -> None:
        """A ROOM shows at once that Vexa is on it: "Vexa heard you · thinking…" from the moment its
        wake is accepted (here), until the reply replaces it (`rooms.publish`)."""
        from control_plane import rooms as _rooms_h  # noqa: PLC0415
        try:
            if session_store is not None and _rooms_h.is_room(session_store, chat):
                _rooms_h.note_heard(_rooms_h.redis_for(redis_url), chat, str((data or {}).get("reason") or ""))
        except Exception:  # noqa: BLE001 — a courtesy line never fails the wake
            pass

    def _room_note(subject: str, chat: str, mrow, row: str):
        """The note of a call held in a room, minted in THAT room's workspace when it has none:
        ``{path, workspace}``, or None (not a room's call, or nothing could be written)."""
        if session_store is None or wsr is None:
            return None
        try:
            from pathlib import Path as _P  # noqa: PLC0415

            from control_plane import (meeting_mint as _mm, meeting_note as _mn, room_principal as _rpm,  # noqa: PLC0415
                                       rooms as _rr)
            from shared import meeting_doc as _md  # noqa: PLC0415
            from workspaces.shared import workspace_paths as _wp  # noqa: PLC0415
            try:
                chat = sess.chat_for(subject, chat) if sess is not None else chat
            except Exception:  # noqa: BLE001
                pass
            if not (_rr.is_room(session_store, chat) and _rr.enabled(_global_root, subject)):
                return None
            rp_ = _rpm.resolve(session_store, wsr.root, chat, speaker=subject)
            if rp_ is None or not rp_.workspace:
                return None
            wdir = _P(wsr.root) / rp_.workspace
            there = _mn.resolve_exact_in(wdir, mrow)
            rel = there or _mm.compose(mrow)
            if not there:
                f = _mn.row_facts(mrow)
                _wp.write_text_inside(wdir, rel, _md.scaffold(meeting=row, title=f["title"], native=f["native"],
                                                              date=f["day"]))
                if (wdir / ".git").is_dir():
                    from shared.gitexec import run_git as _git  # noqa: PLC0415
                    _git(wdir, "add", "--", rel)
                    _git(wdir, "-c", "user.name=vexa-agent", "-c", "user.email=agent@vexa.local",
                         "commit", "-q", "-m", f"meeting note for call {row}", "--", rel)
            if _meeting_note_recorder is not None:
                try:
                    _meeting_note_recorder(str(subject), str(row), rel, rp_.workspace)
                except Exception:  # noqa: BLE001 — the page is there; the row learns it later
                    pass
            return {"path": rel, "workspace": rp_.workspace}
        except Exception:  # noqa: BLE001 — no room note: the person's own mint below
            return None

    def _situation(subject: str, chat: str, mrow, room_p, page) -> str:
        """THE SITUATION the turn wakes into (`signal_situation`): gathered here, cheaply, before it
        starts — every source optional, none of them a model call."""
        import json
        import time as _t  # noqa: PLC0415
        from pathlib import Path

        from control_plane import meeting_listen  # noqa: PLC0415

        from control_plane import signal_situation as _sit  # noqa: PLC0415
        now = _t.time()
        r = None
        try:
            from control_plane import rooms as _rs  # noqa: PLC0415
            r = _rs.redis_for(redis_url)
        except Exception:  # noqa: BLE001
            pass
        # the call: the woken meeting, else the call the room is holding
        row = mrow if isinstance(mrow, dict) else None
        if row is None and session_store is not None and _meeting_owner_lookup is not None:
            try:
                held = session_store.active_call(chat)
                if held:
                    row = _meeting_owner_lookup(subject, str(held["meeting_id"]))
            except Exception:  # noqa: BLE001
                row = None
        transcript, speakers = [], []
        if isinstance(row, dict) and r is not None:
            try:
                seen: dict = {}
                for eid, f in reversed(r.xrevrange(f"tc:meeting:{row.get('id')}", count=400) or []):
                    p = json.loads(f.get("payload") or "{}")
                    for sgm in (p.get("segments") or []) if isinstance(p, dict) else []:
                        if sgm.get("completed") is not True or not str(sgm.get("text") or "").strip():
                            continue
                        at = float(sgm.get("abs_start_ms") or 0) / 1000 or float(str(eid).split("-")[0]) / 1000
                        seen[str(sgm.get("segment_id") or eid)] = (at, str(sgm.get("speaker") or "Someone"),
                                                                   str(sgm["text"]))
                transcript = [x for x in seen.values() if now - x[0] <= 300]
                speakers = list(dict.fromkeys(x[1] for x in sorted(seen.values())))
            except Exception:  # noqa: BLE001
                transcript = []
        call = None
        if isinstance(row, dict):
            data = row.get("data") if isinstance(row.get("data"), dict) else {}
            started = row.get("start_time") or row.get("created_at") or data.get("scheduled_at")
            try:
                from datetime import datetime as _dt  # noqa: PLC0415
                started_ts = (float(started) if isinstance(started, (int, float))
                              else _dt.fromisoformat(str(started).replace("Z", "+00:00")).timestamp()) if started else None
            except (TypeError, ValueError):
                started_ts = None
            people = [str(x.get("name") if isinstance(x, dict) else x) for x in (data.get("participants") or [])] or speakers
            call = {"platform": row.get("platform") or "", "title": data.get("title") or row.get("title") or "",
                    "started": started_ts, "participants": people,
                    "speaking": transcript[-1][1] if transcript else "",
                    "join": data.get("meeting_url") or row.get("constructed_meeting_url") or "",
                    "recording": "on" if data.get("recording_enabled", True) else "off",
                    "transcript": "live" if str(row.get("status") or "") in ("active", "live") else str(row.get("status") or "")}
        # the room and its workspace — by TITLE
        room = None
        rights = _sit.private_rights()
        names: dict = {}
        if session_store is not None:
            try:
                from control_plane import people_directory as _pd  # noqa: PLC0415
                for pp in session_store.participants(chat):
                    if pp.get("kind") == "human":
                        n = _pd.name_of(wsr.root, pp["principal"], lambda w: list(mindex.list(w) or []) if mindex else []) if wsr else ""
                        names[pp["principal"]] = n or str(pp["principal"])
            except Exception:  # noqa: BLE001
                pass
        if room_p is not None:
            title = room_p.workspace
            try:
                reg = d.get("workspace_registry")
                rec = reg.by_slug(room_p.workspace) if reg is not None else None
                title = (rec or {}).get("name") or title
            except Exception:  # noqa: BLE001
                pass
            readme = ""
            try:
                from workspaces.shared import workspace_paths as _wp  # noqa: PLC0415
                readme = _wp.read_text_inside(Path(wsr.root) / room_p.workspace, "README.md") or ""
            except Exception:  # noqa: BLE001
                readme = ""
            open_items, routine_rows = [], []
            try:
                from control_plane import routine_flows as _rf  # noqa: PLC0415
                if _rf.configured():
                    from control_plane import attention as _att  # noqa: PLC0415
                    rows = []
                    for f in _rf.FlowsClient().routines(subject):
                        if f.get("status") == "retired" or not f.get("name"):
                            continue
                        card = _rf.card_from_flow(subject, f, state={})
                        thread = card.get("thread")
                        tgt = (sess.target(subject, thread) or "").strip() if (thread and sess is not None) else ""
                        rows.append({"id": f["name"], "name": f["name"], "last_run": f.get("last_run") or None,
                                     "status": "active" if f.get("status") == "active" else "paused",
                                     "where": _att.home_slug(tgt, subject),
                                     "next": card.get("schedule") or card.get("cron") or ""})
                    open_items, routine_rows = _sit.workspace_status(rows, room_p.workspace)
            except Exception:  # noqa: BLE001 — flows did not answer: no status, nothing guessed
                open_items, routine_rows = [], []
            room = {"workspace": title, "members": list(names.values()), "readme": readme,
                    "open_items": open_items, "routines": routine_rows}
            rights = _sit.room_rights(title)
        messages = []
        if session_store is not None:
            try:
                messages = [(n["created"], "Vexa" if n["author"] == "vexa" else names.get(n["author"], "Someone"), n["text"])
                            for n in session_store.notes(chat, limit=10)]
            except Exception:  # noqa: BLE001
                messages = []
        vexa = []
        if session_store is not None:
            try:
                from control_plane.routers.chat_read import _turns_of  # noqa: PLC0415
                rec = session_store.get(chat)
                files = (session_store.load_bundle(rec["id"]).get("files") or {}) if rec else {}
                vexa = [(0.0, t["text"]) for t in _turns_of(files) if t.get("role") == "agent"][-3:]
            except Exception:  # noqa: BLE001
                vexa = []
        note = None
        if page is not None and wsr is not None:
            try:
                from shared import meeting_doc as _md  # noqa: PLC0415
                place = meeting_listen.locate(wsr.root, subject, row) if isinstance(row, dict) else None
                text = meeting_listen.page_text(place) if place else ""
                note = {k: _md.read_region(text, k) or "" for k in ("decisions", "commitments", "questions")}
            except Exception:  # noqa: BLE001
                note = None
        return _sit.compose(now=now, call=call, room=room, transcript=transcript, messages=messages,
                            vexa=vexa, note=note, rights=rights)

    def _shared_plan(subject: str, chat: str):
        """The leak rule for a turn in a chat other people read (ADR-0048 decision 4), planned the
        way a person's turn is (`session_turn.plan_turn`, addressed): ``None`` when the store is off."""
        if session_store is None or wsr is None:
            return None
        from control_plane import session_turn, system_mounts, workspace_membership  # noqa: PLC0415
        from control_plane.workspace_attach import active_workspaces, shared_active_mounts  # noqa: PLC0415

        def _is_member(ws: str, who: str) -> bool:
            try:
                return workspace_membership.is_member(wsr.root, ws, who) is not None
            except Exception:  # noqa: BLE001
                return False
        try:
            mounts = (active_workspaces(wsr.root, subject)
                      + shared_active_mounts(wsr.root, subject, mindex.list(subject) if mindex else []))
            candidates = [m.slug for m in mounts] + [system_mounts.GLOBAL_SLUG, "_system", str(subject)]
        except Exception:  # noqa: BLE001
            candidates = [system_mounts.GLOBAL_SLUG]
        return session_turn.plan_turn(
            session_store, subject=subject, session=chat, text="", candidates=candidates,
            is_member=_is_member, global_root=_global_root() if callable(_global_root) else None,
            names={c: c for c in candidates}, addressed=True)

    def _signal_turn(request: Request, event: dict, invocation: dict):
        """Run a ``signal`` turn in the chat ``target.chat`` names: on that chat's ``-signal`` unit,
        with the chat's conversation, queued behind a running turn of the same chat, its tools
        writing only the meeting's page (`signal_scope`)."""
        from control_plane import meeting_listen, session_sync  # noqa: PLC0415
        from control_plane.routers.chats import queue_behind  # noqa: PLC0415

        subject = str(invocation["identity"]["subject"])
        data = event.get("data") or {}
        row = str(data.get("meeting_row") or "")
        pol = _signal_policy(subject)
        if pol["transcript_signals"] != "on":
            raise _refuse(request, 409, "transcript_signals_off",
                          "transcript signals are off for this company (_global/POLICIES.md "
                          "transcript_signals); nothing is woken")
        room_p = None
        mrow = None
        if event["name"] == "room.signal":
            # A ROOM'S SENSOR WOKE IT (rooms, 2026-10-15): the room itself is the chat; the turn may
            # answer in the room and propose, and writes no page (no grant is recorded).
            from control_plane import rooms as _rooms  # noqa: PLC0415
            if not _rooms.enabled(_global_root, subject):
                raise _refuse(request, 409, "rooms_off", "room mode is switched off (feature rooms)")
            room_id = str(event["target"]["chat"])
            rrow = session_store.get(room_id) if session_store is not None else None
            if rrow is None or not session_store.can_read(room_id, subject):
                raise _refuse(request, 404, "room_not_found",
                              "the event's subject is not in this room; nothing is woken")
            st = _rooms.settings(session_store, room_id)
            if not st["room"]:
                raise _refuse(request, 409, "not_a_room", "a private chat is not woken by a room's sensor")
            if not st.get("agent_on", True):
                # "VEXA IN THIS ROOM" IS OFF (S4, deny test 25): no signal wakes it, addressed or not
                raise _refuse(request, 409, "room_vexa_off",
                              "the people in this room switched Vexa off; nothing is woken")
            if st["proactivity"] == "off" and str(data.get("reason") or "") != "addressed":
                # OFF MEANS ADDRESSED ONLY: being spoken to still wakes the agent
                raise _refuse(request, 409, "room_proactivity_off",
                              "the people in this room switched Vexa's proactivity off; only being "
                              "addressed wakes it")
            from control_plane import room_principal as _rpm  # noqa: PLC0415
            room_p = (_rpm.resolve(session_store, wsr.root, room_id, speaker=subject,
                                   call_lookup=_call_row(subject)) if wsr is not None else None)
            if room_p is None:
                raise _refuse(request, 409, "room_has_no_workspace",
                              "a room's sensor wakes its workspace's agent; this room has none")
            if not _rooms.enabled(_global_root, room_p.owner):
                raise _refuse(request, 403, "rooms_off_for_owner",
                              "rooms is not enabled for this workspace owner's account")
            wake_key, page = f"room:{room_id}", None
        else:
            mrow = _meeting_owner_lookup(subject, row) if _meeting_owner_lookup is not None else None
            if not isinstance(mrow, dict):
                raise _refuse(request, 404, "meeting_not_found",
                              "the event's subject cannot read this meeting; nothing is woken")
            place = meeting_listen.locate(wsr.root, subject, mrow) if wsr is not None else None
            if place is None and wsr is not None:
                # A ROOM'S CALL KEEPS ITS NOTE IN THE ROOM'S WORKSPACE (2026-10-16), where the room
                # agent's signal turn can write it — never the person's home.
                place = _room_note(subject, str(event["target"]["chat"]), mrow, row)
            if place is None and wsr is not None:
                # THE MEETING'S NOTE IS ITS OWN ARTEFACT (app.dev 2026-10-16): a call woke its turn
                # and the Notes panel stayed empty — the meeting had no page to keep. Mint it (the
                # idempotent act the bot-send performs) so the turn has its meeting's note to write.
                try:
                    from control_plane import meeting_mint as _mint  # noqa: PLC0415
                    _mint.mint(wsr.root, subject, mrow, record=_meeting_note_recorder)
                except Exception as exc:  # noqa: BLE001 — no page: the turn still answers
                    import logging as _logging  # noqa: PLC0415
                    _logging.getLogger("agent_api.ingress").warning(
                        "meeting %s: note not minted for a signal (%s)", row, type(exc).__name__)
                place = meeting_listen.locate(wsr.root, subject, mrow)
            wake_key = row
            # No page found yet still names the meeting: its brief (`meeting_listen`) stays reachable,
            # and no page write is granted (`signal_scope.allowed_verbs`).
            page = ({"meeting": row, "path": place["path"], "workspace": place["workspace"]}
                    if place is not None else {"meeting": row, "path": "", "workspace": ""})
        _cap = int(pol["signals_wakes_per_meeting"])
        _n = _wake_count(subject, wake_key)
        if _n > _cap:
            if _n == _cap + 1:
                # SAID, NOT SILENT: the room reads that Vexa paused, once per hour of meeting
                _target = str(event["target"]["chat"])
                try:
                    _target = sess.chat_for(subject, _target) if sess is not None else _target
                except Exception:  # noqa: BLE001
                    pass
                from control_plane import rooms as _rooms_p  # noqa: PLC0415
                say_paused(session_store, _rooms_p.redis_for(redis_url), subject, _target)
            raise _refuse(request, 429, "signals_wakes_per_meeting",
                          f"this has woken its agent {_cap} times this hour of the meeting "
                          "(_global/POLICIES.md signals_wakes_per_meeting, per hour)")
        chat = str(event["target"]["chat"])
        try:
            chat = sess.chat_for(subject, chat) if sess is not None else chat
        except Exception:  # noqa: BLE001 — the name as given
            pass
        if room_p is None and session_store is not None and wsr is not None:
            # A CALL'S WAKE INTO A ROOM IS THE ROOM AGENT'S (founder ruling 2026-10-16): the call was
            # held in a room, so the turn runs as that room's workspace agent, with the room's scope.
            try:
                from control_plane import room_principal as _rpm2, rooms as _rooms2  # noqa: PLC0415
                if _rooms2.is_room(session_store, chat) and _rooms2.enabled(_global_root, subject):
                    # WHO IS ON THE CALL COUNTS IN THE AUDIENCE (D27): the woken meeting's own
                    # participants, read from its row this turn
                    room_p = _rpm2.resolve(session_store, wsr.root, chat, speaker=subject,
                                           call_lookup=_call_row(subject),
                                           participants=_rpm2.call_participants_of(mrow)
                                           if isinstance(mrow, dict) else None)
            except Exception:  # noqa: BLE001 — not resolvable: the person's own signal turn
                room_p = None
        try:
            plan = _shared_plan(subject, chat)
        except session_sync.SessionDenied as exc:
            raise _refuse(request, 403, "session_denied", f"the chat refused this turn ({exc.reason})")
        except session_sync.SessionUnavailable as exc:
            return JSONResponse(status_code=503, content=session_sync.answer(exc.kind, chat))
        try:
            gen = sess.mount_gen(subject, chat) if sess is not None else 0
        except Exception:  # noqa: BLE001 — an unreadable generation is the id it always had
            gen = 0
        ctx = {**invocation["context"], "session": chat}
        if gen:
            ctx["mount_gen"] = gen
        if plan is not None and plan.gen:
            ctx["mount_gen"] = f"{gen or 0}{plan.gen}"
        start = invocation["start"]
        if plan is not None and plan.preamble:
            start = units.entrypoint(inline=plan.preamble + start["entrypoint"]["inline"])
        try:
            _sit = _situation(subject, chat, mrow if event["name"] != "room.signal" else None, room_p, page)
        except Exception:  # noqa: BLE001 — the turn still runs on its plan
            _sit = ""
        if _sit:
            start = units.entrypoint(inline=start["entrypoint"]["inline"] + "\n\n" + _sit)
        if page is not None:
            start = units.entrypoint(inline=start["entrypoint"]["inline"] + note_instruction(page))
        if room_p is not None:
            # THE ROOM'S OWN AGENT WAKES (C1): the workspace's agent, on the room unit's `-signal`
            # sibling — read-only, proposals only.
            ctx["room_unit"] = room_p.unit + units.SIGNAL_UNIT_SUFFIX
        inv = {**invocation, "context": ctx, "start": start}
        unit_id = units.dispatch_id(inv)
        kwargs = dict(signal_page=page, shared_scope=(plan.scope if plan is not None else None))
        if room_p is not None:
            kwargs.update(room_principal=room_p, shared_scope=None)
        try:
            workload_id = dispatcher.dispatch(inv, **kwargs)
            _heard(chat, data)
            if room_p is not None:
                from control_plane import rooms as _rooms_sp  # noqa: PLC0415
                _rooms_sp.note_speaker(_rooms_sp.redis_for(redis_url), room_p.session,
                                       room_p.on_behalf_of or str(subject))
        except llm_budget_mod.BudgetExhausted as refusal:
            return JSONResponse(status_code=refusal.http_status, content=refusal.as_dict())
        except session_sync.SessionBusy as busy:
            if not busy.queueable_for(unit_id):
                return JSONResponse(status_code=409, content=session_sync.answer(busy.kind, chat))
            # QUEUED BEHIND THE CHAT'S OWN TURN (session-busy-auto): it runs the moment the head is
            # free, on a thread of its own, so the flow's request is answered now.
            queue_behind(dispatcher._redis() if hasattr(dispatcher, "_redis") else None, busy,
                         unit_id, lambda: dispatcher.dispatch(inv, **kwargs), lambda: None,
                         session=chat)
            _heard(chat, data)
            return {"workload_id": unit_id, "trigger": "signal", "session": chat, "queued": True,
                    "behind": busy.holder_kind or "reply"}
        return {"workload_id": workload_id, "trigger": "signal", "session": chat, "queued": False}

    return router
