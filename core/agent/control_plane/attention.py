"""attention.py — THE RULES of the attention home: what needs you, what is working, how it ranks
(home R3, architecture decision E1, Vexa-ai/vexa#1783).

A READ MODEL WITH NO STORE AND NO MODEL CALL. `routers/desktop.py` reads each fact from the ONE owner
that already holds it and hands the plain rows to the functions here; these decide nothing about I/O
and touch nothing, so every rule is provable offline (`tests/test_attention.py`). Each item carries
its act as a POINTER to the owner's route — the click goes to the owner, never through here.

NEEDS YOU, by kind, each from its one owner:

  proposal  · the desk's short list (`shared/proposals.py`, open rows), accepted with
              `POST /api/proposals/resolve {"id": <proposal>, "status": "ran"}` — act
              `{kind: accept-proposal, source: "desk", proposal}`, label Accept;
            · a post-call proposal (`agent_session_proposal`) whose OWNER is the viewer, still open,
              in a chat the viewer can read, reviewed with
              `POST /api/sessions/{session}/proposals/{proposal}/accept|reject` (feature `calls`) — act
              `{kind: accept-proposal, source: "call", proposal, session}`, label Review. It lives in
              its own workspace, else where its chat works.
            (A write-target proposal, `propose_write_target`, is a stream event in the chat it was
            made in and is stored nowhere, so it is not a need here: the chat itself shows it.)
  mention   an UNREAD `chat_mentioned` notification (feature `notifications`) from a chat the viewer
            can STILL read (`store.can_read`) — act `{kind: open-session, session, notification}`; the
            terminal marks it read with `POST /api/notifications/read {"ids": [notification]}`.
  card      a crm-api card the terminal passed in (`cards`, the desk read's cards) that WAITS ON YOU:
            crm encodes that in one free-text field, `waiting` ("who or what it waits on"), so a card
            waits on you when you are one of its members (member or invited) AND its `waiting` names
            you — your subject, your email, your email's local part or your display name, matched
            whole, case-insensitively. It lives in the first of its workspaces you can open; a card
            whose workspaces are all closed to you is DROPPED (the body never widens access); no
            workspace is the desk. Act `{kind: open-card, card}`.
  upcoming  a meeting in the viewer's own meetings list starting within `UPCOMING_SEC`. Act `prepare`
            (its note page, `{path, slug}`) when it has one, `open-meeting` otherwise; `Join` when it
            starts within `JOIN_SOON_SEC`.
  failed    one of the viewer's OWN routines (flows, `scope == user:<viewer>`) that is active and whose
            last run failed, was refused or was stopped. Act `{kind: retry-routine, routine,
            reaction}`: flows-api `POST /reactions/{reaction}/retry` as the person (its last run's id).

RANKING (deterministic): failed routines and proposals first, then mentions, then cards waiting on
you, then upcoming calls (sooner first); within a rank, newest first; then by id.

ACCESS IS THE CORE PROPERTY. Every rule takes `openable` — the slugs the viewer can open, the same
slugs `/api/desktop/sections` lists — and an item homed anywhere else is dropped, never renamed to a
place the viewer can see. A workspace outside `openable` never appears as a key, and no text names it.
"""
from __future__ import annotations

import datetime
import re
import time
from typing import Iterable, Mapping, Optional

DESK = "personal"
TEXT_MAX = 120
NEEDS_MAX = 30
WS_NEEDS_MAX = 10
WORKING_MAX = 8
#: A room is "active" when a message landed in it within this window (2 hours).
ROOM_ACTIVE_SEC = 2 * 3600
#: A call is "upcoming" when it starts within this window (24 hours).
UPCOMING_SEC = 24 * 3600
#: …and offers Join rather than Prepare inside this one.
JOIN_SOON_SEC = 10 * 60
#: The per-workspace page's lists (one workspace asked for).
PAGE_ROOMS_MAX = 20
PAGE_CHATS_MAX = 20
PAGE_CALLS_MAX = 10
RUNS_MAX = 12
#: How many of the person's newest runs are read to build the histories (flows `GET /flows/runs`).
RUNS_SCAN = 200

