"""meeting_listen.py — the meeting's LISTENING BRIEF: what a small model listens for, written by the agent.

Transcript signals (2026-10-15): a signal model scores each new line of a live meeting against a
brief, and a buffer whose score reaches the brief's `wake_at` wakes the meeting's agent in the
meeting's chat. The brief is the AGENT'S to tune — focus, labels, what it already knows (`known:`),
how sensitive it is — inside company bounds it cannot move: `addressed_to_vexa` is always there and
always wakes, `wake_at` and `max_wait_s` stay inside `BOUNDS`, and the model and caps are policy.

WHERE IT LIVES: the meeting page's front matter, as a `listen:` block, so the people in the meeting
see what Vexa listens for and the brief is versioned with the page. agent-api is its one writer
(`meeting_listen`, through `set_listen`), and `signals` reads it through agent-api's internal API.

    listen:
      focus: "install blockers; decisions on dates"
      labels:
        - {name: decision, question: "Was a decision made?", wake: true}
        - {name: addressed_to_vexa, question: "...", wake: true}
      known: "Proxy date agreed (Tue). Open: TLS CA."
      wake_at: 1.0
      max_wait_s: 300
      threshold: 0.6       # highlight (chip) from this score; display only
      version: 4

POLICY KEYS (`_global/POLICIES.md`, read live): `transcript_signals` (off|on, default off),
`model_signals` ({adapter: jev|openai-compatible, route, secret_ref, base_url?, thinking?}, or those
by tier `{cheapest: …, small: …}`; default none),
`signals_wakes_per_meeting` (12), `signals_cost_per_meeting_usd` (0.50), `signals_default_brief`
(a brief, default the built-in `DEFAULT_BRIEF`).
"""
from __future__ import annotations

import copy
import math
import re
from pathlib import Path
from typing import Optional

import yaml

from control_plane import meeting_note
from workspaces.shared import workspace_paths as wpaths

ADDRESSED = "addressed_to_vexa"
ADDRESSED_QUESTION = ("Is someone speaking to Vexa, the meeting assistant, directly — asking it to "
                      "do, note, find or say something?")
LABEL_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")

#: The company's bounds on what the agent may tune. Not policy keys: the policy decides the caps on
#: wakes and spend; these keep a brief readable and a meeting's wake rhythm sane.
BOUNDS = {"wake_at": (0.2, 10.0), "max_wait_s": (30, 1800), "threshold": (0.0, 1.0)}
MAX_LABELS = 12
#: The rule tier (`meeting_signals.rules`): what wakes the agent at once, with no model call.
RULE_KINDS = ("keyword", "person", "regex")
MAX_RULES = 20
MAX_RULE_VALUE = 200
MAX_FOCUS = 600
MAX_QUESTION = 300
MAX_KNOWN = 4000

DEFAULT_BRIEF = {
    "focus": "decisions, action items and blockers raised in this meeting",
    "labels": [
        {"name": "decision", "question": "Was a decision made, or a choice settled?", "wake": True},
        {"name": "action_item", "question": "Did someone commit to doing something, or ask someone to?",
         "wake": True},
        {"name": ADDRESSED, "question": ADDRESSED_QUESTION, "wake": True},
    ],
    "known": "",
    "wake_at": 1.0,
    "max_wait_s": 300,
    # the score from which a line is HIGHLIGHTED on the transcript (a chip) — display only, separate
    # from `wake_at`, which is what wakes the agent
    "threshold": 0.6,
}

POLICY_DEFAULTS = {
    "transcript_signals": "off",
    "model_signals": None,
    "signals_wakes_per_meeting": 12,
    "signals_cost_per_meeting_usd": 0.50,
    "signals_default_brief": None,
}
ADAPTERS = ("jev", "openai-compatible")
#: The tiers a company may allow signal models by (signals.v1 `ModelSignalsTiers`).
MODEL_TIERS = ("cheapest", "small")
_HTTP_URL = re.compile(r"^https?://[^\s]+$")


