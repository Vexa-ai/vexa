"""api_shared.py — everything agent-api's routes are built OUT of, and none of the wiring.

Split out of `api.py` so the per-owner routers in `control_plane/routers/` can import it. They
cannot import `api.py`: `api.py` imports THEM, and a module that imports its own importer is a
cycle whose behaviour depends on which line the interpreter reached first — a fragile thing to
put under 79 routes.

Nothing here changed in the move. It is the same request/response models, the same pure helpers
and the same constants, in the same order, byte for byte; `git log --follow` and a `git diff -M`
show it as a move. What DID change is that they now have exactly one home, reachable from both
`create_app` and every router, rather than being reachable only from inside one 3,914-line file.
"""
from __future__ import annotations


import os

import functools
import hashlib
import hmac
import json
import logging
import re
import time
from pathlib import Path
from typing import Callable, Iterator, Optional

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from jsonschema.exceptions import ValidationError
from pydantic import BaseModel, Field

from control_plane import meeting_room
from control_plane import meeting_steering
from control_plane import schedule_digest as schedule_digest_mod
from control_plane import routines as routines_mod
from control_plane.config_preflight import NOT_CONFIGURED, capability_state, missing_capability_keys
from shared import units
from workspaces.shared import entities as entities_mod
from control_plane import workspace_routines as workspace_routines_mod
from control_plane import link_resolver as link_resolver_mod
from control_plane import workspace_ids as ids_mod
from shared.seeding import resolve_seed_dir, seed_workspace, validate_seed
from control_plane.workspace_attach import (
    CloneError,
    activate_workspace,
    active_workspaces,
    attach_shared_workspace,
    attached_workspaces,
    create_shared_workspace_dir,
    create_workspace,
    deactivate_workspace,
    delete_workspace,
    ensure_workspace_private,
    ensure_workspace_shareable,
    rename_workspace,
    set_archived,
    set_shared_active,
    shared_active_mounts,
    shared_attached_state,
    swap_workspace,
    workspace_dir_for,
    workspace_slot_dir,
)
from control_plane.workspace_publish import PublishError, RepoExistsError, publish_workspace, published_remote_url
from control_plane.workspace_git_sync import RemoteSyncError, pull_origin, push_origin, remote_status
from control_plane.workspace_purpose import read_purpose, write_purpose
from control_plane import workspace_membership as membership_mod
from control_plane import git_credentials as git_creds
from control_plane import dispatch as dispatch_mod
from control_plane import deploy_keys as deploy_keys_mod
from control_plane import workspace_credentials as wcreds
from control_plane import repo_ref
from shared.git_redaction import redact as redact_secrets
from workspaces.shared import workspace_paths as wpaths
from control_plane import global_layer
from control_plane import version as version_mod
from control_plane import system_mounts
from control_plane import scaffolds as scaffolds_mod
from control_plane import model_endpoint
from control_plane import chat_intents
from control_plane.workspace_membership import MembershipError, MembershipIndex, InMemoryMembershipIndex
from control_plane.dispatch import Dispatcher
from control_plane.events import event_to_invocation
from shared.ports import SchedulerPort, StreamReader
from control_plane.workspace_reader import WorkspaceReader


logger = logging.getLogger("agent_api.api")

# The phase, in the meeting's own vocabulary — the same three words the terminal's header uses
# (`minutes/MinutesShell.tsx` PHASE_WORD). One vocabulary, two renderers.
_PHASE_WORD = {"prep": "upcoming", "live": "live", "post": "held"}


def _iso(epoch) -> "str | None":
    """An epoch as an ISO-8601 UTC string, or None. The wire carries times as STRINGS because the
    client half pins them that way; the store keeps the float, which is what arithmetic wants."""
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(epoch)))
    except (TypeError, ValueError):
        return None


def _provenance_line(prov: dict, minted_at) -> str:
    """The record's provenance as ONE readable line — what produced this touch, and when.

    The OBJECT stays on the wire beside it: flow, reaction id, run id and minted_by are four facts,
    and a string that concatenates four facts is not a record of them. This line exists so a panel
    can show something without parsing, not so the object can be dropped."""
    if not isinstance(prov, dict):
        return ""
    bits = [str(prov[k]) for k in ("flow", "reaction_id", "run_id") if prov.get(k)]
    if prov.get("minted_by"):
        bits.append(f"minted by {prov['minted_by']}")
    stamp = _iso(minted_at)
    if stamp:
        bits.append(stamp)
    return " · ".join(bits)


def _epoch_text(when) -> str:
    """An epoch as a readable UTC line, or "" — the last-resort rendering of a meeting's time when
    the caller did not render one in the recipient's own zone. UTC and SAID to be UTC: a bare
    "14:00" in a zone nobody named is the kind of half-fact that reads as a bug in an inbox."""
    try:
        return time.strftime("%a %d %b %H:%M UTC", time.gmtime(float(when)))
    except (TypeError, ValueError):
        return ""

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MEETING_STREAM_TRANSCRIPT_REPLAY = 80


def _upload_filename(name: str | None) -> str:
    base = (name or "upload").replace("\\", "/").rsplit("/", 1)[-1].strip()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._-")
    return base[:160] or "upload"


def _truncate_title(text: str, *, limit: int = 60) -> str:
    """A session's default title — the first prompt, single-lined + truncated."""
    title = " ".join((text or "").split())
    return title[: limit - 1] + "…" if len(title) > limit else title


def _stream_tail_id(redis_url: str | None, stream: str) -> str | None:
    if not redis_url:
        return None
    try:
        import redis

        r = redis.from_url(redis_url, decode_responses=True)
        rows = r.xrevrange(stream, count=1)
        return str(rows[0][0]) if rows else "0-0"
    except Exception as exc:
        logger.warning("could not resolve transcript stream tail for %s: %s", stream, exc)
        return None


# ── THE CHAT'S INBOX (Vexa-ai/vexa#1610) ────────────────────────────────────────────────────────
#
# The founder, dropping several acts onto one page while a job ran: *"i drop new tasks to that chat,
# can i be sure everything submitted there is actually processed?"* The honest answer was no, and one
# of the two reasons was that a message typed mid-turn lived in ONE BROWSER's localStorage until that
# turn ended — another device never saw it, and a cleared browser never sent it.
#
# So the in-topic is the inbox: every submission is XADD'd to it the moment it is submitted, and the
# worker takes entries off it in order. What is still QUEUED is therefore exactly "the entries after
# the worker's cursor", which the worker publishes (`shared/units.inbox_cursor_key`). This reads it.
#
# Two filters, and each one is about not lying:
#   · an entry with no `inbox` stamp is skipped — it was written by a build that had no inbox, and
#     showing an old consumed message as pending would invent a queue nobody is in;
#   · an entry older than the cap is skipped — a worker that died without ever writing a cursor would
#     otherwise leave a chat permanently claiming to be behind.

#: An act runs 30-120s and ten of them queue, so an hour is generous by design: the cap is here to
#: bound a WORKER THAT NEVER RAN, not to hurry a queue that is working through itself.
INBOX_PENDING_MAX_AGE_SEC = 3600

#: How many queued entries a reader will enumerate. Past this the count is what matters, not the row.
INBOX_PENDING_MAX_ROWS = 200


def inbox_pending(redis_url: "str | None", unit_id: str) -> list[dict]:
    """Everything submitted to this chat that its worker has not taken yet, oldest first.

    Best-effort by construction: no redis, an unreachable one, a stream that has never existed — all
    answer "nothing queued". A chat that cannot read its inbox shows no queued rows, which is what it
    showed before this existed; it must never fail the surface that is asking."""
    if not redis_url:
        return []
    try:
        import redis

        r = redis.from_url(redis_url, decode_responses=True)
        cursor = r.get(units.inbox_cursor_key(unit_id))
        # INCLUSIVE + DROP, rather than the `(id` exclusive form: exclusive ranges need Redis 6.2 and
        # this reader must not be the thing that pins the deployment's server version.
        rows = r.xrange(units.input_topic(unit_id), min=cursor or "-", max="+",
                        count=INBOX_PENDING_MAX_ROWS + 1)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not read the inbox for unit=%s: %s", unit_id, exc)
        return []
    out: list[dict] = []
    now = time.time()
    for entry_id, fields in rows or []:
        if cursor and entry_id == cursor:
            continue                      # the entry the worker is on — taken, not queued
        try:
            msg = json.loads(fields.get("turn", "{}"))
        except (TypeError, ValueError):
            continue
        meta = msg.get("inbox")
        if not isinstance(meta, dict):
            continue
        at = float(meta.get("at") or 0)
        if at and now - at > INBOX_PENDING_MAX_AGE_SEC:
            continue
        out.append({"entry": entry_id, "id": str(meta.get("id") or entry_id),
                    "kind": str(meta.get("kind") or ""), "target": str(meta.get("target") or ""),
                    "display": str(meta.get("display") or ""), "at": at})
    return out


# How long a turn's start-cursor record lives — covers the client's whole resume window (its hard
# timeout is minutes); after this a stale nonce simply falls back to the fresh-dispatch path.
CHAT_TURN_HEAD_TTL_SEC = 900


def _chat_turn_head_key(unit_id: str) -> str:
    return f"unit:{unit_id}:turnhead"


