"""Agent-domain steps — agent-api HTTP. The conversation pattern is TWO steps so the worker is
restart-proof: dispatch records the history baseline in ITS receipt; collect waits (Wait, never
sleep) until the session history outgrows it."""
from __future__ import annotations

import time
import urllib.parse

from flows import Done, StepCtx, StepError, Wait

from .common import AGENT_API, http, scaffolded, ws_file


def history(uid: str, session: str) -> list:
    code, hist = http("GET", f"{AGENT_API}/api/sessions/{urllib.parse.quote(session)}/history",
                      {"X-User-Id": uid})
    if isinstance(hist, dict):
        hist = hist.get("turns", [])          # the endpoint wraps: {"turns": [...]}
    return hist if isinstance(hist, list) else []


def dispatch_turn(uid: str, session: str, prompt: str, room_read: list | None = None) -> int:
    """Fire an agent turn; returns the history length BEFORE it (the collect baseline).
    /api/chat is an SSE STREAM that stays open for the whole turn — a client timeout while
    the stream runs is SUCCESS, not failure (the double-dispatch bug of 2026-08-23 evening).

    ``room_read`` is a PROPOSAL, not an instruction: the ordered, capped list of the meeting's
    speakers whose workspaces this turn may read. agent-api verifies it against the meeting's real
    participants and mounts the intersection READ-ONLY, so what is sent here can only ever narrow
    that side's answer — a caller cannot widen its own access by naming somebody. Omitted entirely
    when empty, so every existing dispatch sends exactly the body it sent before."""
    base = len(history(uid, session))
    body = {"prompt": prompt, "session": session}
    if room_read:
        body["room_read"] = list(room_read)
    try:
        http("POST", f"{AGENT_API}/api/chat", {"X-User-Id": uid}, body, timeout=3)
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


def latest_meeting_note(uid: str, baseline_shas: list[str]):
    code, git = http("GET", f"{AGENT_API}/api/workspace/git", {"X-User-Id": uid})
    commits = git.get("commits", []) if isinstance(git, dict) else []
    for c in commits:
        if c.get("sha") in baseline_shas:
            continue
        files = c.get("files") or []
        for f in files:
            if f.startswith("kg/entities/meeting/"):
                return c["sha"][:9], f
    return None, None


def commit_shas(uid: str) -> list[str]:
    code, git = http("GET", f"{AGENT_API}/api/workspace/git", {"X-User-Id": uid})
    return [c["sha"] for c in (git.get("commits", []) if isinstance(git, dict) else [])]
