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

from control_plane import meeting_room
from control_plane import model_providers
from control_plane import meeting_steering
from control_plane import schedule_digest as schedule_digest_mod
from control_plane import unit_faults
from control_plane.config_preflight import NOT_CONFIGURED, capability_state, missing_capability_keys
from shared import unit_input, units
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


def inbox_pending(redis_url: "str | None", unit_id: str, key: str) -> list[dict]:
    """Everything submitted to this chat that its worker has not taken yet, oldest first.

    Only entries signed with the unit's input key (``key``, ``shared.unit_input``) are listed: the
    worker runs nothing else, so an unsigned or forged entry is not queued for anybody and must not
    show as if it were. No key lists nothing.

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
    # WHAT IS HOLDING THE QUEUE, when something is (P18). A spawn the runtime refused leaves its
    # typed fault on the unit (`unit_faults`); every row still waiting behind it says so, rather than
    # "queued behind the current turn" when no turn is coming. Evidence-based: the record stops
    # blocking the moment the worker takes anything.
    blocked = unit_faults.live(r, unit_id) if rows else None
    out: list[dict] = []
    now = time.time()
    for entry_id, fields in rows or []:
        if cursor and entry_id == cursor:
            continue                      # the entry the worker is on — taken, not queued
        msg = unit_input.verified_turn(key, fields or {})
        if msg is None:
            continue                      # unsigned, forged or another unit's: the worker refuses it
        meta = msg.get("inbox")
        if not isinstance(meta, dict):
            continue
        at = float(meta.get("at") or 0)
        if at and now - at > INBOX_PENDING_MAX_AGE_SEC:
            continue
        out.append({"entry": entry_id, "id": str(meta.get("id") or entry_id),
                    "kind": str(meta.get("kind") or ""), "target": str(meta.get("target") or ""),
                    "display": str(meta.get("display") or ""), "at": at,
                    **({"blocked": blocked} if blocked else {})})
    return out


#: How far past a client's cursor `turns_taken_after` looks. A chat writes a few hundred events per
#: turn at most; a client that has been away for longer than this has nothing exact to resume anyway.
TURNS_TAKEN_SCAN_MAX = 2000


#: How far back the turn-outcome reader looks on a unit's output Stream. A routine's turn is one of
#: the last few on its own unit; this bounds one read, not what a unit may hold.
TURN_OUTCOME_SCAN_MAX = 4000


def turn_outcome(redis_url: "str | None", unit_id: str, turn_id: str, after: str = "") -> dict:
    """HOW ONE TURN ENDED, read off its unit's output Stream (flows phase 0: a routine's run used to
    report `done` the moment its turn was POSTED, so a run whose every act was refused read as a
    success). Counted off the events the worker stamps with ``turn_id``:

        {"state": "unknown"}                          nothing for this turn yet (queued, or no redis)
        {"state": "running"}                          taken, not finished
        {"state": "done", "ok", "refused", "acted", "failed"}

    ``refused`` is the ``done`` frame's own ``{tool, reason, count}`` (``worker.engine`` stamps it);
    ``acted`` the tools whose results came back ok, ``failed`` the ones that did not. Pure reading,
    best-effort: an unreadable Stream is ``unknown``, never an answer about the turn.

    ``after`` — the unit's Stream tail the caller read just before it posted (`GET /api/chat/pending`
    answers it as `routine_cursor`). A worker names the turns it takes itself (a cold unit's first is
    `t0`), not by the id the poster gave, so with ``after`` the run's turn is the first one the worker
    accepted after that cursor (found on app.dev, 2026-10-14: every routine run read `unknown`)."""
    if not redis_url or not unit_id or not turn_id:
        return {"state": "unknown"}
    try:
        import redis

        r = redis.from_url(redis_url, decode_responses=True)
        if after:
            rows = r.xrange(units.output_topic(unit_id), min=f"({after}", max="+",
                            count=TURN_OUTCOME_SCAN_MAX)
        else:
            rows = list(reversed(r.xrevrange(units.output_topic(unit_id), max="+", min="-",
                                             count=TURN_OUTCOME_SCAN_MAX) or []))
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not read the output stream for unit=%s: %s", unit_id, exc)
        return {"state": "unknown"}
    parsed = []
    for _entry_id, fields in rows or []:
        try:
            ev = json.loads((fields or {}).get("event") or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(ev, dict):
            parsed.append(ev)
    want = turn_id
    if after and not any(str(e.get("turn_id") or "") == turn_id for e in parsed):
        first = next((e for e in parsed if e.get("type") == "turn-accepted"), None)
        want = str((first or {}).get("turn_id") or "")
        if first is not None:
            parsed = parsed[parsed.index(first):]
    events = [e for e in parsed if want and str(e.get("turn_id") or "") == want]
    if not events:
        return {"state": "unknown"}
    names = {str(e.get("callId") or ""): str(e.get("tool") or "")
             for e in events if e.get("type") == "tool-call"}
    done = next((e for e in reversed(events) if e.get("type") == "done"), None)
    if done is None:
        return {"state": "running"}
    acted, failed = [], []
    for e in events:
        if e.get("type") != "tool-result":
            continue
        tool = str(e.get("tool") or names.get(str(e.get("callId") or ""), ""))
        (acted if e.get("ok") else failed).append(tool)
    return {"state": "done", "ok": bool(done.get("ok", True)),
            "refused": done.get("refused") if isinstance(done.get("refused"), dict) else None,
            "acted": acted, "failed": failed}


def running_turn_from(redis_url: "str | None", unit_id: str, scan: int = TURNS_TAKEN_SCAN_MAX) -> "str | None":
    """WHERE A RUNNING TURN STARTS (founder 2026-10-16: a reload mid-reply dropped the streaming output).
    The Stream id just before the newest `turn-accepted` that has no `turn-complete` after it — a
    client with no cursor of its own attaches there and sees the partial reply, then the rest. None
    when no turn is running (or the Stream cannot be read); "0-0" when it is the first entry."""
    if not redis_url or not unit_id:
        return None
    try:
        import redis

        r = redis.from_url(redis_url, decode_responses=True)
        rows = r.xrevrange(units.output_topic(unit_id), max="+", min="-", count=scan) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not read the output stream for unit=%s: %s", unit_id, exc)
        return None
    completed: set = set()
    for i, (entry_id, fields) in enumerate(rows):
        try:
            ev = json.loads((fields or {}).get("event") or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(ev, dict):
            continue
        tid = str(ev.get("turn_id") or "")
        if ev.get("type") == "turn-complete":
            completed.add(tid)
            if not tid:
                return None          # a completion naming no turn ends them all
        elif ev.get("type") == "turn-accepted" and tid:
            if tid in completed:
                return None
            return rows[i + 1][0] if i + 1 < len(rows) else "0-0"
    return None


def turns_taken_after(redis_url: "str | None", unit_id: str, after: str) -> int:
    """How many turns this chat's worker has TAKEN since ``after`` — a cursor on the unit's output
    Stream that a client has read up to. Counted off the worker's own ``turn-accepted`` events.

    WHY THE PENDING LIST IS NOT ENOUGH (the founder, 2026-10-10: *"sometimes chat does not answer if
    asked while it's not yet answered"*). A message submitted while a turn runs leaves the inbox the
    moment the worker takes it — and the worker takes it within milliseconds of the turn in front
    finishing. A client whose view of that turn had just closed asked "is anything queued?", heard
    "no", and watched nothing: the answer was written to a Stream nobody was reading. "Pending"
    answers *what is waiting*; this answers *what started after the last thing I saw*, which is the
    question a client that just went idle actually has.

    Best-effort like the inbox reader: no redis, an unreadable one, or a malformed cursor count as
    zero — a chat that cannot tell watches what it would have watched before this existed."""
    if not redis_url or not after:
        return 0
    try:
        import redis

        r = redis.from_url(redis_url, decode_responses=True)
        rows = r.xrange(units.output_topic(unit_id), min=after, max="+", count=TURNS_TAKEN_SCAN_MAX)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not read the output stream for unit=%s: %s", unit_id, exc)
        return 0
    taken = 0
    for entry_id, fields in rows or []:
        if entry_id == after:
            continue                      # inclusive range (no Redis 6.2 dependency): drop the cursor
        try:
            ev = json.loads((fields or {}).get("event") or "{}")
        except (TypeError, ValueError):
            continue
        # A turn the WORKER started carries its `turn_id`. The injected-steering ack carries only a
        # nonce and starts nothing new, so it is not counted.
        if isinstance(ev, dict) and ev.get("type") == "turn-accepted" and ev.get("turn_id"):
            taken += 1
    return taken


def inbox_send_now(redis_url: "str | None", unit_id: str, key: str, item_id: str) -> str:
    """Name one QUEUED entry (by its client id) as the next to run; the entry id, or "" when it is
    not queued. The worker stops its running turn, runs that entry, and clears the key."""
    if not redis_url or not item_id or not key:
        return ""
    try:
        import redis

        r = redis.from_url(redis_url, decode_responses=True)
        topic = units.input_topic(unit_id)
        cursor = r.get(units.inbox_cursor_key(unit_id))
        rows = r.xrange(topic, min=cursor or "-", max="+", count=INBOX_PENDING_MAX_ROWS + 1)
        for entry_id, fields in rows or []:
            if cursor and entry_id == cursor:
                continue
            meta = (unit_input.verified_turn(key, fields or {}) or {}).get("inbox")
            if isinstance(meta, dict) and str(meta.get("id") or "") == item_id:
                r.set(units.send_now_key(unit_id), entry_id, ex=600)
                return entry_id
        return ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not mark %s to send now on unit=%s: %s", item_id, unit_id, exc)
        return ""


def stop_turns(redis_url: "str | None", unit_ids, *, session_id: str = "") -> int:
    """STOP THE RUNNING REPLY (live app.dev 2026-10-15): write the moment Stop was pressed to each
    unit's stop key, and to the unit holding ``session_id``'s head lease when there is one (a turn
    started under another mount generation, or by another participant of a shared chat, is the one
    holding the conversation). The worker's stop watcher kills that turn's whole process tree and
    the turn ends, said. The number of units signalled."""
    if not redis_url:
        return 0
    try:
        import redis
        from control_plane import session_sync  # noqa: PLC0415

        r = redis.from_url(redis_url, decode_responses=True)
        targets = {u for u in unit_ids if u}
        if session_id:
            holder = r.get(session_sync.lease_key(session_id))
            if holder:
                targets.add(str(holder))
        pressed = str(int(time.time() * 1000))
        for uid in targets:
            r.set(units.stop_key(uid), pressed, ex=600)
        return len(targets)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not signal stop for %s: %s", sorted(unit_ids), exc)
        return 0


def inbox_withdraw(redis_url: "str | None", unit_id: str, key: str, item_id: str) -> int:
    """Take back what is still QUEUED under the id its client gave it; the count withdrawn.

    WHAT MAKES A RETRY RUN ONCE (P18). A row a runtime fault blocked is still on the inbox, and the
    next worker that boots runs everything it finds there — so a retry that put a second copy beside
    it would answer the person twice. The client retries under the row's own id; this withdraws the
    held copy first. Only entries the worker has NOT taken (after its cursor) are touched: one it
    took is running, and withdrawing it would change nothing but the record. Best-effort, like the
    reader above — a failure here withdraws nothing and never fails the submission."""
    if not redis_url or not item_id or not key:
        return 0
    try:
        import redis

        r = redis.from_url(redis_url, decode_responses=True)
        topic = units.input_topic(unit_id)
        cursor = r.get(units.inbox_cursor_key(unit_id))
        rows = r.xrange(topic, min=cursor or "-", max="+", count=INBOX_PENDING_MAX_ROWS + 1)
        gone = 0
        for entry_id, fields in rows or []:
            if cursor and entry_id == cursor:
                continue
            msg = unit_input.verified_turn(key, fields or {})
            meta = (msg or {}).get("inbox")
            if isinstance(meta, dict) and str(meta.get("id") or "") == item_id:
                gone += int(r.xdel(topic, entry_id) or 0)
        return gone
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not withdraw %s from the inbox of unit=%s: %s", item_id, unit_id, exc)
        return 0


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


#: A CONCERN CHAT'S LIFECYCLE (ADR-0046 decision 12): opened by a person, a routine or an event
#: agent → active → waiting (on someone) → resolved → archived.
CONCERN_STAGES = ("opened", "active", "waiting", "resolved", "archived")


def _concern_read(raw) -> "dict | None":
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, dict) or raw.get("stage") not in CONCERN_STAGES:
        return None
    return {k: raw[k] for k in ("stage", "key", "flow", "since") if raw.get(k) is not None}


def _concern_of(new: dict, old) -> dict:
    """Merge a concern update into what is stored: the key and flow are kept once set; `stage` moves."""
    prev = _concern_read(old) or {}
    stage = new.get("stage") if new.get("stage") in CONCERN_STAGES else prev.get("stage", "opened")
    out = {**prev, **{k: v for k, v in new.items() if k in ("key", "flow") and v}, "stage": stage}
    if stage != prev.get("stage"):
        out["since"] = time.time()
    return out


def _routine_pair(raw) -> "dict | None":
    """A routine thread's marker — ``{flow, scope, version}`` — or None. Accepts the stored JSON."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, dict):
        return None
    flow, scope = str(raw.get("flow") or "").strip(), str(raw.get("scope") or "").strip()
    if not flow or not scope:
        return None
    try:
        version = int(raw.get("version") or 0)
    except (TypeError, ValueError):
        version = 0
    return {"flow": flow, "scope": scope, "version": version}


#: How long an output stream may sit on a non-final event before the thread stops counting as
#: busy — a worker that died mid-turn must not hold a routine off its thread forever.
TURN_STALE_SEC = 1800.0


def unit_busy(redis_url: "str | None", unit_id: str, key: str) -> bool:
    """IS THIS UNIT RUNNING OR HOLDING A TURN (ADR-0046 decision 12)? Something queued on its inbox,
    or its output stream's last event is not `turn-complete` and is recent. Best-effort: a redis
    that cannot answer reads as idle, which is the behaviour chats had before the check existed."""
    if inbox_pending(redis_url, unit_id, key):
        return True
    if not redis_url:
        return False
    try:
        import redis

        r = redis.from_url(redis_url, decode_responses=True)
        rows = r.xrevrange(units.output_topic(unit_id), count=1)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not read the output tail for unit=%s: %s", unit_id, exc)
        return False
    if not rows:
        return False
    entry_id, fields = rows[0]
    try:
        kind = json.loads((fields or {}).get("event") or "{}").get("type")
    except ValueError:
        kind = None
    if kind == "turn-complete":
        return False
    try:
        at = int(str(entry_id).split("-", 1)[0]) / 1000.0
    except ValueError:
        return False
    return time.time() - at < TURN_STALE_SEC


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
    changing it is the whole feature. ``set_target`` is its ONE writer.

    ``model`` — a model-catalog id, or absent for the person's default — is which of the operator's
    models this chat runs on. Not a latch either: picking another model is the feature.
    ``set_model`` is its ONE writer."""

    def __init__(self, redis_client=None) -> None:
        self._redis = redis_client
        self._mem: dict[str, dict[str, dict]] = {}  # subject → {session → {created,last_active,title}}
        self._marks: dict[str, dict[str, str]] = {}  # subject → {"p:<row>" | "h:<row>" → "1"}
        self._routes: dict[tuple, list] = {}          # (subject, session) → route receipts (QB-5)

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
               meeting_native: str | None = None, routine: "dict | None" = None,
               concern: "dict | None" = None) -> None:
        """Record the session on use: create it (stamping ``created`` + a default ``title``) or touch its
        ``last_active``. An explicit ``title`` overrides; otherwise the first prompt seeds it once.

        ``workspaces`` and ``scaffold`` are RESTATED, not merged: they describe what this chat is
        over right now, and a turn that knows them is the freshest answer there is. ``None`` means
        "this turn did not know", which is not the same as "there is none" — it leaves what is
        stored alone.

        ``touched`` only ever goes UP. It answers "has a person written here", and a machinery turn
        arriving after a human one does not un-write what they typed.

        ``meeting`` is written ONCE and never overwritten — see the class docstring. A send into a
        second meeting from the same chat leaves the first binding standing.

        ``routine`` (ADR-0046 decision 12) marks the thread as a routine's — ``{flow, scope,
        version}`` — so the rail can say so and link the flow's controls. Restated on every run, so
        a newer version of the flow is what the thread names."""
        now = self._now()
        routine_pair = _routine_pair(routine)
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
            if routine_pair is not None:
                fields["routine"] = json.dumps(routine_pair)
            if concern is not None:
                fields["concern"] = json.dumps(_concern_of(concern, existing.get("concern")))
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
        if routine_pair is not None:
            rec["routine"] = routine_pair
        if concern is not None:
            rec["concern"] = _concern_of(concern, rec.get("concern"))
        if bind and not str(rec.get("meeting") or "").strip():
            rec["meeting"] = bind
            if native:
                rec["meeting_native"] = native

    def meta(self, subject: str, session: str) -> dict:
        """The raw stored fields of one session — `{}` when there is none."""
        if self._redis is not None:
            return dict(self._redis.hgetall(self._meta_key(subject, session)) or {})
        return dict(self._mem.get(subject, {}).get(session) or {})

    #: How many route receipts one chat keeps (the fast intent router, QB-5): the newest last.
    ROUTES_KEPT = 50

    @staticmethod
    def _routes_key(subject: str, session: str) -> str:
        return f"agent:session-routes:{subject}:{session}"

    def record_route(self, subject: str, session: str, receipt: dict) -> None:
        """THE ROUTE RECEIPT OF ONE MESSAGE (QB-5): what the fast intent router decided — route,
        confidence, latency, source, and whether it ran the action or handed the message to the
        agent — kept beside the chat's record (the last :data:`ROUTES_KEPT`). A key of its own, never
        a field of the chat's meta: a receipt written before a chat's first turn must not make the
        chat look already created."""
        if self._redis is not None:
            k = self._routes_key(subject, session)
            self._redis.rpush(k, json.dumps(dict(receipt)))
            self._redis.ltrim(k, -self.ROUTES_KEPT, -1)
            return
        kept = self._routes.setdefault((subject, session), [])
        kept.append(dict(receipt))
        del kept[:-self.ROUTES_KEPT]

    def routes(self, subject: str, session: str) -> list:
        """The chat's route receipts, oldest first."""
        if self._redis is not None:
            out = []
            for raw in self._redis.lrange(self._routes_key(subject, session), 0, -1) or []:
                try:
                    out.append(json.loads(raw))
                except ValueError:
                    continue
            return out
        return list(self._routes.get((subject, session)) or [])

    def set_agent_chain(self, subject: str, session: str, chain: list) -> None:
        """THE ORIGIN CHAIN OF THE LAST AGENT MESSAGE DELIVERED INTO THIS THREAD (ADR-0046 decision
        13), stamped with when — a message sent FROM this thread carries it forward one hop."""
        value = json.dumps({"chain": [str(x) for x in chain][:64], "at": self._now()})
        if self._redis is not None:
            self._redis.hset(self._meta_key(subject, session), mapping={"agent_chain": value})
            return
        rec = self._mem.setdefault(subject, {}).get(session)
        if rec is not None:
            rec["agent_chain"] = value

    def name(self, subject: str, session: str, title: str, *, human: bool, turns: "int | None" = None) -> bool:
        """Persist a deliberate title without touching activity; human naming wins atomically.
        `turns` (an automatic title only) records how long the chat was when it was titled, with the
        row, so the one re-title after it grows survives a restart (`chat_autotitle`)."""
        if self._redis is not None:
            return bool(self._redis.eval("""
                if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
                if ARGV[2] == 'agent' and redis.call('HGET', KEYS[1], 'name_source') == 'human' then return 0 end
                redis.call('HSET', KEYS[1], 'named_title', ARGV[1], 'name_source', ARGV[2])
                if ARGV[3] ~= '' then redis.call('HSET', KEYS[1], 'auto_titled_turns', ARGV[3]) end
                return 1
            """, 1, self._meta_key(subject, session), title, 'human' if human else 'agent',
                '' if turns is None else str(int(turns))))
        rec = self._mem.get(subject, {}).get(session)
        if rec is None or (not human and rec.get('name_source') == 'human'):
            return False
        rec.update(named_title=title, name_source='human' if human else 'agent')
        if turns is not None:
            rec['auto_titled_turns'] = str(int(turns))
        return True

    def rail_order(self, subject: str, order=None) -> list[str]:
        if self._redis is not None:
            key = f'agent:rail-order:{subject}'
            if order is not None:
                self._redis.set(key, json.dumps(order))
            return json.loads(self._redis.get(key) or '[]')
        if not hasattr(self, '_orders'):
            self._orders = {}
        if order is not None:
            self._orders[subject] = list(order)
        return list(self._orders.get(subject, []))

    #: The most pages one person pins (#1783: pinned pages are the PERSON's, in every chat).
    PINNED_PAGES_CAP = 50

    def pinned_pages(self, subject: str, pages=None) -> list[dict]:
        """THIS PERSON'S PINNED PAGES, in their order: ``[{slug, path, label}]``. One list per person,
        so a page pinned in one chat is a tab in every chat. ``pages`` replaces the list."""
        if self._redis is not None:
            key = f'agent:pinned-pages:{subject}'
            if pages is not None:
                self._redis.set(key, json.dumps(list(pages)))
            try:
                return list(json.loads(self._redis.get(key) or '[]'))
            except ValueError:
                return []
        if not hasattr(self, '_pinned_pages'):
            self._pinned_pages = {}
        if pages is not None:
            self._pinned_pages[subject] = list(pages)
        return list(self._pinned_pages.get(subject, []))

    def appearance(self, subject: str, value=None) -> dict:
        """THIS PERSON'S APPEARANCE choice (Settings → Appearance): ``{accent?, font_ui?, …}`` — only
        what they picked; the company default fills the rest. ``value`` replaces it."""
        if self._redis is not None:
            key = f'agent:appearance:{subject}'
            if value is not None:
                self._redis.set(key, json.dumps(dict(value)))
            try:
                return dict(json.loads(self._redis.get(key) or '{}'))
            except ValueError:
                return {}
        if not hasattr(self, '_appearance'):
            self._appearance = {}
        if value is not None:
            self._appearance[subject] = dict(value)
        return dict(self._appearance.get(subject, {}))

    #: The most rows one person may mark. A mark is one hash field per flag, so this bounds the
    #: carrier the way ``ChatOrderBody`` bounds the order.
    RAIL_MARKS_CAP = 5000
    #: The two marks a rail row can carry, by their field prefix in the hash.
    _MARK_FLAGS = {"pinned": "p", "hidden": "h"}

    @staticmethod
    def _marks_key(subject: str) -> str:
        return f"agent:rail-marks:{subject}"

    def rail_marks(self, subject: str) -> dict:
        """THIS PERSON'S PINS AND HIDES on their own rail: ``{pinned: [row], hidden: [row]}``.

        A row is a rail key (``c:<chat session>`` or ``m:<meeting id>``). The marks are the
        viewer's and nobody else's: a pin on a meeting shared with you lives under YOUR subject, so
        the owner's rail never moves because you pinned or hid it."""
        if self._redis is not None:
            fields = self._redis.hgetall(self._marks_key(subject)) or {}
        else:
            fields = dict(self._marks.get(subject, {}))
        out: dict[str, list[str]] = {name: [] for name in self._MARK_FLAGS}
        for field in sorted(fields):
            flag, _, row = str(field).partition(":")
            for name, prefix in self._MARK_FLAGS.items():
                if flag == prefix and row:
                    out[name].append(row)
        return out

    def mark_row(self, subject: str, row: str, *, pinned: "bool | None" = None,
                 hidden: "bool | None" = None) -> dict:
        """Set or clear one row's marks and return the person's marks. ``None`` leaves a flag alone.

        One hash field PER FLAG (``p:<row>``, ``h:<row>``), so a pin and a hide written at the same
        moment from two windows cannot overwrite each other: neither write reads the other's field.
        Raises ``OverflowError`` when a new mark would pass ``RAIL_MARKS_CAP``."""
        wanted = {self._MARK_FLAGS[n]: v for n, v in (("pinned", pinned), ("hidden", hidden)) if v is not None}
        if self._redis is not None:
            key = self._marks_key(subject)
            adds = [f"{p}:{row}" for p, on in wanted.items() if on and not self._redis.hexists(key, f"{p}:{row}")]
            if adds and self._redis.hlen(key) + len(adds) > self.RAIL_MARKS_CAP:
                raise OverflowError("too many marked rows")
            for prefix, on in wanted.items():
                if on:
                    self._redis.hset(self._marks_key(subject), f"{prefix}:{row}", "1")
                else:
                    self._redis.hdel(self._marks_key(subject), f"{prefix}:{row}")
            return self.rail_marks(subject)
        mine = self._marks.setdefault(subject, {})
        adds = [f"{p}:{row}" for p, on in wanted.items() if on and f"{p}:{row}" not in mine]
        if adds and len(mine) + len(adds) > self.RAIL_MARKS_CAP:
            raise OverflowError("too many marked rows")
        for prefix, on in wanted.items():
            if on:
                mine[f"{prefix}:{row}"] = "1"
            else:
                mine.pop(f"{prefix}:{row}", None)
        return self.rail_marks(subject)

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
        # A CHAT WITH ITS OWN READS reads what joins it too (Vexa-ai/vexa#1783): a workspace made or
        # targeted from the conversation is mounted on its next turn either way.
        own = self.reads(subject, session)
        if own is not None and wid not in own:
            self.set_reads(subject, session, [*own, wid])
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

    def has_record(self, subject: str, session: str) -> bool:
        """Has this chat a record yet? A chat that has none is NEW: it takes the person's defaults
        (Vexa-ai/vexa#1783). One that has one keeps what it has."""
        if self._redis is not None:
            return bool(self._redis.exists(self._meta_key(subject, session)))
        return session in self._mem.get(subject, {})

    def set_target(self, subject: str, session: str, workspace: str, *, restart: bool = True) -> bool:
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
            if (meta.get("target") or "").strip() == wid and meta.get("no_target") != "1":
                return False
            fields = {"target": wid, "no_target": "", "last_active": str(self._now())}
            if restart:
                fields["mounts_stale"] = "1"
            self._redis.hset(mkey, mapping=fields)
            self._redis.sadd(self._ids_key(subject), session)
            return True
        rec = self._mem.setdefault(subject, {}).get(session)
        if rec is None:
            rec = {"created": self._now(), "last_active": self._now(), "title": session,
                   "touched": False}
            self._mem[subject][session] = rec
        if str(rec.get("target") or "").strip() == wid and not rec.get("no_target"):
            return False
        rec["target"] = wid
        rec["no_target"] = False
        if restart:
            rec["mounts_stale"] = True
        rec["last_active"] = self._now()
        return True

    #: The most workspaces one chat may name as its reads.
    READS_CAP = 200

    def set_reads(self, subject: str, session: str, reads: "list[str] | None") -> bool:
        """THE CHAT'S OWN READS (Vexa-ai/vexa#1783) — the workspaces this chat mounts — and whether
        that changed anything. THE ONE WRITER of ``reads``: the person's toggle and the agent's
        ``workspace_state_set`` both come through here.

        ``None`` clears it: the chat follows the person's default active set again, which is what
        every chat did before the field existed. A list is the chat's own (``"personal"`` is the
        person's desk); a malformed slug in it is dropped, never repaired. Raises the stale-mounts
        semaphore for the reason ``set_target`` does: the mount table is fixed at spawn."""
        if reads is None:
            value = ""
        else:
            clean: list[str] = []
            for w in reads:
                w = str(w or "").strip()
                if w and (w == "personal" or _is_slug(w)) and w not in clean:
                    clean.append(w)
            value = json.dumps(clean[: self.READS_CAP])
        if self._redis is not None:
            mkey = self._meta_key(subject, session)
            meta = self._redis.hgetall(mkey) or {}
            if (meta.get("reads") or "") == value:
                return False
            self._redis.hset(mkey, mapping={"reads": value, "mounts_stale": "1",
                                            "last_active": str(self._now())})
            self._redis.sadd(self._ids_key(subject), session)
            return True
        rec = self._mem.setdefault(subject, {}).get(session)
        if rec is None:
            rec = {"created": self._now(), "last_active": self._now(), "title": session,
                   "touched": False}
            self._mem[subject][session] = rec
        if (rec.get("reads") or "") == value:
            return False
        rec["reads"] = value
        rec["mounts_stale"] = True
        rec["last_active"] = self._now()
        return True

    def set_no_target(self, subject: str, session: str) -> bool:
        """THIS CHAT WRITES NOWHERE (Vexa-ai/vexa#1783: "no workspaces"). Cleared by ``set_target``
        — "write here" gives it a target again. Raises the stale-mounts semaphore."""
        if self._redis is not None:
            mkey = self._meta_key(subject, session)
            if (self._redis.hget(mkey, "no_target") or "") == "1":
                return False
            self._redis.hset(mkey, mapping={"no_target": "1", "target": "", "mounts_stale": "1",
                                            "last_active": str(self._now())})
            self._redis.sadd(self._ids_key(subject), session)
            return True
        rec = self._mem.setdefault(subject, {}).get(session)
        if rec is None:
            rec = {"created": self._now(), "last_active": self._now(), "title": session, "touched": False}
            self._mem[subject][session] = rec
        if rec.get("no_target"):
            return False
        rec.update({"no_target": True, "target": "", "mounts_stale": True, "last_active": self._now()})
        return True

    def set_profile(self, subject: str, session: str, name: str, applied: "dict | None" = None) -> None:
        """Which chat profile this chat uses (Vexa-ai/vexa#1783); ``incognito`` also marks it so no
        other chat reads it and chat search leaves it out. ``applied`` is the scope the profile put on
        the chat — what "Work (changed)" is measured against."""
        fields = {"profile": name, "incognito": "1" if name == "incognito" else "",
                  "profile_scope": json.dumps(applied or {}, sort_keys=True)}
        if self._redis is not None:
            self._redis.hset(self._meta_key(subject, session), mapping=fields)
            self._redis.sadd(self._ids_key(subject), session)
            return
        rec = self._mem.setdefault(subject, {}).setdefault(
            session, {"created": self._now(), "last_active": self._now(), "title": session, "touched": False})
        rec.update(fields)

    def profile_applied(self, subject: str, session: str) -> dict:
        """The scope this chat's profile put on it, as written by ``set_profile``."""
        if self._redis is not None:
            raw = self._redis.hget(self._meta_key(subject, session), "profile_scope") or ""
        else:
            raw = (self._mem.get(subject, {}).get(session) or {}).get("profile_scope") or ""
        try:
            val = json.loads(raw) if raw else {}
        except ValueError:
            val = {}
        return val if isinstance(val, dict) else {}

    def profile(self, subject: str, session: str) -> str:
        if self._redis is not None:
            return str(self._redis.hget(self._meta_key(subject, session), "profile") or "")
        return str((self._mem.get(subject, {}).get(session) or {}).get("profile") or "")

    def is_incognito(self, subject: str, session: str) -> bool:
        return self.profile(subject, session) == "incognito"

    def record_attachment(self, subject: str, session: str, path: str, ws: str) -> None:
        """WHERE an attachment of this chat was stored (Vexa-ai/vexa#1783): ``ws`` is the workspace
        slug, ``_system`` for an incognito chat, or ``""`` for the person's desk."""
        if not session or not path:
            return
        if self._redis is not None:
            mkey = self._meta_key(subject, session)
            try:
                cur = json.loads(self._redis.hget(mkey, "attachment_ws") or "{}")
            except ValueError:
                cur = {}
            cur = cur if isinstance(cur, dict) else {}
            cur[path] = ws
            self._redis.hset(mkey, "attachment_ws", json.dumps(cur))
            return
        rec = self._mem.setdefault(subject, {}).setdefault(
            session, {"created": self._now(), "last_active": self._now(), "title": session, "touched": False})
        rec.setdefault("attachment_ws", {})[path] = ws

    def attachments_of(self, subject: str, session: str) -> dict:
        """``{path: ws}`` for this chat's attachments, as ``record_attachment`` wrote them."""
        if self._redis is not None:
            try:
                val = json.loads(self._redis.hget(self._meta_key(subject, session), "attachment_ws") or "{}")
            except ValueError:
                val = {}
        else:
            val = (self._mem.get(subject, {}).get(session) or {}).get("attachment_ws") or {}
        return dict(val) if isinstance(val, dict) else {}

    def no_target(self, subject: str, session: str) -> bool:
        """Does this chat write nowhere?"""
        if self._redis is not None:
            return (self._redis.hget(self._meta_key(subject, session), "no_target") or "") == "1"
        return bool((self._mem.get(subject, {}).get(session) or {}).get("no_target"))

    def reads(self, subject: str, session: str) -> "list[str] | None":
        """This chat's own reads, or ``None`` — it follows the person's default active set."""
        if self._redis is not None:
            raw = (self._redis.hgetall(self._meta_key(subject, session)) or {}).get("reads") or ""
        else:
            raw = str((self._mem.get(subject, {}).get(session) or {}).get("reads") or "")
        if not raw:
            return None
        try:
            val = json.loads(raw)
        except ValueError:
            return None
        return [str(w) for w in val if str(w).strip()] if isinstance(val, list) else None

    def set_model(self, subject: str, session: str, model: str, effort: str = "", *,
                  restart: bool = True, level: Optional[int] = None) -> bool:
        """PIN this chat to one model of the operator's catalog, at one effort level, and say
        whether that changed anything. THE ONE WRITER of ``model`` and ``effort``.

        ``model`` is a catalog id the caller has already checked against the catalog and the
        person's access, or ``""`` — the person's default, which is what an unpicked chat runs on
        rather than a second name for it. ``effort`` is a level the caller has checked against that
        model, or ``""`` — the model's own default. They are written together because an effort
        means something only for the model it was picked for. A value that is not even shaped like
        an id or a level is REFUSED.

        IT RAISES THE STALE-MOUNTS SEMAPHORE, for the reason ``set_target`` does. The route is baked
        into the container at spawn — the worker reads its harness, endpoint, credential, model and
        effort from its environment, once — so a warm worker would keep answering on the old model
        for its whole window. A new unit id is the cure; ``take_mount_generation`` hands the next
        fresh turn one. A REAL CHANGE ONLY: re-picking what is in force costs nobody a cold start.

        ``restart=False`` HOLDS what the turn being dispatched runs on anyway (an unpicked chat
        taking the person's default at its turn): the worker about to start already has that
        route, so the semaphore stays down.

        ``level`` (1–5, hosted plans, ``routers/levels.py``) is the level the model was picked by;
        written with it, so a pick from the model picker (no level) clears it."""
        mid = str(model or "").strip()
        eff = str(effort or "").strip()
        lvl = str(int(level)) if level in (1, 2, 3, 4, 5) else ""
        if mid and not model_providers.is_model_id(mid):
            return False
        if eff and eff not in model_providers.EFFORTS:
            return False
        if self._redis is not None:
            mkey = self._meta_key(subject, session)
            meta = self._redis.hgetall(mkey) or {}
            if (meta.get("model") or "").strip() == mid and (meta.get("effort") or "").strip() == eff \
                    and (meta.get("level") or "") == lvl:
                return False
            fields = {"model": mid, "effort": eff, "level": lvl, "last_active": str(self._now())}
            if restart:
                fields["mounts_stale"] = "1"
            self._redis.hset(mkey, mapping=fields)
            self._redis.sadd(self._ids_key(subject), session)
            return True
        rec = self._mem.setdefault(subject, {}).get(session)
        if rec is None:
            rec = {"created": self._now(), "last_active": self._now(), "title": session,
                   "touched": False}
            self._mem[subject][session] = rec
        if str(rec.get("model") or "").strip() == mid and str(rec.get("effort") or "").strip() == eff \
                and str(rec.get("level") or "") == lvl:
            return False
        rec["model"] = mid
        rec["effort"] = eff
        rec["level"] = lvl
        if restart:
            rec["mounts_stale"] = True
        rec["last_active"] = self._now()
        return True

    @staticmethod
    def _pick_key(subject: str) -> str:
        return f"agent:model-pick:{subject}"

    @staticmethod
    def _meeting_chat_key(subject: str, row: str) -> str:
        return f"agent:meeting-chat:{subject}:{row}"

    def bind_meeting_chat(self, subject: str, row: str, session: str) -> None:
        """THE CHAT A MEETING WAS STARTED FROM IS THAT MEETING'S SESSION (founder, 2026-10-13): what
        addresses the meeting by its own name (``meet-<row>``) lands there. Per person; a meeting's
        own ``meet-<row>`` chat never binds to itself."""
        row, session = str(row or "").strip(), str(session or "").strip()
        if not row or not session or session.startswith("meet-"):
            return
        if self._redis is not None:
            self._redis.set(self._meeting_chat_key(subject, row), session)
            return
        if not hasattr(self, "_meeting_chats"):
            self._meeting_chats = {}
        self._meeting_chats[(subject, row)] = session

    def meeting_chat(self, subject: str, row: str) -> str:
        """The chat this person started meeting ``row`` from, or "" (it has its own chat)."""
        if self._redis is not None:
            try:
                return str(self._redis.get(self._meeting_chat_key(subject, str(row))) or "")
            except Exception:  # noqa: BLE001 — no answer: the meeting's own chat
                return ""
        return str(getattr(self, "_meeting_chats", {}).get((subject, str(row)), ""))

    def chat_for(self, subject: str, session: str) -> str:
        """``session`` as the chat it lands in: ``meet-<row>`` of a meeting this person started from a
        chat is that chat; anything else is itself."""
        s = str(session or "")
        if s.startswith("meet-"):
            return self.meeting_chat(subject, s[len("meet-"):]) or s
        return s

    def remember_pick(self, subject: str, model: str, effort: str = "") -> None:
        """THE PERSON'S LAST PICK — the model and effort they last chose in any chat, which every
        new chat of theirs starts on. Stored here, per person, on the server: it follows them
        across devices and browsers, and nobody else's chats ever read it. A value not shaped like
        a catalog id or a level is not stored; one the catalog later stops offering this person
        is skipped when it is read (``Catalog.default_for``), never refused."""
        mid = str(model or "").strip()
        eff = str(effort or "").strip()
        if not mid or not model_providers.is_model_id(mid) or (eff and eff not in model_providers.EFFORTS):
            return
        value = {"model": mid, "effort": eff, "at": self._now()}
        if self._redis is not None:
            self._redis.set(self._pick_key(subject), json.dumps(value))
            return
        if not hasattr(self, "_picks"):
            self._picks = {}
        self._picks[subject] = value

    def remembered_pick(self, subject: str) -> tuple[str, str]:
        """``(model, effort)`` of the person's last pick; ``("", "")`` when they never picked."""
        if self._redis is not None:
            try:
                value = json.loads(self._redis.get(self._pick_key(subject)) or "{}")
            except ValueError:
                value = {}
        else:
            value = dict(getattr(self, "_picks", {}).get(subject) or {})
        if not isinstance(value, dict):
            return "", ""
        return str(value.get("model") or "").strip(), str(value.get("effort") or "").strip()

    def model(self, subject: str, session: str) -> str:
        """This chat's own model pick (a catalog id), or ``""`` for the person's default."""
        if self._redis is not None:
            meta = self._redis.hgetall(self._meta_key(subject, session)) or {}
            return (meta.get("model") or "").strip()
        return str((self._mem.get(subject, {}).get(session) or {}).get("model") or "").strip()

    def level(self, subject: str, session: str) -> Optional[int]:
        """The level (1–5) this chat's model was picked by, or None (picked by model, or unpicked)."""
        if self._redis is not None:
            raw = ((self._redis.hgetall(self._meta_key(subject, session)) or {}).get("level") or "")
        else:
            raw = str((self._mem.get(subject, {}).get(session) or {}).get("level") or "")
        return int(raw) if raw in ("1", "2", "3", "4", "5") else None

    def effort(self, subject: str, session: str) -> str:
        """This chat's own effort pick, or ``""`` for the model's default."""
        if self._redis is not None:
            meta = self._redis.hgetall(self._meta_key(subject, session)) or {}
            return (meta.get("effort") or "").strip()
        return str((self._mem.get(subject, {}).get(session) or {}).get("effort") or "").strip()

    @staticmethod
    def _default_target_key(subject: str) -> str:
        return f"agent:default-target:{subject}"

    def default_target(self, subject: str) -> str:
        """THE PERSON'S DEFAULT TARGET — the workspace their new chats start writing to, or ``""``
        for Personal. Stored as set; the route that reads it re-checks it every time, so a stored
        value the person can no longer write is never handed out."""
        if self._redis is not None:
            return str(self._redis.get(self._default_target_key(subject)) or "").strip()
        return str(getattr(self, "_default_targets", {}).get(subject) or "").strip()

    def set_default_target(self, subject: str, workspace: str) -> None:
        """THE ONE WRITER of the default target. ``workspace`` was already checked writable by the
        caller; ``""`` clears it back to Personal. A value that is not a slug is not stored."""
        wid = str(workspace or "").strip()
        if wid and not _is_slug(wid):
            return
        if self._redis is not None:
            if wid:
                self._redis.set(self._default_target_key(subject), wid)
            else:
                self._redis.delete(self._default_target_key(subject))
            return
        if not hasattr(self, "_default_targets"):
            self._default_targets = {}
        self._default_targets[subject] = wid

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

    def settle_mounts(self, subject: str, session: str) -> None:
        """Lower the stale-mounts flag WITHOUT a new generation: the scope just written is the one the
        turn being dispatched already runs on (a new chat's default profile)."""
        if self._redis is not None:
            self._redis.hset(self._meta_key(subject, session), "mounts_stale", "0")
            return
        rec = self._mem.get(subject, {}).get(session)
        if rec is not None:
            rec["mounts_stale"] = False

    def mark_all_stale(self, subject: str) -> int:
        """A PERSON-WIDE workspace change (a hide, an unhide, new defaults — Vexa-ai/vexa#1783) makes
        every one of their chats' warm workers hold an out-of-date mount table. Raise the stale-mounts
        semaphore on each existing chat so its next fresh turn gets a container built from the
        current state; a hidden workspace then leaves the worker's disk on the next message, not in
        15 minutes. Returns how many chats were marked."""
        n = 0
        if self._redis is not None:
            for session in self._redis.smembers(self._ids_key(subject)) or set():
                mkey = self._meta_key(subject, session)
                if self._redis.exists(mkey):
                    self._redis.hset(mkey, "mounts_stale", "1")
                    n += 1
            return n
        for rec in self._mem.get(subject, {}).values():
            rec["mounts_stale"] = True
            n += 1
        return n

    def step_generation(self, subject: str, session: str) -> int:
        """Move this chat to a unit nobody has spawned yet, now: raise the stale-mounts flag and take
        it (``take_mount_generation``). For a turn whose chat runs on another model than its running
        worker was started on."""
        if self._redis is not None:
            self._redis.hset(self._meta_key(subject, session), mapping={"mounts_stale": "1"})
        else:
            self._mem.setdefault(subject, {}).setdefault(
                session, {"created": self._now(), "last_active": self._now(), "title": session})["mounts_stale"] = True
        return self.take_mount_generation(subject, session)

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
            sessions = list(self._redis.smembers(self._ids_key(subject)) or set())
            metas = self._hgetall_many([self._meta_key(subject, s) for s in sessions])
            for session, meta in zip(sessions, metas):
                meta = meta or {}
                try:
                    mounts = json.loads(meta.get("workspaces") or "[]")
                except ValueError:
                    mounts = []
                rows.append({
                    "session": session,
                    "title": meta.get("title") or session,
                    "named_title": meta.get("named_title"), "name_source": meta.get("name_source"),
                    "auto_titled_turns": int(meta.get("auto_titled_turns")) if str(meta.get("auto_titled_turns") or "").isdigit() else None,
                    "created": float(meta.get("created", 0) or 0),
                    "last_active": float(meta.get("last_active", 0) or 0),
                    "workspaces": [str(w) for w in mounts] if isinstance(mounts, list) else [],
                    # absent → the person's own desk. `null`, not `""`, so a client can tell "this
                    # server predates the field" from "this chat targets the desk" — they mean the
                    # same thing today and a client that guessed would be wrong the day they do not.
                    "target": (meta.get("target") or "").strip() or None,
                    # absent → the person's default model; `null` for the reason `target` is
                    "model": (meta.get("model") or "").strip() or None,
                    "scaffold": self._scaffold_pair(meta.get("scaffold")),
                    # absent → True: a row older than the field is a conversation that happened
                    "touched": meta.get("touched") != "0",
                    "meeting": (meta.get("meeting") or "").strip() or None,
                    "meeting_native": (meta.get("meeting_native") or "").strip() or None,
                    "mount_gen": self._as_gen(meta.get("mount_gen")),
                    "routine": _routine_pair(meta.get("routine")),
                    "concern": _concern_read(meta.get("concern")),
                })
        else:
            for session, meta in self._mem.get(subject, {}).items():
                rows.append({
                    "session": session, "title": meta.get("title") or session,
                    "named_title": meta.get("named_title"), "name_source": meta.get("name_source"),
                    "auto_titled_turns": int(meta.get("auto_titled_turns")) if str(meta.get("auto_titled_turns") or "").isdigit() else None,
                    "created": meta.get("created", 0.0), "last_active": meta.get("last_active", 0.0),
                    "workspaces": list(meta.get("workspaces") or []),
                    "target": str(meta.get("target") or "").strip() or None,
                    "model": str(meta.get("model") or "").strip() or None,
                    "scaffold": self._scaffold_pair(meta.get("scaffold")),
                    "touched": meta.get("touched", True) is not False,
                    "meeting": str(meta.get("meeting") or "").strip() or None,
                    "meeting_native": str(meta.get("meeting_native") or "").strip() or None,
                    "mount_gen": self._as_gen(meta.get("mount_gen")),
                    "routine": _routine_pair(meta.get("routine")),
                    "concern": _concern_read(meta.get("concern")),
                })
        rows.sort(key=lambda r: r["last_active"], reverse=True)
        return rows

    def targets(self, pairs) -> "dict[tuple[str, str], str]":
        """``target`` of many chats, any owners, in one round trip: ``{(subject, session): slug}``
        (the desktop asked once per chat it lists, 2026-10-18)."""
        pairs = list(dict.fromkeys((str(a), str(b)) for a, b in pairs))
        if self._redis is None:
            return {p: self.target(*p) for p in pairs}
        metas = self._hgetall_many([self._meta_key(a, b) for a, b in pairs])
        return {p: ((m or {}).get("target") or "").strip() for p, m in zip(pairs, metas)}

    def _hgetall_many(self, keys: "list[str]") -> list:
        """``HGETALL`` of every key, in order, in one round trip (the rail lists every chat's record:
        one round trip per chat was ~0.1 s of each listing on a long rail, 2026-10-18)."""
        pipe = getattr(self._redis, "pipeline", None)
        if pipe is None or not keys:
            return [self._redis.hgetall(k) for k in keys]
        p = pipe(transaction=False)
        for k in keys:
            p.hgetall(k)
        return p.execute()

    def profiles(self, subject: str, sessions: "list[str]") -> "dict[str, str]":
        """``profile`` of many chats in one round trip: ``{session: profile}``."""
        if self._redis is None:
            return {s: self.profile(subject, s) for s in sessions}
        pipe = getattr(self._redis, "pipeline", None)
        if pipe is None:
            return {s: self.profile(subject, s) for s in sessions}
        p = pipe(transaction=False)
        for s_ in sessions:
            p.hget(self._meta_key(subject, s_), "profile")
        return {s_: str(v or "") for s_, v in zip(sessions, p.execute())}

    def drop(self, subject: str, session: str) -> None:
        if self._redis is not None:
            self._redis.srem(self._ids_key(subject), session)
            self._redis.delete(self._meta_key(subject, session))
            self._redis.delete(self._routes_key(subject, session))
            return
        self._mem.get(subject, {}).pop(session, None)
        self._routes.pop((subject, session), None)


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
    emits for a successful ``request_meeting_bot`` and for nothing else (``llm/tool_events.py::_bot_artifact``).
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
    """A workspace slug, by THE workspace name rule (``workspace_paths.is_workspace_name``, its one
    owner): one path segment, never a dotname, never a separator.

    Used by everything here that takes a slug off a stream or a request and stores it durably
    (``workspace_focus`` below, ``_Sessions.set_target``). A focus or a target aimed at a guess is
    worse than none: it survives into every later turn of the chat."""
    return wpaths.is_workspace_name(wid)


