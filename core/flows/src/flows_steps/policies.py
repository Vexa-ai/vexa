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

ABSENT IS THE DEFAULTS; UNREADABLE IS CLOSED. A file that is not there (404), or an agent domain
this deployment does not run, means nobody answered — the defaults, which change nothing. A file
that IS there but could not be read (agent-api restarting, a timeout, an error status), or a
`profile:` this module does not know, means an admin answered and we could not hear what: then
every rule that widens who is mailed, joined or read takes its CLOSED value (`CLOSED`) and the
failure is logged at warning. A `profile: bank` deployment must not mail the people it excluded
because a door blinked. No read ever raises: a send is never failed by a policy read, it is
narrowed. A value this module does not recognise falls back to the default and is named in
`problems`, because a policy file that is silently ignored is worse than one that is not there.

THE DISCLOSURE IS DERIVED, NEVER WRITTEN. `visibility_sentence` composes the sentence every attendee
reads out of the rules above it. There is deliberately no front-matter key for that sentence: a
disclosure a deployment can edit away from what its software actually does is worse than none.
`test_policies.py` pins the default composition to `mailtext.VISIBILITY_SENTENCE` byte for byte —
the founder's own words are what the defaults compose to, and the parity ledger's copies of that
sentence are untouched by this module.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from . import mailkinds
from .common import AgentDomainAbsent, swallowed, ws_get

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
    # an agent may read its user's desk when its user is a participant (founder decision 21).
    # DECLARED, NOT ENFORCED: a room mounts no other person's desk whatever it says, and a desk is
    # readable by any signed-in member (agent-api `_read_target`). The disclosure does not follow it.
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
    # how long the words and the audio are kept. DECLARED, NOT ENFORCED: nothing deletes a
    # transcript or a recording on a schedule, and the disclosure does not follow them.
    "transcript_retention_days": FOREVER,
    "recording_retention_days": 0,
    # a newcomer to a series reads its earlier reports — the ONE rule that ships off
    "newcomer_reads_history": False,
    # only the admin account writes `_global` (identity names it; admin-api)
    "global_admin_only": True,
    # an agent may fetch from the open web
    "open_web": True,
    # prep mail and the invite line to organizers — the second measured lever
    "prep_and_invite_mail": True,
    # the minutes mail to the organiser (and to a bound group's members) after a meeting
    "minutes_mail": True,
    # which domains count as inside. EMPTY IS NOT "EVERYONE": it means the organiser's own domain,
    # exactly as the inbound allow-list unset means the mailbox's own.
    "attendee_domains": (),
    # which domains the MAILBOX takes invitations and mail from (the inbound allow-list). EMPTY IS
    # NOT "EVERYONE": it falls back to `VEXA_FLOWS_MAIL_DOMAINS`, then to the mailbox's own domain.
    "mail_domains": (),
    # this deployment's own sentence about where the words live; empty = the derived one
    "data_statement": "",
    # the steps a PERSON's own flow may name (ADR-0046 decision 6). EMPTY IS NOT "NONE": it means
    # the steps the image marks user-allowed, which is `agent_run` alone. A list here narrows or
    # widens that set within the deployed vocabulary.
    "user_flow_steps": (),
    # WHAT A PERSON'S ROUTINE MAY BE GRANTED TO DO WITH NOBODY IN THE LOOP (flows phase 0, founder
    # 2026-10-14). A grant is named by the person when they make the routine in chat and shown to
    # them; this list caps what any routine may hold (`none` for nothing). Read by flows-api when a
    # routine is saved and by the worker at every run (`flows_steps.grants`).
    "routine_unattended_actions": ("bot.request", "bot.stop", "flow.retire_self"),
    # A ROUTINE WHOSE CHAT BECAME SHARED (Vexa-ai/vexa#1783): a shared turn reads only what every
    # participant can, so a run refused a private source there (`shared_conversation`) pauses the
    # routine with one line in its chat (`on`), or fails that run and keeps the routine on (`off`).
    # Read by the flows worker at the end of every routine run (`flows_steps.routine`).
    "routine_pauses_when_shared": True,
    # AGENTS SPEAK TO EACH OTHER (ADR-0046 decision 13). Between one person's own threads; between
    # two people only when this is switched on, and then only into a conversation (`s-…`) both take
    # part in, inside a workspace both belong to where the sender writes; never from a routine
    # running with nobody in the loop unless this says so.
    "agent_messages_own": True,
    "agent_messages_shared": False,
    "agent_messages_from_routines": False,
    # ADR-0048: with two or more people in a chat, the agent answers `mentioned` (when @-named or
    # asked directly) or `always`. Read by agent-api (`control_plane/session_access.py`), which
    # reads a channel workspace's own POLICIES.md first; listed here so file and table stay one set.
    "session_agent_speaks": "mentioned",
    # WHAT AN AGENT MAY DO WITH A PERSON'S CONNECTED MAIL AND CALENDAR (founder, 2026-10-12: "this
    # should be policy configurable"). Read and ENFORCED by agent-api's `connection_policy` (typed,
    # and loud: a value it cannot read refuses every connection action) and again by the credential
    # broker; no flow reads them. They are here, spelled as the seed spells them, so this table stays
    # the one list of every key the file may carry — `core/agent/tests/test_connection_policy.py`
    # holds agent-api's table to these values.
    "connection_actions": "all",
    "connection_actions_unattended": "none",
    "calendar_notify_attendees": False,
    "calendar_attendee_domains": "any",
    "calendar_ids": "primary",
    "draft_recipient_domains": "any",
    "draft_attachments": False,
    "draft_attachment_max_kb": "5120",
    # ADR-0048 decision 7: a change proposed after a call waits for its owner (`off`) or lands at
    # once in their name (`on`). Read by agent-api (`control_plane/routers/calls.py`).
    "post_meeting_auto_accept": False,
    # HOW LARGE A DROPPED FOLDER MAY BE (founder 2026-10-13: "as we drop calls we can drop folder").
    # Read and enforced by agent-api's `folder_import` (typed and loud, like the connection keys); no
    # flow reads them. Listed here so this table stays the one list of every key the file may carry.
    "folder_import_max_mb": "200",
    "folder_import_max_files": "10000",
    "folder_import_file_max_mb": "25",
    # HOW MANY TOOL CALLS ONE CHAT TURN MAY MAKE (Vexa-ai/vexa#1783). Read by agent-api's
    # `turn_budget` at dispatch; a workspace or a person may only narrow it. No flow reads it.
    "chat_turn_max_tool_calls": "40",
    # HOW LONG ONE SHELL COMMAND OF THE AGENT MAY RUN, in seconds (live app.dev 2026-10-15). Read by
    # agent-api at dispatch (`turn_budget.shell_timeout`, narrow-only) and enforced in the worker by
    # `llm/shell_guard.sh`; no flow reads it.
    "shell_timeout_sec": "120",
    # WHICH UNPROVEN FEATURES ARE ON (founder 2026-10-13). Read and enforced by agent-api's
    # `features` (the install default is its environment); no flow reads it. Empty = the install's answer.
    "features": "",
    # WHAT MAIL THIS DEPLOYMENT SENDS, PER KIND (founder 2026-10-14: "the actual notifications should
    # be decided by the global account"). `mail.<kind>: on | off | admins`, one row per kind in
    # `mailkinds.KINDS`; the sign-in link is always on whatever its row says. `mail_caps` is the
    # per-kind daily ceiling (`minutes=50, *=500`), under the operator's own ceiling.
    **mailkinds.policy_defaults(),
    "mail_caps": "",
    # HOW LONG THE NEW-CHAT HOME'S HISTORY IS KEPT (#1783). Read by agent-api's `home` on every
    # composition; no flow reads it. Listed so this table stays the one list of every key.
    "home_history_retention_days": "forever",
    # WHETHER AVATARS AND COMPANY LOGOS ARE FETCHED (#1783), by agent-api's `home_media`; no flow reads it.
    "external_images": True,
    # THE HOME'S SWITCHES (#1783): agent-api's home reads them; no flow does.
    "home_cards": "off",
    "home_people": "on",
    # WHO SEES WHETHER A COLLEAGUE IS ACTIVE (desktop D1, founder 2026-10-15: "like in social media,
    # and that's configurable in global"). `off` · `shared_chats` · `workspace_members`. Read by
    # agent-api's desktop (`routers/desktop.py`) on every read; a person's own switch still wins and a
    # value it cannot read is `off`. No flow reads it; listed so this table stays the one list of keys.
    "presence_visibility": "workspace_members",
    # THE DESKTOP'S LIMITS (desktop D1, ADR desktop-cards §2.7). Read and enforced by crm-api's desk
    # (`core/crm/src/crm/policies.py`, through agent-api's door, cached 30 s); a company may only narrow
    # them, and a wider or unreadable value is the default. No flow reads them; listed so this table
    # stays the one list of every key the file may carry.
    "desk_placements_max": "50",
    "card_proposals_pending_max": "5",
    "card_proposals_per_agent_per_day": "20",
    "card_proposal_ttl_days": 7,
    # THE CARD LIFECYCLE (desktop D2): stages in order, then side states after `|`. Read by crm-api's
    # cards (`core/crm/src/crm/card_ops.py`); a company's own card types add `lifecycles.<type>`.
    "lifecycles.card": "To do > Doing > Done | Dropped",
    # When an unfinished card that has not moved shows "Stalled" (days; crm-api's cards read it).
    "card_stalled_days": 14,
    # BUDGETS AND MODEL POLICY (#1783, founder: "globally set-up policy — budgets for tokens for
    # users and routines, model policies"). agent-api's `budgets` reads and enforces the token,
    # home, event and model keys; flows reads the two routine quotas live (`company_quota`). An empty
    # value is the install default (the environment), never "unlimited"; the seed carries these.
    "budget_tokens_per_user_day": "2000000",
    "budget_tokens_routines_share": "50",
    "routine_runs_per_day": "48",
    "routine_min_interval_minutes": "10",
    "home_compose_per_day": "14",
    "home_expand_per_day": "40",
    "event_agent_runs_per_day": "30",
    # PRD 0.13.4 S2: days a mailed invite's link signs its one address in (agent-api's invite act
    # reads it through `budgets`; 0 = never, at most 30).
    "invite_signin_days": 7,
    "event_agent_max_trust": "draft",
    "model_chat_default": "",
    "models_allowed": "all",
    "model_routines": "cheapest",
    "model_home": "cheapest",
    "model_event_agents": "cheapest",
    "models_for": "",
    "model_rules": "",
    "model_levels": "",
    "appearance_accent": "clay",
    "appearance_font_ui": "geist",
    "appearance_font_reading": "ui",
    # WHAT THIS DEPLOYMENT MAY SPEND ON MODEL CALLS, in US dollars (founder, production cutover).
    # Read and enforced by agent-api's `llm_budget` before every turn; no flow reads them. Listed
    # here so this table stays the one list of every key the file may carry;
    # `core/agent/tests/test_llm_budget_policy.py` holds agent-api's table to these values.
    "llm_budget_org_daily_usd": "25",
    "llm_budget_org_monthly_usd": "250",
    "llm_budget_user_daily_usd": "5",
    "llm_budget_user_monthly_usd": "50",
    # empty: a model the market price table does not name counts at its base open-weight price
    "llm_price_input_usd_per_mtok": "",
    "llm_price_output_usd_per_mtok": "",
    "llm_budget_notify": "",
    # TRANSCRIPT SIGNALS (2026-10-15): agent-api's event door and its internal brief read take
    # these, and the signals service gets them through that read; no flow decides on them.
    "transcript_signals": "off",
    "model_signals": "",
    "signals_wakes_per_meeting": "12",
    "signals_cost_per_meeting_usd": "0.50",
    "signals_default_brief": "",
    # VEXA CREDITS (agent-api `credits`, feature `usage`): market cost x the markup; the plan's 5-hour
    # and weekly allowances in credits ($). Empty allowance: no window limit.
    "credit_markup": "1.3",
    "credits_5h": "",
    "credits_week": "",
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