RANK = {"failed": 0, "proposal": 0, "mention": 1, "card": 2, "upcoming": 3}
FAILED_RUNS = ("failed", "refused", "blocked")
RUNNING_RUNS = ("running", "retrying", "pending")
REF_KINDS = ("room", "chat", "call", "file", "person", "card", "routine", "workspace")


# ── small pieces ───────────────────────────────────────────────────────────────────────────────

def home_slug(target: Optional[str], subject: str) -> str:
    """Where a chat works, as a tile slug: no target (or the person's own) is the desk."""
    t = str(target or "").strip()
    return DESK if t in ("", str(subject), "desk", DESK) else t


def clip(text, n: int = TEXT_MAX) -> str:
    """One human line: whitespace folded, at most ``n`` characters."""
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def epoch(value) -> Optional[float]:
    """An epoch from a number or an ISO-8601 string; None for anything else."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        s = str(value).strip().replace("Z", "+00:00")
        d = datetime.datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=datetime.timezone.utc)
        return d.timestamp()
    except ValueError:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


def _day(ts: float) -> str:
    g = time.gmtime(ts)
    return f"{time.strftime('%b', g)} {g.tm_mday}"


def when(at: Optional[float], now: float) -> Optional[str]:
    """A short human time: `now`, `5m ago`, `3h ago`, `in 30m`, `in 3h`, else the day."""
    if at is None:
        return None
    d = float(at) - float(now)
    a = abs(d)
    if a < 60:
        return "now"
    if a < 3600:
        span = f"{int(a // 60)}m"
    elif a < 86400:
        span = f"{int(a // 3600)}h"
    else:
        return _day(at)
    return f"in {span}" if d > 0 else f"{span} ago"


def need(kind: str, nid: str, workspace: str, text: str, at, act: dict) -> dict:
    return {"id": f"{kind}:{nid}", "kind": kind, "workspace": workspace, "text": clip(text),
            "at": int(at or 0), "act": act}


def rank(needs: Iterable[dict]) -> list[dict]:
    """Failed and proposals, mentions, cards, upcoming (sooner first); ties newest first, then id."""
    def key(n):
        r = RANK.get(n.get("kind"), 9)
        at = int(n.get("at") or 0)
        return (r, at if n.get("kind") == "upcoming" else -at, str(n.get("id")), str(n.get("workspace")))
    return sorted(needs, key=key)


# ── needs you ──────────────────────────────────────────────────────────────────────────────────

def desk_proposal_needs(items: Iterable[dict]) -> list[dict]:
    """The desk's open short-list rows (`shared/proposals.open_items`)."""
    out = []
    for it in items or []:
        if not isinstance(it, dict) or it.get("status", "open") != "open" or not it.get("id"):
            continue
        out.append(need("proposal", str(it["id"]), DESK, it.get("act") or "", epoch(it.get("since")),
                        {"label": "Accept", "kind": "accept-proposal", "proposal": str(it["id"]),
                         "source": "desk"}))
    return out


def identity_needs(rows: Iterable[dict], openable) -> list[dict]:
    """A proposed workspace image (`.vexa/` proposal, `workspace_icon.proposal`) waiting for its
    OWNER — a just-created workspace's identity is accepted on its card. Only the owner sees it; a
    workspace outside `openable` never appears. Act: `POST /api/workspaces/{slug}/identity/proposal`
    `{"accept": true|false}` (`source: identity`)."""
    out = []
    for r in rows or []:
        slug, prop = str(r.get("slug") or ""), r.get("proposal")
        if not prop or not r.get("owner") or slug not in openable:
            continue
        what = "a mark" if str(prop.get("source") or "") in ("", "designed", "uploaded") else "its logo"
        out.append(need("proposal", f"identity-{slug}", slug, f"Vexa proposes {what} for {r.get('name') or slug}",
                        prop.get("at"), {"label": "Use", "kind": "accept-proposal", "source": "identity",
                                         "proposal": f"identity-{slug}", "slug": slug}))
    return out


