"""PERSON FACTS — the settings identity owns, and the only place they live.

WHY IDENTITY. These five keys are facts about a PERSON: which clock their times are stated in, and
how they want to be contacted. They lived in `.settings.json`, a file in a workspace in the AGENT
domain, written by the control MCP and read by flows. That made two domains depend on a third for a
fact about a person — so a deployment without the agent domain had people with no timezone and no
mail preferences, and flows could neither state a time in their clock nor honour "stop mailing me
minutes". Identity is the only domain everyone may depend on (founder ruling, 2026-09-02), and this
is the kind of fact that belongs to it.

WHAT IS NOT HERE, DELIBERATELY. `bot_name`. A bot default is a fact about the BOT, and the bot is
the meetings domain's — it already resolves one through `/internal/users/{id}/bot-context`. Adding
it to this vocabulary would make a fourth store for one fact.

THE VOCABULARY IS CLOSED and an unknown key is refused WITH the list. A setting that silently does
nothing is worse than an error, and an agent with no vocabulary invents one.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

DATA_KEY = "person_settings"

#: key -> (default, kind, what it means to the person)
VOCAB: Dict[str, Tuple[Any, str, str]] = {
    "timezone":     ("", "text",
                     "their IANA zone, e.g. Europe/Lisbon — every time is stated in it"),
    "mail_minutes": (True, "on/off",
                     "the write-up after a meeting ends"),
    "mail_join":    (False, "on/off",
                     "a note each time the notetaker joins a call"),
    "mail_rsvp":    (True, "on/off",
                     "replying yes in the calendar when Vexa is invited to a meeting"),
    "mail_prep":    (True, "on/off",
                     "the day-before prepare email for upcoming meetings"),
}

MEANINGS = {k: v[2] for k, v in VOCAB.items()}
DEFAULTS = {k: v[0] for k, v in VOCAB.items()}

_TRUE = ("on", "true", "yes", "1")
_FALSE = ("off", "false", "no", "0")


class Refused(ValueError):
    """An unknown key, or a value the vocabulary does not accept. ``detail`` is safe to return."""

    def __init__(self, detail: dict) -> None:
        super().__init__(str(detail))
        self.detail = detail


def read(data: dict | None) -> dict:
    """This person's settings, defaults filled in. Never raises, never empty, never a missing key.

    A caller that has to tell "unset" from "off" will get it wrong eventually, and the wrong way
    round is a person who quietly stops receiving their minutes."""
    raw = (data or {}).get(DATA_KEY) or {}
    out = dict(DEFAULTS)
    if isinstance(raw, dict):
        out.update({k: v for k, v in raw.items() if k in VOCAB})
    return out


def coerce(key: str, value: Any) -> Any:
    """One value, validated in the domain that owns it — so there is ONE parser, not one per caller.

    The MCP tool passes through whatever the person said ("off", "no", "yes"), because deciding what
    those words mean is this vocabulary's job and not the tool's."""
    if key not in VOCAB:
        raise Refused({
            "refused": f"there is no setting called {key!r}",
            "the_settings_that_exist": MEANINGS,
            "do": ("pick one of these, or report it as a rough edge if the thing they want is "
                   "missing — do NOT edit a flow to work around it."),
        })
    _default, kind, _meaning = VOCAB[key]
    if kind == "on/off":
        if isinstance(value, bool):
            return value
        v = str(value).strip().lower()
        if v not in _TRUE + _FALSE:
            raise Refused({"refused": f"{key} is on or off", "you_sent": value})
        return v in _TRUE
    val = str(value).strip()
    if key == "timezone" and val:
        try:
            import zoneinfo
            zoneinfo.ZoneInfo(val)
        except Exception:  # noqa: BLE001
            raise Refused({"refused": f"{val!r} is not a timezone",
                           "give_me": "an IANA name like Europe/Lisbon"})
    return val


def apply(data: dict | None, update: dict) -> dict:
    """The new ``users.data`` for this person. Validates EVERY key before writing ANY of them: a
    half-applied settings change is a person who thinks they turned two things off and turned one."""
    if not isinstance(update, dict) or not update:
        raise Refused({"refused": "give at least one setting to change",
                       "the_settings_that_exist": MEANINGS})
    cleaned = {k: coerce(k, v) for k, v in update.items()}
    out = dict(data or {})
    stored = dict(out.get(DATA_KEY) or {})
    stored.update(cleaned)
    out[DATA_KEY] = stored
    return out


def plan_import(existing: dict | None, legacy: dict | None) -> tuple:
    """The one-shot migration off `.settings.json`, as a pure decision. Returns
    ``(new_data, imported, kept, dropped)``.

    THREE RULES, and each is a failure this migration could otherwise cause:

      * a key the person has ALREADY set through the new door is KEPT, never overwritten. The
        migration is re-runnable across an estate where somebody has since changed a preference,
        and a second run that clobbered it would silently undo a person's choice.
      * `bot_name` is DROPPED. It is a fact about the bot, not the person, and importing it here
        would make a fourth store for it.
      * an unknown key is DROPPED, not refused. A migration that stops on one odd key leaves half
        the estate on the old store, and there is no second run that fixes that.
    """
    out = dict(existing or {})
    stored = dict(out.get(DATA_KEY) or {})
    imported: Dict[str, Any] = {}
    kept, dropped = [], []
    for key, value in (legacy or {}).items():
        if key not in VOCAB:
            dropped.append(key)
            continue
        if key in stored:
            kept.append(key)
            continue
        try:
            imported[key] = coerce(key, value)
        except Refused:
            dropped.append(key)
    stored.update(imported)
    out[DATA_KEY] = stored
    return out, imported, sorted(kept), sorted(dropped)
