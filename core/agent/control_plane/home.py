"""home.py — the new-chat home: what to show a person when they open a new chat (#1783).

Founder-approved shape: ONE ranked stream, no fixed sections. A background flow (`home_compose`)
collects candidates from what agent-api can already see, ranks them — with at most ONE call to the
cheapest configured model (``home_model``), never per page view — and APPENDS a composition. The
page reads the latest composition and nothing else.

THREE CONTRACTS, all in ``core/agent/contracts``:

* ``home-card.v1`` — the ``view``: a greeting and cards built from primitives (title, text, person,
  row, list, evidence, checklist, action; group and attach). ``sanitize_view`` is the server half of
  the terminal's ``minutes/homeSpec.ts``: unknown blocks and props are dropped, text is capped, an
  action only sends a prompt, opens a chat/page by id, or opens a message to a person.
* ``home-customer.v1`` — the ``customer``: what the person's agent knows about them as a customer
  (stage and why, capabilities adopted, jobs, patterns, preferences, friction, open loops, health,
  next-best-actions with a hypothesis and a metric, the experiment log). Never rendered as cards; read
  by the person's own agent (``customer_profile``) and by the next compose. The person can correct it
  and a correction wins over anything inferred.

APPEND-ONLY. Every composition is a row (``agent_session_home_composition``) and every card's lifecycle is an
event (``agent_session_home_event``: shown, clicked, sent, dismissed+reason, snoozed, expired, done). Nothing
is overwritten: ranking learns from outcomes, the agent can answer "what did you suggest last week
and what came of it" (``home_history``), and suggestion quality can be evaluated offline. Rows carry
ids, titles and short reasons — never copied mail or transcript — plus, since the founder's UI-lab
pass (2026-10-14), the person's OWN short previews: one line of a chat's last answer and the first
lines of a finished meeting's note on their own desk (at most 160 characters each). Retention is the
company policy key ``home_history_retention_days`` (default ``forever``, like transcripts).

USER-SCOPED. Every read and write is ``WHERE owner = ?`` with the caller's own subject.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from control_plane import front_page, home_media, identity_media

VIEW_SPEC = "home-card.v1"
CUSTOMER_SPEC = "home-customer.v1"
KINDS = ("meeting", "waiting", "suggestion", "try", "result", "continue")
TONES = ("default", "suggested", "try", "muted")
ICONS = ("calendar", "mail", "chat", "clock", "repeat", "folder", "doc", "plane", "bot", "spark", "people", "mic")
LIMITS = {"cards": 7, "phoneCards": 5, "blocks": 12, "rows": 6, "items": 6, "actions": 4, "depth": 3,
          "people": 40, "short": 60, "text": 240, "prompt": 600}
EVENTS = ("shown", "clicked", "sent", "dismissed", "snoozed", "expired", "done", "evidence", "corrected",
          "state", "expanded")
#: Where an item has got to, advanced by EVIDENCE (a prep chat that exists, a meeting that started, the
#: agent's own `home_item_update` when the work finishes) — never by a click alone. Terminal states
#: take the item off the home.
STATES = ("open", "acted", "in_progress", "prepared", "live", "ended", "follow_up", "replied", "done")
TERMINAL_STATES = ("replied", "done")
REASONS = ("wrong", "done", "not_now")
STAGES = ("new", "activating", "active", "power", "drifting", "dormant")

#: The cadence: a composition is stale after this, or earlier when a meeting comes within the hour.
EVERY_S = 2 * 3600
MEETING_LEAD_S = 3600
#: The least time between two compositions (a meeting ending may come sooner: it is the moment).
DEBOUNCE_S = 10 * 60
AFTER_MEETING_DEBOUNCE_S = 60
#: How many times a day the model may rank one person's home, and how many elaborations they may
#: ask for, are the company's: `home_compose_per_day` and `home_expand_per_day` in
#: `_global/POLICIES.md` (`budgets`), read on every composition.
SNOOZE_S = 24 * 3600
#: A waiting item from a real person comes back after this even when dismissed — demoted, never
#: hidden for good (founder ruling on the mockup).
PERSON_RETURN_S = 24 * 3600
ONBOARDING_AT_ONCE = 2
POLICY_KEY = "home_history_retention_days"

# The tables (agent_session_home_composition, agent_session_home_event) are in the schema of record,
# ``session_store._DDL``, created and granted by the migrations path like every other.


# ── the view: home-card.v1 ────────────────────────────────────────────────────────────────────

_ID = re.compile(r"^[A-Za-z0-9._:@+-]{1,120}$")
_PAGE = re.compile(r"^(?!.*\.\.)(?![a-z]+:)[A-Za-z0-9 _./@+-]{1,200}$")
_EMAIL = re.compile(r"^[^\s@<>]{1,64}@[^\s@<>]{1,190}$")
_CTRL = re.compile(r"[\x00-\x1f\x7f]+")


def clip(v: Any, n: int) -> str:
    """Plain text: control characters out, whitespace collapsed, capped with an ellipsis."""
    if isinstance(v, bool) or not isinstance(v, (str, int, float)):
        return ""
    s = re.sub(r"\s+", " ", _CTRL.sub(" ", str(v))).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _key(v: Any) -> Optional[str]:
    return v if isinstance(v, str) and _ID.match(v) else None


def _one(v: Any, allowed: Iterable[str], default: Optional[str] = None) -> Optional[str]:
    return v if isinstance(v, str) and v in allowed else default


def _person(v: Any) -> Optional[dict]:
    if not isinstance(v, dict):
        return None
    name = clip(v.get("name"), LIMITS["short"])
    if not name:
        return None
    initials = clip(v.get("initials"), 3) or "".join(w[:1] for w in name.split()[:2]).upper()
    p = {"name": name, "initials": initials, "vexa": v.get("vexa") is True}
    for k in ("role", "last", "reach"):
        s = clip(v.get(k), LIMITS["short"])
        if s:
            p[k] = s
    if _key(v.get("account_id")):
        p["account_id"] = v["account_id"]
    if isinstance(v.get("email"), str) and _EMAIL.match(v["email"]):
        p["email"] = v["email"]
    for k in ("avatar", "logo"):
        if isinstance(v.get(k), str) and home_media.valid_key(v[k]):
            p[k] = v[k]
    # a Vexa user's photo (UI 15g): only ever the photo route of the account it names, never a URL
    # a card could carry
    if p.get("account_id") and v.get("photo") and v["photo"] == identity_media.photo_url(p["account_id"]):
        p["photo"] = v["photo"]
    if v.get("presence") in ("now", "today"):
        p["presence"] = v["presence"]
    return p


def _action(v: Any, people: dict) -> Optional[dict]:
    if not isinstance(v, dict):
        return None
    label = clip(v.get("label"), LIMITS["short"])
    if not label:
        return None
    a: dict = {"type": "action", "label": label}
    if v.get("primary") is True:
        a["primary"] = True
    if v.get("needs_go") is True:
        a["needs_go"] = True
    o = v.get("open") if isinstance(v.get("open"), dict) else {}
    chat, page = _key(o.get("chat")), o.get("page") if isinstance(o.get("page"), str) and _PAGE.match(o["page"]) else None
    prompt = clip(v.get("prompt"), LIMITS["prompt"])
    if isinstance(v.get("message"), str) and v["message"] in people:
        a["message"] = v["message"]
    elif chat or page:
        a["open"] = {"chat": chat} if chat else {"page": page}
    elif prompt:
        a["prompt"] = prompt
    elif v.get("expand") is True:
        a["expand"] = True
    elif v.get("call") is True:
        a["call"] = True
    else:
        return None
    return a


def _row(v: Any, people: dict) -> Optional[dict]:
    if not isinstance(v, dict):
        return None
    text = clip(v.get("text"), LIMITS["text"])
    if not text:
        return None
    r: dict = {"type": "row", "text": text}
    if _one(v.get("icon"), ICONS):
        r["icon"] = v["icon"]
    for k, n in (("meta", LIMITS["short"]), ("age", 12)):
        s = clip(v.get(k), n)
        if s:
            r[k] = s
    if v.get("hot") is True:
        r["hot"] = True
    if isinstance(v.get("person"), str) and v["person"] in people:
        r["person"] = v["person"]
    a = _action(v.get("action"), people)
    if a:
        r["action"] = a
    return r


def _block(v: Any, people: dict, depth: int) -> Optional[dict]:
    if not isinstance(v, dict):
        return None
    t = v.get("type")
    if t == "title":
        s = clip(v.get("text"), LIMITS["text"])
        return {"type": "title", "text": s} if s else None
    if t == "text":
        s = clip(v.get("text"), LIMITS["text"])
        if not s:
            return None
        tone = _one(v.get("tone"), ("why", "plain", "ok", "warn"))
        return {"type": "text", "text": s, **({"tone": tone} if tone else {})}
    if t == "person":
        ps = [k for k in (v.get("people") or []) if isinstance(k, str) and k in people][:6] \
            if isinstance(v.get("people"), list) else []
        extra = [clip(x, 30) for x in v.get("extra") or [] if clip(x, 30)][:3] if isinstance(v.get("extra"), list) else []
        if not ps and not extra:
            return None
        return {"type": "person", "people": ps, **({"extra": extra} if extra else {})}
    if t == "row":
        return _row(v, people)
    if t == "list":
        rows = [r for r in (_row(x, people) for x in (v.get("rows") or [] if isinstance(v.get("rows"), list) else [])) if r]
        return {"type": "list", "rows": rows[: LIMITS["rows"]]} if rows else None
    if t == "evidence":
        items = []
        for it in v.get("items") or [] if isinstance(v.get("items"), list) else []:
            if isinstance(it, dict) and clip(it.get("text"), LIMITS["text"]):
                items.append({"text": clip(it.get("text"), LIMITS["text"]), "source": clip(it.get("source"), 24) or "source"})
        return {"type": "evidence", "items": items[: LIMITS["items"]]} if items else None
    if t == "checklist":
        items = []
        for i, it in enumerate(v.get("items") or [] if isinstance(v.get("items"), list) else []):
            if not isinstance(it, dict) or not clip(it.get("text"), LIMITS["text"]):
                continue
            c = {"id": _key(it.get("id")) or f"i{i}", "text": clip(it.get("text"), LIMITS["text"]),
                 "checked": it.get("checked") is not False}
            if clip(it.get("meta"), 24):
                c["meta"] = clip(it.get("meta"), 24)
            items.append(c)
        if not items:
            return None
        out = {"type": "checklist", "items": items[: LIMITS["items"]]}
        a = _action(v.get("action"), people)
        if a:
            out["action"] = a
        return out
    if t == "action":
        return _action(v, people)
    if t == "group":
        if depth >= LIMITS["depth"]:
            return None
        bs = _blocks(v.get("blocks"), people, depth + 1)
        if not bs:
            return None
        g = {"type": "group", "blocks": bs}
        label, summary = clip(v.get("label"), LIMITS["short"]), clip(v.get("summary"), LIMITS["text"])
        if label:
            g["label"] = label
        if v.get("collapsed") is True:
            g["collapsed"] = True
        if summary:
            g["summary"] = summary
        return g
    if t == "attach":
        if depth >= LIMITS["depth"]:
            return None
        c = _card(v.get("card"), people, depth + 1, "attach")
        return {"type": "attach", "card": c} if c else None
    if t == "flow":
        return _flow_block(v.get("flow"))
    return None


_STEP_NAME = re.compile(r"^[a-z][a-z0-9_.]{0,59}$")


def _flow_block(v: Any) -> Optional[dict]:
    """A flow drawn read-only (blocks.v1 `flow`): name, a schedule or an event, at most 8 step names."""
    if not isinstance(v, dict):
        return None
    name = clip(v.get("name"), LIMITS["short"])
    t = v.get("trigger") if isinstance(v.get("trigger"), dict) else {}
    trigger = None
    if t.get("kind") == "schedule" and len(clip(t.get("cron"), LIMITS["short"]).split(" ")) == 5:
        trigger = {"kind": "schedule", "cron": clip(t.get("cron"), LIMITS["short"])}
    elif t.get("kind") == "event" and clip(t.get("event"), LIMITS["short"]):
        trigger = {"kind": "event", "event": clip(t.get("event"), LIMITS["short"])}
    steps = [x for x in (v.get("steps") if isinstance(v.get("steps"), list) else [])
             if isinstance(x, str) and _STEP_NAME.match(x)][:8]
    if not name or not trigger or not steps:
        return None
    flow = {"name": name, "trigger": trigger, "steps": steps}
    instruction, notify = clip(v.get("instruction"), LIMITS["prompt"]), clip(v.get("notify"), LIMITS["short"])
    if instruction:
        flow["instruction"] = instruction
    if notify:
        flow["notify"] = notify
    return {"type": "flow", "flow": flow}


def _blocks(v: Any, people: dict, depth: int) -> list:
    out, actions = [], 0
    for b in (v if isinstance(v, list) else [])[: LIMITS["blocks"] * 2]:
        s = _block(b, people, depth)
        if not s:
            continue
        if s["type"] == "action":
            actions += 1
            if actions > LIMITS["actions"]:
                continue
        out.append(s)
        if len(out) >= LIMITS["blocks"]:
            break
    return out


def _card(v: Any, people: dict, depth: int, fallback: str) -> Optional[dict]:
    if not isinstance(v, dict):
        return None
    blocks = _blocks(v.get("blocks"), people, depth)
    if not blocks:
        return None
    kind = _one(v.get("kind"), KINDS, "suggestion")
    tone = {"suggestion": "suggested", "try": "try", "continue": "muted", "result": "muted"}.get(kind, "default")
    c = {"type": "card", "id": _key(v.get("id")) or fallback, "kind": kind,
         "tone": _one(v.get("tone"), TONES, tone), "wide": v.get("wide") is True,
         "controls": v.get("controls") is not False, "blocks": blocks}
    for k in ("label", "meta"):
        s = clip(v.get(k), LIMITS["short"])
        if s:
            c[k] = s
    if isinstance(v.get("person"), str) and v["person"] in people:
        c["person"] = v["person"]
    if v.get("collapsed") is True:
        c["collapsed"] = True
    if clip(v.get("summary"), LIMITS["text"]):
        c["summary"] = clip(v.get("summary"), LIMITS["text"])
    if _one(v.get("state"), STATES):
        c["state"] = v["state"]
    res = v.get("result") if isinstance(v.get("result"), dict) else {}
    if _key(res.get("chat")):
        c["result"] = {"chat": res["chat"]}
    elif isinstance(res.get("page"), str) and _PAGE.match(res["page"]):
        c["result"] = {"page": res["page"]}
    refs = v.get("refs") if isinstance(v.get("refs"), dict) else {}
    rr: dict = {}
    if _key(refs.get("session")):
        rr["session"] = refs["session"]
    if _key(refs.get("meeting")):
        rr["meeting"] = refs["meeting"]
    ents = [x for x in refs.get("entities") or [] if isinstance(x, str) and _PAGE.match(x)][:4] if isinstance(refs.get("entities"), list) else []
    pages = [x for x in refs.get("pages") or [] if isinstance(x, str) and _PAGE.match(x)][:4] if isinstance(refs.get("pages"), list) else []
    urls = [x for x in refs.get("urls") or [] if isinstance(x, str) and re.match(r"^https://[^\s<>\"']{1,300}$", x)][:3] if isinstance(refs.get("urls"), list) else []
    if ents:
        rr["entities"] = ents
    if pages:
        rr["pages"] = pages
    if urls:
        rr["urls"] = urls
    if rr:
        c["refs"] = rr
    r = v.get("rank")
    if isinstance(r, dict) and isinstance(r.get("pos"), (int, float)) and isinstance(r.get("score"), (int, float)) \
            and not isinstance(r.get("pos"), bool):
        c["rank"] = {"pos": max(1, int(round(r["pos"]))), "score": max(0.0, min(1.0, float(r["score"])))}
        if clip(r.get("signal"), LIMITS["short"]):
            c["rank"]["signal"] = clip(r.get("signal"), LIMITS["short"])
    return c


def sanitize_view(raw: Any) -> Optional[dict]:
    """Anything → a home-card.v1 document that is safe to draw, or ``None``. Never raises."""
    try:
        if not isinstance(raw, dict) or raw.get("spec") != VIEW_SPEC:
            return None
        people = {}
        for k, v in list((raw.get("people") or {}).items() if isinstance(raw.get("people"), dict) else [])[: LIMITS["people"]]:
            p = _person(v) if _key(k) else None
            if p:
                people[k] = p
        cards, seen = [], set()
        for i, c in enumerate((raw.get("cards") if isinstance(raw.get("cards"), list) else [])[: LIMITS["cards"] * 2]):
            s = _card(c, people, 0, f"c{i}")
            if s and s["id"] not in seen:
                seen.add(s["id"])
                cards.append(s)
            if len(cards) >= LIMITS["cards"]:
                break
        g = raw.get("greeting") if isinstance(raw.get("greeting"), dict) else {}
        title = clip(g.get("title"), LIMITS["short"])
        if not title and not cards:
            return None
        doc = {"spec": VIEW_SPEC, "composed_at": clip(raw.get("composed_at"), 40),
               "greeting": {"title": title, "text": clip(g.get("text"), LIMITS["text"])},
               "cards": cards, "people": people}
        if clip(raw.get("left_out"), LIMITS["text"]):
            doc["left_out"] = clip(raw.get("left_out"), LIMITS["text"])
        return doc
    except Exception:  # noqa: BLE001 — a malformed composition costs the composition, never the page
        return None


def sanitize_fence(raw: Any) -> Optional[dict]:
    """A blocks.v1 fence ``{"v":1,"people":{…},"blocks":[…]}`` → safe, or ``None``."""
    try:
        if not isinstance(raw, dict) or raw.get("v", 1) != 1:
            return None
        people = {k: p for k, v in list((raw.get("people") or {}).items() if isinstance(raw.get("people"), dict) else [])[: LIMITS["people"]]
                  for p in [_person(v) if _key(k) else None] if p}
        blocks = _blocks(raw.get("blocks"), people, 0)
        return {"v": 1, "people": people, "blocks": blocks} if blocks else None
    except Exception:  # noqa: BLE001
        return None


def card_people(card: dict) -> list:
    out: list = []

    def add(k):
        if k and k not in out:
            out.append(k)

    add(card.get("person"))

    def walk(bs):
        for b in bs:
            if b["type"] == "person":
                for k in b["people"]:
                    add(k)
            elif b["type"] == "row":
                add(b.get("person"))
            elif b["type"] == "list":
                for r in b["rows"]:
                    add(r.get("person"))
            elif b["type"] == "group":
                walk(b["blocks"])

    walk(card.get("blocks") or [])
    return out


# ── the curriculum: what a new person is shown over time ─────────────────────────────────────

CURRICULUM_FILE = "onboarding.md"
#: The evidence vocabulary a step's ``done when`` may name. Each is a COUNT derived from usage —
#: never from clicking a card, because a card clicked is an intention, not a capability adopted.
EVIDENCE = ("meetings", "calls", "voice_notes", "workspaces", "shared_chats", "routines", "mentions",
            "chats", "crm_records", "connection:google_calendar", "connection:google_email")
_STEP = re.compile(r"^##\s+([a-z0-9][a-z0-9-]{0,40})\s*$")
_FIELD = re.compile(r"^-\s*(label|why|prompt|requires|done when|icon|stage|act)\s*:\s*(.*)$", re.I)


def parse_curriculum(text: Optional[str]) -> list:
    """`_global/onboarding.md` → the ordered steps. A step without a label, prompt or a ``done when``
    in the evidence vocabulary is skipped (the admin's file is read, never trusted to be whole)."""
    steps, cur = [], None
    for line in (text or "").splitlines():
        m = _STEP.match(line.strip())
        if m:
            cur = {"id": m.group(1)}
            steps.append(cur)
            continue
        f = _FIELD.match(line.strip()) if cur is not None else None
        if f:
            cur[f.group(1).lower().replace(" ", "_")] = f.group(2).strip()
    out = []
    for s in steps:
        done = [d.strip() for d in s.get("done_when", "").split(",") if d.strip()]
        conds = []
        for d in done:
            m = re.match(r"^([a-z_:]+)\s*>=\s*(\d+)$", d) or re.match(r"^(connection:[a-z_]+)$", d)
            if m and m.group(1) in EVIDENCE:
                conds.append((m.group(1), int(m.group(2)) if m.lastindex and m.lastindex > 1 else 1))
        label, prompt = clip(s.get("label"), LIMITS["short"]), clip(s.get("prompt"), LIMITS["prompt"])
        if not (label and prompt and conds):
            continue
        out.append({"id": s["id"], "label": label, "why": clip(s.get("why"), LIMITS["text"]), "prompt": prompt,
                    "requires": [r.strip() for r in s.get("requires", "").split(",") if r.strip()],
                    "done_when": conds, "icon": _one(s.get("icon"), ICONS, "spark"),
                    **({"act": "call"} if (s.get("act") or "").strip().lower() == "call" else {})})
    return out


def step_done(step: dict, facts: dict) -> bool:
    return all(int(facts.get(k, 0) or 0) >= n for k, n in step["done_when"])


def curriculum_progress(steps: list, facts: dict) -> dict:
    done = [s["id"] for s in steps if step_done(s, facts)]
    return {"adopted": done, "not_yet": [s["id"] for s in steps if s["id"] not in done]}


def next_steps(steps: list, facts: dict, refused: set, snoozed: set, n: int = ONBOARDING_AT_ONCE) -> list:
    """The next steps to offer: not done, prerequisites done, never one refused as wrong or done,
    one snoozed only after its snooze ran out (the caller passes the live snoozes)."""
    done = {s["id"] for s in steps if step_done(s, facts)}
    out = []
    for s in steps:
        if s["id"] in done or s["id"] in refused or s["id"] in snoozed:
            continue
        if any(r not in done for r in s["requires"]):
            continue
        out.append(s)
        if len(out) >= n:
            break
    return out


# ── the lifecycle stage ───────────────────────────────────────────────────────────────────────

def lifecycle(facts: dict, now: float) -> tuple[str, str]:
    """(stage, why) — DERIVED from usage, never stored by hand.

    new         first days, little connected
    activating  some sources connected or a first meeting, not yet a habit
    active      regular meetings and chats
    power       routines and shared work on top of many meetings
    drifting    no visit for 7 days; dormant after 21"""
    first = facts.get("first_seen") or now
    age_d = max(0.0, (now - first) / 86400)
    last = facts.get("last_visit")
    idle_d = (now - last) / 86400 if last else 0.0
    meetings, routines, shared = (int(facts.get(k, 0) or 0) for k in ("meetings", "routines", "shared_chats"))
    connected = sum(1 for k in ("connection:google_calendar", "connection:google_email") if facts.get(k))
    if last and idle_d >= 21:
        return "dormant", f"no visit for {int(idle_d)} days"
    if last and idle_d >= 7:
        return "drifting", f"no visit for {int(idle_d)} days"
    if routines >= 1 and (shared >= 1 or meetings >= 20):
        return "power", f"{routines} routine(s), {shared} shared chat(s), {meetings} meetings"
    if meetings >= 5 or (age_d >= 14 and meetings >= 2):
        return "active", f"{meetings} meetings over {int(age_d)} days"
    if meetings >= 1 or connected:
        return "activating", f"{meetings} meeting(s), {connected} source(s) connected"
    return "new", f"{int(age_d)} day(s) in, nothing connected or recorded yet"


#: How much each kind counts at each stage — the stage sets the mix.
STAGE_MIX = {
    "new": {"try": 1.4, "meeting": 1.0, "waiting": 1.0, "suggestion": 0.6, "result": 0.6, "continue": 0.4},
    "activating": {"try": 1.2, "meeting": 1.1, "waiting": 1.0, "suggestion": 0.9, "result": 0.9, "continue": 0.6},
    "active": {"try": 0.8, "meeting": 1.1, "waiting": 1.1, "suggestion": 1.0, "result": 1.0, "continue": 0.8},
    "power": {"try": 0.6, "meeting": 1.1, "waiting": 1.1, "suggestion": 1.2, "result": 0.9, "continue": 0.7},
    "drifting": {"try": 0.5, "meeting": 1.0, "waiting": 1.2, "suggestion": 1.0, "result": 1.1, "continue": 0.5},
    "dormant": {"try": 0.4, "meeting": 1.0, "waiting": 1.2, "suggestion": 0.9, "result": 1.2, "continue": 0.4},
}


# ── feedback → weights ────────────────────────────────────────────────────────────────────────

_DELTA = {("clicked", ""): 0.10, ("sent", ""): 0.15, ("done", ""): 0.05,
          ("dismissed", "wrong"): -0.25, ("dismissed", "done"): -0.05, ("dismissed", "not_now"): -0.05,
          ("dismissed", ""): -0.10, ("snoozed", ""): -0.02, ("expired", ""): -0.03}


def weights(events: list) -> dict:
    """Per kind and per person multipliers in [0.3, 1.6], from every outcome the person produced."""
    kind: dict = {}
    person: dict = {}
    for e in events:
        d = _DELTA.get((e["event"], e.get("reason") or ""), _DELTA.get((e["event"], ""), 0.0))
        if not d:
            continue
        if e.get("kind"):
            kind[e["kind"]] = kind.get(e["kind"], 1.0) + d
        for p in e.get("people") or []:
            person[p] = person.get(p, 1.0) + d
    clamp = lambda x: round(max(0.3, min(1.6, x)), 3)  # noqa: E731
    return {"kind": {k: clamp(v) for k, v in kind.items()}, "person": {k: clamp(v) for k, v in person.items()}}


def suppressed(events: list, now: float) -> dict:
    """Which card ids must not show now, and why. A refusal (wrong/done) holds for good; not-now and
    a snooze hold 24 h. A waiting card that names a real person is NEVER held for good: a refusal on
    it holds 24 h, then it returns, demoted by the weights."""
    held: dict = {}
    for e in events:
        cid, ev, reason, age = e["card"], e["event"], e.get("reason") or "", now - float(e["at"])
        person_waiting = e.get("kind") == "waiting" and bool(e.get("people"))
        if ev == "snoozed" or (ev == "dismissed" and reason in ("not_now", "")):
            if age < SNOOZE_S:
                held[cid] = "snoozed"
        elif ev == "dismissed" and reason in ("wrong", "done"):
            if not person_waiting:
                held[cid] = reason
            elif age < PERSON_RETURN_S:
                held[cid] = reason
    return held


# ── composing ─────────────────────────────────────────────────────────────────────────────────

def _score(c: dict, w: dict, stage: str) -> float:
    s = float(c.get("prior", 0.5))
    s *= STAGE_MIX.get(stage, STAGE_MIX["active"]).get(c["card"]["kind"], 1.0)
    s *= w["kind"].get(c["card"]["kind"], 1.0)
    ps = c.get("people") or []
    if ps:
        pw = min(w["person"].get(p, 1.0) for p in ps)
        if c["card"]["kind"] == "waiting":
            pw = max(pw, 0.5)  # demoted, never buried
        s *= pw
    return round(max(0.0, min(1.0, s)), 3)


_KIND_WORDS = {"meeting": "meetings", "waiting": "replies waiting", "suggestion": "suggestions",
               "try": "things to try", "result": "results", "continue": "recent chats"}


def first_name(name: str) -> str:
    """The name a greeting uses: the first word of a person's name, never an address or a handle."""
    n = re.sub(r"@.*$", "", str(name or "")).strip()
    n = re.split(r"[\s._-]+", n)[0] if n else ""
    return n[:1].upper() + n[1:] if n and not n.isdigit() else ""


def part_of_day(hour: Optional[int]) -> str:
    if hour is None:
        return ""
    return "morning" if 5 <= hour < 12 else "afternoon" if 12 <= hour < 18 else "evening"


def greeting(stage: str, cards: list, name: str, now_local: str, hour: Optional[int] = None) -> dict:
    """THE GREETING (founder, live app.dev: "Hello, there" — "this makes no sense"): the person's
    first name, never "there"; "Good morning/afternoon/evening" when their hour is known."""
    meetings = sum(1 for c in cards if c["kind"] == "meeting")
    waiting = sum(1 for c in cards if c["kind"] == "waiting")
    who = first_name(name)
    day = part_of_day(hour)
    hello = (f"Welcome, {who}" if stage == "new" else f"Welcome back, {who}" if stage in ("drifting", "dormant")
             else f"Good {day}, {who}" if day else f"Hello, {who}")
    parts = []
    if meetings:
        parts.append(f"{meetings} meeting{'s' if meetings > 1 else ''} coming up")
    if waiting:
        parts.append(f"{waiting} thing{'s' if waiting > 1 else ''} waiting on you")
    if stage == "new" and not parts:
        parts.append("Here is where to start")
    if not parts:
        parts.append("Nothing urgent")
    return {"title": clip(hello.strip(", "), LIMITS["short"]),
            "text": clip(f"{now_local} · " + ", ".join(parts) + ".", LIMITS["text"])}


def arrange(candidates: list, events: list, stage: str, now: float, *, ranker: Optional[Callable] = None,
            name: str = "", now_local: str = "", people: Optional[dict] = None,
            mixer: Optional[Callable] = None, hour: Optional[int] = None) -> tuple[dict, dict]:
    """Candidates → (view, ranking facts). The deterministic score orders everything; a ``ranker``
    (the one model call) may re-order the top, mark cards wide, nest one inside another and word the
    greeting — it never writes a card. Whatever it returns is validated against the ids it was given."""
    w = weights(events)
    held = suppressed(events, now)
    pool = []
    for c in candidates:
        if c["card"]["id"] in held:
            continue
        pool.append({**c, "score": _score(c, w, stage)})
    pool.sort(key=lambda c: -c["score"])
    mix_record: dict = {}
    if mixer is not None:
        pool, mix_record = mixer(pool)
    pool = pool[:12]
    order = [c["card"]["id"] for c in pool]
    wide: set = {c["card"]["id"] for c in pool[:1] if c["card"]["kind"] in ("meeting", "result")}
    attach: dict = {}
    g = None
    left_out = ""
    signals: dict = {}
    model_used = ""
    if ranker and pool:
        try:
            out = ranker([{"id": c["card"]["id"], "kind": c["card"]["kind"], "title": c.get("title", ""),
                           "score": c["score"], "facts": c.get("facts", "")} for c in pool], stage)
        except Exception:  # noqa: BLE001 — the deterministic order stands
            out = None
        if isinstance(out, dict):
            known = set(order)
            o = [i for i in out.get("order") or [] if i in known]
            if o:
                order = o + [i for i in order if i not in o]
            wide = {i for i in out.get("wide") or [] if i in known} or wide
            for pair in out.get("attach") or []:
                if isinstance(pair, list) and len(pair) == 2 and pair[0] in known and pair[1] in known and pair[0] != pair[1]:
                    attach[pair[1]] = pair[0]
            if isinstance(out.get("greeting"), dict):
                g = {"title": clip(out["greeting"].get("title"), LIMITS["short"]),
                     "text": clip(out["greeting"].get("text"), LIMITS["text"])}
                if not g["title"]:
                    g = None
            left_out = clip(out.get("left_out"), LIMITS["text"])
            signals = {k: clip(v, LIMITS["short"]) for k, v in (out.get("signals") or {}).items() if k in known}
            model_used = clip(out.get("_model"), 80)
    by_id = {c["card"]["id"]: c for c in pool}
    cards: list = []
    for cid in order:
        if cid in attach and attach[cid] in by_id:
            continue
        c = by_id[cid]
        card = json.loads(json.dumps(c["card"]))
        card["wide"] = cid in wide
        card["rank"] = {"pos": len(cards) + 1, "score": c["score"]}
        sig = signals.get(cid) or c.get("signal")
        if sig:
            card["rank"]["signal"] = clip(sig, LIMITS["short"])
        for child, parent in attach.items():
            if parent == cid and child in by_id:
                inner = json.loads(json.dumps(by_id[child]["card"]))
                inner.pop("rank", None)
                card["blocks"].append({"type": "attach", "card": inner})
        cards.append(card)
        if len(cards) >= LIMITS["cards"]:
            break
    shown = {c["id"] for c in cards}
    missing = sorted({_KIND_WORDS[k] for k in KINDS} - {_KIND_WORDS[c["kind"]] for c in cards})
    if not left_out and missing:
        left_out = "not shown now: " + ", ".join(missing[:3]) + "."
    view = {"spec": VIEW_SPEC, "composed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "greeting": g or greeting(stage, cards, name, now_local, hour), "cards": cards,
            "people": people or {}}
    # NO "Not shown now: …" FOOTER (founder, live home): what is left out is not shown at all
    clean = sanitize_view(view) or {"spec": VIEW_SPEC, "composed_at": view["composed_at"],
                                    "greeting": greeting(stage, [], name, now_local, hour), "cards": [], "people": {}}
    return clean, {"weights": w, "held": held, "considered": [c["card"]["id"] for c in pool],
                   "shown": sorted(shown), "model": model_used, "mix": mix_record}


# ── the customer: home-customer.v1 ───────────────────────────────────────────────────────────

def _field(value, evidence: list, confidence: float, now: float) -> dict:
    return {"value": value, "evidence": [clip(e, 120) for e in evidence][:12],
            "confidence": round(max(0.0, min(1.0, confidence)), 2),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))}