def session_proposal_needs(rows: Iterable[dict], subject: str, openable) -> list[dict]:
    """Post-call proposals: ``rows`` are ``{proposal, session, target}`` for chats the viewer reads;
    only open ones the viewer owns, homed in a workspace the viewer can open."""
    out = []
    for r in rows or []:
        p = (r or {}).get("proposal") or {}
        if p.get("owner") != subject or p.get("status") != "open" or not p.get("id"):
            continue
        ws = home_slug(p.get("workspace"), subject) if p.get("workspace") else home_slug(r.get("target"), subject)
        if ws not in openable:
            continue
        what = p.get("title") or p.get("path") or f"A {p.get('kind') or 'change'} from a call"
        out.append(need("proposal", str(p["id"]), ws, f"Review: {what}", p.get("created"),
                        {"label": "Review", "kind": "accept-proposal", "proposal": str(p["id"]),
                         "session": str(r.get("session") or p.get("session_id")), "source": "call"}))
    return out


def mention_needs(notes: Iterable[dict], chats: Mapping[str, dict], openable) -> list[dict]:
    """Unread ``chat_mentioned`` rows; ``chats`` maps a chat id to ``{readable, session, workspace}``
    — a chat missing from it, or no longer readable, drops its mention."""
    out = []
    for n in notes or []:
        if n.get("kind") != "chat_mentioned" or n.get("read"):
            continue
        obj = n.get("object") or {}
        chat = chats.get(str(obj.get("id") or ""))
        if not chat or not chat.get("readable") or chat.get("workspace") not in openable:
            continue
        out.append(need("mention", str(n["id"]), chat["workspace"], n.get("text") or "You were mentioned",
                        n.get("created"), {"label": "Open", "kind": "open-session",
                                           "session": str(chat["session"]), "notification": str(n["id"])}))
    return out


def _norm(s) -> str:
    return " ".join(str(s or "").lower().split())


def waits_on(card: dict, me: Iterable[str]) -> bool:
    """crm's `waiting` names the viewer (whole-token match on any of their identifiers), and the
    viewer is on the card."""
    names = {_norm(m) for m in me if _norm(m)}
    member = any(str(m.get("subject")) in names or _norm(m.get("subject")) in names
                 for m in card.get("members") or [] if isinstance(m, dict))
    if not member:
        return False
    w = _norm(card.get("waiting"))
    if not w:
        return False
    for name in names:
        if re.search(r"(^|[^\w@.])" + re.escape(name) + r"($|[^\w@])", w):
            return True
    return False


def card_home(card: dict, openable) -> Optional[str]:
    """The workspace a card lives in for this viewer: the first of its workspaces they can open; the
    desk when it names none; None (drop it) when every one it names is closed to them."""
    named = [str(w) for w in card.get("workspaces") or [] if str(w or "").strip()]
    if not named:
        return DESK
    for w in named:
        if w in openable:
            return w
    return None


def card_needs(cards: Iterable[dict], me: Iterable[str], openable) -> list[dict]:
    me = list(me)
    out = []
    for c in cards or []:
        if not isinstance(c, dict) or not c.get("id") or not waits_on(c, me):
            continue
        ws = card_home(c, openable)
        if ws is None:
            continue
        text = f"{c.get('title') or 'A card'} is waiting on you"
        if c.get("next"):
            text += f": {c['next']}"
        out.append(need("card", str(c["id"]), ws, text, epoch(c.get("at")) or 0,
                        {"label": "Open", "kind": "open-card", "card": str(c["id"])}))
    return out


