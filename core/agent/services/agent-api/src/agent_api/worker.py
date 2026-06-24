"""worker.py — the in-container agent harness (the ``vexa-agent`` image entrypoint).

Runs INSIDE a runtime-spawned, ISOLATED container (agents never run in the control plane). It reads
its dispatch from env — the mounted workspace, the minted token, ``REDIS_URL`` + the ``unit:<id>:in/out``
Stream topics, the ``start`` — runs the claude turn over the mounted ``/workspace`` via the SAME governed
``run_unit_turn`` (workspace.v1 revalidate + commit) the control plane proved, and ``XADD``s each
UnitEvent to its output Stream. Then it blocks on the input Stream for the next message (chat
continuity) until idle — TTL-on-idle by the harness. Continuity is the **session file** in the
workspace, so a reaped+respawned container resumes instantly.

The redis loop is factored into ``serve()`` with the turn-runner INJECTED, so it is offline-provable
with a fake redis + a fake turn (no docker, no claude).
"""
from __future__ import annotations

import itertools
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Iterator, Protocol

from .decision_claude import run_unit_turn

# A turn-runner: given a prompt, yield the turn's UnitEvents (message-delta/tool-call/commit/...).
TurnFn = Callable[[str], Iterator[dict]]


class _Stream(Protocol):
    """The slice of redis the harness needs (XADD out, XREAD in) — a fake satisfies it in tests."""

    def xadd(self, name: str, fields: dict) -> str: ...
    def xread(self, streams: dict, count: int = 1, block: int | None = None) -> list: ...


# ── the claude turn over the mounted workspace (reuses the governed run_unit_turn) ───────────────

def _exec_claude(argv: list[str], cwd: str) -> Iterator[str]:
    proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    assert proc.stdout is not None
    try:
        yield from proc.stdout
    finally:
        proc.wait()


