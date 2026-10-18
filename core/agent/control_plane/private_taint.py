"""private_taint.py — what a session read from the person's PRIVATE data stays out of every surface
other people read, unless the person confirms that one write (D6, SC4).

A real call (2026-10-14): the person's agent searched their Gmail and a banking connection, then
wrote a payment approval, an unpaid invoice and contacts' addresses into a workspace another
member reads. The leak rule (ADR-0048 decision 4) covered READS in a shared chat; the person's own
chat may read their mail, and nothing stopped the write that carried it to someone else.

THE MARK IS A LABEL ON DATA, PERSISTED (`shared/taint.py`). Every read route that returns private
data labels its answer (``X-Vexa-Privacy``; `privacy_label.py` for agent-api's own routes), and the
gateway/MCP edge marks the unit AND its conversation in Redis with a TTL. agent-api's connection
routes also mark directly (they are the person's private sources by definition). The mark survives a
restart, a second replica and a worker idle exit. A store that cannot be read counts as held.

THE HOLD APPLIES AT EVERY EGRESS INTO A MULTI-READER SURFACE (`check_egress`): page writes, uploads
and assets, renames and moves (filenames), a workspace's name and purpose, an agent message into a
shared session, a room message, a notice to other people. crm-api holds CRM writes and card
proposals from the forwarded state (``X-Vexa-Taint``); the worker holds its own file writes and its
commit body (`llm/private_writes.py`). A held write is refused (409 ``private_sources_to_shared``)
with a typed fault, a short confirmation code, the surface and who else reads it; its content is
kept ENCRYPTED in agent-api (`held_store.py`), off every desk mount, for the person's share card.

HELD ONLY FOR WHAT IT CARRIES (frictions 1+2, 2026-10-16, merged onto D6 at int-s1). A read whose
answer agent-api served itself (the connection routes) is also FINGERPRINTED
(``shared/private_fingerprint.py``: digests of its identifiers and runs of words, never the text),
persisted beside the mark. A write whose ``content`` is known is then held only when what it ADDS
(over ``before``) carries one of those digests. The narrowing applies only when EVERY source the
session is marked with was fingerprinted: a source only the edge saw (a transcript, a CRM read), an
unreadable store, or a caller that does not say what it writes holds the write, as before (FD3).

When the PERSON (a non-delegated chat message, never the agent) says ``confirm <code>``, that one
write — same conversation, same surface, same target — goes through once. A held FILE write from the
worker is written by agent-api itself on that confirmation (`release_confirmed_files`).
"""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

from shared import private_fingerprint
from shared import taint as taint_mod
from shared.taint import TTL_SEC, UNVERIFIABLE, StoreUnavailable  # noqa: F401 — re-exported

#: How long a confirmation code waits for the person.
CODE_TTL_SEC = 6 * 3600

CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
CONFIRM_RE = re.compile(r"\bconfirm\s+([a-z0-9]{6})\b", re.IGNORECASE)

REASON = "private_sources_to_shared"
#: The typed fault a held write carries (P18): the person sees a held state, never a silent drop.
FAULT = "held_private_egress"
#: Answered instead of private data when the mark cannot be written (the read is refused).
MARK_UNAVAILABLE = "privacy_mark_unavailable"

_CODE_PREFIX = "vexa:taint:code:"
#: Fingerprints of what a conversation was given (digests) and which sources were fingerprinted.
_FP_PREFIX = "vexa:taint:fp:"
_FPCLS_PREFIX = "vexa:taint:fpcls:"
_lock = threading.Lock()
_codes: dict[str, dict] = {}        # in-process fallback when no store is configured
_fps: dict[str, tuple] = {}         # session -> (at, digests, fingerprinted sources) — no-store fallback


def reset() -> None:
    """Forget in-process codes and fingerprints (tests)."""
    with _lock:
        _codes.clear()
        _fps.clear()


# ── the mark ─────────────────────────────────────────────────────────────────────────────────────

def mark(unit: str, source: str, text: Optional[str] = None, *, also: tuple = ()) -> None:
    """Record that session ``unit`` read the person's private ``source``. Raises
    ``StoreUnavailable`` when the mark cannot be persisted — the caller refuses the read.

    ``text`` is what the read gave the agent: it is fingerprinted (digests only) so a later write is
    held only for what it carries. None = not seen; the session then holds every shared write.
    ``also`` names the other labels the same read is marked under (the edge's class for the route),
    so the fingerprint covers the source whichever marker named it."""
    unit = str(unit or "").strip()
    if not unit:
        return
    taint_mod.mark(unit, [str(source or "private")])
    if text is not None:
        _fp_add(unit, private_fingerprint.digests_of(str(text)),
                {str(source or "private"), *[str(a) for a in also if a]})


