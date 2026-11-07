"""page_grammar.py — THE ONE PAGE GRAMMAR (PRD 0.13.4 D17, D18; UI decisions 7aa, 7gg).

A page stays markdown. Two additions make it live, and they are the only two:

    [[kind:id]]  [[kind:id|label]]     an INLINE REFERENCE to any object, rendered as a chip for
                                       the viewer (``universal_refs`` decides what each viewer sees)
    {{name key=value "positional"}}    a LIVE BLOCK (a list the server resolves per viewer) or an
                                       ACTION (a button; nothing runs on render)

Before D17 there were two grammars on the client (``richParse.ACTION_VERBS`` with
``[[action:verb:target|label]]``, and the mock's ``DO_VERBS`` with ``{{verb …}}``). They are one
registry here (``VERBS``); the old spellings parse into it so existing pages keep working.

Reference ids carry an optional ANCHOR, so one form addresses a moment or a place:

    [[meeting:153@00:12:30]]     a meeting at a time          /r/meeting/153#t=00:12:30
    [[meeting:153#L42]]          a meeting's transcript line  /r/meeting/153#L42
    [[page:<wid>/notes.md#venue]] a page's heading            /r/page/<wid>/notes.md#venue

Rules every consumer relies on (and the tests prove):
* fenced code is text — nothing inside a fence is live;
* an unknown block name, a malformed block or a verb outside ``VERBS`` is DROPPED, never shown raw
  and never shown as an error (7aa: "never a raw placeholder or parser error");
* the grammar is pure: parsing never resolves, reads or runs anything.

``RESOLVED_MARK`` is the sentinel every serialised render carries. The platform commit path refuses
text that holds it (deny test 23): git holds placeholders only, never a value resolved for a viewer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Union

# ── kinds ─────────────────────────────────────────────────────────────────────────────────────────

#: Every kind a reference may name (7gg: one form for every object).
REF_KINDS = ("workspace", "room", "chat", "page", "file", "entity", "meeting", "card", "person",
             "routine", "sensor", "message", "query", "series", "join", "commit", "policy")
#: Older spellings, accepted and normalised (the rich-README grammar and links.REF_KINDS used them).
KIND_ALIASES = {"call": "meeting", "flow": "routine"}
ALL_KINDS = REF_KINDS + tuple(KIND_ALIASES)


def canonical_kind(kind: str) -> Optional[str]:
    k = str(kind or "").strip().lower()
    k = KIND_ALIASES.get(k, k)
    return k if k in REF_KINDS else None


# ── blocks and verbs ──────────────────────────────────────────────────────────────────────────────

#: The live blocks (7aa), resolved per viewer by ``page_render``. ``personal`` blocks show only the
#: viewer's own state: they render only in the viewer's panel, never in a room, unfurl or nudge.
BLOCKS = {
    "waiting-on-you": {"title": "Waiting on you", "personal": True},
    "waiting-on-others": {"title": "Waiting on others", "personal": True},
    "commitments": {"title": "Commitments", "personal": False},
    "rooms": {"title": "Rooms", "personal": False},
    "people": {"title": "People", "personal": False},
    "routines": {"title": "Agents and routines", "personal": False},
    "meetings": {"title": "Meetings", "personal": False},
    "files": {"title": "Files", "personal": False},
    "pinned": {"title": "Pinned", "personal": False},
    "changes-since": {"title": "What changed", "personal": True},
    "crm": {"title": "", "personal": False},
    "card": {"title": "", "personal": False},
    "cards": {"title": "", "personal": False},
}
#: The rich-README badges, kept so old pages still read (each renders as a small inline status).
BADGES = ("status", "needs-you", "working-here", "members", "upcoming")


@dataclass(frozen=True)
class Verb:
    """One action a page may place. ``needs`` is the least workspace role that may perform it
    (``reader`` < ``contributor`` < ``owner``); ``confirm`` = it reaches beyond the clicker's own
    screen, so the client confirms first. Actions NEVER run on render."""
    name: str
    label: str
    needs: str = "reader"
    confirm: bool = False
    aliases: tuple = ()


#: THE ONE VERB REGISTRY (D17): the founder's six README actions (7aa) plus the rich-README verbs.
VERBS: dict[str, Verb] = {v.name: v for v in (
    Verb("new-chat", "New chat"),
    Verb("new-room", "New room", needs="contributor", confirm=True),
    Verb("schedule", "Schedule", needs="contributor", confirm=True, aliases=("schedule-call",)),
    Verb("invite", "Invite", needs="contributor"),
    Verb("ask", "Ask"),
    Verb("run", "Run", needs="contributor", confirm=True, aliases=("run-routine",)),
    Verb("start-call", "Start a call", needs="contributor", confirm=True),
    Verb("join-call", "Join"),
    Verb("prepare", "Prepare"),
    Verb("accept-proposal", "Accept", needs="contributor", confirm=True),
    Verb("move-card", "Move", needs="contributor", confirm=True),
)}
_VERB_ALIASES = {a: v.name for v in VERBS.values() for a in v.aliases}
ROLE_RANK = {"reader": 0, "contributor": 1, "owner": 2}


def canonical_verb(name: str) -> Optional[str]:
    n = str(name or "").strip().lower()
    n = _VERB_ALIASES.get(n, n)
    return n if n in VERBS else None


def verb_allowed(verb: str, role: Optional[str]) -> bool:
    v = VERBS.get(canonical_verb(verb) or "")
    if v is None or role not in ROLE_RANK:
        return False
    return ROLE_RANK[role] >= ROLE_RANK[v.needs]


# ── the serialised-render sentinel (deny test 23) ─────────────────────────────────────────────────

RESOLVED_MARK = "vx-resolved"
_RESOLVED_RE = re.compile(r"<!--\s*vx-resolved\b|data-vx-resolved\b")


def contains_resolved(text: str) -> bool:
    """Does this text carry a render's output? The commit path refuses it (D17, deny test 23)."""
    return bool(_RESOLVED_RE.search(str(text or "")))


