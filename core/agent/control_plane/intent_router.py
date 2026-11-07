"""intent_router.py — the fast intent router: a simple ask runs its action without a full turn
(QB-5; graph rows ``a_anywhere#AW-fast`` and ``turn#TU-route``).

Founder: *"we need router that would understand the intent before calling a big model to understand
if they want something simple"*. "Call @megan" spent a whole agent turn — a container, a big model,
twenty seconds — to arrive at one verb. This module decides, before any turn, whether the person's
message is ONE simple ask this platform can answer itself; ``routers/chats.py`` runs the matching
action when it is, and dispatches the full turn, unchanged, when it is not.

ORDER. RULES FIRST (regex, no model, microseconds), then — only when the rules are unsure but the
message is short and names an intent word — ONE call to a small model with a hard deadline
(``MODEL_TIMEOUT_S``), strict JSON, and a confidence floor (``CONFIDENCE_FLOOR``). Anything else is
``none``: the full agent, exactly as before.

THE VOCABULARY IS CLOSED: ``call_person``, ``open_entity``, ``invite_person``, ``schedule``, ``none``.

WHAT THIS MODULE NEVER DOES. It runs nothing: it answers a :class:`Route`. Running is the chat
route's job, through the existing verbs and every guard they carry. ``invite_person`` and
``schedule`` are OUTBOUND (an email, a calendar event): the router recognises them for the receipt
and the chat route hands them to the full agent, which asks before anything leaves. A message that
names an outbound verb (send, email, publish, post, share, forward, reply, delete, pay) is ``none``
before any rule or model reads it, so no model answer can turn it into an action.

ALIGNED WITH THE WORKER'S IMPERATIVE GATE (``worker/engine.py``, ``_IMPERATIVE_PATTERNS``): the call
pattern below excludes the same non-person words after "call" ("call it", "call back", "call off"),
and the same negations and questions ("don't call", "did you call") are never an ask. A test pins
that every message routed to ``call_person`` here is also one that gate names ``call_person``.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

INTENTS = ("call_person", "open_entity", "invite_person", "schedule", "none")
#: Intents whose action leaves the platform. Recognised, never executed by the router.
OUTBOUND = frozenset({"invite_person", "schedule"})
#: The small model answers within this, or the message goes to the full agent.
MODEL_TIMEOUT_S = 0.7
#: Below this the model's answer is not trusted.
CONFIDENCE_FLOOR = 0.8
#: A message longer than this is not one simple ask.
MAX_WORDS = 12
MAX_CHARS = 140

# ── what is never a simple ask ─────────────────────────────────────────────────────────────────
#: A question, a negation, an "about" — the worker's `_IMPERATIVE_NOT_ASKED`, plus question words.
_NOT_ASKED = re.compile(
    r"^\s*(?:did|does|do|has|have|had|is|are|was|were|why|when|whether|if|what|who|how|which|where|should|would)\b"
    r"|\b(?:don'?t|do\s+not|never|no\s+need\s+to|not\s+yet|didn'?t|won'?t|shouldn'?t)\b", re.I)
#: More than one request in one message.
_COMPOUND = re.compile(r"\b(?:and|then|also|after|before|but|plus)\b|[;,\n]|&", re.I)
#: A verb whose effect leaves the platform. Never routed — the full agent asks first.
_OUTBOUND_VERB = re.compile(
    r"\b(?:send|e-?mail|mail|publish|post|share|forward|reply|message|text|tweet|dm|delete|remove|"
    r"pay|transfer|unpublish|announce|notify)\b", re.I)
#: Machinery the terminal or a flow composes — never a person's simple ask.
_MACHINERY = re.compile(r"<!--|\[\[|\]\]|```")
_POLITE_HEAD = re.compile(r"^(?:(?:hey|hi|ok|okay)\s+vexa\W*\s*)?(?:(?:please|pls|can\s+you|could\s+you|now)\s+)*",
                          re.I)
_POLITE_TAIL = re.compile(r"\s*(?:,?\s*(?:please|pls|now|thanks|thank\s+you))*\s*[.!]*\s*$", re.I)

# ── the rules ──────────────────────────────────────────────────────────────────────────────────
#: Words that follow "call" without naming a person (the worker gate's exclusion list).
_NOT_A_PERSON = r"(?!(?:the|a|an|it|this|that|me|back|off|out|for|of|in|on|to|up|him|her|them|us|everyone)\b)"
#: Words that make a "person" a time or a place — "call megan tomorrow" is not a call now.
_TIME_WORDS = re.compile(
    r"\b(?:today|tonight|tomorrow|yesterday|later|soon|morning|afternoon|evening|monday|tuesday|"
    r"wednesday|thursday|friday|saturday|sunday|next|week|am|pm|at|on|in)\b|\d", re.I)
_PERSON = (r"(@?[A-Za-z0-9][\w.+@'-]*"
           r"(?:\s+(?!(?:back|again|up|out|to|with|about|re|regarding|later|now)\b)[A-Za-z][\w.'-]*)?)")
_CALL = re.compile(r"^(?:call|ring|phone|dial)\s+" + _NOT_A_PERSON + _PERSON + r"$", re.I)
_CALL_WITH = re.compile(r"^(?:start|begin|make|open)\s+(?:a\s+)?(?:vexa\s+)?(?:video\s+)?call\s+with\s+"
                        + _PERSON + r"$", re.I)
_OPEN = re.compile(r"^(?:open|show(?:\s+me)?|pull\s+up|go\s+to)\s+(?:up\s+)?(?:the\s+|my\s+|our\s+)?"
                   r"(.{1,60}?)(?:\s+(?:page|entity|note|notes|doc|document|file|profile|record|card))?$",
                   re.I)
#: "open settings", "open a new chat" — places the full agent (or the terminal) handles.
_OPEN_NOT_ENTITY = re.compile(
    r"^(?:it|this|that|them|a|an|new|settings|chat|chats|call|meeting|transcript|inbox|home|desk|"
    r"browser|link|window|tab|panel|sidebar|everything|all)\b", re.I)
_INVITE = re.compile(r"^invite\s+" + _PERSON + r"(?:\s+to\s+(?:the\s+|my\s+|our\s+)?(.{1,60}?)(?:\s+workspace)?)?$",
                     re.I)
_SCHEDULE = re.compile(r"^(?:schedule|book|set\s+up|arrange|plan)\s+(?:a\s+|an\s+)?(?:quick\s+)?"
                       r"(?:call|meeting|sync|chat|meet)\s+with\s+(@?[A-Za-z0-9][\w.+@'-]*)(?:\s+(.{1,60}))?$",
                       re.I)
#: The words that make a short message worth asking the small model about ("rules unsure").
_INTENT_HINT = re.compile(r"\b(?:call|ring|phone|dial|open|show|pull|invite|schedule|book|meet|meeting)\b", re.I)

MODEL_SYSTEM = (
    "You route one chat message for a meetings and workspace assistant. Decide if it is exactly ONE "
    "simple ask from this list, and reply with ONE JSON object and nothing else:\n"
    "{\"intent\": \"call_person\"|\"open_entity\"|\"invite_person\"|\"schedule\"|\"none\", "
    "\"args\": {...}, \"confidence\": 0.0-1.0}\n"
    "call_person: start a call with a person now. args: {\"person\": the name as written}.\n"
    "open_entity: open a page about a person, company, project or meeting. args: {\"query\": the name as written}.\n"
    "invite_person: invite a person. args: {\"person\": ..., \"workspace\": ... or \"\"}.\n"
    "schedule: schedule a call or meeting with a person. args: {\"person\": ..., \"when\": ...}.\n"
    "Anything else, anything with more than one request, any question, anything that sends, posts or "
    "deletes something: {\"intent\": \"none\", \"args\": {}, \"confidence\": 1.0}. "
    "Copy names exactly as the person wrote them; never invent one."
)


@dataclass
class Route:
    """What the router decided about one message."""
    intent: str = "none"
    args: dict = field(default_factory=dict)
    confidence: float = 0.0
    source: str = "none"            # rules | model | none
    latency_ms: float = 0.0
    reason: str = ""                # why `none`, or what the rule matched
    model: str = ""                 # the catalog id the model step used, when it ran

    @property
    def simple(self) -> bool:
        return self.intent != "none"

    @property
    def outbound(self) -> bool:
        return self.intent in OUTBOUND

    def receipt(self, *, executed: bool, outcome: str, turn_id: str = "") -> dict:
        """The ROUTE RECEIPT for one message: what was decided, how sure, how fast, and what happened."""
        out = {"route": self.intent, "confidence": round(float(self.confidence), 3), "source": self.source,
               "latency_ms": round(float(self.latency_ms), 1), "executed": bool(executed),
               "outcome": str(outcome or ""), "reason": self.reason, "at": time.time()}
        if self.args:
            out["args"] = {k: str(v)[:120] for k, v in self.args.items()}
        if self.model:
            out["model"] = self.model
        if turn_id:
            # NOT `turn_id`: on the stream that key routes an event to a worker turn in the client.
            out["message"] = str(turn_id)[:128]
        return out


def _clean(text: str) -> str:
    t = " ".join(str(text or "").split())
    t = _POLITE_HEAD.sub("", t, count=1)
    t = _POLITE_TAIL.sub("", t, count=1)
    return t.strip()


def _person_ok(p: str) -> bool:
    return bool(p) and not _TIME_WORDS.search(p.lstrip("@"))


def rules(text: str) -> tuple[Optional[Route], str]:
    """``(route, "")`` when a rule decides — a simple ask, or a definite ``none`` — else
    ``(None, cleaned)``: the rules are unsure and the model may be asked about ``cleaned``."""
    raw = str(text or "").strip()
    if not raw:
        return Route(reason="empty", source="rules", confidence=1.0), ""
    if _MACHINERY.search(raw):
        return Route(reason="machinery", source="rules", confidence=1.0), ""
    if "?" in raw or "\n" in raw.strip() or len(raw) > MAX_CHARS:
        return Route(reason="not_simple", source="rules", confidence=1.0), ""
    if _NOT_ASKED.search(raw):
        return Route(reason="not_asked", source="rules", confidence=1.0), ""
    if _OUTBOUND_VERB.search(raw):
        return Route(reason="outbound_verb", source="rules", confidence=1.0), ""
    t = _clean(raw)
    if not t or len(t.split()) > MAX_WORDS or _COMPOUND.search(t):
        return Route(reason="not_simple", source="rules", confidence=1.0), ""
    m = _CALL.match(t) or _CALL_WITH.match(t)
    if m and _person_ok(m.group(1)):
        return Route("call_person", {"person": m.group(1).strip()}, 1.0, "rules", reason="call"), ""
    m = _SCHEDULE.match(t)
    if m and m.group(1):
        args = {"person": m.group(1).strip()}
        if m.group(2):
            args["when"] = m.group(2).strip()
        return Route("schedule", args, 1.0, "rules", reason="schedule"), ""
    m = _INVITE.match(t)
    if m and _person_ok(m.group(1)):
        args = {"person": m.group(1).strip()}
        if m.group(2):
            args["workspace"] = m.group(2).strip()
        return Route("invite_person", args, 1.0, "rules", reason="invite"), ""
    m = _OPEN.match(t)
    if m:
        q = m.group(1).strip().strip("'\"")
        if q and not _OPEN_NOT_ENTITY.match(q) and len(q.split()) <= 5:
            return Route("open_entity", {"query": q}, 1.0, "rules", reason="open"), ""
    if _INTENT_HINT.search(t):
        return None, t                      # short, names an intent word, no rule fits: unsure
    return Route(reason="no_intent", source="rules", confidence=1.0), ""


def _from_model(out, text: str) -> Route:
    """The model's answer, checked: a known intent, string args, every name copied from the text,
    a confidence at or over the floor. Anything else is ``none``."""
    if not isinstance(out, dict):
        return Route(source="model", reason="model_garbage")
    intent = str(out.get("intent") or "none").strip()
    if intent not in INTENTS:
        return Route(source="model", reason="model_unknown_intent")
    try:
        conf = float(out.get("confidence"))
    except (TypeError, ValueError):
        return Route(source="model", reason="model_garbage")
    if not 0.0 <= conf <= 1.0:
        return Route(source="model", reason="model_garbage")
    model_id = str(out.get("_model") or "")
    if intent == "none":
        return Route(source="model", confidence=conf, reason="model_none", model=model_id)
    if conf < CONFIDENCE_FLOOR:
        return Route(source="model", confidence=conf, reason="model_low_confidence", model=model_id)
    raw_args = out.get("args") if isinstance(out.get("args"), dict) else {}
    args = {k: str(v).strip() for k, v in raw_args.items()
            if k in ("person", "query", "workspace", "when") and isinstance(v, str) and str(v).strip()}
    need = "query" if intent == "open_entity" else "person"
    if not args.get(need):
        return Route(source="model", confidence=conf, reason="model_missing_args", model=model_id)
    low = text.lower()
    # NEVER A NAME THE PERSON DID NOT WRITE: a model that invents a person cannot route a call to them.
    for k in ("person", "query", "workspace"):
        if args.get(k) and args[k].lower().lstrip("@") not in low:
            return Route(source="model", confidence=conf, reason="model_invented_args", model=model_id)
    if need == "person" and not _person_ok(args["person"]):
        return Route(source="model", confidence=conf, reason="model_bad_person", model=model_id)
    return Route(intent, args, conf, "model", reason="model", model=model_id)


def _ask_model(complete: Callable, text: str, timeout_s: float):
    """``complete(system, user)`` on a thread, waited for at most ``timeout_s``. ``(answer, timed_out)``."""
    box: dict = {}

    def run() -> None:
        try:
            box["out"] = complete(MODEL_SYSTEM, text)
        except Exception as exc:  # noqa: BLE001 — a failed model is a fall-through, never an error
            box["err"] = type(exc).__name__

    th = threading.Thread(target=run, daemon=True, name="intent-router-model")
    th.start()
    th.join(timeout_s)
    if th.is_alive():
        return None, True
    return box.get("out"), False


def route(text: str, ctx: Optional[dict] = None, *, complete: Optional[Callable] = None,
          clock: Callable[[], float] = time.monotonic, timeout_s: float = MODEL_TIMEOUT_S) -> Route:
    """Route one message: rules first, then (only when they are unsure) the small model.

    ``ctx`` — ``{"mentions": [account ids]}``: one @-mention the composer resolved rides a
    ``call_person`` / ``invite_person`` as ``mention`` (the directory still decides who it is).
    ``complete(system, user) -> dict | None`` is the small model; None skips the model step."""
    t0 = clock()
    r, unsure = rules(text)
    if r is None:
        if complete is None:
            r = Route(source="none", reason="unsure_no_model")
        else:
            out, timed_out = _ask_model(complete, unsure, timeout_s)
            r = Route(source="model", reason="model_timeout") if timed_out else _from_model(out, unsure)
    mentions = [str(m) for m in ((ctx or {}).get("mentions") or []) if str(m).strip()]
    if r.intent in ("call_person", "invite_person") and len(mentions) == 1 \
            and str(r.args.get("person", "")).startswith("@"):
        r.args = {**r.args, "mention": mentions[0]}
    r.latency_ms = max(0.0, (clock() - t0) * 1000.0)
    return r


# ── open_entity: the person's own pages ─────────────────────────────────────────────────────────
_TITLE = re.compile(r"^\s*(title|aliases|id)\s*:\s*(.*)$", re.I)
#: At most this many entity pages are read per lookup — a lookup is a fast path, not a search engine.
MAX_PAGES = 4000


def _norm(s: str) -> str:
    return re.sub(r"[\s_-]+", " ", str(s or "").strip().strip("'\"").lower()).strip()


def _names_of(base, rel: str) -> set:
    """The names a page answers to: its file name, and its front matter's title, id and aliases.
    Read through ``workspace_paths`` (never following a link out of the work tree)."""
    from workspaces.shared import workspace_paths as _wpaths  # noqa: PLC0415

    stem = rel.rsplit("/", 1)[-1][:-3] if rel.endswith(".md") else rel.rsplit("/", 1)[-1]
    names = {_norm(stem)}
    head = _wpaths.read_head_inside(base, rel, 4096)
    if not head or not head.startswith("---"):
        return names
    for line in head.split("\n---", 1)[0].splitlines()[1:]:
        m = _TITLE.match(line)
        if not m:
            continue
        v = m.group(2).strip()
        if m.group(1).lower() == "aliases":
            v = v.strip("[]")
            names.update(_norm(x) for x in v.split(",") if x.strip())
        else:
            names.add(_norm(v))
    names.discard("")
    return names


def find_pages(mounts: list[tuple[str, str]], query: str) -> list[dict]:
    """The knowledge-graph pages (``kg/entities/<kind>/<name>.md``) whose file name, title, id or an
    alias IS ``query`` (case and separators aside), across ``mounts`` = ``[(workspace slug, dir)]`` —
    the mounts the person's own turn would read, nothing wider. ``[{workspace, path, title}]``."""
    from workspaces.shared import workspace_paths as _wpaths  # noqa: PLC0415

    want = _norm(query)
    if not want:
        return []
    out: list[dict] = []
    seen = 0
    for slug, base in mounts:
        for kind in _wpaths.list_dirs_inside(base, "kg/entities"):
            for name in _wpaths.list_files_inside(base, f"kg/entities/{kind}", suffix=".md"):
                seen += 1
                if seen > MAX_PAGES:
                    return out
                if name.lower() in ("index.md", "readme.md"):
                    continue
                rel = f"kg/entities/{kind}/{name}"
                if want in _names_of(base, rel):
                    out.append({"workspace": slug, "path": rel, "title": name[:-3].replace("-", " ")})
    return out