def _signal_model(m) -> Optional[dict]:
    """One `model_signals` map as signals takes it, or None. EVERY KEY SIGNALS READS RIDES THROUGH
    (live app.dev 2026-10-18, meeting 162): `base_url` and `thinking` were dropped here, so the
    self-hosted model the company named was called at OpenRouter's address, every call answered
    401, and no line was ever scored — a name said mid-sentence ("…слышат ли нас Векса…") woke
    nobody until a later sentence happened to START with it, 12 s on. signals validates the map
    again (`meeting_signals.policy.parse_model`); this keeps only what it would accept."""
    if not isinstance(m, dict) or str(m.get("adapter") or "").strip() not in ADAPTERS:
        return None
    adapter = str(m["adapter"]).strip()
    out = {"adapter": adapter, "route": str(m.get("route") or "").strip(),
           "secret_ref": str(m.get("secret_ref") or "").strip()}
    if adapter == "openai-compatible":
        base = str(m.get("base_url") or "").strip()
        if base and _HTTP_URL.match(base):
            out["base_url"] = base.rstrip("/")
        thinking = str(m.get("thinking") if m.get("thinking") is not None else "").strip().lower()
        # YAML reads a bare `off`/`on` as a boolean
        thinking = {"false": "off", "true": "on"}.get(thinking, thinking)
        if thinking in ("off", "on"):
            out["thinking"] = thinking
    return out


class BriefError(ValueError):
    """A brief this module will not write. ``str()`` is safe to show the agent."""


# ── policy ─────────────────────────────────────────────────────────────────────────────────

_FM = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)


def _front(text: str) -> tuple[str, str, bool]:
    """(front matter, body, had front matter)."""
    m = _FM.match(text or "")
    if not m:
        return "", text or "", False
    return m.group(1), (text or "")[m.end():], True


def _yaml(text: str) -> dict:
    try:
        out = yaml.safe_load(text or "")
    except yaml.YAMLError:
        return {}
    return out if isinstance(out, dict) else {}


def policy(raw_policies: Optional[str], subject: Optional[str] = None) -> dict:
    """The five signal keys from POLICIES.md's front matter, each falling back to its default when
    absent or unreadable — a typo never turns signals on or lifts a cap.

    ``subject`` (the meeting's or room's owner): an account `features_accounts` names for
    `transcript_signals` is listened to although the company switch is off — the same per-account
    switch that serves it the brief tool. The model and caps stay the company's."""
    fm, _body, _had = _front(raw_policies or "")
    attrs = _yaml(fm)
    out = dict(POLICY_DEFAULTS)
    v = str(attrs.get("transcript_signals") or "").strip().lower()
    if v in ("on", "true", "yes", "1"):
        out["transcript_signals"] = "on"
    elif subject:
        from control_plane import features as _features  # noqa: PLC0415
        if str(subject) in _features.account_answers(raw_policies).get("transcript_signals", frozenset()):
            out["transcript_signals"] = "on"
    m = attrs.get("model_signals")
    if isinstance(m, dict) and m and set(m) <= set(MODEL_TIERS):
        tiers = {t: _signal_model(m[t]) for t in MODEL_TIERS if t in m}
        tiers = {t: v for t, v in tiers.items() if v is not None}
        out["model_signals"] = tiers or None
    else:
        out["model_signals"] = _signal_model(m)
    try:
        n = int(str(attrs.get("signals_wakes_per_meeting", "")).strip())
        if 0 <= n <= 1000:
            out["signals_wakes_per_meeting"] = n
    except ValueError:
        pass
    try:
        c = float(str(attrs.get("signals_cost_per_meeting_usd", "")).strip())
        if 0 <= c <= 1000 and math.isfinite(c):
            out["signals_cost_per_meeting_usd"] = c
    except ValueError:
        pass
    b = attrs.get("signals_default_brief")
    if isinstance(b, dict):
        try:
            out["signals_default_brief"] = normalize(b)
        except BriefError:
            pass
    return out


def default_brief(pol: Optional[dict] = None) -> dict:
    """The company's default brief (`signals_default_brief`), else the built-in one."""
    b = (pol or {}).get("signals_default_brief")
    return copy.deepcopy(b) if isinstance(b, dict) else normalize(DEFAULT_BRIEF)


# ── the brief's shape ──────────────────────────────────────────────────────────────────────

