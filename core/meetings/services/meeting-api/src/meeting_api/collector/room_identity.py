"""room_identity — is this speaker a MEETING ROOM or a person? (the READ side)

A room system (Google Meet hardware, a Microsoft Teams Room, a Zoom Room) mixes every microphone
INSIDE the room hardware, before the call. What joins the meeting is one participant, one audio
stream, one display name — so every human in that room is attributed to the room's name. Google's
own Meet transcripts do exactly the same: in-room speech is attributed to the room device's robot
account, whose ``displayName`` is "the administrator-specified device name".

This module does NOT separate those voices. It makes the existing, honest answer LEGIBLE: the
transcript and the participants roster now say the speaker is a room, so a consumer stops reading a
device name as a person's name.

WHY IT EXISTS HERE AS WELL AS IN THE BOT. ``speaker_kind`` is stamped by the bot at publish time,
but the ``transcriptions`` table has no column for it — persisting one would be a migration, and
rung 1 buys no migration. The classification is a pure function of the display name, so the read
path recomputes it from ``transcriptions.speaker``: the same answer, no schema change, and rows
written before this shipped are classified too.

THE TABLE IS DATA, NOT CODE. ``DEFAULT_ROOM_PATTERNS`` mirrors
``core/meetings/contracts/room-identity/room-patterns.json`` — the cross-language source of truth
shared with ``services/bot/src/room-identity.ts``. It is embedded rather than read from disk
because this service ships as its own image and does not mount that directory;
``tests/test_room_identity.py`` reads the JSON and fails if this table has drifted from it.

THE THREE VALUES ARE NOT SYMMETRIC:
  ``room``    — EVIDENCE. A pattern matched.
  ``person``  — A DEFAULT, not evidence. A resolved display name with no room marker.
  ``unknown`` — No name to classify (the binder refused, or the label is provisional).
A room kit named after a human — we have met a real one called ``Steve Jobs`` — reads ``person``
and always will, because nothing in the data distinguishes it. Anything consuming this field must
read ``person`` as "no room marker found", never as "confirmed human".
"""
from __future__ import annotations

import json
import os
import re
from typing import Iterable, Optional

# Each entry is ``(id, regex_source)``; every pattern is applied case-insensitively. Keep the ids
# and the sources byte-identical to room-patterns.json — the drift test compares them.
DEFAULT_ROOM_PATTERNS: tuple[tuple[str, str], ...] = (
    ("meet-device-resource", r"(^|\s)devices/"),
    ("room-word", r"\b(meeting\s*room|conference\s*room|board\s*room|boardroom|huddle\s*(room|space)|training\s*room|war\s*room|focus\s*room|breakout\s*room)\b"),
    ("numbered-room", r"\b(room|rm)[\s._-]*\d{1,4}\b"),
    ("room-suffix", r"\((meeting\s*)?(room|conference\s*room)\)\s*$"),
    ("teams-room", r"\b(microsoft\s*teams\s*rooms?|teams\s*rooms?|\bMTR\b|surface\s*hub)\b"),
    ("zoom-room", r"\bzoom\s*rooms?\b"),
    ("google-meet-hardware", r"\b(google\s*meet\s*hardware|meet\s*hardware|series\s*one\s*(desk|board)?|chromebase|chromebox)\b"),
    ("vendor-room-kit", r"\b(rally\s*bar|rallybar|logitech\s*(rally|tap|roommate)|tap\s*(ip|scheduler)|roommate|poly\s*(studio|g7500|x30|x50|x52|x70)|neat\s*(bar|board|frame|pad)|yealink\s*(meetingbar|mvc|a\d{2})|crestron\s*(flex|uc-)|cisco\s*(room\s*(kit|bar|navigator)|webex\s*room)|webex\s*room\s*kit)\b"),
    ("room-word-non-english", r"\b(vergaderruimte|vergaderzaal|besprechungsraum|konferenzraum|sitzungszimmer|salle\s*de\s*r[eé]union|sala\s*de\s*reuni[oó]n|sala\s*riunioni|sala\s*de\s*reuni[oõ]es|m[oö]tesrum|m[oø]terom|neuvotteluhuone)\b"),
)

ROOM_PATTERNS_ENV = "VEXA_ROOM_PATTERNS"

# Labels that name NOBODY — the lane's internal refusal spellings plus the blank the bot publishes
# when it will not guess. A name in this set is ``unknown``, never ``person``.
_NO_NAME = re.compile(r"^(?:seg_\d+|ch-\d+(?::\d+)?|Speaker|Speaker [A-Z]+|Unknown|unknown)$")


def _parse_env(raw: Optional[str]) -> Optional[tuple[str, list[str]]]:
    """Parse ``VEXA_ROOM_PATTERNS``. ``None`` when unset, empty or malformed — never raises, because
    a bad env var must not take the read path down. Returns ``(mode, sources)`` where mode is
    ``"replace"`` or ``"append"`` (a leading ``"+"`` element selects append)."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    if not isinstance(parsed, list) or any(not isinstance(p, str) for p in parsed):
        return None
    mode = "append" if parsed[:1] == ["+"] else "replace"
    sources = [s for s in (parsed[1:] if mode == "append" else parsed) if s.strip()]
    if not sources:
        return None
    return mode, sources


def compile_patterns(env: Optional[dict] = None) -> list[tuple[str, re.Pattern]]:
    """Compile the table in force. A single uncompilable pattern is DROPPED rather than failing the
    whole override — one bad regex in an operator's list must not disable every other one."""
    environ = os.environ if env is None else env
    override = _parse_env(environ.get(ROOM_PATTERNS_ENV))
    if override is None:
        requested: list[tuple[str, str]] = list(DEFAULT_ROOM_PATTERNS)
    else:
        mode, sources = override
        env_entries = [(f"env:{i}", src) for i, src in enumerate(sources)]
        requested = (list(DEFAULT_ROOM_PATTERNS) + env_entries) if mode == "append" else env_entries

    out: list[tuple[str, re.Pattern]] = []
    for pid, src in requested:
        try:
            out.append((pid, re.compile(src, re.IGNORECASE)))
        except re.error:
            continue
    return out


_compiled: Optional[list[tuple[str, re.Pattern]]] = None


def _patterns() -> list[tuple[str, re.Pattern]]:
    global _compiled
    if _compiled is None:
        _compiled = compile_patterns()
    return _compiled


def matched_pattern(display_name: Optional[str], patterns: Optional[Iterable] = None) -> Optional[str]:
    """The id of the pattern that classified this name as a room, or ``None``. Diagnostic."""
    name = (display_name or "").strip()
    if not name or _NO_NAME.match(name):
        return None
    for pid, rx in (patterns if patterns is not None else _patterns()):
        if rx.search(name):
            return pid
    return None


def speaker_kind(display_name: Optional[str], patterns: Optional[Iterable] = None) -> str:
    """``"room"`` | ``"person"`` | ``"unknown"`` for one display name. See the module docstring for
    why the three values are not symmetric."""
    name = (display_name or "").strip()
    if not name or _NO_NAME.match(name):
        return "unknown"
    return "room" if matched_pattern(name, patterns) else "person"