def _record_chat_turn_head(redis_url: str | None, unit_id: str, turn_id: str, start: str) -> None:
    """Remember, per warm chat unit, the CURRENT turn's nonce + the out-Stream id it started after.
    Best-effort (redis-less unit tests skip it): losing the record only degrades a no-cursor retry
    back to today's behavior, it never breaks the turn."""
    if not redis_url:
        return
    try:
        import redis

        r = redis.from_url(redis_url, decode_responses=True)
        r.set(_chat_turn_head_key(unit_id), json.dumps({"turn_id": turn_id, "start": start}),
              ex=CHAT_TURN_HEAD_TTL_SEC)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not record chat turn head for %s: %s", unit_id, exc)


def _chat_turn_head(redis_url: str | None, unit_id: str, turn_id: str) -> str | None:
    """The recorded start cursor of the turn ``turn_id`` — or None when it isn't the current turn."""
    if not redis_url or not turn_id:
        return None
    try:
        import redis

        r = redis.from_url(redis_url, decode_responses=True)
        raw = r.get(_chat_turn_head_key(unit_id))
        head = json.loads(raw) if raw else None
        return head["start"] if head and head.get("turn_id") == turn_id else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not read chat turn head for %s: %s", unit_id, exc)
        return None


class _Sessions:
    """Durable, per-subject chat-session index. Each session carries a created + last-active stamp and an
    optional title (default the first prompt, truncated). ``list`` returns them most-recent first.

    Backed by redis when a client is wired (one hash per session under ``agent:sessions:<subject>`` +
    the per-subject id set), with an in-memory fallback so the unit tests need no redis. Multiple
    conversation threads live in the ONE user workspace — this indexes the threads, not workspaces.

    ⚠ THIS INDEX IS THE RAIL (Vexa-ai/vexa#1591). The terminal's chat list lived in ONE browser's
    ``localStorage``, so the founder signed in from a new window after a morning of work and got an
    empty rail — *"i logged in again and now see no chats and it's starting over again while it has
    the context"*. The rail is now DERIVED from these rows, so what a session carries is what a rail
    row can show, and three fields were missing:

      · ``workspaces`` — the mount set the chat is over, so a row reopened on a second browser
        mounts what it always mounted rather than the personal default;
      · ``scaffold``   — ``{kind, id}``, the record the chat was composed from. The client's own
        rule (F37) is that the kind and the record id travel TOGETHER or not at all, so this is one
        object here too and a half-record is dropped rather than repaired;
      · ``touched``    — did a PERSON write in this thread, or is every turn in it machinery? The
        rail's default filter hides the untouched, and the client could only ever know about turns
        typed in THIS browser. The server sees every turn, so it is the honest place to record it.

    ``touched`` is a LATCH and its absence means ``True``: a row written before this field existed
    is a real conversation somebody had, and the failure the rail is being fixed for is chats that
    do not show. A row that has only ever carried machinery says so explicitly ("0").

    ⚠ AND A CHAT THAT MADE A MEETING IS THAT MEETING'S CHAT (Vexa-ai/vexa#1597). ``meeting`` — the
    row id, with ``meeting_native`` beside it — is written when a bot goes out FROM this session,
    and it is why the field is here rather than derived like the rest: the terminal names a
    meeting's own session ``meet-<row>`` and reads the ref back off that id, which answers for the
    meeting somebody opened from the rail and answers NOTHING for the chat that created the meeting
    from itself. That chat has an ordinary id, so the binding exists nowhere but here, and without it
    the rail showed the founder TWO rows for one meeting — his conversation, and an auto-created
    meeting row beside it: *"there is no need to create a new chat for that — we already have
    meeting owner, just attach the status to it"*.

    A LATCH, like ``touched``, and for a sharper reason: the chat's identity is what the reader is
    looking at. A second send in the same conversation must not silently move the room, the pinned
    transcript and the note out from under them — it is a second meeting, and a second meeting is a
    second chat.

    ⚠ AND ONE OF THOSE MOUNTS IS THE ONE WRITES GO TO (Vexa-ai/vexa#1611). ``target`` — a slug, or
    absent for the person's own desk — is a DIFFERENT question from ``workspaces``: that list is
    what this chat can reach, this field is where it works. The founder was in a chat whose header
    chip read ``personal`` while the whole conversation was about a customer's workspace, and the
    files landed on his desk: *"it creates files in the wrong workspace, we need so that the thing
    knew the workspace of writing, if it's specified. We have this "personal" and we probably
    should be able to set a workspace that we are targeting (other workspaces still available to
    read and even to write, if explicit ask and purpose)"*.

    NOT a latch, unlike ``meeting``: a target is a thing a person changes by clicking a chip, and
    changing it is the whole feature. ``set_target`` is its ONE writer."""

    def __init__(self, redis_client=None) -> None:
        self._redis = redis_client
        self._mem: dict[str, dict[str, dict]] = {}  # subject → {session → {created,last_active,title}}

    # ── redis key helpers ──
    @staticmethod
    def _ids_key(subject: str) -> str:
        return f"agent:sessions:{subject}"

    @staticmethod
    def _meta_key(subject: str, session: str) -> str:
        return f"agent:session:{subject}:{session}"

    def _now(self) -> float:
        import time

        return time.time()

    @staticmethod
    def _scaffold_pair(raw) -> "dict | None":
        """``{kind, id}`` or nothing — the client's F37 rule, enforced on the way IN as well.

        A kind with no record id is the shape that let a planted row render the pre-scaffold admin
        card. A half-record is dropped here rather than stored and repaired later."""
        if isinstance(raw, str) and raw:
            try:
                raw = json.loads(raw)
            except ValueError:
                return None
        if not isinstance(raw, dict):
            return None
        kind, sid = str(raw.get("kind") or ""), str(raw.get("id") or "")
        return {"kind": kind, "id": sid} if kind and sid else None

    def upsert(self, subject: str, session: str, *, title: str | None = None,
               workspaces: "list[str] | None" = None, scaffold: "dict | None" = None,
               touched: bool | None = None, meeting: str | None = None,
               meeting_native: str | None = None) -> None:
        """Record the session on use: create it (stamping ``created`` + a default ``title``) or touch its
        ``last_active``. An explicit ``title`` overrides; otherwise the first prompt seeds it once.

        ``workspaces`` and ``scaffold`` are RESTATED, not merged: they describe what this chat is
        over right now, and a turn that knows them is the freshest answer there is. ``None`` means
        "this turn did not know", which is not the same as "there is none" — it leaves what is
        stored alone.

        ``touched`` only ever goes UP. It answers "has a person written here", and a machinery turn
        arriving after a human one does not un-write what they typed.

        ``meeting`` is written ONCE and never overwritten — see the class docstring. A send into a
        second meeting from the same chat leaves the first binding standing."""
        now = self._now()
        pair = self._scaffold_pair(scaffold)
        bind = str(meeting or "").strip()
        native = str(meeting_native or "").strip()
        if self._redis is not None:
            mkey = self._meta_key(subject, session)
            existing = self._redis.hgetall(mkey) or {}
            fields = {"last_active": str(now)}
            if not existing:
                fields["created"] = str(now)
                fields["title"] = title or session
                # stamped on CREATE so "absent" keeps meaning "written before the field existed"
                fields["touched"] = "1" if touched else "0"
            elif title is not None:
                fields["title"] = title
            if touched and existing.get("touched") != "1":
                fields["touched"] = "1"
            if workspaces is not None:
                fields["workspaces"] = json.dumps([str(w) for w in workspaces if str(w).strip()])
            if pair is not None:
                fields["scaffold"] = json.dumps(pair)
            if bind and not (existing.get("meeting") or "").strip():
                fields["meeting"] = bind
                if native:
                    fields["meeting_native"] = native
            self._redis.hset(mkey, mapping=fields)
            self._redis.sadd(self._ids_key(subject), session)
            return
        rec = self._mem.setdefault(subject, {}).get(session)
        if rec is None:
            rec = {"created": now, "last_active": now, "title": title or session,
                   "touched": bool(touched)}
            self._mem[subject][session] = rec
        else:
            rec["last_active"] = now
            if title is not None:
                rec["title"] = title
            if touched:
                rec["touched"] = True
        if workspaces is not None:
            rec["workspaces"] = [str(w) for w in workspaces if str(w).strip()]
        if pair is not None:
            rec["scaffold"] = pair
        if bind and not str(rec.get("meeting") or "").strip():
            rec["meeting"] = bind
            if native:
                rec["meeting_native"] = native

    def add_workspace(self, subject: str, session: str, workspace: str) -> bool:
        """PUT a workspace into this chat's focus, and say whether that changed anything
        (Vexa-ai/vexa#1603). THE RAISER of the stale-mounts semaphore.

        ADDITIVE, unlike ``upsert(workspaces=…)``, and the difference is the whole point. That
        parameter RESTATES the set because a scaffolded turn knows the whole set; a turn that
        CREATED a workspace knows one new member and nothing about the rest, so restating from it
        would silently drop every other mount the chat had.

        WHY IT ALSO RAISES A FLAG. A chat's worker is spawned with its mount table and keeps it for
        the whole warm window (15 minutes) — the runtime's create is an idempotent touch that
        DISCARDS the spec env — so a workspace made mid-conversation stayed invisible to the agent
        for the rest of it, which is exactly what the founder hit: *"not native workspace??"*. The
        cure is a new container, and the way to get one without stopping anything is a new unit id;
        ``mount_gen`` is what puts one in ``dispatch_id``.

        The flag rather than the bump, because THIS RUNS MID-TURN. Moving the id under a turn that
        is streaming would strand its own reconnect on a unit that does not exist. So the write here
        only says *the mounts are stale*; ``take_mount_generation`` — called once, at the start of
        the NEXT fresh turn — is the lowerer. One value, one raiser, one lowerer.

        A REAL CHANGE ONLY: re-creating a workspace already in the focus, or a retried turn,
        raises nothing and costs nobody a cold start."""
        wid = str(workspace or "").strip()
        if not wid:
            return False
        if self._redis is not None:
            mkey = self._meta_key(subject, session)
            meta = self._redis.hgetall(mkey) or {}
            try:
                current = json.loads(meta.get("workspaces") or "[]")
            except ValueError:
                current = []
            current = [str(w) for w in current if str(w).strip()] if isinstance(current, list) else []
            if wid in current:
                return False
            self._redis.hset(mkey, mapping={"workspaces": json.dumps(current + [wid]),
                                            "mounts_stale": "1",
                                            "last_active": str(self._now())})
            self._redis.sadd(self._ids_key(subject), session)
            return True
        rec = self._mem.setdefault(subject, {}).get(session)
        if rec is None:
            rec = {"created": self._now(), "last_active": self._now(), "title": session,
                   "touched": False}
            self._mem[subject][session] = rec
        current = [str(w) for w in (rec.get("workspaces") or [])]
        if wid in current:
            return False
        rec["workspaces"] = current + [wid]
        rec["mounts_stale"] = True
        rec["last_active"] = self._now()
        return True

    def set_target(self, subject: str, session: str, workspace: str) -> bool:
        """POINT this chat's writes at a workspace, and say whether that changed anything
        (Vexa-ai/vexa#1611). THE ONE WRITER of ``target``.

        ``workspace`` is a slug, or ``""`` — the person's own desk, which is the default and the
        thing a chat falls back to rather than a second name for it. A malformed slug is REFUSED,
        never repaired, for the reason ``workspace_focus`` refuses one: this is durable and it
        decides where every later turn writes.

        IT RAISES THE SAME STALE-MOUNTS SEMAPHORE ``add_workspace`` does, and the reason is the same
        shape rather than the same fact. The mount SET does not change when a target moves — every
        workspace in the focus was already mounted — but two things baked into the container do: the
        turn's cwd (``dispatch._worker_cwd`` makes the target primary) and the delegation token the
        tools default their ``slug`` from. A warm worker keeps both for its whole 15-minute window,
        so without this the chip would move and the writes would keep landing where they were —
        which is the defect, not a smaller version of it.

        A REAL CHANGE ONLY: re-selecting the target already in force raises nothing and costs
        nobody a cold start."""
        wid = str(workspace or "").strip()
        if wid and not _is_slug(wid):
            return False
        if self._redis is not None:
            mkey = self._meta_key(subject, session)
            meta = self._redis.hgetall(mkey) or {}
            if (meta.get("target") or "").strip() == wid:
                return False
            self._redis.hset(mkey, mapping={"target": wid, "mounts_stale": "1",
                                            "last_active": str(self._now())})
            self._redis.sadd(self._ids_key(subject), session)
            return True
        rec = self._mem.setdefault(subject, {}).get(session)
        if rec is None:
            rec = {"created": self._now(), "last_active": self._now(), "title": session,
                   "touched": False}
            self._mem[subject][session] = rec
        if str(rec.get("target") or "").strip() == wid:
            return False
        rec["target"] = wid
        rec["mounts_stale"] = True
        rec["last_active"] = self._now()
        return True

    def target(self, subject: str, session: str) -> str:
        """This chat's target workspace slug, or ``""`` for the person's own desk."""
        if self._redis is not None:
            meta = self._redis.hgetall(self._meta_key(subject, session)) or {}
            return (meta.get("target") or "").strip()
        return str((self._mem.get(subject, {}).get(session) or {}).get("target") or "").strip()

    @staticmethod
    def _as_gen(raw) -> int:
        try:
            return max(0, int(float(raw or 0)))
        except (TypeError, ValueError):
            return 0

    def mount_gen(self, subject: str, session: str) -> int:
        """This chat's CURRENT mount generation — read only, and what a RESUME must use.

        ``0`` (every chat that has never created a workspace, and every row written before the field
        existed) keeps the unit id byte-identical to the one it has always had."""
        if self._redis is not None:
            meta = self._redis.hgetall(self._meta_key(subject, session)) or {}
            return self._as_gen(meta.get("mount_gen"))
        return self._as_gen((self._mem.get(subject, {}).get(session) or {}).get("mount_gen"))

    def take_mount_generation(self, subject: str, session: str) -> int:
        """The generation THIS turn runs under — THE LOWERER of the stale-mounts semaphore.

        Called once per FRESH turn, never on a resume. If a previous turn created a workspace the
        flag is standing: the generation goes up by one and the flag comes down, so this turn gets a
        unit id nobody has spawned yet and therefore a container built with the current mount table.
        Otherwise it is a plain read and the id does not move.

        Idempotent by construction — the flag is gone after the first call, so a second turn on the
        same focus reuses the warm unit the first one spawned."""
        if self._redis is not None:
            mkey = self._meta_key(subject, session)
            meta = self._redis.hgetall(mkey) or {}
            gen = self._as_gen(meta.get("mount_gen"))
            if str(meta.get("mounts_stale") or "") != "1":
                return gen
            gen += 1
            self._redis.hset(mkey, mapping={"mount_gen": str(gen), "mounts_stale": "0"})
            return gen
        rec = self._mem.get(subject, {}).get(session)
        if rec is None:
            return 0
        gen = self._as_gen(rec.get("mount_gen"))
        if not rec.get("mounts_stale"):
            return gen
        gen += 1
        rec["mount_gen"] = gen
        rec["mounts_stale"] = False
        return gen

    def list(self, subject: str) -> list[dict]:
        """The subject's sessions, most-recently-active first — the rail, as the server holds it."""
        rows: list[dict] = []
        if self._redis is not None:
            for session in self._redis.smembers(self._ids_key(subject)) or set():
                meta = self._redis.hgetall(self._meta_key(subject, session)) or {}
                try:
                    mounts = json.loads(meta.get("workspaces") or "[]")
                except ValueError:
                    mounts = []
                rows.append({
                    "session": session,
                    "title": meta.get("title") or session,
                    "created": float(meta.get("created", 0) or 0),
                    "last_active": float(meta.get("last_active", 0) or 0),
                    "workspaces": [str(w) for w in mounts] if isinstance(mounts, list) else [],
                    # absent → the person's own desk. `null`, not `""`, so a client can tell "this
                    # server predates the field" from "this chat targets the desk" — they mean the
                    # same thing today and a client that guessed would be wrong the day they do not.
                    "target": (meta.get("target") or "").strip() or None,
                    "scaffold": self._scaffold_pair(meta.get("scaffold")),
                    # absent → True: a row older than the field is a conversation that happened
                    "touched": meta.get("touched") != "0",
                    "meeting": (meta.get("meeting") or "").strip() or None,
                    "meeting_native": (meta.get("meeting_native") or "").strip() or None,
                    "mount_gen": self._as_gen(meta.get("mount_gen")),
                })
        else:
            for session, meta in self._mem.get(subject, {}).items():
                rows.append({
                    "session": session, "title": meta.get("title") or session,
                    "created": meta.get("created", 0.0), "last_active": meta.get("last_active", 0.0),
                    "workspaces": list(meta.get("workspaces") or []),
                    "target": str(meta.get("target") or "").strip() or None,
                    "scaffold": self._scaffold_pair(meta.get("scaffold")),
                    "touched": meta.get("touched", True) is not False,
                    "meeting": str(meta.get("meeting") or "").strip() or None,
                    "meeting_native": str(meta.get("meeting_native") or "").strip() or None,
                    "mount_gen": self._as_gen(meta.get("mount_gen")),
                })
        rows.sort(key=lambda r: r["last_active"], reverse=True)
        return rows

    def drop(self, subject: str, session: str) -> None:
        if self._redis is not None:
            self._redis.srem(self._ids_key(subject), session)
            self._redis.delete(self._meta_key(subject, session))
            return
        self._mem.get(subject, {}).pop(session, None)