def update_customer(prev: Optional[dict], *, facts: dict, stage: tuple, progress: dict, events: list,
                    w: dict, candidates: list, view: dict, now: float, jobs: Optional[list] = None,
                    corrections: Optional[dict] = None, mix: Optional[dict] = None) -> dict:
    """The previous profile, updated from new evidence and outcomes. Every field carries its
    evidence ids, a confidence and when it changed; ``changes`` says what moved and why. A person's
    correction WINS: a corrected field keeps the person's value until they correct it again."""
    prev = prev if isinstance(prev, dict) and prev.get("spec") == CUSTOMER_SPEC else {}
    f = dict(prev.get("fields") or {})
    new: dict = {}
    new["stage"] = _field({"stage": stage[0], "why": stage[1]}, ["facts:usage"], 0.8, now)
    new["capabilities"] = _field(progress, [f"curriculum:{i}" for i in progress.get("adopted", [])], 0.9, now)
    new["patterns"] = _field({k: facts.get(k) for k in ("meetings", "meetings_7d", "chats", "routines",
                                                         "shared_chats", "active_hours") if facts.get(k) is not None},
                             ["facts:usage"], 0.7, now)
    acted = [e for e in events if e["event"] in ("clicked", "sent", "done")]
    refused = [e for e in events if e["event"] == "dismissed"]
    new["preferences"] = _field({"weights": w, "acted": len(acted), "dismissed": len(refused)},
                                [f"event:{e['id']}" for e in (acted + refused)[-8:]], 0.6 if events else 0.2, now)
    loops = [{"card": c["card"]["id"], "title": c.get("title", "")} for c in candidates if c["card"]["kind"] == "waiting"]
    new["open_loops"] = _field(loops[:10], [c["card"]["id"] for c in candidates if c["card"]["kind"] == "waiting"][:10], 0.7, now)
    frictions = facts.get("friction") or []
    new["friction"] = _field(frictions[:10], [x.get("id", "") for x in frictions[:10] if isinstance(x, dict)], 0.6, now)
    health = "drifting" if stage[0] in ("drifting", "dormant") else ("blocked" if frictions else "healthy")
    new["health"] = _field({"state": health, "why": stage[1] if health == "drifting" else ""}, ["facts:usage"], 0.6, now)
    if jobs is not None:
        new["jobs"] = _field([{"job": clip(j.get("job"), 120), "evidence": [x for x in j.get("evidence", []) if isinstance(x, str)][:5]}
                              for j in jobs if isinstance(j, dict) and clip(j.get("job"), 120)][:8],
                             [x for j in jobs if isinstance(j, dict) for x in j.get("evidence", []) if isinstance(x, str)][:12], 0.5, now)
    nba = [{"card": c["id"], "kind": c["kind"], "hypothesis": (c.get("rank") or {}).get("signal", "ranked high now"),
            "metric": "acted on (clicked, sent or done) within 48 h"} for c in view.get("cards", [])[:3]]
    new["next_best_actions"] = _field(nba, [n["card"] for n in nba], 0.5, now)
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    before = ((f.get("stage") or {}).get("value") or {}).get("stage")
    trans = list((f.get("transitions") or {}).get("value") or [])
    if before and before != stage[0]:
        trans.append({"from": before, "to": stage[0], "at": at, "why": stage[1]})
    if trans:
        new["transitions"] = _field(trans[-20:], ["facts:usage"], 0.8, now)
    log = list((f.get("experiments") or {}).get("value") or [])
    log.append({"at": at, "shown": [c["id"] for c in view.get("cards", [])], "stage": stage[0],
                "mix": (mix or {}).get("chosen", {}), "result": f"{len(acted)} acted, {len(refused)} dismissed so far"})
    new["experiments"] = _field(log[-20:], [], 0.9, now)
    corrections = corrections or prev.get("corrections") or {}
    changes = []
    for k, v in new.items():
        if k in corrections:
            continue
        old = (f.get(k) or {}).get("value")
        if old != v["value"] and k != "experiments":
            changes.append({"field": k, "why": "new evidence" if old is not None else "first seen"})
        f[k] = v
    for k, v in corrections.items():
        f[k] = {"value": v.get("value"), "evidence": ["corrected by the person"], "confidence": 1.0,
                "updated_at": v.get("at", "")}
    return {"spec": CUSTOMER_SPEC, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "fields": f, "corrections": corrections, "changes": changes[-20:]}


