"""Agent-domain steps — agent-api HTTP. The conversation pattern is TWO steps so the worker is
restart-proof: dispatch records the history baseline in ITS receipt; collect waits (Wait, never
sleep) until the session history outgrows it."""
from __future__ import annotations

import time
import urllib.parse

from flows import Done, StepCtx, StepError, Wait

from .common import AGENT_API, http, require_internal_secret, scaffolded, ws_file


def history(uid: str, session: str) -> list:
    code, hist = http("GET", f"{AGENT_API}/api/sessions/{urllib.parse.quote(session)}/history",
                      {"X-User-Id": uid})
    if isinstance(hist, dict):
        hist = hist.get("turns", [])          # the endpoint wraps: {"turns": [...]}
    return hist if isinstance(hist, list) else []


def dispatch_turn(uid: str, session: str, prompt: str, room: dict | None = None) -> int:
    """Fire an agent turn; returns the history length BEFORE it (the collect baseline).
    /api/chat is an SSE STREAM that stays open for the whole turn — a client timeout while
    the stream runs is SUCCESS, not failure (the double-dispatch bug of 2026-08-23 evening).

    ``room`` opens THE MEETING ROOM: the post-meeting widening in which this turn may read the
    DESKS of the people who were in the meeting. Four fields go on the post, and the split between
    them is the whole safety property:

      ``meeting_id``      -> ``room_meeting_id``. The caller names ONLY THE MEETING, never a
                             workspace — a caller who could name workspaces could read anybody's
                             desk by naming it. Must be the meetings-domain ROW id.
      ``read``            -> ``room_participants``: the invite's ADDRESSES, in priority order. agent-api
                             resolves each through admin-api and mounts only those that already
                             have a subject AND a desk. **Addresses, not subject ids**: this side
                             deliberately does not resolve identity, so it cannot mint a ghost
                             account, and a person who is not a user is simply skipped there.
      ``names``           -> ``room_participant_names``: address -> the invite's ``CN=`` display name,
                             so the far side never has to guess a person from an email local part.
      ``read_max``        -> ``room_read_max``: the flow's cap, clamped to a server ceiling.

    THE INTERNAL-TIER HEADER IS PART OF THE ROOM, NOT AN EXTRA. agent-api refuses a room to any
    caller that cannot present ``X-Internal-Secret``, so it goes on the same post. Its value comes
    from the environment (``INTERNAL_API_SECRET``, a mode-600 file the lane's start script
    exports) and never from this repository; both entrypoints refuse to start without it, so by
    the time a turn is dispatched it exists.

    Omitted entirely when there is no room, so every other dispatch in this file sends exactly the
    body it has always sent."""
    base = len(history(uid, session))
    body = {"prompt": prompt, "session": session}
    headers = {"X-User-Id": uid}
    if room and room.get("meeting_id"):
        headers["X-Internal-Secret"] = require_internal_secret()
        body["room_meeting_id"] = str(room["meeting_id"])
        if room.get("read"):
            body["room_participants"] = [str(x) for x in room["read"]]
        if room.get("names"):
            body["room_participant_names"] = dict(room["names"])
        if room.get("read_max"):
            body["room_read_max"] = int(room["read_max"])
    try:
        http("POST", f"{AGENT_API}/api/chat", headers, body, timeout=3)
    except Exception:  # noqa: BLE001 — stream-open timeout: the turn IS running
        pass
    return base


def collect_reply(uid: str, session: str, baseline: int):
    hist = history(uid, session)
    if len(hist) > baseline and hist[-1].get("role") == "agent" and hist[-1].get("text"):
        return hist[-1]["text"].strip()
    return None


def collect_outbox(uid: str, session: str, sent_hash: str | None):
    """THE FILE-OUTBOX CONTRACT — harness-agnostic reply collection (codex serves some workers
    and its transcripts never reach the history endpoint): the agent WRITES its email reply to
    ``mail_outbox/<session>.md``; a content change vs the last-sent hash is a new reply."""
    import hashlib
    content = ws_file(uid, f"mail_outbox/{session}.md")
    if not content or not content.strip():
        return None, sent_hash
    h = hashlib.sha256(content.encode()).hexdigest()[:16]
    if h == sent_hash:
        return None, sent_hash
    return content.strip(), h