def _ensure_repo(work: Path) -> None:
    """The mounted workspace folder is a git repo (continuity + the governance undo). If the bind is
    empty (first dispatch for this subject), initialize it with a seed commit so ``run_unit_turn`` has a
    HEAD to commit onto. Idempotent."""
    work.mkdir(parents=True, exist_ok=True)
    if (work / ".git").exists():
        return
    for args in (
        ("init", "-q"),
        ("config", "user.email", "agent@vexa"),
        ("config", "user.name", "vexa-agent"),
    ):
        subprocess.run(["git", *args], cwd=str(work), check=True, capture_output=True, text=True)
    if not (work / "CLAUDE.md").exists():
        (work / "CLAUDE.md").write_text(
            "# Workspace — your durable memory\n\n"
            "This directory (your current working directory) is your ONLY durable memory, and it is a\n"
            "git repo that is committed automatically after every turn. Anything you should remember —\n"
            "facts about the user, knowledge, tasks, notes, decisions — MUST be saved as files here,\n"
            "under this workspace.\n\n"
            "- Save knowledge/notes as markdown files in this workspace (e.g. `notes/`, `kg/entities/`).\n"
            "- To recall something, READ the files in this workspace.\n"
            "- NEVER write memory to `~/.claude` or any path outside this workspace — that is ephemeral\n"
            "  and will be lost. Always use paths relative to this workspace directory.\n"
        )
    subprocess.run(["git", "add", "-A"], cwd=str(work), check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed", "--allow-empty"], cwd=str(work), check=True, capture_output=True, text=True)


def _link_chat_into_workspace(work: Path) -> None:
    """Save + resume chats FROM THE WORKSPACE. claude-code stores a conversation's transcript at
    ``~/.claude/projects/<cwd-slug>/<session>.jsonl`` — inside the container, so it is wiped when the
    per-turn container is recreated (no memory). Symlink that dir into the workspace's ``.claude/projects``
    so the chat is written to the durable git folder and ``--resume`` reads it back across turns. We keep
    it under ``.claude`` (excluded from the governance ``git clean``) so a rejected turn never wipes the
    history; it persists on the workspace volume."""
    ws_projects = work / ".claude" / "projects"
    ws_projects.mkdir(parents=True, exist_ok=True)
    home_claude = Path(os.environ.get("HOME", "/root")) / ".claude"
    home_claude.mkdir(parents=True, exist_ok=True)
    link = home_claude / "projects"
    try:
        if link.is_symlink():
            if os.readlink(link) == str(ws_projects):
                return
            link.unlink()
        elif link.exists():
            shutil.rmtree(link, ignore_errors=True)
        link.symlink_to(ws_projects, target_is_directory=True)
    except OSError:
        pass  # best-effort; a fresh turn still works, just without cross-turn resume


def run_turn_over_workspace(
    work: Path, prompt: str, *, model: str | None = None, allowed_tools: list[str] | None = None,
) -> Iterator[dict]:
    """One governed claude turn over the mounted workspace: resume from the session file, run
    ``run_unit_turn`` (which revalidates entity writes vs workspace.v1 and commits), and persist the
    captured session id. A stale ``--resume`` (the server session expired) retries fresh once.
    ``allowed_tools`` defaults to Read/Write/Edit; pass ``["Read"]`` for a propose-only (no-write) turn."""
    _ensure_repo(work)
    _link_chat_into_workspace(work)  # chats are saved to / resumed from the workspace, not ~/.claude
    sess_file = work / ".claude" / ".session"
    resume = sess_file.read_text().strip() if sess_file.exists() else None
    allowed = allowed_tools or ["Read", "Write", "Edit"]
    gen = run_unit_turn(str(work), prompt, _exec_claude, allowed_tools=allowed, session=resume, model=model)
    first = next(gen, None)
    if resume and first is not None and first.get("type") == "done" and not first.get("ok", True):
        if sess_file.exists():
            sess_file.unlink()
        gen = run_unit_turn(str(work), prompt, _exec_claude, allowed_tools=allowed, session=None, model=model)
        first = next(gen, None)
    captured: str | None = None
    for ev in (gen if first is None else itertools.chain([first], gen)):
        if ev.get("type") == "done" and ev.get("sessionId"):
            captured = ev["sessionId"]
        yield ev
    if captured:
        (work / ".claude").mkdir(parents=True, exist_ok=True)
        sess_file.write_text(captured)


def start_prompt(start: dict) -> str | None:
    """The first prompt from the dispatch ``start`` — an inline ask, a plan path, or None (session-only)."""
    ep = start.get("entrypoint") or {}
    if ep.get("inline"):
        return ep["inline"]
    if ep.get("path"):
        return f"Read and execute the plan at {ep['path']}."
    return None  # a session start serves the input Stream with no first prompt


# ── the harness loop (redis + the turn injected) ─────────────────────────────────────────────────

def serve(stream: _Stream, *, out_topic: str, in_topic: str, turn: TurnFn, start: dict, idle_ms: int) -> None:
    """Run the entrypoint turn (if any), then serve interactive messages on ``in_topic`` until idle.

    Each turn's UnitEvents are XADD'd to ``out_topic`` (tagged with a turn id), followed by a
    ``turn-complete`` marker. An empty blocking read (idle) returns — the process exits and the
    container is reaped (TTL-on-idle). A ``{"type":"stop"}`` message exits immediately.
    """
    def run_message(prompt: str, turn_id: str) -> None:
        for ev in turn(prompt):
            stream.xadd(out_topic, {"event": json.dumps({**ev, "turn_id": turn_id})})
        stream.xadd(out_topic, {"event": json.dumps({"type": "turn-complete", "turn_id": turn_id})})

    first = start_prompt(start)
    if first:
        run_message(first, "t0")

    last = "$"
    n = 0
    while True:
        resp = stream.xread({in_topic: last}, count=1, block=idle_ms)
        if not resp:
            return  # idle → exit 0 → container reaped
        for _name, entries in resp:
            for entry_id, fields in entries:
                last = entry_id
                msg = json.loads(fields.get("turn", "{}"))
                if msg.get("type") == "stop":
                    return
                n += 1
                run_message(msg.get("prompt", ""), f"t{n}")


# ── meeting mode: consume the transcript Stream, gate, emit proactive cards ───────────────────────

CARD_PROMPT = (
    "You are a live meeting copilot watching a conversation in real time. Here are the NEWEST "
    "transcript lines:\n\n{lines}\n\n"
    "Surface what a participant would want tracked from THESE lines: PEOPLE or companies introduced, "
    "TOPICS / decisions / notable claims discussed, and ACTION items or commitments. Be useful and "
    "reasonably generous — this is a live feed, not a summary. Respond with ONLY a JSON array (no prose, "
    "no markdown fence, and do NOT write any files), each element:\n"
    '  {{"kind": "person"|"topic"|"action", "title": "<short>", "body": "<one line>"}}\n'
    "Use [] only if these specific lines genuinely add nothing worth surfacing."
)


def parse_cards(reply: str | None) -> list[dict]:
    """Tolerantly pull the JSON card array out of the agent's reply (it may wrap it in prose/fences)."""
    if not reply:
        return []
    import re

    m = re.search(r"\[.*\]", reply, re.DOTALL)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return []
    return [c for c in arr if isinstance(c, dict) and c.get("title") and c.get("kind")]


def meeting_card_turn(work: Path, segments: list[dict], *, model: str | None = None) -> Iterator[dict]:
    """One copilot beat: read the recent transcript, stream the agent working, then emit a proactive
    card per salient finding. Reuses the governed turn (with session continuity across beats)."""
    lines = "\n".join(f"[{s.get('speaker', '?')}] {s.get('text', '')}" for s in segments)
    reply: str | None = None
    # propose-only: a read-only turn (no Write/Edit). We DON'T forward the streaming turn events — the
    # raw JSON reply would leak into the UI as a "note"; the meeting feed wants only the parsed cards.
    for ev in run_turn_over_workspace(work, CARD_PROMPT.format(lines=lines), model=model, allowed_tools=["Read"]):
        if ev.get("type") == "done":
            reply = ev.get("reply")
    for card in parse_cards(reply):
        yield {"type": "card", "card": card}


def serve_meeting(
    stream: _Stream, *, transcript_stream: str, out_topic: str,
    card_turn: Callable[[list[dict]], Iterator[dict]], idle_ms: int,
    beat_segments: int = 4,
) -> None:
    """Consume the meeting's ``transcript.v1`` Stream (the meetings⊥agent seam — read by schema), gate
    cheaply (a NEW speaker, or ``beat_segments`` completed segments), and run a copilot beat that XADDs
    proactive cards to ``out_topic``. The transcript keeps it non-idle; ``session_end`` (or an idle gap)
    reaps it."""
    seen_speakers: set[str] = set()
    buffer: list[dict] = []
    last = "0"  # read the whole transcript from the start (never miss early segments)
    n = 0
    while True:
        resp = stream.xread({transcript_stream: last}, count=50, block=idle_ms)
        if not resp:
            return  # transcript idle/ended → reap
        new_speaker = False
        for _name, entries in resp:
            for entry_id, fields in entries:
                last = entry_id
                payload = json.loads(fields.get("payload", "{}"))
                if payload.get("type") == "session_end":
                    if buffer:
                        _emit_beat(stream, out_topic, card_turn, list(buffer), n + 1)
                    return  # meeting ended → reap
                for seg in payload.get("segments", []):
                    if not seg.get("completed", True):
                        continue  # skip live drafts
                    buffer.append(seg)
                    sp = seg.get("speaker")
                    if sp and sp not in seen_speakers:
                        seen_speakers.add(sp)
                        new_speaker = True
        if buffer and (new_speaker or len(buffer) >= beat_segments):
            n += 1
            _emit_beat(stream, out_topic, card_turn, list(buffer), n)
            buffer = []


def _emit_beat(stream: _Stream, out_topic: str, card_turn, segments: list[dict], n: int) -> None:
    tid = f"beat{n}"
    for ev in card_turn(segments):
        stream.xadd(out_topic, {"event": json.dumps({**ev, "turn_id": tid})})
    stream.xadd(out_topic, {"event": json.dumps({"type": "turn-complete", "turn_id": tid})})


def main() -> None:  # pragma: no cover — the container entrypoint (wired in tests via serve())
    import redis

    work = Path(os.environ.get("VEXA_WORKSPACE_PATH", "/workspace"))
    model = os.environ.get("VEXA_AGENT_MODEL") or None
    # The live-meeting WATCHER runs a cheap, high-frequency card-extraction beat — pin it to Haiku 4.5
    # (override with VEXA_MEETING_MODEL). Chat/routine turns keep the default (VEXA_AGENT_MODEL).
    meeting_model = os.environ.get("VEXA_MEETING_MODEL") or "claude-haiku-4-5-20251001"
    client = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    out_topic = os.environ["VEXA_UNIT_OUT_TOPIC"]
    idle_ms = int(os.environ.get("VEXA_IDLE_TIMEOUT_SEC", "120")) * 1000

    transcript_stream = os.environ.get("VEXA_TRANSCRIPT_STREAM")
    if transcript_stream:  # a live meeting dispatch — consume the transcript, emit cards
        serve_meeting(
            client, transcript_stream=transcript_stream, out_topic=out_topic,
            card_turn=lambda segs: meeting_card_turn(work, segs, model=meeting_model), idle_ms=idle_ms,
        )
    else:  # chat / routine / event — run the entrypoint, then serve interactive messages
        # Research-capable toolset: WEB search/fetch + the workspace tools. Writes are still governed
        # (workspace.v1 revalidation + commit). Override with VEXA_CHAT_TOOLS (comma-separated).
        chat_tools = (os.environ.get("VEXA_CHAT_TOOLS")
                      or "Read,Write,Edit,Glob,Grep,Bash,WebSearch,WebFetch").split(",")
        serve(
            client, out_topic=out_topic, in_topic=os.environ["VEXA_UNIT_IN_TOPIC"],
            turn=lambda prompt: run_turn_over_workspace(work, prompt, model=model, allowed_tools=chat_tools),
            start=json.loads(os.environ.get("VEXA_START", "{}")), idle_ms=idle_ms,
        )


if __name__ == "__main__":  # pragma: no cover
    main()