# ── parsing ───────────────────────────────────────────────────────────────────────────────────────

_ID = r"[^\]\[|\s]{1,300}"
REF_RE = re.compile(r"\[\[(" + "|".join(ALL_KINDS) + r"):(" + _ID + r")(?:\|([^\]]{0,200}))?\]\]")
LEGACY_ACTION_RE = re.compile(r"\[\[action:([a-z-]{1,40}):([^\]|]{0,300})\|([^\]]{1,120})\]\]")
BLOCK_RE = re.compile(r"\{\{\s*([a-z][a-z-]{0,39})((?:[^{}\n]){0,400})\}\}")
_ARG_RE = re.compile(r'([a-z_][\w-]{0,39})\s*=\s*("([^"]*)"|\'([^\']*)\'|[^\s"\']+)|"([^"]*)"|\'([^\']*)\'|(\S+)')
_FENCE = re.compile(r"^```[^\n]*\n[\s\S]*?^```[ \t]*$", re.M)
_TIME = re.compile(r"^(?:(\d{1,2}):)?(\d{1,2}):(\d{2})$")


@dataclass(frozen=True)
class Ref:
    kind: str                 # canonical kind
    id: str                   # the object's stable id, without the anchor
    anchor: Optional[str] = None   # "t=00:12:30" · "L42" · a heading slug
    label: Optional[str] = None    # the writer's hint; never echoed by a resolver

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.id}" + (f"#{self.anchor}" if self.anchor else "")


@dataclass(frozen=True)
class Block:
    name: str
    args: dict = field(default_factory=dict)
    positional: tuple = ()

    @property
    def key(self) -> str:
        """The block's stable key: name + sorted args. The BFF and agent-api agree on it."""
        parts = [self.name] + [f'{k}="{v}"' for k, v in sorted(self.args.items())] + [f'"{p}"' for p in self.positional]
        return " ".join(parts)


