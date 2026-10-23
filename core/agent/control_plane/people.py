"""people.py — follow-ups and discovery of people, for the home and for the agent (#1783).

PURE: every source arrives as plain rows (``routers/people.py`` reads them) and this turns them into
ITEMS — one per person and reason, each with its evidence and its actions. The agent's tools
(``people_followups``, ``people_discover``) answer the items; the new-chat home gets the same items as
candidates (``candidates``, through ``home_sources.register_source``) and ranks them into its one
stream. There is no separate people screen: people are where a chat starts (founder, 2026-10-13).

WHAT A PERSON MAY SEE. Nothing here widens it:

* colleagues are the people the viewer shares a group workspace with (``people_directory``) — the
  organisation's whole user list is never read, so another organisation's users cannot appear;
* a colleague whose roster entry says ``"discoverable": false`` is never SUGGESTED (they stay
  reachable where the viewer already works with them);
* external people come only from the viewer's own meetings (the invitation's attendees) and the
  viewer's own queue; whether an external address is a Vexa user is never looked up;
* texts name people, meetings and chats by title — never a message, a page or a transcript line.

Ranking is deterministic (no model): a follow-up's score is how overdue it is against the person's
own cadence, a discovery's score is how much the two of you already share.
"""
from __future__ import annotations

import re
import statistics
from datetime import datetime, timezone
from typing import Iterable, Optional

DAY = 86400.0
#: An unanswered direct message counts as owed after this long.
UNANSWERED_AFTER_S = 4 * 3600
#: A meeting that ended within this long, with nothing recorded after it, is a promised follow-up.
FOLLOW_UP_WINDOW_S = 14 * DAY
#: Going cold: at least this many contacts to know a cadence; overdue past max(2 × usual gap, 14 d);
#: older than 120 d is not cold, it is over.
COLD_MIN_CONTACTS = 3
COLD_FLOOR_S = 14 * DAY
COLD_CEILING_S = 120 * DAY
#: A frequent external attendee: in at least this many of your meetings.
FREQUENT_MEETINGS = 2
LIST_MAX = 20
INTROS_MAX = 3

_ID = re.compile(r"^[A-Za-z0-9._:@+-]{1,120}$")
_EMAIL = re.compile(r"^[^\s@<>]{1,64}@[^\s@<>]{1,190}$")
_CTRL = re.compile(r"[\x00-\x1f\x7f]+")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]{3,}")
_STOP = {"meeting", "call", "sync", "weekly", "daily", "with", "intro", "chat", "catch", "check", "update",
         "team", "standup", "review", "notes", "voice", "note", "google", "teams", "zoom", "meet"}


_TEST_ACCOUNT = re.compile(r"^(?:tester|test|qa|demo)(?:[-_.]\w+|\d+)?$|@(?:[\w-]+\.)*(?:test|example\.(?:com|org|net))$|\.test$", re.I)


def is_test_account(name, email=None) -> bool:
    """tester-b, qa-1, someone@x.test, anyone@example.com: a test account, never suggested to a person."""
    for v in (name, email):
        v = str(v or "").strip()
        if v and (_TEST_ACCOUNT.search(v) or _TEST_ACCOUNT.search(v.split("@", 1)[0])):
            return True
    return False


def clip(v, n: int) -> str:
    if isinstance(v, bool) or not isinstance(v, (str, int, float)):
        return ""
    s = re.sub(r"\s+", " ", _CTRL.sub(" ", str(v))).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def ts(v) -> Optional[float]:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if not isinstance(v, str) or not v:
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    except ValueError:
        return None


def ago(seconds: float) -> str:
    s = max(0.0, seconds)
    if s < 3600:
        return f"{max(1, int(s // 60))} min ago"
    if s < DAY:
        return f"{int(s // 3600)} h ago"
    return f"{int(s // DAY)}d ago"


