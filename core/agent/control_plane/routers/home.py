"""routers/home.py — the new-chat home over HTTP (#1783).

    GET  /api/home                   the latest composition's view — a store read, NO model call
    POST /api/home/feedback          one outcome on one card: click, dismiss (+reason), snooze
    POST /api/home/compose           the background flow's door (internal tier only): compose now
    GET  /api/home/history           what was suggested and what came of it (tool ``home_history``)
    GET  /api/home/customer          what Vexa knows about this person (tool ``customer_profile``)
    POST /api/home/customer/correct  the person corrects one field; a correction wins

THE PAGE NEVER COMPOSES WITH A MODEL. A read that finds the composition stale (older than two hours,
a meeting within the hour that it predates, a routine that ran or a notification that arrived since)
publishes ``home.stale`` for the flows engine and answers with what is stored; the flow calls
``/api/home/compose`` in the background. The very first read, with nothing stored, composes once
without a model — onboarding cards and whatever is already there — so a new person never meets a
blank page. The subject is the caller's, always (P20).
"""
from __future__ import annotations

from workspaces.shared import workspace_paths as wpaths_mod

import json
import logging
import os
import time
import urllib.request
from pathlib import Path
from typing import Optional

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from fastapi.responses import Response

from control_plane import budgets, front_page as front_page_mod, home, home_media, identity_presence, home_model, home_playbook, home_sources, meeting_note, people_directory, publish, session_sync
from control_plane.ceiling import refuse_delegated
from shared import usage as usage_mod
from control_plane import llm_budget

logger = logging.getLogger("agent_api.home")

_SIGNAL = {"click": "clicked", "dismiss": "dismissed", "snooze": "snoozed", "sent": "sent"}
CUSTOMER_FIELDS = ("stage", "capabilities", "jobs", "patterns", "preferences", "friction", "open_loops",
                   "health", "next_best_actions", "goals")


