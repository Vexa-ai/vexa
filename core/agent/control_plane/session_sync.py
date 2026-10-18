"""session_sync.py — agent-api's half of ADR-0048 decision 3: hand a turn its conversation, and save
what comes back.

IN. ``bundle_for`` resolves the session a chat turn belongs to — the caller's own thread by name, or
a session id they participate in — and returns the bundle the dispatcher puts on the turn's input
entry (``session_bundle``). The worker materialises it outside every mount.

OUT. The worker hands the conversation back as a ``session-bundle`` event on its own output stream,
before ``turn-complete``. ``SessionSaver`` reads it there — whether or not anybody is watching the
turn (a submitted turn has no stream open) — and saves it as the next version. A version based on
anything but the head is refused by the store and logged with counts only; nothing is merged.

The saver watches only units it handed a bundle to, from the output tail at hand-over, and stops
watching a unit when every turn it handed over has completed (or after ``WATCH_TTL_S``).
"""
from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
from typing import Optional

from shared import stream_bounds
from control_plane.session_store import SessionConflict, SessionDenied, SessionStore

logger = logging.getLogger("agent_api.session_sync")

#: The prefix every store-minted session id carries; anything else is a person's own thread name.
STORE_ID_PREFIX = "s-"
WATCH_TTL_S = 3600.0
POLL_S = 0.5


class SessionUnavailable(RuntimeError):
    """The store could not answer for a session whose readers it alone knows — a store id, or a
    thread with other people in it. Such a turn is refused (503), never run on the old layout with
    the speaker's whole reach: only a private thread may fall back."""

    kind = "session_unavailable"

    def __init__(self, session: str) -> None:
        super().__init__(self.kind)
        self.session = session


class SessionBusy(RuntimeError):
    """Another unit holds this session's head lease: its turn has not completed yet. Refused up
    front (409 ``session_conflict``) so no reply is produced that the store would then refuse."""

    kind = "session_conflict"

    def __init__(self, session: str, holder: str = "", holder_kind: str = "") -> None:
        super().__init__(self.kind)
        self.session = session
        self.holder = holder or ""
        self.holder_kind = holder_kind or ""

    def queueable_for(self, unit_id: str) -> bool:
        """True when the turn holding the head is THIS chat's own (its ``-auto`` sibling or another
        generation of its unit): the person's message waits behind it instead of being refused
        (live app.dev 2026-10-15: the post-meeting recap held the head and "finalize the meeting"
        got a 409). Another participant's turn is still refused."""
        return same_chat(self.holder, unit_id)


#: THE HEAD LEASE IS A HEARTBEAT, NOT A HORIZON. A turn holds it for ``LEASE_TTL_S`` and the saver
#: renews it every ``LEASE_RENEW_S`` while the unit's workload is alive, so a turn whose worker died
#: frees its conversation within ``LEASE_TTL_S`` — never the hour a fixed TTL held it for. The
#: saver's watch (``WATCH_TTL_S``) is still the upper bound on renewal.
LEASE_TTL_S = 90
LEASE_RENEW_S = 30
#: Dead probes in a row (one per renewal) before a watched unit's turn is ended as interrupted.
DEAD_PROBES = 2
#: What an interrupted turn says, typed as the runtime fault the chat already renders with a retry.
INTERRUPTED = {"source": "runtime", "kind": "unavailable", "op": "status", "status": None,
               "detail": "that reply was interrupted — your agent stopped before it finished",
               "remedy": "Send your message again."}
#: How long a person's message waits behind its own chat's running turn before it is refused.
QUEUE_WAIT_S = 300
FAULT_TTL_S = 24 * 3600


def lease_key(session_id: str) -> str:
    return f"session:{session_id}:lease"


def fault_key(session_id: str) -> str:
    return f"session:{session_id}:fault"


def lease_kind_key(session_id: str) -> str:
    return f"session:{session_id}:lease:kind"


def chat_of(unit_id: str) -> str:
    """The chat a unit serves: its id without the mount generation and the routine or signal suffix."""
    return re.sub(r"(-g[0-9A-Za-z]+)?(-read)?(-auto|-signal)?$", "", str(unit_id or ""))


def same_chat(a: str, b: str) -> bool:
    return bool(a) and bool(b) and chat_of(a) == chat_of(b)


