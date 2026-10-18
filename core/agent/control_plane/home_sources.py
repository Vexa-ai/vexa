"""home_sources.py — the home's candidates, from what agent-api can already see (#1783).

PURE: every source arrives as plain rows (the router reads them), and this turns them into
candidate cards in ``home-card.v1`` primitives plus the usage facts the lifecycle stage and the
curriculum read. Nothing here calls a model or a network.

Sources, each optional (a missing one is named in the composition's ``inputs``):

* ``meetings``   meeting-api rows — upcoming (meeting cards) and just finished (result cards)
* ``waiting``    flows' ``/queue/waiting`` items — what the person's Vexa is waiting on them for
* ``notes``      the person's unread notifications — mentions, chats shared, invites (waiting cards)
* ``sessions``   the chat list — routine chats that ran (result cards), recent chats (continue)
* ``curriculum`` `_global/onboarding.md` — the next onboarding steps (try cards)

Every action is a PROMPT the agent runs in a chat, an open of a chat by id, or a message to a
person. The words are short and name ids and titles only; no source content is copied.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from control_plane import display_name as display_name_mod, home, home_media, identity_media

# ── THE CANDIDATE INTERFACE, for other domains to plug into (#1783) ───────────────────────────
#
# A source is ``fn(ctx) -> list[candidate]``, registered once by name with ``register_source``. The
# composer calls every registered source on each composition (a source that raises is skipped and
# named in the composition's inputs), applies item state and feedback, and ranks. ``ctx`` is a dict:
#
#     owner       the person's subject            now         epoch seconds
#     sessions    their chat rows                 colleagues  {subject: {name, email, presence}}
#     events      their home events (60 days)     facts       usage counts (the stage reads them)
#     meetings    meeting-api rows (as above)     waiting     their queue items
#     states      each home item's state (``home.item_states``)
#
# A candidate:
#
#     {"card":   blocks.v1 Card — id (stable across compositions: it is the item id feedback and
#                state attach to), kind (meeting|waiting|suggestion|try|result|continue), blocks,
#      "prior":  0..1 — how much it matters now, before stage mix and feedback weights,
#      "title":  short words for the ranker and the history (ids and titles only, never content),
#      "people": [keys into the people map this source adds to], "signal": why it ranks there,
#      "source": "<domain>:<id>" — what it came from, "people_map": {key: blocks.v1 Person} (optional),
#      "refs":   {session?, meeting?, entities[], pages[], urls[]} (optional)}
#
# To message a person, a card uses an action with ``message: <person key>``: the home opens the
# 1:1 chat (on Vexa) or a draft-only chat (external) — the composer is where a chat with a person
# starts.
SOURCES: dict = {}


def register_source(name: str, fn) -> None:
    SOURCES[str(name)] = fn


def from_sources(ctx: dict) -> tuple[list, dict, list]:
    """Every registered source's candidates, the people they add, and the names that failed."""
    out, people, failed = [], {}, []
    for name, fn in list(SOURCES.items()):
        try:
            rows = list(fn(ctx) or [])
        except Exception:  # noqa: BLE001 — one source cannot take the home down
            failed.append(name)
            continue
        for c in rows[:20]:
            if not isinstance(c, dict) or not isinstance(c.get("card"), dict) or not home._key(c["card"].get("id")):
                continue
            people.update({k: v for k, v in (c.pop("people_map", None) or {}).items() if home._key(k) and isinstance(v, dict)})
            c.setdefault("prior", 0.5)
            c.setdefault("source", f"{name}:{c['card']['id']}")
            out.append(c)
    return out, people, failed


_LIVE = {"requested", "joining", "awaiting_admission", "active", "needs_help", "stopping"}
_DONE = {"completed", "stopped"}


def _ts(v) -> Optional[float]:
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, str) or not v:
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    except ValueError:
        return None


