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


def dispatch_turn(uid: str, session: str, prompt: str) -> int:
    """Fire an agent turn; returns the history length BEFORE it (the collect baseline).
    /api/chat is an SSE STREAM that stays open for the whole turn — a client timeout while
    the stream runs is SUCCESS, not failure (the double-dispatch bug of 2026-08-23 evening)."""
    base = len(history(uid, session))
    try:
        http("POST", f"{AGENT_API}/api/chat", {"X-User-Id": uid},
             {"prompt": prompt, "session": session}, timeout=3)
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


def workspace_init(uid: str) -> None:
    http("POST", f"{AGENT_API}/api/workspace/init", {"X-User-Id": uid})


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