@dataclass(frozen=True)
class Action:
    verb: str
    args: dict = field(default_factory=dict)
    label: Optional[str] = None


Inline = Union[str, Ref, Action, Block]


def split_anchor(kind: str, raw_id: str) -> tuple[str, Optional[str]]:
    """``153@00:12:30`` → (``153``, ``t=00:12:30``); ``153#L42`` → (``153``, ``L42``);
    ``wid/notes.md#venue`` → (``wid/notes.md``, ``venue``)."""
    rid = str(raw_id or "").strip()
    anchor = None
    if "#" in rid:
        rid, _, anchor = rid.partition("#")
        anchor = anchor.strip() or None
    if kind == "meeting" and "@" in rid:
        rid, _, at = rid.partition("@")
        t = _TIME.match(at.strip())
        anchor = f"t={normalize_time(at)}" if t else anchor
    if anchor and anchor.startswith("t="):
        t = normalize_time(anchor[2:])
        anchor = f"t={t}" if t else None
    return rid.strip(), anchor


def normalize_time(s: str) -> Optional[str]:
    m = _TIME.match(str(s or "").strip())
    if not m:
        return None
    h, mi, se = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    if se >= 60 or (m.group(1) is not None and mi >= 60):
        return None
    total = h * 3600 + mi * 60 + se
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def parse_ref_parts(kind: str, raw_id: str, label: Optional[str] = None) -> Optional[Ref]:
    k = canonical_kind(kind)
    if k is None:
        return None
    rid, anchor = split_anchor(k, raw_id)
    if not rid or any(seg in ("", ".", "..") for seg in rid.split("/")):
        return None
    lab = (label or "").strip() or None
    return Ref(k, rid, anchor, lab)


def parse_args(s: str) -> tuple[dict, tuple]:
    args: dict = {}
    pos: list = []
    for m in _ARG_RE.finditer(str(s or "")):
        if m.group(1):
            v = m.group(3) if m.group(3) is not None else m.group(4) if m.group(4) is not None else m.group(2)
            args[m.group(1).lower().replace("_", "-")] = v.strip()
        else:
            v = next((g for g in (m.group(5), m.group(6), m.group(7)) if g is not None), "")
            if v.strip():
                pos.append(v.strip())
    return args, tuple(pos)


def parse_brace(name: str, rest: str) -> Union[Block, Action, str, None]:
    """``{{name …}}`` → a Block, an Action, a badge name (str), or None (dropped)."""
    n = str(name or "").strip().lower()
    args, pos = parse_args(rest)
    if n in BLOCKS:
        return Block(n, args, pos)
    v = canonical_verb(n)
    if v is not None:
        return Action(v, args, args.get("label"))
    if n in BADGES and not rest.strip():
        return n
    return None


def _legacy_action(verb: str, target: str, label: str) -> Optional[Action]:
    v = canonical_verb(verb)
    if v is None:
        return None
    t = target.strip()
    args = {"target": t} if t else {}
    return Action(v, args, label.strip() or None)


def split_inline(line: str) -> list[Inline]:
    """One line of prose → text, references, actions and blocks, in order. A dropped part leaves
    nothing behind (a refused legacy action keeps its label as text, as it always did)."""
    out: list[Inline] = []
    pat = re.compile(f"{REF_RE.pattern}|{LEGACY_ACTION_RE.pattern}|{BLOCK_RE.pattern}")
    last = 0
    s = str(line or "")

    def push(t: str) -> None:
        if not t:
            return
        if out and isinstance(out[-1], str):
            out[-1] += t
        else:
            out.append(t)

    for m in pat.finditer(s):
        push(s[last:m.start()])
        if m.group(1):
            r = parse_ref_parts(m.group(1), m.group(2), m.group(3))
            if r is not None:
                out.append(r)
        elif m.group(4):
            a = _legacy_action(m.group(4), m.group(5), m.group(6))
            if a is not None:
                out.append(a)
            else:
                push(m.group(6).strip())
        else:
            got = parse_brace(m.group(7), m.group(8) or "")
            if isinstance(got, (Block, Action)):
                out.append(got)
            elif isinstance(got, str):
                out.append(Block(got))       # a badge is a tiny block
        last = m.end()
    push(s[last:])
    return out