#: THE CLOSED VALUE OF EVERY RULE THAT WIDENS WHO IS MAILED, JOINED OR READ — what applies when an
#: admin's file exists but cannot be read, or names a profile this module does not know. Each is the
#: answer under which the engine does LESS: no mail to externals or attendees, no prep or minutes
#: mail, no join on an invite or into a mixed meeting, no desk read, no page written, no open web,
#: no agent message across people. Mail kinds are `off`. Keys not listed keep their default: they
#: either narrow already or are read and enforced loudly by their own owner (agent-api, crm-api).
CLOSED: dict = {
    "agent_reads_desk": False,
    "report_to_participants": False,
    "external_participants": False,
    "bot_joins_on_invite": False,
    "bot_joins_mixed_meetings": False,
    "agent_writes_pages": False,
    "open_web": False,
    "prep_and_invite_mail": False,
    "minutes_mail": False,
    "agent_messages_own": False,
    "agent_messages_shared": False,
    "agent_messages_from_routines": False,
    **{k: "off" for k in mailkinds.policy_defaults()},
}


class PolicyUnreadable(RuntimeError):
    """`_global/POLICIES.md` exists, or may exist, and could not be read."""


def ws_file(uid: str, path: str, slug: Optional[str] = None) -> Optional[str]:
    """The policy file's text; None when it is not there (404); `PolicyUnreadable` for any other
    answer. The seam `read` fetches through — the one place absent and unreadable part ways."""
    code, body = ws_get(uid, path, slug)
    if code == 200 and isinstance(body, dict):
        return str(body.get("content") or "")
    if code == 404:
        return None
    raise PolicyUnreadable(f"agent-api answered {code} for {GLOBAL_SLUG}/{path}")