def _text(v, cap: int, what: str) -> str:
    if v is None:
        return ""
    if not isinstance(v, str):
        raise BriefError(f"{what} must be text")
    v = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]+", " ", v).strip()
    if len(v) > cap:
        raise BriefError(f"{what} is longer than {cap} characters")
    return v


def _bounded(v, key: str, default):
    lo, hi = BOUNDS[key]
    if v is None or v == "":
        return default
    try:
        n = float(v)
    except (TypeError, ValueError):
        raise BriefError(f"{key} must be a number") from None
    if not math.isfinite(n):
        raise BriefError(f"{key} must be a number")
    n = min(max(n, lo), hi)          # bounded, not refused: the agent tunes inside the bounds
    return int(n) if key == "max_wait_s" else round(n, 3)


def normalize(brief: dict, *, base: Optional[dict] = None) -> dict:
    """The brief as it is written: validated, `addressed_to_vexa` present and waking, `wake_at` and
    `max_wait_s` inside `BOUNDS`. Fields the agent leaves out keep ``base``'s (the page's current
    brief, else the default). Raises `BriefError` on a shape it will not write."""
    if not isinstance(brief, dict):
        raise BriefError("the brief must be an object")
    unknown = set(brief) - {"focus", "labels", "rules", "known", "wake_at", "max_wait_s", "threshold",
                            "version"}
    if unknown:
        raise BriefError(f"unknown brief field(s): {', '.join(sorted(unknown))}")
    base = base if isinstance(base, dict) else DEFAULT_BRIEF
    focus = _text(brief.get("focus", base.get("focus", "")), MAX_FOCUS, "focus")
    known = _text(brief.get("known", base.get("known", "")), MAX_KNOWN, "known")
    raw_labels = brief.get("labels", base.get("labels") or [])
    if not isinstance(raw_labels, list):
        raise BriefError("labels must be a list")
    labels, seen = [], set()
    for item in raw_labels:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            raise BriefError("each label is {name, question, wake}")
        extra = set(item) - {"name", "question", "wake"}
        if extra:
            raise BriefError(f"unknown label field(s): {', '.join(sorted(extra))}")
        name = str(item.get("name") or "").strip()
        if not LABEL_RE.match(name):
            raise BriefError(f"label name {name!r} must be lower_snake_case, at most 40 characters")
        if name in seen:
            continue
        seen.add(name)
        wake = item.get("wake", True)
        if not isinstance(wake, bool):
            raise BriefError(f"label {name}: wake must be true or false")
        question = _text(item.get("question"), MAX_QUESTION, f"label {name}: question")
        if name == ADDRESSED:
            # ALWAYS ON (founder decision 2): the agent cannot remove it, mute it or reword it away.
            wake, question = True, question or ADDRESSED_QUESTION
        labels.append({"name": name, "question": question or name.replace("_", " "), "wake": wake})
    if ADDRESSED not in seen:
        labels.append({"name": ADDRESSED, "question": ADDRESSED_QUESTION, "wake": True})
    if len(labels) > MAX_LABELS:
        raise BriefError(f"at most {MAX_LABELS} labels, {ADDRESSED} included")
    rules = _rules(brief.get("rules", base.get("rules") or []))
    return {**_core(focus, labels, known, brief, base), **({"rules": rules} if rules else {})}