# P21 (ADR 0027 family — the panel's stale-live finding): a registry entry is "live" only while
# segments actually FLOW. The watcher re-adds on every batch (~2s apart), so this much silence
# means the meeting is over even when the session_end frame was lost (e.g. a hot-reload racing the
# wire leaves it pending-unacked) — the server-side stale-"live" the terminal's durableTerminal
# guard was papering over.
LIVE_SILENCE_TTL_SEC = 60.0

class _LiveMeetings:
    """In-memory registry of live meetings — the terminal's 'meetings' feed. Keyed by session_uid (the
    native Meet code). A stopped/ended meeting is KEPT (``status='stopped'``) so the terminal can offer to
    send the bot back; ``add`` (re)marks it live. Liveness is EVIDENCE, not a latch: ``add`` stamps
    ``last_seen`` and ``list`` demotes an entry silent past LIVE_SILENCE_TTL_SEC to stopped (P21 —
    absence of the expected signal is itself a reportable state). The dev-tier foundation."""

    def __init__(self) -> None:
        self._by_uid: dict[str, dict] = {}

    def add(self, meeting: dict) -> None:
        m = dict(meeting)
        m["status"] = "live"
        m["last_seen"] = time.monotonic()
        self._by_uid[meeting["session_uid"]] = m

    def stop(self, session_uid: str) -> None:
        m = self._by_uid.get(session_uid)
        if m:
            m["status"] = "stopped"

    def drop(self, session_uid: str) -> None:
        # the meeting ended — keep the row (stopped) so 'send the bot back' stays available
        self.stop(session_uid)

    def list(self) -> list[dict]:
        now = time.monotonic()
        for m in self._by_uid.values():
            if m.get("status") == "live" and now - m.get("last_seen", now) > LIVE_SILENCE_TTL_SEC:
                m["status"] = "stopped"  # earned liveness expired — the segment flow went silent
        return list(self._by_uid.values())


