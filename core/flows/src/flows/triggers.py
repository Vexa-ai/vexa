"""Triggers beyond a bare event name and a cron (ADR-0050 decision 3): a glob, a filter, a keyed time.

A flow has exactly one trigger. Two kinds were here before (`event` — an exact `event_type`; and
`schedule` — a cron) and phase 0 added `at` (one time, then the flow retires). This module is the
engine's reading of the rest, stdlib only, pure:

* an EVENT GLOB — `calendar.event.*` matches `calendar.event.created|updated|cancelled`. Only `*` is
  special, and it never crosses into a second word by accident: the glob is matched against the
  whole type with `fnmatchcase`.
* a FILTER — a small predicate over the event's envelope, so a flow fires only for the events it is
  about (`payload.video_url exists and payload.self_response != 'declined'`). The grammar is closed:
  paths, string and number literals, `==`, `!=`, `in [..]`, `exists`, `contains`, `and`, `or`, `not`,
  parentheses. No code, no regex, nothing that can loop: the parse is one pass over at most
  :data:`MAX_FILTER` characters and evaluation is one walk of the tree.
* a RELATIVE time — `payload.start - 60s`: a path into the event plus or minus a duration
  (`s`, `m`, `h`, `d`). With a `key` (`payload.event_id`) it makes a KEYED SCHEDULE (`flows.keyed`):
  one pending fire per key, moved when the event moves, cancelled when it is cancelled or stops
  passing the filter.

THE ENVELOPE a filter and a time read is `{"type": event_type, "uid": refs.uid, "payload": refs}` —
`payload` is the event's refs as admission holds them, so `payload.start` is `refs["start"]`.
"""
from __future__ import annotations

import re
from datetime import datetime
from fnmatch import fnmatchcase
from typing import Any

#: The longest filter or time expression this engine reads.
MAX_FILTER = 512

#: Where a flow keeps its trigger beyond the row's `on_event`/`cron` columns (`params._trigger`). The
#: leading underscore marks it engine-owned: a person's `params` cannot set it directly (flows-api
#: writes it from the submission's `trigger`).
TRIGGER_PARAM = "_trigger"


class TriggerError(ValueError):
    """A trigger this engine cannot read. Raised at submission, never mid-fire."""


# ── globs ────────────────────────────────────────────────────────────────────────────────────────

_GLOB = re.compile(r"^[a-z][a-z0-9_]*(\.([a-z][a-z0-9_]*|\*))*(\.\*)?$")


def is_glob(event: str) -> bool:
    return "*" in str(event or "")


def check_glob(event: str) -> str:
    """`event` when it is an event type or a glob over event types (`calendar.event.*`)."""
    e = str(event or "").strip()
    if not e or len(e) > 120 or not _GLOB.match(e):
        raise TriggerError(f"{e!r} is not an event type or a glob like calendar.event.*")
    return e


def glob_matches(pattern: str, event_type: str) -> bool:
    return fnmatchcase(str(event_type or ""), str(pattern or ""))


# ── filters ──────────────────────────────────────────────────────────────────────────────────────

_TOKEN = re.compile(r"""(?:
    (?P<str>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
  | (?P<num>-?\d+(?:\.\d+)?)
  | (?P<op>==|!=|\(|\)|\[|\]|,)
  | (?P<word>[A-Za-z_][A-Za-z0-9_.]*)
)""", re.X)
_KEYWORDS = {"and", "or", "not", "in", "exists", "contains", "true", "false", "null"}


def _tokens(text: str) -> list:
    out, i = [], 0
    text = str(text or "")
    while True:
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            break
        m = _TOKEN.match(text, i)
        if not m or m.end() == i:
            raise TriggerError(f"cannot read the filter at {text[i:i + 20]!r}")
        i = m.end()
        kind = m.lastgroup
        val = m.group(kind)
        if kind == "str":
            out.append(("lit", bytes(val[1:-1], "utf-8").decode("unicode_escape")))
        elif kind == "num":
            out.append(("lit", float(val) if "." in val else int(val)))
        elif kind == "op":
            out.append(("op", val))
        elif val in ("true", "false"):
            out.append(("lit", val == "true"))
        elif val == "null":
            out.append(("lit", None))
        elif val in _KEYWORDS:
            out.append(("kw", val))
        else:
            out.append(("path", val))
    return out


class _Parser:
    def __init__(self, toks: list):
        self.t, self.i = toks, 0

    def peek(self, kind=None, val=None):
        if self.i >= len(self.t):
            return None
        k, v = self.t[self.i]
        if (kind and k != kind) or (val is not None and v != val):
            return None
        return self.t[self.i]

    def take(self, kind=None, val=None):
        tok = self.peek(kind, val)
        if tok is None:
            want = val or kind or "more"
            got = self.t[self.i][1] if self.i < len(self.t) else "the end"
            raise TriggerError(f"filter: expected {want}, found {got!r}")
        self.i += 1
        return tok

    def expr(self):
        node = self.conj()
        while self.peek("kw", "or"):
            self.take()
            node = ("or", node, self.conj())
        return node

    def conj(self):
        node = self.unary()
        while self.peek("kw", "and"):
            self.take()
            node = ("and", node, self.unary())
        return node

    def unary(self):
        if self.peek("kw", "not"):
            self.take()
            return ("not", self.unary())
        if self.peek("op", "("):
            self.take()
            node = self.expr()
            self.take("op", ")")
            return node
        return self.compare()

    def operand(self):
        tok = self.peek("path") or self.peek("lit")
        if tok is None:
            raise TriggerError("filter: expected a path or a value")
        self.take()
        return tok

    def compare(self):
        left = self.operand()
        if self.peek("kw", "exists"):
            self.take()
            return ("exists", left)
        if self.peek("op", "==") or self.peek("op", "!="):
            op = self.take()[1]
            return (op, left, self.operand())
        if self.peek("kw", "contains"):
            self.take()
            return ("contains", left, self.operand())
        if self.peek("kw", "in"):
            self.take()
            self.take("op", "[")
            items = [self.take("lit")[1]]
            while self.peek("op", ","):
                self.take()
                items.append(self.take("lit")[1])
            self.take("op", "]")
            return ("in", left, tuple(items))
        raise TriggerError("filter: a value must be compared (==, !=, in, contains) or tested (exists)")