def record(unit: str, source: str, text: str) -> None:
    """Session ``unit`` was given ``text`` from the person's private ``source``."""
    mark(unit, source, str(text or ""))


def _fp_add(unit: str, digests: list, classes: set) -> None:
    """Persist digests beside the mark (best effort: a lost fingerprint only widens the hold)."""
    session = taint_mod.session_of_unit(unit)
    c = _client()
    if c is not None:
        try:
            pipe = c.pipeline()
            if digests:
                pipe.sadd(_FP_PREFIX + session, *digests)
            pipe.sadd(_FPCLS_PREFIX + session, *sorted(classes))
            pipe.expire(_FP_PREFIX + session, TTL_SEC)
            pipe.expire(_FPCLS_PREFIX + session, TTL_SEC)
            pipe.execute()
            return
        except Exception:  # noqa: BLE001 — unrecorded: the next write is held whole (fail closed)
            try:
                c.delete(_FPCLS_PREFIX + session)
            except Exception:  # noqa: BLE001
                pass
            return
    with _lock:
        _at, ds, cls = _fps.get(session, (0.0, set(), set()))
        _fps[session] = (time.time(), set(ds) | set(digests), set(cls) | set(classes))


def fingerprint(unit: str, found: set):
    """The session's Fingerprint when it covers EVERY source in ``found``; else None (hold whole)."""
    if not found or UNVERIFIABLE in found:
        return None
    session = taint_mod.session_of_unit(str(unit or "").strip())
    c = _client()
    digests: set = set()
    classes: set = set()
    if c is not None:
        try:
            classes = {x.decode() if isinstance(x, bytes) else str(x)
                       for x in (c.smembers(_FPCLS_PREFIX + session) or [])}
            if not set(found) <= classes:
                return None
            digests = {x.decode() if isinstance(x, bytes) else str(x)
                       for x in (c.smembers(_FP_PREFIX + session) or [])}
        except Exception:  # noqa: BLE001
            return None
    else:
        with _lock:
            at, ds, cls = _fps.get(session, (0.0, set(), set()))
            if time.time() - at > TTL_SEC:
                return None
            digests, classes = set(ds), set(cls)
        if not set(found) <= classes:
            return None
    fp = private_fingerprint.Fingerprint()
    fp.add_digests(digests)
    return fp if fp.complete else None


def sources(unit: str) -> set:
    """The private sources ``unit``'s conversation read within ``TTL_SEC``. Empty when none;
    ``{"unverifiable"}`` when the store cannot answer (held)."""
    unit = str(unit or "").strip()
    if not unit:
        return set()
    return taint_mod.sources(unit)


def mark_unavailable_body(detail: str = "") -> dict:
    return {"refused": MARK_UNAVAILABLE,
            "fault": {"code": MARK_UNAVAILABLE, "retry": "later"},
            "instruction": ("This read returns the person's private data, and the privacy mark that "
                            "keeps it out of shared pages could not be recorded right now, so nothing "
                            "was read. Tell the person in one line and try again later.")}


# ── who else reads ───────────────────────────────────────────────────────────────────────────────

def other_readers(ws_dir: Path, subject: str) -> list[str]:
    """Who else reads workspace ``ws_dir``: every member in ``policy/members.json`` but ``subject``
    (by email when the roster has one). Empty for the person's own desk."""
    from workspaces.shared import workspace_paths as wpaths  # noqa: PLC0415
    raw = wpaths.read_text_inside(Path(ws_dir), "policy/members.json", allow=("policy",))
    try:
        rows = json.loads(raw) if raw else []
    except ValueError:
        return []
    out = []
    for m in rows if isinstance(rows, list) else []:
        if isinstance(m, dict) and m.get("subject") and str(m.get("subject")) != str(subject):
            out.append(str(m.get("email") or m.get("subject")))
    return out


# ── confirmation codes (persisted beside the mark) ───────────────────────────────────────────────

def _client():
    store = taint_mod.current()
    return getattr(store, "client", None) if store is not None else None


def _code_get(code: str) -> Optional[dict]:
    c = _client()
    if c is not None:
        try:
            raw = c.get(_CODE_PREFIX + code)
            return json.loads(raw) if raw else None
        except Exception:  # noqa: BLE001 — an unreadable code is no confirmation
            return None
    with _lock:
        row = _codes.get(code)
        return dict(row) if row else None


