"""routers/desktop.py — what the things on a person's desktop ARE right now (desktop D1, #1783).

    POST /api/desktop/sections   {refs: [...]} → DesktopSections (contracts/desktop.v1)

The desktop is a READ MODEL. crm-api stores which agents and files a person placed and how they
arranged the sections; this route answers, for the person's own app, what each of those things is
now — and the one section nobody places: every workspace the person can open. The terminal's
`/api/desktop` composes the two per request. Nothing here is stored and nothing calls a model: it is
the same listings, rosters, chats, calls and git history the rest of agent-api already reads.

WHAT EACH PART READS (one writer each, none of them this route):

  workspaces     `workspace_names.listing` — the names the Files pane shows, hidden ones counted
                 only; the roster from `policy/members.json`, shown to whoever the members route
                 shows it to (contributor and owner; a reader sees the workspace, not its roster);
                 the last change from `routers/workspaces.last_change`, the function the front
                 page's line is made by; calls from `agent_session_call`, bound to a workspace by
                 the chat they were held in (that chat's target).
  presence       `_global/POLICIES.md` `presence_visibility` (off · shared_chats · workspace_members,
                 default workspace_members), coarse ("now" < 10 min, "today" < 24 h), never one's
                 own, and never for a person who switched theirs off (`POST /api/home/presence`).
  agents         the person's OWN routines, as flows lists them (`routine_flows`); status, last
                 and next run are flows' evidence — no `paused` is stored for the desktop.
  files          `_read_target`, the guard the file read uses: a ref the viewer cannot open, or one
                 that is gone, is `missing` and says nothing else.

CALLS — EVERY MEETING, WHATEVER THE KIND (Meet / Teams / Zoom / Jitsi bot meetings and in-app Vexa
calls). A meeting belongs to a workspace when
  · its note page lives there — the row's own recorded `note_path` (Vexa-ai/vexa#1601) or a page
    whose frontmatter names the meeting's row id (`meeting_note`'s tiers 0 and 1), or
  · its chat works there — the chat that sent the bot (`meeting` binding, #1597) or the meeting's own
    `meet-<row>` chat, by that chat's target; a Vexa call by the chat it was held in, or
  · it is bound to that shared workspace (`data.workspace_id`).
WHICH MEETINGS: only rows in the viewer's own meetings list (`schedule_source`, meeting-api's
`GET /meetings` as the viewer: theirs and the ones shared with them). A note in a shared tree naming a
meeting the viewer cannot read links nothing, so a tile never says such a meeting exists. Live is the
row's status (`active`) for a bot meeting and an open LiveKit room for a Vexa call; the newest wins.
The line is the meeting's display name (`display_name`, UI decision 14), never an id.

PERSON ONLY. A delegated caller is refused (`person_only`): the desktop is the person's own page,
and an agent reads its arrangement through crm-api's `desktop_read`.

BOUNDED, because this runs on every page load: at most `ACTIVITY_SCAN_MAX` workspaces and
`FILE_CHANGE_MAX` files have their git history read, at most `PRESENCE_MEMBERS_MAX` colleagues'
chat indexes are read, the newest `CALL_SCAN` chats are looked at for calls and at most
`LIVE_CHECKS_MAX` rooms are asked whether they are still open; meetings are ONE read of the viewer's
(TTL-cached) meetings list, the newest `MEETING_ROWS_MAX` of them, and at most `NOTE_SCAN_MAX` note
heads per scanned workspace.
"""
from __future__ import annotations

import contextvars
import logging
import re
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from control_plane import attention as att
from control_plane import page_grammar as page_grammar_mod
from control_plane import page_render as page_render_mod
from control_plane import universal_refs as universal_refs_mod
from control_plane import workspace_pins
from control_plane.routers import refs as refs_router
from workspaces.shared import links as links_mod
from control_plane import display_name as display_mod
from control_plane import features as features_mod
from control_plane import front_page
from control_plane import meeting_mint
from control_plane import meeting_note
from control_plane import global_layer
from control_plane import home as home_mod
from control_plane import routine_flows
from control_plane import workspace_icon
from control_plane import system_mounts
from control_plane import workspace_membership as membership_mod
from control_plane import workspace_names as names_mod
from control_plane.ceiling import refuse_delegated, require_read_in_ceiling
from control_plane import identity_presence
from control_plane.routers.home import presence_of, presence_switched_off, shared_chat_presence
from control_plane.routers.workspaces import last_change, page_head
from control_plane.workspace_reader import home_of
from shared import proposals as proposals_store
from workspaces.shared import workspace_paths as wpaths

logger = logging.getLogger("agent_api.desktop")

#: The reads one desktop request starts early or fans out (flows, chat lists, git histories). Its
#: tasks never wait on each other, so a shared pool cannot deadlock; a full pool only queues.
_POOL = ThreadPoolExecutor(max_workers=32, thread_name_prefix="desktop-read")

MAX_REFS = 100
ACTIVITY_SCAN_MAX = 12
FILE_CHANGE_MAX = 30
PRESENCE_MEMBERS_MAX = 40
MEMBERS_MAX = 50
CALL_SCAN = 40
LIVE_CHECKS_MAX = 3
MEETING_ROWS_MAX = 100
NOTE_SCAN_MAX = 30
NOTE_HEAD_BYTES = 2048

#: The meeting kinds a tile counts as a call, by meeting-api's platform names. A voice note is not a
#: call; a platform this list does not know is left out rather than guessed at.
CALL_PLATFORMS = ("google_meet", "teams", "zoom", "jitsi", "vexa_call")
VEXA_CALL = "vexa_call"
LIVE_STATUSES = ("active",)
ENDED_STATUSES = ("completed", "failed", "stopping")

POLICY_KEY = "presence_visibility"
VISIBILITY = ("off", "shared_chats", "workspace_members")
DEFAULT_VISIBILITY = "workspace_members"

DESK = "personal"
PERSONAL = "Personal"
UNTITLED_CHAT = "Untitled chat"
COMPANY = "Company"
_ACCESS = {"owner": "owner", "contributor": "contributor"}   # anything else reads
#: `{{changes-since}}` with no earlier visit known: the last week.
CHANGES_WINDOW_SEC = 7 * 86400
_PLUMBING = re.compile(r"^(policy|policies|seed|workspace identity|identity|scaffold|init|sync|merge|chore|wip|pin|unpin)\b", re.I)
_EDIT = re.compile(r"^edit (\S+)(?: \(.*\))?$", re.I)
_SCAFFOLD = re.compile(r"^((claude|agents)\.md$|(flows|skills|policies|policy|templates|kg/templates|\.[^/]*|_[^/]*)/)", re.I)


def _who_name(tile: dict, subject) -> str:
    for m in tile.get("members") or []:
        if str(m.get("subject")) == str(subject or ""):
            return str(m.get("name") or subject)
    return "Someone"


def _people_rows(tile: dict, cards: list, viewer: str) -> list:
    """`{{people}}` (founder 2026-10-17, "a workspace is about the people around it"): named faces
    with role and presence, and who is waiting on whom — from the cards the viewer may read only."""
    if tile.get("kind") != "shared":
        return []
    rows = []
    for m in tile.get("members") or []:
        name = str(m.get("name") or m.get("subject"))
        waits_on = sum(1 for c in cards if str(c.get("waiting") or "").strip().lower() in
                       {name.lower(), str(m.get("subject") or "").lower()} and c.get("waiting"))
        bits = [str(m.get("role") or "")]
        pres = m.get("presence")
        if isinstance(pres, dict) and pres.get("state"):
            bits.append(str(pres["state"]))
        elif isinstance(pres, str) and pres:
            bits.append(pres)
        if waits_on:
            bits.append(f"{waits_on} waiting on {'you' if str(m.get('subject')) == viewer else name.split(' ')[0]}")
        rows.append({"key": m["subject"], "title": name + (" (you)" if str(m.get("subject")) == viewer else ""),
                     "detail": " · ".join(b for b in bits if b), "ref": f"person:{m['subject']}",
                     "state": m.get("initials") or ""})
    return rows


def _strip_front_matter(md: str) -> str:
    """A page's front matter is metadata, never rendered."""
    if md.startswith("---\n"):
        end = md.find("\n---", 4)
        if end >= 0:
            return md[end + 4:].lstrip("\n")
    return md


class DesktopSectionsBody(BaseModel):
    """The refs placed on the person's desktop (crm-api's placements), resolved for this viewer."""
    model_config = ConfigDict(extra="forbid")
    refs: list[Annotated[str, StringConstraints(max_length=600)]] = Field(
        default_factory=list, max_length=MAX_REFS,
        description="`agent:<routine id>`, `chat:<chat session id>` and `file:<workspace slug>/<path>` "
                    "refs; any other kind is not resolved here.")


# ── the attention home (home R3, decision E1) ─────────────────────────────────────────────────────

MAX_CARDS = 100
#: The newest chats looked at for proposals, mentions and rooms (the person's own store rows).
SESSION_SCAN = 100
#: …of which at most this many are asked for their post-call proposals.
PROPOSAL_SCAN = 40
MENTIONS_MAX = 50
UPCOMING_STATUSES = ("scheduled", "requested", "planned")
NOT_A_CALL = ("voice_note",)
_Str = Annotated[str, StringConstraints(max_length=300)]
_Slug = Annotated[str, StringConstraints(max_length=200)]


class CardMemberIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    subject: _Str = ""
    status: Annotated[str, StringConstraints(max_length=20)] = ""


class CardIn(BaseModel):
    """A card as the crm desk read returned it to the terminal (crm.v1 CardView): only the fields the
    attention rules read. The body never widens access — a card naming a workspace the viewer cannot
    open is dropped, whatever else it says."""
    model_config = ConfigDict(extra="ignore")
    id: _Slug
    title: Optional[_Str] = ""
    state: Optional[_Str] = ""
    next: Optional[_Str] = None
    waiting: Optional[_Str] = None
    due: Optional[Annotated[str, StringConstraints(max_length=40)]] = None
    workspaces: list[_Slug] = Field(default_factory=list, max_length=20)
    members: list[CardMemberIn] = Field(default_factory=list, max_length=200)
    # S5 (D11): the structured next actor and whether the card is still open
    next_actor: Optional[Annotated[str, StringConstraints(max_length=128)]] = None
    open: Optional[bool] = None


class AttentionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cards: list[CardIn] = Field(default_factory=list, max_length=MAX_CARDS,
                                description="The crm desk's cards, as the terminal read them.")
    workspace: Optional[_Slug] = Field(default=None, description=(
        "Only this workspace (the workspace page): its key alone, with its rooms, chats, routines and "
        "calls. One the viewer cannot open answers empty."))


class PageBody(BaseModel):
    """`POST /api/desktop/page` (PRD 0.13.4 D17): one page rendered for this viewer. The terminal's
    BFF passes what only it can read with the viewer's own key — the crm desk's cards, each
    ``{{crm …}}`` block's answer (crm-api ``blocks/read``) and each card block's rows (the card read
    interface) — keyed by the block's ``page_grammar.Block.key``. A missing key = that owner is down."""
    model_config = ConfigDict(extra="forbid")
    workspace: _Slug = Field(default="personal")
    path: Annotated[str, StringConstraints(max_length=300)] = "README.md"
    cards: list[CardIn] = Field(default_factory=list, max_length=MAX_CARDS)
    crm: dict[Annotated[str, StringConstraints(max_length=600)], Optional[dict]] = Field(default_factory=dict, max_length=40)
    card_blocks: dict[Annotated[str, StringConstraints(max_length=600)], Optional[list[dict]]] = Field(
        default_factory=dict, max_length=40)
    since: Optional[float] = Field(default=None, description="The viewer's previous visit (epoch s), for {{changes-since}}.")


class RefsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refs: list[Annotated[str, StringConstraints(max_length=600)]] = Field(default_factory=list, max_length=MAX_REFS)
    workspace: _Slug = Field(default="personal", description="The page's workspace, for relative file refs.")
    cards: list[CardIn] = Field(default_factory=list, max_length=MAX_CARDS)



#: An in-app call nobody has been in this long is ended (the room's own timeouts cover the rest).
ORPHAN_CALL_GRACE_SEC = 300

def presence_visibility(raw_policies: Optional[str]) -> str:
    """`presence_visibility` from `_global/POLICIES.md`. Unset is the default; a value nobody can read
    is `off` — a typo in a privacy key hides presence, it never widens it."""
    value = (front_page.front_matter(raw_policies or "").get(POLICY_KEY) or "").strip().lower()
    if not value:
        return DEFAULT_VISIBILITY
    if value in VISIBILITY:
        return value
    logger.warning("POLICIES.md %s: %r is not one of %s — presence is off", POLICY_KEY, value,
                   ", ".join(VISIBILITY))
    return "off"


def initials(name: str) -> str:
    words = [w for w in str(name or "").replace(".", " ").split() if w[:1].isalnum()]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][:1] + words[-1][:1]).upper()


def activity_line(change: Optional[dict]) -> Optional[dict]:
    """A last change as the tile's line — who changed what — and when, or None."""
    if not change:
        return None
    who = change.get("author")
    pages = change.get("pages") or []
    count = int(change.get("count") or 0)
    what = pages[0]["title"] if count == 1 and pages else (f"{count} pages" if count > 1 else "")
    if who and what:
        line = f"{who} changed {what}"
    elif who:
        line = f"{who} made a change"
    elif what:
        line = f"{what[:1].upper()}{what[1:]} changed"
    else:
        line = "Changed"
    return {"line": line, "at": int(change["ts"]) if change.get("ts") else None}


def _day(ts: float) -> str:
    g = time.gmtime(ts)
    return f"{time.strftime('%b', g)} {g.tm_mday}"


def _agent_status(f: dict) -> str:
    if f.get("status") != "active":
        return "paused"
    last = (f.get("last_run") or {}).get("status")
    if last in ("running", "retrying", "pending"):
        return "running"
    if last in ("failed", "refused", "blocked"):
        return "failed"
    return "idle"


def _last_line(last: Optional[dict]) -> Optional[str]:
    if not last:
        return None
    status = str(last.get("status") or "")
    reason = str(last.get("reason") or "").strip()[:120]
    if status == "done":
        return "Ran"
    if status in ("running", "retrying", "pending"):
        return "Running"
    word = {"failed": "Failed", "refused": "Refused", "blocked": "Stopped"}.get(status, status.capitalize() or "Ran")
    return f"{word}: {reason}" if reason else word


def _agent_name(name: str) -> str:
    words = front_page.humanized(name)
    return (words[:1].upper() + words[1:]) if words else name


