"""policies.py — THE RULES THIS DEPLOYMENT RUNS UNDER, read from a file an admin can see.

Founder, 2026-09-06: *"we need to get to policy primitives, see how they compose and what effects
pros and cons each has — that's the choice we will let the global admin take."* A rule has one
shape — *subject may action object when relation* — and the admin's answers live in the front matter
of `_global/POLICIES.md`, beside a body that says, per rule, what it changes, what it buys through
adoption, what it costs in security, what a hostile person does with it, and the price of the other
answer. The values and the reasoning are the same file, because a switch whose consequences live
somewhere else is a switch nobody can weigh.

WHY A FILE AND NOT ENV. These were env on the flows lane (`VEXA_FLOWS_ATTENDEE_DOMAINS`, the data
statement). Env is invisible to the person the choice belongs to: an admin cannot read it, cannot
see what it changes, and cannot change it without an operator and a restart. `_global` is git-backed
and mounted into every worker, so an answer is a reviewable commit with an author, read hot on the
next send. **Env stays as the fallback** — a deployment that set it still means it, and a
`POLICIES.md` that is absent (or a `_global` unreachable because the agent domain is not deployed)
must change nothing.

THE RESOLUTION ORDER, and it is the same shape `mailtext.render` and `prompt_for` already use:

    flow param  ->  `_global/POLICIES.md`  ->  env  ->  the default in this file

FAIL SOFT, ALWAYS. Every read is wrapped: an unreachable agent door, a malformed file, a value this
module does not recognise — none of them may fail a send. What cannot be understood falls back to
the default and is named in `problems`, because a policy file that is silently ignored is worse than
one that is not there.

THE DISCLOSURE IS DERIVED, NEVER WRITTEN. `visibility_sentence` composes the sentence every attendee
reads out of the rules above it. There is deliberately no front-matter key for that sentence: a
disclosure a deployment can edit away from what its software actually does is worse than none.
`test_policies.py` pins the default composition to `mailtext.VISIBILITY_SENTENCE` byte for byte —
the founder's own words are what the defaults compose to, and the parity ledger's copies of that
sentence are untouched by this module.
"""
from __future__ import annotations

import logging
from typing import Optional

from .common import ws_file

logger = logging.getLogger(__name__)

#: The file, under the organisation tier. One name, joined by every reader.
POLICIES_FILE = "POLICIES.md"
GLOBAL_SLUG = "_global"

#: The front-matter fence, and the words that mean yes and no. Same small shape as
#: `flows_queue.parse`: one `key: value` per line, between two `---` rules, at the very top or not
#: at all. No YAML parser exists in this engine and none is being added for thirteen lines.
_FENCE = "---"
_TRUE = frozenset({"true", "yes", "on", "1"})
_FALSE = frozenset({"false", "no", "off", "0"})

#: What "kept for as long as this deployment holds it" is spelled as. Not `-1`, not `None`: the
#: value is read by a person before it is read by this module.
FOREVER = "forever"
_FOREVER_WORDS = frozenset({FOREVER, "∞", "always", "indefinitely"})

#: EVERY RULE, WITH ITS DEFAULT, IN THE ORDER THE PAGE WALKS THEM. This table is the contract:
#: `test_policies.py` asserts the seeded `behavior/global/POLICIES.md` declares exactly these keys
#: with exactly these values, so the file a person reads and the defaults the code applies cannot
#: drift. A key here that the file does not carry is a rule nobody can answer; a key in the file
#: that is not here is a control that silently does nothing.
DEFAULTS: dict = {
    # an agent may read its user's desk when its user is a participant (founder decision 21)
    "agent_reads_desk": True,
    # the report is delivered to every participant — the measured adoption lever
    "report_to_participants": True,
    # an external participant is delivered the report
    "external_participants": True,
    # the bot joins a meeting when the mailbox is invited
    "bot_joins_on_invite": True,
    # the organizer confirms each join before the bot is admitted. DECLARED, NOT ENFORCED, and
    # `False` because that is what the engine does today: an invite is the whole decision. A
    # default that claimed the gate exists would be the one direction this table must never
    # lie in — an admin reading `on` would believe a person stands between an invite and a
    # recording. Wizard question 4 (Vexa-ai/vexa#1627) is what asks for it.
    "organizer_confirms_join": False,
    # the bot joins a meeting with external participants
    "bot_joins_mixed_meetings": True,
    # an agent may write pages into a workspace from a meeting when every member was a participant
    "agent_writes_pages": True,
    # how long the words and the audio are kept
    "transcript_retention_days": FOREVER,
    "recording_retention_days": 0,
    # a newcomer to a series reads its earlier reports — the ONE rule that ships off
    "newcomer_reads_history": False,
    # only the admin writes `_global` (editors may be added)
    "global_admin_only": True,
    # an agent may fetch from the open web
    "open_web": True,
    # prep mail and the invite line to organizers — the second measured lever
    "prep_and_invite_mail": True,
    # which domains count as inside. EMPTY IS NOT "EVERYONE": it means the organiser's own domain,
    # exactly as the inbound allow-list unset means the mailbox's own.
    "attendee_domains": (),
    # this deployment's own sentence about where the words live; empty = the derived one
    "data_statement": "",
}