def parse_filter(text: str):
    """The filter's tree, or ``None`` for an empty filter. Raises :class:`TriggerError`."""
    text = str(text or "").strip()
    if not text:
        return None
    if len(text) > MAX_FILTER:
        raise TriggerError(f"a filter is at most {MAX_FILTER} characters")
    p = _Parser(_tokens(text))
    node = p.expr()
    if p.i != len(p.t):
        raise TriggerError(f"filter: unexpected {p.t[p.i][1]!r}")
    return node


def envelope(event_type: str, refs: dict) -> dict:
    return {"type": event_type, "uid": str((refs or {}).get("uid") or ""), "payload": dict(refs or {})}


def resolve(env: dict, path: str) -> Any:
    """`payload.start` → env["payload"]["start"]; missing anywhere along the way is ``None``."""
    cur: Any = env
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _value(env: dict, tok) -> Any:
    kind, val = tok
    return resolve(env, val) if kind == "path" else val


def evaluate(node, env: dict) -> bool:
    if node is None:
        return True
    op = node[0]
    if op == "and":
        return evaluate(node[1], env) and evaluate(node[2], env)
    if op == "or":
        return evaluate(node[1], env) or evaluate(node[2], env)
    if op == "not":
        return not evaluate(node[1], env)
    if op == "exists":
        v = _value(env, node[1])
        return v not in (None, "", [], {})
    if op in ("==", "!="):
        a, b = _value(env, node[1]), _value(env, node[2])
        same = (str(a) == str(b)) if (a is not None and b is not None) else (a == b)
        return same if op == "==" else not same
    if op == "contains":
        a, b = _value(env, node[1]), _value(env, node[2])
        if isinstance(a, (list, tuple)):
            return b in a
        return a is not None and b is not None and str(b).lower() in str(a).lower()
    if op == "in":
        a = _value(env, node[1])
        return a is not None and str(a) in {str(x) for x in node[2]}
    raise TriggerError(f"unknown filter node {op}")


def passes(filter_text: str, event_type: str, refs: dict) -> bool:
    """Does this event pass the filter? An unreadable filter passes nothing."""
    try:
        return evaluate(parse_filter(filter_text), envelope(event_type, refs))
    except TriggerError:
        return False


# ── relative times ───────────────────────────────────────────────────────────────────────────────

_AT = re.compile(r"^(?P<path>[A-Za-z_][A-Za-z0-9_.]*)(?:\s*(?P<sign>[+-])\s*(?P<n>\d+)\s*(?P<unit>[smhd]))?$")
_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400}
#: The largest offset a relative time may carry: a bot a week early is not a bot for this meeting.
MAX_OFFSET_S = 7 * 86400


def parse_at(text: str) -> tuple:
    """`payload.start - 60s` → ("payload.start", -60). Raises :class:`TriggerError`."""
    m = _AT.match(str(text or "").strip())
    if not m:
        raise TriggerError("a relative time is a path plus or minus a duration, e.g. payload.start - 60s")
    off = 0
    if m.group("sign"):
        off = int(m.group("n")) * _UNIT[m.group("unit")]
        off = -off if m.group("sign") == "-" else off
    if abs(off) > MAX_OFFSET_S:
        raise TriggerError(f"an offset is at most {MAX_OFFSET_S // 86400} days")
    return m.group("path"), off


def as_epoch(value: Any) -> "float | None":
    """An event time as UTC epoch seconds: a number, an ISO-8601 time with an offset, or a Calendar
    `{dateTime}`/`{date}` object (a date alone is midnight UTC). ``None`` when it is none of them."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        value = value.get("dateTime") or value.get("date_time") or (
            (value.get("date") + "T00:00:00+00:00") if value.get("date") else None)
    if isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            return None
        return dt.timestamp()
    return None


def fire_at(at_text: str, event_type: str, refs: dict) -> "float | None":
    """When a relative trigger fires for this event, or ``None`` when the event has no such time."""
    try:
        path, off = parse_at(at_text)
    except TriggerError:
        return None
    base = as_epoch(resolve(envelope(event_type, refs), path))
    return None if base is None else base + off


def key_of(key_path: str, event_type: str, refs: dict) -> str:
    v = resolve(envelope(event_type, refs), key_path)
    return "" if v in (None, "") else str(v)[:200]


def spec_of(flow) -> dict:
    """The flow's trigger beyond its row (`params._trigger`), ``{}`` for a bare event or schedule."""
    raw = flow.param(TRIGGER_PARAM, None) if flow is not None else None
    return raw if isinstance(raw, dict) else {}


def check_spec(spec: dict) -> dict:
    """Validate a submitted `{kind: event|relative, event, filter?, at?, key?}`; the stored form."""
    kind = str(spec.get("kind") or "").strip()
    out: dict = {"kind": kind}
    if spec.get("filter"):
        parse_filter(spec["filter"])
        out["filter"] = str(spec["filter"]).strip()
    if kind == "relative":
        parse_at(spec.get("at") or "")
        key = str(spec.get("key") or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,80}", key):
            raise TriggerError("a relative trigger needs `key`, a path such as payload.event_id")
        out.update(at=str(spec["at"]).strip(), key=key)
    return out
