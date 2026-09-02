"""HOW VEXA BEHAVES FOR ONE PERSON — the closed vocabulary, beside the file it writes.

`.settings.json` in a person's workspace is read by the flows engine at processing time
(`flows_steps/common.py`) and written, until now, by the control MCP — which held the vocabulary as
a Python dict and wrote the file with `docker exec … cat >` (seam inventory B1, B6.1). So the list
of things Vexa can be told to do lived in a tool body on another host, and the only writer of a file
this service owns was a shell inside its own container.

THE VOCABULARY IS CLOSED, and that is the point. An unknown key is refused WITH the list: a setting
that silently does nothing is worse than an error, and an agent with no vocabulary invents one.
"""
from __future__ import annotations

import json
from pathlib import Path

SETTINGS_PATH = ".settings.json"

#: key -> (default, kind, what it means to the person)
VOCAB = {
    "bot_name":     ("Vexa", "text",
                     "the name the notetaker shows up as in the room"),
    "mail_minutes": (True, "on/off",
                     "the write-up after a meeting ends"),
    "mail_join":    (False, "on/off",
                     "a note each time the notetaker joins a call"),
    "mail_rsvp":    (True, "on/off",
                     "replying yes in the calendar when Vexa is invited to a meeting"),
    "mail_prep":    (True, "on/off",
                     "the day-before prepare email for upcoming meetings"),
    "timezone":     ("", "text",
                     "their IANA zone, e.g. Europe/Lisbon — every time is stated in it"),
}

MEANINGS = {k: v[2] for k, v in VOCAB.items()}


def read(workspace: Path) -> dict:
    """This person's preferences, defaults filled in. Never raises, never empty."""
    try:
        raw = json.loads((Path(workspace) / SETTINGS_PATH).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        raw = {}
    out = {k: v[0] for k, v in VOCAB.items()}
    if isinstance(raw, dict):
        out.update({k: v for k, v in raw.items() if k in VOCAB})
    return out


def write(workspace: Path, key: str, value) -> tuple:
    """Set one setting. ``(status, body)`` — 422 carries the refusal AND the vocabulary."""
    if key not in VOCAB:
        return 422, {"detail": {
            "refused": f"there is no setting called {key!r}",
            "the_settings_that_exist": MEANINGS,
            "do": ("pick one of these, or report_friction() if the thing they want is missing — "
                   "do NOT edit a flow to work around it."),
        }}
    default, kind, meaning = VOCAB[key]
    if kind == "on/off":
        v = str(value).strip().lower()
        if v not in ("on", "off", "true", "false", "yes", "no", "1", "0"):
            return 422, {"detail": {"refused": f"{key} is on or off", "you_sent": value}}
        val = v in ("on", "true", "yes", "1")
    else:
        val = str(value).strip()
        if key == "timezone" and val:
            try:
                import zoneinfo
                zoneinfo.ZoneInfo(val)
            except Exception:  # noqa: BLE001
                return 422, {"detail": {"refused": f"{val!r} is not a timezone",
                                        "give_me": "an IANA name like Europe/Lisbon"}}
    ws = Path(workspace)
    try:
        raw = json.loads((ws / SETTINGS_PATH).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw = {}
    except Exception:  # noqa: BLE001
        raw = {}
    raw[key] = val
    f = ws / SETTINGS_PATH
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(raw, indent=1), encoding="utf-8")
    return 200, {
        "changed": {key: val}, "settings": read(ws),
        "tell_your_person": f"Done — {meaning}: now {val!r}. It applies from the next meeting.",
        "scope": "this is theirs alone; nobody else's Vexa changed",
    }