#: `profile:` applies a preset; a key written explicitly in the file wins over it. The two the
#: founder named, and nothing invented beside them.
PROFILES: dict[str, dict] = {
    "default": {},
    # externals off · mixed meetings off · transcript-only retention · open web off · loop levers on
    "bank": {
        "external_participants": False,
        "bot_joins_mixed_meetings": False,
        "recording_retention_days": 0,
        "open_web": False,
        "report_to_participants": True,
        "prep_and_invite_mail": True,
    },
    # the defaults, plus recordings retained
    "studio": {"recording_retention_days": FOREVER},
}


# ── reading the file ─────────────────────────────────────────────────────────────────────────────

def front_matter(raw: str) -> dict:
    """The `key: value` lines between the opening and closing fence, lowercased keys, raw values.

    A file with no fence declares nothing, and a fence that never closes is not front matter — both
    answer `{}` rather than raising, because an admin mid-edit must not be able to break a send."""
    text = (raw or "").strip()
    if not text.startswith(_FENCE):
        return {}
    lines = text.splitlines()
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == _FENCE), None)
    if close is None:
        return {}
    attrs: dict[str, str] = {}
    for line in lines[1:close]:
        if line.strip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if sep and key.strip():
            attrs[key.strip().lower()] = value.strip().strip("\"'")
    return attrs


def _as_bool(value: str, default: bool, key: str, problems: list) -> bool:
    v = value.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    problems.append(f"{key}: {value!r} is neither on nor off — using the default {default!r}")
    return default


def _as_days(value: str, default, key: str, problems: list):
    v = value.strip().lower()
    if v in _FOREVER_WORDS:
        return FOREVER
    if v.isdigit():
        return int(v)
    problems.append(f"{key}: {value!r} is neither a number of days nor {FOREVER!r} — using the "
                    f"default {default!r}")
    return default


def _as_domains(value: str) -> tuple:
    return tuple(d.strip().lower().lstrip("@") for d in value.split(",") if d.strip())


def resolve(raw: Optional[str]) -> dict:
    """The effective rules: this file's defaults, overlaid with `profile:`, overlaid with whatever
    the front matter answers explicitly. Returns every key in `DEFAULTS`, plus `profile` and
    `problems`.

    An EMPTY value is not an answer — `attendee_domains:` with nothing after it means "unset", which
    for that key means the organiser's own domain. That is why the empty string cannot simply be
    coerced: it is the shape an unanswered row has in a file somebody is still filling in."""
    attrs = front_matter(raw or "")
    problems: list[str] = []
    name = (attrs.get("profile") or "default").strip().lower() or "default"
    if name not in PROFILES:
        problems.append(f"profile: {name!r} is not one of {', '.join(sorted(PROFILES))} — "
                        f"using the defaults")
        name = "default"
    out: dict = {"profile": name}
    preset = PROFILES[name]
    for key, default in DEFAULTS.items():
        base = preset.get(key, default)
        raw_value = attrs.get(key, "")
        if not str(raw_value).strip():
            out[key] = base
            continue
        if isinstance(default, bool):
            out[key] = _as_bool(raw_value, bool(base), key, problems)
        elif key.endswith("_days"):
            out[key] = _as_days(raw_value, base, key, problems)
        elif isinstance(default, tuple):
            out[key] = _as_domains(raw_value)
        else:
            out[key] = raw_value
    out["problems"] = problems
    return out