def closed(problem: str) -> dict:
    """The rules with every widening rule at its CLOSED value, `problem` named in `problems`."""
    out = resolve(None)
    out.update(CLOSED)
    out["problems"] = [problem]
    out["_explicit"] = frozenset(CLOSED)      # a closed mail kind reads `off`, not "unanswered"
    return out


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


def message_refusal(rules: dict, refs: dict) -> "str | None":
    """THE POLICY KEY THAT REFUSES THIS AGENT-TO-AGENT MESSAGE, or None (ADR-0046 decision 13).

    `refs` are the `thread.message` carrier's: who sends (`sender_uid`), to whose thread (`uid`),
    whether the two share a workspace (`shared`) and whether the sender is a routine running with
    nobody in the loop (`sender_routine`). A message between two people is refused under
    `agent_messages_shared` unless the key is on, they share a workspace, and the target thread is
    a stored conversation (`s-…`) — never the other person's own threads or routines.
    """
    r = rules or {}
    if refs.get("sender_routine") and not r.get("agent_messages_from_routines", False):
        return "agent_messages_from_routines"
    own = str(refs.get("sender_uid") or "") == str(refs.get("uid") or "")
    if own:
        return None if r.get("agent_messages_own", True) else "agent_messages_own"
    if (not r.get("agent_messages_shared", False) or not refs.get("shared")
            or not str(refs.get("thread") or "").startswith("s-")):
        return "agent_messages_shared"
    return None