def _code_put(code: str, row: dict) -> None:
    c = _client()
    if c is not None:
        try:
            c.set(_CODE_PREFIX + code, json.dumps(row), ex=CODE_TTL_SEC)
            idx = _CODE_PREFIX + "index:" + row["subject"]
            c.sadd(idx, code)
            c.expire(idx, CODE_TTL_SEC)
            return
        except Exception:  # noqa: BLE001 — fall back to this process: worst case it asks again
            pass
    with _lock:
        _codes[code] = dict(row)


def _code_drop(code: str, subject: str) -> None:
    c = _client()
    if c is not None:
        try:
            c.delete(_CODE_PREFIX + code)
            c.srem(_CODE_PREFIX + "index:" + subject, code)
        except Exception:  # noqa: BLE001
            pass
    with _lock:
        _codes.pop(code, None)


def _codes_of(subject: str) -> list[str]:
    c = _client()
    found: set = set()
    if c is not None:
        try:
            found |= {x.decode() if isinstance(x, bytes) else str(x)
                      for x in (c.smembers(_CODE_PREFIX + "index:" + subject) or [])}
        except Exception:  # noqa: BLE001
            pass
    with _lock:
        found |= {k for k, r in _codes.items() if r.get("subject") == subject}
    return sorted(found)


def _new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


def confirm_from_text(subject: str, text: str) -> list[str]:
    """The PERSON said ``confirm <code>`` in a chat message: mark each of their waiting codes
    confirmed. Called only from the person's own (non-delegated) chat route. Returns what it
    confirmed."""
    said = {c.lower() for c in CONFIRM_RE.findall(str(text or ""))}
    done = []
    now = time.time()
    for code in said:
        row = _code_get(code)
        if row and row.get("subject") == str(subject) and now - float(row.get("at") or 0) <= CODE_TTL_SEC:
            row["confirmed"] = True
            _code_put(code, row)
            done.append(code)
    return done


# ── the hold ─────────────────────────────────────────────────────────────────────────────────────

def check_egress(*, subject: str, unit: str, surface: str, target: str, readers: list[str],
                 workspace: str = "", name: str = "", path: str = "", content=None,
                 delegated: bool = False, before: str = "", text: Optional[str] = None) -> Optional[dict]:
    """``None`` when the write may proceed; otherwise the refusal body (409). A write is held only
    when the surface has readers besides the person AND the session is marked (or its mark cannot be
    read). A confirmed code for this exact (conversation, surface, target) lets it through once.
    ``content`` is what would have been written: kept encrypted for the person's share card."""
    if not readers:
        return None
    # A worker's request that names no session cannot be checked against a mark: held (fail closed).
    found = sources(unit) if str(unit or "").strip() else ({UNVERIFIABLE} if delegated else set())
    if not found:
        return None
    # ONLY WHAT CARRIES THE READ (friction 2): when every marked source was fingerprinted and the
    # caller says what it writes, a write that adds nothing from those reads goes through.
    carried: list = []
    said = text if text is not None else (content if isinstance(content, str) else None)
    if said is not None:
        fp = fingerprint(unit, set(found))
        if fp is not None:
            carried = fp.carried(said, before or "")
            if not carried:
                return None
    subject, session = str(subject), taint_mod.session_of_unit(unit)
    key = [subject, session, str(surface), str(target)]
    now = time.time()
    mine = None
    for code in _codes_of(subject):
        row = _code_get(code)
        if row is None or now - float(row.get("at") or 0) > CODE_TTL_SEC:
            _code_drop(code, subject)
            continue
        if [row.get("subject"), row.get("session"), row.get("surface"), row.get("target")] != key:
            continue
        if row.get("confirmed"):
            _code_drop(code, subject)          # one confirmation, one write
            return None
        mine = code
        break
    kept = None
    if mine is None:
        mine = _new_code()
        while _code_get(mine) is not None:
            mine = _new_code()
        _code_put(mine, {"subject": subject, "session": session, "unit": str(unit), "surface": str(surface),
                         "target": str(target), "at": now, "confirmed": False})
        kept = _keep(subject, mine, surface=surface, found=found,
                     payload={"surface": surface, "workspace": workspace, "target": target,
                              "path": path or target, "content": content})
    return refusal(code=mine, workspace=workspace or target, path=path or target, readers=readers,
                   found=found, name=name, surface=surface, kept=kept, carried=carried)


def _keep(subject: str, code: str, *, surface: str, found, payload: dict) -> bool:
    from control_plane import held_store  # noqa: PLC0415
    store = held_store.current()
    if store is None:
        return False
    return store.put(subject, code, surface=surface, sources=found, payload=payload)