#: A pattern with a quantified group that itself holds a quantifier (`(a+)+`) — the signals service
#: refuses it (`meeting_signals.rules`); it is refused here first, where the agent hears why.
_NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*}](?:[^()\\]|\\.)*\)\s*[+*{]")


def _rules(raw) -> list:
    """The brief's rule tier as it is written: `{kind, value, wake}`, at most `MAX_RULES`."""
    if not isinstance(raw, list):
        raise BriefError("rules must be a list")
    out, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise BriefError("each rule is {kind, value, wake}")
        extra = set(item) - {"kind", "value", "wake"}
        if extra:
            raise BriefError(f"unknown rule field(s): {', '.join(sorted(extra))}")
        kind = str(item.get("kind") or "")
        if kind not in RULE_KINDS:
            raise BriefError(f"rule kind must be one of {', '.join(RULE_KINDS)}")
        value = _text(item.get("value"), MAX_RULE_VALUE, f"{kind} rule")
        if not value:
            raise BriefError(f"{kind} rule: value is empty")
        if kind == "regex":
            if _NESTED_QUANTIFIER.search(value):
                raise BriefError("regex rule: a quantified group holding a quantifier is refused")
            try:
                re.compile(value)
            except re.error as exc:
                raise BriefError(f"regex rule: {exc}") from None
        wake = item.get("wake", True)
        if not isinstance(wake, bool):
            raise BriefError(f"{kind} rule {value!r}: wake must be true or false")
        if (kind, value.casefold()) in seen:
            continue
        seen.add((kind, value.casefold()))
        out.append({"kind": kind, "value": value, "wake": wake})
    if len(out) > MAX_RULES:
        raise BriefError(f"at most {MAX_RULES} rules")
    return out


def _core(focus, labels, known, brief, base) -> dict:
    return {"focus": focus, "labels": labels, "known": known,
            "wake_at": _bounded(brief.get("wake_at", base.get("wake_at")), "wake_at", DEFAULT_BRIEF["wake_at"]),
            "max_wait_s": _bounded(brief.get("max_wait_s", base.get("max_wait_s")), "max_wait_s",
                                   DEFAULT_BRIEF["max_wait_s"]),
            "threshold": _bounded(brief.get("threshold", base.get("threshold")), "threshold",
                                  DEFAULT_BRIEF["threshold"])}


# ── the page's front matter ────────────────────────────────────────────────────────────────

_LISTEN_LINE = re.compile(r"^listen\s*:")


def _split_listen(fm: str) -> tuple[list[str], list[str]]:
    """(front matter lines without the `listen:` block, the block's lines)."""
    keep, block, inside = [], [], False
    for line in fm.splitlines():
        if _LISTEN_LINE.match(line):
            inside = True
            block.append(line)
            continue
        if inside and (not line.strip() or line[:1] in (" ", "\t", "-")):
            block.append(line)
            continue
        inside = False
        keep.append(line)
    return keep, block


def read_listen(text: str) -> tuple[Optional[dict], int]:
    """(the page's brief, its version) — ``(None, 0)`` when the page has none or it is unreadable.
    Read from the `listen:` block alone, so a page whose other keys are not YAML still answers."""
    fm, _body, had = _front(text or "")
    if not had:
        return None, 0
    _keep, block = _split_listen(fm)
    if not block:
        return None, 0
    raw = _yaml("\n".join(block)).get("listen")
    if not isinstance(raw, dict):
        return None, 0
    try:
        version = max(0, int(raw.get("version") or 0))
    except (TypeError, ValueError):
        version = 0
    try:
        return normalize({k: v for k, v in raw.items() if k != "version"}), version
    except BriefError:
        return None, version


def set_listen(text: str, brief: dict, version: int) -> str:
    """``text`` with its `listen:` block replaced by ``brief`` at ``version`` — every other line of
    the page, front matter included, byte for byte."""
    block = yaml.safe_dump({"listen": {**brief, "version": int(version)}}, sort_keys=False,
                           allow_unicode=True, width=1000, default_flow_style=False).rstrip("\n")
    fm, body, had = _front(text or "")
    if not had:
        return f"---\n{block}\n---\n" + (text or "")
    keep, _old = _split_listen(fm)
    lines = keep + block.splitlines()
    return "---\n" + "\n".join(lines) + "\n---\n" + body


# ── where the page is ──────────────────────────────────────────────────────────────────────

def locate(workspaces_root, subject: str, row) -> Optional[dict]:
    """This meeting's page on this person's side: ``{path, workspace, dir}`` (``workspace`` ``""`` is
    their home), or None when no page names the meeting yet."""
    for place in meeting_note.note_places(workspaces_root, subject):
        if place.get("hidden"):
            continue
        hit = meeting_note.resolve_in(place["dir"], row)
        if hit:
            return {"path": hit, "workspace": place["slug"], "dir": Path(place["dir"])}
    return None


def page_text(place: dict) -> str:
    """The page's text, read nofollow inside its workspace; ``""`` when it cannot be read."""
    try:
        return wpaths.read_text_inside(place["dir"], place["path"]) or ""
    except (OSError, ValueError):
        return ""
