"""engine.py — the GENERIC turn engine of the in-container agent harness.

Runs INSIDE a runtime-spawned, ISOLATED container (agents never run in the control plane). It reads
its dispatch from env — the mounted workspace, the minted token, ``REDIS_URL`` + the ``unit:<id>:in/out``
Stream topics, the ``start`` — runs the claude turn over the mounted ``/workspace`` via the SAME governed
``run_unit_turn`` (workspace.v1 revalidate + commit) the control plane proved, and ``XADD``s each
UnitEvent to its output Stream. Then it blocks on the input Stream for the next message (chat
continuity) until idle — TTL-on-idle by the harness. Continuity is the **session file** in the
workspace, so a reaped+respawned container resumes instantly.

The redis loop is factored into ``serve()`` with the turn-runner INJECTED, so it is offline-provable
with a fake redis + a fake turn (no docker, no claude).

This module holds the GENERIC engine; the MEETING copilot lives in ``worker.meeting``. ``worker.worker``
re-exports both so existing ``from worker.worker import X`` imports keep resolving.
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Iterator, Protocol

from shared.seeding import resolve_seed_dir, seed_workspace, validate_seed
from worker.decision_claude import run_unit_turn

log = logging.getLogger("agent_api.worker")

# Bootstrap memory root used ONLY when no valid workspace-seed template is available (tests / misconfig);
# the normal path seeds the full template (which carries its own CLAUDE.md + agents/ + views/).
_FALLBACK_CLAUDE_MD = (
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

# A turn-runner: given a prompt, yield the turn's UnitEvents (message-delta/tool-call/commit/...).
TurnFn = Callable[[str], Iterator[dict]]


class _Stream(Protocol):
    """The slice of redis the harness needs (XADD out, XREAD in) — a fake satisfies it in tests."""

    def xadd(self, name: str, fields: dict) -> str: ...
    def xread(self, streams: dict, count: int = 1, block: int | None = None) -> list: ...


# ── provider/token fail-loud (WS1) ────────────────────────────────────────────────────────────────
# A 401 from the model provider (e.g. an OpenRouter `sk-or-` token sent to api.anthropic.com, or vice
# versa) used to surface only as a GENERIC "Model inference failed" — the operator was never told the
# token and the endpoint disagreed. We detect that signature in the subprocess output / done reply and
# emit a DISTINCT `auth-error` event that names the provider host + the exact "check BASE_URL vs TOKEN"
# fix, plus a cheap boot preflight that catches an obviously-mismatched token/host pair before any call.

# Substrings that mark a provider authentication failure in claude-CLI / OpenRouter / Anthropic output.
_AUTH_SIGNATURE_RE = re.compile(
    r"\b401\b"
    r"|unauthorized"
    r"|invalid[ _-]*bearer"
    r"|invalid[ _-]*(?:x-)?api[ _-]*key"
    r"|authentication[ _-]*error"
    r"|no auth credentials"
    r"|user not found",  # OpenRouter's 401 body for a bad key
    re.IGNORECASE,
)


def looks_like_auth_failure(text: object) -> bool:
    """True if a blob of provider/CLI output carries an authentication-failure signature (401/Unauthorized/
    invalid bearer/invalid api key/authentication_error). Used to upgrade a generic model error into a
    distinct, actionable auth-error."""
    if not text:
        return False
    return bool(_AUTH_SIGNATURE_RE.search(str(text)))


def provider_host(base_url: str | None = None) -> str:
    """The host of ``ANTHROPIC_BASE_URL`` (the provider the token is being sent to), for the auth-error
    hint. Falls back to the raw value, then to ``"unknown"``."""
    raw = base_url if base_url is not None else os.environ.get("ANTHROPIC_BASE_URL", "")
    raw = (raw or "").strip()
    if not raw:
        return "unknown"
    from urllib.parse import urlparse

    host = urlparse(raw).netloc or urlparse(raw).path  # bare host (no scheme) lands in .path
    return host.strip("/") or raw


def _auth_error_event(detail: object, *, model: str | None, stage: str) -> dict:
    """A DISTINCT auth-error event (NOT the generic model-error): names the provider host and tells the
    operator to reconcile ANTHROPIC_BASE_URL with ANTHROPIC_AUTH_TOKEN — the token/endpoint mismatch that
    produces a silent 401."""
    host = provider_host()
    text = " ".join(str(detail or "provider rejected the token").split())
    return {
        "type": "auth-error",
        "error": {
            "stage": stage,
            "model": model or "",
            "provider_host": host,
            "hint": (
                f"provider {host} returned an auth failure (401) — token/endpoint mismatch; "
                "check ANTHROPIC_BASE_URL vs ANTHROPIC_AUTH_TOKEN (an sk-or- token must go to "
                "openrouter.ai, an sk-ant- token to api.anthropic.com)"
            ),
            "message": text[:600],
        },
    }


def preflight_provider_guard(
    *, base_url: str | None = None, token: str | None = None
) -> str | None:
    """Cheap boot guard: if the token PREFIX and the base_url HOST obviously disagree (an ``sk-or-``
    OpenRouter token pointed at ``api.anthropic.com``, or an ``sk-ant-`` Anthropic token pointed at an
    openrouter host), return a loud warning string (the caller logs it). Returns None when the pair is
    consistent or can't be judged (missing values, third-party host). Intentionally conservative — it
    only fires on a known-bad combination so it never nags on a legitimate custom gateway."""
    tok = (token if token is not None else os.environ.get("ANTHROPIC_AUTH_TOKEN", "")) or ""
    host = provider_host(base_url).lower()
    tok = tok.strip()
    is_openrouter_host = "openrouter.ai" in host
    is_anthropic_host = "api.anthropic.com" in host
    if tok.startswith("sk-or-") and is_anthropic_host:
        return (
            "PROVIDER MISMATCH: ANTHROPIC_AUTH_TOKEN looks like an OpenRouter key (sk-or-…) but "
            f"ANTHROPIC_BASE_URL points at {host} — this will 401. Point the base_url at openrouter.ai/api "
            "or supply an Anthropic (sk-ant-…) token."
        )
    if tok.startswith("sk-ant-") and is_openrouter_host:
        return (
            "PROVIDER MISMATCH: ANTHROPIC_AUTH_TOKEN looks like an Anthropic key (sk-ant-…) but "
            f"ANTHROPIC_BASE_URL points at {host} — this will 401. Point the base_url at api.anthropic.com "
            "or supply an OpenRouter (sk-or-…) token."
        )
    return None


# ── the claude turn over the mounted workspace (reuses the governed run_unit_turn) ───────────────

def _exec_claude(argv: list[str], cwd: str) -> Iterator[str]:
    proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    assert proc.stdout is not None
    try:
        yield from proc.stdout
    finally:
        proc.wait()


def _ensure_repo(work: Path) -> None:
    """First dispatch for a subject: seed the workspace from the VALIDATED workspace-seed template (the
    single seed primitive, ``shared.seeding.seed_workspace``) so the turn has a governance root + HEAD.
    Idempotent: an existing ``.git`` is left untouched. If no valid template is available (tests/misconfig),
    bootstrap a bare repo with a fallback CLAUDE.md so a turn still has its memory root."""
    if (work / ".git").exists():
        return
    seed_dir = resolve_seed_dir()              # registry root / default template (env override wins)
    problems = validate_seed(seed_dir)
    if problems:
        log.warning("workspace seed %s unavailable (%s) — bootstrapping a bare workspace",
                    seed_dir, "; ".join(problems))
        work.mkdir(parents=True, exist_ok=True)
        (work / "CLAUDE.md").write_text(_FALLBACK_CLAUDE_MD)
        seed_workspace(work, None)             # git init + commit over the fallback root
    else:
        seed_workspace(work, seed_dir)         # copy the validated template → git init → commit


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


def _link_skills_into_workspace(work: Path) -> None:
    """Expose the user's GOVERNED skills to claude. Skills live as VISIBLE, git-tracked files under the
    workspace's ``skills/<name>/SKILL.md`` (the ``skills/`` tree mirrors the ``agents/`` config home —
    not a dotfile, so it shows in the Files surface and is committed). claude auto-discovers skills from
    ``.claude/skills``, which is governance-excluded; so we point ``.claude/skills`` at the real
    ``skills/`` dir via a symlink. The real files stay durable + committed; claude finds them through the
    link. Idempotent: create ``skills/`` if absent, then (re)point a stale/wrong symlink — but never
    clobber a real ``.claude/skills`` directory."""
    skills = work / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    link = work / ".claude" / "skills"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        if link.is_symlink():
            if os.readlink(link) == str(skills):
                return
            link.unlink()
        elif link.exists():
            return  # a real dir already there — don't clobber
        link.symlink_to(skills, target_is_directory=True)
    except OSError:
        pass  # best-effort; the turn still works, just without workspace skills


DEFAULT_CHAT_SESSION = "main"


def _session_file(work: Path, session: str) -> Path:
    """The per-thread continuity file: ``work/.claude/sessions/<session>.session``. Multiple chat threads
    coexist in the ONE user workspace, each with its own claude ``--resume`` pointer. The default thread
    (``"main"``) transparently ADOPTS the legacy single-thread file (``.claude/.session``) on first read
    so the current conversation isn't lost when sessions go multi (migrate-on-read)."""
    sessions_dir = work / ".claude" / "sessions"
    namespaced = sessions_dir / f"{session}.session"
    if session == DEFAULT_CHAT_SESSION and not namespaced.exists():
        legacy = work / ".claude" / ".session"
        if legacy.exists():
            sessions_dir.mkdir(parents=True, exist_ok=True)
            namespaced.write_text(legacy.read_text())
    return namespaced