class ChatContextBody(BaseModel):
    """The terminal-state CONTEXT BUNDLE (slice 1). ``extra="ignore"`` on purpose — forward-
    tolerant: a newer terminal adding bundle fields must never 422 against this server."""
    model_config = {"extra": "ignore"}
    tz: Optional[str] = None            # IANA tz for digest rendering (invalid → UTC)
    surface: Optional[dict] = None      # {list?: str, tab?: {kind: str}} — the ambient gate signal
    focus: Optional[dict] = None        # the focused thing (meeting/file/workspace/today); None = cleared
    include: Optional[dict] = None      # {schedule?: bool} — explicit user toggle beats the gate


class ChatBody(BaseModel):
    model_config = {"extra": "forbid"}
    prompt: str
    # subject is DERIVED server-side from X-User-Id (P20) — kept here only so a client that still sends it
    # doesn't 422 (extra=forbid); the value is IGNORED. Dropped from the client in Stage 4.
    subject: Optional[str] = None
    session: Optional[str] = None
    # LEGACY single-focus grounding ({kind, ref}) — still honored when ``context`` is absent, so
    # old clients keep byte-identical behavior. The terminal now sends ``context`` (below) too.
    active: Optional[dict] = None
    # the terminal-state context bundle; when present it is AUTHORITATIVE (including
    # ``focus: null`` = the user cleared the focus chip — legacy ``active`` is then ignored).
    context: Optional[ChatContextBody] = None
    # Client-minted TURN NONCE (one per user turn, constant across that turn's reconnect attempts).
    # Lets the server tell a no-cursor RETRY (the stream dropped before the client ever saw an ``id:``,
    # so it can't send Last-Event-ID) from a genuinely new turn with identical text ("yes" twice):
    # a matching nonce re-attaches from the turn's recorded start — no second dispatch, no lost events.
    turn_id: Optional[str] = None
    # THE MEETING ROOM (post-meeting run). The caller may name ONLY THE MEETING — a meetings-domain
    # ROW id — and the server resolves who was in it (control_plane/meeting_room.py). There is
    # deliberately NO field here that names a workspace or a subject: a caller able to say "mount
    # u_bob" could read any user's desk by naming it, which is the exact hole this shape exists to
    # close. Accepting it additionally requires the internal-tier secret (see ``_resolve_room``), so
    # the general end-user chat surface cannot open a room at all.
    room_meeting_id: Optional[str] = None
    # MEMBERSHIP = the INVITE's participant list, as ADDRESSES. The trusted caller holds the parsed
    # invite, so it sends the addresses; agent-api resolves each one to a subject through admin-api
    # and mounts only participants who ALREADY have a subject and a desk. Addresses, never subject
    # ids and never workspace names: the resolution — and therefore the blast radius — stays here.
    room_participants: Optional[list[str]] = None
    # The invite's ICS ``CN=`` map (address → display name). Used ONLY to match transcript speaker
    # labels back to addresses that are ALREADY in the list above, i.e. only to ORDER the room. A
    # name never admits anybody, so a bad match costs position and nothing else.
    room_participant_names: Optional[dict[str, str]] = None
    # The transcript's speaker labels, already ordered by speaking time DESCENDING (the caller holds
    # the transcript, so it does that arithmetic). Unmatched labels are simply ignored.
    room_speakers: Optional[list[str]] = None
    room_read_max: Optional[int] = None
    # THE SCAFFOLD (PRD 5.5). The terminal sends this on the FIRST turn of a chat a link composed.
    # It is an ID, never a mount list and never prompt text: the record it names says which
    # workspaces this turn mounts and what the opening ask is, and the server reads BOTH from the
    # store. A scaffold that is not this subject's is ignored (never an error) — a stale or
    # forwarded id must not be able to widen anybody's mounts, and must not break their turn either.
    scaffold_id: Optional[str] = None
    # THE INTENT (PRD decision 32/35). A button pressed on a page — Extend, Create this page,
    # Explore a term in a transcript, Highlight the transcript — is an ACT on a named thing, not a
    # sentence somebody typed. The terminal has sent this since decision 32 landed client-side; with
    # `extra="forbid"` above and no field here, every one of those presses 422'd. It is a plain dict
    # rather than a model on purpose: the CLOSED vocabulary is `chat_intents.INTENT_PRESETS`, an
    # unknown kind is ignored, and a client one release ahead must not be refused at the door.
    intent: Optional[dict] = None


class ScaffoldMintBody(BaseModel):
    """`POST /internal/scaffolds` — what a FLOW says at the moment it creates a touch.

    ``extra="forbid"``: a mint is the thing a step checks before it sends, so a caller that spells a
    field wrong has to hear about it here rather than mail a link built from a field nobody read.

    There is deliberately NO field carrying prompt text. ``opening`` is a NAME in `_global/asks/`
    and the server refuses anything that is not one — the URL never carried text (PRD 6) and neither
    does the record behind it, for the same reason: anyone who can mint a touch would otherwise be
    able to drive the recipient's agent."""
    model_config = {"extra": "forbid"}
    who: str                                   # the RECIPIENT ADDRESS. Not a subject: they may not exist yet.
    kind: str                                  # one of scaffolds.KINDS
    opening: str                               # a preset NAME in _global/asks/, never text
    meeting: Optional[str] = None              # the meetings-domain ROW id, or None. PHASE IS NOT STORED.
    workspaces: Optional[list[str]] = None     # slugs to mount; None = derived (see the route)
    refs: Optional[dict] = None                # the facts for the agent: title, when, organizer, participants…
    tabs: Optional[list[str]] = None           # UI half; None = the preset's own `tabs:`
    focus: Optional[str] = None                # UI half; None = the preset's own `focus:`
    # The RESTRICTED transcript share, minted by the caller against the meeting ROW
    # (`flows_steps/meeting.mint_transcript_share`) when the meeting is not the recipient's own.
    # Minted THERE and not here because the mint needs the meeting OWNER's gateway key, which flows
    # holds and agent-api deliberately does not — see the route's docstring.
    share_token: Optional[str] = None
    provenance: Optional[dict] = None          # flow, reaction/run id, minted_by (the fact's admitted_by)


class ScaffoldHandBody(BaseModel):
    """`POST /api/scaffolds/hand` — a HAND LINK (`/?ask=<preset>&meeting=<row>`) turned into a record.

    Two fields, both NAMES, neither prompt text — the same rule as the internal mint and for the same
    reason (PRD 6, decisions 13/18): a URL must never be able to drive somebody's agent. What made
    this route necessary is that the terminal used to substitute `?meeting=`/`?ws=` straight into the
    composed opening, so a crafted link put attacker-chosen text into the first turn.

    There is deliberately NO `who`: the recipient is the SIGNED-IN CALLER, taken from the session.
    A field would let anyone who can reach this route mint a first turn for somebody else."""
    model_config = {"extra": "forbid"}
    preset: str                                # a preset NAME in _global/asks/, never text
    meeting: Optional[str] = None              # a meetings-domain ROW id the CALLER can see, or None


class ResetBody(BaseModel):
    """Body for POST /api/chat/reset — the docs (api/agent.mdx) say it's just ``{session?}``. reset only
    needs the session; ``prompt``/``subject``/``active`` are accepted-and-ignored so a client reusing the
    chat-body shape doesn't 422 (reset must NOT require a prompt the way the chat turn does)."""
    model_config = {"extra": "forbid"}
    session: Optional[str] = None
    subject: Optional[str] = None
    prompt: Optional[str] = None
    active: Optional[dict] = None
    context: Optional[ChatContextBody] = None  # accepted-and-ignored, same rationale
    room_meeting_id: Optional[str] = None      # accepted-and-ignored (reset mounts nothing)


class RoutineCreate(BaseModel):
    """The Routines surface / ``/routine`` create form — compiles to a routine.v1 + a schedule.v1 job."""
    model_config = {"extra": "forbid"}
    subject: Optional[str] = None  # DERIVED from X-User-Id (P20); ignored if sent. Dropped client-side in Stage 4.
    name: str
    cron: str
    prompt: str
    run_now: bool = True  # fire one immediate run so the author sees a result without waiting for cron


