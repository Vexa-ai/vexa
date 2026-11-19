"""home_playbook.py — the lifecycle playbook and the purpose mix of the home (#1783).

Founder, 2026-10-13: *"probably it's a lot about user lifecycle"* and *"distribute the surface across
a few purposes — teaching is one of them"*. Two things live here:

* THE PLAYBOOK — `_global/lifecycle.md`, admin-editable data: stages checked top-down by evidence
  (`enter when`), each with its goal, the purpose weights of the home, the curriculum steps in scope,
  nudge caps and a success metric. A file that cannot be read falls back to the seeded copy in the
  image, then to `home.lifecycle`'s built-in rules — the home never fails on a bad edit.
* THE MIX — every card has one PURPOSE (work, people, automation, connections, teaching). The stage's
  weights tilt the scores; the nudge caps bound each non-work purpose; when there is room, at least
  one card is not work. What was chosen, and why, is returned for the history and the customer model,
  so later agents can tune the weights from outcomes.

Also here: the discovery sources the founder asked for — AUTOMATIONS (the same thing done by hand three
times becomes a routine draft, with its cost against the daily run limit), CONNECTIONS (a tool named
in chats that is not connected) and TEACHING tied to the person's own context. Each yields candidates
in the shape `home_sources` documents.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Optional

from control_plane import home

PURPOSES = ("work", "people", "automation", "connections", "teaching")
FILE = "lifecycle.md"
_STAGE = re.compile(r"^##\s+([a-z][a-z0-9-]{0,30})\s*$")
_FIELD = re.compile(r"^-\s*(enter when|goal|weights|curriculum|nudges|success)\s*:\s*(.*)$", re.I)
_COND = re.compile(r"^([a-z_0-9]+)\s*(>=|<)\s*(\d+)$")
FACTS = ("meetings", "meetings_7d", "chats", "routines", "shared_chats", "workspaces", "mentions", "voice_notes",
         "connected", "age_days", "idle_days")


def parse(text: Optional[str]) -> list:
    stages, cur = [], None
    for line in (text or "").splitlines():
        m = _STAGE.match(line.strip())
        if m:
            cur = {"id": m.group(1), "enter": [], "weights": {}, "curriculum": None, "nudges": {}, "goal": "", "success": ""}
            stages.append(cur)
            continue
        f = _FIELD.match(line.strip()) if cur is not None else None
        if not f:
            continue
        key, val = f.group(1).lower(), f.group(2).strip()
        if key == "enter when":
            for part in [p.strip() for p in val.split(",") if p.strip()]:
                c = _COND.match(part)
                if c and c.group(1) in FACTS:
                    cur["enter"].append((c.group(1), c.group(2), int(c.group(3))))
                else:
                    cur["enter"].append(("__invalid__", ">=", 1))   # a condition nobody can meet
        elif key == "weights":
            for part in val.split(","):
                bits = part.split()
                if len(bits) == 2 and bits[0] in PURPOSES:
                    try:
                        cur["weights"][bits[0]] = max(0.0, float(bits[1]))
                    except ValueError:
                        pass
        elif key == "nudges":
            for part in val.split(","):
                bits = part.split()
                if len(bits) == 2 and bits[0] in PURPOSES and bits[1].isdigit():
                    cur["nudges"][bits[0]] = int(bits[1])
        elif key == "curriculum":
            v = val.strip().lower()
            cur["curriculum"] = "all" if v == "all" else [] if v in ("none", "") else [x.strip() for x in v.split(",") if x.strip()]
        else:
            cur[key] = home.clip(val, 240)
    return [s for s in stages if s["weights"]]


def read(global_dir: Optional[Path]) -> list:
    """The playbook from `_global` (or the image's seed copy); ``[]`` falls back to built-in rules."""
    return parse(home.read_global(global_dir, FILE))


def stage_facts(facts: dict, now: float) -> dict:
    out = {k: int(facts.get(k, 0) or 0) for k in FACTS if k in facts}
    out["connected"] = sum(1 for k in ("connection:google_calendar", "connection:google_email") if facts.get(k))
    out["age_days"] = int(max(0.0, now - float(facts.get("first_seen") or now)) // 86400)
    last = facts.get("last_visit")
    out["idle_days"] = int(max(0.0, now - float(last)) // 86400) if last else 0
    return out


def stage_of(stages: list, facts: dict, now: float) -> tuple[Optional[dict], str]:
    """The first stage whose `enter when` all hold, and the evidence, in words."""
    f = stage_facts(facts, now)
    for s in stages:
        ok = all((f.get(k, 0) >= n) if op == ">=" else (f.get(k, 0) < n) for k, op, n in s["enter"])
        if ok:
            why = ", ".join(f"{k} {f.get(k, 0)}" for k, _op, _n in s["enter"]) or "no other stage applies"
            return s, why
    return None, ""


# ── purposes and the mix ──────────────────────────────────────────────────────────────────────

def purpose_of(c: dict) -> str:
    if c.get("purpose") in PURPOSES:
        return c["purpose"]
    src = str(c.get("source") or "")
    if src.startswith(("curriculum:", "teach:")):
        return "teaching"
    if src.startswith("automation:"):
        return "automation"
    if src.startswith("connect:"):
        return "connections"
    if c.get("people") or src.startswith(("people", "notification:")) or str(c["card"]["id"]).startswith("d-"):
        return "people"
    return "work"


def mix(cards: list, stage: Optional[dict], slots: int = home.LIMITS["cards"]) -> tuple[list, dict]:
    """Order and trim scored candidates to the stage's purpose mix. Returns (chosen, the mix record)."""
    weights = (stage or {}).get("weights") or {"work": 1, "people": 0.5, "automation": 0.3, "connections": 0.3, "teaching": 0.3}
    caps = dict((stage or {}).get("nudges") or {})
    caps.setdefault("teaching", 2)
    caps["teaching"] = min(caps["teaching"], 2)              # never more than two teaching cards
    top = max(weights.values() or [1]) or 1
    for c in cards:
        c["purpose"] = purpose_of(c)
        c["score"] = round(c["score"] * (0.5 + 0.5 * weights.get(c["purpose"], 0) / top), 3)
    ranked = sorted(cards, key=lambda c: -c["score"])
    chosen, count, skipped = [], Counter(), []
    for c in ranked:
        p = c["purpose"]
        if p != "work" and p in caps and count[p] >= caps[p]:
            skipped.append((c["card"]["id"], f"{p} cap {caps[p]}"))
            continue
        if p != "work" and weights.get(p, 0) == 0:
            skipped.append((c["card"]["id"], f"{p} weight 0 at this stage"))
            continue
        chosen.append(c)
        count[p] += 1
        if len(chosen) >= slots:
            break
    # at least one card that is not work, when there is room for four or more
    if len(chosen) >= 4 and all(c["purpose"] == "work" for c in chosen):
        other = next((c for c in ranked if c["purpose"] != "work" and c not in chosen
                      and not (c["purpose"] in caps and caps[c["purpose"]] == 0) and weights.get(c["purpose"], 0) > 0), None)
        if other:
            chosen[-1] = other
            count = Counter(c["purpose"] for c in chosen)
    record = {"stage": (stage or {}).get("id", ""), "weights": weights, "caps": caps,
              "chosen": {k: v for k, v in Counter(c["purpose"] for c in chosen).items()},
              "skipped": skipped[:12]}
    return chosen, record


# ── discovery: automations, connections, teaching ────────────────────────────────────────────

_NORM = re.compile(r"[^a-z0-9 ]+")
TOOLS = {"slack": "Slack", "discord": "Discord", "github": "GitHub", "google drive": "Google Drive", "drive": "Google Drive",
         "outlook": "Outlook", "notion": "Notion", "jira": "Jira", "linear": "Linear", "hubspot": "HubSpot",
         "salesforce": "Salesforce", "gmail": "Gmail", "calendar": "Google Calendar"}
PROVIDER = {"Gmail": "google_email", "Google Calendar": "google_calendar"}


def _norm(t: str) -> str:
    return " ".join(_NORM.sub(" ", (t or "").lower()).split())


def discover(*, now: float, sessions: list, meetings: list, connected: set, routines_today: int = 0,
             runs_per_day: int = 0) -> list:
    """Automation, connection and teaching candidates from the person's own usage."""
    out: list = []
    recent = [s for s in sessions or [] if not s.get("flow") and now - float(s.get("last_active") or 0) <= 14 * 86400]
    # AUTOMATIONS: the same chat started by hand three times or more
    by_title = Counter(_norm(s.get("title") or "") for s in recent if (s.get("title") or "").strip())
    for title, n in by_title.most_common(3):
        if n < 3 or len(title) < 4 or title in ("new chat", "chat"):
            continue
        shown = next(s.get("title") for s in recent if _norm(s.get("title") or "") == title)
        slug = re.sub(r"[^a-z0-9]+", "-", title)[:40].strip("-")
        cost = f"1 run a day{f' of your {runs_per_day}' if runs_per_day else ''}"
        out.append({"source": f"automation:{slug}", "purpose": "automation", "title": f"Make “{shown}” a routine",
                    "prior": 0.55 + min(0.2, 0.03 * n), "people": [], "signal": f"done by hand {n}× in two weeks",
                    "card": {"type": "card", "id": f"a-{slug}", "kind": "try", "label": "Automate", "blocks": [
                        {"type": "row", "icon": "repeat", "text": home.clip(f"Make “{shown}” a routine", 200)},
                        {"type": "text", "tone": "why", "text": f"You started it by hand {n} times in two weeks. As a routine it runs each weekday at 08:00 and its result lands on this home."},
                        {"type": "evidence", "items": [{"text": f"{n} chats titled “{home.clip(shown, 60)}”", "source": "chats"},
                                                       {"text": cost, "source": "limits"}]},
                        {"type": "action", "label": "Create routine", "primary": True,
                         "prompt": home.clip(f"Turn “{shown}” into a weekday 08:00 routine that runs in this chat. Show me the draft (schedule, instruction, where it reports) before you create it with flows_submit.", 600)}]}})
    # CONNECTIONS: a tool named in chat or meeting titles that is not connected
    text = " ".join(_norm(s.get("title") or "") for s in recent) + " " + " ".join(_norm(m.get("title") or "") for m in meetings or [])
    seen = set()
    for word, name in TOOLS.items():
        if name in seen or not re.search(rf"\b{re.escape(word)}\b", text):
            continue
        seen.add(name)
        prov = PROVIDER.get(name)
        if prov and prov in connected:
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower())
        out.append({"source": f"connect:{slug}", "purpose": "connections", "title": f"Connect {name}", "prior": 0.5,
                    "people": [], "signal": f"{name} comes up in your chats",
                    "card": {"type": "card", "id": f"k-{slug}", "kind": "try", "label": "Connect", "blocks": [
                        {"type": "row", "icon": "spark", "text": f"Connect {name}"},
                        {"type": "text", "tone": "why", "text": f"{name} comes up in your chats and meetings. Connected, Minutes can read it when you ask and bring it into prep and follow-ups."},
                        {"type": "action", "label": f"Connect {name}", "primary": True,
                         "prompt": f"Connect my {name} so you can use it. Open the secure setup for it."}]}})
        if len(seen) >= 2:
            break
    # TEACHING tied to their own context: three or more meetings with the same title
    clusters = Counter(_norm(m.get("title") or "")[:40] for m in meetings or [] if (m.get("title") or "").strip())
    for title, n in clusters.most_common(1):
        if n < 3 or len(title) < 4:
            continue
        shown = next(m.get("title") for m in meetings if _norm(m.get("title") or "")[:40] == title)
        slug = re.sub(r"[^a-z0-9]+", "-", title)[:40].strip("-")
        out.append({"source": f"teach:{slug}", "purpose": "teaching", "title": f"Your {n} “{shown}” calls in one workspace",
                    "prior": 0.45, "people": [], "signal": f"{n} calls with the same title",
                    "card": {"type": "card", "id": f"t-{slug}", "kind": "try", "label": "How to", "blocks": [
                        {"type": "row", "icon": "folder", "text": home.clip(f"Your {n} “{shown}” calls could feed one workspace", 200)},
                        {"type": "text", "tone": "why", "text": "A workspace gathers their transcripts and notes, and a flow can file every new one there after the call. Ask anything across all of them."},
                        {"type": "action", "label": "Try it", "primary": True,
                         "prompt": home.clip(f"Make a workspace from my “{shown}” meetings and show me how a flow could file each new one there after the call.", 600)}]}})
    return out
