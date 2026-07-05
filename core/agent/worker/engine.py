"""engine.py — the GENERIC turn engine of the in-container agent harness.

Runs INSIDE a runtime-spawned, ISOLATED container (agents never run in the control plane). It reads
its dispatch from env — the mounted workspace, the minted token, ``REDIS_URL`` + the ``unit:<id>:in/out``
Stream topics, the ``start`` — runs the agent turn over the mounted ``/workspace`` via the
provider-agnostic ``llm`` ports (the HARNESS adapter is selected by ``VEXA_RUNNER``; this module
never names a vendor), and ``XADD``s each UnitEvent to its output Stream. Then it blocks on the
input Stream for the next message (chat continuity) until idle — TTL-on-idle by the harness.
Continuity is the **session file** in the workspace, so a reaped+respawned container resumes
instantly.

The redis loop is factored into ``serve()`` with the turn-runner INJECTED, and the harness itself
resolves through the ``worker.worker.harness_factory`` seam, so everything is offline-provable with
a fake redis + a fake harness (no docker, no CLI, no provider).

This module holds the GENERIC engine; the MEETING copilot lives in ``worker.meeting``. ``worker.worker``
re-exports both so existing ``from worker.worker import X`` imports keep resolving.
"""
from __future__ import annotations

import itertools
import json
import logging
import os
from pathlib import Path
from typing import Callable, Iterator, Protocol

from llm import (
    HarnessPort,
    auth_error_event,
    harness_from_env,
    looks_like_auth_failure,
    preflight_provider_guard,
    provider_host,
    run_harness_turn,
)
from llm.errors import _AUTH_SIGNATURE_RE  # noqa: F401 — re-exported for the worker.worker shim
from shared.seeding import resolve_seed_dir, seed_workspace, validate_seed

log = logging.getLogger("agent_api.worker")

# Back-compat aliases: these names predate the llm module split; the worker.worker shim (and
# meeting.py) re-export/import them under the old underscore names.
_auth_error_event = auth_error_event