# ── the store ─────────────────────────────────────────────────────────────────────────────────

class HomeStore:
    """The compositions and the events, in the session store's database (its own role, its lock).
    Every query is keyed by the owner; no method takes another person's id from a caller."""

    def __init__(self, store, clock: Callable[[], float] = time.time) -> None:
        self._s, self._clock = store, clock

    def append(self, owner: str, *, trigger: str, model: str, stage: str, inputs: dict, view: dict,
               customer: dict, next_due: float) -> dict:
        cid = f"hc-{uuid.uuid4().hex[:16]}"
        now = self._clock()
        with self._s._tx():
            self._s._x("INSERT INTO agent_session_home_composition (id, owner, composed_at, trigger, model, stage, inputs, view, "
                       "customer, next_due) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (cid, owner, now, clip(trigger, 40), clip(model, 80), stage, json.dumps(inputs),
                        json.dumps(view), json.dumps(customer), float(next_due)))
        return {"id": cid, "composed_at": now}

    def latest(self, owner: str) -> Optional[dict]:
        with self._s._lock:
            r = self._s._x("SELECT id, composed_at, trigger, model, stage, inputs, view, customer, next_due "
                           "FROM agent_session_home_composition WHERE owner = ? ORDER BY composed_at DESC LIMIT 1",
                           (owner,)).fetchone()
        if not r:
            return None
        return {"id": r[0], "composed_at": float(r[1]), "trigger": r[2], "model": r[3], "stage": r[4],
                "inputs": json.loads(r[5] or "{}"), "view": json.loads(r[6] or "{}"),
                "customer": json.loads(r[7] or "{}"), "next_due": float(r[8])}

    def count_since(self, owner: str, since: float, *, with_model: bool = False) -> int:
        q = "SELECT COUNT(*) FROM agent_session_home_composition WHERE owner = ? AND composed_at >= ?"
        if with_model:
            q += " AND model <> ''"
        with self._s._lock:
            return int(self._s._x(q, (owner, since)).fetchone()[0])

    def record(self, owner: str, *, card: str, event: str, kind: str = "", reason: str = "",
               people: Iterable[str] = (), chat: str = "", title: str = "", composition: str = "",
               state: str = "", page: str = "") -> dict:
        if event not in EVENTS:
            raise ValueError(f"unknown event {event!r}")
        if reason and reason not in REASONS:
            raise ValueError(f"unknown reason {reason!r}")
        if state and state not in STATES:
            raise ValueError(f"unknown state {state!r}")
        if page and not _PAGE.match(page):
            raise ValueError("not a page path")
        eid, now = f"he-{uuid.uuid4().hex[:16]}", self._clock()
        ps = [p for p in people if isinstance(p, str) and _ID.match(p)][:6]
        with self._s._tx():
            self._s._x("INSERT INTO agent_session_home_event (id, owner, composition, card, kind, event, reason, people, chat, "
                       "title, state, page, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (eid, owner, composition, clip(card, 120), kind if kind in KINDS else "", event, reason,
                        json.dumps(ps), clip(chat, 120), clip(title, 120), state, page, now))
        return {"id": eid, "card": card, "event": event, "state": state, "chat": chat, "page": page, "at": now}

    def events(self, owner: str, *, since: float = 0.0, limit: int = 2000) -> list:
        with self._s._lock:
            rows = self._s._x("SELECT id, composition, card, kind, event, reason, people, chat, title, state, page, at "
                              "FROM agent_session_home_event WHERE owner = ? AND at >= ? ORDER BY at ASC LIMIT ?",
                              (owner, since, int(limit))).fetchall()
        return [{"id": r[0], "composition": r[1], "card": r[2], "kind": r[3], "event": r[4], "reason": r[5],
                 "people": json.loads(r[6] or "[]"), "chat": r[7], "title": r[8], "state": r[9], "page": r[10],
                 "at": float(r[11])} for r in rows]

    def compositions(self, owner: str, *, since: float = 0.0, limit: int = 50) -> list:
        with self._s._lock:
            rows = self._s._x("SELECT id, composed_at, trigger, model, stage, view FROM agent_session_home_composition "
                              "WHERE owner = ? AND composed_at >= ? ORDER BY composed_at DESC LIMIT ?",
                              (owner, since, int(limit))).fetchall()
        out = []
        for r in rows:
            v = json.loads(r[5] or "{}")
            out.append({"id": r[0], "composed_at": float(r[1]), "trigger": r[2], "model": r[3], "stage": r[4],
                        "cards": [{"id": c.get("id"), "kind": c.get("kind"), "title": _title(c)} for c in v.get("cards", [])]})
        return out

    def last_of(self, owner: str, cards: Iterable[str]) -> Optional[str]:
        """Which of these evidence cards this person recorded last (e.g. presence:on / presence:off)."""
        cs = list(cards)
        q = "SELECT card FROM agent_session_home_event WHERE owner = ? AND event = 'evidence' AND card IN (%s) ORDER BY at DESC LIMIT 1" % \
            ", ".join("?" for _ in cs)
        with self._s._lock:
            r = self._s._x(q, (owner, *cs)).fetchone()
        return r[0] if r else None

    def first_seen(self, owner: str) -> Optional[float]:
        with self._s._lock:
            r = self._s._x("SELECT MIN(composed_at) FROM agent_session_home_composition WHERE owner = ?", (owner,)).fetchone()
        return float(r[0]) if r and r[0] is not None else None

    def prune(self, owner: str, days: Optional[int]) -> int:
        """Retention: drop this person's rows older than ``days`` (``None`` = forever)."""
        if not days:
            return 0
        cut = self._clock() - days * 86400
        with self._s._tx():
            a = self._s._x("DELETE FROM agent_session_home_composition WHERE owner = ? AND composed_at < ?", (owner, cut)).rowcount
            b = self._s._x("DELETE FROM agent_session_home_event WHERE owner = ? AND at < ?", (owner, cut)).rowcount
        return int(a or 0) + int(b or 0)


