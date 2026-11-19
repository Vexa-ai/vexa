"""page_render.py — a page rendered for ONE viewer, on the server (PRD 0.13.4 D17; UI 7aa, 7cc, 7ee).

The workspace page is its README. The README in git holds placeholders only (``{{rooms}}``,
``{{crm company="OeNB"}}``, ``[[meeting:153@00:12:30]]``…); this module turns the parsed page
(``page_grammar``) plus what each owner answered FOR THIS VIEWER into a render tree the client
draws as one document. It is pure: every input is already access-filtered by its owner, and the
route (`routers/desktop.py` ``POST /api/desktop/page``) gathers them with the viewer's identity.

The rules (and the tests that hold them):
* RESTRICTED IS ABSENT. A block with no row this viewer may see renders nothing at all — no heading,
  no "0", no "N hidden" — so a block's bytes are identical whether hidden records exist or not
  (deny test 22). "Show N more" counts only rows the viewer may see.
* NEVER A RAW PLACEHOLDER OR A PARSER ERROR. Unknown blocks, failed sources and refused verbs
  produce nothing; a source that is DOWN produces one typed quiet state (``unavailable``), the same
  for everyone.
* ACTIONS NEVER RUN ON RENDER. An action is a descriptor: verb, label, arguments, and whether THIS
  viewer may perform it (with the reason when not).
* PERSONAL BLOCKS ARE MARKED. ``waiting-on-you``, ``waiting-on-others``, ``changes-since`` and a CRM
  field only the viewer may see ("only you see this") carry ``personal``; ``public_view`` drops them,
  so nothing personal can reach a room timeline, an unfurl or a nudge.
* GIT HOLDS PLACEHOLDERS ONLY. ``to_markdown`` (an export, never written back) stamps
  ``page_grammar.RESOLVED_MARK``; the platform commit path refuses any text carrying it (deny test 23).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from control_plane import page_grammar as g

#: Rows shown before "Show N more", per block (``limit=`` overrides, capped at ``LIMIT_MAX``).
DEFAULT_LIMIT = {"waiting-on-you": 5, "waiting-on-others": 5, "commitments": 5, "rooms": 6, "people": 8,
                 "routines": 6, "meetings": 5, "files": 8, "pinned": 8, "changes-since": 8, "cards": 8,
                 "crm": 10, "card": 1}
LIMIT_MAX = 50
#: The block names whose rows the agent-api route supplies; the others come from the BFF (crm, cards).
AGENT_BLOCKS = ("waiting-on-you", "rooms", "people", "routines", "meetings", "files", "pinned", "changes-since")
CARD_BLOCKS = ("commitments", "waiting-on-others", "card", "cards")
#: A section heading the README places over a block (H2–H6; the page's own H1 title is never dropped).
_SUBHEADING = re.compile(r"^\s*#{2,6}\s")
ARG_KEYS = {
    "new-chat": ("prompt", "label"), "new-room": ("name", "label"), "schedule": ("title", "every", "with", "label"),
    "invite": ("role", "label"), "ask": ("label",), "run": ("routine", "label", "target"),
    "start-call": ("target", "label"), "join-call": ("target", "label"), "prepare": ("target", "label"),
    "accept-proposal": ("target", "label"), "move-card": ("target", "label"),
}


@dataclass
class Viewer:
    subject: str
    workspace: str
    role: Optional[str]            # reader · contributor · owner · None (cannot open it)
    shared: bool = True            # a shared workspace (invite makes sense) vs the person's own desk


@dataclass
class Sources:
    """Each owner's answer for this viewer. ``lists`` rows: ``{key, title, detail?, ref?, at?, state?}``.
    ``crm`` / ``cards`` map a block key to its rows; a missing key or ``None`` = that owner is down
    (or, for cards, the card read interface is not there yet: S5)."""
    refs: Callable[[list], dict] = lambda refs: {}
    #: CLAIMS AND PROOF (UI 7aa "cards prove pages", converged §14) — S5's seam: a card id →
    #: ``proven`` · ``pending`` · ``stale`` for THIS viewer, or None (unknown). Until S5's card read
    #: interface merges it answers None and a claim renders as its plain chip, with no marker.
    proof: Callable[[str], Optional[str]] = lambda card_id: None
    lists: dict = field(default_factory=dict)
    crm: dict = field(default_factory=dict)
    cards: dict = field(default_factory=dict)


def _limit(block: g.Block) -> int:
    raw = str(block.args.get("limit") or "")
    n = int(raw) if raw.isdigit() else DEFAULT_LIMIT.get(block.name, 8)
    return max(1, min(n, LIMIT_MAX))


def _clip(s, n=300) -> str:
    s = str(s if s is not None else "")
    return s if len(s) <= n else s[: n - 1] + "…"


def _row(r: dict) -> dict:
    out = {"key": _clip(r.get("key") or r.get("ref") or r.get("title"), 200), "title": _clip(r.get("title"), 200)}
    for k in ("detail", "ref", "state", "by", "url"):
        if r.get(k):
            out[k] = _clip(r[k], 300)
    if r.get("at"):
        try:
            out["at"] = int(r["at"])
        except (TypeError, ValueError):
            pass
    if r.get("act"):
        out["act"] = r["act"]
    if r.get("restricted"):
        out = {"key": out["key"], "restricted": True, **({"by": out["by"]} if "by" in out else {}),
               **({"at": out["at"]} if "at" in out else {})}
    return out


def _filter_rows(block: g.Block, rows: list) -> list:
    """The block's own filters, over rows the viewer may see (never over hidden ones)."""
    out = list(rows)
    if block.name == "meetings":
        when = str(block.args.get("when") or "").lower()
        if when in ("upcoming", "past", "live"):
            want = {"upcoming": ("upcoming",), "past": ("ended",), "live": ("live",)}[when]
            out = [r for r in out if r.get("state") in want]
    if block.name == "files":
        folder = str(block.args.get("folder") or "").strip("/")
        if folder:
            out = [r for r in out if str(r.get("key") or "").startswith(folder + "/")]
    if block.name in ("commitments", "cards"):
        for k in ("status", "state", "kind", "owner", "room", "meeting"):
            v = str(block.args.get(k) or "").strip().casefold()
            if v:
                out = [r for r in out if str(r.get(k if k != "status" else "state") or "").casefold() == v]
    return out


