"""SESSION_PROFILE — what a stored browser session may contain, and the check a write-back passes.

The definition is ``session-profile.v1.json`` next to this module: a byte-identical copy of
``@vexa/remote-browser``'s ``src/session-profile.v1.json``, the file the bot restores and collects
with (``tests/test_session_profile.py`` fails on any difference, so the two cannot drift). A path is
in the profile when it is listed in ``files``, or when its parent is one of ``leveldbDirs`` and its
name matches ``leveldbFile``; everything else — extensions, caches, preferences backups, nested
paths, traversal names — is not, and a write-back that names one is refused whole.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from pathlib import Path
from typing import Any, Optional

_SPEC_FILE = Path(__file__).with_name("session-profile.v1.json")

#: The parsed ``session-profile.v1.json`` (``files``, ``leveldbDirs``, ``leveldbFile``,
#: ``maxFileBytes``, ``maxTotalBytes``, ``maxFiles``).
SESSION_PROFILE: dict[str, Any] = json.loads(_SPEC_FILE.read_text(encoding="utf-8"))

_FILES = frozenset(SESSION_PROFILE["files"])
_LEVELDB_DIRS = frozenset(SESSION_PROFILE["leveldbDirs"])
_LEVELDB_FILE = re.compile(SESSION_PROFILE["leveldbFile"])
MAX_FILE_BYTES: int = int(SESSION_PROFILE["maxFileBytes"])
MAX_TOTAL_BYTES: int = int(SESSION_PROFILE["maxTotalBytes"])
MAX_FILES: int = int(SESSION_PROFILE["maxFiles"])

#: The largest request body a complete profile can need: every byte base64-encoded, plus the JSON
#: framing of ``MAX_FILES`` entries. A body over this is refused before it is parsed.
MAX_BODY_BYTES: int = ((MAX_TOTAL_BYTES + 2) // 3) * 4 + MAX_FILES * 1024 + 4096


class InvalidSessionProfile(ValueError):
    """A write-back body that is not a set of SESSION_PROFILE files. The message names the path and
    the reason, never the bytes."""


def profile_path_refusal(path: Any) -> Optional[str]:
    """Why ``path`` is not a SESSION_PROFILE path, or ``None`` when it is one."""
    if not isinstance(path, str) or not path:
        return "is not a path"
    if len(path) > 512:
        return "is longer than 512 characters"
    if any(ord(c) < 0x20 or c == "\x7f" for c in path):
        return "contains a control character"
    if "\\" in path:
        return "contains a backslash"
    if path.startswith("/"):
        return "is absolute"
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return "has an empty, '.' or '..' segment"
    if path in _FILES:
        return None
    if "/".join(parts[:-1]) in _LEVELDB_DIRS and _LEVELDB_FILE.fullmatch(parts[-1]):
        return None
    return "is not part of the session profile"


def is_profile_path(path: Any) -> bool:
    return profile_path_refusal(path) is None


def parse_profile_upload(raw: bytes) -> list[tuple[str, bytes]]:
    """The ``(path, bytes)`` pairs of a write-back body, or ``InvalidSessionProfile``.

    The body is ``{"files": [{"path": <profile path>, "data": <base64>}, ...]}`` and nothing else:
    every entry is exactly a path and its bytes (no link targets, modes or other fields), every path
    is a SESSION_PROFILE path named once, every file is within ``maxFileBytes``, and all of them
    together within ``maxFiles`` / ``maxTotalBytes``. One bad entry refuses the whole body, so a
    write-back either replaces the session with valid files or changes nothing."""
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise InvalidSessionProfile("the body is not JSON") from None
    if not isinstance(body, dict) or set(body) != {"files"}:
        raise InvalidSessionProfile('the body must be exactly {"files": [...]}')
    files = body["files"]
    if not isinstance(files, list) or not files:
        raise InvalidSessionProfile("files must be a non-empty list")
    if len(files) > MAX_FILES:
        raise InvalidSessionProfile(f"{len(files)} files exceed the limit of {MAX_FILES}")
    max_encoded = ((MAX_FILE_BYTES + 2) // 3) * 4
    out: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    total = 0
    for i, entry in enumerate(files):
        if not isinstance(entry, dict) or set(entry) != {"path", "data"}:
            raise InvalidSessionProfile(
                f"entry {i} must be exactly a path and its data (a link, a mode or any other field is refused)")
        path, data = entry["path"], entry["data"]
        reason = profile_path_refusal(path)
        if reason:
            raise InvalidSessionProfile(f"entry {i} ({path!r}) {reason}")
        if path in seen:
            raise InvalidSessionProfile(f"entry {i} ({path!r}) is named twice")
        seen.add(path)
        if not isinstance(data, str):
            raise InvalidSessionProfile(f"entry {i} ({path!r}) data must be a base64 string")
        if len(data) > max_encoded:
            raise InvalidSessionProfile(f"entry {i} ({path!r}) is larger than {MAX_FILE_BYTES} bytes")
        try:
            blob = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise InvalidSessionProfile(f"entry {i} ({path!r}) data is not base64") from None
        if len(blob) > MAX_FILE_BYTES:
            raise InvalidSessionProfile(f"entry {i} ({path!r}) is larger than {MAX_FILE_BYTES} bytes")
        total += len(blob)
        if total > MAX_TOTAL_BYTES:
            raise InvalidSessionProfile(f"the files exceed {MAX_TOTAL_BYTES} bytes together")
        out.append((path, blob))
    return out