def take_lease(r, session_id: str, unit_id: str, *, kind: str = "reply") -> bool:
    """Hold ``session_id``'s head for ``unit_id`` until its turns complete. True when taken (or
    already held by this unit — a queued turn behind its own); raises ``SessionBusy`` (naming the
    holder and its kind) when another unit holds it. A store of leases that cannot answer takes
    nothing and refuses nothing (False): the version check at save is still the authority."""
    if r is None:
        return False
    try:
        if r.set(lease_key(session_id), unit_id, nx=True, ex=LEASE_TTL_S):
            r.set(lease_kind_key(session_id), kind or "reply", ex=LEASE_TTL_S)
            return True
        holder = r.get(lease_key(session_id))
        if holder in (None, unit_id):
            r.set(lease_key(session_id), unit_id, ex=LEASE_TTL_S)
            r.set(lease_kind_key(session_id), kind or "reply", ex=LEASE_TTL_S)
            return True
        holder_kind = r.get(lease_kind_key(session_id)) or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("session %s: head lease not taken (%s)", session_id, type(exc).__name__)
        return False
    raise SessionBusy(session_id, holder=str(holder), holder_kind=str(holder_kind))


def renew_lease(r, session_id: str, unit_id: str) -> bool:
    """The heartbeat: extend the lease ``unit_id`` still holds by ``LEASE_TTL_S``. False (nothing
    renewed) when another unit holds it, nobody does, or the store cannot answer."""
    if r is None:
        return False
    try:
        if r.get(lease_key(session_id)) != unit_id:
            return False
        r.expire(lease_key(session_id), LEASE_TTL_S)
        r.expire(lease_kind_key(session_id), LEASE_TTL_S)
        return True
    except Exception as exc:  # noqa: BLE001 — the lease lapses on its own
        logger.warning("session %s: head lease not renewed (%s)", session_id, type(exc).__name__)
        return False


def lease_free(r, session_id: str) -> bool:
    """True when nobody holds the head (or the store cannot say — the take decides)."""
    if r is None:
        return True
    try:
        return not r.exists(lease_key(session_id))
    except Exception:  # noqa: BLE001
        return True


def release_lease(r, session_id: str, unit_id: str) -> None:
    if r is None:
        return
    try:
        if r.get(lease_key(session_id)) == unit_id:
            r.delete(lease_key(session_id), lease_kind_key(session_id))
    except Exception as exc:  # noqa: BLE001 — the lease expires on its own
        logger.warning("session %s: head lease not released (%s)", session_id, type(exc).__name__)


def record_fault(r, session_id: str, f: dict) -> None:
    """Record why the last reply in this session was not saved (read back by the history route)."""
    if r is None:
        return
    try:
        r.set(fault_key(session_id), json.dumps({**f, "at": round(time.time(), 3)}), ex=FAULT_TTL_S)
    except Exception as exc:  # noqa: BLE001
        logger.warning("session %s: refusal not recorded (%s)", session_id, type(exc).__name__)


def clear_fault(r, session_id: str) -> None:
    if r is None:
        return
    try:
        r.delete(fault_key(session_id))
    except Exception:  # noqa: BLE001
        pass


def recorded_fault(r, session_id: str) -> Optional[dict]:
    if r is None:
        return None
    try:
        raw = r.get(fault_key(session_id))
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001 — a record that cannot be read says nothing
        return None


def _redis_for(url: str):
    """A client for the history route's fault read (a seam for tests)."""
    if not url:
        return None
    import redis  # noqa: PLC0415

    return redis.from_url(url, decode_responses=True)


def fault(kind: str, session: str, **extra) -> dict:
    """The typed half of a session refusal, the shape ``unit_faults.answer`` gives a runtime one."""
    return {"source": "session-store", "kind": kind, "session": session, **extra}


SENTENCES = {
    # live app.dev 2026-10-15: said what is happening, and offered the act that ends it (`action`)
    "session_conflict": "A previous reply is still running in this conversation.",
    "session_unavailable": "This conversation's store is not answering right now, so the agent cannot "
                           "answer here safely — nothing was lost, please send it again shortly.",
}


#: What the history route says about a recorded refusal, after the fact: the reply was produced
#: but is not in the conversation (S5). Read by the terminal like any typed fault.
AFTER = {
    "session_conflict": ("The last reply in this conversation was not saved: another turn changed "
                         "the conversation first.",
                         "Send that message again; the agent answers from what is saved."),
    "session_unavailable": ("The last reply in this conversation was not saved: the conversation "
                            "store did not answer.",
                            "Send that message again shortly; if it keeps happening, an operator "
                            "should check agent-api's log."),
}