def upcoming_needs(items: Iterable[dict], now: float, openable) -> list[dict]:
    """``items``: ``{meeting, name, start, homes, note}`` from the viewer's own meetings list; one need
    per openable home (the desk when none), deduplicated in the global list by `compose`."""
    out = []
    for u in items or []:
        start = u.get("start")
        if start is None or not (now < float(start) <= now + UPCOMING_SEC):
            continue
        homes = [h for h in u.get("homes") or [] if h in openable] or [DESK]
        note = u.get("note") if isinstance(u.get("note"), dict) else None
        soon = float(start) - now <= JOIN_SOON_SEC
        for ws in homes:
            if soon:
                act = {"label": "Join", "kind": "open-meeting", "meeting": str(u["meeting"])}
            elif note and note.get("slug") in openable and note.get("path"):
                act = {"label": "Prepare", "kind": "prepare", "path": note["path"], "slug": note["slug"],
                       "meeting": str(u["meeting"])}
            else:
                act = {"label": "Prepare", "kind": "open-meeting", "meeting": str(u["meeting"])}
            out.append(need("upcoming", str(u["meeting"]), ws,
                            f"{u.get('name') or 'A call'} · {when(start, now)}", start, act))
    return out


def failed_routine_needs(routines: Iterable[dict], subject: str, openable) -> list[dict]:
    """``routines``: ``{id, name, status, last_run, where, owner}`` — the viewer's own only."""
    out = []
    for r in routines or []:
        if r.get("owner") != subject or r.get("status") != "active":
            continue
        last = r.get("last_run") or {}
        if last.get("status") not in FAILED_RUNS or r.get("where") not in openable:
            continue
        reason = clip(last.get("reason") or "", 80)
        word = {"failed": "failed", "refused": "was refused", "blocked": "was stopped"}[last["status"]]
        text = f"{r.get('name') or 'A routine'} {word}" + (f": {reason}" if reason else "")
        act = {"label": "Retry", "kind": "retry-routine", "routine": str(r["id"])}
        if last.get("id"):
            act["reaction"] = str(last["id"])
        out.append(need("failed", str(r["id"]), r["where"], text,
                        last.get("ended_at") or last.get("started_at"), act))
    return out


# ── working here ───────────────────────────────────────────────────────────────────────────────

def routine_state(r: dict) -> str:
    if r.get("status") != "active":
        return "paused"
    last = (r.get("last_run") or {}).get("status")
    if last in RUNNING_RUNS:
        return "running"
    if last in FAILED_RUNS:
        return "failed"
    if last:
        return "ok"
    return "next" if r.get("next_at") else "ok"


def routine_work(r: dict, now: float) -> dict:
    last = r.get("last_run") or {}
    state = routine_state(r)
    last_at = last.get("ended_at") or last.get("started_at")
    if state == "next":
        at = r.get("next_at")
    elif state == "paused":
        at = None
    elif state == "running":
        at = last.get("started_at") or now
    else:
        at = last_at
    w = {"kind": "routine", "label": str(r.get("name") or r.get("id")), "state": state,
         "when": "now" if state == "running" else when(at, now), "at": int(at) if at else None,
         "ref": f"agent:{r['id']}"}
    if r.get("thread"):
        w["session"] = str(r["thread"])
    return w


def run_line(last: Optional[dict]) -> Optional[dict]:
    """A last run as one history row ``{at, ok, what}``."""
    if not last:
        return None
    status = str(last.get("status") or "")
    at = last.get("ended_at") or last.get("started_at")
    what = {"done": "Ran", "failed": "Failed", "refused": "Refused", "blocked": "Stopped"}.get(
        status, "Running" if status in RUNNING_RUNS else (status.capitalize() or "Ran"))
    reason = clip(last.get("reason") or "", 80)
    return {"at": int(at) if at else None, "ok": status not in FAILED_RUNS,
            "what": f"{what}: {reason}" if reason and status in FAILED_RUNS else what}