def _list_node(block: g.Block, rows: Optional[list], *, heading: bool) -> Optional[dict]:
    meta = g.BLOCKS[block.name]
    if rows is None:
        return {"t": "block", "name": block.name, "state": "unavailable", "personal": meta["personal"]}
    rows = _filter_rows(block, rows)
    if not rows:
        return None                                   # restricted = absent; empty = absent
    n = _limit(block)
    # every visible row up to LIMIT_MAX travels, the first `limit` shown; "Show N more" opens the rest
    # (N counts only rows this viewer may see, so it says nothing about hidden ones)
    shown = rows[:LIMIT_MAX]
    node = {"t": "block", "name": block.name, "title": "" if heading else meta["title"],
            "personal": meta["personal"], "rows": [_row(r) for r in shown]}
    if len(shown) > n:
        node["limit"] = n
        node["more"] = len(shown) - n
    return node


def _crm_node(block: g.Block, payload: Optional[dict]) -> Optional[dict]:
    if payload is None:
        return {"t": "block", "name": "crm", "state": "unavailable", "personal": False}
    rows = []
    for r in (payload.get("rows") or [])[:_limit(block)]:
        fields = [{"name": _clip(f.get("name"), 60), "value": f.get("value") if isinstance(f.get("value"), (int, float, bool)) else _clip(f.get("value"), 300),
                   **({"only_you": True} if not f.get("shared") else {})}
                  for f in (r.get("fields") or []) if isinstance(f, dict) and f.get("name")]
        rows.append({"key": _clip(r.get("id"), 200), "title": _clip(r.get("title"), 200),
                     "type": _clip(r.get("type"), 60), "fields": fields,
                     **({"url": _clip(r.get("href"), 300)} if r.get("href") else {})})
    if not rows:
        return None
    personal = any(f.get("only_you") for r in rows for f in r["fields"])
    return {"t": "block", "name": "crm", "view": _clip(payload.get("view"), 20), "personal": personal, "rows": rows}


def _cards_node(block: g.Block, rows: Optional[list], *, heading: bool) -> Optional[dict]:
    if rows is None:
        # the card read interface (S5) is not here yet, or crm is down: one quiet typed state
        return {"t": "block", "name": block.name, "state": "pending", "personal": g.BLOCKS[block.name]["personal"]}
    return _list_node(block, rows, heading=heading)