def _queue_waiting(owner: str) -> Optional[list]:
    base = (os.environ.get("VEXA_FLOWS_API_URL") or "").rstrip("/")
    key = (os.environ.get("VEXA_FLOWS_API_KEY") or "").strip()
    if not base or not key:
        return None
    req = urllib.request.Request(f"{base}/queue/waiting?subject={urllib.request.quote(owner)}&limit=20",
                                 headers={"X-Flows-Operator-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=4) as r:  # noqa: S310 — the internal flows door
            body = json.loads(r.read().decode())
        return [i for i in body.get("items") or [] if isinstance(i, dict)]
    except Exception:  # noqa: BLE001 — a source that does not answer is named, not fatal
        return None


class FeedbackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card: str = Field(pattern=r"^[A-Za-z0-9._:@+-]{1,120}$")
    signal: Literal["click", "dismiss", "snooze", "sent"]
    kind: Optional[str] = Field(default=None, max_length=20)
    reason: Optional[Literal["wrong", "done", "not_now"]] = None
    people: Optional[list[str]] = Field(default=None, max_length=6)
    chat: Optional[str] = Field(default=None, max_length=128)
    title: Optional[str] = Field(default=None, max_length=300)


class ComposeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trigger: Optional[str] = Field(default=None, max_length=60)


class PresenceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visible: bool


class ItemBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item: str = Field(pattern=r"^[A-Za-z0-9._:@+-]{1,120}$",
                      description="the home item id the prompt named (\"This came from your home, item …\")")
    state: Literal["acted", "in_progress", "prepared", "live", "ended", "follow_up", "replied", "done"] = Field(
        description="where the work got to: prepared (a prep exists), follow_up (a recap or proposals are drafted), "
                    "replied (the reply is sent), done (nothing left); in_progress while still under way")
    chat: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
                                description="the chat the work lives in (this chat's thread id), so the card can continue it")
    page: Optional[str] = Field(default=None, max_length=200, description="the page the work produced, `<slug>/<path>`")


class ExpandBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item: str = Field(pattern=r"^[A-Za-z0-9._:@+-]{1,120}$", description="the card or section being elaborated")
    card: dict = Field(description="the card as blocks.v1 (what the person sees)")
    ask: Optional[str] = Field(default=None, max_length=400, description="what the person wants to know about it")


class CorrectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: Literal["stage", "capabilities", "jobs", "patterns", "preferences", "friction", "open_loops",
                   "health", "next_best_actions", "goals"] = Field(description="the profile field the person corrects")
    value: Any = Field(default=None, description="the person's own words or value for it (at most 2000 characters "
                                                 "as JSON); null withdraws an earlier correction")


#: Presence is coarse: "now" is activity in the last ten minutes, "today" in the last day.
PRESENCE_NOW_S, PRESENCE_TODAY_S = 600, 86400


def presence_of(age: float) -> Optional[str]:
    """``now`` · ``today`` · None for activity ``age`` seconds ago — no times, ever."""
    return "now" if age < PRESENCE_NOW_S else "today" if age < PRESENCE_TODAY_S else None


def shared_chat_presence(store, owner: str, sessions: Optional[list], now: float) -> dict:
    """PRESENCE FROM THE CHATS ``owner`` SHARES — ``{principal: "now"|"today"}`` for each other
    person in a shared chat (or a ``dm-`` one) among ``owner``'s 30 most recent, from that chat's
    last activity. Never from anything the colleague did elsewhere. The home's people strip and the
    desktop's ``shared_chats`` presence both read it. ``sessions`` rows carry ``shared`` (another
    person owns it); a ``dm-`` row gains ``other``, the person on the far side."""
    seen: dict = {}
    for srow in (sessions or [])[:30]:
        if not srow.get("shared") and not str(srow.get("id", "")).startswith("dm-"):
            continue
        try:
            parts = [p["principal"] for p in store.participants(srow["id"]) if p.get("principal") != owner]
        except Exception:  # noqa: BLE001
            continue
        if str(srow.get("id", "")).startswith("dm-") and parts:
            srow["other"] = parts[0]
        pres = presence_of(now - float(srow.get("last_active") or 0))
        for who in parts:
            if pres and seen.get(who) != "now":
                seen[who] = pres
    return seen


#: The home events presence USED to be recorded as — read only to carry an old opt-out over.
LEGACY_PRESENCE_CARDS = ("presence:on", "presence:off")


def presence_switched_off(hs: "home.HomeStore", who: str) -> bool:
    """Is ``who``'s presence hidden from colleagues? Their own choice, which identity keeps
    (`control_plane.identity_presence`) and which wins over every policy. FAILS CLOSED: identity
    unreachable or not knowing the person hides the dot. A person who never chose in identity and
    whose latest home event (the store before identity owned this) says off is carried over there
    once, so nobody who opted out reappears."""
    return identity_presence.switched_off(
        who, lambda: hs.last_of(who, LEGACY_PRESENCE_CARDS) == "presence:off")


def build(**d) -> APIRouter:
    router = APIRouter()
    subject_of = d["subject_of"]
    store = d.get("session_store")
    notifier = d.get("notifier")
    schedule_source = d.get("_schedule_source")
    global_root = d.get("_global_root")
    internal_caller = d.get("_internal_caller")
    dispatcher = d.get("dispatcher")
    clock = d.get("home_clock") or time.time
    rank_override = d.get("home_ranker")      # tests: a fake model
    queue_source = d.get("home_queue_source") or _queue_waiting
    publisher = d.get("home_publish") or publish.publish
    expander = d.get("home_expander")         # tests: a fake model
    media_fetch = d.get("home_media_fetch")   # tests: a fake network
    colleagues_of = d.get("home_colleagues")  # tests: a fake directory
    wsr, mindex = d.get("wsr"), d.get("mindex")
    if d.get("settings") is not None:
        identity_presence.configure(d["settings"])   # the presence choice is identity's (desktop reads it too)

    def _hs() -> home.HomeStore:
        if store is None:
            raise HTTPException(status_code=503, detail="The home is not configured on this deployment")
        return home.HomeStore(store, clock=clock)

    def _owner(request: Request) -> str:
        try:
            return str(subject_of(request))
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject") from None

    def _gdir() -> Optional[Path]:
        try:
            return global_root() if global_root else None
        except Exception:  # noqa: BLE001
            return None

    def _curriculum() -> list:
        return home.parse_curriculum(home.read_global(_gdir(), home.CURRICULUM_FILE))

    def _notes(owner: str) -> Optional[list]:
        if notifier is None:
            return None
        try:
            rows = notifier.listing(owner, unread_only=True, limit=20, before=None).get("notifications") or []
        except Exception:  # noqa: BLE001
            return None
        return [{"id": n["id"], "kind": n["kind"], "actor": (n.get("actor") or {}).get("id"),
                 "actor_name": (n.get("actor") or {}).get("name"), "object_kind": (n.get("object") or {}).get("kind"),
                 "object_id": (n.get("object") or {}).get("id"), "object_name": (n.get("object") or {}).get("name"),
                 "text": n.get("text"), "created": n.get("created"),
                 "read_at": n.get("created") if n.get("read") else None} for n in rows]

    def _sessions(owner: str) -> Optional[list]:
        try:
            rows = store.list_for(owner) if store is not None else None
        except Exception:  # noqa: BLE001
            return None
        return [{**r, "shared": r.get("owner") != owner} for r in rows or []][:200] if rows is not None else None

    def _meetings(owner: str) -> Optional[list]:
        if schedule_source is None:
            return None
        try:
            return list(schedule_source(owner) or [])
        except Exception:  # noqa: BLE001
            return None

    def _colleagues(owner: str, sessions: Optional[list], now: float) -> dict:
        """The people this person shares a workspace with (the @-mention directory), with presence
        from the chats they share — never from anything the colleague did elsewhere — unless the
        colleague turned presence off."""
        try:
            if colleagues_of is not None:
                rows = list(colleagues_of(owner))
            elif wsr is not None:
                rows = people_directory.mentionable(wsr.root, owner, lambda w: list(mindex.list(w) or []) if mindex else [])
            else:
                rows = []
        except Exception:  # noqa: BLE001
            rows = []
        out = {str(r.get("subject")): {"name": r.get("name"), "email": r.get("email")} for r in rows if r.get("subject")}
        if store is not None:
            for who, pres in shared_chat_presence(store, owner, sessions, now).items():
                if who in out:
                    out[who]["presence"] = pres
        hs = _hs()
        shown = [who for who in out if out[who].get("presence")]
        try:
            identity_presence.prefetch(shown)      # one read of identity for the whole strip
        except identity_presence.Unavailable:
            pass                                   # each lookup below then hides (fails closed)
        for who in shown:
            if presence_switched_off(hs, who):
                out[who].pop("presence", None)
        return out

    def _line_of(owner: str, sessions: Optional[list]):
        """A chat's last answer, read from its stored conversation (a preview, a routine's outcome)."""
        rows = {str(r.get("id")): r for r in sessions or []}

        def line(sid: str) -> str:
            row = rows.get(sid)
            if row is None:
                return ""
            turns = None
            try:
                turns = session_sync.history(store, row) if store is not None else None
                if turns is None and wsr is not None:
                    turns = wsr.history(owner, str(row.get("name") or sid))
            except Exception:  # noqa: BLE001 — a preview is extra
                return ""
            for t in reversed(turns or []):
                if t.get("role") != "user" and str(t.get("text") or "").strip():
                    return str(t["text"])
            return ""
        return line

    def _note_of(owner: str):
        """A finished meeting's note on this person's own desk, when one is written."""
        def note(m: dict) -> str:
            if wsr is None:
                return ""
            try:
                found = meeting_note.find(wsr.root, owner, m)      # wherever it lives (#1783)
                if not found:
                    return ""
                return wpaths_mod.read_text_inside(found[1], found[0]) or ""
            except Exception:  # noqa: BLE001
                return ""
        return note

    def _evidence(events: list, prefix: str) -> set:
        return {e["card"][len(prefix):] for e in events if e["event"] == "evidence" and e["card"].startswith(prefix)}

    def _who_when(owner: str, events: list, now: float, *, email: str = "", tz_hint: str = "") -> tuple:
        """(the person's name, their local hour or None, their local day) — the greeting's facts
        (founder: "Hello, there" makes no sense). The name is the one `/api/people/me` gives — their
        own person page, the directory, then their verified address read as a name — so every
        greeting path names them the same way. The zone is the browser's own (`tz_hint`, sent on
        each read) before any recorded one, so the first visit already says morning or evening."""
        tz = [tz_hint] if tz_hint else sorted(_evidence(events, "tz:"))
        local = time.strftime("%a %d %b", time.gmtime(now))
        hour = None
        try:
            from zoneinfo import ZoneInfo  # noqa: PLC0415
            from datetime import datetime as _dt  # noqa: PLC0415
            if tz:
                at = _dt.fromtimestamp(now, ZoneInfo(tz[-1]))
                hour, local = at.hour, at.strftime("%a %d %b")
        except Exception:  # noqa: BLE001 — an unknown zone keeps UTC words and no hour
            hour = None
        me = ""
        if wsr is not None:
            try:
                me = front_page_mod.display_name(wsr.root, owner, email=email or None, global_dir=_gdir()) or ""
            except Exception:  # noqa: BLE001
                me = ""
            if not me:
                try:
                    me = people_directory.name_of(wsr.root, owner, lambda s: list(mindex.list(s) or []) if mindex else [], email=email)
                except Exception:  # noqa: BLE001
                    me = ""
        if not me and email:
            me = front_page_mod.name_from_address(email) or ""   # the floor: their address, read as a name
        return me, hour, local

    def _switches() -> tuple:
        """(home cards on, people strip on) — `home_cards` (default OFF: founder, "for now let's just
        disable these cards, let's clean up to release") and `home_people` (default on), from
        `_global/POLICIES.md`, else `VEXA_HOME_CARDS` / `VEXA_HOME_PEOPLE`."""
        pol = budgets.read(_gdir())
        return budgets.on(pol, "home_cards"), budgets.on(pol, "home_people")

    def compose(owner: str, trigger: str, *, allow_model: bool = True) -> dict:
        hs, now = _hs(), clock()
        events = hs.events(owner, since=now - 60 * 86400)
        meetings, sessions, notes = _meetings(owner), _sessions(owner), _notes(owner)
        waiting = queue_source(owner)
        connected = _evidence(events, "connection:")
        facts = home_sources.facts_from(meetings=meetings, sessions=sessions, notes=notes, events=events,
                                        first_seen=hs.first_seen(owner), connected=connected, now=now)
        playbook = home_playbook.read(_gdir())
        stage_def, why = home_playbook.stage_of(playbook, facts, now)
        stage = (stage_def["id"], why) if stage_def else home.lifecycle(facts, now)
        curriculum = _curriculum()
        scope = (stage_def or {}).get("curriculum")
        if isinstance(scope, list):
            curriculum = [c for c in curriculum if c["id"] in scope]
        held = home.suppressed(events, now)
        refused = {e["card"][2:] for e in events if e["event"] == "dismissed" and e.get("reason") in ("wrong", "done")
                   and e["card"].startswith("o-")}
        snoozed = {cid[2:] for cid, why in held.items() if cid.startswith("o-") and why == "snoozed"}
        cands, people = home_sources.candidates(now=now, meetings=meetings, waiting=waiting, notes=notes,
                                                sessions=sessions, curriculum=curriculum, facts=facts,
                                                refused=refused, snoozed=snoozed, states=home.item_states(events),
                                                colleagues=_colleagues(owner, sessions, now), owner=owner,
                                                line_of=_line_of(owner, sessions), note_of=_note_of(owner))
        cands += home_playbook.discover(now=now, sessions=sessions or [], meetings=meetings or [], connected=connected)
        ranker, model_id = None, ""
        pol = budgets.read(_gdir())
        ucli = budgets.usage_client(d.get("redis_url"))
        over = budgets.verdict(pol, usage_mod.rolling(ucli, owner), background=True).get("stop")
        if allow_model and not over and hs.count_since(owner, now - 86400, with_model=True) < pol["home_compose_per_day"]:
            if rank_override is not None:
                ranker, model_id = rank_override, "test"
            else:
                ranker, model_id = home_model.ranker(getattr(dispatcher, "catalog", None), named=pol["model_home"],
                                                     allowed=budgets.allowed_list(pol))
        jobs: list = []

        def _rank(c, s):
            out = ranker(c, s)
            if isinstance(out, dict) and isinstance(out.get("jobs"), list):
                jobs.extend(out["jobs"])
            if isinstance(out, dict) and out.get("_tokens"):
                llm_budget.record_local(owner, "home", out.get("_usage") or {"prompt_tokens": int(out["_tokens"])},
                                        fallback_client=ucli)
            return out

        tz = sorted(_evidence(events, "tz:"))
        me, hour, local = _who_when(owner, events, now)
        view, facts_out = home.arrange(cands, events, stage[0], now, ranker=_rank if ranker else None,
                                       now_local=local, people=people, name=me, hour=hour,
                                       mixer=lambda pool: home_playbook.mix(pool, stage_def))
        latest = hs.latest(owner)
        customer = home.update_customer(latest["customer"] if latest else None, facts=facts, stage=stage,
                                        progress=home.curriculum_progress(curriculum, facts), events=events,
                                        w=facts_out["weights"], candidates=cands, view=view, now=now,
                                        jobs=jobs if ranker else None, mix=facts_out.get("mix"))
        upcoming = [home_sources._ts(m.get("scheduled_at")) for m in meetings or []
                    if str(m.get("status")) in ("scheduled", "idle")]
        next_due = now + home.EVERY_S
        for s in upcoming:
            if s and s - home.MEETING_LEAD_S > now:
                next_due = min(next_due, s - home.MEETING_LEAD_S)
        inputs = {"sources": {"meetings": meetings is not None, "queue": waiting is not None,
                              "notifications": notes is not None, "chats": sessions is not None,
                              "curriculum": bool(curriculum),
                              "gmail": "via whats_waiting (background reads follow connection_actions_unattended)",
                              "calendar": "meeting-api scheduled rows"},
                  "candidates": [{"id": c["card"]["id"], "kind": c["card"]["kind"], "source": c["source"],
                                  "title": home.clip(c.get("title"), 80)} for c in cands][:40],
                  "considered": facts_out["considered"], "held": facts_out["held"], "tz": tz[:1],
                  "mix": facts_out.get("mix", {}), "stage_why": stage[1]}
        rec = hs.append(owner, trigger=trigger, model=facts_out["model"],
                        stage=stage[0], inputs=inputs, view=view, customer=customer, next_due=next_due)
        _frame(owner, {"type": "home.composed", "id": rec["id"]})
        try:
            hs.prune(owner, home.retention_days(_gdir()))
        except Exception:  # noqa: BLE001 — retention is best-effort per compose
            pass
        return {"id": rec["id"], "composed_at": rec["composed_at"], "stage": stage[0], "cards": len(view["cards"]),
                "model": facts_out["model"]}

    def _frame(owner: str, frame: dict) -> None:
        """A live patch to every tab this person has open (the /ws socket's own channel)."""
        try:
            if notifier is not None:
                notifier.send_frame(owner, frame)
        except Exception:  # noqa: BLE001 — the next read shows it anyway
            pass

    def _stale(owner: str, latest: dict, now: float) -> bool:
        if now >= latest["next_due"]:
            return True
        try:
            if notifier is not None:
                n = notifier.listing(owner, unread_only=True, limit=1, before=None).get("notifications") or []
                if n and float(n[0].get("created") or 0) > latest["composed_at"]:
                    return True
            # a meeting that started or ended since the composition (meeting-api's own timestamps)
            for m in _meetings(owner) or []:
                for k in ("start_time", "end_time"):
                    t = home_sources._ts(m.get(k))
                    if t and latest["composed_at"] < t <= now:
                        return True
            if store is not None:
                for s in store.list_for(owner)[:30]:
                    if s.get("flow") and float(s.get("last_active") or 0) > latest["composed_at"]:
                        return True
        except Exception:  # noqa: BLE001
            return False
        return False

    def _claims_live_wrongly(view: Optional[dict], meetings: Optional[list]) -> bool:
        """A stored card says a meeting is live that meeting-api says is not (founder, live app.dev:
        "it's lying, there is no live meeting"). The composition is a snapshot; the meeting's status
        is the truth, so a card that contradicts it is never served."""
        if not view or meetings is None:
            return False
        status = {str(m.get("id")): str(m.get("status") or "") for m in meetings}
        for c in view.get("cards") or []:
            mid = str(((c.get("refs") or {}).get("meeting")) or (c["id"][2:] if str(c.get("id", "")).startswith("m-") else ""))
            if not mid or mid not in status or status[mid] in home_sources._LIVE:
                continue
            words = json.dumps(c).lower()
            if c.get("state") == "live" or "live now" in words or "follow live" in words:
                return True
        return False

    @router.get("/api/home")
    def read_home(request: Request,
                  connected: str = Query(default="", max_length=200, description="the caller's ready connections, comma-separated providers"),
                  tz: str = Query(default="", max_length=64)):
        """The home for a new chat: the latest composition's view, as stored. No model call here."""
        owner, hs, now = _owner(request), _hs(), clock()
        events = hs.events(owner, since=now - 60 * 86400)
        have = _evidence(events, "connection:")
        for c in {x.strip() for x in connected.split(",") if x.strip()}:
            if home._key(c) and c not in have:
                hs.record(owner, card=f"connection:{c}", event="evidence")
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        if day not in _evidence(events, "visit:"):
            hs.record(owner, card=f"visit:{day}", event="evidence")   # the stage reads visits, not views
        tz_ok = tz if tz and home._key(tz.replace("/", ".")) else ""
        if tz_ok and tz_ok not in _evidence(events, "tz:"):
            hs.record(owner, card=f"tz:{tz_ok}", event="evidence")
        # the gateway's verified address (it strips any client-sent one): the name's floor
        email = (request.headers.get("x-user-email") or "").strip()
        cards_on, people_on = _switches()
        if not cards_on:
            # HOME CARDS OFF: the greeting with the person's name, and nothing composed — no
            # `home.stale`, no composition, no model call; the composer, the starters and the People
            # strip are the terminal's
            me, hour, local = _who_when(owner, events, now, email=email, tz_hint=tz_ok)
            g = home.greeting("active", [], me, local, hour)
            view = {"spec": home.VIEW_SPEC, "composed_at": "", "greeting": {"title": g["title"], "text": local},
                    "cards": [], "people": {}}
            return {"view": view, "id": None, "stage": "", "stale": False, "you": owner, "cards": False, "people": people_on}
        latest = hs.latest(owner)
        stale = False
        if latest is None:
            compose(owner, "first-visit", allow_model=False)
            latest = hs.latest(owner)
            stale = True
        elif _stale(owner, latest, now):
            stale = True
        if latest is not None and _claims_live_wrongly(latest.get("view"), _meetings(owner)):
            # recomposed here and now, without the model: a meeting that ended reads as ended at once
            compose(owner, "meeting-ended", allow_model=False)
            latest = hs.latest(owner)
            stale = False
        if stale:
            publisher("home.stale", f"home:{owner}:{int(now // home.DEBOUNCE_S)}", {"uid": owner})
        view = home.sanitize_view(latest["view"] if latest else None)
        if view:
            # EVERY GREETING NAMES THE PERSON, at their hour NOW (founder, live: "Hello, there"): the
            # stored composition's title was worded at compose time — by the model, or without the
            # person's name or zone — so the read re-says it; the composed day line stays
            me, hour, local = _who_when(owner, events, now, email=email, tz_hint=tz_ok)
            g = home.greeting(latest["stage"] if latest and latest.get("stage") in ("new", "drifting", "dormant") else "active",
                              view.get("cards") or [], me, local, hour)
            view["greeting"] = {**(view.get("greeting") or {}), "title": g["title"]}
        if view and latest:
            seen = {e["card"] for e in events if e["event"] == "shown" and e["composition"] == latest["id"]}
            for c in view["cards"]:
                if c["id"] not in seen:
                    hs.record(owner, card=c["id"], event="shown", kind=c["kind"], composition=latest["id"],
                              people=home.card_people(c), title=home._title(c))
        return {"view": view, "id": latest["id"] if latest else None, "stage": latest["stage"] if latest else "",
                "stale": stale, "you": owner, "cards": True, "people": people_on}

    @router.post("/api/home/feedback")
    def feedback(fb: FeedbackBody, request: Request):
        """One outcome on one card. ``signal``: click | dismiss (with ``reason`` wrong, done or
        not_now) | snooze (24 h) | sent (a prompt went into a chat: ``chat`` names it)."""
        owner, hs = _owner(request), _hs()
        body = fb.model_dump(exclude_none=True)
        card = body.get("card")
        ev = _SIGNAL.get(str(body.get("signal") or ""))
        if not home._key(card) or not ev:
            raise HTTPException(status_code=422, detail="name a card id and a signal: click, dismiss, snooze or sent")
        reason = str(body.get("reason") or "")
        if reason and (ev != "dismissed" or reason not in home.REASONS):
            raise HTTPException(status_code=422, detail="a reason goes with dismiss only: wrong, done or not_now")
        latest = hs.latest(owner)
        chat = body.get("chat") if home._key(body.get("chat")) else ""
        rec = hs.record(owner, card=card, event=ev, kind=str(body.get("kind") or ""), reason=reason,
                        people=[p for p in body.get("people") or [] if isinstance(p, str)][:6],
                        chat=chat, title=home.clip(body.get("title"), 120),
                        composition=latest["id"] if latest else "")
        if ev == "sent" and chat:
            _frame(owner, {"type": "home.item", "item": card, "state": "acted", "chat": chat, "at": rec["at"]})
        return rec

    @router.post("/api/home/item")
    def item_update(body: ItemBody, request: Request):
        """Record where a home item got to, when the work that came from it finishes or moves on:
        prepared, follow_up, replied, done, in_progress. Call it from the chat the work ran in, with
        that chat's id, so the card continues the work instead of starting it again. The open home
        updates at once; a terminal state (replied, done) takes the item off it."""
        owner, hs = _owner(request), _hs()
        page = body.page if body.page and home._PAGE.match(body.page) else ""
        rec = hs.record(owner, card=body.item, event="state", state=body.state, chat=body.chat or "", page=page)
        _frame(owner, {"type": "home.item", "item": body.item, "state": body.state,
                       **({"chat": body.chat} if body.chat else {}), **({"page": page} if page else {}), "at": rec["at"]})
        if _switches()[0]:
            publisher("home.stale", f"home:{owner}:{int(clock() // home.DEBOUNCE_S)}", {"uid": owner})
        return {"item": body.item, "state": body.state}

    @router.post("/api/home/compose")
    def compose_now(cb: ComposeBody, request: Request):
        """THE BACKGROUND FLOW'S DOOR — internal tier only. Composes and APPENDS, unless the last
        composition is under ten minutes old (one minute after a meeting ended)."""
        if internal_caller is None or not internal_caller(request):
            raise HTTPException(status_code=403, detail="the home is composed by its flow, not on request")
        owner, hs, now = _owner(request), _hs(), clock()
        if not _switches()[0]:
            return {"skipped": "home_cards off"}                 # no composition, no model call
        trigger = home.clip(cb.trigger or "home.stale", 40)
        latest = hs.latest(owner)
        gap = home.AFTER_MEETING_DEBOUNCE_S if trigger == "meeting.completed" else home.DEBOUNCE_S
        if latest and now - latest["composed_at"] < gap and latest["trigger"] not in ("first-visit", "correction"):
            return {"skipped": "recent", "id": latest["id"]}
        return compose(owner, trigger)

    @router.post("/api/home/expand")
    def expand(body: ExpandBody, request: Request):
        """EXPAND IN PLACE: one card or section, elaborated by the cheapest configured model from what
        the card itself says (no tools, no other reads), answered as blocks to insert under it. The
        answer is kept in the home history; a page keeps it only if the person presses Keep."""
        owner, hs = _owner(request), _hs()
        card = home.sanitize_view({"spec": home.VIEW_SPEC, "greeting": {"title": "x"}, "cards": [body.card], "people": {}})
        item = (card or {}).get("cards", [None])[0] if card else None
        if not item:
            raise HTTPException(status_code=422, detail="the card is not blocks.v1")
        today = clock() - 86400
        pol = budgets.read(_gdir())
        if sum(1 for e in hs.events(owner, since=today) if e["event"] == "expanded") >= pol["home_expand_per_day"]:
            raise HTTPException(status_code=429, detail={"refused": "home_expand_per_day",
                                                         "note": f"{pol['home_expand_per_day']} elaborations a day — your admin sets this"})
        ucli = budgets.usage_client(d.get("redis_url"))
        stop = budgets.verdict(pol, usage_mod.rolling(ucli, owner), background=False)
        if stop.get("stop"):
            raise HTTPException(status_code=429, detail={"refused": "budget_exhausted", "note": stop["why"]})
        user = json.dumps({"item": item, "ask": home.clip(body.ask, 400)}, ensure_ascii=False)
        out = expander(user) if expander else home_model.complete_json(
            getattr(dispatcher, "catalog", None), home_model.EXPAND, user, named=pol["model_home"],
            allowed=budgets.allowed_list(pol))
        if isinstance(out, dict) and out.get("_tokens"):
            llm_budget.record_local(owner, "home", out.get("_usage") or {"prompt_tokens": int(out["_tokens"])},
                                    fallback_client=ucli)
        fence = home.sanitize_fence({"v": 1, "blocks": (out or {}).get("blocks")}) if isinstance(out, dict) else None
        if not fence:
            raise HTTPException(status_code=503, detail="no model is configured to elaborate, or it had nothing to add")
        hs.record(owner, card=body.item, event="expanded", kind=item["kind"], title=home._title(item))
        return {"item": body.item, "blocks": fence["blocks"]}

    @router.get("/api/home/media/{key}")
    def media(key: str):
        """An avatar (``g:<md5 of the email>``) or a company's site icon (``d:<domain>``), fetched and
        cached server-side so the browser never asks a third party. 404 means: draw the initials."""
        if not home_media.valid_key(key) or not home_media.enabled(_gdir()):
            raise HTTPException(status_code=404, detail="no picture")
        got = home_media.get(key, fetch=media_fetch)
        if not got:
            raise HTTPException(status_code=404, detail="no picture")
        return Response(content=got[0], media_type=got[1],
                        headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff",
                                 "Content-Security-Policy": "default-src 'none'"})

    @router.post("/api/home/presence")
    def presence(body: PresenceBody, request: Request):
        """Show or hide this person's presence dot to colleagues (on by default). The person's own
        choice, kept by identity; a worker acting for them never changes it."""
        refuse_delegated(request, reason="presence_is_the_persons",
                         instruction="whether colleagues see the person is their own switch; ask them "
                                     "to change it in their settings")
        owner = _owner(request)
        if not identity_presence.set_choice(owner, body.visible):
            raise HTTPException(status_code=503, detail="Presence was not saved. Try again in a moment.")
        return {"visible": body.visible}

    @router.get("/api/home/history")
    def home_history(request: Request, days: int = Query(default=7, ge=1, le=365)):
        """What the home suggested over the last ``days`` and what came of each: clicked, sent into
        which chat, dismissed and why, snoozed, done. Ids and titles only."""
        return home.history(_hs(), _owner(request), days=days, now=clock())

    @router.get("/api/home/customer")
    def customer_profile(request: Request):
        """What Vexa knows about this person as a customer: lifecycle stage and why, capabilities
        adopted, jobs and goals, working patterns, preferences learned from outcomes, friction, open
        loops, health, next-best-actions with their hypotheses, the experiment log. Each field has
        its evidence ids, a confidence and when it changed. The person may correct any field."""
        latest = _hs().latest(_owner(request))
        return (latest or {}).get("customer") or {"spec": home.CUSTOMER_SPEC, "fields": {}, "corrections": {}, "changes": []}

    @router.post("/api/home/customer/correct")
    def correct(body: CorrectBody, request: Request):
        """The person corrects one field of their profile. The correction wins over anything inferred,
        until they correct it again; ``value: null`` withdraws it."""
        owner, hs, now = _owner(request), _hs(), clock()
        field, value = body.field, body.value
        if len(json.dumps(value)) > 2000:
            raise HTTPException(status_code=422, detail="a correction is at most 2000 characters")
        latest = hs.latest(owner)
        cust = dict((latest or {}).get("customer") or {"spec": home.CUSTOMER_SPEC, "fields": {}})
        corrections = dict(cust.get("corrections") or {})
        at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
        if value is None:
            corrections.pop(field, None)
        else:
            corrections[field] = {"value": value, "at": at}
        fields = dict(cust.get("fields") or {})
        if value is not None:
            fields[field] = {"value": value, "evidence": ["corrected by the person"], "confidence": 1.0, "updated_at": at}
        cust.update({"spec": home.CUSTOMER_SPEC, "fields": fields, "corrections": corrections, "updated_at": at,
                     "changes": [{"field": field, "why": "corrected by the person"}]})
        hs.record(owner, card=f"customer:{field}", event="corrected")
        hs.append(owner, trigger="correction", model="", stage=(latest or {}).get("stage", ""),
                  inputs={"correction": field}, view=(latest or {}).get("view") or {"spec": home.VIEW_SPEC, "cards": []},
                  customer=cust, next_due=(latest or {}).get("next_due", now))
        return {"field": field, "corrected": value is not None}

    return router