class RoutineEnabledPatch(BaseModel):
    model_config = {"extra": "forbid"}
    enabled: bool


class WorkspaceSwapBody(BaseModel):
    """Attach a custom external git repo as the subject's workspace. Omit ``repo`` to swap back to seed."""
    model_config = {"extra": "forbid"}
    repo: Optional[str] = None   # git URL to clone (None → swap back to the seeded default)
    ref: Optional[str] = None    # branch/tag/sha to check out (defaults to main)
    slug: Optional[str] = None   # target a parked slot DIRECTLY (e.g. a no-repo backup) — restores, no re-clone
    fresh: bool = False          # swap-to-seed only: rebuild the default from template (start fresh) vs restore the park
    token: Optional[str] = None  # access token for a PRIVATE repo — used for the clone only, never stored (P15)


class WorkspacePublishBody(BaseModel):
    """Publish the subject's vexa-born workspace to GitHub — create the repo (unless ``remote_url``
    targets a pre-created one) and push the current branch's full history. ``token`` is the caller's
    PAT, used server-side for this call only, NEVER stored (P15)."""
    model_config = {"extra": "forbid"}
    repo_name: Optional[str] = None    # name of the repo to create (required unless remote_url is given)
    private: bool = True               # create the repo private (default) or public
    token: Optional[str] = None        # GitHub PAT (repo-creation + push); OPTIONAL — falls back to the caller's SAVED token
    org: Optional[str] = None          # create under this org instead of the user's account
    remote_url: Optional[str] = None   # skip creation and push to this (pre-created/empty) repo
    slug: Optional[str] = None         # target workspace (own slot or shared membership); omitted = the seed-slot workspace


class WorkspaceRenameBody(BaseModel):
    """Set a workspace slot's DISPLAY name (label only — the slug/parked dir are unchanged). Empty clears it."""
    model_config = {"extra": "forbid"}
    slug: str
    name: Optional[str] = None


class WorkspacePushBody(BaseModel):
    """Push a workspace's current branch to its GitHub home (origin / vexa-publish), fast-forward only.
    ``slug`` targets one of the caller's workspaces (default = the primary); ``token`` is the caller's PAT.
    OPTIONAL — when omitted, the caller's SAVED reusable GitHub token (git_credentials) is used. Whichever
    token applies is used for this push only and NEVER stored on the workspace remote (P15)."""
    model_config = {"extra": "forbid"}
    slug: Optional[str] = None
    token: Optional[str] = None


class GitTokenBody(BaseModel):
    """Save (or, with an empty/omitted ``token``, CLEAR) the caller's reusable GitHub token — stored ONCE,
    server-side, and reused as the fallback credential for every git op across all their repos."""
    model_config = {"extra": "forbid"}
    token: Optional[str] = None


class WorkspacePullBody(BaseModel):
    """Fetch + fast-forward a workspace from its GitHub home. ``slug`` targets one of the caller's
    workspaces (default = primary); ``token`` (optional — public repos need none) is used for the fetch
    only and NEVER stored (P15). A divergence is refused, not merged/rebased/forced."""
    model_config = {"extra": "forbid"}
    slug: Optional[str] = None
    token: Optional[str] = None


class WorkspacePurposeBody(BaseModel):
    """Set a workspace's PURPOSE — a one-line statement of what it's for, stored IN the workspace so it
    travels when shared and is read into the agent's mount preamble. ``slug`` targets one of the caller's
    workspaces (default = primary); an empty ``purpose`` clears it."""
    model_config = {"extra": "forbid"}
    slug: Optional[str] = None
    purpose: str = ""


class InviteCreateBody(BaseModel):
    """Mint a scoped invite for a shared workspace (owner/contributor only). Returns the token ONCE."""
    model_config = {"extra": "forbid"}
    workspace_id: str
    role: str = "viewer"                 # viewer | contributor (never owner)
    expires_in_sec: int = 604800         # 7 days
    max_uses: int = 1
    mode: str = "open"                   # open (anyone-with-link) | restricted (allowed_emails only)
    allowed_emails: Optional[list[str]] = None  # restricted mode: the verified emails permitted to redeem


class InviteAcceptBody(BaseModel):
    """Redeem an invite token (any logged-in user). Idempotent per user."""
    model_config = {"extra": "forbid"}
    token: str


class RoleSetBody(BaseModel):
    """Flip a member's role (owner only) — the "change read/write permissions" DoD item."""
    model_config = {"extra": "forbid"}
    role: str                            # viewer | contributor | owner


class SharedNewBody(BaseModel):
    """CREATE a new shared workspace (top-level, caller becomes owner) — the bootstrap that makes a
    workspace shareable so invites can be minted against it. ``name`` → display + workspace-id base."""
    model_config = {"extra": "forbid"}
    name: str = "Shared workspace"


class SharedAttachBody(BaseModel):
    """LOAD AN EXISTING REPO into a SHARED (group) workspace — the group counterpart of
    ``POST /api/workspace/swap``. The group's current tree is PARKED (kept, swappable-back), the repo is
    cloned in, and the member list is carried across so nobody loses access.

    ``token`` is the terminal's optional per-call PAT for an https repo; the MCP path never sends one —
    an ssh repo authenticates with the workspace's deploy key, resolved server-side."""
    model_config = {"extra": "forbid"}
    repo: Optional[str] = None   # git URL (ssh → deploy key; https → PAT). None + ``slug`` = swap back
    ref: Optional[str] = None    # branch/tag/sha (defaults to main)
    slug: Optional[str] = None   # a parked slot to restore DIRECTLY (no re-clone), e.g. "seed"
    token: Optional[str] = None  # https only, used for the clone and never stored (P15)


class SharedActiveBody(BaseModel):
    """Switch a shared workspace ON (mount) or OFF (hide) in the caller's active set — per-user, membership
    is unchanged."""
    model_config = {"extra": "forbid"}
    active: bool


class ArchiveBody(BaseModel):
    """Archive (collapse, keep) or un-archive one of the caller's own workspaces."""
    model_config = {"extra": "forbid"}
    archived: bool = True


class WorkspaceActivateBody(BaseModel):
    """ADD a workspace to the subject's active set (the additive mount set — WP-A2.1). Pass ``repo`` to
    clone/restore a git repo, or ``slug`` to activate an already-parked slot. Unlike swap it does NOT park
    the others — the private baseline and any other active workspaces stay mounted."""
    model_config = {"extra": "forbid"}
    repo: Optional[str] = None   # git URL to clone (first time) / restore (thereafter)
    ref: Optional[str] = None    # branch/tag/sha (defaults to main)
    slug: Optional[str] = None   # activate an already-parked slot directly (no repo needed)
    token: Optional[str] = None  # access token for a PRIVATE repo — clone only, never stored (P15)


class WorkspaceNewBody(BaseModel):
    """CREATE a brand-new BLANK workspace (seeded from the template) at a fresh slug and ADD it to the
    active set — the additive-model "new workspace" action. NOT a swap: nothing is parked/rebuilt/backed
    up. ``name`` (optional) → the new workspace's display label (default a unique "New workspace")."""
    model_config = {"extra": "forbid"}
    name: Optional[str] = None


class WorkspaceRemoveBody(BaseModel):
    """REMOVE one page from a workspace — the body behind `workspace_delete` (Vexa-ai/vexa#1621).

    ``path`` is workspace-RELATIVE; ``slug`` names the workspace (omitted = the caller's own desk /
    the chat's target).

    ⚠ WHY THIS IS A POST AND NOT `DELETE /api/workspace/file`, which is what it was written as
    first. `DELETE /api/workspace/{slug}` already exists and DESTROYS A WHOLE WORKSPACE
    irreversibly — and `{slug}` matches the literal segment `file`, so the two routes can match one
    URL and the answer would be decided by which router `create_app` includes first.
    `test_route_table.test_no_two_routes_can_match_the_same_url` caught it, which is the entire
    point of that gate: of all the pairs to leave to registration order, "remove one page" and
    "destroy the workspace" is the worst. A POST on its own literal path cannot be confused with
    anything, and it reads beside its sibling `POST /api/workspace/move`."""
    model_config = {"extra": "forbid"}
    path: str
    slug: Optional[str] = None


class WorkspaceMoveBody(BaseModel):
    """MOVE one page from one path to another — the body behind `workspace_move` (Vexa-ai/vexa#1621).

    ``from``/``to`` are workspace-RELATIVE paths. ``slug`` names the workspace the page is in today
    (omitted = the caller's own desk / the chat's target); ``to_slug`` names where it is going,
    and omitted means *the same workspace*, which is the ordinary rename.

    A CROSS-WORKSPACE MOVE IS A WRITE IN THE TARGET AND A DELETE IN THE SOURCE — two repositories,
    two commits, and either end being read-only refuses the whole call before anything is written.

    ``from`` is a Python keyword, so the field is ``from_`` with the alias the wire actually
    carries. ``populate_by_name`` keeps the Python spelling usable from a caller that builds the
    model directly (the tests do); the published OpenAPI property is ``from``, which is what a
    manifest-bound tool would name and what the rig sends."""
    model_config = {"extra": "forbid", "populate_by_name": True}
    from_: str = Field(alias="from")
    to: str
    slug: Optional[str] = None
    to_slug: Optional[str] = None