def read(uid: str) -> dict:
    """The rules as this instance holds them RIGHT NOW, read off `_global` per call.

    Not cached, on purpose and for the reason `mailtext.render` is not: an admin who changes a rule
    expects the next mail to carry the change, and policy reads are nowhere near a rate at which one
    HTTP call matters. Never raises — an agent domain that is not deployed (`AgentDomainAbsent`), an
    unreachable door, a file that is not there: all of them are "no answers on file", which resolves
    to the defaults and changes nothing."""
    raw = None
    if str(uid or "").strip():
        try:
            raw = ws_file(str(uid), POLICIES_FILE, GLOBAL_SLUG)
        except Exception:  # noqa: BLE001 — a policy file we cannot fetch is not a reason to fail
            logger.debug("policies: could not read %s from %s — using the defaults",
                         POLICIES_FILE, GLOBAL_SLUG)
            raw = None
    out = resolve(raw)
    for problem in out.get("problems") or ():
        logger.warning("policies: %s", problem)
    return out


def param(ctx, key: str, uid: str = ""):
    """One rule, resolved the way every knob in this engine is: FLOW PARAM first (a flow authored
    against this deployment is the most specific statement there is), then the policy file, then the
    default. Env is NOT in this chain — the two keys that have one keep it at their own call site,
    where the fallback literal also lives."""
    if ctx is not None and getattr(ctx, "flow", None) is not None:
        value = ctx.flow.param(key)
        if value is not None:
            return value
    if not uid:
        uid = str((getattr(ctx, "refs", None) or {}).get("uid") or "")
    return read(uid).get(key, DEFAULTS.get(key))


# ── the sentence attendees read, DERIVED ─────────────────────────────────────────────────────────
#
# Three clauses, joined with "; ". At the defaults they compose to exactly the sentence the founder
# wrote on 2026-09-02 (decision 21) and `test_policies.py` pins that equality against
# `mailtext.VISIBILITY_SENTENCE` — which stays where it is, spelled the way the parity ledger's
# `visibility-sentence` fact expects to find it. Nothing in this module edits a pinned site; it
# composes to the same words and departs from them only where an admin has departed from the
# defaults.

#: Where it runs. Locality is STATED, NOT CHOSEN — it is a fact about the install. A deployment that
#: wants to say it in its own words writes `data_statement`, which replaces this clause and nothing
#: else.
LOCALITY_CLAUSE = "Vexa runs on this organisation's own servers"

#: Who reads what a person keeps. The two answers to `agent_reads_desk`.
DESK_VISIBLE_CLAUSE = ("what you and your colleagues keep in your workspaces is visible to the "
                       "company's agents")
DESK_PRIVATE_CLAUSE = ("what you and your colleagues keep in your workspaces is read only by an "
                       "agent working for its own person")

#: What is kept, at the defaults.
RETENTION_STAYS_CLAUSE = "recordings and transcripts stay here"


def _retention_clause(transcript, recording) -> str:
    if transcript == FOREVER and recording in (0, FOREVER):
        return RETENTION_STAYS_CLAUSE
    if recording == 0:
        return f"transcripts stay here for {transcript} days and no recording is kept"
    if transcript == FOREVER:
        return f"transcripts stay here and recordings for {recording} days"
    if recording == FOREVER:
        return f"transcripts stay here for {transcript} days and recordings stay here"
    return f"transcripts stay here for {transcript} days and recordings for {recording} days"


def visibility_sentence(rules: Optional[dict] = None) -> str:
    """WHO CAN SEE WHAT, composed from the rules. Three facts, one sentence, no disclaimer.

    It goes into the mails a person reads before they have decided whether to keep anything here,
    because that is the only moment at which telling them is a choice they still have."""
    p = dict(DEFAULTS) if rules is None else rules
    locality = str(p.get("data_statement") or "").strip().rstrip(".") or LOCALITY_CLAUSE
    desk = DESK_VISIBLE_CLAUSE if p.get("agent_reads_desk", True) else DESK_PRIVATE_CLAUSE
    retention = _retention_clause(p.get("transcript_retention_days", FOREVER),
                                  p.get("recording_retention_days", 0))
    return f"{locality}; {desk}; {retention}."