def check_write(*, subject: str, unit: str, workspace: str, path: str, ws_dir: Path,
                workspace_name: str = "", content=None, before: str = "",
                text: Optional[str] = None) -> Optional[dict]:
    """A PAGE write (the file and entity routes): ``check_egress`` on the workspace's roster."""
    return check_egress(subject=subject, unit=unit, surface="page", target=f"{workspace}/{path}",
                        readers=other_readers(ws_dir, subject), workspace=workspace,
                        name=workspace_name, path=path, content=content, before=before, text=text)


_SURFACE_WORDS = {
    "page": "{path} in {name}",
    "filename": "the name {path} in {name}",
    "workspace_name": "the name of {name}",
    "purpose": "the purpose of {name}",
    "asset": "the file {path} in {name}",
    "agent_message": "a message into a conversation",
    "notice": "a notice",
    "room_message": "a room message",
}


def refusal(*, code: str, workspace: str, path: str, readers: list[str], found, name: str = "",
            surface: str = "page", kept: Optional[bool] = None, carried: Optional[list] = None) -> dict:
    who = ", ".join(sorted(readers)[:5]) + (" and others" if len(readers) > 5 else "")
    kinds = ", ".join(sorted(found))
    # the workspace by its NAME in what the agent says (founder 2026-10-16: it said a slug)
    name = str(name or "").strip() or workspace
    where = _SURFACE_WORDS.get(surface, "{path}").format(path=path, name=name)
    unverifiable = UNVERIFIABLE in set(found)
    why = ("the privacy mark for this session cannot be read right now, so it is treated as having "
           "read the person's private data" if unverifiable and len(found) == 1
           else f"this session read the person's private sources ({kinds})")
    # WHAT CAME FROM THEM (friction 2): the agent can leave exactly that out instead of guessing.
    carried = [str(c)[:60] for c in (carried or [])][:5]
    which = (f" From those sources: {'; '.join(carried)}. Without them the write goes through."
             if carried else "")
    return {
        "refused": REASON,
        "fault": {"code": FAULT, "surface": surface, "held": bool(kept),
                  "release": f"confirm {code}", "unverifiable": unverifiable},
        "surface": surface,
        "workspace": workspace,
        "workspace_name": name,
        "path": path,
        "readers": sorted(readers),
        "sources": sorted(found),
        "confirm_code": code,
        "carried": carried,
        "instruction": (
            f"Nothing was written. {why[0].upper() + why[1:]}, and {where} is also read by {who}.{which} "
            f"Tell the person, in one line, which facts you are withholding and why, and ask; if they "
            f"reply 'confirm {code}', repeat this exact call once. Do not write it another way, and "
            f"do not say it was saved."),
    }


# ── the person's confirmation of a held FILE write (the worker's commit seam) ────────────────────

def release_confirmed_files(subject: str, text: str, root: Path) -> list[dict]:
    """The person said ``confirm <code>`` for a write the WORKER held (its own file writes, kept
    encrypted here by the session saver): agent-api writes those files into the workspace and
    commits them, once, before the turn runs. Returns what was released."""
    from control_plane import held_store  # noqa: PLC0415
    from workspaces.shared import workspace_paths as wpaths  # noqa: PLC0415
    from shared.gitexec import run_git  # noqa: PLC0415
    import base64  # noqa: PLC0415

    store = held_store.current()
    if store is None:
        return []
    out = []
    for code in sorted({c.lower() for c in CONFIRM_RE.findall(str(text or ""))}):
        item = store.get(subject, code)
        if item is None or item.get("kind") != "files":
            continue
        p = item.get("payload") or {}
        slug = str(p.get("workspace") or "")
        ws = Path(root) / slug
        if not slug or "/" in slug or slug.startswith(".") or not (ws / ".git").is_dir():
            continue
        # only into a workspace the person may still write (their roster role is not reader)
        from control_plane import workspace_membership as m  # noqa: PLC0415
        try:
            role = m.is_member(Path(root), slug, str(subject))
        except Exception:  # noqa: BLE001
            role = None
        if role in (None, "reader", "viewer") and slug != str(subject):
            continue
        written = []
        for rel, b64 in sorted((p.get("files") or {}).items()):
            try:
                if b64 is None:
                    wpaths.unlink_inside(ws, rel)
                else:
                    wpaths.write_bytes_inside(ws, rel, base64.b64decode(b64))
                written.append(rel)
            except (wpaths.PathRefused, ValueError, OSError):
                continue
        if written:
            run_git(ws, "add", "-A", "--", *written)
            run_git(ws, "-c", "user.name=vexa-terminal", "-c", "user.email=terminal@vexa.local",
                    "commit", "-m", f"release {len(written)} held page(s) the person confirmed ({code})")
        store.discard(subject, code)
        out.append({"code": code, "workspace": slug, "paths": written})
    return out