# ── the client's events, in the shapes the harness emits for the same verbs ─────────────────────
_CALL_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")


def call_event(answer: dict) -> Optional[dict]:
    """The ``call`` event a ``call_person`` answer earns — the same shape ``llm/tool_events.py``
    derives from the tool's result, so the terminal joins and rings exactly as it does today."""
    sid = str(answer.get("session_id") or "")
    call = answer.get("call") if isinstance(answer.get("call"), dict) else {}
    room = str(call.get("room") or "")
    if not _CALL_ID.fullmatch(sid) or not _CALL_ID.fullmatch(room):
        return None
    other = answer.get("with") if isinstance(answer.get("with"), dict) else {}
    return {"type": "call", "chat": sid, "room": room, "with": str(other.get("name") or "")[:200],
            "already_live": answer.get("already_live") is True}


def call_line(answer: dict) -> str:
    """The one assistant line a routed call leaves in the chat."""
    other = answer.get("with") if isinstance(answer.get("with"), dict) else {}
    who = str(other.get("name") or "the room")
    ref = str(answer.get("room_ref") or "")
    if answer.get("already_live"):
        line = f"A call with {who} is already live; they were rung again. {ref}"
    else:
        line = f"Calling {who}… {ref}"
    if not answer.get("rang"):
        line += " Nobody was rung."
    return line.strip()