def _chat_resume_max_bytes() -> int:
    try:
        return int(os.environ.get("VEXA_CHAT_RESUME_MAX_BYTES", "1000000"))
    except ValueError:
        return 1000000


def _session_transcript_bytes(work: Path, session_id: str) -> int:
    total = 0
    for path in (work / ".claude" / "projects").glob(f"*/{session_id}.jsonl"):
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def _resume_id(work: Path, sess_file: Path) -> str | None:
    if not sess_file.exists():
        return None
    sid = sess_file.read_text().strip()
    limit = _chat_resume_max_bytes()
    if sid and limit > 0 and _session_transcript_bytes(work, sid) > limit:
        return None
    return sid or None


def run_turn_over_workspace(
    work: Path, prompt: str, *, model: str | None = None, allowed_tools: list[str] | None = None,
    commit: bool = True, session_continuity: bool = True, session: str = DEFAULT_CHAT_SESSION,
) -> Iterator[dict]:
    """One governed claude turn over the mounted workspace: resume from the session file, run
    ``run_unit_turn`` (which revalidates entity writes vs workspace.v1 and commits), and persist the
    captured session id. A stale ``--resume`` (the server session expired) retries fresh once.
    ``allowed_tools`` defaults to Read/Write/Edit; pass ``["Read"]`` for a propose-only (no-write) turn.
    ``session`` namespaces the continuity file so chat threads stay distinct (default ``"main"``)."""
    _ensure_repo(work)
    _link_chat_into_workspace(work)  # chats are saved to / resumed from the workspace, not ~/.claude
    _link_skills_into_workspace(work)  # governed skills/ → .claude/skills so claude auto-discovers them
    sess_file = _session_file(work, session)
    # session_continuity=False (the meeting copilot): never read/write the shared chat session — its
    # card-extraction beats must NOT pollute the user's chat conversation memory.
    resume = _resume_id(work, sess_file) if session_continuity else None
    allowed = allowed_tools or ["Read", "Write", "Edit"]
    # Resolve _exec_claude through the worker.worker shim at call time so a test patching
    # `worker.worker._exec_claude` reaches this call site (the harness was one module historically).
    import worker.worker as _w
    exec_claude = getattr(_w, "_exec_claude", _exec_claude)
    gen = run_unit_turn(str(work), prompt, exec_claude, allowed_tools=allowed, session=resume, model=model, commit=commit)
    first = next(gen, None)
    if resume and first is not None and first.get("type") == "done" and not first.get("ok", True):
        if sess_file.exists():
            sess_file.unlink()
        gen = run_unit_turn(str(work), prompt, exec_claude, allowed_tools=allowed, session=None, model=model, commit=commit)
        first = next(gen, None)
    captured: str | None = None
    for ev in (gen if first is None else itertools.chain([first], gen)):
        if ev.get("type") == "done" and ev.get("sessionId"):
            captured = ev["sessionId"]
        yield ev
    if captured and session_continuity:
        sess_file.parent.mkdir(parents=True, exist_ok=True)
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