def slug(s: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", str(s or "").lower()).strip("-")
    return out[:60] or "x"


def email_of(v) -> str:
    return v.strip().lower() if isinstance(v, str) and _EMAIL.match(v.strip()) else ""


def _hslug(s: str) -> str:
    """The home's own key spelling (``home_sources._slug``), so a colleague is ONE person on the home."""
    out = "".join(ch if ch.isalnum() else "-" for ch in str(s))[:60].strip("-")
    return out or "x"


def key_for(*, account: str = "", email: str = "", name: str = "") -> str:
    """One stable key per person: the account for a colleague, else the address, else the name."""
    if account:
        return f"p-{_hslug(account)}"[:120]
    if email:
        return f"p-{_hslug(email)}"[:120]
    return f"p-n-{_hslug(name)}"[:120]


def initials(name: str) -> str:
    return "".join(w[:1] for w in str(name).split()[:2]).upper() or "?"


def _domain(email: str) -> str:
    return email.split("@", 1)[1] if "@" in email else ""


# ── the directory: who is who, from what the viewer may see ─────────────────────────────────────

class Directory:
    """Everyone the sources name, merged by account and by address. ``me``/``my_email`` are never in it."""

    def __init__(self, me: str, my_email: str, colleagues: Iterable[dict], pages: Iterable[dict] = ()) -> None:
        self.me, self.my_email = str(me), email_of(my_email)
        self.people: dict = {}
        self._by_email: dict = {}
        self._by_account: dict = {}
        self._pages = list(pages or [])
        for c in colleagues or []:
            sub = str(c.get("subject") or "")
            if not sub or sub == self.me:
                continue
            p = self._add(key_for(account=sub), name=c.get("name") or c.get("email") or sub, email=email_of(c.get("email")))
            p.update(account_id=sub if _ID.match(sub) else None, vexa=True, colleague=True,
                     workspaces=[str(w) for w in c.get("workspaces") or []][:8],
                     discoverable=c.get("discoverable") is not False)
            self._by_account[sub] = p["key"]
            # D35: one Person, several logins — the other accounts the viewer already knew map here
            for other in c.get("linked") or []:
                if str(other) and str(other) != self.me:
                    self._by_account[str(other)] = p["key"]
            if int(c.get("accounts") or 1) > 1:
                p["accounts"] = int(c["accounts"])
                p["accounts_confirmed"] = bool(c.get("accounts_confirmed"))

    def _add(self, key: str, *, name: str, email: str = "") -> dict:
        p = self.people.get(key)
        if p is None:
            p = {"key": key, "name": clip(name, 60) or email or "Someone", "email": email, "vexa": False,
                 "colleague": False, "account_id": None, "workspaces": [], "discoverable": True,
                 "company": "", "role": "", "page": "", "contacts": []}
            self.people[key] = p
        if email:
            self._by_email.setdefault(email, key)
        return p

    def colleague(self, account: str) -> Optional[dict]:
        k = self._by_account.get(str(account))
        return self.people.get(k) if k else None

    def attendee(self, a: dict) -> Optional[dict]:
        """A meeting attendee → their person (a colleague when the address is one), or None for the viewer."""
        if not isinstance(a, dict):
            return None
        email, name = email_of(a.get("email")), clip(a.get("name") or a.get("displayName"), 60)
        if email and email == self.my_email:
            return None
        if email and email in self._by_email:
            return self.people[self._by_email[email]]
        if not email and not name:
            return None
        if not name or "@" in name:
            name = (email.split("@", 1)[0].replace(".", " ").title() if email else name) or "Someone"
        p = self._add(key_for(email=email, name=name), name=name, email=email)
        if email and not p["company"]:
            p["company"] = _domain(email)
        return p

    def page_for(self, p: dict) -> str:
        """The viewer's own entity page for this person, if one names them (by address, id or name)."""
        if p.get("page"):
            return p["page"]
        for pg in self._pages:
            if (p["email"] and email_of(pg.get("email")) == p["email"]) or slug(pg.get("title") or "") == slug(p["name"]) \
                    or pg.get("id") == slug(p["name"]):
                p["page"] = str(pg.get("path") or "")
                if pg.get("role") and not p["role"]:
                    p["role"] = clip(pg["role"], 60)
                if pg.get("company") and not p["company"]:
                    p["company"] = clip(pg["company"], 60)
                break
        return p.get("page") or ""

    def row(self, p: dict, now: float) -> dict:
        """The blocks.v1 person (what the chip and the peek draw): a colleague exactly as the home
        draws one (``home_sources.person_of``), an external person with their address, initials and
        their company's logo as a media key the server fetches — never a face (UI 15g)."""
        from control_plane import home_media, home_sources
        last = max(p["contacts"]) if p["contacts"] else None
        if p.get("colleague"):
            out = home_sources.person_of(p["account_id"] or "", p["name"], {"email": p["email"], "role": p.get("role")})
        else:
            out = {"name": p["name"], "vexa": False}
            role = " · ".join(x for x in (p.get("role"), p.get("company")) if x)
            if role:
                out["role"] = clip(role, 60)
            if p["email"]:
                out["email"] = p["email"]
                out["reach"] = "Email"
                # initials and their company's logo — never a face looked up on the web (UI 15g)
                lg = home_media.logo_key(p["email"])
                if lg:
                    out["logo"] = lg
        out["initials"] = initials(p["name"])
        if last:
            out["last"] = ago(now - last)
        return out


def _attendees(m: dict) -> list:
    data = m.get("data") if isinstance(m.get("data"), dict) else {}
    a = data.get("attendees") or m.get("attendees") or []
    return a if isinstance(a, list) else []


def _meeting_at(m: dict) -> Optional[float]:
    return ts(m.get("end_time")) or ts(m.get("start_time")) or ts(m.get("scheduled_at"))


def _meeting_title(m: dict) -> str:
    data = m.get("data") if isinstance(m.get("data"), dict) else {}
    return clip(m.get("title") or data.get("title") or data.get("name") or "a meeting", 80)


_DONE = {"completed", "stopped"}


def _dm_peer(d: dict, me: str) -> str:
    others = [p for p in d.get("participants") or [] if p and p != me]
    return others[0] if len(others) == 1 else ""


def _last_from(notes: list, who: str) -> Optional[dict]:
    for n in reversed(notes or []):
        if n.get("author") == who:
            return n
    return None


# ── the three lists ─────────────────────────────────────────────────────────────────────────────

def _item(*, id: str, person: dict, kind: str, reason: str, score: float, evidence: list, actions: list,
          label: str, at: Optional[float] = None, extra: Optional[dict] = None) -> dict:
    it = {"id": id[:120], "person": person["key"], "kind": kind, "label": label, "reason": clip(reason, 200),
          "score": round(max(0.0, min(1.0, score)), 3), "evidence": evidence[:6], "actions": actions[:4]}
    if at:
        it["at"] = at
    if extra:
        it.update(extra)
    return it


def _continue(p: dict, chat: str) -> dict:
    return ({"type": "action", "label": "Continue chat", "primary": True, "open": {"chat": chat}} if chat
            else {"type": "action", "label": "Message", "primary": True, "message": p["key"]})


def _draft(p: dict, about: str) -> dict:
    to = f"{p['name']} <{p['email']}>" if p.get("email") else p["name"]
    return {"type": "action", "label": "Draft follow-up", "needs_go": True,
            "prompt": clip(f"Draft a follow-up to {to} about {about}. Base it on our most recent exchange. "
                           "Leave it as a draft, list any commitments it makes, and send nothing until I give "
                           "my explicit go on the final text.", 600)}


def _contacts(dir: Directory, meetings: list) -> dict:
    """Every finished meeting the viewer had with each person: key → [(at, title, id)]."""
    met: dict = {}
    for m in meetings or []:
        if str(m.get("status") or "") not in _DONE:
            continue
        at = _meeting_at(m)
        if not at:
            continue
        for a in _attendees(m):
            p = dir.attendee(a)
            if p is None:
                continue
            p["contacts"].append(at)
            met.setdefault(p["key"], []).append((at, _meeting_title(m), m.get("id")))
    return met


def followups(*, dir: Directory, now: float, dms: list, meetings: list, waiting: list, states: dict,
              held: dict, skip_dm_newer_than: float = 0.0) -> list:
    """People the viewer owes something, ranked: unanswered chats, meetings with no follow-up yet,
    the viewer's queue naming a person (unanswered mail), contacts gone quiet against their cadence."""
    items: list = []
    for d in dms or []:
        peer = _dm_peer(d, dir.me)
        p = dir.colleague(peer) if peer else None
        theirs = _last_from(d.get("notes") or [], peer) if p else None
        if not theirs:
            continue
        mine = _last_from(d.get("notes") or [], dir.me)
        t_at = float(theirs.get("created") or 0)
        if (mine and float(mine.get("created") or 0) >= t_at) or now - t_at < UNANSWERED_AFTER_S \
                or now - float(d.get("last_active") or t_at) < skip_dm_newer_than:
            continue
        wait = now - t_at
        items.append(_item(id=f"fu-dm-{d['id']}-{int(t_at)}", person=p, kind="unanswered", label="Unanswered",
                           reason=f"{p['name']} wrote {ago(wait)} and has no reply yet",
                           score=0.6 + min(0.35, wait / (7 * DAY) * 0.35), at=t_at,
                           evidence=[{"text": f"Their last message, {ago(wait)}", "source": "chat"}],
                           actions=[_continue(p, d["id"]), _draft(p, "their last message")],
                           extra={"chat": d["id"]}))
    met = _contacts(dir, meetings)
    for m in meetings or []:
        mid = m.get("id")
        end = _meeting_at(m)
        if str(m.get("status") or "") not in _DONE or not end or not 0 <= now - end <= FOLLOW_UP_WINDOW_S:
            continue
        st = (states.get(f"m-{mid}") or {}).get("state")
        if st in ("follow_up", "replied", "done"):
            continue
        ps = [p for p in (dir.attendee(a) for a in _attendees(m)) if p is not None]
        if not ps:
            continue
        title, p = _meeting_title(m), ps[0]
        others = f" and {len(ps) - 1} more" if len(ps) > 1 else ""
        items.append(_item(id=f"fu-m-{mid}", person=p, kind="promised", label="After a meeting",
                           reason=f"\"{title}\" with {p['name']}{others} ended {ago(now - end)}; no follow-up recorded",
                           score=0.55 + 0.3 * max(0.0, 1 - (now - end) / FOLLOW_UP_WINDOW_S), at=end,
                           evidence=[{"text": f"Meeting \"{title}\", {ago(now - end)}", "source": "meeting"}],
                           actions=[{"type": "action", "label": "Review follow-ups", "primary": True,
                                     "prompt": clip(f"Show the follow-ups proposed from the meeting \"{title}\" "
                                                    f"(meeting {mid}) so I can approve or edit each.", 600)},
                                    _draft(p, f"our meeting \"{title}\"")],
                           extra={"people": [x["key"] for x in ps[:6]], "meeting": str(mid)}))
    for w in waiting or []:
        wid = clip(w.get("id"), 60)
        say = clip(w.get("say") or w.get("reason"), 200)
        who = w.get("person") if isinstance(w.get("person"), dict) else {}
        email = email_of(w.get("email") or who.get("email"))
        name = clip(w.get("name") or who.get("name"), 60)
        mail = "mail" in str(w.get("flow") or "").lower() or "mail" in str(w.get("kind") or "").lower()
        if not wid or not say or not (email or name or mail):
            continue
        p = dir.attendee({"email": email, "name": name or (email.split("@")[0] if email else "Unanswered mail")})
        if p is None:
            continue
        since = ts(w.get("since")) or now
        items.append(_item(id=f"fu-q-{slug(wid)}", person=p, kind="unanswered", label="Unanswered mail" if mail else "Waiting",
                           reason=say, score=0.65 + min(0.3, (now - since) / (7 * DAY) * 0.3), at=since,
                           evidence=[{"text": say, "source": "mail" if mail else "queue"}],
                           actions=[{"type": "action", "label": "Draft reply", "primary": True, "needs_go": True,
                                     "prompt": clip(f"Help me reply: {say}. Draft only; send nothing until I give my "
                                                    "explicit go on the final text.", 600)}]))
    for key, rows in met.items():
        p = dir.people[key]
        times = sorted(set(p["contacts"]))
        if len(times) < COLD_MIN_CONTACTS:
            continue
        gap = statistics.median([b - a for a, b in zip(times, times[1:]) if b - a > 3600] or [0])
        if gap <= 0:
            continue
        since = now - times[-1]
        due = max(2 * gap, COLD_FLOOR_S)
        if since < due or since > COLD_CEILING_S:
            continue
        last_title = sorted(rows)[-1][1]
        items.append(_item(id=f"fu-cold-{key}-{int(times[-1] // DAY)}", person=p, kind="cold", label="Going cold",
                           reason=f"Usually every {max(1, round(gap / DAY))} days; last contact {ago(since)}",
                           score=0.35 + min(0.4, (since - due) / due * 0.4), at=times[-1],
                           evidence=[{"text": f"{len(times)} contacts, usually every {max(1, round(gap / DAY))} days", "source": "history"},
                                     {"text": f"Last: \"{last_title}\", {ago(since)}", "source": "meeting"}],
                           actions=[_continue(p, "") if p.get("account_id") else _draft(p, f"catching up since \"{last_title}\""),
                                    *([_draft(p, f"catching up since \"{last_title}\"")] if p.get("account_id") else [])]))
    items = [i for i in items if i["id"] not in held]
    best: dict = {}
    for i in sorted(items, key=lambda x: -x["score"]):
        best.setdefault((i["person"], i["kind"]), i)
    return sorted(best.values(), key=lambda x: (-x["score"], -(x.get("at") or 0)))[:LIST_MAX]


def discover(*, dir: Directory, now: float, dms: list, meetings: list, sessions: list, held: dict) -> list:
    """People worth knowing, inside what the viewer may see: colleagues never messaged, frequent
    external attendees with no page yet, suggested introductions between two of the viewer's contacts."""
    items: list = []
    met = _contacts(dir, meetings)
    dm_peers = {_dm_peer(d, dir.me) for d in dms or []}
    together: dict = {}
    for s in sessions or []:
        if str(s.get("id") or "").startswith("dm-"):
            continue
        for who in s.get("participants") or []:
            if who and who != dir.me:
                together[who] = together.get(who, 0) + 1
    for p in list(dir.people.values()):
        if not p["colleague"] or not p["discoverable"] or p["account_id"] in dm_peers:
            continue
        ev, score = [], 0.3
        if p["workspaces"]:
            ev.append({"text": f"You share {len(p['workspaces'])} workspace{'s' if len(p['workspaces']) != 1 else ''}: "
                               + ", ".join(p["workspaces"][:3]), "source": "workspace"})
            score += min(0.2, 0.07 * len(p["workspaces"]))
        n_met = len(met.get(p["key"], []))
        if n_met:
            ev.append({"text": f"In {n_met} meeting{'s' if n_met != 1 else ''} with you", "source": "meeting"})
            score += min(0.3, 0.1 * n_met)
        n_chat = together.get(p["account_id"] or "", 0)
        if n_chat:
            ev.append({"text": f"Both in {n_chat} chat{'s' if n_chat != 1 else ''}", "source": "chat"})
            score += min(0.2, 0.07 * n_chat)
        # ONLY A REAL INTERACTION earns a suggestion (founder, live home: a "Colleague · tester-b" card),
        # and never a test account: a shared workspace alone is not a reason to message someone
        if not (n_met or n_chat) or is_test_account(p.get("name"), p.get("email")):
            continue
        items.append(_item(id=f"dc-{p['key']}", person=p, kind="colleague", label="Colleague", score=score,
                           reason=f"{p['name']} works with you on Vexa; you have not messaged yet", evidence=ev,
                           actions=[{"type": "action", "label": "Message", "primary": True, "message": p["key"]}]))
    for key, rows in met.items():
        p = dir.people[key]
        if p["colleague"] or len(rows) < FREQUENT_MEETINGS or dir.page_for(p):
            continue
        titles = [t for _, t, _ in sorted(rows, reverse=True)[:3]]
        items.append(_item(id=f"dx-{key}", person=p, kind="frequent", label="No page yet",
                           score=0.3 + min(0.5, 0.1 * len(rows)), at=max(r[0] for r in rows),
                           reason=f"In {len(rows)} of your meetings and has no page yet",
                           evidence=[{"text": f"\"{t}\"", "source": "meeting"} for t in titles],
                           actions=[{"type": "action", "label": "Create page", "primary": True,
                                     "prompt": clip(f"Create a person page for {p['name']}"
                                                    + (f" ({p['email']})" if p["email"] else "")
                                                    + " from our meetings: " + "; ".join(titles) + ".", 600)}]))
    # introductions: two of your contacts, each met recently on the same subject, who never met each other
    recent: dict = {}
    for m in meetings or []:
        at = _meeting_at(m)
        if str(m.get("status") or "") not in _DONE or not at or now - at > 60 * DAY:
            continue
        ps = [x for x in (dir.attendee(a) for a in _attendees(m)) if x is not None]
        words = {w.lower() for w in _WORD.findall(_meeting_title(m))} - _STOP
        for x in ps:
            r = recent.setdefault(x["key"], {"words": {}, "with": set()})
            for w in words:
                r["words"].setdefault(w, _meeting_title(m))
            r["with"].update(y["key"] for y in ps if y is not x)
    keys = sorted(recent)
    intros = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            pa, pb = dir.people[a], dir.people[b]
            if b in recent[a]["with"] or not (pa["discoverable"] and pb["discoverable"]):
                continue
            if pa["company"] and pa["company"] == pb["company"]:
                continue
            common = sorted(set(recent[a]["words"]) & set(recent[b]["words"]))
            if not common:
                continue
            intros.append((len(common), a, b, common[0]))
    for n, a, b, word in sorted(intros, reverse=True)[:INTROS_MAX]:
        pa, pb = dir.people[a], dir.people[b]
        iid = f"di-{a}-{b}"[:120]
        items.append(_item(id=iid, person=pa, kind="intro", label="Suggested intro", score=0.25 + 0.1 * min(n, 3),
                           reason=f"{pa['name']} and {pb['name']} both met you about \"{word}\" and have not met each other",
                           evidence=[{"text": f"\"{recent[a]['words'][word]}\"", "source": "meeting"},
                                     {"text": f"\"{recent[b]['words'][word]}\"", "source": "meeting"}],
                           actions=[{"type": "action", "label": "Draft intro", "primary": True, "needs_go": True,
                                     "prompt": clip(f"Draft a short introduction between {pa['name']} and {pb['name']}: "
                                                    f"both talked to me about {word}. Draft only; send nothing until I "
                                                    "give my explicit go on the final text.", 600)}],
                           extra={"people": [a, b]}))
    items = [i for i in items if i["id"] not in held]
    return sorted(items, key=lambda x: (-x["score"], x["id"]))[:LIST_MAX]


# ── the same items as blocks.v1 cards ───────────────────────────────────────────────────────────

def candidates(items: list, dir: Directory, now: float, *, prior_scale: float = 1.0) -> list:
    """The same items as the home's candidates (``home_sources`` interface): one blocks.v1 card per
    item, the person first, the reason, the evidence, the actions. Snooze and dismiss (Done) are the
    card's own controls; the home's feedback holds them. Titles carry names and meeting titles only."""
    out = []
    for i in items:
        keys = [k for k in (i.get("people") or [i["person"]]) if k in dir.people][:6]
        pmap = {k: dir.row(dir.people[k], now) for k in keys}
        blocks = [{"type": "person", "people": keys, "extra": [i["label"]]},
                  {"type": "text", "tone": "why", "text": i["reason"]}]
        if i["evidence"]:
            blocks.append({"type": "evidence", "items": i["evidence"]})
        blocks.extend(i["actions"])
        card = {"type": "card", "id": i["id"], "kind": "waiting" if i["id"].startswith("fu-") else "suggestion",
                "label": i["label"], "person": i["person"], "controls": True, "blocks": blocks}
        if i.get("at"):
            card["meta"] = ago(now - i["at"])
        c = {"card": card, "prior": round(i["score"] * prior_scale, 3), "title": clip(i["reason"], 120),
             "people": keys, "people_map": pmap, "signal": i["label"].lower(), "facts": f"people {i['kind']}",
             "source": f"people:{i['id']}"}
        refs = {k: v for k, v in (("meeting", i.get("meeting")), ("session", i.get("chat"))) if v}
        if refs:
            c["refs"] = refs
        out.append(c)
    return out


def for_agent(items: list, dir: Directory, now: float) -> list:
    """The items as an agent reads them: the person spelled out, no keys to resolve."""
    out = []
    for i in items:
        p = dir.people.get(i["person"]) or {}
        row = {"id": i["id"], "kind": i["kind"], "reason": i["reason"], "score": i["score"],
               "person": {k: v for k, v in dir.row(p, now).items() if k != "initials"} if p else {},
               "evidence": i["evidence"],
               "actions": [{k: a[k] for k in ("label", "needs_go", "prompt", "open") if k in a} for a in i["actions"]]}
        if i.get("people"):
            row["with"] = [dir.people[k]["name"] for k in i["people"] if k in dir.people]
        out.append(row)
    return out


# ── everyone the viewer has interacted with (the home's People strip) ────────────────────────────

_TEST_ACCOUNT = re.compile(r"^(?:tester|test|qa|demo)(?:[-_.]\w+|\d+)?$|@(?:[\w-]+\.)*(?:test|example\.(?:com|org|net))$|\.test$", re.I)
_NOBODY = re.compile(r"^(speaker\s*[a-z]?\d*|unknown( speaker)?|unidentified( speaker)?|guest|participant\s*\d*|vexa( bot)?|.*notetaker|.*\bbot)$", re.I)
HOW = ("met", "chat", "workspace", "mail", "page")
HOW_WORDS = {"met": "met in a meeting", "chat": "in a chat with you", "workspace": "shares a workspace",
             "mail": "emailed", "page": "on your pages"}


def is_test_account(name, email=None) -> bool:
    """tester-b, qa-1, someone@x.test, anyone@example.com: a test account, never shown to a person."""
    for v in (name, email):
        v = str(v or "").strip()
        if v and (_TEST_ACCOUNT.search(v) or _TEST_ACCOUNT.search(v.split("@", 1)[0])):
            return True
    return False


def known(*, dir: Directory, now: float, dms: list, meetings: list, shared: list,
          mail: Optional[list] = None) -> list:
    """FOUNDER, 2026-10-15: "we need to have all people we have interacted with on the main page."
    Everyone the viewer met (an identified meeting participant or attendee), chatted with (a direct
    or a shared chat), shares a workspace with, or emailed — merged by account and address, most
    recent first. Never a test account, never an unidentified speaker, never the viewer."""
    seen: dict = {}
    # the viewer under their own name in a transcript (no address there): their attendee name, or
    # their address's first word
    me_names = {str(a.get("name") or "").strip().lower() for m in meetings or [] for a in _attendees(m)
                if isinstance(a, dict) and email_of(a.get("email")) == dir.my_email and a.get("name")}
    me_first = dir.my_email.split("@", 1)[0].split(".")[0].lower() if dir.my_email else ""

    def is_me(name: str) -> bool:
        n = name.strip().lower()
        return bool(n) and (n in me_names or (bool(me_first) and n.split()[0] == me_first))

    def touch(p: Optional[dict], how: str, at: Optional[float]) -> None:
        if p is None or is_test_account(p.get("name"), p.get("email")) or _NOBODY.match(str(p.get("name") or "")):
            return
        row = seen.setdefault(p["key"], {"p": p, "how": [], "last": 0.0})
        if how not in row["how"]:
            row["how"].append(how)
        row["last"] = max(row["last"], float(at or 0))

    for m in meetings or []:
        if str(m.get("status") or "") not in _DONE:
            continue
        at = _meeting_at(m)
        for a in _attendees(m):
            touch(dir.attendee(a), "met", at)
        data = m.get("data") if isinstance(m.get("data"), dict) else {}
        for n in data.get("participants") or []:
            name = clip(n if isinstance(n, str) else (n or {}).get("name"), 60)
            if name and not _NOBODY.match(name) and not is_me(name):
                # a transcript names a person without an address: the same name already known is them
                same = next((q for q in dir.people.values() if q["name"].strip().lower() == name.lower()), None)
                touch(same or dir.attendee({"name": name}), "met", at)
    for d in dms or []:
        peer = _dm_peer(d, dir.me)
        touch(dir.colleague(peer) if peer else None, "chat", ts(d.get("last_active")))
    for s in shared or []:
        for who in s.get("participants") or []:
            if who and who != dir.me:
                touch(dir.colleague(who), "chat", ts(s.get("last_active")))
    for p in list(dir.people.values()):
        if p.get("colleague") and p.get("workspaces"):
            touch(p, "workspace", None)
    for e in mail or []:
        touch(dir.attendee({"email": e.get("email"), "name": e.get("name")}), "mail", ts(e.get("at")))
    out = []
    for key, row in seen.items():
        p = row["p"]
        page = dir.page_for(p)
        if page:
            row["how"].append("page")
        person = dir.row(p, now)
        out.append({"key": key, "name": p["name"], "person": person, "how": row["how"],
                    # a Vexa account can be invited into a chat or rung into a call; anyone else is a
                    # reference only (and a call link the person sends themselves)
                    "subject": p.get("account_id") or None, "vexa": bool(p.get("colleague")),
                    "how_words": ", ".join(HOW_WORDS[h] for h in row["how"] if h in HOW_WORDS),
                    "last": row["last"] or None, "page": page or None,
                    **({"accounts": p["accounts"], "accounts_confirmed": bool(p.get("accounts_confirmed"))}
                       if p.get("accounts") else {})})
    out.sort(key=lambda r: (-(r["last"] or 0), r["name"].lower()))
    return out
