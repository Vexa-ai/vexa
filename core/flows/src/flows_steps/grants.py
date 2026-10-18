"""grants.py — what a person's routine may do with nobody in the loop (flows phase 0, founder 2026-10-14).

A routine runs unwatched, so every verb that needs a person refuses it (`human_session_required`). On
2026-10-14 a one-shot routine made in chat to send a bot at 12:13 fired, its `request_meeting_bot`
and its self-retire were both refused, and the run said `done`: unattended routines could not do the
thing they were made for. The answer is not "let routines do more". It is a GRANT:

* EXPLICIT AND NAMED. A routine carries `params.grants`, a list from the closed vocabulary below,
  written when its person creates it in their chat (`POST /flows` is person-only) and shown to them in
  words — "This routine may: send bots to meetings." Nothing is implied by a routine's instruction.
* CAPPED BY THE COMPANY. `_global/POLICIES.md` `routine_unattended_actions` lists the verbs any
  routine may be granted (`none` for none). Checked when the routine is saved and again at every run,
  so an administrator who narrows it narrows every routine from its next run.
* CHECKED PER CALL, WHERE THE REFUSAL LIVES. flows sends the run's grants to agent-api over the
  internal tier; agent-api mints them into the run's delegation (delegation.v1 `scope.grants` beside
  `scope.flow`); the service that refuses an unwatched caller lets exactly the granted verb through
  (meeting-api `POST /bots` for `bot.request`; flows-api retire/pause of that routine itself for
  `flow.retire_self`). A no-LLM step checks the same grant here before it acts.
* REVOCABLE AND AUDITED. `flow_lifecycle … revoke_grants` drops them from the routine; each granted
  use is logged (`routine_grant_used`) by the service that let it through.

The vocabulary is delegation.v1's `UNATTENDED_GRANTS`, restated here (flows does not vendor that
module); `tests/test_routine_phase0.py` holds the two to one list.
"""
from __future__ import annotations

from typing import Iterable

#: delegation.v1 `UNATTENDED_GRANTS` — the whole vocabulary, in the order the sentence names them.
VOCAB = ("bot.request", "bot.stop", "flow.retire_self")

#: How each grant reads to the person who gives it.
WORDS = {"bot.request": "send bots to meetings", "bot.stop": "stop its bots",
         "flow.retire_self": "retire itself"}

#: The `_global/POLICIES.md` key that caps them.
POLICY_KEY = "routine_unattended_actions"


class GrantError(ValueError):
    """A grant outside the vocabulary. ``unknown`` names them."""

    def __init__(self, unknown: list):
        super().__init__(f"not a grant: {', '.join(unknown)}")
        self.unknown = unknown


def clean(raw: "Iterable[str] | str | None") -> list:
    """``raw`` (a list, or a comma-separated string) as stored: unique, in vocabulary order.
    Raises :class:`GrantError` naming anything outside the vocabulary — a grant is never guessed."""
    if raw is None:
        return []
    items = raw.split(",") if isinstance(raw, str) else list(raw)
    names = {str(g).strip().lower() for g in items if str(g).strip()}
    unknown = sorted(n for n in names if n not in VOCAB)
    if unknown:
        raise GrantError(unknown)
    return [g for g in VOCAB if g in names]


def allowed(rules: "dict | None") -> set:
    """The grants the company's policy lets any routine hold. `all` is the whole vocabulary,
    `none` (or anything else outside it) is nothing."""
    listed = tuple((rules or {}).get(POLICY_KEY) or ())
    if isinstance(listed, str):
        listed = tuple(x.strip() for x in listed.split(","))
    names = {str(x).strip().lower() for x in listed}
    if "all" in names:
        return set(VOCAB)
    return names & set(VOCAB)


def cap(grants: Iterable[str], rules: "dict | None") -> list:
    """``grants`` narrowed to what the policy allows, in vocabulary order."""
    ok = allowed(rules)
    return [g for g in VOCAB if g in set(grants or ()) and g in ok]


def may_sentence(grants: Iterable[str]) -> str:
    """"This routine may: send bots to meetings, retire itself." — or "" when it may do nothing on its
    own. What the person reads when they make it and on the routine's card."""
    named = [WORDS[g] for g in VOCAB if g in set(grants or ())]
    return f"This routine may: {', '.join(named)}." if named else ""


def of_flow(flow) -> list:
    """The grants recorded on a flow version (``params.grants``), unknown names dropped."""
    raw = flow.param("grants", []) if flow is not None else []
    try:
        return clean(raw if isinstance(raw, (list, tuple, str)) else [])
    except GrantError as e:
        keep = [g for g in (raw if isinstance(raw, (list, tuple)) else []) if g not in e.unknown]
        return clean(keep)


def effective(flow, uid: str = "", rules: "dict | None" = None) -> list:
    """What this routine may do on THIS run: its recorded grants, capped by the policy as it reads now."""
    granted = of_flow(flow)
    if not granted:
        return []
    if rules is None:
        from . import policies
        rules = policies.read(uid or getattr(flow, "owner", ""))
    return cap(granted, rules)