@dataclass
class Node:
    """One parsed piece of a page: ``md`` (plain markdown, nothing live in it), ``line`` (a line
    with live parts), ``block`` (a line that is exactly one live block or action)."""
    t: str
    text: str = ""
    parts: list = field(default_factory=list)
    item: Union[Block, Action, None] = None


def parse(md: str) -> list[Node]:
    """A page → nodes. Fences are kept as markdown, untouched."""
    src = str(md or "")
    nodes: list[Node] = []
    buf: list[str] = []
    live = re.compile(f"{REF_RE.pattern}|{LEGACY_ACTION_RE.pattern}|{BLOCK_RE.pattern}")
    stray = re.compile(r"\{\{[^{}\n]*\}\}")

    def flush() -> None:
        if "".join(buf).strip():
            nodes.append(Node("md", text="\n".join(buf)))
        buf.clear()

    lines = src.split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        if re.match(r"^```", ln):
            j = i + 1
            while j < len(lines) and not re.match(r"^```[ \t]*$", lines[j]):
                j += 1
            buf.extend(lines[i:min(j + 1, len(lines))])
            i = j + 1
            continue
        if live.search(ln):
            flush()
            parts = split_inline(ln)
            only = [p for p in parts if not (isinstance(p, str) and not p.strip())]
            if len(only) == 1 and isinstance(only[0], (Block, Action)) and not re.match(r"^\s*[-*]\s", ln):
                nodes.append(Node("block", item=only[0]))
            elif parts:
                nodes.append(Node("line", text=ln, parts=parts))
            i += 1
            continue
        if stray.search(ln):
            # a malformed or unknown {{…}} outside a fence: dropped, never shown raw (7aa)
            ln = stray.sub("", ln)
        buf.append(ln)
        i += 1
    flush()
    return nodes


def refs_in(md: str) -> list[Ref]:
    """Every reference in a page, once each, in order (fenced code excluded)."""
    seen: set = set()
    out: list[Ref] = []
    for m in REF_RE.finditer(_FENCE.sub("", str(md or ""))):
        r = parse_ref_parts(m.group(1), m.group(2), m.group(3))
        if r is not None and r.key not in seen:
            seen.add(r.key)
            out.append(r)
    return out


def blocks_in(md: str) -> list[Block]:
    """Every live block in a page, once each by key (fenced code excluded)."""
    seen: set = set()
    out: list[Block] = []
    for node in parse(md):
        items = [node.item] if node.t == "block" else [p for p in node.parts if not isinstance(p, (str, Ref))]
        for it in items:
            if isinstance(it, Block) and it.name in BLOCKS and it.key not in seen:
                seen.add(it.key)
                out.append(it)
    return out


def ref_url(r: Ref, *, base: str = "") -> str:
    """The pasteable URL for a reference (anchor as the fragment)."""
    from urllib.parse import quote

    url = f"{str(base or '').rstrip('/')}/r/{r.kind}/{quote(r.id, safe='/-_.~@:')}"
    return url + (f"#{r.anchor}" if r.anchor else "")


def ref_chip(r: Ref, label: Optional[str] = None) -> str:
    rid = r.id
    if r.anchor:
        rid += f"@{r.anchor[2:]}" if r.kind == "meeting" and r.anchor.startswith("t=") else f"#{r.anchor}"
    return f"[[{r.kind}:{rid}|{label}]]" if label else f"[[{r.kind}:{rid}]]"