def shown_fault(f: dict) -> dict:
    """A recorded refusal as the history route returns it: the record plus its sentence and remedy."""
    detail, remedy = AFTER.get(str(f.get("kind")), ("The last reply in this conversation was not saved.", ""))
    return {**f, "detail": detail, "remedy": remedy}


#: What the person can do about a refusal, and the act the chat offers for it (`POST /api/chat/stop`).
REMEDIES = {
    "session_conflict": ("Stop it, then send your message again.", "stop"),
}

#: What a message waiting behind its own chat's turn says, by the holder's kind.
HOLDER_WORK = {
    "meeting_recap": "Finishing the meeting recap",
    "routine": "Finishing a routine's run",
    "reply": "Finishing the previous reply",
    "signal": "Reading what was just said in the meeting",
    "room": "Vexa is answering in this room",
}


QUEUE_TIMEOUT_SENTENCE = ("A previous reply is still running in this conversation after five minutes, "
                          "so your message was not sent.")


def queued_sentence(holder_kind: str) -> str:
    return f"{HOLDER_WORK.get(holder_kind or '', HOLDER_WORK['reply'])} — your message runs next."


def answer(kind: str, session: str, **extra) -> dict:
    """The JSON body a refused session turn is answered with: a sentence and the typed fault — and,
    where there is one, the remedy and the act that carries it out (``action: "stop"``)."""
    remedy = REMEDIES.get(kind)
    if remedy is not None:
        extra = {"remedy": remedy[0], "action": remedy[1], **extra}
    return {"detail": SENTENCES.get(kind, kind),
            "fault": fault(kind, session, detail=SENTENCES.get(kind, kind), **extra)}


def resolve(store: SessionStore, subject: str, session: str, *, create: bool = True) -> Optional[dict]:
    """The session record for ``session`` as ``subject`` addresses it, or None.

    A store id (``s-…``) is answered only for a participant; any other name is the subject's own
    thread, created on first use (its owner is the subject)."""
    if session.startswith(STORE_ID_PREFIX):
        row = store.get(session)
        if row is None or not store.can_read(row["id"], subject):
            return None
        return row
    row = store.by_name(subject, session)
    if row is None and create:
        row = store.create(subject, name=session)
    return row


#: The most attachment bytes one turn's bundle carries (newest first); the rest stay posted, unread.
MAX_BUNDLE_ATTACHMENT_BYTES = 24 << 20


def session_attachments(store: SessionStore, session_id: str, read) -> dict:
    """The files posted in this session, ``{stored path: base64}``, read from each POSTER's desk by
    ``read(poster, path)``. This is the leak rule's explicit share (ADR-0048 decision 4): a file
    posted into a conversation is read by every participant already, so its turns may read it — and
    nothing else on the poster's desk travels."""
    out: dict = {}
    total = 0
    for it in store.attachments(session_id):
        path = it["path"]
        if path in out:
            continue
        try:
            data = read(it["poster"], path)
        except Exception:  # noqa: BLE001 — a file that cannot be read is not carried
            data = None
        if data is None or total + len(data) > MAX_BUNDLE_ATTACHMENT_BYTES:
            continue
        total += len(data)
        out[path] = base64.b64encode(data).decode("ascii")
    return out


def bundle_for(store: SessionStore, subject: str, session: str, *, faults=None,
               read_attachment=None) -> Optional[dict]:
    """The bundle for a turn ``subject`` is about to run in ``session``, or None.

    ``faults`` is the Redis the saver records refusals in. When the last hand-back in this session
    was refused, the bundle is marked ``replace``: a worker counts its own hand-back as the next
    version before the store answers, so its copy is ahead of the head and must give way to it.

    Raises ``SessionDenied`` when the subject may read the session but not write to it (a viewer)."""
    row = resolve(store, subject, session)
    if row is None:
        return None
    if not store.can_write(row["id"], subject):
        raise SessionDenied("session_read_only")
    got = store.load_bundle(row["id"])
    out = {"id": row["id"], "version": got["version"],
           "files": {k: base64.b64encode(v).decode("ascii") for k, v in got["files"].items()}}
    if recorded_fault(faults, row["id"]) is not None:
        out["replace"] = True
    if read_attachment is not None:
        files = session_attachments(store, row["id"], read_attachment)
        if files:
            out["attachments"] = files
    return out