class WorkspaceDeactivateBody(BaseModel):
    """REMOVE a workspace from the active set (park it — never destroyed). The private baseline cannot be
    deactivated (it is the subject's durable memory root)."""
    model_config = {"extra": "forbid"}
    slug: str


def _encode_sse_cursor(last: dict, tkey: str) -> str:
    """The transcript stream's redis cursor as the SSE event id (the browser echoes it as
    Last-Event-ID on reconnect → we resume EXACTLY from here, gapless). '-' = not-yet-read.
    ONE part since PRD decision 34: the copilot out-stream and the processed-notes stream that
    used to occupy the other two are gone, and with them everything they carried."""
    return str(last.get(tkey, "-"))


def _decode_sse_cursor(raw: str | None) -> "str | None":
    """Last-Event-ID → the transcript stream id, or None when absent/malformed (fresh connect).
    Tolerates the retired multi-part form (``transcript|output|processed``) a client reconnecting
    across the deploy still holds: the first field was always the transcript cursor."""
    if not raw:
        return None
    head = raw.split("|")[0].strip()
    return head if head and head != "-" else None


MEETING_ARTIFACT_PREFIX = "meeting:"


def meeting_binding(ev: object) -> "tuple[str, str] | None":
    """``(row, native)`` this turn BOUND its chat to, or None (Vexa-ai/vexa#1597).

    ONE EVENT MEANS IT, and only one: the ``artifact`` carrying ``meeting:<row>``, which the harness
    emits for a successful ``bot_send`` and for nothing else (``llm/claude_code.py::_bot_artifact``).
    A bot went into a room BECAUSE this conversation asked for one, so this conversation is that
    meeting's chat — the founder's rule, and his words for the alternative: *"there is no need to
    create a new chat for that"*.

    NOT the ``open`` event, which carries the same ``meeting:<row>`` dialect. Opening a transcript is
    a person asking to LOOK at a meeting; it is not making one, and a chat that reads somebody's
    transcript must not take that meeting's identity.

    ``native`` may be empty — the send resolved a row either way, and the row is what every consumer
    addresses. Pure, and here rather than inline in the route because what an event MEANS is a
    contract with the worker, and a contract is worth a test."""
    if not isinstance(ev, dict) or ev.get("type") != "artifact":
        return None
    path = str(ev.get("path") or "").strip()
    if not path.startswith(MEETING_ARTIFACT_PREFIX):
        return None
    row = path[len(MEETING_ARTIFACT_PREFIX):].strip()
    # `meeting:` with nothing after it, or a row that is really a path, names no meeting. Same
    # refusal the client's own `pageForArtifact` makes, and for the same reason: a binding aimed at
    # a guess is worse than no binding — it puts a chat permanently in a room that does not exist.
    if not row or "/" in row:
        return None
    return row, str(ev.get("native") or "").strip()


def _is_slug(wid: str) -> bool:
    """A workspace slug is ONE path segment and never a dot-namespaced reserved one.

    ONE spelling, shared by everything that takes a slug off a stream or a request and stores it
    durably (``workspace_focus`` below, ``_Sessions.set_target``). A focus or a target aimed at a
    guess is worse than none: it survives into every later turn of the chat."""
    return bool(wid) and "/" not in wid and not wid.startswith(".")


def workspace_focus(ev: object) -> "str | None":
    """The workspace this turn brought INTO the chat's focus, or None (Vexa-ai/vexa#1603).

    ONE EVENT MEANS IT, and only one: the ``focus`` the harness emits for a successful
    ``workspace_new`` and for nothing else (``llm/claude_code.py::_workspace_focus``). A workspace
    made from a conversation is part of that conversation from that moment — the founder's rule,
    and his words for the alternative: *"not native workspace??"*.

    NOT the ``artifact`` a write into that workspace produces, and not ``open``: writing into a
    place, or reading one, is not joining it. A chat that reads a shared workspace's file must not
    thereby mount that workspace for every later turn.

    Pure, and here rather than inline in the route for the same reason ``meeting_binding`` is: what
    an event MEANS is a contract with the worker, and a contract is worth a test."""
    if not isinstance(ev, dict) or ev.get("type") != "focus":
        return None
    wid = str(ev.get("workspace") or "").strip()
    return wid if _is_slug(wid) else None


# ── THE TARGET WORKSPACE, SAID IN THE PROMPT (Vexa-ai/vexa#1611) ─────────────────────────────────
#
# The founder's own sentence for what the chat carries, and the answer to *"how to softly reinforce
# that?"* (#1603): it is CONTEXT, not a rule the person repeats. The turn is told which workspace it
# writes to and which it may only read — by NAME, never by slug (#1585/#1602) — and the tools'
# defaults (`entity_upsert`, `workspace_write`, and the cwd `Write` lands in) are pointed at the
# same place, so the sentence and the machinery cannot disagree.
#
# It is composed SERVER-SIDE, per turn, for the reason `chat_label` is: agent-api is the one thing
# every turn passes through — a person's message, a flow's kick, a routine's wake — and it is the
# only place that holds both the session record and the workspace registry. Composed here rather
# than stamped into the container, because a warm worker outlives a chip click.
TARGET_LINE = ("target workspace: {target} — writes go here unless asked otherwise; "
               "{others} are mounted to read; write there only on an explicit ask with its purpose.")
TARGET_LINE_ALONE = "target workspace: {target} — writes go here unless asked otherwise."

#: THE COMPANY LAYER SAYS WHAT IT IS FOR (Vexa-ai/vexa#1616). An admin may now aim any chat at
#: `_global`, and a turn told only "writes go here" would fill the organisation tier with meeting
#: notes — the one thing its own README says it is not for. One sentence, the seed's own words:
#: the five files stay thin, and the company-tier PAGES the graph links to live under
#: `kg/entities/` (the rule Vexa-ai/vexa#1589 settled when it stopped refusing entity writes here).
GLOBAL_TARGET_NOTE = ("The company layer is thin: the five files at its root, and company-tier "
                      "pages under `kg/entities/`. Meeting notes, customer records and personal "
                      "documents belong on a desk or in an ordinary workspace, never here.")


def target_preamble(target: str, others: "list[str] | None" = None, note: str = "") -> str:
    """The turn's target line, or ``""`` when there is no target to name.

    ``target`` and ``others`` are NAMES the caller already resolved — this function does no lookup,
    so what it renders is exactly what a test can state. An empty ``others`` drops the whole
    read-only clause rather than rendering an empty list: "nothing is mounted to read" is not a
    thing worth a sentence, and a sentence about an empty set reads as a defect.

    ``note`` is ONE SENTENCE about the target that a turn writing there has to know — today only
    the company layer has one (``GLOBAL_TARGET_NOTE``). It is a parameter rather than a branch
    because this function does no lookup by design: the caller knows which workspace it resolved,
    and a composer that stayed pure is a composer a test can state exactly."""
    name = str(target or "").strip()
    if not name:
        return ""
    rest = [str(o).strip() for o in (others or []) if str(o).strip() and str(o).strip() != name]
    line = (TARGET_LINE.format(target=name, others=", ".join(rest)) if rest
            else TARGET_LINE_ALONE.format(target=name))
    tail = f" {note.strip()}" if str(note or "").strip() else ""
    return f"## Where this turn writes\n\n{line}{tail}\n\n"


def _sse(events) -> Iterator[str]:
    for item in events:
        # ``None`` is the reader's idle tick → an SSE comment keepalive. Proxies (and the client's own
        # idle-stall detector) cut a byte-silent stream in ~18-30s; a long agent think is byte-silent for
        # minutes. The comment is invisible to EventSource parsing but keeps bytes flowing.
        if item is None:
            yield ": keepalive\n\n"
            continue
        # Each item is either a bare event dict, or (event, sse_id) — the id makes reconnects resumable.
        ev, sid = item if isinstance(item, tuple) else (item, None)
        prefix = f"id: {sid}\n" if sid else ""
        yield f"{prefix}data: {json.dumps(ev)}\n\n"


def _has_custom_model_endpoint(cfg: dict) -> bool:
    """True iff a per-user Settings → Models config actually delivers a credential to the worker.

    ONE spelling, shared with the dispatch overlay and the Test button (F93): this used to be a
    third hand-written copy of "is this custom", and the three disagreed — the Test button certified
    a configuration the turn would not use. It now delegates to `model_endpoint.has_custom_endpoint`
    so the pre-flight gate and the dispatch cannot answer differently."""
    return model_endpoint.has_custom_endpoint(cfg)


def _model_creds_error_message() -> str:
    keys = ", ".join(missing_capability_keys("model_inference"))
    return (
        "No model credentials are configured, so the agent cannot run. "
        f"Set one of {keys} in the deployment environment "
        "(deploy/compose/.env for the compose stack, then `make all`), "
        "or add a custom endpoint under Settings → Models."
    )


MEETING_CHAT_TRANSCRIPT_SEGMENTS = 400  # bound the live transcript folded into a meeting-chat prompt