def open_event(page: dict) -> dict:
    """The ``open`` event an ``open_page`` answer earns (``llm/tool_events._open_event``)."""
    ws, path = str(page.get("workspace") or ""), str(page.get("path") or "")
    return {"type": "open", "target": f"{ws}/{path}" if ws else path, "workspace": ws, "path": path}


def open_line(page: dict) -> str:
    ws, path = str(page.get("workspace") or ""), str(page.get("path") or "")
    ref = f"[[page:{ws}/{path}]]" if ws else f"[[page:{path}]]"
    return f"Opening {page.get('title') or path.rsplit('/', 1)[-1]}. {ref}"


def model_complete(catalog, policy: dict, *, timeout_s: float = MODEL_TIMEOUT_S) -> Optional[Callable]:
    """``complete(system, user)`` over the deployment's fast model — the company's
    ``model_intent_router`` (else ``model_home``, ``cheapest`` by default), inside ``models_allowed``,
    with an HTTP deadline under the router's own. None when no catalog is configured."""
    if catalog is None or getattr(catalog, "empty", True):
        return None
    from control_plane import budgets, home_model  # noqa: PLC0415
    import urllib.error  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    def post(url: str, body: dict, headers: dict) -> tuple[int, str]:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"content-type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as r:  # noqa: S310 — the operator's endpoint
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    named = str(policy.get("model_intent_router") or "").strip() or str(policy.get("model_home") or "cheapest")

    def complete(system: str, user: str):
        return home_model.complete_json(catalog, system, user, post=post, named=named,
                                        allowed=budgets.allowed_list(policy))
    return complete