def user_flow_steps(rules: dict, marked: "set[str] | frozenset", vocabulary) -> set:
    """THE STEPS A PERSON'S FLOW MAY NAME: the policy's list within the vocabulary, or — when the
    policy says nothing — the steps the image marks user-allowed."""
    listed = tuple((rules or {}).get("user_flow_steps") or ())
    vocab = set(vocabulary)
    return (set(listed) & vocab) if listed else (set(marked) & vocab)


def _as_domains(value: str) -> tuple:
    return tuple(d.strip().lower().lstrip("@") for d in value.split(",") if d.strip())


#: WHAT A DOMAIN IN AN ALLOW-LIST LOOKS LIKE: labels of letters, digits and hyphens, at least one dot.
#: `*`, `com`, `@`, an address and a URL are not domains, and an allow-list is the one value where
#: reading a typo generously widens a door on the public internet.
_DOMAIN_RE = re.compile(r"^(?=.{3,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")

#: The allow-lists whose every entry must be a domain. `attendee_domains` is read as it always was;
#: `mail_domains` is new and guards an INBOUND door, so it is checked on the way in.
_STRICT_DOMAIN_KEYS = frozenset({"mail_domains"})


def _as_strict_domains(value: str, key: str, problems: list) -> tuple:
    """Every entry that is a domain; every other entry DROPPED and named. Dropping narrows: an
    allow-list left empty by it falls back to the env, then to the mailbox's own domain."""
    out = []
    for d in _as_domains(value):
        if _DOMAIN_RE.match(d):
            out.append(d)
        else:
            problems.append(f"{key}: {d!r} is not a domain — ignored")
    return tuple(out)


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
    unknown_profile = name not in PROFILES
    if unknown_profile:
        problems.append(f"profile: {name!r} is not one of {', '.join(sorted(PROFILES))} — "
                        f"every widening rule is closed until it names one")
        name = "default"
    out: dict = {"profile": name}
    # An admin who asked for a profile we cannot apply asked for SOMETHING narrower than nothing;
    # the closed values stand in for it, and a row the file answers explicitly still wins.
    preset = {**PROFILES[name], **(CLOSED if unknown_profile else {})}
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
        elif isinstance(default, tuple) and key in _STRICT_DOMAIN_KEYS:
            out[key] = _as_strict_domains(raw_value, key, problems)
        elif isinstance(default, tuple):
            out[key] = _as_domains(raw_value)
        elif key.startswith("mail.") and key[5:] in mailkinds.KINDS:
            v = str(raw_value).strip().lower()
            if v in mailkinds.SWITCH_VALUES:
                out[key] = v
            else:
                problems.append(f"{key}: {raw_value!r} is not on, off or admins — using the "
                                f"default {base!r}")
                out[key] = base
        else:
            out[key] = raw_value
    out["problems"] = problems
    #: the keys the page actually answered — `mailkinds.switch` tells "written off" from "unanswered"
    out["_explicit"] = (frozenset(k for k, v in attrs.items() if str(v).strip())
                        | (frozenset(CLOSED) if unknown_profile else frozenset()))
    return out