def _fold_meeting_transcript(redis_url: "str | None", stream_key: str, *, limit: int) -> str:
    """Fold the live transcript Stream ``tc:meeting:{stream_key}`` — the SAME stream the terminal
    renders — into ordered ``speaker: text`` lines for chat
    grounding. ``stream_key`` is the meetings-domain ROW id (P0 cross-tenant leak fix: the carrier keys
    on the row id, never the native id which collides across tenants/re-sends). Refining live drafts are
    upserted by ``segment_id`` (latest text wins, no duplicate), arrival order preserved, bounded to the
    last ``limit`` segments. Best-effort: returns "" when redis is unwired or the stream is empty."""
    if not redis_url:
        return ""
    try:
        import redis

        r = redis.from_url(redis_url, decode_responses=True)
        rows = r.xrange(f"tc:meeting:{stream_key}")
    except Exception as exc:  # noqa: BLE001 — grounding is best-effort; never fail the chat turn
        logger.warning("could not read transcript for %s: %s", stream_key, exc)
        return ""
    order: list[str] = []
    seg_by_id: dict[str, dict] = {}
    for entry_id, fields in rows:
        payload = json.loads(fields.get("payload", "{}"))
        if payload.get("type") == "session_end":
            continue
        for i, seg in enumerate(payload.get("segments", [])):
            sid = str(seg.get("segment_id") or f"{entry_id}:{i}")
            if sid not in seg_by_id:
                order.append(sid)
            seg_by_id[sid] = seg
    lines: list[str] = []
    for sid in order[-limit:]:
        seg = seg_by_id[sid]
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        speaker = (seg.get("speaker") or "Speaker").strip()
        lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


def _meeting_grounding(
    active: "dict | None", session: str, prompt: str, redis_url: "str | None"
) -> "tuple[dict, list[str], str]":
    """Cookbook #1 — chat grounding in the terminal's ACTIVE meeting, branched by the meeting's
    LIFECYCLE PHASE (design-spec meeting-lifecycle-v2, W4; steering templates + the _global override
    live in control_plane/meeting_steering.py):

      prep (idle/scheduled)          — no transcript fold (none exists); steer toward preparation,
                                       naming the bound prep workspace when the client sent one.
      live (default; absent status)  — fold ``tc:meeting:{row}`` fresh on every turn — a legacy
                                       client that sends no status keeps exactly this behavior.
      post (completed/failed/stopped)— fold the recorded transcript ``tc:meeting:{row}``; if there is
                                       none say so plainly (fail loud, never fabricate).

    There is ONE transcript and one fold of it: the "processed notes" the post branch used to prefer
    were the in-product inference pipeline's output, and PRD decision 34 removed that pipeline.
    Returns the plain (none-context, no tools, prompt) when the active tab isn't a meeting."""
    a = active or {}
    if a.get("kind") != "meeting":
        return ({"kind": "none", "session": session}, [], prompt)
    m = a.get("meeting") or a  # tolerate {kind, meeting:{…}} or a flat {kind, platform, native_id}
    native = m.get("native_id") or m.get("ref")
    if not native:
        return ({"kind": "none", "session": session}, [], prompt)
    platform = m.get("platform") or "google_meet"
    # A chat turn (trigger "message"), not a live-meeting serve — the transcript travels in the prompt,
    # so the dispatch context stays plain (no meeting env / serve path is engaged for a chat).
    ctx = {"kind": "none", "session": session}
    # P0 (cross-tenant leak fix): read the streams by the meetings-domain ROW id (``meeting_id``),
    # which the terminal passes on the active meeting — the carriers key on it, never the native id
    # (which would fold a DIFFERENT tenant's / an older row's transcript into this user's chat).
    # Fall back to native only when the client didn't send a row id (legacy), documented as best-effort.
    stream_key = str(m.get("meeting_id") or native)
    status = str(m.get("status") or "").strip().lower()
    phase = meeting_steering.phase_for(status)
    fields = {
        "title": str(m.get("title") or "").strip() or str(native),
        "platform": platform,
        "native": str(native),
        "meeting_id": str(m.get("meeting_id") or native),   # the ROW id — the meeting's stable ?meeting= link
    }

    if phase == "prep":
        when = str(m.get("scheduled_at") or "").strip()
        fields["when"] = f", scheduled for {when}" if when else " (no time set yet)"
        workspace = str(m.get("workspace_id") or "").strip()
        fields["workspace"] = (
            f"The prep workspace bound to this meeting is \"{workspace}\" — ground your research and "
            f"write the brief there (its kg/ entities cover the attendees and companies). "
            if workspace
            else (
                "No shared prep workspace is bound to this meeting — the brief lives (or will live) "
                "as this meeting's note under kg/entities/meeting/ in the user's OWN workspace, "
                "reused across the meeting's series. "
            )
        )
        return (ctx, [], meeting_steering.render("prep", fields) + prompt)

    if phase == "post":
        fields["failed"] = " — the bot FAILED during this meeting" if status == "failed" else ""
        folded = _fold_meeting_transcript(redis_url, stream_key, limit=MEETING_CHAT_TRANSCRIPT_SEGMENTS)
        fields["source"] = "raw transcript"
        if not folded:
            return (ctx, [], meeting_steering.NO_RECORD_POST.format(**fields) + prompt)
        fields["transcript"] = folded
        return (ctx, [], meeting_steering.render("post", fields) + prompt)

    transcript = _fold_meeting_transcript(redis_url, stream_key, limit=MEETING_CHAT_TRANSCRIPT_SEGMENTS)
    if transcript:
        fields["transcript"] = transcript
        preamble = meeting_steering.render("live", fields)
    else:
        preamble = meeting_steering.NO_TRANSCRIPT_LIVE.format(platform=platform, native=native)
    return (ctx, [], preamble + prompt)


# The grounding/user-message boundary marker. Every server-folded context block (kg-links + mounts,
# added by the WORKER, land BEFORE the user prompt too; schedule digest; meeting/workspace grounding)
# sits BEFORE this sentinel; the user's actual words are AFTER it. The terminal strips everything up to
# and including it in ONE cut — robust to preamble wording drift, unlike per-block regexes. An HTML
# comment so the model treats it as inert. Old chats (no sentinel) fall back to the client's regexes.
CONTEXT_SENTINEL = "<!--vexa:user-input-below-->"


# ── terminal-state context bundle (slice 1) — the grounding orchestrator ────────────────────────────
# A chat turn's prompt is assembled [ambient <schedule> digest] + [focus fold] + user prompt.
#   ambient — the schedule digest, SURFACE-GATED (Meetings list / Today tab / meeting-ish tab focused)
#             with the user's explicit include.schedule toggle beating the gate either way.
#   focus   — meeting/prep (delegates to _meeting_grounding, ENRICHED with the server row so a cold
#             client store can't ground a planned meeting as live), workspace (purpose + README head),
#             today (the full-day digest REPLACES ambient), file/none (unchanged).
# Everything stays inside the trusted control plane and rides the prompt (P15); ctx stays "none".

_AMBIENT_TAB_KINDS = {"today", "meeting", "meetingPrep"}


def _ambient_gated(context: "ChatContextBody | None") -> bool:
    """Digest on/off: explicit ``include.schedule`` wins; absent → on iff the user is on a
    meetings-relevant surface. No context (legacy client) → off (old behavior)."""
    if context is None:
        return False
    include = context.include or {}
    if isinstance(include.get("schedule"), bool):
        return include["schedule"]
    surface = context.surface or {}
    if surface.get("list") == "meetings":
        return True
    tab = surface.get("tab") or {}
    if tab.get("kind") in _AMBIENT_TAB_KINDS:
        return True
    focus = context.focus or {}
    return focus.get("kind") == "today"


_WORKSPACE_README_LINES = 60
_WORKSPACE_README_CHARS = 3000


def _fold_workspace_grounding(mounts: "list", slug: str) -> str:
    """The workspace-focus preamble: purpose + README head for the mount matching ``slug``.
    FAIL-CLOSED: a slug outside the caller's active/shared mounts folds nothing — the mount set
    IS the authorization; we never read a workspace the turn couldn't see."""
    mount = next((m for m in mounts if getattr(m, "slug", None) == slug
                  or getattr(m, "workspace_id", None) == slug), None)
    if mount is None:
        return ""
    name = str(getattr(mount, "name", "") or slug)
    try:
        purpose = read_purpose(mount.path) or ""
    except Exception:  # noqa: BLE001
        purpose = ""
    purpose_part = f" Its purpose: {purpose.strip()}." if purpose.strip() else ""
    readme = ""
    try:
        text = (Path(mount.path) / "README.md").read_text(encoding="utf-8")
        readme = "\n".join(text.splitlines()[:_WORKSPACE_README_LINES])[:_WORKSPACE_README_CHARS]
    except OSError:
        readme = ""
    fields = {"name": name, "slug": slug, "purpose": purpose_part, "readme": readme}
    if not readme.strip():
        return meeting_steering.NO_README_WORKSPACE_FOCUS.format(**fields)
    return meeting_steering.render("workspace_focus", fields)