def _in(seconds: float) -> str:
    m = int(seconds // 60)
    return f"in {m} min" if m < 60 else f"in {m // 60} h {m % 60:02d}" if m % 60 else f"in {m // 60} h"


def _ago(seconds: float) -> str:
    m = int(seconds // 60)
    return f"{m} min ago" if m < 60 else (f"{m // 60} h ago" if m < 1440 else f"{m // 1440}d ago")


def _age(seconds: float) -> str:
    return f"{max(0, int(seconds // 86400))}d" if seconds >= 86400 else f"{max(1, int(seconds // 3600))}h"


def _slug(s: str) -> str:
    out = "".join(ch if ch.isalnum() else "-" for ch in str(s))[:60].strip("-")
    return out or "x"


# ── WORDS A PERSON READS (founder, UI lab 2026-10-14) ──────────────────────────────────────────
# A meeting is called by its real title, never "Meet 05:55 PM" or a platform id; a routine by its
# name and what its last run said; a person by a display name, never a handle; a chat's preview is
# the last useful line of its last answer, never "Vexa: …" or a draft's capitalised boilerplate.

_PLATFORM_WORD = {"google_meet": "Google Meet", "zoom": "Zoom", "teams": "Teams", "vexa_call": "Vexa call",
                  "voice_note": "Voice note", "jitsi": "Jitsi"}
_RAW = re.compile(r"^(?:meet|pchat|flow|dm|call)-[\w-]+$|^(?:new chat|chat)$|^https?://|^[a-z]{3}-[a-z]{4}-[a-z]{3}$", re.I)


def is_raw(title) -> bool:
    """A name nobody chose: an id, a slug, a placeholder, a link, a Meet code."""
    t = str(title or "").strip()
    return not t or bool(_RAW.match(t)) or t.lower() in _PLATFORM_WORD


def display_name(handle) -> str:
    """"valeriia.bushueva" → "Valeriia Bushueva"; a name that already has capitals is kept."""
    h = str(handle or "").strip()
    if not h or "@" in h:
        h = h.split("@", 1)[0]
    if h and (h == h.lower() or re.fullmatch(r"[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)+", h)):
        return " ".join(w[:1].upper() + w[1:] for w in re.split(r"[._\s-]+", h) if w)
    return h


def meeting_title(m: dict, chat_title: str = "") -> str:
    """The meeting's real title: its own, its chat's when someone named it, who was in it, and only
    then the platform's word."""
    # the one rule (control_plane.display_name): title · chat name · people · Call with Vexa
    return home.clip(display_name_mod.meeting_display_name(m, chat_title), 80)


def _plain(t: str) -> str:
    # a [[ref]] reads as its name in a one-line preview (founder, live: raw `[[Dmitry Grankin]]`)
    t = re.sub(r"\[\[([^\]|]*)\|([^\]]*)\]\]", r"\2", str(t))
    t = re.sub(r"\[\[(?:[\w-]+:)?([^\]]*)\]\]", lambda m: m.group(1).rsplit("/", 1)[-1].replace(".md", ""), t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    return re.sub(r"\s+", " ", re.sub(r"[#*`>_|]", "", t)).strip()


def useful_line(text, n: int = 120) -> str:
    """The last useful line of an answer: no machinery, no heading, no capitalised block label, a
    sentence with some words in it; cut at a word boundary."""
    lines = []
    for raw in str(text or "").splitlines():
        line = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", raw).strip()
        if (not line or line.startswith(("#", "<!--", "```", "|")) or re.fullmatch(r"(?:vexa|you)\s*:", line, re.I)
                or sum(c.isupper() for c in line) > sum(c.islower() for c in line)):
            continue
        lines.append(re.sub(r"^(?:vexa|assistant)\s*:\s*", "", line, flags=re.I))
    pick = next((x for x in reversed(lines) if len(x.split()) >= 4), lines[-1] if lines else "")
    return _cut(_plain(pick), n)


def note_lines(text, n: int = 160) -> str:
    """A note's first lines, under its frontmatter and headings — "" when nothing is written yet."""
    body = re.sub(r"^---\n.*?\n---\n?", "", str(text or ""), count=1, flags=re.S)
    out = [ln.strip() for ln in body.splitlines()
           if ln.strip() and not ln.strip().startswith(("#", "<!--")) and not re.match(r"_?nothing (?:written|yet)", ln.strip(), re.I)]
    return _cut(_plain(" ".join(out[:2])), n) if out else ""


def _cut(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    cut = s[: n - 1]
    at = cut.rfind(" ")
    return (cut[:at] if at > n * 0.6 else cut).rstrip(" ,;:.-—") + "…"


_DONE_LABEL = {"prepared": "Prepared", "acted": "In progress", "in_progress": "In progress", "live": "Live now",
               "ended": "Ended", "follow_up": "Follow-up drafted"}


def apply_state(cand: dict, st: Optional[dict]) -> Optional[dict]:
    """THE ITEM'S STATE, APPLIED: a terminal state takes the card off the home; otherwise, when the
    work already has a chat or a page, the card says where it got to and its first button goes BACK
    INTO that work (*Continue*) instead of starting it again (founder, 2026-10-13: the live screen
    offered "Prepare for …" for a meeting he had already prepared)."""
    if not st:
        return cand
    if st.get("state") in home.TERMINAL_STATES:
        return None
    card = cand["card"]
    card["state"] = st["state"]
    chat, page = st.get("chat") or "", st.get("page") or ""
    if chat:
        card["result"] = {"chat": chat}
    elif page:
        card["result"] = {"page": page}
    word = _DONE_LABEL.get(st["state"])
    if not (chat or page):
        if word and st["state"] != "ended":     # an ended meeting's card already says it ended
            card["blocks"].insert(1, {"type": "text", "tone": "ok", "text": word})
        return cand
    go = {"type": "action", "label": "Continue", "primary": True, "open": {"chat": chat} if chat else {"page": page}}
    blocks, replaced = [], False
    for b in card["blocks"]:
        if b.get("type") == "action" and not replaced and (b.get("prompt") or b.get("open")):
            blocks.append(go)
            replaced = True
            continue
        if b.get("type") == "action" and b.get("primary"):
            b = {**b, "primary": False}
        blocks.append(b)
    if not replaced:
        blocks.append(go)
    if word:
        blocks.insert(1, {"type": "text", "tone": "ok", "text": word})
    card["blocks"] = blocks
    cand["prior"] = max(0.2, float(cand.get("prior", 0.5)) * 0.85)   # started work sits a little lower
    return cand


def person_of(subject: str, name: str, info: Optional[dict]) -> dict:
    """A colleague on Vexa as a blocks.v1 person: their photo is the one identity keeps for them
    (``photo``, served only to people who share a workspace or chat with them — UI 15g), their
    company's logo a media key the server fetches, and presence is only what chats you share show."""
    info = info or {}
    email = info.get("email") or ""
    p = {"name": home.clip(name, 60) or "A colleague", "vexa": True, "reach": "Direct chat"}
    if home._key(subject):
        p["account_id"] = subject
    url = identity_media.photo_url(subject)
    if url:
        p["photo"] = url
    lg = home_media.logo_key(email)
    if lg:
        p["logo"] = lg
    if info.get("presence") in ("now", "today"):
        p["presence"] = info["presence"]
    if info.get("role"):
        p["role"] = home.clip(info["role"], 60)
    return p


def candidates(*, now: float, meetings: Optional[list] = None, waiting: Optional[list] = None,
               notes: Optional[list] = None, sessions: Optional[list] = None,
               curriculum: Optional[list] = None, facts: Optional[dict] = None,
               refused: Optional[set] = None, snoozed: Optional[set] = None,
               states: Optional[dict] = None, colleagues: Optional[dict] = None,
               owner: str = "", line_of: Optional[Callable[[str], str]] = None,
               note_of: Optional[Callable[[dict], str]] = None) -> tuple[list, dict]:
    """→ (candidates, people). A candidate is ``{card, prior, title, people, signal, facts, source}``.
    ``states`` (``home.item_states``) advances each item from evidence; a meeting whose own chat
    (``meet-<row>``) exists counts as prepared even when nobody pressed anything here.
    ``line_of(session)`` gives a chat's last answer (its preview, a routine's outcome) and
    ``note_of(meeting)`` a finished meeting's note text; each may be absent or return ""."""
    def _line(sid: str) -> str:
        try:
            return useful_line(line_of(sid)) if line_of else ""
        except Exception:  # noqa: BLE001 — a preview is extra
            return ""
    out: list = []
    people: dict = {}
    states = dict(states or {})
    by_session = {str(s.get("id")): s for s in sessions or []}
    for m in meetings or []:
        mid = m.get("id")
        st = states.get(f"m-{mid}") or {}
        chat = f"meet-{mid}" if f"meet-{mid}" in by_session else ""
        status = str(m.get("status") or "")
        if status in ("scheduled", "idle") and chat and st.get("state") not in home.TERMINAL_STATES:
            states[f"m-{mid}"] = {**st, "state": st.get("state") or "prepared", "chat": st.get("chat") or chat}
        elif status in _LIVE:
            states[f"m-{mid}"] = {**st, "state": "live", "chat": st.get("chat") or chat}
        elif status in _DONE and st.get("state") not in home.TERMINAL_STATES + ("follow_up",):
            # ENDED IS ENDED, chat or no chat: a "live" state from while it ran never outlives it
            states[f"m-{mid}"] = {**st, "state": "ended", "chat": st.get("chat") or chat}
    for m in meetings or []:
        mid = m.get("id")
        title = meeting_title(m, str((by_session.get(f"meet-{mid}") or {}).get("title") or ""))
        status = str(m.get("status") or "")
        start = _ts(m.get("scheduled_at") or m.get("start_time"))
        end = _ts(m.get("end_time"))
        platform = _PLATFORM_WORD.get(str(m.get("platform") or ""), home.clip(m.get("platform"), 20))
        if status in ("scheduled", "idle") and start and 0 < start - now <= 24 * 3600:
            soon = start - now
            prior = 0.95 if soon <= 3600 else 0.85 if soon <= 3 * 3600 else 0.55
            when = time.strftime("%H:%M", time.gmtime(start))
            out.append({"source": f"meeting:{mid}", "title": title, "prior": prior, "people": [], "refs": {"meeting": str(mid)},
                        "signal": f"starts {_in(soon)}", "facts": f"meeting {_in(soon)}",
                        "card": {"type": "card", "id": f"m-{mid}", "kind": "meeting", "controls": soon > 3600,
                                 "meta": " · ".join(x for x in (when + " UTC", platform, _in(soon)) if x),
                                 "blocks": [{"type": "title", "text": title},
                                            {"type": "action", "label": "Prepare", "primary": True,
                                             "prompt": f"Prepare me for the meeting \"{title}\" (meeting {mid})."},
                                            {"type": "action", "label": "Send bot",
                                             "prompt": f"Send the Vexa bot to the meeting \"{title}\" (meeting {mid})."}]}})
        elif status in _LIVE:
            out.append({"source": f"meeting:{mid}", "title": title, "prior": 0.98, "people": [], "signal": "live now",
                        "facts": "live meeting",
                        "card": {"type": "card", "id": f"m-{mid}", "kind": "meeting", "controls": False, "meta": "live now",
                                 "blocks": [{"type": "title", "text": title},
                                            {"type": "action", "label": "Follow live", "primary": True,
                                             "prompt": f"Follow the live meeting \"{title}\" (meeting {mid}) and tell me what matters."}]}})
        elif status in _DONE and end and 0 <= now - end <= 6 * 3600:
            ago = now - end
            try:
                note_text = note_of(m) if note_of else ""
            except Exception:  # noqa: BLE001
                note_text = ""
            note = note_lines(note_text)
            # "Review follow-ups" only when the note has some (founder, live home)
            has_followups = bool(re.search(r"^#{1,4}\s*(follow[- ]?ups?|next steps|action items|to ?dos?)\b|^\s*[-*]\s*\[ \]",
                                           str(note_text or ""), re.I | re.M))
            acts = ([{"type": "action", "label": "Review follow-ups", "primary": True,
                      "prompt": f"Show the follow-ups proposed from the meeting \"{title}\" (meeting {mid}) so I can approve or edit each."}]
                    if has_followups else [])
            acts.append({"type": "action", "label": "Open notes", "primary": not has_followups,
                         "prompt": f"Open the notes from the meeting \"{title}\" (meeting {mid})."})
            out.append({"source": f"meeting:{mid}", "title": title, "prior": 0.97 if ago < 3600 else 0.7,
                        "people": [], "signal": f"ended {_ago(ago)}", "facts": f"meeting ended {_ago(ago)}",
                        "card": {"type": "card", "id": f"m-{mid}", "kind": "result", "label": "Just finished",
                                 "controls": ago > 3600, "tone": "default",
                                 "meta": " · ".join(x for x in (platform, "ended " + _ago(ago)) if x),
                                 "blocks": [{"type": "title", "text": title},
                                            # the note's first lines, or that it is being written
                                            {"type": "text", "tone": "why", "text": note or "Writing notes…"},
                                            *acts]}})
    # WHAT IS WAITING ON THE PERSON (founder, live home: five "This one stopped… · Handle" cards —
    # "this just does not work"). The queue's `say` is an INSTRUCTION to the agent
    # (`behavior/queue/*.md`), never words for a person, and a run that failed or is retrying
    # stays in its routine's own chat. Only a real ask reaches the home: a routine PAUSED until the
    # person decides. They share ONE card, a plain row each — the routine's name and a real act.
    asks: dict = {}
    titles = {str(s.get("flow") or ""): str(s.get("title") or "") for s in sessions or [] if s.get("flow")}
    for w in waiting or []:
        detail = str((w.get("reason") or {}).get("detail") if isinstance(w.get("reason"), dict) else w.get("reason") or "")
        if "routine_paused" not in detail and str(w.get("status") or "") != "paused":
            continue
        flow = str(w.get("flow") or "")
        if not flow or flow in asks:
            continue
        name = titles.get(flow, "")
        bare = flow.split("/")[-1].split(":")[-1]
        if not name or is_raw(name) or name == bare:
            name = re.sub(r"-?\d{4}-\d{2}-\d{2}$", "", bare).replace("-", " ").replace("_", " ").strip().capitalize() or "A routine"
        why = "it needs you present for mail, calendar or a connection" if "human_session_required" in detail else "it was paused"
        asks[flow] = {"name": name, "why": why, "since": _ts(w.get("since")) or now}
    if asks:
        rows = [{"type": "row", "icon": "repeat", "text": home.clip(f"{a['name']} paused — {a['why']}", 200),
                 "age": _age(now - a["since"]),
                 "action": {"type": "action", "label": "Resume", "prompt": f"Resume my routine “{a['name']}”."}}
                for a in sorted(asks.values(), key=lambda a: -a["since"])[:3]]
        out.append({"source": "queue:paused", "title": "Routines paused until you decide", "prior": 0.75, "people": [],
                    "signal": f"{len(asks)} paused", "facts": "paused routines",
                    "card": {"type": "card", "id": "q-paused", "kind": "waiting", "label": "Needs you",
                             "blocks": [{"type": "list", "rows": rows}]}})
    for n in notes or []:
        if n.get("read_at"):
            continue
        kind = n.get("kind")
        if kind not in ("chat_mentioned", "chat_added", "chat_shared", "workspace_invited", "agent", "call_started",
                        "workspace_added", "invite_accepted"):
            continue
        actor = home.clip(n.get("actor"), 120)
        handle = home.clip(n.get("actor_name"), 60)
        name = display_name(handle) if kind != "agent" else handle
        pkey = None
        if actor and name and kind != "agent":
            pkey = f"p-{_slug(actor)}"[:120]
            people[pkey] = {**person_of(actor, name, (colleagues or {}).get(actor)),
                            "last": _ago(now - float(n.get("created") or now))}
        at = float(n.get("created") or now)
        chat = n.get("object_id") if n.get("object_kind") == "chat" else None
        act = ({"type": "action", "label": "Open", "open": {"chat": chat}} if chat and home._key(chat)
               else {"type": "action", "label": "Open", "prompt": f"Open what this notification is about: {home.clip(n.get('text'), 160)}"})
        text = home.clip(n.get("text"), 200)
        if handle and name and handle != name:
            text = text.replace(handle, name)
        if kind == "invite_accepted" and name:
            # a person event says who and where: "Valeriia Bushueva joined AI Tinkerers Lisbon"
            text = f"{name} joined {home.clip(n.get('object_name'), 80) or 'your workspace'}"
            out.append({"source": f"notification:{n.get('id')}", "title": text, "prior": 0.55,
                        "people": [pkey] if pkey else [], "signal": "joined", "facts": kind,
                        "card": {"type": "card", "id": f"n-{_slug(n.get('id') or '')}", "kind": "continue", "label": "People",
                                 "meta": _ago(now - at), **({"person": pkey} if pkey else {}),
                                 "blocks": [*([{"type": "person", "people": [pkey]}] if pkey else []),
                                            {"type": "text", "text": text},
                                            *([{"type": "action", "label": "Message", "message": pkey, "primary": True}] if pkey else [])]}})
            continue
        row = {"type": "row", "icon": "chat" if kind.startswith("chat") else "people", "text": text,
               "age": _age(now - at), "action": act}
        if pkey:
            row["person"] = pkey
        out.append({"source": f"notification:{n.get('id')}", "title": home.clip(text, 120),
                    "prior": 0.8 if kind == "chat_mentioned" else 0.6, "people": [pkey] if pkey else [],
                    "signal": kind.replace("_", " "), "facts": kind,
                    "card": {"type": "card", "id": f"n-{_slug(n.get('id') or '')}", "kind": "waiting",
                             **({"person": pkey} if pkey else {}), "blocks": [row]}})
    # PEOPLE YOU WORK WITH, FIRST-CLASS: a direct chat with a colleague that moved recently.
    for s in sessions or []:
        sid = str(s.get("id") or "")
        other = s.get("other")
        last = float(s.get("last_active") or 0)
        if not sid.startswith("dm-") or not other or now - last > 2 * 86400:
            continue
        info = (colleagues or {}).get(other) or {}
        pkey = f"p-{_slug(other)}"[:120]
        people[pkey] = {**person_of(other, info.get("name") or "A colleague", info), "last": _ago(now - last)}
        out.append({"source": f"session:{sid}", "title": f"Direct chat with {people[pkey]['name']}",
                    "prior": 0.7 if now - last < 6 * 3600 else 0.45, "people": [pkey], "signal": f"active {_ago(now - last)}",
                    "facts": "direct chat", "refs": {"session": sid},
                    "card": {"type": "card", "id": f"d-{_slug(sid)}", "kind": "waiting", "label": "Direct message",
                             "person": pkey, "blocks": [
                                 {"type": "row", "person": pkey, "text": home.clip(s.get("title") or "Direct chat", 120),
                                  "age": _age(now - last),
                                  "action": {"type": "action", "label": "Continue", "primary": True, "open": {"chat": sid}}}]}})
    recent = []
    # ONE ROW PER ROUTINE, its latest run (founder, live: two "Unanswered inbox sweep" rows)
    latest_of: dict = {}
    for s in sessions or []:
        if s.get("flow"):
            f = str(s.get("flow"))
            if float(s.get("last_active") or 0) >= float((latest_of.get(f) or {}).get("last_active") or 0):
                latest_of[f] = s
    for s in sessions or []:
        sid = s.get("id")
        if not home._key(sid):
            continue
        last = float(s.get("last_active") or 0)
        raw_title = s.get("title") or s.get("name") or ""
        if s.get("flow") and latest_of.get(str(s.get("flow"))) is not s:
            continue
        if s.get("flow") and now - last <= 12 * 3600:
            # a routine: its name (the chat's, else its flow's words) and what its last run said
            flow = str(s.get("flow") or "").split("/")[-1].split(":")[-1]
            name = home.clip(raw_title, 60) if not is_raw(raw_title) and raw_title != flow else (
                re.sub(r"-?\d{4}-\d{2}-\d{2}$", "", flow).replace("-", " ").replace("_", " ").strip().capitalize() or "A routine")
            said = _line(str(sid))
            out.append({"source": f"session:{sid}", "title": name, "prior": 0.45, "people": [], "signal": f"ran {_ago(now - last)}",
                        "facts": "routine ran",
                        "card": {"type": "card", "id": f"rr-{_slug(sid)}", "kind": "result", "label": "Routine", "controls": False,
                                 "meta": _ago(now - last),
                                 "blocks": [{"type": "row", "icon": "repeat", "text": f"{name} · {said or 'ran'}",
                                             "action": {"type": "action", "label": "Open", "open": {"chat": sid}}}]}})
        elif not s.get("flow") and not is_raw(raw_title) and now - last <= 7 * 86400 and not str(sid).startswith("dm-"):
            recent.append((last, sid, home.clip(raw_title, 80)))
    for last, sid, title in sorted(recent, reverse=True)[:2]:
        said = _line(str(sid))
        out.append({"source": f"session:{sid}", "title": title, "prior": 0.25, "people": [], "signal": f"active {_ago(now - last)}",
                    "facts": "recent chat",
                    "card": {"type": "card", "id": f"c-{_slug(sid)}", "kind": "continue", "controls": False, "meta": _ago(now - last),
                             "blocks": [{"type": "title", "text": title},
                                        *([{"type": "text", "tone": "why", "text": said}] if said else []),
                                        {"type": "action", "label": "Continue", "primary": True, "open": {"chat": sid}}]}})
    for i, step in enumerate(home.next_steps(curriculum or [], facts or {}, refused or set(), snoozed or set())):
        blocks = [{"type": "row", "icon": step["icon"], "text": step["label"]}]
        if step["why"]:
            blocks.append({"type": "text", "tone": "why", "text": step["why"]})
        # a step whose act is a call starts one (a new chat, a one-person call), never a prompt
        blocks.append({"type": "action", "label": "Start a call", "primary": i == 0, "call": True} if step.get("act") == "call"
                      else {"type": "action", "label": "Show me", "primary": i == 0, "prompt": step["prompt"]})
        out.append({"source": f"curriculum:{step['id']}", "title": step["label"], "prior": 0.7 - 0.1 * i, "people": [],
                    "signal": "next onboarding step", "facts": f"onboarding {step['id']}",
                    "card": {"type": "card", "id": f"o-{step['id']}", "kind": "try", "label": "Get started" if (facts or {}).get("meetings", 0) == 0 else "Try",
                             "blocks": blocks}})
    extra, extra_people, _failed = from_sources({"owner": owner, "now": now, "sessions": sessions or [],
                                                 "colleagues": colleagues or {}, "facts": facts or {},
                                                 "events": [], "meetings": meetings or [], "waiting": waiting or [],
                                                 "states": states})
    out.extend(extra)
    people.update(extra_people)
    applied = []
    for c in out:
        refs = dict(c.get("refs") or {})
        st = states.get(c["card"]["id"])
        if st and st.get("chat"):
            refs["session"] = st["chat"]
        if st and st.get("page"):
            refs.setdefault("pages", []).append(st["page"])
        if refs:
            c["card"]["refs"] = refs
        c = apply_state(c, st)
        if c is not None:
            applied.append(c)
    return applied, {k: v for k, v in people.items() if v}


def facts_from(*, meetings: Optional[list], sessions: Optional[list], notes: Optional[list], events: list,
               first_seen: Optional[float], connected: set, now: float) -> dict:
    """The usage counts the stage and the curriculum's ``done when`` read — derived, never asked."""
    ms = [m for m in meetings or [] if str(m.get("status") or "") in _DONE]
    f = {
        "meetings": len(ms),
        "meetings_7d": sum(1 for m in ms if (_ts(m.get("end_time")) or 0) >= now - 7 * 86400),
        "voice_notes": sum(1 for m in ms if str(m.get("platform") or "") in ("voice_note", "voice-note", "upload")),
        "calls": sum(1 for m in ms if str(m.get("platform") or "") == "vexa_call"),
        "chats": sum(1 for s in sessions or [] if not s.get("flow")),
        "routines": len({s.get("flow") for s in sessions or [] if s.get("flow")}),
        "shared_chats": sum(1 for s in sessions or [] if int(s.get("participants") or 0) > 1 or s.get("shared")),
        "mentions": sum(1 for n in notes or [] if n.get("kind") == "chat_mentioned"),
        "workspaces": len({w for s in sessions or [] for w in (s.get("workspaces") or []) if isinstance(w, str)}),
        "crm_records": 0,
        "first_seen": first_seen or now,
        "last_visit": max((e["at"] for e in events if e["event"] == "shown"
                           or (e["event"] == "evidence" and e["card"].startswith("visit:"))), default=None),
    }
    for c in connected:
        f[f"connection:{c}"] = 1
    hours = [time.gmtime(float(s.get("last_active") or 0)).tm_hour for s in sessions or [] if s.get("last_active")]
    if hours:
        f["active_hours"] = sorted({h for h in hours})[:12]
    return f