def item_states(events: list) -> dict:
    """Each item's latest state, from the evidence events: ``home_item_update`` (``state``) and a
    prompt sent from the card into a chat (``sent`` → ``acted`` with that chat)."""
    out: dict = {}
    for e in events:
        if e["event"] == "state" and e.get("state"):
            prev = out.get(e["card"], {})
            out[e["card"]] = {"state": e["state"], "chat": e.get("chat") or prev.get("chat", ""),
                              "page": e.get("page") or prev.get("page", ""), "at": e["at"]}
        elif e["event"] == "sent" and e.get("chat"):
            prev = out.get(e["card"], {})
            if prev.get("state") not in TERMINAL_STATES + ("prepared", "live", "ended", "follow_up"):
                out[e["card"]] = {"state": "acted", "chat": e["chat"], "page": prev.get("page", ""), "at": e["at"]}
    return out


def _title(card: dict) -> str:
    for b in card.get("blocks") or []:
        if b.get("type") in ("title", "row", "text"):
            return clip(b.get("text"), 120)
        if b.get("type") == "list" and b.get("rows"):
            return clip(b["rows"][0].get("text"), 120)
    return clip(card.get("label") or card.get("kind"), 120)


def read_global(global_dir: Optional[Path], name: str) -> str:
    """A `_global` file the home reads (POLICIES.md, onboarding.md, lifecycle.md), through
    `workspace_paths` — and the image's seed copy when this `_global` does not have it yet."""
    from control_plane import global_seed  # noqa: PLC0415
    from workspaces.shared import workspace_paths as wpaths  # noqa: PLC0415
    for root in (global_dir, global_seed.image_seed_dir()):
        if root is None or not Path(root).is_dir():
            continue
        text = wpaths.read_text_inside(root, name, max_bytes=1_000_000)
        if text:
            return text
    return ""