def read(uid: str) -> dict:
    """The rules as this instance holds them RIGHT NOW, read off `_global` per call.

    Not cached, on purpose and for the reason `mailtext.render` is not: an admin who changes a rule
    expects the next mail to carry the change, and policy reads are nowhere near a rate at which one
    HTTP call matters. Never raises. An agent domain that is not deployed (`AgentDomainAbsent`) or
    a door that answered 404 is "no answers on file": the defaults. A file that could not be read is
    the CLOSED rules (module docstring), logged at warning. With no `uid` the company layer is read
    as `COMPANY_READER` — `_global` is every subject's to read, so nobody is needed to read it."""
    reader = str(uid or "").strip() or COMPANY_READER
    try:
        out = resolve(ws_file(reader, POLICIES_FILE, GLOBAL_SLUG))
    except AgentDomainAbsent:
        out = resolve(None)             # no agent domain here: nobody can have answered
    except Exception as exc:  # noqa: BLE001 — a send is narrowed by this, never failed
        swallowed("policies.read", "policy-file-unreadable", exc, file=f"{GLOBAL_SLUG}/{POLICIES_FILE}")
        out = closed(f"{GLOBAL_SLUG}/{POLICIES_FILE} could not be read "
                     f"({type(exc).__name__}) — every widening rule is closed")
    for problem in out.get("problems") or ():
        logger.warning("policies: %s", problem)
    return out


_CQ: dict = {"at": 0.0, "rules": None}
COMPANY_TTL_S = 30.0