def _enriched_meeting_focus(focus: dict, rows: "list[dict]") -> dict:
    """Overlay the SERVER row's truth onto the client-sent meeting focus — status/title/
    scheduled_at/workspace_id come from the meetings domain when the row is found; the client's
    values remain only as the fallback (legacy clients / row not fetched)."""
    nid = focus.get("native_id") or focus.get("ref")
    row = schedule_digest_mod.find_row(
        rows, meeting_id=focus.get("meeting_id"), platform=focus.get("platform"), native_id=nid)
    if row is None and nid is not None:
        # The terminal's tab param is the ROW id for planned meetings without a link (native is
        # NULL there) — it rides in native_id, so retry it as the row id before giving up.
        row = schedule_digest_mod.find_row(rows, meeting_id=nid)
    if row is None:
        return focus
    data = row.get("data") or {}
    merged = dict(focus)
    merged["meeting_id"] = row.get("id", focus.get("meeting_id"))
    merged["status"] = row.get("status") or focus.get("status")
    if row.get("platform") and row.get("platform") != "unknown":
        merged["platform"] = row["platform"]
    if row.get("native_meeting_id"):
        merged["native_id"] = row["native_meeting_id"]
    for src_key, dst_key in (("title", "title"), ("scheduled_at", "scheduled_at"), ("workspace_id", "workspace_id")):
        if data.get(src_key):
            merged[dst_key] = data[src_key]
    return merged


def _context_grounding(
    body: "ChatBody", session: str, redis_url: "str | None", *,
    schedule_rows: "Callable[[], list[dict]]",
    workspace_mounts: "Callable[[], list]",
) -> "tuple[dict, list[str], str]":
    """Assemble the turn's grounding from the context bundle (or the legacy ``active``).
    ``schedule_rows`` / ``workspace_mounts`` are LAZY — fetched only for the branches that
    need them, and both degrade to empty on failure (a bundle must never fail the turn)."""
    prompt = body.prompt
    context = body.context
    focus = context.focus if context is not None else body.active
    ctx = {"kind": "none", "session": session}

    ambient = _ambient_gated(context)
    kind = (focus or {}).get("kind")
    need_rows = ambient or kind in ("meeting", "today")
    rows: "list[dict]" = []
    if need_rows:
        try:
            rows = schedule_rows() or []
        except Exception:  # noqa: BLE001 — best-effort by contract
            rows = []

    tz = context.tz if context is not None else None
    preamble = ""
    if kind == "today":
        digest = schedule_digest_mod.build_schedule_digest(rows, tz=tz, full_day=True)
        if digest:
            preamble = digest + meeting_steering.render("schedule", {})
        return (ctx, [], preamble + prompt)

    if ambient:
        digest = schedule_digest_mod.build_schedule_digest(rows, tz=tz)
        if digest:
            preamble = digest + meeting_steering.render("schedule", {})

    if kind == "meeting":
        enriched = _enriched_meeting_focus(dict(focus), rows) if rows else dict(focus)
        _c, _t, folded_prompt = _meeting_grounding(enriched, session, prompt, redis_url)
        return (_c, _t, preamble + folded_prompt if preamble else folded_prompt)

    if kind == "workspace" and (focus or {}).get("slug"):
        try:
            mounts = workspace_mounts() or []
        except Exception:  # noqa: BLE001
            mounts = []
        preamble += _fold_workspace_grounding(mounts, str(focus["slug"]))
        return (ctx, [], preamble + prompt)

    # file focus stays client-side-preambled; none/unknown kinds fold nothing extra
    return (ctx, [], preamble + prompt)


# ── SSE ownership gate (P0 cross-tenant leak fix — the SSE sibling of the by-id REST check) ──────────
# The live SSE feed `GET /api/meeting/stream` is keyed on a CALLER-SUPPLIED row id (`meeting_id`) and a
# `session_uid`. Row ids are sequential ints, so without an ownership check any authenticated user B could
# `EventSource(...?meeting_id=<A_row>&session_uid=<A_native>)` and stream tenant A's live transcript +
# an ACTIVE, enumerable cross-tenant read. We mirror the WS `/ws` pattern (gateway
# `authorize_subscribe` → `Meeting.user_id == user_id`) and the by-id REST path (`get_transcript_by_id`
# owner-scopes in SQL): verify the caller OWNS the row BEFORE opening the redis stream. Fail CLOSED.
#
# agent-api has no meetings DB; it asks meeting-api `GET /meetings/{meeting_id}` forwarding the
# gateway-injected `X-User-Id` (meeting-api's `_resolve_user_id` trusts it exactly as its by-id path does)
# — a row owned by another user (or absent) returns 404 there → we treat it as NOT-OWNED. The returned
# record's `native_meeting_id` also lets us confirm the requested `session_uid` belongs to the SAME owned
# meeting, so B can't pair its own row with A's native to sniff A's feed. Returns the owned
# meeting record (dict) on success, else None. Injectable so the L2 suite drives it over a fake.
# How room membership was derived, stamped on every room dispatch + log line so an audit can tell
# WHERE the room came from. Under the participant model it is the invite's addresses, resolved to
# subjects here — the ORDER comes from the transcript, the MEMBERSHIP never does.
_ROOM_SOURCE = "invite:participants→admin-api"


def _http_email_subject_lookup(admin_api_url: str, internal_secret: str, admin_token: str):
    """Build the participant ADDRESS → subject resolver: ``(address) -> str | None``.

    THE DOOR PROBLEM, stated because it constrains the whole feature. agent-api already holds an
    internal-tier seam to admin-api (``X-Internal-Secret``, used for the membership index + model
    config), but that tier exposes NO email lookup. The one route that answers this question,
    ``GET /admin/users/email/{email}``, is gated by ``verify_admin_token`` — a DIFFERENT and much
    broader credential (it can also create and patch users). So:

      * the narrow door is tried FIRST — ``GET /internal/users/by-email/{email}`` with the internal
        secret. It does not exist on admin-api today; this is the route that SHOULD be added
        (returning only ``{"id": ...}``), and when it is, the room works with no new credential and
        ``VEXA_ADMIN_API_TOKEN`` can be dropped. A 404 here marks it absent for the process lifetime
        so we probe once, not once per participant;
      * the wide door is used only when an operator has explicitly set ``VEXA_ADMIN_API_TOKEN``;
      * with neither, every lookup returns None — the room resolves to ZERO desks and says so. It
        never falls back to matching a person by name, which is the failure this design exists to
        avoid.

    Fail-CLOSED and quiet-per-call: any error is None (that participant is skipped), never an
    exception that could take down the turn."""
    import urllib.error
    import urllib.parse
    import urllib.request

    base = (admin_api_url or "").rstrip("/")
    state = {"internal_route": True}   # flipped off the first time the narrow door 404s

    def _get(url: str, headers: dict) -> "dict | None":
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:   # noqa: S310 — internal service URL
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode() or "null")

    def _lookup(address: str) -> "str | None":
        if not base or not address:
            return None
        quoted = urllib.parse.quote(str(address).strip().lower(), safe="")
        if state["internal_route"] and internal_secret:
            try:
                row = _get(f"{base}/internal/users/by-email/{quoted}",
                           {"X-Internal-Secret": internal_secret})
                if isinstance(row, dict) and row.get("id") is not None:
                    return str(row["id"])
                return None
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    # Ambiguous by design: 404 is both "no such user" and "no such route". Probe the
                    # ROUTE once — if the wide door is configured we can tell the difference by
                    # asking it; if it is not, treat the narrow door as present and this address as
                    # unknown (the safe reading — it skips a person rather than inventing one).
                    if not admin_token:
                        return None
                    state["internal_route"] = False
                else:
                    return None
            except Exception:  # noqa: BLE001 — unreachable admin-api → skip this participant
                return None
        if not admin_token:
            logger.warning("room: no email→subject resolver is configured (admin-api has no "
                           "internal by-email route and VEXA_ADMIN_API_TOKEN is unset) — the room "
                           "will mount ZERO desks")
            return None
        try:
            row = _get(f"{base}/admin/users/email/{quoted}", {"X-Admin-API-Key": admin_token})
        except Exception:  # noqa: BLE001 — 404 (no such user) and transport errors alike → skip
            return None
        return str(row["id"]) if isinstance(row, dict) and row.get("id") is not None else None

    return _lookup


def _http_meeting_owner_lookup(meeting_api_url: str):
    """Build the default owner-lookup: GET {meeting_api_url}/meetings/{id} with the caller's X-User-Id.
    Returns a callable ``(user_id: str, meeting_id: str) -> dict | None`` (the owned meeting record, or
    None when the row is absent / owned by someone else / meeting-api is unreachable — fail-closed)."""
    import urllib.error
    import urllib.request

    base = (meeting_api_url or "").rstrip("/")

    def _lookup(user_id: str, meeting_id: str) -> "dict | None":
        if not base or not user_id or not str(meeting_id).isdigit():
            return None  # non-numeric row id can't be an owned meeting row → fail closed
        try:
            req = urllib.request.Request(
                f"{base}/meetings/{int(meeting_id)}", headers={"X-User-Id": str(user_id)})
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status != 200:
                    return None
                return json.loads(resp.read().decode() or "null")
        except urllib.error.HTTPError:
            return None   # 404 (not owned / absent) or any other status → refuse
        except Exception:  # noqa: BLE001 — meeting-api unreachable → fail CLOSED, never open the stream
            return None

    return _lookup