def retention_days(global_dir: Optional[Path]) -> Optional[int]:
    """``home_history_retention_days`` from `_global/POLICIES.md`: a whole number of days, or
    ``forever`` (the default). A value that cannot be read keeps everything — the safe direction for
    a history the person can always ask to see."""
    raw = read_global(global_dir, "POLICIES.md")
    v = str(front_page.front_matter(raw).get(POLICY_KEY, "")).strip().lower()
    return int(v) if v.isdigit() and int(v) > 0 else None


def history(hs: HomeStore, owner: str, *, days: int = 7, now: Optional[float] = None) -> dict:
    """What was suggested and what came of it, over ``days``: each card once, with its outcome."""
    now = now if now is not None else time.time()
    since = now - max(1, min(days, 365)) * 86400
    comps = hs.compositions(owner, since=since, limit=200)
    evs = hs.events(owner, since=since)
    cards: dict = {}
    for c in reversed(comps):
        for card in c["cards"]:
            cards.setdefault(card["id"], {**card, "first_shown": c["composed_at"], "outcomes": []})
    for e in evs:
        if e["event"] in ("shown", "evidence"):
            continue
        if e["event"] == "state":
            row = cards.setdefault(e["card"], {"id": e["card"], "kind": e["kind"], "title": e["title"], "outcomes": []})
            row["outcomes"].append({k: e[k] for k in ("event", "state", "chat", "page", "at") if e.get(k)})
            continue
        row = cards.setdefault(e["card"], {"id": e["card"], "kind": e["kind"], "title": e["title"], "outcomes": []})
        row["outcomes"].append({k: e[k] for k in ("event", "reason", "chat", "at") if e[k]})
    return {"days": days, "compositions": len(comps), "cards": list(cards.values())[-100:]}