def _extends(head: dict, files: dict) -> bool:
    """Is ``files`` the stored conversation carried further? Every stored file is still there and
    starts with what is stored (transcripts and turn records are append-only); the pointer is equal."""
    if not head:
        return False
    for rel, old in head.items():
        new = files.get(rel)
        if new is None or not new.startswith(old):
            return False
    return True


def _retry_onto_head(store: SessionStore, sid: str, subject: str, files: dict) -> Optional[int]:
    """A ONE-PERSON CHAT NEVER ASKS ITS PERSON TO SEND AGAIN. Only that person's turns write it, so
    a hand-back whose base is not the head — its earlier hand-backs were not taken, or a queued turn
    ran on an older delivery — is still that conversation when it carries every stored file further;
    it is saved onto the current head. Anything else (another person in the chat, a copy that
    diverged) stays refused."""
    try:
        humans = [p for p in store.participants(sid) if p.get("kind") == "human"]
        if len(humans) != 1 or humans[0].get("principal") != subject:
            return None
        head = store.load_bundle(sid)
        if not _extends(head.get("files") or {}, files):
            return None
        v = store.save_bundle(sid, int(head["version"]), files)
        logger.info("session %s: a hand-back on an older base carried the head further; saved as v%d", sid, v)
        return v
    except Exception as exc:  # noqa: BLE001 — the refusal stands
        logger.warning("session %s: retry onto the head failed (%s)", sid, type(exc).__name__)
        return None


def save_event(store: SessionStore, ev: dict, bound: Optional[dict] = None,
               on_conflict=None, on_failure=None) -> Optional[int]:
    """Save one ``session-bundle`` event; the new version, or None when refused.

    ``bound`` is what agent-api handed the unit (``{session_id, subject, base}``), and it — never the
    event — decides where the version goes (S4). The worker's own ``session_id`` is a claim: one
    that differs from the bound session is refused, a base the unit was not handed is refused, and
    the speaker must still be able to write the session when the version lands."""
    if bound is None:
        logger.warning("session bundle refused: the unit was handed no conversation")
        return None
    sid = str(bound.get("session_id") or "")
    claimed = str(ev.get("session_id") or "")
    if claimed != sid:
        logger.warning("session %s: a hand-back naming another session was refused", sid)
        return None
    try:
        base = int(ev.get("base") or 0)
    except (TypeError, ValueError):
        logger.warning("session %s: bundle refused (bad base)", sid)
        return None
    if base != int(bound.get("base") or 0):
        try:
            files = {k: base64.b64decode(v) for k, v in (ev.get("files") or {}).items()}
        except (ValueError, TypeError):
            files = None
        if files and store.can_write(sid, str(bound.get("subject") or "")):
            v = _retry_onto_head(store, sid, str(bound.get("subject") or ""), files)
            if v is not None:
                return v
        logger.warning("session %s: a hand-back on base %d was refused, the unit was handed %d",
                       sid, base, int(bound.get("base") or 0))
        if on_conflict is not None:
            on_conflict(base, int(bound.get("base") or 0))
        return None
    try:
        if not store.can_write(sid, str(bound.get("subject") or "")):
            logger.warning("session %s: the speaker can no longer write it; the version was refused", sid)
            return None
        files = {k: base64.b64decode(v) for k, v in (ev.get("files") or {}).items()}
        return store.save_bundle(sid, base, files)
    except SessionConflict as exc:
        v = _retry_onto_head(store, sid, str(bound.get("subject") or ""), files)
        if v is not None:
            return v
        logger.warning("session %s: version based on %d refused, head is %d", sid, exc.base, exc.head)
        if on_conflict is not None:
            on_conflict(exc.base, exc.head)
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("session %s: bundle refused (%s)", sid, type(exc).__name__)
    except Exception as exc:  # noqa: BLE001 — a store that cannot write now: said, and the next turn hands it back again
        logger.error("session %s: version not saved, the store failed (%s)", sid, type(exc).__name__)
        if on_failure is not None:
            on_failure(base)
    return None