def run_history(runs: Optional[Iterable[dict]], routine_id: str, limit: int = RUNS_MAX) -> list[dict]:
    """One routine's runs (flows' `/flows/runs` rows, the person's own) as history rows, newest
    first: ``{at, ok, what}``. ``routine_id`` is ``flow:<name>``; another flow's runs never mix in."""
    name = routine_id.split(":", 1)[1] if routine_id.startswith("flow:") else routine_id
    out = []
    for r in runs or ():
        if str(r.get("flow") or "") != name:
            continue
        status = str(r.get("status") or "")
        at = r.get("updated_at") if status not in RUNNING_RUNS else r.get("started_at")
        line = run_line({"status": status, "reason": r.get("reason"),
                         "ended_at": at if status not in RUNNING_RUNS else None, "started_at": r.get("started_at")})
        if line:
            out.append(line)
    out.sort(key=lambda x: x.get("at") or 0, reverse=True)
    return out[:limit]


def rooms_active(chats: Iterable[dict], now: float) -> dict[str, int]:
    """Shared chats (more than one human) active within `ROOM_ACTIVE_SEC`, counted per workspace."""
    out: dict[str, int] = {}
    for c in chats or []:
        if int(c.get("humans") or 0) > 1 and now - float(c.get("last_active") or 0) <= ROOM_ACTIVE_SEC:
            out[c["workspace"]] = out.get(c["workspace"], 0) + 1
    return out


# ── the answer ─────────────────────────────────────────────────────────────────────────────────

def compose(*, now: float, slugs: list[str], needs: list[dict], working: Mapping[str, list],
            rooms: Mapping[str, int], live: Mapping[str, Optional[dict]], last: Mapping[str, Optional[str]],
            only: Optional[str] = None, page: Optional[dict] = None) -> dict:
    """The response. Keys are exactly ``slugs`` (or ``only`` when it is one of them, else none);
    anything homed elsewhere is dropped. ``page`` adds the one-workspace lists to ``only``'s entry."""
    keys = [s for s in slugs if only is None or s == only]
    keyset = set(keys)
    kept = rank(n for n in needs if n.get("workspace") in keyset)
    seen: set = set()
    flat = []
    for n in kept:
        if n["id"] in seen:
            continue
        seen.add(n["id"])
        flat.append(n)
    out = {"generated_at": int(now), "needs": flat[:NEEDS_MAX], "workspaces": {}}
    for s in keys:
        entry = {"needs": [n for n in kept if n["workspace"] == s][:WS_NEEDS_MAX],
                 "working": list(working.get(s) or [])[:WORKING_MAX],
                 "rooms_active": int(rooms.get(s) or 0),
                 "live_call": live.get(s), "last": last.get(s)}
        if page is not None and s == only:
            entry.update(page)
        out["workspaces"][s] = entry
    return out


# ── refs ───────────────────────────────────────────────────────────────────────────────────────

def invisible(kind: Optional[str]) -> dict:
    """The one shape of a ref the viewer may not see — or one that does not exist, or is malformed:
    the two are indistinguishable on purpose."""
    return {"kind": kind if kind in REF_KINDS else "unknown", "visible": False}


def parse_ref(ref: str) -> tuple[Optional[str], Optional[str]]:
    kind, sep, rid = str(ref or "").partition(":")
    rid = rid.strip()
    if not sep or kind not in REF_KINDS or not rid:
        return None, None
    return kind, rid


def card_ref(cid: str, cards: Iterable[dict], openable) -> dict:
    c = next((c for c in cards or [] if isinstance(c, dict) and str(c.get("id")) == cid), None)
    if c is None or card_home(c, openable) is None:
        return invisible("card")
    sub = " · ".join(x for x in (f"Waiting on {c['waiting']}" if c.get("waiting") else "",
                                 f"Next: {c['next']}" if c.get("next") else "") if x)
    return {"kind": "card", "visible": True, "name": clip(c.get("title") or "Card"),
            "state": str(c.get("state") or ""), "sub": clip(sub) if sub else None}