def _action_node(a: g.Action, viewer: Viewer, positional: tuple = ()) -> dict:
    verb = g.VERBS[a.verb]
    keys = ARG_KEYS.get(a.verb, ("label",))
    args = {k: _clip(v, 300) for k, v in a.args.items() if k in keys and k != "label"}
    label = a.label or (positional[0] if positional else None) or verb.label
    if a.verb == "ask" and positional:
        args["prompt"] = _clip(positional[0], 300)
    node = {"t": "action", "verb": a.verb, "label": _clip(label, 80), "args": args, "confirm": verb.confirm,
            "enabled": True}
    reason = None
    if viewer.role is None:
        reason = "You can't open this workspace"
    elif not g.verb_allowed(a.verb, viewer.role):
        reason = "Read-only for you here" if viewer.role == "reader" else "Not available to you here"
    elif a.verb == "invite" and not viewer.shared:
        reason = "Only shared workspaces have members"
    elif a.verb == "run" and not (args.get("routine") or args.get("target")):
        reason = "No routine named"
    if reason:
        node["enabled"] = False
        node["reason"] = reason
    return node


def _ref_part(r: g.Ref) -> dict:
    # A CLAIM (UI 7aa, converged §14): `[[card:id|words]]` keeps the page's own words (page text the
    # reader already reads) beside the card's live state; for every other kind the label is dropped.
    if r.kind == "card" and r.label:
        return {"t": "ref", "key": r.key, "words": _clip(r.label, 200)}
    return {"t": "ref", "key": r.key}


def render(md: str, viewer: Viewer, sources: Sources, *, path: str = "README.md") -> dict:
    """The render tree for this viewer. Deterministic: the same inputs give the same bytes."""
    nodes_in = g.parse(md)
    refs = g.refs_in(md)
    answers = {}
    if refs:
        try:
            answers = sources.refs(refs) or {}
        except Exception:  # noqa: BLE001 — a resolver that failed names nothing
            answers = {}
    out: list[dict] = []
    for i, n in enumerate(nodes_in):
        prev = out[-1] if out else None
        heading = bool(prev and prev["t"] == "md" and _ends_with_heading(prev["text"]))
        if n.t == "md":
            out.append({"t": "md", "text": n.text})
            continue
        if n.t == "block":
            node = _item_node(n.item, viewer, sources, heading=heading)
            if node is not None:
                out.append(node)
            elif heading and prev is not None:
                # the README's own heading over a block that rendered nothing goes too, so an empty
                # (or entirely hidden) list leaves no trace
                prev["text"] = _drop_last_heading(prev["text"])
                if not prev["text"].strip():
                    out.pop()
            continue
        parts: list[dict] = []
        hoisted: list[dict] = []
        for p in n.parts:
            if isinstance(p, str):
                if parts and parts[-1]["t"] == "text":
                    parts[-1]["text"] += p
                else:
                    parts.append({"t": "text", "text": p})
            elif isinstance(p, g.Ref):
                parts.append(_ref_part(p))
            elif isinstance(p, g.Action):
                parts.append(_action_node(p, viewer, ()))
            elif isinstance(p, g.Block):
                if p.name in g.BADGES:
                    continue                    # retired inline badges: the blocks say it now
                node = _item_node(p, viewer, sources, heading=False)
                if node is not None:
                    hoisted.append(node)
        if any(pt["t"] != "text" or pt["text"].strip() for pt in parts):
            out.append({"t": "line", "parts": parts, **({"list": True} if n.text.lstrip().startswith(("- ", "* ")) else {})})
        out.extend(hoisted)
    ref_map = {}
    for r in refs:
        a = answers.get(r.key) or {"access": "gone"}
        ref_map[r.key] = _ref_answer(r, a)
        if r.kind == "card" and ref_map[r.key]["access"] == "readable":
            try:
                mark = sources.proof(r.id)
            except Exception:  # noqa: BLE001 — no answer, no marker
                mark = None
            if mark in ("proven", "pending", "stale"):
                ref_map[r.key]["proof"] = mark
    return {"v": 1, "workspace": viewer.workspace, "path": path, "nodes": out, "refs": ref_map}


def _item_node(item, viewer: Viewer, sources: Sources, *, heading: bool) -> Optional[dict]:
    if isinstance(item, g.Action):
        return _action_node(item, viewer, ())
    if not isinstance(item, g.Block) or item.name not in g.BLOCKS:
        return None
    if item.name == "crm":
        view = item.positional[0] if item.positional else next(
            (k for k in ("company", "person", "deals", "timeline", "people") if k in item.args), "company")
        if view in ("company", "person") and not (item.args.get(view) or item.args.get("name")):
            return None
        return _crm_node(item, sources.crm.get(item.key) if item.key in sources.crm else None)
    if item.name in CARD_BLOCKS:
        return _cards_node(item, sources.cards.get(item.key) if item.key in sources.cards else None,
                           heading=heading)
    rows = sources.lists.get(item.name)
    return _list_node(item, rows, heading=heading)