def workspace_init(uid: str) -> dict:
    """Seed THIS subject's workspace tiers — the personal baseline plus the private `_system`
    tier — as that subject. Idempotent by contract: an existing workspace is returned untouched,
    so it is safe on every login and on every re-run.

    A non-2xx RAISES. It used to be discarded, which made "the workspace was created" and "the
    call 404'd" the same observable event: the next step then wrote into, or read out of, a
    directory that is not a git repo, and the failure surfaced somewhere with no information
    about its cause."""
    code, body = http("POST", f"{AGENT_API}/api/workspace/init", {"X-User-Id": uid}, {})
    if not _ok(code):
        raise StepError(f"workspace init for {uid}: HTTP {code} — {str(body)[:200]}")
    return body if isinstance(body, dict) else {}


def head_sha(uid: str) -> str:
    """The current HEAD commit of ONE subject's desk, or "" if it has no repo (or cannot be read).

    Used as a BEFORE/AFTER witness, never as an assertion about content: a step that must not
    write anywhere proves it by showing the desk's history did not move. Degrading to "" is
    deliberate — a probe that cannot read must not be able to manufacture a difference, so a
    failed read compares equal to a failed read and the detector stays silent rather than
    failing a meeting on its own blind spot."""
    try:
        code, body = http("GET", f"{AGENT_API}/api/workspace/git", {"X-User-Id": uid}, None)
    except Exception:  # noqa: BLE001 — a probe never costs the caller its step
        return ""
    if not _ok(code) or not isinstance(body, dict):
        return ""
    commits = body.get("commits") or []
    if not commits or not isinstance(commits[0], dict):
        return ""
    return str(commits[0].get("sha") or "")


def head_subjects(uid: str, limit: int = 3) -> list:
    """The newest commit subjects on a desk — for naming, in a failure, exactly what landed."""
    try:
        code, body = http("GET", f"{AGENT_API}/api/workspace/git", {"X-User-Id": uid}, None)
    except Exception:  # noqa: BLE001
        return []
    if not _ok(code) or not isinstance(body, dict):
        return []
    out = []
    for c in (body.get("commits") or [])[:limit]:
        if isinstance(c, dict):
            out.append(f"{str(c.get('sha') or '')[:9]} {c.get('subject') or c.get('message') or ''}")
    return out


def workspace_write(uid: str, path: str, content: str) -> None:
    """WRITE one file into ONE subject's workspace, as that subject, COMMITTED.

    `PUT /api/workspace/file` is the door the terminal's own page editor uses: it writes the file,
    `git add`s it and commits, so history stays honest and nothing out here has to reach for a
    repository it does not own. Reusing it rather than adding a second write path is the same rule
    the mail directory learned — two mechanisms writing one surface is how they drift.

    NOTE ON THE COMMIT AUTHOR: that endpoint stamps `vexa-terminal <terminal@vexa.local>`, which
    is hardcoded in `core/agent/control_plane/api.py`. It is not in `_SYSTEM_AUTHOR_NAMES`, so the
    terminal shows these commits as a real author rather than as plumbing.

    A non-2xx RAISES: a write that silently did not land leaves the workspace disagreeing with a
    mail that has already gone out, and nobody would ever learn that from a return value."""
    code, body = http("PUT", f"{AGENT_API}/api/workspace/file", {"X-User-Id": uid},
                      {"path": path, "content": content})
    if not _ok(code):
        raise StepError(f"workspace write {path!r} for {uid}: HTTP {code} — {str(body)[:200]}")


def _ok(code) -> bool:
    try:
        return 200 <= int(code) < 300
    except (TypeError, ValueError):
        return False


# GONE WITH THE DESK WRITE THEY WATCHED FOR (founder decision 22, 2026-09-02):
# `latest_meeting_note(uid, baseline_shas)` and its `commit_shas(uid)` baseline. They detected the
# post-meeting turn's completion as a NEW COMMIT touching `kg/entities/meeting/` in the ORGANISER'S
# own repo. That run no longer writes into any desk — the canonical home of the note is the meeting
# row and its transcript store, and every attendee's desk (the organiser's included) receives the
# artefact afterwards, from `drop_to_attendees`. The commit will never happen, so a detector
# waiting for it would wait fifteen minutes and then fail every meeting.
#
# Deleted rather than left unused: they encode "the note is a commit in the organiser's desk",
# which is precisely the thing that stopped being true, and an unused detector is one somebody
# wires back up. Completion is now the agent's REPLY, grounded in the transcript — see
# `flows_defs/production.process_meeting`.