def workspace_focus(ev: object) -> "str | None":
    """The workspace this turn brought INTO the chat's focus, or None (Vexa-ai/vexa#1603).

    ONE EVENT MEANS IT, and only one: the ``focus`` the harness emits for a successful
    ``workspace_new`` and for nothing else (``llm/tool_events.py::_workspace_focus``). A workspace
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


def qualify_artifact(ev: object, *, target: str = "", desk: str = "") -> "str | None":
    """Give a file ``artifact`` its qualified reference, ``[[page:<workspace>/<path>]]``, and return
    it (founder ruling 2026-10-18: *"we must require full paths to specific workspaces, we do not
    want to guess which readme this should open"*).

    The worker knows the path it wrote; a write that named no workspace (`workspace_write` with no
    `slug`) landed in the chat's ``target`` (`ceiling.write_slug`), else the person's ``desk`` — and
    those two only agent-api knows, on the way past. The desk aliases name the desk. A meeting
    artifact (`meeting:<row>`) is no file; it, and an event with a ``ref`` already, are left as they
    are. Nothing else in the event changes."""
    if not isinstance(ev, dict) or ev.get("type") != "artifact":
        return None
    if ev.get("ref"):
        return str(ev["ref"])
    path = str(ev.get("path") or "").strip()
    if not path or ":" in path.split("/", 1)[0]:
        return None
    ws = str(ev.get("workspace") or "").strip() or str(target or "").strip() or str(desk or "").strip()
    if ws in ("personal", "desk"):
        ws = str(desk or "").strip()
    if not ws:
        return None
    from workspaces.shared import links as _links  # noqa: PLC0415
    ev["ref"] = _links.page_ref(ws, path)
    return ev["ref"]


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
                      "documents belong in a personal workspace or an ordinary workspace, never here.")


def toolbelt_preamble(session: str) -> str:
    """The asks that name a TOOL, so they ride only a turn whose worker holds the toolbelt that
    serves them (the `worker_toolbelt` capability): name the chat, and read the clock.

    THE CLOCK IS A TOOL, NOT A GUESS. A conversation's timestamps are stale the moment they are
    written, and a model left to infer "today" from them answers relative dates wrong. On the
    dogfood rig the queue (`whats_waiting`) carried a fresh clock; on a standard deployment the
    queue is flows' and the clock is `current_time`, so the turn is told so on every turn."""
    return (f"Current chat session: {json.dumps(session)}. Once the task is clear, call chat_name "
            "with this session and a concise 3–7 word task title describing the actual objective "
            "(for example, ‘Connect personal calendar’). Do not copy the raw opening message, "
            "include secrets, or narrate naming. A human-chosen title is protected.\n"
            "For the date or time — now, today, anything relative — call `current_time`; never infer "
            "it from earlier messages. If it answers `timezone_required`, ask the person their "
            "timezone and save it with `timezone_set`.\n"
            "When the task says to notify, alert or tell the person about something (an important email, "
            "a finished job), call `notify` with one line naming it and this session; never paste content.\n")


def pinned_pages_preamble(pages: "list[dict]") -> str:
    """The person's pinned pages, by title and address only — never their content — so "update my
    unanswered page" names a page the agent can find. ``""`` when nothing is pinned."""
    rows = []
    for p in (pages or [])[:50]:
        path = str((p or {}).get("path") or "").strip()
        if not path:
            continue
        slug = str((p or {}).get("slug") or "").strip()
        label = str((p or {}).get("label") or path.rsplit("/", 1)[-1]).strip()
        rows.append(f"- {label} — `{(slug + ':') if slug else ''}{path}`")
    if not rows:
        return ""
    return "## The person's pinned pages\n\n" + "\n".join(rows) + "\n\n"


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
    ``active`` is the FLAT focus ``_readable_meeting_focus`` built — one level, scalar fields only.
    A ``meeting`` key inside it names nothing and is not read.

    Returns the plain (none-context, no tools, prompt) when the active tab isn't a meeting."""
    m = active or {}
    if m.get("kind") != "meeting":
        return ({"kind": "none", "session": session}, [], prompt)
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
    # NOFOLLOW: the README is folded into the turn's grounding, and the mount is a work tree the
    # model's tools can write — a link at `README.md` must not fold another file into the prompt.
    raw = wpaths.read_bytes_inside(Path(mount.path), "README.md")
    if raw is not None:
        text = raw.decode("utf-8", errors="replace")
        readme = "\n".join(text.splitlines()[:_WORKSPACE_README_LINES])[:_WORKSPACE_README_CHARS]
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


#: The api.v1 field meeting-api stamps when the owner deletes a meeting's transcript and recordings
#: (``MeetingResponse.data.artifact_deletion``, schema ``ArtifactDeletion``). Named once; the test of
#: ``transcript_erased`` reads the sealed contract's own goldens, so a reshape there fails it.
ARTIFACT_DELETION_FIELD = "artifact_deletion"


def transcript_erased(row: "dict | None") -> bool:
    """Whether the owner deleted (or is deleting) this meeting's transcript and recordings.

    meeting-api keeps the row after a typed delete — its title, times and status are history the
    owner asked to keep — and stamps api.v1's ``ArtifactDeletion`` on ``data.artifact_deletion``, in
    state ``pending`` while the delete runs and ``completed`` after. Every reader of the transcript
    treats that stamp, in either state, as "there is no transcript", whatever a cache may still hold."""
    data = row.get("data") if isinstance(row, dict) else None
    stamp = data.get(ARTIFACT_DELETION_FIELD) if isinstance(data, dict) else None
    return isinstance(stamp, dict) and bool(stamp)


# The fields a meeting focus may carry into the prompt. Each is a scalar; nothing nested is read.
_MEETING_FOCUS_FIELDS = ("meeting_id", "native_id", "platform", "status", "title", "scheduled_at",
                         "workspace_id")


def _meeting_focus_level(focus: dict) -> dict:
    """The ONE level of a client meeting focus that names the meeting: ``focus["meeting"]`` for the
    wrapped ``{kind, meeting: {…}}`` shape, else the focus itself. Selected once, here; nothing
    downstream unwraps again."""
    inner = focus.get("meeting")
    return inner if isinstance(inner, dict) else focus


def _focus_scalar(value) -> str:
    """A focus field as text, or "" for anything that is not a plain string or integer."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    return str(value).strip()


def _prep_focus(level: dict) -> dict:
    """A planned meeting's focus: the caller's own scalar fields, copied into a fresh flat dict."""
    out = {"kind": "meeting"}
    for key in _MEETING_FOCUS_FIELDS:
        value = _focus_scalar(level.get(key))
        if value:
            out[key] = value
    if "native_id" not in out and _focus_scalar(level.get("ref")):
        out["native_id"] = _focus_scalar(level.get("ref"))
    return out


def _focus_from_row(rid: str, row: dict) -> dict:
    """The focus of a CHECKED meeting, built from its server row alone: row id, native id, platform,
    status, and the title / time / bound workspace from ``row["data"]``. No client field is read."""
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    out = {"kind": "meeting", "meeting_id": rid,
           "status": _focus_scalar(row.get("status")).lower(),
           # A row with no native link (a planned meeting, an imported transcript) is named by its
           # row id — the same value the terminal puts in the tab.
           "native_id": _focus_scalar(row.get("native_meeting_id")) or rid}
    platform = _focus_scalar(row.get("platform"))
    if platform and platform != "unknown":
        out["platform"] = platform
    for key in ("title", "scheduled_at", "workspace_id"):
        value = _focus_scalar(data.get(key))
        if value:
            out[key] = value
    return out


def _readable_meeting_focus(
    level: dict, meeting_access: "Callable[[str], dict | None] | None",
) -> "dict | None":
    """The meeting focus a chat may ground on, or None when the caller may not read that meeting.

    ``level`` is the one level of the client focus that names the meeting
    (``_meeting_focus_level``). The focus is CLIENT-SENT, and the transcript fold reads
    ``tc:meeting:{row id}`` straight out of redis, so the row id must be checked before anything is
    folded — the same access decision the live stream makes (owner, transcript-share recipient, or
    member of the bound workspace; one union, evaluated by meeting-api). A caller who passes none of
    those gets no meeting grounding.

    The PREP phase reads nothing server-side (it renders the caller's own fields, overlaid only from
    the caller's own rows), so it passes through unchecked. Every other phase folds a transcript and
    is checked. FAIL CLOSED: no access check wired, a row id that is not a row id, a lookup that
    raises, or a meeting whose transcript its owner deleted all mean no grounding.

    Either way the result is a FRESH flat dict of scalar fields: for a checked meeting it is built
    from the server row alone (``_focus_from_row``), so the stream that is folded is exactly the row
    that was checked, and no client key reaches the fold."""
    status = _focus_scalar(level.get("status")).lower()
    if meeting_steering.phase_for(status) == "prep":
        return _prep_focus(level)
    rid = (_focus_scalar(level.get("meeting_id")) or _focus_scalar(level.get("native_id"))
           or _focus_scalar(level.get("ref")))
    if meeting_access is None or not rid.isdigit():
        return None
    try:
        row = meeting_access(rid)
    except Exception:  # noqa: BLE001 — an access check that cannot answer refuses
        logger.warning("meeting access check failed for row %s — no meeting grounding", rid)
        return None
    if not isinstance(row, dict) or transcript_erased(row):
        return None
    return _focus_from_row(rid, row)


def _context_grounding(
    body: "ChatBody", session: str, redis_url: "str | None", *,
    schedule_rows: "Callable[[], list[dict]]",
    workspace_mounts: "Callable[[], list]",
    meeting_access: "Callable[[str], dict | None] | None" = None,
) -> "tuple[dict, list[str], str]":
    """Assemble the turn's grounding from the context bundle (or the legacy ``active``).
    ``schedule_rows`` / ``workspace_mounts`` are LAZY — fetched only for the branches that
    need them, and both degrade to empty on failure (a bundle must never fail the turn).

    ``meeting_access(row_id) -> row | None`` is the caller's meeting access check. A meeting focus
    folds a transcript only for a row it approves; without one, no transcript is folded."""
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
        level = _meeting_focus_level(focus)
        enriched = _enriched_meeting_focus(dict(level), rows) if rows else dict(level)
        readable = _readable_meeting_focus(enriched, meeting_access)
        if readable is None:
            return (ctx, [], preamble + prompt)
        _c, _t, folded_prompt = _meeting_grounding(readable, session, prompt, redis_url)
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