def _ref_answer(r: g.Ref, a: dict) -> dict:
    """What the client draws for one reference: the label only when readable; "restricted" with no
    name when not; nothing from the writer's ``|label`` ever (D18)."""
    access = a.get("access") or "gone"
    out = {"kind": r.kind, "access": access}
    if access in ("readable", "grant"):
        if a.get("label"):
            out["label"] = _clip(a["label"], 200)
        if a.get("open"):
            out["open"] = a["open"]
        if a.get("url"):
            out["url"] = a["url"]
    elif access == "not-yours":
        out["request_access"] = bool(a.get("request_access"))
    return out


def _ends_with_heading(text: str) -> bool:
    lines = [ln for ln in str(text or "").split("\n") if ln.strip()]
    return bool(lines) and bool(_SUBHEADING.match(lines[-1]))


def _drop_last_heading(text: str) -> str:
    lines = str(text or "").split("\n")
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip():
            if _SUBHEADING.match(lines[i]):
                del lines[i]
            break
    return "\n".join(lines).rstrip("\n")


def public_view(tree: dict) -> dict:
    """The tree with everything personal removed — for anything other people see (a room, an unfurl,
    a nudge). Personal blocks go whole; a CRM row keeps only its workspace-shared fields."""
    nodes = []
    for n in tree.get("nodes") or []:
        if n.get("t") == "block" and n.get("name") == "crm" and n.get("rows"):
            rows = [{**r, "fields": [f for f in r["fields"] if not f.get("only_you")]} for r in n["rows"]]
            rows = [r for r in rows if r["fields"]]
            if rows:
                nodes.append({**n, "rows": rows, "personal": False})
            continue
        if n.get("personal"):
            continue
        nodes.append(n)
    return {**tree, "nodes": nodes}


def to_bytes(tree: dict) -> bytes:
    """The canonical serialisation the byte-identical tests compare."""
    return json.dumps(tree, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def to_markdown(tree: dict) -> str:
    """A flat export of a render (copy as text, an unfurl preview). STAMPED: the commit path refuses
    any text carrying the mark, so a render can never be committed back as the page (deny test 23)."""
    out = [f"<!-- {g.RESOLVED_MARK} {tree.get('workspace', '')}/{tree.get('path', '')} -->"]
    refs = tree.get("refs") or {}
    for n in tree.get("nodes") or []:
        t = n.get("t")
        if t == "md":
            out.append(n["text"])
        elif t == "line":
            words = []
            for p in n["parts"]:
                if p["t"] == "text":
                    words.append(p["text"])
                elif p["t"] == "ref":
                    a = refs.get(p["key"]) or {}
                    words.append(a.get("label") or "restricted")
                elif p["t"] == "action":
                    words.append(f"[{p['label']}]")
            out.append("".join(words))
        elif t == "block" and n.get("rows"):
            if n.get("title"):
                out.append(f"**{n['title']}**")
            for r in n["rows"]:
                out.append(f"- {r.get('title') or 'restricted'}")
        elif t == "action":
            out.append(f"[{n['label']}]")
    return "\n\n".join(out)


#: The README a new workspace starts with (7aa: "new workspaces get a seeded default README"). Founder
#: ruling 2026-10-17, "a workspace is about the people around it": what waits on you, then the people,
#: rooms, routines and commitments, then prose. The workspace's agent owns the layout from then on; a
#: person's edit wins a conflict (D17).
DEFAULT_README = """# {name}

{{{{waiting-on-you}}}}

## People
{{{{people}}}}

## Rooms
{{{{rooms}}}}

## Agents and routines
{{{{routines}}}}

## Commitments
{{{{commitments limit=5}}}}

## How to use this workspace
Start a conversation here, or open a room for the team: {{{{new-chat prompt="What should I know about {name}?"}}}} {{{{new-room name="general"}}}}
Bring someone in: {{{{invite role=contributor}}}}

## Pinned
{{{{pinned}}}}

## What changed
{{{{changes-since}}}}
"""


def default_readme(name: str) -> str:
    safe = str(name or "This workspace").replace("{", "").replace("}", "").replace('"', "'").strip() or "This workspace"
    return DEFAULT_README.format(name=safe)
