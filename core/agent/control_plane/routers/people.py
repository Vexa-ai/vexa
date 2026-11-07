"""routers/people.py — who to follow up with and who is worth knowing (#1783).

    GET /api/people/followups   people the caller owes something, ranked (tool ``people_followups``)
    GET /api/people/discover    people worth knowing, inside what the caller may see (tool ``people_discover``)
    GET /api/people/peers       every human the caller shares a workspace with, once each (the People list)

The same items reach the new-chat home: ``build`` registers a ``people`` candidate source with the
home's composer (``home_sources.register_source``), so follow-ups and discoveries are ranked into the
one stream with everything else. There is no people screen; the composer is where a chat with a
person starts. The subject is the caller's, always (P20); nothing here reads another person's chats.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from control_plane import home, home_sources, identity_token, people, people_directory, person_resolution, room_principal
from control_plane import workspace_membership as membership_mod
from control_plane.routers.home import _queue_waiting
from workspaces.shared import workspace_paths as wpaths

logger = logging.getLogger("agent_api.people")

_FM = re.compile(r"^(email|title|name|id|role|company):\s*(.+?)\s*$", re.MULTILINE)
PAGES_DIR = Path("kg") / "entities" / "person"
#: Into the home: obligations rank as they score; discoveries a step below them.
HOME_FOLLOWUPS, HOME_DISCOVER, DISCOVER_SCALE = 5, 3, 0.6


def build(**d) -> APIRouter:
    router = APIRouter()
    subject_of = d["subject_of"]
    store = d.get("session_store")
    wsr, mindex = d.get("wsr"), d.get("mindex")
    schedule_source = d.get("_schedule_source")
    queue_source = d.get("home_queue_source") or _queue_waiting
    clock = d.get("home_clock") or time.time
    colleagues_of = d.get("people_colleagues")      # tests: a fake directory
    pages_of = d.get("people_pages")                # tests: fake entity pages
    email_of_owner = d.get("people_self_email")     # tests: the caller's own address

    def _index(w: str) -> list:
        return list(mindex.list(w) or []) if mindex is not None else []

    persons = d.get("person_resolver") or person_resolution.default()

    def _colleagues_raw(owner: str) -> list:
        """The directory rule, one row per account: who the caller shares a workspace with."""
        try:
            if colleagues_of is not None:
                return list(colleagues_of(owner))
            return people_directory.mentionable(wsr.root, owner, _index) if wsr is not None else []
        except Exception:  # noqa: BLE001
            return []

    def _colleagues(owner: str) -> list:
        try:
            if colleagues_of is not None:
                rows = list(colleagues_of(owner))
            else:
                rows = people_directory.mentionable(wsr.root, owner, _index) if wsr is not None else []
        except Exception:  # noqa: BLE001 — an unreadable directory names nobody
            return []
        # D35: one row per Person (the picker and the People strip), "2 accounts" when several logins;
        # V4: each named by the Person's own name, never by a raw handle
        return people_directory.with_person_names(person_resolution.group_people(rows, persons), persons)

    def _self_email(owner: str, hint: str = "") -> str:
        if hint:
            return hint
        if email_of_owner is not None:
            return email_of_owner(owner) or ""
        if wsr is None:
            return ""
        try:
            rows, _ = membership_mod.reconciled_memberships(wsr.root, owner, _index)
            for r in rows:
                for m in membership_mod.read_members(wsr.root, str((r or {}).get("workspace_id") or "")):
                    if str(m.get("subject") or "") == owner and m.get("email"):
                        return str(m["email"])
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _pages(owner: str) -> list:
        """The caller's own person pages: path and the front matter that names who it is."""
        if pages_of is not None:
            return list(pages_of(owner))
        out: list = []
        try:
            desk = wsr.workspace_dir(owner) if wsr is not None else None
        except ValueError:
            return out
        if desk is None:
            return out
        rel = PAGES_DIR.as_posix()
        # by descriptor, never by name: a person page is on a work tree the model's tools write
        for name in wpaths.list_files_inside(desk, rel, suffix=".md")[:500]:
            head = wpaths.read_head_inside(desk, f"{rel}/{name}", 2048)
            if head is None:
                continue
            stem = name[:-3]
            fm = {k: v.strip("\"'") for k, v in _FM.findall(head.split("\n---", 1)[0])}
            out.append({"path": f"{rel}/{name}", "id": fm.get("id") or stem,
                        "title": fm.get("title") or fm.get("name") or stem.replace("-", " "),
                        "email": fm.get("email", ""), "role": fm.get("role", ""), "company": fm.get("company", "")})
        return out

    def _dms(owner: str, sessions: list) -> list:
        out = []
        if store is None:
            return out
        for s in sessions or []:
            sid = str(s.get("id") or "")
            if not sid.startswith("dm-"):
                continue
            try:
                parts = [p["principal"] for p in store.participants(sid) if p.get("kind", "human") == "human"]
                notes = store.notes(sid, limit=20)
            except Exception:  # noqa: BLE001
                continue
            out.append({"id": sid, "participants": parts, "notes": notes, "last_active": s.get("last_active")})
            if len(out) >= 50:
                break
        return out

    def _sessions(owner: str) -> list:
        try:
            return list(store.list_for(owner) or [])[:200] if store is not None else []
        except Exception:  # noqa: BLE001
            return []

    def gather(owner: str, *, now: float, sessions=None, meetings=None, waiting=None, states=None,
               email: str = "") -> dict:
        sessions = _sessions(owner) if sessions is None else sessions
        if meetings is None:
            try:
                meetings = list(schedule_source(owner) or []) if schedule_source is not None else []
            except Exception:  # noqa: BLE001
                meetings = []
        if waiting is None:
            waiting = queue_source(owner) or []
        rows = []
        for s in sessions:
            if s.get("id") and not str(s["id"]).startswith("dm-") and (s.get("shared") or s.get("owner") != owner):
                try:
                    rows.append({"id": s["id"], "participants": store.humans(s["id"])})
                except Exception:  # noqa: BLE001
                    continue
        return {"dir": people.Directory(owner, _self_email(owner, email), _colleagues(owner), _pages(owner)),
                "dms": _dms(owner, sessions), "meetings": meetings, "waiting": waiting,
                "states": states or {}, "shared": rows[:60], "now": now}

    def _held(owner: str, now: float) -> tuple[dict, dict]:
        if store is None:
            return {}, {}
        hs = home.HomeStore(store, clock=clock)
        events = hs.events(owner, since=now - 60 * 86400)
        return home.suppressed(events, now), home.item_states(events)

    def _followups(g: dict, held: dict, skip_dm: float = 0.0) -> list:
        return people.followups(dir=g["dir"], now=g["now"], dms=g["dms"], meetings=g["meetings"],
                                waiting=g["waiting"], states=g["states"], held=held, skip_dm_newer_than=skip_dm)

    def _discover(g: dict, held: dict) -> list:
        return people.discover(dir=g["dir"], now=g["now"], dms=g["dms"], meetings=g["meetings"],
                               sessions=g["shared"], held=held)

    def home_source(ctx: dict) -> list:
        """The home's ``people`` source. The home already shows a direct chat that moved in the last two
        days, so an unanswered one joins here only after that; feedback is the home's own."""
        owner, now = str(ctx.get("owner") or ""), float(ctx.get("now") or clock())
        if not owner:
            return []
        g = gather(owner, now=now, sessions=ctx.get("sessions"), meetings=ctx.get("meetings"),
                   waiting=ctx.get("waiting"), states=ctx.get("states"))
        fu = _followups(g, {}, skip_dm=2 * 86400)[:HOME_FOLLOWUPS]
        dc = _discover(g, {})[:HOME_DISCOVER]
        return (people.candidates(fu, g["dir"], now)
                + people.candidates(dc, g["dir"], now, prior_scale=DISCOVER_SCALE))

    home_sources.register_source("people", home_source)

    def _answer(request: Request, which: str, limit: int) -> dict:
        owner, now = str(subject_of(request)), clock()
        held, states = _held(owner, now)
        g = gather(owner, now=now, states=states, email=request.headers.get("x-user-email") or "")
        items = (_followups(g, held) if which == "followups" else _discover(g, held))[:limit]
        return {"items": people.for_agent(items, g["dir"], now), "count": len(items)}

    @router.get("/api/people/followups")
    def followups(request: Request, limit: int = Query(default=10, ge=1, le=20)):
        """Who the caller should follow up with, most overdue first: unanswered direct chats, meetings
        that ended with no follow-up recorded, their queue naming a person (unanswered mail), and
        contacts gone quiet against their usual cadence. Each item: the person, why, the evidence,
        and actions (continue the chat, or a follow-up that is DRAFTED only — sending is the person's
        explicit go). Snoozed or done items (home feedback) are left out."""
        return _answer(request, "followups", limit)

    @router.get("/api/people/discover")
    def discover(request: Request, limit: int = Query(default=10, ge=1, le=20)):
        """People worth knowing, only from what the caller may see: colleagues sharing a workspace who
        were never messaged, frequent attendees of the caller's meetings with no person page yet, and
        introductions between two of the caller's contacts who met them about the same subject."""
        return _answer(request, "discover", limit)

    @router.post("/api/people/me/accounts-merged")
    def accounts_merged(request: Request):
        """D35: the person confirmed on their merge card, from every login, that these accounts are all
        theirs. Each of their accounts now gets the workspaces any of them is in (up to contributor),
        so either address opens them. A person's own act (``person: true`` in routes.v1); nothing
        happens unless the CRM says the person merged them."""
        owner = str(subject_of(request))
        if wsr is None or mindex is None:
            return {"merged": False, "granted": 0}
        return person_resolution.union_memberships(wsr.root, owner, request.headers.get("x-user-email") or "",
                                                   mindex, persons)

    @router.get("/api/people/{person}/profile")
    def profile(person: str, request: Request):
        """D35: a person's profile (their claimed CRM record's person-owned fields) under the directory
        rule — the caller themself, or someone they share a workspace with. Anyone else is answered
        exactly like someone with no profile. Never an org field."""
        owner = str(subject_of(request))
        email = request.headers.get("x-user-email") or ""
        if person != owner:
            row = next((p for p in _colleagues_raw(owner) if str(p.get("subject")) == person), None)
            if row is None:
                return {"subject": person, "profile": None}
            email = str(row.get("email") or "")
        return {"subject": person, "profile": persons.profiles([{"subject": person, "email": email}]).get(person)}

    @router.get("/api/profiles/public/{handle}")
    def public_profile(handle: str):
        """A public profile page (``/p/<handle>``): capability-gated by the handle the person published,
        no account needed; only the fields the person marked public. 404 otherwise."""
        page = persons.public_profile(handle)
        if page is None:
            raise HTTPException(status_code=404, detail="no public profile")
        return page

    @router.get("/api/people/known")
    def known(request: Request, limit: int = Query(default=200, ge=1, le=500),
              q: str = Query(default="", max_length=80)):
        """EVERYONE THE CALLER HAS INTERACTED WITH (the home's People strip; founder 2026-10-15): met
        in a meeting or a call, chatted with, shares a workspace with — merged by account and address,
        most recent first, each with how the caller knows them and their page when the caller has one.
        Only what the caller may see; never a test account or an unidentified speaker."""
        owner, now = str(subject_of(request)), clock()
        g = gather(owner, now=now, email=request.headers.get("x-user-email") or "")
        sessions = _sessions(owner)
        last = {str(s.get("id")): s.get("last_active") for s in sessions}
        shared = [{**r, "last_active": last.get(str(r["id"]))} for r in g["shared"]]
        rows = people.known(dir=g["dir"], now=now, dms=g["dms"], meetings=g["meetings"], shared=shared)
        # ONE SET FOR THE HOME AND THE @ PICKER (founder: "all those people should be available as
        # @"): searched by name, address or organisation
        needle = q.strip().lower()
        if needle:
            rows = [r for r in rows if needle in " ".join(str(x or "") for x in (
                r["name"], r["person"].get("email"), r["person"].get("role"))).lower()]
        return {"people": rows[:limit], "count": len(rows)}

    @router.get("/api/people/peers")
    def peers(request: Request, limit: int = Query(default=500, ge=1, le=2000)):
        """EVERY HUMAN THE CALLER SHARES A WORKSPACE WITH (Slack-like; founder 2026-10-17, UI-1 People):
        the union of the members of every group workspace the caller is a member of — the authoritative
        rosters (``policy/members.json``), the same set the @ picker may add — each once, with their
        role in each shared workspace. Nobody outside those rosters is named; people seen only in the
        CRM are the CRM's to show, per the caller's CRM access.

        ``shared: refuse``: a worker whose answer other people read (a room's turn, any
        ``delegation.shared`` token) is refused with :data:`identity_token.SHARED_REFUSAL`, so a room
        cannot enumerate the people of its members' other workspaces."""
        headers = request.headers
        if identity_token.is_shared(headers) or room_principal.is_room_unit(identity_token.session_of(headers)):
            return JSONResponse(identity_token.SHARED_REFUSAL, status_code=403)
        owner = str(subject_of(request))
        rows = []
        for p in _colleagues(owner):
            ws = [w for w in (p.get("workspaces") or []) if w]
            if not ws:
                continue
            rows.append({"subject": p.get("subject"), "name": p.get("name") or "", "email": p.get("email") or "",
                         "workspaces": ws, "roles": dict(p.get("roles") or {})})
        return {"people": rows[:limit], "count": len(rows)}

    return router