def company_quota(key: str, env_name: str, *, scale: int = 1, now: "float | None" = None) -> int:
    """A routine quota as the COMPANY holds it now (`routine_runs_per_day`,
    `routine_min_interval_minutes`), read live from `_global/POLICIES.md` at most every 30 s, else the
    install default from the environment. A value that is not a whole number is the default."""
    import time as _t  # noqa: PLC0415
    import flows_config  # noqa: PLC0415
    now = _t.time() if now is None else now
    if _CQ["rules"] is None or now - _CQ["at"] > COMPANY_TTL_S:
        try:
            _CQ["rules"] = read_company()
        except Exception:  # noqa: BLE001
            _CQ["rules"] = {}
        _CQ["at"] = now
    v = str((_CQ["rules"] or {}).get(key) or "").strip().replace(",", "")
    return int(v) * scale if v.isdigit() else flows_config.get_int(env_name)


#: WHO READS `_global` WHEN NO PERSON IS INVOLVED. The inbound mailbox decides whether to act on a
#: message before it knows whose message it is, so there is no uid to read the company layer as.
#: `_global` is readable by every subject (agent-api `_read_target`), so the subject named here
#: grants nothing; it exists because the internal-tier door names somebody on every request, and a
#: log line that says `flows` is more honest than one that borrows a person's id.
COMPANY_READER = "flows"


def read_company() -> dict:
    """The rules as the COMPANY holds them, for a decision taken before any person is known — the
    inbound mailbox's allow-list and its invite switch. Same file, same absent-is-defaults and
    unreadable-is-closed rule as `read`; only the reader differs."""
    return read(COMPANY_READER)


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


# ── the sentence attendees read ──────────────────────────────────────────────────────────────────
#
# Two clauses, joined with "; ". It composes to exactly `mailtext.VISIBILITY_SENTENCE`, and
# `test_policies.py` pins that equality; the constant stays where it is, spelled the way the parity
# ledger's `visibility-sentence` fact expects to find it.
#
# EVERY CLAUSE IS ONE THE SOFTWARE BACKS, WHATEVER THE RULES SAY AND HOWEVER IT IS DEPLOYED. A
# clause that followed a rule nothing enforces would tell a stranger something the install does not
# do, so only `data_statement` (a deployment's own words about itself) changes the sentence.
# `agent_reads_desk` and the two retention rules are declared and not yet enforced (`POLICIES.md`
# § *Where each rule is read today*), and the sentence does not follow them. Nor does it say where
# recordings and transcripts are kept: a supported install may send the audio to a hosted
# transcription service, keep recordings in an external object store and hand transcripts to a
# hosted model, and only the operator knows which. An operator who can promise it writes it in
# `data_statement`.

#: Where it runs. Locality is STATED, NOT CHOSEN — it is a fact about the install. A deployment that
#: wants to say it in its own words writes `data_statement`, which replaces this clause and nothing
#: else.
LOCALITY_CLAUSE = "Vexa runs on this organisation's own servers"

#: Who reads what a person keeps. agent-api serves a desk to any signed-in member of the instance,
#: and so to any agent working for one (`api._read_target`, founder ruling 2026-09-02; pinned by
#: agent `test_agent_api_authz.py::test_a_colleagues_desk_is_readable_and_never_writable`). No rule
#: narrows that read, `agent_reads_desk` included.
DESK_VISIBLE_CLAUSE = ("what you and your colleagues keep in your workspaces is visible to the "
                       "company's agents")


def visibility_sentence(rules: Optional[dict] = None) -> str:
    """WHO CAN SEE WHAT. Two facts, one sentence, no disclaimer.

    It goes into the mails a person reads before they have decided whether to keep anything here,
    because that is the only moment at which telling them is a choice they still have."""
    p = dict(DEFAULTS) if rules is None else rules
    locality = str(p.get("data_statement") or "").strip().rstrip(".") or LOCALITY_CLAUSE
    return f"{locality}; {DESK_VISIBLE_CLAUSE}."
