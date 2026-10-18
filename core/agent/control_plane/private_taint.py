"""private_taint.py — what a session read from the person's PRIVATE sources stays out of pages other
people read, unless the person confirms that one write.

A real call (2026-10-14): the person's agent searched their Gmail and a banking connection, then
wrote a payment approval, an unpaid invoice and contacts' addresses into a workspace another
member reads. The leak rule (ADR-0048 decision 4) covered READS in a shared chat; the person's own
chat may read their mail, and nothing stopped the write that carried it to someone else.

THE RULE ON WRITES. A session (unit) that read a private source — any route of the connections
router: mail, calendar, a connected service — is MARKED. A page write from a marked session into a
workspace whose readers are not only the person is refused (409 ``private_sources_to_shared``) with
a short confirmation code, the workspace and who else reads it. The agent says what it withholds and
asks; when the PERSON (a non-delegated chat message, never the agent) says ``confirm <code>``, that
one write — same session, same workspace, same page — goes through once.

The worker applies the same rule to the files a turn writes on its own mounts
(``llm/private_writes.py``), with the same code shape, so one ``confirm <code>`` answers both.

In-process state with a TTL: a session's mark outlives a turn (what the model read stays in its
context), and is dropped after ``TTL_SEC``. A restart forgets marks — the failure direction is
"asks again later" for writes already confirmed, and "a mark lost" for reads made before it.
"""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

#: How long a session stays marked after its last private read.
TTL_SEC = 12 * 3600
#: How long a confirmation code waits for the person.
CODE_TTL_SEC = 6 * 3600

CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
CONFIRM_RE = re.compile(r"\bconfirm\s+([a-z0-9]{6})\b", re.IGNORECASE)

REASON = "private_sources_to_shared"

_lock = threading.Lock()
_marks: dict[str, tuple[float, set]] = {}          # unit -> (last mark time, sources)
_codes: dict[str, dict] = {}                        # code -> {subject, unit, workspace, path, at, confirmed}


def reset() -> None:
    """Forget everything (tests)."""
    with _lock:
        _marks.clear()
        _codes.clear()


def mark(unit: str, source: str) -> None:
    """Record that session ``unit`` read the person's private ``source``."""
    unit = str(unit or "").strip()
    if not unit:
        return
    with _lock:
        _, sources = _marks.get(unit, (0.0, set()))
        sources = set(sources) | {str(source or "private")}
        _marks[unit] = (time.time(), sources)


def sources(unit: str) -> set:
    """The private sources ``unit`` read within ``TTL_SEC`` (empty when none)."""
    unit = str(unit or "").strip()
    with _lock:
        row = _marks.get(unit)
        if not row:
            return set()
        at, found = row
        if time.time() - at > TTL_SEC:
            _marks.pop(unit, None)
            return set()
        return set(found)


def other_readers(ws_dir: Path, subject: str) -> list[str]:
    """Who else reads workspace ``ws_dir``: every member in ``policy/members.json`` but ``subject``
    (by email when the roster has one). Empty for the person's own desk."""
    from workspaces.shared import workspace_paths as wpaths  # noqa: PLC0415
    raw = wpaths.read_text_inside(Path(ws_dir), "policy/members.json")
    try:
        rows = json.loads(raw) if raw else []
    except ValueError:
        return []
    out = []
    for m in rows if isinstance(rows, list) else []:
        if isinstance(m, dict) and m.get("subject") and str(m.get("subject")) != str(subject):
            out.append(str(m.get("email") or m.get("subject")))
    return out


def _new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


def confirm_from_text(subject: str, text: str) -> list[str]:
    """The PERSON said ``confirm <code>`` in a chat message: mark each of their waiting codes
    confirmed. Called only from the person's own (non-delegated) chat route. Returns what it
    confirmed."""
    said = {c.lower() for c in CONFIRM_RE.findall(str(text or ""))}
    done = []
    now = time.time()
    with _lock:
        for code in said:
            row = _codes.get(code)
            if row and row["subject"] == str(subject) and now - row["at"] <= CODE_TTL_SEC:
                row["confirmed"] = True
                done.append(code)
    return done


def check_write(*, subject: str, unit: str, workspace: str, path: str, ws_dir: Path,
                workspace_name: str = "") -> Optional[dict]:
    """``None`` when the write may proceed; otherwise the refusal body (409). A write is held only
    when the session read a private source AND the workspace has readers besides the person. A
    confirmed code for this exact (session, workspace, page) lets it through once."""
    found = sources(unit)
    if not found:
        return None
    readers = other_readers(ws_dir, subject)
    if not readers:
        return None
    key = (str(subject), str(unit), str(workspace), str(path))
    now = time.time()
    with _lock:
        for code, row in list(_codes.items()):
            if (row["subject"], row["unit"], row["workspace"], row["path"]) != key:
                continue
            if now - row["at"] > CODE_TTL_SEC:
                _codes.pop(code, None)
                continue
            if row["confirmed"]:
                _codes.pop(code, None)          # one confirmation, one write
                return None
            mine = code
            break
        else:
            mine = _new_code()
            while mine in _codes:
                mine = _new_code()
            _codes[mine] = {"subject": str(subject), "unit": str(unit), "workspace": str(workspace),
                            "path": str(path), "at": now, "confirmed": False}
    return refusal(code=mine, workspace=workspace, path=path, readers=readers, found=found, name=workspace_name)


def refusal(*, code: str, workspace: str, path: str, readers: list[str], found, name: str = "") -> dict:
    who = ", ".join(sorted(readers)[:5]) + (" and others" if len(readers) > 5 else "")
    kinds = ", ".join(sorted(found))
    # the workspace by its NAME in what the agent says (founder 2026-10-16: it said a slug)
    name = str(name or "").strip() or workspace
    return {
        "refused": REASON,
        "workspace": workspace,
        "workspace_name": name,
        "path": path,
        "readers": sorted(readers),
        "sources": sorted(found),
        "confirm_code": code,
        "instruction": (
            f"Nothing was written. This session read the person's private sources ({kinds}), and "
            f"{name} is also read by {who}. Tell the person, in one line, which facts you are "
            f"withholding from {path} and why, and ask; if they reply 'confirm {code}', repeat "
            f"this exact call once. Do not write it another way."),
    }