class SessionSaver:
    """Reads the ``session-bundle`` events of the units it handed a conversation to."""

    def __init__(self, store: SessionStore, redis_client, *, output_topic, alive=None) -> None:
        self._store = store
        self._r = redis_client
        self._topic = output_topic
        #: ``alive(unit_id) -> bool``: whether the unit's workload still runs. The lease is renewed
        #: only while it does (None = no probe wired: renew while watched).
        self._alive = alive
        self._lock = threading.Lock()
        self._watch: dict[str, dict] = {}
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def watch(self, unit_id: str, from_id: str = "0-0", *, session_id: str, subject: str,
              base: int, replace: bool = False) -> None:
        """Bind ``unit_id`` to the session it was just handed, at ``base`` (S4). A unit serves one
        session; a later hand-over of the same session moves the expected base forward (a newer
        head) and never back (the worker keeps a newer local copy, ``worker.session_carry``) —
        except a ``replace`` hand-over, after a refused save, which the worker adopts whatever its
        version."""
        with self._lock:
            w = self._watch.get(unit_id)
            if w is None or w["session_id"] != session_id or w["subject"] != subject:
                if w is not None:
                    logger.warning("session saver: unit re-bound to another session; the old watch dropped")
                self._watch[unit_id] = {"cursor": from_id or "0-0", "expect": 1, "since": time.time(),
                                        "session_id": session_id, "subject": subject,
                                        "base": int(base or 0)}
            else:
                w["expect"] += 1
                w["since"] = time.time()
                w["base"] = int(base or 0) if replace else max(int(w["base"]), int(base or 0))

    def watching(self) -> list[str]:
        with self._lock:
            return sorted(self._watch)

    def poll_once(self) -> int:
        """One pass over every watched unit; returns how many versions were saved."""
        saved = 0
        with self._lock:
            units = {u: dict(w) for u, w in self._watch.items()}
        for uid, w in units.items():
            try:
                entries = self._r.xrange(self._topic(uid), min=f"({w['cursor']}", count=200) or []
            except Exception as exc:  # noqa: BLE001 — a stream that cannot be read is tried again
                logger.warning("session saver: unit stream unreadable (%s)", type(exc).__name__)
                continue
            done = 0
            cursor = w["cursor"]
            for eid, fields in entries:
                cursor = eid
                try:
                    ev = json.loads((fields or {}).get("event") or "{}")
                except ValueError:
                    continue
                t = ev.get("type")
                if t == "session-bundle":
                    with self._lock:
                        cur = self._watch.get(uid)
                        bound = dict(cur) if cur is not None else None
                    sid = str((bound or {}).get("session_id") or "")

                    def _conflict(base, head, _sid=sid, _uid=uid):
                        # THE REPLY DOES NOT VANISH SILENTLY (S5): the refusal is recorded, typed,
                        # where the conversation's readers look (the history route).
                        record_fault(self._r, _sid, fault("session_conflict", _sid, unit=_uid,
                                                          base=int(base), head=int(head)))

                    def _failure(base, _sid=sid, _uid=uid):
                        record_fault(self._r, _sid, fault("session_unavailable", _sid, unit=_uid,
                                                          base=int(base)))

                    try:   # a hand-back travels by reference (shared.stream_bounds)
                        ev = stream_bounds.resolve_handback(getattr(self._r, "get", None), ev)
                    except Exception as exc:  # noqa: BLE001 — counted as a store failure below
                        logger.warning("session %s: hand-back unreadable (%s)", sid, type(exc).__name__)
                        _failure(int(ev.get("base") or 0))
                        continue
                    version = save_event(self._store, ev, bound, on_conflict=_conflict,
                                         on_failure=_failure)
                    if version is not None:
                        saved += 1
                        clear_fault(self._r, sid)
                        with self._lock:
                            cur = self._watch.get(uid)
                            if cur is not None:
                                cur["base"] = max(int(cur["base"]), int(version))
                elif t == "turn-complete":
                    done += 1
            with self._lock:
                cur = self._watch.get(uid)
                if cur is None:
                    continue
                cur["cursor"] = cursor
                cur["expect"] -= done
                if cur["expect"] <= 0 or time.time() - cur["since"] > WATCH_TTL_S:
                    self._watch.pop(uid, None)
                    release = cur.get("session_id")
                else:
                    release = None
            if release:
                release_lease(self._r, release, uid)
            else:
                self._heartbeat(uid)
        return saved

    def _heartbeat(self, uid: str) -> None:
        """Renew the head lease of a watched unit every ``LEASE_RENEW_S`` while its workload is
        alive. A dead one is not renewed: its lease lapses within ``LEASE_TTL_S``."""
        with self._lock:
            cur = self._watch.get(uid)
            if cur is None:
                return
            now = time.time()
            if now - float(cur.get("renewed") or cur["since"]) < LEASE_RENEW_S:
                return
            cur["renewed"] = now
            sid = cur.get("session_id")
        alive = True
        if self._alive is not None:
            try:
                alive = bool(self._alive(uid))
            except Exception:  # noqa: BLE001 — an unanswerable probe does not keep a lease alive
                alive = False
        if alive:
            with self._lock:
                cur = self._watch.get(uid)
                if cur is not None:
                    cur["dead"] = 0
            renew_lease(self._r, sid, uid)
            return
        # GONE, SAID ONCE (app.dev 2026-10-16: workers died at start and a chat said "still working"
        # for 15 minutes). One dead probe may be a cold start; two in a row is a unit that will not
        # answer: stop watching it, free the head NOW (the next message starts a fresh turn), and
        # record why on the unit, where an open view of it reads the reason and ends.
        with self._lock:
            cur = self._watch.get(uid)
            if cur is None:
                return
            cur["dead"] = int(cur.get("dead") or 0) + 1
            gone = cur["dead"] >= DEAD_PROBES
            if gone:
                self._watch.pop(uid, None)
        if not gone:
            logger.warning("session %s: unit is not running; its head lease is left to lapse", sid)
            return
        logger.warning("session %s: unit %s is gone; the turn is ended and the head freed", sid, uid)
        release_lease(self._r, sid, uid)
        from control_plane import unit_faults  # noqa: PLC0415
        unit_faults.record(self._r, uid, INTERRUPTED)

    def start(self) -> None:
        if self._thread is not None:
            return

        def _loop() -> None:
            while not self._stop.wait(POLL_S):
                try:
                    if self._watch:
                        self.poll_once()
                except Exception:  # noqa: BLE001 — the saver outlives any one bad pass
                    logger.exception("session saver pass failed")

        self._thread = threading.Thread(target=_loop, daemon=True, name="session-saver")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