def build(**d) -> APIRouter:
    router = APIRouter()
    subject_of = d["subject_of"]
    wsr = d["wsr"]
    mindex = d["mindex"]
    sess = d["sess"]
    settings = d["settings"]
    store = d.get("session_store")
    livekit = d.get("livekit")
    workspace_registry = d["workspace_registry"]
    _read_target = d["_read_target"]
    schedule_source = d.get("_schedule_source")
    notifier = d.get("notifier")

    def _gdir() -> Path:
        return system_mounts.global_root(settings, wsr.root)

    # ── ONE READ PER KIND PER REQUEST (2026-10-18: the workspace page took 15-31 s). Each route runs
    # inside `workspace_membership.read_scope()`; these remember a person's chat lists, chat targets
    # and room head-counts until it ends, so a page reads each once instead of once per tile/row.
    memo = membership_mod.scope_memo

    def _future(key: tuple, fn, *, early: bool = False) -> Future:
        """``fn()``'s answer as a future, once per read scope for ``key``: started on the pool now
        when ``early`` (it then runs beside the rest of the request), else computed here."""
        def start() -> Future:
            if early:
                return _POOL.submit(contextvars.copy_context().run, fn)
            f: Future = Future()
            try:
                f.set_result(fn())
            except Exception as exc:  # noqa: BLE001 — raised again to the caller, as before
                f.set_exception(exc)
            return f
        return memo(key, start)

    def _list_for(subject: str) -> list[dict]:
        return _future(("desktop.list_for", str(subject)), lambda: store.list_for(subject)).result()

    def _index(subject: str) -> list[dict]:
        return _future(("desktop.index", str(subject)), lambda: sess.list(subject)).result()

    def _early_reads(subject: str, only: Optional[str]) -> None:
        """Start the slow independent reads of an attention answer before the tiles are built:
        flows' routines (and, for one workspace's page, its runs), the chat list, the chat index."""
        _future(("desktop.routines", subject), lambda: _routine_rows(subject), early=True)
        if only is not None:
            _future(("desktop.runs", subject), lambda: _run_history(subject), early=True)
        if store is not None:
            _future(("desktop.list_for", subject), lambda: store.list_for(subject), early=True)
        _future(("desktop.index", subject), lambda: sess.list(subject), early=True)

    def _prefetch_targets(pairs) -> None:
        known = memo(("desktop.targets",), dict)
        want = list(dict.fromkeys((str(o), str(n)) for o, n in pairs if n))
        want = [p for p in want if p not in known]
        get = getattr(sess, "targets", None)
        if not want or get is None:
            return
        try:
            known.update(get(want))
        except Exception:  # noqa: BLE001 — each chat is then asked on its own, as before
            pass

    def _target_of(owner, name) -> str:
        """``sess.target`` (raises as it does), answered from the request's one batched read."""
        known = memo(("desktop.targets",), dict)
        key = (str(owner), str(name))
        if key in known:
            return known[key]
        return sess.target(owner, name)

    def _prefetch_humans(ids) -> None:
        known = memo(("desktop.humans",), dict)
        want = [i for i in dict.fromkeys(str(i) for i in ids if i) if i not in known]
        get = getattr(store, "participants_of", None) if store is not None else None
        if not want or get is None:
            return
        try:
            parts = get(want)
        except Exception:  # noqa: BLE001 — each room is then counted on its own, as before
            return
        for i in want:
            known[i] = len([p for p in parts.get(i, ()) if p.get("kind") == "human"])

    def _parallel(fn, items: list) -> list:
        """``[fn(x) for x in items]`` on a few threads (each git read is a subprocess), every call in a
        copy of this request's context so the read scope's memos are shared."""
        if len(items) < 2:
            return [fn(x) for x in items]
        futs = [_POOL.submit(contextvars.copy_context().run, fn, x) for x in items]
        return [f.result() for f in futs]

    def _rows(subject: str) -> list[dict]:
        memberships, _ = membership_mod.reconciled_memberships(wsr.root, subject, mindex.list)
        return names_mod.listing(wsr.root, subject, registry=workspace_registry,
                                 index_list=lambda _s: memberships)

    def _member(subject: str, email: Optional[str], role: str, gdir: Path) -> dict:
        try:
            name = front_page.display_name(wsr.root, subject, email=email, global_dir=gdir)
        except Exception:  # noqa: BLE001 — a name that cannot be read is the subject's own id
            name = None
        name = name or subject
        return {"subject": subject, "name": name, "initials": initials(name),
                "role": _ACCESS.get(role, "reader"), "presence": None}

    def _target(request: Request, slug: Optional[str]) -> Optional[Path]:
        try:
            return _read_target(request, slug)
        except (HTTPException, ValueError, OSError):
            return None

    def _tiles(request: Request, subject: str, email: Optional[str], gdir: Path, admin: bool):
        """Every workspace the person can open, as the desktop's tiles (with the last change), plus
        the listing they came from, the names by slug and the authorized directories read. Shared by
        the sections and the attention routes, so both answer the SAME slugs."""
        rows = [] if admin else _rows(subject)
        shown = [r for r in rows if r.get("state") != "hidden"]
        names = {r["slug"]: (r.get("display") or r["name"]) for r in rows}
        names[DESK] = PERSONAL
        names[system_mounts.GLOBAL_SLUG] = COMPANY

        # ── workspaces ────────────────────────────────────────────────────────────────────────
        me = _member(subject, email, "owner", gdir)
        tiles: list[dict] = []
        for r in shown:
            kind = r["kind"]
            if kind == "personal":
                name = r["name"] if r["name"] not in ("", subject) else PERSONAL
                tiles.append({"slug": DESK, "id": subject, "name": name, "kind": "private",
                              "access": "owner", "write": True, "members": [dict(me)]})
            elif kind == "private":
                tiles.append({"slug": r["slug"], "id": r["slug"], "name": r.get("display") or r["name"],
                              "kind": "private", "access": "owner", "write": True, "members": [dict(me)]})
            elif kind == "shared":
                access = _ACCESS.get(str(r.get("role") or ""), "reader")
                members: list[dict] = []
                if access != "reader":
                    # the roster is shown to whoever `GET /api/workspace/members` shows it to
                    try:
                        roster = membership_mod.read_members(wsr.root, r["slug"])[:MEMBERS_MAX]
                    except Exception:  # noqa: BLE001
                        roster = []
                    for mrow in roster:
                        who = str(mrow.get("subject") or "")
                        if who:
                            members.append(_member(who, mrow.get("email"), str(mrow.get("role") or ""), gdir))
                tiles.append({"slug": r["slug"], "id": r["slug"], "name": r.get("display") or r["name"],
                              "kind": "shared", "access": access, "write": access != "reader",
                              "members": members})
        if gdir.is_dir():
            tiles.append({"slug": system_mounts.GLOBAL_SLUG, "id": system_mounts.GLOBAL_SLUG, "name": COMPANY,
                          "kind": "company", "access": "owner" if admin else "reader", "write": bool(admin),
                          "members": []})
        if admin:
            tiles = [t for t in tiles if t["kind"] == "company"]

        dirs: dict[str, Path] = {}
        jobs: list[tuple[dict, Path]] = []
        for i, t in enumerate(tiles):
            t["last_activity"] = None
            t["last_call"] = None
            t["live_call"] = None
            if i >= ACTIVITY_SCAN_MAX:
                continue
            target = _target(request, None if t["slug"] == DESK else t["slug"])
            if target is None:
                continue
            dirs[t["slug"]] = target
            jobs.append((t, target))

        def line_of(job: tuple[dict, Path]) -> Optional[dict]:
            t, target = job
            try:
                _, change = last_change(wsr, target, slug=t["slug"], path=None, viewer=subject, global_dir=gdir)
            except Exception:  # noqa: BLE001 — a history that cannot be read costs the line, not the page
                change = None
            return activity_line(change)

        for (t, _target_dir), line in zip(jobs, _parallel(line_of, jobs)):
            t["last_activity"] = line

        return rows, shown, names, tiles, dirs

    @router.post("/api/desktop/sections")
    def desktop_sections(request: Request, body: DesktopSectionsBody = Body(default=None)):
        """THE DESKTOP'S SECTIONS, read fresh for the person's own app: every workspace they can open
        (hidden ones counted), and what each placed agent and file ref is now. Deterministic, no model
        call, nothing stored. Person only."""
        refuse_delegated(request, reason="person_only", instruction=(
            "The desktop is read by the person's own app. To read or change how it is arranged, use "
            "desktop_read or desktop_arrange_propose."))
        body = body or DesktopSectionsBody()
        with membership_mod.read_scope():
            return _sections(request, body)

    def _sections(request: Request, body: DesktopSectionsBody) -> dict:
        subject = str(subject_of(request))
        email = (request.headers.get("x-user-email") or "").strip() or None
        now = time.time()
        gdir = _gdir()
        visibility = presence_visibility(features_mod.read_policies(gdir))
        admin = global_layer.is_admin_account(settings, subject)
        rows, shown, names, tiles, dirs = _tiles(request, subject, email, gdir, admin)
        by_slug = {t["slug"]: t for t in tiles}
        _calls(subject, email, by_slug, dirs)
        _presence(subject, visibility, tiles, now)

        # ── placed refs ───────────────────────────────────────────────────────────────────────
        refs: list[str] = []
        for ref in body.refs:
            if ref not in refs:
                refs.append(ref)
        agents = _agents(subject, [r for r in refs if r.startswith("agent:")], names)
        files = _files(request, subject, [r for r in refs if r.startswith("file:")], names, gdir)
        out = {"presence_visibility": visibility, "workspaces": tiles,
               "hidden_workspaces": len(rows) - len(shown), "agents": agents, "files": files}
        chat_refs = [r for r in refs if r.startswith("chat:")]
        if chat_refs:
            out["chats"] = _chats(subject, chat_refs, names)
        return out

    # ── calls ─────────────────────────────────────────────────────────────────────────────────

    def _calls(subject: str, email: Optional[str], by_slug: dict, dirs: dict, *,
               raw: Optional[list] = None, extra: tuple = ()) -> tuple[list, dict, list]:
        """The live and the last call per workspace — the newest of every meeting the viewer may read
        that belongs there, bot meetings and Vexa calls alike (see the module header). Answers the
        call rows, every row's homes (``extra`` rows homed too — the attention route's upcoming ones)
        and the Vexa calls, for a caller that reads more than the tiles."""
        me = [m for m in (email, subject) if m]
        lives: dict[str, list[dict]] = {}
        lasts: dict[str, list[dict]] = {}
        rows = _meeting_rows(subject, raw)
        by_id = {str(r.get("id")): r for r in rows}
        seen: dict[str, set] = {}
        vexa = _vexa_calls(subject, by_slug, by_id, me)
        for slug, call in vexa:
            (lives if "since" in call else lasts).setdefault(slug, []).append(call)
            seen.setdefault(slug, set()).add(call["meeting"])
        homes: dict[str, set] = {}
        if rows or extra:
            homes = _meeting_homes(subject, rows + list(extra), by_slug, dirs)
            for row in rows:
                mid = str(row.get("id"))
                for slug in homes.get(mid, ()):
                    if mid in seen.get(slug, ()):
                        continue
                    call = _bot_call(row, me)
                    if call is None:
                        continue
                    (lives if "since" in call else lasts).setdefault(slug, []).append(call)
        for slug, tile in by_slug.items():
            if lives.get(slug):
                tile["live_call"] = max(lives[slug], key=lambda c: c["since"])
            if lasts.get(slug):
                tile["last_call"] = max(lasts[slug], key=lambda c: c["at"])
        return rows, homes, vexa

    def _raw_meetings(subject: str) -> list[dict]:
        """ONE read of the viewer's own meetings list (meeting-api's `GET /meetings` as them)."""
        if schedule_source is None:
            return []
        try:
            return [r for r in list(schedule_source(subject) or []) if isinstance(r, dict)]
        except Exception:  # noqa: BLE001 — meeting-api did not answer: the tiles show Vexa calls only
            return []

    def _meeting_rows(subject: str, raw: Optional[list] = None) -> list[dict]:
        """The viewer's meetings that can be calls, newest first: ONE read of their meetings list."""
        raw = _raw_meetings(subject) if raw is None else raw
        out = []
        for r in raw:
            if not isinstance(r, dict) or str(r.get("platform") or "") not in CALL_PLATFORMS:
                continue
            start = meeting_mint._epoch(r.get("start_time"))
            if start is None or not str(r.get("id") or "").strip():
                continue          # a bot that never got in held no call
            out.append((start, r))
        out.sort(key=lambda p: p[0], reverse=True)
        return [r for _, r in out[:MEETING_ROWS_MAX]]

    def _meeting_homes(subject: str, rows: list[dict], by_slug: dict, dirs: dict) -> dict[str, set]:
        """meeting row id → the tiles it belongs to: by its note page, its chat, its bound workspace."""
        ids = {str(r.get("id")) for r in rows}
        homes: dict[str, set] = {}

        def add(mid: str, slug: str) -> None:
            if mid in ids and slug in by_slug:
                homes.setdefault(mid, set()).add(slug)

        for r in rows:
            data = r.get("data") if isinstance(r.get("data"), dict) else {}
            bound = str((data or {}).get("workspace_id") or "").strip()
            if bound:
                add(str(r.get("id")), bound)
        # the chat that sent the bot, or the meeting's own chat, by where that chat works
        try:
            chats = _index(subject)
        except Exception:  # noqa: BLE001
            chats = []
        for c in chats:
            name = str(c.get("session") or "")
            mid = str(c.get("meeting") or "").strip() or (name[len("meet-"):] if name.startswith("meet-") else "")
            if mid:
                add(mid, (c.get("target") or "").strip() or DESK)
        # the note page: the row's recorded path, or a page naming the row
        recorded = {}
        for r in rows:
            p = meeting_note.recorded_path(r)
            if p:
                recorded.setdefault(p.rsplit("/", 1)[-1], set()).add(str(r.get("id")))
        for slug, root in dirs.items():
            try:
                names = [n for n in wpaths.list_files_inside(root, meeting_note.MEETING_DIR, suffix=".md")
                         if n != "index.md"]
            except Exception:  # noqa: BLE001
                continue
            for n in names:
                for mid in recorded.get(n, ()):
                    add(mid, slug)
            for n in sorted(names, reverse=True)[:NOTE_SCAN_MAX]:
                try:
                    head = wpaths.read_head_inside(root, f"{meeting_note.MEETING_DIR}/{n}", NOTE_HEAD_BYTES)
                except Exception:  # noqa: BLE001
                    head = None
                if not head:
                    continue
                fm = meeting_note.front_matter(head)
                if str(fm.get("type") or "").strip().lower() not in ("", "meeting"):
                    continue
                mid = str(fm.get("meeting") or "").strip()
                if mid:
                    add(mid, slug)
        return homes

    def _bot_call(row: dict, me: list) -> Optional[dict]:
        """A meeting row as a tile's call: live while its bot is in the room, else when it ended."""
        status = str(row.get("status") or "")
        platform = str(row.get("platform") or "")
        start = meeting_mint._epoch(row.get("start_time"))
        name = display_mod.meeting_display_name(row, me=me)
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        if status in LIVE_STATUSES and platform != VEXA_CALL:     # a Vexa call is live by its room
            call = {"platform": platform, "meeting": str(row["id"]), "line": name, "since": int(start)}
            people = (data or {}).get("participants")
            if isinstance(people, list) and people:
                call["people"] = len(people)
            return call
        if status in ENDED_STATUSES:
            at = meeting_mint._epoch(row.get("end_time")) or start
            return {"platform": platform, "meeting": str(row["id"]), "line": f"{name} · {_day(at)}",
                    "at": int(at)}
        return None

    def _vexa_calls(subject: str, by_slug: dict, by_id: dict, me: list) -> list[tuple[str, dict]]:
        """The Vexa calls held in the person's newest chats, by the workspace that chat works in."""
        if store is None:
            return []
        try:
            rows = _list_for(subject)[:CALL_SCAN]
        except Exception:  # noqa: BLE001
            return []
        _prefetch_targets((r.get("owner") or "", r.get("name")) for r in rows)
        where: dict[str, tuple[str, dict]] = {}
        for row in rows:
            owner = str(row.get("owner") or "")
            try:
                target = (_target_of(owner, row["name"]) or "").strip()
            except Exception:  # noqa: BLE001
                continue
            slug = target or (DESK if owner == subject else "")
            if slug and slug in by_slug:
                chat = row["name"] if owner == subject else row["id"]
                where[row["id"]] = (slug, {**row, "chat": chat})
        if not where:
            return []
        try:
            calls = store.recent_calls(list(where), limit=100)
        except Exception:  # noqa: BLE001
            return []
        out: list[tuple[str, dict]] = []
        live_slugs: set = set()
        checks = 0
        for call in calls:
            slug, row = where[call["session_id"]]
            mid = str(call["meeting_id"])
            name = display_mod.meeting_display_name(by_id.get(mid) or {"platform": VEXA_CALL},
                                                    chat_name=str(row.get("title") or ""), me=me)
            if call["ended"] is None:
                if slug in live_slugs or livekit is None or checks >= LIVE_CHECKS_MAX:
                    continue
                checks += 1
                try:
                    room = livekit.room(call["room"])
                except Exception:  # noqa: BLE001 — a call server that does not answer shows no live call
                    room = None
                # A CALL IS LIVE ONLY WITH A PERSON IN IT (founder 2026-10-16: "· 0 people · Join"):
                # the hidden listener is nobody. Empty past its grace, the call is ended here.
                people = int(room.get("humans", room.get("num_participants")) or 0) if room is not None else 0
                if room is not None and people == 0:
                    if time.time() - float(call["started"] or 0) > ORPHAN_CALL_GRACE_SEC:
                        try:
                            livekit.delete_room(call["room"])
                            store.end_call(call["session_id"], call["room"])
                        except Exception:  # noqa: BLE001 — a room that cannot be ended stays off the home
                            logger.warning("orphan call %s not ended", call["room"])
                    room = None
                if room is not None:
                    live_slugs.add(slug)
                    rname = ""
                    try:
                        rname = str((store.room(call["session_id"]) or {}).get("name") or "")
                    except Exception:  # noqa: BLE001
                        rname = ""
                    out.append((slug, {"platform": VEXA_CALL, "session": row["chat"], "meeting": mid,
                                       "line": rname or name, "people": people,
                                       "since": int(call["started"])}))
            else:
                out.append((slug, {"platform": VEXA_CALL, "meeting": mid,
                                   "line": f"{name} · {_day(call['ended'])}", "at": int(call["ended"])}))
        return out

    # ── presence ──────────────────────────────────────────────────────────────────────────────

    def _presence(subject: str, visibility: str, tiles: list[dict], now: float) -> None:
        if visibility == "off" or store is None:
            return          # without the store a person's own switch cannot be read: show none
        hs = home_mod.HomeStore(store)
        off: dict[str, bool] = {}
        try:   # one batched identity read for every member on the page (the switch lives in identity)
            identity_presence.prefetch({str(p.get("subject")) for t in tiles for p in t.get("members") or []
                                        if p.get("subject") and str(p.get("subject")) != subject})
        except Exception:  # noqa: BLE001 — each member is then asked (and fails closed) on its own
            pass

        def switched_off(who: str) -> bool:
            if who not in off:
                try:
                    off[who] = presence_switched_off(hs, who)
                except Exception:  # noqa: BLE001 — unknown is off
                    off[who] = True
            return off[who]

        if visibility == "shared_chats":
            try:
                sessions = [{**r, "shared": r.get("owner") != subject} for r in _list_for(subject)][:200]
                seen = shared_chat_presence(store, subject, sessions, now)
            except Exception:  # noqa: BLE001
                seen = {}
            for t in tiles:
                for p in t["members"]:
                    if p["subject"] != subject and seen.get(p["subject"]) and not switched_off(p["subject"]):
                        p["presence"] = seen[p["subject"]]
            return
        chats: dict[str, list] = {}
        for t in tiles:
            if t["kind"] != "shared":
                continue
            for p in t["members"]:
                who = p["subject"]
                if who == subject:
                    continue
                if who not in chats:
                    if len(chats) >= PRESENCE_MEMBERS_MAX:
                        continue
                    try:
                        chats[who] = _index(who)
                    except Exception:  # noqa: BLE001
                        chats[who] = []
                newest = max((float(c.get("last_active") or 0) for c in chats[who]
                              if (c.get("target") or "") == t["slug"]), default=0.0)
                pres = presence_of(now - newest) if newest else None
                if pres and not switched_off(who):
                    p["presence"] = pres

    # ── agents ────────────────────────────────────────────────────────────────────────────────

    def _agents(subject: str, refs: list[str], names: dict) -> list[dict]:
        if not refs:
            return []
        if not routine_flows.configured():
            return [{"ref": r, "missing": True} for r in refs]
        try:
            flows = routine_flows.FlowsClient().routines(subject)
        except Exception:  # noqa: BLE001 — flows did not answer: the tiles say so, nothing is guessed
            logger.warning("desktop: flows did not answer for subject=%s", subject)
            return [{"ref": r, "id": r[len("agent:"):], "missing": False, "unavailable": True} for r in refs]
        try:
            state = routine_flows.load_flow_state(wsr.root, subject)
        except Exception:  # noqa: BLE001
            state = {}
        mine = {f["name"]: f for f in flows if f.get("scope") == f"user:{subject}"}
        out = []
        for ref in refs:
            rid = ref[len("agent:"):]
            name = rid[len(routine_flows.FLOW_ID_PREFIX):] if rid.startswith(routine_flows.FLOW_ID_PREFIX) else rid
            f = mine.get(name)
            if f is None or f.get("status") == "retired":
                out.append({"ref": ref, "missing": True})
                continue
            card = routine_flows.card_from_flow(subject, f, state=state)
            status = _agent_status(f)
            last = f.get("last_run") or None
            thread = card.get("thread")
            try:
                target = (sess.target(subject, thread) or "").strip() if thread else ""
            except Exception:  # noqa: BLE001
                target = ""
            what = str(card.get("plan_summary") or "").strip().splitlines()
            out.append({
                "ref": ref, "id": rid, "name": _agent_name(f["name"]),
                "what": what[0][:200] if what else None,
                "status": status,
                "last": _last_line(last),
                "last_at": int(last.get("ended_at") or last.get("started_at") or 0) or None if last else None,
                "next": (card.get("schedule") or card.get("cron")) if status != "paused" else None,
                "next_at": int(card["next_run"]) if status != "paused" and card.get("next_run") else None,
                "where": names.get(target or DESK),
                "thread": thread,
                "missing": False,
            })
        return out

    # ── chats ─────────────────────────────────────────────────────────────────────────────────

    def _chats(subject: str, refs: list[str], names: dict) -> list[dict]:
        """A placed chat is the person's OWN chat as their chat index holds it: its title, when it was
        last active and where it works. Another person's chat, or one that does not exist, is
        `missing` and says nothing else — the index is per person, so it is never looked up."""
        out = []
        for ref in refs:
            sid = ref[len("chat:"):].strip()
            gone = {"ref": ref, "missing": True}
            try:
                meta = sess.meta(subject, sid) if sid else {}
            except Exception:  # noqa: BLE001 — an index that cannot be read costs the tile, not the page
                meta = {}
            if not meta:
                out.append(gone)
                continue
            title = str(meta.get("named_title") or meta.get("title") or "").strip()
            if not title or title == sid:
                title = UNTITLED_CHAT
            try:
                last_at = int(float(meta.get("last_active") or 0)) or None
            except (TypeError, ValueError):
                last_at = None
            try:
                target = (sess.target(subject, sid) or "").strip()
            except Exception:  # noqa: BLE001
                target = ""
            out.append({"ref": ref, "id": sid, "title": title[:200],
                        "last": f"Active {_day(last_at)}" if last_at else None, "last_at": last_at,
                        "where": names.get(target or DESK), "missing": False})
        return out

    # ── files ─────────────────────────────────────────────────────────────────────────────────

    def _files(request: Request, subject: str, refs: list[str], names: dict, gdir: Path) -> list[dict]:
        out = []
        scanned = 0
        for ref in refs:
            slug, _, path = ref[len("file:"):].partition("/")
            slug, path = slug.strip(), path.strip()
            gone = {"ref": ref, "missing": True}
            if not slug or not path or slug == system_mounts.SYSTEM_SLUG:
                out.append(gone)
                continue
            own = slug in (DESK, "desk", subject)
            target = _target(request, None if own else slug)
            head = page_head(target, path) if target is not None else None
            if head is None:
                out.append(gone)
                continue
            changed = changed_at = None
            if scanned < FILE_CHANGE_MAX:
                scanned += 1
                try:
                    _, change = last_change(wsr, target, slug=slug, path=path, viewer=subject, global_dir=gdir)
                except Exception:  # noqa: BLE001
                    change = None
                if change:
                    changed = f"{change['author']} changed it" if change.get("author") else "Changed"
                    changed_at = int(change["ts"]) if change.get("ts") else None
            out.append({"ref": ref, "slug": slug, "path": path, "title": front_page.page_title(head, path),
                        "where": names.get(DESK if own else slug), "changed": changed,
                        "changed_at": changed_at, "missing": False})
        return out

    # ── the attention home (home R3, decision E1; rules in `control_plane/attention.py`) ──────────

    def _context(request: Request) -> dict:
        subject = str(subject_of(request))
        email = (request.headers.get("x-user-email") or "").strip() or None
        gdir = _gdir()
        admin = global_layer.is_admin_account(settings, subject)
        return {"subject": subject, "email": email, "gdir": gdir, "admin": admin, "now": time.time()}

    def _me_names(subject: str, email: Optional[str], gdir: Path) -> set:
        me = {subject}
        if email:
            me |= {email, email.split("@", 1)[0]}
        try:
            name = front_page.display_name(wsr.root, subject, email=email, global_dir=gdir)
        except Exception:  # noqa: BLE001
            name = None
        if name and name != subject:
            me.add(name)
        return me

    def _store_rows(subject: str) -> list[dict]:
        if store is None:
            return []
        try:
            return _list_for(subject)[:SESSION_SCAN]
        except Exception:  # noqa: BLE001
            return []

    def _chat_home(row: dict, subject: str, openable: set) -> str:
        """Where a chat the viewer reads shows: its target when the viewer can open it; the viewer's
        own desk otherwise (another person's desk or a workspace closed to the viewer is never named)."""
        owner = str(row.get("owner") or "")
        try:
            target = (_target_of(owner, row["name"]) or "").strip()
        except Exception:  # noqa: BLE001
            target = ""
        slug = att.home_slug(target, subject) if owner == subject else (target or "")
        return slug if slug in openable else att.DESK

    def _open_id(row: dict, subject: str) -> str:
        """The id the terminal opens a chat by: the person's own chat by its name, else the row id."""
        return row["name"] if str(row.get("owner")) == subject else row["id"]

    def _humans(row_id: str) -> int:
        known = memo(("desktop.humans",), dict)
        if str(row_id) in known:
            return known[str(row_id)]
        try:
            return len(store.humans(row_id)) if store is not None else 1
        except Exception:  # noqa: BLE001
            return 1

    def _unread(row_id: str, subject: str) -> int:
        """What others wrote in a room since this person last read it (the store's read mark)."""
        try:
            return int(store.unread(row_id, subject)) if store is not None and row_id else 0
        except Exception:  # noqa: BLE001 — a count that cannot be read is no badge, never a guess
            return 0

    def _closed(row: dict) -> bool:
        """The room's own settings (R1, `agent_session_room.visibility`) say it is closed. A chat with
        no room record is not a room yet: it reads open."""
        if bool(row.get("closed")) or str(row.get("visibility") or "") == "closed":
            return True
        get = getattr(store, "room", None) if store is not None else None
        try:
            rec = get(str(row.get("id") or "")) if get and row.get("id") else None
        except Exception:  # noqa: BLE001 — a room that cannot be read is not called closed
            rec = None
        return bool(rec) and str(rec.get("visibility") or "") == "closed"

    def _run_history(subject: str) -> Optional[list[dict]]:
        """The person's own runs, newest first (flows-api `GET /flows/runs`), or None without them."""
        try:
            client = routine_flows.FlowsClient()
            get = getattr(client, "runs", None)
            return list(get(subject, limit=att.RUNS_SCAN)) if get else None
        except Exception:  # noqa: BLE001 — flows did not answer: the last run alone, nothing guessed
            return None

    def _routine_rows(subject: str) -> list[dict]:
        """The viewer's OWN routines as plain dicts for the rules: flows' evidence, where they work."""
        if not routine_flows.configured():
            return []
        try:
            flows = routine_flows.FlowsClient().routines(subject)
        except Exception:  # noqa: BLE001 — flows did not answer: no routine items, nothing guessed
            logger.warning("attention: flows did not answer for subject=%s", subject)
            return []
        try:
            state = routine_flows.load_flow_state(wsr.root, subject)
        except Exception:  # noqa: BLE001
            state = {}
        out = []
        for f in flows:
            if f.get("scope") != f"user:{subject}" or f.get("status") == "retired" or not f.get("name"):
                continue
            card = routine_flows.card_from_flow(subject, f, state=state)
            thread = card.get("thread")
            try:
                target = (sess.target(subject, thread) or "").strip() if thread else ""
            except Exception:  # noqa: BLE001
                target = ""
            active = f.get("status") == "active"
            out.append({"id": f"{routine_flows.FLOW_ID_PREFIX}{f['name']}", "name": _agent_name(f["name"]),
                        "status": "active" if active else "paused", "last_run": f.get("last_run") or None,
                        "where": att.home_slug(target, subject), "owner": subject, "thread": thread,
                        "next_at": float(card["next_run"]) if active and card.get("next_run") else None,
                        "next": (card.get("schedule") or card.get("cron")) if active else None})
        return out

    def _upcoming_rows(raw: list[dict], now: float) -> list[tuple[float, dict]]:
        out = []
        for r in raw:
            if str(r.get("platform") or "") in NOT_A_CALL or str(r.get("status") or "") not in UPCOMING_STATUSES:
                continue
            if not str(r.get("id") or "").strip():
                continue
            data = r.get("data") if isinstance(r.get("data"), dict) else {}
            start = att.epoch((data or {}).get("scheduled_at")) or meeting_mint._epoch(r.get("start_time"))
            if start is not None and start > now:
                out.append((float(start), r))
        return out

    def _call_state(row: dict) -> str:
        status = str(row.get("status") or "")
        if status in LIVE_STATUSES:
            return "live"
        if status in ENDED_STATUSES:
            return "ended"
        return "upcoming"

    @router.post("/api/desktop/attention")
    def desktop_attention(request: Request, body: AttentionBody = Body(default=None)):
        """WHAT NEEDS YOU AND WHAT IS WORKING, per workspace, for the person's own home (status cards,
        the Now strip, the workspace page). Each item from its one owner, access-filtered, with its act
        as a pointer to the owner's route. Deterministic rules, no model call, nothing stored. Person
        only."""
        body = body or AttentionBody()
        if body.workspace:     # a named workspace answers to the ceiling first, like every resolver
            require_read_in_ceiling(request, body.workspace)
        refuse_delegated(request, reason="person_only", instruction=(
            "The attention home is read by the person's own app. Use whats_waiting for what is waiting."))
        with membership_mod.read_scope():
            cx = _context(request)
            _early_reads(cx["subject"], body.workspace)
            return _attention(request, body, cx,
                              _tiles(request, cx["subject"], cx["email"], cx["gdir"], cx["admin"]))

    def _attention(request: Request, body: AttentionBody, cx: dict, tiled: tuple) -> dict:
        """The attention answer from one ``_tiles`` read — the page passes the one it already made."""
        subject, email, gdir, now = cx["subject"], cx["email"], cx["gdir"], cx["now"]
        rows, shown, names, tiles, dirs = tiled
        slugs = [t["slug"] for t in tiles]
        openable = set(slugs)
        only = body.workspace
        if only is not None and only not in openable:
            return {"generated_at": int(now), "needs": [], "workspaces": {}}
        by_slug = {t["slug"]: t for t in tiles}
        on = features_mod.effective(gdir)
        me = [m for m in (email, subject) if m]
        raw = _raw_meetings(subject)
        upcoming = _upcoming_rows(raw, now)
        call_rows, homes, vexa = _calls(subject, email, by_slug, dirs, raw=raw, extra=tuple(r for _, r in upcoming))
        needs: list[dict] = []

        # proposals — the desk's short list
        if att.DESK in openable:
            try:
                needs += att.desk_proposal_needs(proposals_store.open_items(home_of(wsr, subject, for_read=True)))
            except Exception:  # noqa: BLE001 — a list that cannot be read costs its items
                pass
        srows = _store_rows(subject)
        _prefetch_targets((r.get("owner") or "", r.get("name")) for r in srows)
        _prefetch_humans(r.get("id") for r in srows)
        homes_by_chat = {r["id"]: _chat_home(r, subject, openable) for r in srows}
        # proposals after a call are NOT needs (founder ruling 2026-10-16): an ended meeting is not a
        # decision, and its post-call proposals are reviewed in that call's chat. A review item exists
        # only when an agent asks — the desk's short list above, or an inline card in a chat.
        if False and on.get("calls") and store is not None:  # noqa: SIM223 — kept for the ruling's revert
            prop_rows = []
            for r in srows[:PROPOSAL_SCAN]:
                try:
                    for p in store.proposals(r["id"]):
                        if p.get("owner") == subject and p.get("status") == "open":
                            try:
                                target = (sess.target(str(r.get("owner")), r["name"]) or "").strip()
                            except Exception:  # noqa: BLE001
                                target = ""
                            prop_rows.append({"proposal": p, "session": r["id"],
                                              "target": target if str(r.get("owner")) == subject or target in openable else ""})
                except Exception:  # noqa: BLE001
                    continue
            needs += att.session_proposal_needs(prop_rows, subject, openable)
        # mentions — unread, from chats the viewer can still read (feature `notifications`)
        if on.get("notifications") and notifier is not None and store is not None:
            try:
                notes = notifier.listing(subject, unread_only=True, limit=MENTIONS_MAX)["notifications"]
            except Exception:  # noqa: BLE001
                notes = []
            chats: dict[str, dict] = {}
            for n in notes:
                cid = str((n.get("object") or {}).get("id") or "")
                if n.get("kind") != "chat_mentioned" or not cid or cid in chats:
                    continue
                try:
                    row = store.get(cid)
                    readable = row is not None and store.can_read(cid, subject)
                except Exception:  # noqa: BLE001
                    row, readable = None, False
                if readable:
                    chats[cid] = {"readable": True, "session": _open_id(row, subject),
                                  "workspace": homes_by_chat.get(cid) or _chat_home(row, subject, openable)}
            needs += att.mention_needs(notes, chats, openable)
        # cards waiting on you (the terminal passed the crm desk's cards)
        if body.cards:
            needs += att.card_needs([c.model_dump() for c in body.cards],
                                    [subject, *sorted(_me_names(subject, email, gdir) - {subject})], openable)
        # upcoming calls
        items = []
        for start, r in upcoming:
            mid = str(r["id"])
            hs = sorted(homes.get(mid, ()))
            path = meeting_note.recorded_path(r)
            items.append({"meeting": mid, "name": display_mod.meeting_display_name(r, me=me), "start": start,
                          "homes": hs, "note": {"slug": hs[0], "path": path} if path and hs else None})
        needs += att.upcoming_needs(items, now, openable)
        # routines
        routines = _future(("desktop.routines", subject), lambda: _routine_rows(subject)).result()
        needs += att.failed_routine_needs(routines, subject, openable)
        # a proposed workspace image (a just-created workspace's identity) waits for its owner
        id_rows = []
        for t in tiles:
            d = dirs.get(t["slug"])
            if d is None or t.get("access") != "owner":
                continue
            try:
                prop = workspace_icon.proposal(d)
            except Exception:  # noqa: BLE001 — a proposal that cannot be read is no item
                prop = None
            if prop:
                id_rows.append({"slug": t["slug"], "name": t["name"], "owner": True, "proposal": prop})
        needs += att.identity_needs(id_rows, openable)

        # ── working here ──────────────────────────────────────────────────────────────────────
        working: dict[str, list] = {}
        for t in tiles:
            live = t.get("live_call")
            if live:
                w = {"kind": "call", "label": live.get("line") or "Call", "state": "live",
                     "when": att.when(live.get("since"), now), "at": live.get("since"),
                     "ref": f"meeting:{live['meeting']}", "meeting": live["meeting"]}
                if live.get("session"):
                    w["session"] = live["session"]
                working.setdefault(t["slug"], []).append(w)
        room_chats = []
        for r in srows:
            if now - float(r.get("last_active") or 0) > att.ROOM_ACTIVE_SEC and only is None:
                continue
            n = _humans(r["id"])
            if n > 1:
                room_chats.append({**r, "humans": n, "workspace": homes_by_chat.get(r["id"], att.DESK)})
        rooms = att.rooms_active(room_chats, now)
        for c in room_chats:
            if now - float(c.get("last_active") or 0) <= att.ROOM_ACTIVE_SEC:
                working.setdefault(c["workspace"], []).append({
                    "kind": "room", "label": str(c.get("title") or "").strip() or UNTITLED_CHAT, "state": "live",
                    "when": att.when(c.get("last_active"), now), "at": int(c.get("last_active") or 0) or None,
                    "ref": f"chat:{c['id']}", "session": _open_id(c, subject)})
        order = {"running": 0, "failed": 1, "next": 2, "ok": 3, "paused": 4}
        for r in sorted(routines, key=lambda r: (order.get(att.routine_state(r), 9), r["name"])):
            if r["where"] in openable:
                working.setdefault(r["where"], []).append(att.routine_work(r, now))
        last = {t["slug"]: (t.get("last_activity") or {}).get("line") for t in tiles}
        live = {t["slug"]: t.get("live_call") for t in tiles}

        page = None
        if only is not None:
            page = _page(subject, only, room_chats, routines, call_rows, homes, vexa, upcoming, now, me)
        return att.compose(now=now, slugs=slugs, needs=needs, working=working, rooms=rooms, live=live,
                           last=last, only=only, page=page)

    def _page(subject, slug, room_chats, routines, call_rows, homes, vexa, upcoming, now, me) -> dict:
        """The one-workspace lists the workspace page draws (rooms, your chats, routines, calls)."""
        rooms = sorted((c for c in room_chats if c["workspace"] == slug),
                       key=lambda c: float(c.get("last_active") or 0), reverse=True)[:att.PAGE_ROOMS_MAX]
        out_rooms = [{"session": _open_id(c, subject), "name": str(c.get("title") or "").strip() or UNTITLED_CHAT,
                      "members": int(c["humans"]), "last_active": int(c.get("last_active") or 0) or None,
                      "unread": _unread(str(c.get("id") or ""), subject), "closed": _closed(c)}
                     for c in rooms]
        shared_names = {c["name"] for c in room_chats if str(c.get("owner")) == subject}
        chats = []
        try:
            index = _index(subject)
        except Exception:  # noqa: BLE001
            index = []
        by_name: dict = {}
        if store is not None and getattr(store, "by_names", None) is not None:
            want = [str(c.get("session") or "") for c in index[:SESSION_SCAN]
                    if str(c.get("session") or "") and str(c.get("session") or "") not in shared_names
                    and att.home_slug(c.get("target"), subject) == slug]
            try:
                by_name = store.by_names(subject, want) if want else {}
                by_name = {n: by_name.get(n) for n in want}
                _prefetch_humans(r["id"] for r in by_name.values() if r is not None)
            except Exception:  # noqa: BLE001 — each chat is then looked up on its own, as before
                by_name = {}
        for c in index[:SESSION_SCAN]:
            name = str(c.get("session") or "")
            if not name or name in shared_names or att.home_slug(c.get("target"), subject) != slug:
                continue
            if store is not None:
                try:
                    row = by_name[name] if name in by_name else store.by_name(subject, name)
                    if row is not None and _humans(row["id"]) > 1:
                        continue
                except Exception:  # noqa: BLE001
                    pass
            title = str(c.get("named_title") or c.get("title") or "").strip()
            try:
                at = int(float(c.get("last_active") or 0)) or None
            except (TypeError, ValueError):
                at = None
            chats.append({"session": name, "name": title if title and title != name else UNTITLED_CHAT,
                          "last_active": at})
        chats.sort(key=lambda c: c["last_active"] or 0, reverse=True)
        out_routines = []
        history = (_future(("desktop.runs", subject), lambda: _run_history(subject)).result()
                   if any(r["where"] == slug for r in routines) else None)
        for r in routines:
            if r["where"] != slug:
                continue
            w = att.routine_work(r, now)
            last = r.get("last_run") or {}
            line = att.run_line(last or None)
            runs = att.run_history(history, r["id"]) if history is not None else []
            out_routines.append({"ref": w["ref"], "name": w["label"], "state": w["state"], "when": w["when"],
                                 "last_at": int(last.get("ended_at") or last.get("started_at") or 0) or None,
                                 "next_at": int(r["next_at"]) if r.get("next_at") else None,
                                 "session": w.get("session"),
                                 "runs": runs or ([line] if line else [])})
        calls = []
        seen = set()
        for s, call in vexa:
            if s == slug and call["meeting"] not in seen:
                seen.add(call["meeting"])
                calls.append({"meeting": call["meeting"], "name": str(call.get("line") or "").split(" · ")[0],
                              "at": int(call.get("since") or call.get("at") or 0) or None,
                              "state": "live" if "since" in call else "ended"})
        for row in call_rows:
            mid = str(row.get("id"))
            if slug in homes.get(mid, ()) and mid not in seen:
                state = _call_state(row)
                if state == "upcoming":
                    continue
                seen.add(mid)
                at = meeting_mint._epoch(row.get("end_time")) if state == "ended" else None
                at = at or meeting_mint._epoch(row.get("start_time"))
                calls.append({"meeting": mid, "name": display_mod.meeting_display_name(row, me=me),
                              "at": int(at) if at else None, "state": state})
        for start, row in upcoming:
            mid = str(row.get("id"))
            if slug in homes.get(mid, ()) and mid not in seen:
                seen.add(mid)
                calls.append({"meeting": mid, "name": display_mod.meeting_display_name(row, me=me),
                              "at": int(start), "state": "upcoming"})
        rank = {"live": 0, "upcoming": 1, "ended": 2}
        calls.sort(key=lambda c: (rank[c["state"]], (c["at"] or 0) if c["state"] == "upcoming" else -(c["at"] or 0)))
        return {"rooms": out_rooms, "chats": chats[:att.PAGE_CHATS_MAX], "routines": out_routines,
                "calls": calls[:att.PAGE_CALLS_MAX]}

    # ── refs: what a typed reference in a README is, for this viewer ─────────────────────────────

    @router.post("/api/desktop/refs")
    def desktop_refs(request: Request, body: RefsBody = Body(default=None)):
        """WHAT EACH TYPED REFERENCE IS, for this viewer: a room, a chat, a call, a file, a person, a
        card, a routine or a workspace. One the viewer may not see — or that does not exist, or is
        malformed — is exactly ``{kind, visible: false}``: nothing from inside it, not even whether it
        exists. Deterministic, no model call, nothing stored. Person only."""
        body = body or RefsBody()
        if body.workspace and body.workspace != att.DESK:
            require_read_in_ceiling(request, body.workspace)
        refuse_delegated(request, reason="person_only", instruction=(
            "References are resolved by the person's own app."))
        with membership_mod.read_scope():
            return _refs(request, body)

    def _refs(request: Request, body: RefsBody) -> dict:
        cx = _context(request)
        subject, email, gdir, now = cx["subject"], cx["email"], cx["gdir"], cx["now"]
        admin = cx["admin"]
        rows = [] if admin else _rows(subject)
        names = {r["slug"]: (r.get("display") or r["name"]) for r in rows}
        names[att.DESK] = PERSONAL
        ws_slugs = set() if admin else {r["slug"] for r in rows}
        if gdir.is_dir():
            ws_slugs.add(system_mounts.GLOBAL_SLUG)
            names[system_mounts.GLOBAL_SLUG] = COMPANY
        file_openable = ws_slugs | ({att.DESK, subject} if not admin else set())
        cards = [c.model_dump() for c in body.cards]
        cache: dict = {}

        def meetings() -> dict:
            if "meetings" not in cache:
                cache["meetings"] = {str(r.get("id")): r for r in _raw_meetings(subject) if r.get("id") is not None}
            return cache["meetings"]

        def routines() -> dict:
            if "routines" not in cache:
                cache["routines"] = {r["id"]: r for r in _routine_rows(subject)}
            return cache["routines"]

        def rosters() -> list[tuple[str, list]]:
            if "rosters" not in cache:
                out = []
                for r in rows:
                    if r.get("kind") != "shared" or _ACCESS.get(str(r.get("role") or ""), "reader") == "reader":
                        continue
                    try:
                        out.append((r["slug"], membership_mod.read_members(wsr.root, r["slug"])[:MEMBERS_MAX]))
                    except Exception:  # noqa: BLE001
                        continue
                cache["rosters"] = out
            return cache["rosters"]

        def resolve(ref: str) -> dict:
            kind, rid = att.parse_ref(ref)
            if kind is None:
                return att.invisible(None)
            gone = att.invisible(kind)
            if kind == "workspace":
                slug = att.DESK if rid in (subject, "desk") else rid
                if slug not in ws_slugs:
                    return gone
                out = {"kind": kind, "visible": True, "name": names.get(slug) or slug, "slug": slug}
                row = next((r for r in rows if r["slug"] == slug), None)
                if row is not None and row.get("kind") in ("personal", "private"):
                    out["members"] = 1
                elif row is not None and _ACCESS.get(str(row.get("role") or ""), "reader") != "reader":
                    roster = dict(rosters()).get(slug)
                    if roster is not None:
                        out["members"] = len(roster)
                return out
            if kind in ("room", "chat"):
                if store is None and not rid:
                    return gone
                row = None
                try:
                    if store is not None:
                        if rid.startswith("s-"):
                            got = store.get(rid)
                            row = got if got is not None and store.can_read(rid, subject) else None
                        else:
                            row = store.by_name(subject, rid)
                except Exception:  # noqa: BLE001
                    row = None
                meta = {}
                if row is None and not rid.startswith("s-"):
                    try:
                        meta = sess.meta(subject, rid) or {}
                    except Exception:  # noqa: BLE001
                        meta = {}
                if row is None and not meta:
                    return gone
                extra = {}
                if row is not None:
                    title = str(row.get("title") or "").strip()
                    session = _open_id(row, subject)
                    members = _humans(row["id"])
                    if kind == "room":
                        extra = {"unread": _unread(row["id"], subject), "closed": _closed(row)}
                else:
                    title = str(meta.get("named_title") or meta.get("title") or "").strip()
                    session, members = rid, 1
                if not title or title in (rid, session):
                    title = UNTITLED_CHAT
                return {"kind": kind, "visible": True, "name": att.clip(title, 200), "session": session,
                        "members": members, **extra}
            if kind == "call":
                row = meetings().get(rid)
                if row is None:
                    return gone
                state = _call_state(row)
                data = row.get("data") if isinstance(row.get("data"), dict) else {}
                at = att.epoch((data or {}).get("scheduled_at")) if state == "upcoming" else None
                at = at or meeting_mint._epoch(row.get("start_time"))
                sub = " · ".join(x for x in (_day(at) if at else "", str(row.get("status") or "")) if x)
                return {"kind": kind, "visible": True,
                        "name": display_mod.meeting_display_name(row, me=[m for m in (email, subject) if m]),
                        "sub": sub, "meeting": rid, "state": state}
            if kind == "file":
                first, _, rest = rid.partition("/")
                if rest and first in file_openable:
                    slug, path = first, rest
                else:
                    slug, path = (body.workspace or att.DESK), rid
                if slug == system_mounts.SYSTEM_SLUG or slug not in file_openable or not path.strip():
                    return gone
                own = slug in (att.DESK, "desk", subject)
                target = _target(request, None if own else slug)
                head = page_head(target, path) if target is not None else None
                if head is None:
                    return gone
                return {"kind": kind, "visible": True, "name": front_page.page_title(head, path), "path": path,
                        "slug": att.DESK if own else slug}
            if kind == "person":
                key = rid.lower()
                if key in {subject.lower(), (email or "").lower()}:
                    name = front_page.display_name(wsr.root, subject, email=email, global_dir=gdir) or subject
                    return {"kind": kind, "visible": True, "name": name, "initials": initials(name), "sub": "You"}
                shared, who = [], None
                for slug, roster in rosters():
                    for m in roster:
                        if key in {str(m.get("subject") or "").lower(), str(m.get("email") or "").lower()}:
                            who = who or m
                            shared.append(names.get(slug) or slug)
                            break
                if who is None:
                    return gone
                name = front_page.display_name(wsr.root, str(who.get("subject") or ""), email=who.get("email"),
                                               global_dir=gdir) or str(who.get("subject"))
                return {"kind": kind, "visible": True, "name": name, "initials": initials(name),
                        "sub": ", ".join(shared[:3])}
            if kind == "routine":
                rkey = rid if rid.startswith(routine_flows.FLOW_ID_PREFIX) else f"{routine_flows.FLOW_ID_PREFIX}{rid}"
                r = routines().get(rkey)
                if r is None:
                    return gone
                state = att.routine_state(r)
                last = r.get("last_run") or None
                line = att.run_line(last)
                bits = []
                if line:
                    bits.append(f"{line['what']} {att.when(line['at'], now)}" if line.get("at") else line["what"])
                if r.get("next_at") and state != "paused":
                    bits.append(f"next {att.when(r['next_at'], now)}")
                history = _run_history(subject)
                runs = att.run_history(history, rkey) if history is not None else []
                return {"kind": kind, "visible": True, "name": r["name"], "state": state,
                        "sub": " · ".join(bits) or None, "runs": runs or ([line] if line else [])}
            if kind == "card":
                return att.card_ref(rid, cards, ws_slugs | ({att.DESK} if not admin else set()))
            return gone

        out: dict[str, dict] = {}
        for ref in body.refs:
            if ref in out:
                continue
            try:
                out[ref] = resolve(ref)
            except Exception:  # noqa: BLE001 — a ref that cannot be resolved is not visible
                out[ref] = att.invisible(att.parse_ref(ref)[0])
        return {"refs": out}

    # ── the page: a README rendered for this viewer (PRD 0.13.4 D17, UI 7aa) ─────────────────────

    def _changes_since(target: Optional[Path], since: float, now: float) -> list[dict]:
        """People-facing commits since ``since`` (plumbing and the system's own commits left out)."""
        if target is None or not (target / ".git").exists():
            return []
        try:
            from shared.gitexec import run_git  # noqa: PLC0415
            out = run_git(target, "log", f"--since=@{int(since)}", "-n", "40", "--format=%H%x1f%an%x1f%at%x1f%s")
            text = out.stdout if hasattr(out, "stdout") else str(out or "")
        except Exception:  # noqa: BLE001 — a history that cannot be read is no list
            return []
        rows = []
        for line in str(text or "").splitlines():
            parts = line.split("\x1f")
            if len(parts) != 4:
                continue
            sha, author, at, msg = parts
            if author in ("vexa-terminal",) and not msg.startswith("edit "):
                continue
            if _PLUMBING.match(msg):
                continue
            edit = _EDIT.match(msg)
            who = "Someone" if author == "vexa-terminal" else author
            # activity reads as people doing things (founder 2026-10-17)
            title = f"{who} edited {Path(edit.group(1)).stem}" if edit else f"{who}: {msg}"
            rows.append({"key": sha[:12], "title": att.clip(title, 200), "by": "" if author == "vexa-terminal" else author,
                         "at": int(at) if at.isdigit() else None})
        return rows[:20]

    def _page_files(target: Optional[Path]) -> list[dict]:
        if target is None:
            return []
        try:
            tree = wsr.tree_at(target)
        except Exception:  # noqa: BLE001
            return []
        pages = [f for f in tree if f.lower().endswith(".md") and not f.startswith(".") and not _SCAFFOLD.match(f)
                 and f.lower() != "readme.md"]
        out = []
        for f in pages[:200]:
            head = page_head(target, f)
            out.append({"key": f, "title": front_page.page_title(head, f) if head is not None else Path(f).stem,
                        "ref": f"file:{f}"})
        return out

    @router.post("/api/desktop/page")
    def desktop_page(request: Request, body: PageBody = Body(default=None)):
        """A PAGE RENDERED FOR YOU (the workspace page is its README, UI 7aa). The page's source holds
        placeholders only; each live block and reference is resolved with YOUR access: what you may not
        see is absent (never counted), what only you may see is marked personal, actions are
        descriptors that never run here. Deterministic, no model call, nothing stored. Person only."""
        body = body or PageBody()
        slug = body.workspace or att.DESK
        if slug != att.DESK:
            require_read_in_ceiling(request, slug)
        refuse_delegated(request, reason="person_only", instruction=(
            "A page is rendered for the person's own app. Read the page's source with workspace_read."))
        rel = body.path.strip().lstrip("/")
        if not rel or ".." in rel.split("/") or not rel.lower().endswith(".md"):
            raise HTTPException(status_code=400, detail="path must be a page (.md) inside the workspace")
        with membership_mod.read_scope():
            return _render_page(request, body, slug, rel)

    def _render_page(request: Request, body: PageBody, slug: str, rel: str) -> dict:
        cx = _context(request)
        subject, email, gdir, now = cx["subject"], cx["email"], cx["gdir"], cx["now"]
        _early_reads(subject, slug)
        tiled = _tiles(request, subject, email, gdir, cx["admin"])
        tiles = tiled[3]
        tile = next((t for t in tiles if t["slug"] == slug), None)
        if tile is None:
            raise HTTPException(status_code=404, detail="no such workspace among yours")
        # ONE `_tiles` read for the page and its attention entry (it was read twice per page); the
        # attention route's own ceiling check still runs, as it did when the page called the route
        require_read_in_ceiling(request, slug)
        attention = _attention(request, AttentionBody(cards=body.cards, workspace=slug), cx, tiled)
        entry = (attention.get("workspaces") or {}).get(slug) or {}
        target = _target(request, None if slug == att.DESK else slug)
        source = None
        if target is not None:
            try:
                source = wpaths.read_text_inside(target, rel, max_bytes=400_000)
            except Exception:  # noqa: BLE001
                source = None
        if source is None:
            if rel.lower() != "readme.md":
                raise HTTPException(status_code=404, detail="no such page")
            source = page_render_mod.default_readme(tile.get("name") or slug)
        source = _strip_front_matter(source)

        lists: dict = {}
        lists["waiting-on-you"] = [{"key": n.get("id"), "title": n.get("text"), "state": n.get("kind"),
                                    "act": n.get("act")} for n in entry.get("needs") or []]
        lists["rooms"] = [{"key": r["session"], "title": r["name"],
                           "detail": f"{r['members']} {'member' if r['members'] == 1 else 'members'}",
                           "at": r.get("last_active"), "state": "closed" if r.get("closed") else None,
                           "act": {"kind": "open-session", "session": r["session"]}}
                          for r in entry.get("rooms") or []]
        lists["routines"] = [{"key": r["ref"], "title": r["name"], "state": r.get("state"), "detail": r.get("when"),
                              "at": r.get("last_at"),
                              **({"act": {"kind": "open-session", "session": r["session"]}} if r.get("session") else {})}
                             for r in entry.get("routines") or []]
        lists["meetings"] = [{"key": str(c["meeting"]), "title": c.get("name") or "Meeting", "state": c.get("state"),
                              "at": c.get("at"), "ref": f"meeting:{c['meeting']}",
                              "act": {"kind": "open-meeting", "meeting": str(c["meeting"])}}
                             for c in entry.get("calls") or []]
        # THE DIRECTORY RULE (founder 2026-10-17, supersedes "no roster for readers"): everyone who
        # shares a workspace with you is discoverable, like Slack — readers included, with profile
        # basics and roles only. Pending invites and requests (approver-only) are never in this list.
        people_tile = tile
        if tile.get("kind") == "shared" and not tile.get("members"):
            try:
                roster = membership_mod.read_members(wsr.root, slug)[:MEMBERS_MAX]
            except Exception:  # noqa: BLE001 — a roster that cannot be read names nobody
                roster = []
            people_tile = {**tile, "members": [_member(str(m.get("subject")), m.get("email"), str(m.get("role") or ""), gdir)
                                               for m in roster if m.get("subject")]}
        lists["people"] = _people_rows(people_tile, [c.model_dump() for c in body.cards], subject)
        lists["files"] = _page_files(target)

        ws_rec = workspace_registry.by_slug(slug) if slug != att.DESK else None
        page_ws = (ws_rec or {}).get("id") or slug
        cards = [c.model_dump() for c in body.cards]
        seams = refs_router.seams_for(request, subject, d, cards=cards, page_workspace=page_ws)

        def resolve_many(refs) -> dict:
            out = {}
            for r in refs:
                try:
                    out[r.key] = universal_refs_mod.resolve(page_grammar_mod.ref_chip(r), seams)
                except Exception:  # noqa: BLE001 — an unresolvable ref names nothing
                    out[r.key] = {"access": "gone"}
            return out

        pins = workspace_pins.read(target) if target is not None else []
        pin_rows = []
        for p in pins:
            u = links_mod.parse_universal(p["ref"])
            if u is None:
                continue
            a = universal_refs_mod.resolve_ref(u, seams)
            row = {"key": p["ref"], "by": p.get("by") or "", "at": p.get("at")}
            if a.get("access") in ("readable", "grant"):
                row.update({"title": a.get("label") or "Untitled", "ref": p["ref"], "url": a.get("url")})
            else:
                row["restricted"] = True
            pin_rows.append(row)
        lists["pinned"] = pin_rows
        since = body.since if body.since and body.since < now else now - CHANGES_WINDOW_SEC
        changes = _changes_since(target, since, now)
        changes += [{"key": f"pin:{r['key']}", "title": f"{_who_name(tile, r.get('by'))} pinned {r.get('title') or 'an item'}", "by": r.get("by"),
                     "at": r.get("at")} for r in pin_rows if not r.get("restricted") and (r.get("at") or 0) >= since]
        lists["changes-since"] = sorted(changes, key=lambda r: -(r.get("at") or 0))

        viewer = page_render_mod.Viewer(subject=subject, workspace=slug,
                                        role=tile.get("access") if tile.get("access") in ("reader", "contributor", "owner") else "reader",
                                        shared=tile.get("kind") == "shared")
        me = {m.lower() for m in _me_names(subject, email, gdir)}
        card_blocks = {}
        for k, rows in body.card_blocks.items():
            if rows is not None and k.startswith("waiting-on-others"):
                rows = [r for r in rows if str((r or {}).get("who") or "").lower() not in me]
            card_blocks[k] = rows
        sources = page_render_mod.Sources(refs=resolve_many, lists=lists, crm=dict(body.crm), cards=card_blocks)
        tree = page_render_mod.render(source, viewer, sources, path=rel)
        tree["title"] = tile.get("name") or slug
        tree["role"] = viewer.role
        return tree


    return router