# Bootstrap memory root used ONLY when no valid workspace-seed template is available (tests / misconfig);
# the normal path seeds the full template (which carries its own conventions file + agents/ + views/).
_FALLBACK_MEMORY_MD = (
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


# ── the agent turn over the mounted workspace (drives the llm HarnessPort) ────────────────────────

def _ensure_repo(work: Path) -> None:
    """First dispatch for a subject: seed the workspace from the VALIDATED workspace-seed template (the
    single seed primitive, ``shared.seeding.seed_workspace``) so the turn has a governance root + HEAD.
    Idempotent: an existing ``.git`` is left untouched. If no valid template is available (tests/misconfig),
    bootstrap a bare repo with a fallback conventions file so a turn still has its memory root."""
    if (work / ".git").exists():
        return
    seed_dir = resolve_seed_dir()              # registry root / default template (env override wins)
    problems = validate_seed(seed_dir)
    if problems:
        log.warning("workspace seed %s unavailable (%s) — bootstrapping a bare workspace",
                    seed_dir, "; ".join(problems))
        work.mkdir(parents=True, exist_ok=True)
        (work / "CLAUDE.md").write_text(_FALLBACK_MEMORY_MD)
        seed_workspace(work, None)             # git init + commit over the fallback root
    else:
        seed_workspace(work, seed_dir)         # copy the validated template → git init → commit


DEFAULT_CHAT_SESSION = "main"


def _session_file(work: Path, session: str) -> Path:
    """The per-thread continuity file: ``work/.claude/sessions/<session>.session``. Multiple chat threads
    coexist in the ONE user workspace, each with its own opaque resume pointer. The default thread
    (``"main"``) transparently ADOPTS the legacy single-thread file (``.claude/.session``) on first read
    so the current conversation isn't lost when sessions go multi (migrate-on-read).

    ``.claude/`` here is the FROZEN on-disk continuity-store path (workspace_reader serves chat
    history from it) — a path contract, not a vendor coupling."""
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


def _resume_id(work: Path, sess_file: Path, harness: HarnessPort) -> str | None:
    """The session id to resume, or None. The id is an OPAQUE per-harness token; the harness also
    accounts the stored transcript size behind it so an over-budget resume restarts fresh."""
    if not sess_file.exists():
        return None
    sid = sess_file.read_text().strip()
    limit = _chat_resume_max_bytes()
    if sid and limit > 0 and harness.transcript_bytes(work, sid) > limit:
        return None
    return sid or None


def run_turn_over_workspace(
    work: Path, prompt: str, *, model: str | None = None, allowed_tools: list[str] | None = None,
    commit: bool = True, session_continuity: bool = True, session: str = DEFAULT_CHAT_SESSION,
) -> Iterator[dict]:
    """One governed agent turn over the mounted workspace: resume from the session file, drive
    ``run_harness_turn`` (which commits the tree when it changed), and persist the captured session
    id. A stale resume (the harness session expired) retries fresh once.
    ``allowed_tools`` defaults to Read/Write/Edit; pass ``["Read"]`` for a propose-only (no-write) turn.
    ``session`` namespaces the continuity file so chat threads stay distinct (default ``"main"``)."""
    _ensure_repo(work)
    # Resolve the harness through the worker.worker seam at call time so a test patching
    # `worker.worker.harness_factory` reaches this call site (the harness was one module historically).
    import worker.worker as _w
    factory = getattr(_w, "harness_factory", harness_from_env)
    harness: HarnessPort = factory()
    harness.prepare(work)  # harness-specific continuity/skills wiring (durable, workspace-rooted)
    sess_file = _session_file(work, session)
    # session_continuity=False (the meeting copilot): never read/write the shared chat session — its
    # card-extraction beats must NOT pollute the user's chat conversation memory.
    resume = _resume_id(work, sess_file, harness) if session_continuity else None
    allowed = allowed_tools or ["Read", "Write", "Edit"]
    gen = run_harness_turn(work, prompt, harness, allowed_tools=allowed, session=resume, model=model, commit=commit)
    first = next(gen, None)
    if resume and first is not None and first.get("type") == "done" and not first.get("ok", True):
        if sess_file.exists():
            sess_file.unlink()
        gen = run_harness_turn(work, prompt, harness, allowed_tools=allowed, session=None, model=model, commit=commit)
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
    # Boot preflight (WS1b): if a credential prefix and its base-url host obviously disagree, log a
    # loud warning NOW — before the first call — so a misconfigured provider pair is visible at
    # container start, not only as a runtime 401. Judges the completion pair, then the harness pair.
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
            # The processed-notes stream is keyed by the meetings-domain ROW id when the dispatch
            # carries it (VEXA_MEETING_NUMERIC_ID): the row id is unique per meeting run, so a
            # re-sent bot on the same native link can never mix/clobber a previous meeting's
            # processed doc — and the meeting-api db-writer (which knows its own row ids) drains
            # proc:meeting:{numeric} into the meeting row's data JSONB (durable). Native-keyed
            # fallback for older dispatchers that don't pass the row id. The CURSOR stays keyed by
            # the NATIVE id: it is a position in tc:meeting:{native}, which re-sends share — the
            # gap-fill must resume from where the previous run left off, whichever row that was.
            proc_stream=f"proc:meeting:{os.environ.get('VEXA_MEETING_NUMERIC_ID') or native}",
            cursor_key=f"proc:meeting:{native}:cursor",
            on_proc_note=on_proc_note,
            on_envelope=on_envelope,
            # Provenance stamped on every processed-notes entry: what pipeline/provider/model
            # produced this cleaned view — persisted verbatim into the durable view's `params`
            # (meeting.data processed views) by the meeting-api db-writer (reproducibility).
            proc_params={
                "pipeline": "meeting-copilot/proc-notes", "version": 1,
                "provider": os.environ.get("VEXA_LLM_PROVIDER"),
                "model": cfg.model or os.environ.get("VEXA_LLM_MODEL"),
            },
        )
    else:  # chat / routine / event — run the entrypoint, then serve interactive messages
        # Research-capable toolset: WEB search/fetch + the workspace tools. Writes are committed by
        # run_harness_turn. Override with VEXA_CHAT_TOOLS (comma-separated).
        chat_tools = (os.environ.get("VEXA_CHAT_TOOLS")
                      or "Read,Write,Edit,Glob,Grep,Bash,WebSearch,WebFetch").split(",")
        session = os.environ.get("VEXA_CHAT_SESSION") or DEFAULT_CHAT_SESSION
        serve(
            client, out_topic=out_topic, in_topic=os.environ["VEXA_UNIT_IN_TOPIC"],
            turn=lambda prompt: run_turn_over_workspace(work, prompt, model=model, allowed_tools=chat_tools, session=session),
            start=json.loads(os.environ.get("VEXA_START", "{}")), idle_ms=idle_ms,
        )