#: The canonical names a bundle carries the pointer and turn records under (``worker.session_carry``;
#: the worker image does not import the control plane, so the literal is held in both and a test
#: asserts they are equal).
CANONICAL = "conversation"


def _first_at(raw: str) -> str:
    """The first timestamp a harness transcript carries ("" when none) — its place in the chat."""
    for line in raw.splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("timestamp"), str):
            return obj["timestamp"]
    return ""


def history_from_files(files: dict) -> list[dict]:
    """The terminal-renderable turns of a stored conversation — the same parser the workspace
    layout uses (``workspace_reader.parse_history``).

    A CHAT IS EVERY HARNESS SESSION IT RAN ON, NOT ONLY THE LAST (#1783, live 2026-10-15). The
    worker starts a fresh harness session when a history is over its resume budget or was written by
    another harness; the pointer then names the new one, and reading only that showed a five-turn
    chat as its last message. The earlier transcripts are still in the bundle: each one that holds a
    turn this chat recorded is read too, oldest first by its own timestamps. A transcript no turn
    record names (a machinery turn, a side session) is not this conversation and is not shown."""
    import re  # noqa: PLC0415

    from control_plane.workspace_reader import parse_history  # noqa: PLC0415

    pointer = files.get(f".claude/sessions/{CANONICAL}.session", b"").decode("utf-8", "replace").strip()
    if not pointer or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", pointer):
        return []
    records = []
    for line in files.get(f".claude/sessions/{CANONICAL}.turns.jsonl", b"").decode("utf-8", "replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and isinstance(rec.get("user_text"), str) and rec.get("key"):
            records.append(rec)
    current = None
    earlier: list[tuple[str, str, str]] = []
    for rel, data in sorted(files.items()):
        if not (rel.startswith(".claude/projects/") and rel.endswith(".jsonl") and rel.count("/") == 3):
            continue
        raw = data.decode("utf-8", "replace")
        if rel.endswith(f"/{pointer}.jsonl"):
            if current is None:
                current = raw
            continue
        if any(t.get("role") == "user" and "user_text" in t for t in parse_history(raw, records)):
            earlier.append((_first_at(raw), rel, raw))
    if current is None:
        return []
    earlier.sort()
    return parse_history("\n".join([*(raw for _at, _rel, raw in earlier), current]), records)


def history(store: SessionStore, row: dict) -> Optional[list[dict]]:
    """The stored conversation's turns, or None when the store holds no version yet (the caller then
    reads the old layout — the one-release fallback)."""
    if not row or int(row.get("version") or 0) == 0:
        return None
    return history_from_files(store.load_bundle(row["id"])["files"])