def main() -> None:  # pragma: no cover — the container entrypoint (wired in tests via serve())
    import redis

    # Meeting entry functions imported function-locally to avoid an import cycle at module load
    # (worker.meeting imports the generic helpers from this module).
    from worker.meeting import (
        meeting_card_turn,
        meeting_doc_turn,
        serve_meeting,
        upsert_meeting_transcript_file,
    )
    from shared.agent_config import load_meeting_config

    work = Path(os.environ.get("VEXA_WORKSPACE_PATH", "/workspace"))
    model = os.environ.get("VEXA_AGENT_MODEL") or None
    # Boot preflight (WS1b): if the token prefix and the base_url host obviously disagree (sk-or- token →
    # api.anthropic.com, or sk-ant- token → openrouter), log a loud warning NOW — before the first call —
    # so a misconfigured provider pair is visible at container start, not only as a runtime 401.
    _warn = preflight_provider_guard()
    if _warn:
        log.warning("agent-api worker: %s", _warn)
    client = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    out_topic = os.environ["VEXA_UNIT_OUT_TOPIC"]
    idle_ms = int(os.environ.get("VEXA_IDLE_TIMEOUT_SEC", "120")) * 1000

    transcript_stream = os.environ.get("VEXA_TRANSCRIPT_STREAM")
    if transcript_stream:  # a live meeting dispatch — consume the transcript, emit cards
        # The GOVERNED, workspace-driven copilot config (agents/meeting.md) — loaded ONCE at meeting
        # start from the mounted workspace; absent ⇒ all defaults. Env stays the ultimate model default.
        cfg = load_meeting_config(work)
        # native id == the tail of the transcript stream (tc:meeting:<native>); meeting facts are in env.
        native = os.environ.get("VEXA_MEETING_ID") or transcript_stream.rsplit(":", 1)[-1]
        session_uid = os.environ.get("VEXA_MEETING_SESSION_UID") or native
        platform = os.environ.get("VEXA_MEETING_PLATFORM") or "google_meet"
        import datetime as _dt
        date = _dt.date.today().isoformat()
        title = f"Meeting {native}"
        # Auth-B/#3a: mirror each cleaned proc note into the per-meeting workspace file, incrementally,
        # so a chat agent focused on the meeting can `Read kg/entities/meeting/<native>.md` mid-meeting.
        meeting_file = work / "kg" / "entities" / "meeting" / f"{native}.md"
        meeting_meta = {
            "type": "meeting", "id": native, "title": title, "meeting_id": native,
            "session_uid": session_uid, "platform": platform, "date": date,
        }
        on_proc_note = lambda note: upsert_meeting_transcript_file(meeting_file, meeting_meta, note)  # noqa: E731
        # Deterministic dual-source render seam: persist the SAME notes/cards as the durable envelope
        # alongside the markdown, so live (redis) and finished (file) render identically.
        from worker.meeting import persist_envelope, _seed_dir, validate_envelope
        meeting_envelope_file = work / "kg" / "entities" / "meeting" / f"{native}.envelope.json"

        def on_envelope(envelope: dict) -> None:
            errors = validate_envelope(envelope, _seed_dir())
            if errors:
                log.warning("agent-api worker: meeting envelope schema errors: %s", "; ".join(errors[:3]))
            persist_envelope(meeting_envelope_file, envelope)
        # write_meeting_doc=false ⇒ no doc_turn (independent of `enabled`, which gates the live beats).
        doc_turn = None
        if cfg.write_meeting_doc:
            doc_turn = lambda cards: meeting_doc_turn(  # noqa: E731
                work, cards, native=native, meeting_id=native, session_uid=session_uid,
                platform=platform, date=date, title=title, model=cfg.model,
            )
        serve_meeting(
            client, transcript_stream=transcript_stream, out_topic=out_topic,
            card_turn=lambda segs: meeting_card_turn(
                work, segs, model=cfg.model, card_kinds=cfg.card_kinds, steering=cfg.steering,
                polish_rules=cfg.polish_rules, tag_rules=cfg.tag_rules,
            ),
            idle_ms=idle_ms, beat_segments=cfg.cadence_segments,
            doc_turn=doc_turn, enabled=cfg.enabled,
            start_id=os.environ.get("VEXA_TRANSCRIPT_START_ID", "0"),
            proc_stream=f"proc:meeting:{native}",
            cursor_key=f"proc:meeting:{native}:cursor",
            on_proc_note=on_proc_note,
            on_envelope=on_envelope,
        )
    else:  # chat / routine / event — run the entrypoint, then serve interactive messages
        # Research-capable toolset: WEB search/fetch + the workspace tools. Writes are still governed
        # (workspace.v1 revalidation + commit). Override with VEXA_CHAT_TOOLS (comma-separated).
        chat_tools = (os.environ.get("VEXA_CHAT_TOOLS")
                      or "Read,Write,Edit,Glob,Grep,Bash,WebSearch,WebFetch").split(",")
        session = os.environ.get("VEXA_CHAT_SESSION") or DEFAULT_CHAT_SESSION
        serve(
            client, out_topic=out_topic, in_topic=os.environ["VEXA_UNIT_IN_TOPIC"],
            turn=lambda prompt: run_turn_over_workspace(work, prompt, model=model, allowed_tools=chat_tools, session=session),
            start=json.loads(os.environ.get("VEXA_START", "{}")), idle_ms=idle_ms,
        )
