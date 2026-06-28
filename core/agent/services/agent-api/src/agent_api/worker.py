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
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Iterator, Protocol

from .agent_config import (
    DEFAULT_CARD_KINDS,
    DEFAULT_POLISH_RULES,
    DEFAULT_TAG_RULES,
    MeetingConfig,
    load_meeting_config,
)
from .decision_claude import run_unit_turn

log = logging.getLogger("agent_api.worker")

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
    gen = run_unit_turn(str(work), prompt, _exec_claude, allowed_tools=allowed, session=resume, model=model, commit=commit)
    first = next(gen, None)
    if resume and first is not None and first.get("type") == "done" and not first.get("ok", True):
        if sess_file.exists():
            sess_file.unlink()
        gen = run_unit_turn(str(work), prompt, _exec_claude, allowed_tools=allowed, session=None, model=model, commit=commit)
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


# ── meeting mode: consume the transcript Stream, gate, emit proactive cards ───────────────────────

# build_card_prompt is a THIN COMPOSER: the MECHANISM (the transcript-window framing + the strict JSON
# shape the parser depends on) lives here in code; the POLICY (how to polish each line, and what to tag)
# is INJECTED from the workspace-governed MeetingConfig.polish_rules / .tag_rules — so a user can change
# copilot behavior by prompting the agent to edit agents/meeting.md, no redeploy.

# The fixed frame around the workspace policy. `{polish}` / `{tags}` are the governed rules; `{kinds}` is
# the wanted card kinds; `{steering}` is the (optional) free-text steering section.
_CARD_FRAME = (
    "You are a live meeting copilot watching a conversation in real time. Here is the mutable transcript "
    "processing window. Each line is sent through at most three passes; pass 1 is fresh, pass 2 should "
    "repair obvious ASR/name/entity errors, and pass 3 should be the final clean version before the line "
    "freezes and leaves this window.\n\n{lines}\n\n"
    "Return a processed transcript plus tag cards. For each input line, emit one note with the SAME id "
    "and speaker, following the POLISH RULES below.\n\n"
    "## Polish rules (governed by this workspace)\n{polish}\n\n"
    "Do not create topic headings. Set chapter to an empty string unless the source text itself gives a "
    "literal section title.\n\n"
    "## Tag rules (governed by this workspace)\n{tags}\n\n"
    "Emit tags ONLY as cards of these kinds: {kinds}. Do not use any other kind.\n\n"
    "Respond with ONLY this JSON object (no prose, no markdown fence, and do NOT write any files):\n"
    "{{\"notes\":[{{\"id\":\"<input id>\",\"speaker\":\"<speaker>\",\"chapter\":\"\",\"text\":\"<clean one-line note>\"}}],"
    "\"cards\":[{{\"kind\":\"<one of {kinds}>\",\"title\":\"<short>\",\"body\":\"<one line>\",\"actionable\":true}}]}}\n"
    "Use an empty cards array if these specific lines add no tags.{steering}"
)

# Appended to the frame only when the workspace config carries non-empty steering.
_STEERING_SECTION = (
    "\n\n## Standing instructions from this workspace\n"
    "Follow these workspace-set instructions about what to watch / ignore / tone:\n\n{steering}\n"
)


def build_card_prompt(
    lines: str,
    card_kinds: list[str],
    steering: str = "",
    *,
    polish_rules: str = DEFAULT_POLISH_RULES,
    tag_rules: str = DEFAULT_TAG_RULES,
) -> str:
    """Compose the copilot prompt: inject the workspace-governed ``polish_rules`` + ``tag_rules`` (the
    POLICY) and the wanted ``card_kinds`` around the transcript ``lines`` (the MECHANISM frame), plus the
    optional ``steering`` section. Changing a governed rule (via agents/meeting.md) changes the prompt."""
    section = _STEERING_SECTION.format(steering=steering.strip()) if steering.strip() else ""
    return _CARD_FRAME.format(
        lines=lines,
        kinds=", ".join(card_kinds),
        polish=(polish_rules or DEFAULT_POLISH_RULES).strip(),
        tags=(tag_rules or DEFAULT_TAG_RULES).strip(),
        steering=section,
    )


def _extract_json_value(reply: str | None):
    if not reply:
        return None
    text = reply.strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    import re

    matches = []
    for pattern in (r"\{.*\}", r"\[.*\]"):
        m = re.search(pattern, reply, re.DOTALL)
        if m:
            matches.append((m.start(), m.group(0)))
    for _start, raw in sorted(matches, key=lambda item: item[0]):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def parse_cards(reply: str | None, card_kinds: list[str] | None = None) -> list[dict]:
    """Tolerantly pull the JSON card array out of the agent's reply (it may wrap it in prose/fences).
    When ``card_kinds`` is given, only cards of those kinds are kept."""
    value = _extract_json_value(reply)
    if isinstance(value, dict):
        arr = value.get("cards") or []
    elif isinstance(value, list):
        arr = value
    else:
        return []
    allowed = {k.lower() for k in card_kinds} if card_kinds else None
    out = []
    for c in arr:
        if not (isinstance(c, dict) and c.get("title") and c.get("kind")):
            continue
        if allowed is not None and str(c.get("kind")).lower() not in allowed:
            continue
        out.append(c)
    return out


def parse_notes(
    reply: str | None,
    stage_by_id: dict[str, int] | None = None,
    segment_by_id: dict[str, dict] | None = None,
) -> list[dict]:
    """Pull processed transcript notes from the copilot JSON object."""
    value = _extract_json_value(reply)
    if not isinstance(value, dict):
        return []
    arr = value.get("notes") or []
    if not isinstance(arr, list):
        return []
    stages = stage_by_id or {}
    segments = segment_by_id or {}
    out = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        note_id = str(item.get("id") or "").strip()
        if segments and note_id not in segments:
            continue
        text = _first_person_note_text(str(item.get("text") or ""))
        if not note_id or not text:
            continue
        stage = int(item.get("pass") or stages.get(note_id) or 1)
        source = segments.get(note_id) or {}
        note = {
            "id": note_id,
            "speaker": str(item.get("speaker") or "").strip() or "Speaker",
            "chapter": str(item.get("chapter") or item.get("chapter_title") or "").strip(),
            "text": text,
            "pass": max(1, min(3, stage)),
            "frozen": stage >= 3,
        }
        ts = item.get("t", item.get("ts", source.get("start")))
        if ts is not None:
            note["t"] = ts
        out.append(note)
    return out


def _first_person_note_text(text: str) -> str:
    out = " ".join(text.split()).strip()
    rewrites = [
        (re.compile(r"^Speaker\s+mentions\s+using\s+(.+?)\s+and\s+finding\s+(.+?)\s+appealing\.?$", re.I), r"I use \1 and find \2 appealing."),
        (re.compile(r"^Speaker\s+(?:describes|frames)\s+(.+?)\s+as\s+", re.I), r"\1 is "),
        (re.compile(r"^Speaker\s+read\s+more\s+about\s+", re.I), "I read more about "),
        (re.compile(r"^Speaker\s+(?:expresses|has)\s+fear\s+that\s+", re.I), "I'm afraid "),
        (re.compile(r"^Speaker\s+(?:initially\s+)?thought\s+", re.I), "I initially thought "),
        (re.compile(r"^Speaker\s+(?:believes|thinks)\s+(?:that\s+)?", re.I), ""),
        (re.compile(r"^Speaker\s+(?:announces|states|says|notes|reports|explains|emphasizes)\s+(?:that\s+)?", re.I), ""),
        (re.compile(r"^Speaker\s+will\s+", re.I), "I will "),
        (re.compile(r"^Speaker\s+wants\s+", re.I), "I want "),
        (re.compile(r"^Speaker\s+needs\s+", re.I), "I need "),
        (re.compile(r"^Speaker\s+plans\s+to\s+", re.I), "I plan to "),
        (re.compile(r"^Speaker\s+asks\s+", re.I), "I ask "),
        (re.compile(r"^Speaker\s+", re.I), ""),
    ]
    for pattern, replacement in rewrites:
        next_out = pattern.sub(replacement, out, count=1).strip()
        if next_out != out:
            out = next_out
            break
    return out[:1].upper() + out[1:] if out else out


def fallback_processed_notes(segments: list[dict], stage_by_id: dict[str, int] | None = None) -> list[dict]:
    stages = stage_by_id or {}
    notes: list[dict] = []
    for index, segment in enumerate(segments):
        note_id = str(segment.get("segment_id") or segment.get("id") or f"segment-{index + 1}").strip()
        text = _first_person_note_text(str(segment.get("text") or ""))
        if not note_id or not text:
            continue
        stage = max(1, min(3, int(segment.get("rewrite_pass") or stages.get(note_id) or 1)))
        note = {
            "id": note_id,
            "speaker": str(segment.get("speaker") or "").strip() or "Speaker",
            "chapter": "Live Transcript",
            "text": text,
            "pass": stage,
            "frozen": stage >= 3,
        }
        ts = segment.get("start")
        if ts is not None:
            note["t"] = ts
        notes.append(note)
    return notes


def _model_error_event(message: object, *, model: str | None, stage: str) -> dict:
    text = " ".join(str(message or "model inference failed").split())
    return {"type": "model-error", "error": {"stage": stage, "model": model or "", "message": text[:600]}}


def meeting_card_turn(
    work: Path, segments: list[dict], *, model: str | None = None,
    card_kinds: list[str] | None = None, steering: str = "",
    polish_rules: str = DEFAULT_POLISH_RULES, tag_rules: str = DEFAULT_TAG_RULES,
) -> Iterator[dict]:
    """One copilot beat: read the recent transcript, stream the agent working, then emit a proactive
    card per salient finding. Reuses the governed turn (with session continuity across beats). The
    wanted ``card_kinds`` + workspace ``steering`` shape the prompt, and cards are filtered to the
    allowed kinds."""
    # Copilot behavior is steered EXCLUSIVELY by agents/meeting.md (folded into the prompt below).
    # Residual risk: the card turn runs `claude -p` with cwd=workspace, so claude also auto-loads the
    # workspace CLAUDE.md as project memory. There is no clean, granular Claude Code flag to suppress
    # ONLY CLAUDE.md for a single turn (`--bare` exists but also strips skills/MCP/hooks and narrows the
    # toolset — too blunt for a turn that Reads kg entities for context). So we keep CLAUDE.md free of
    # copilot steering (seed guard) instead of stripping it: CLAUDE.md must carry file conventions only,
    # and all watch/ignore/tone steering lives in agents/meeting.md's body.
    kinds = card_kinds or list(DEFAULT_CARD_KINDS)
    segment_by_id = {str(s.get("segment_id") or s.get("id") or ""): s for s in segments}
    stage_by_id = {seg_id: int(s.get("rewrite_pass") or 1) for seg_id, s in segment_by_id.items()}
    lines = "\n".join(
        f"[pass {int(s.get('rewrite_pass') or 1)}/3 id={s.get('segment_id') or s.get('id') or '?'} "
        f"speaker={s.get('speaker', '?')}] {s.get('text', '')}"
        for s in segments
    )
    reply: str | None = None
    chunks: list[str] = []
    prompt = build_card_prompt(lines, kinds, steering, polish_rules=polish_rules, tag_rules=tag_rules)
    # propose-only: a read-only turn (no Write/Edit). We DON'T forward the streaming turn events — the
    # raw JSON reply would leak into the UI as a "note"; the meeting feed wants only the parsed cards.
    try:
        for ev in run_turn_over_workspace(work, prompt, model=model, allowed_tools=["Read"], commit=False, session_continuity=False):
            if ev.get("type") == "message-delta" and ev.get("text"):
                chunks.append(str(ev.get("text") or ""))
            if ev.get("type") == "done":
                reply = ev.get("reply") or "".join(chunks)
                if ev.get("ok") is False:
                    # Fail LOUD on a 401/auth mismatch: a distinct auth-error (provider host + the
                    # BASE_URL-vs-TOKEN fix) instead of the opaque generic model-error (WS1b). The 401
                    # text may be in the done reply OR in earlier streamed chunks, so scan both.
                    blob = (reply or "") + " " + "".join(chunks)
                    if looks_like_auth_failure(blob):
                        yield _auth_error_event(reply or blob, model=model, stage="meeting-card")
                        return
                    yield _model_error_event(reply, model=model, stage="meeting-card")
                    return
    except Exception as exc:
        if looks_like_auth_failure(exc):
            yield _auth_error_event(exc, model=model, stage="meeting-card")
            return
        yield _model_error_event(exc, model=model, stage="meeting-card")
        return
    reply = reply or "".join(chunks)
    notes = parse_notes(reply, stage_by_id, segment_by_id)
    cards = parse_cards(reply, kinds)
    if segments and not notes:
        yield _model_error_event("model response did not include processed transcript notes", model=model, stage="meeting-card")
        notes = fallback_processed_notes(segments, stage_by_id)
    for note in notes:
        yield {"type": "note", "note": note}
    for card in cards:
        yield {"type": "card", "card": card}


# ── Auth-B/#3(a): persist the processed transcript to the workspace, INCREMENTALLY ────────────────
# As the worker emits 1:1 cleaned `proc:meeting` notes, it ALSO upserts a per-meeting workspace file at
# kg/entities/meeting/<native>.md so a chat agent focused on the meeting can `Read` it and answer "what's
# the meeting about" — WITHOUT waiting for the post-meeting doc turn. The write is idempotent: each line
# is keyed by its note id (== segment_id) with an HTML-comment marker, so a refining pass UPDATES the
# line in place rather than duplicating it. This reuses the same VISIBLE, git-tracked kg/entities/meeting
# path the post-meeting doc turn authors (the doc turn later distills a summary into the SAME tree).

_PROC_LINE_RE = re.compile(r"^<!-- id:(?P<id>.*?) -->", )


def render_meeting_transcript(meta: dict, notes: list[dict]) -> str:
    """Render the per-meeting transcript file: YAML frontmatter (type/id/title/… from ``meta``), a
    Speakers list, and a Transcript section with one id-keyed line per note. Pure + deterministic so the
    upsert is testable offline."""
    speakers: list[str] = []
    for n in notes:
        sp = str(n.get("speaker") or "Speaker").strip() or "Speaker"
        if sp not in speakers:
            speakers.append(sp)
    fm_keys = ("type", "id", "title", "meeting_id", "session_uid", "platform", "date")
    fm_lines = [f"{k}: {meta[k]}" for k in fm_keys if meta.get(k) is not None]
    parts = ["---", *fm_lines, "---", "", "## Speakers", ""]
    parts += [f"- {sp}" for sp in speakers] or ["- (none yet)"]
    parts += ["", "## Transcript", ""]
    for n in notes:
        nid = str(n.get("id") or "").strip()
        sp = str(n.get("speaker") or "Speaker").strip() or "Speaker"
        text = " ".join(str(n.get("text") or "").split())
        tags = n.get("tags") or []
        suffix = f"  _[tags: {', '.join(str(t) for t in tags)}]_" if tags else ""
        parts.append(f"<!-- id:{nid} --> **{sp}:** {text}{suffix}")
    return "\n".join(parts) + "\n"


def upsert_meeting_transcript_file(path: Path, meta: dict, note: dict) -> None:
    """Idempotently UPSERT one cleaned ``note`` (keyed by ``note['id']``) into the per-meeting transcript
    file at ``path``. Reads the current id-keyed lines, replaces the matching id (or appends a new one),
    preserves order, and rewrites the whole file. Re-running with the same note id never duplicates a
    line; a refining note for an existing id overwrites its text."""
    nid = str(note.get("id") or "").strip()
    if not nid:
        return
    notes: list[dict] = []
    if path.exists():
        try:
            for line in path.read_text().splitlines():
                m = _PROC_LINE_RE.match(line)
                if not m:
                    continue
                rest = line[m.end():].strip()
                # parse "**Speaker:** text"
                sp = "Speaker"
                body = rest
                if rest.startswith("**") and ":**" in rest:
                    sp = rest[2:rest.index(":**")].strip() or "Speaker"
                    body = rest[rest.index(":**") + 3:].strip()
                notes.append({"id": m.group("id"), "speaker": sp, "text": body})
        except OSError:
            notes = []
    replaced = False
    for existing in notes:
        if existing.get("id") == nid:
            existing["speaker"] = note.get("speaker") or existing.get("speaker")
            existing["text"] = note.get("text") or existing.get("text")
            replaced = True
            break
    if not replaced:
        notes.append({"id": nid, "speaker": note.get("speaker") or "Speaker", "text": note.get("text") or ""})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_meeting_transcript(meta, notes))


def _set_cursor(stream: _Stream, cursor_key: str | None, raw_id: str) -> None:
    """Freeze the per-meeting processed CURSOR = the last raw transcript stream-id cleaned. Best-effort:
    a fake stream without ``set`` (or a transient redis error) must never break the live beat."""
    if not cursor_key:
        return
    setter = getattr(stream, "set", None)
    if setter is None:
        return
    try:
        setter(cursor_key, str(raw_id))
    except Exception:  # noqa: BLE001 — the cursor is an optimization; never crash the meeting loop
        pass


def _proc_note(segment: dict) -> dict | None:
    """The baseline 1:1 cleaned note for ONE segment — id == segment_id, the speaker, and the cleaned
    text (same first-person cleanup the fallback uses). None when there's nothing to emit (empty text)."""
    notes = fallback_processed_notes([segment])
    return notes[0] if notes else None


def serve_meeting(
    stream: _Stream, *, transcript_stream: str, out_topic: str,
    card_turn: Callable[[list[dict]], Iterator[dict]], idle_ms: int,
    beat_segments: int = 4,
    doc_turn: Callable[[list[dict]], Iterator[dict]] | None = None,
    enabled: bool = True,
    start_id: str = "0",
    proc_stream: str | None = None,
    cursor_key: str | None = None,
    on_proc_note: Callable[[dict], None] | None = None,
) -> None:
    """Consume the meeting's ``transcript.v1`` Stream (the meetings⊥agent seam — read by schema), gate
    cheaply (a NEW speaker, or ``beat_segments`` segments), and run a copilot beat that XADDs proactive
    cards to ``out_topic``. Live drafts (``completed:false``) are included and upserted by ``segment_id``
    — a refining draft updates its line in place rather than piling up a duplicate. The transcript keeps
    it non-idle; ``session_end`` (or an idle gap) reaps it.

    Cards surfaced across the meeting are accumulated (de-duped by title) and, on ``session_end``,
    handed to ``doc_turn`` — the post-meeting WRITE turn that authors/updates the kg meeting entity.
    """
    seen_speakers: set[str] = set()
    buffer: list[dict] = []
    processing_window: list[dict] = []
    window_by_id: dict[str, dict] = {}  # segment_id → its window item, so a refining draft upserts
    cards: list[dict] = []          # running, de-duped (by title) list of every surfaced card
    seen_titles: set[str] = set()
    last = start_id
    n = 0

    def _emit_proc_note(note: dict) -> None:
        """XADD ONE cleaned note (id == segment_id) onto the per-meeting processed STREAM — the reliable
        1:1 CLEANED transcript channel, SEPARATE from the cards beat on ``out_topic`` — and ALSO upsert it
        into the per-meeting workspace file (Auth-B/#3a) via ``on_proc_note``, so a chat agent can Read the
        meeting context incrementally. Both are best-effort over the same 1:1 cleaned note."""
        if not note:
            return
        if proc_stream:
            stream.xadd(proc_stream, {"note": json.dumps(note)})
        if on_proc_note is not None:
            try:
                on_proc_note(note)
            except Exception:  # noqa: BLE001 — the workspace mirror is an optimization; never crash the loop
                log.warning("serve_meeting: on_proc_note upsert failed", exc_info=True)

    def _run_beat(segs: list[dict], idx: int) -> None:
        tid = f"beat{idx}"
        staged = [{**seg, "rewrite_pass": int(seg.get("_rewrite_passes", 0)) + 1} for seg in segs]
        for ev in card_turn(staged):
            if ev.get("type") == "card":
                _accumulate_card(cards, seen_titles, ev.get("card") or {})
            elif ev.get("type") == "note":
                # The LLM rewrite returned a valid note for this id → UPGRADE the cleaned-stream text
                # (baseline already emitted at ingest; this is the richer pass, still 1:1 by segment_id).
                _emit_proc_note(ev.get("note") or {})
            stream.xadd(out_topic, {"event": json.dumps({**ev, "turn_id": tid})})
        stream.xadd(out_topic, {"event": json.dumps({"type": "turn-complete", "turn_id": tid})})
        sent_ids = {id(seg) for seg in segs}
        for seg in processing_window:
            if id(seg) in sent_ids:
                seg["_rewrite_passes"] = int(seg.get("_rewrite_passes", 0)) + 1

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
                    mutable = [seg for seg in processing_window if int(seg.get("_rewrite_passes", 0)) < 3]
                    if mutable and enabled:
                        n += 1
                        _run_beat(mutable, n)
                    if doc_turn is not None:  # post-meeting WRITE: author/update the kg meeting entity
                        _emit_turn(stream, out_topic, lambda: doc_turn(cards), "meeting-doc")
                    return  # meeting ended → reap
                for seg_index, seg in enumerate(payload.get("segments", [])):
                    sid = seg.get("segment_id") or f"{entry_id}:{seg_index}"
                    existing = window_by_id.get(sid)
                    if existing is not None:
                        # A live draft refining (or finalizing) a segment already in the window: update
                        # it IN PLACE so the next beat reads the latest text, never a duplicate line.
                        # Identity is preserved, so the rewrite-pass bookkeeping still holds.
                        existing["text"] = seg.get("text", existing.get("text"))
                        existing["completed"] = seg.get("completed", True)
                        continue
                    item = dict(seg)
                    item["segment_id"] = sid
                    item["_rewrite_passes"] = 0
                    window_by_id[sid] = item
                    buffer.append(item)
                    processing_window.append(item)
                    # Baseline 1:1 cleaned note onto the processed stream — ALWAYS present per segment,
                    # so the cleaned channel never has a gap even before/without an LLM upgrade beat.
                    base = _proc_note(item)
                    if base is not None:
                        _emit_proc_note(base)
                    sp = seg.get("speaker")
                    if sp and sp not in seen_speakers:
                        seen_speakers.add(sp)
                        new_speaker = True
                # Advance the per-meeting CURSOR to the last raw stream-id we've now cleaned (gap-fill
                # picks up from here on re-enable; OFF freezes it at the last processed entry).
                _set_cursor(stream, cursor_key, entry_id)
        if enabled and buffer and (new_speaker or len(buffer) >= beat_segments):
            n += 1
            mutable = [seg for seg in processing_window if int(seg.get("_rewrite_passes", 0)) < 3]
            if mutable:
                _run_beat(mutable, n)
            processing_window = [seg for seg in processing_window if int(seg.get("_rewrite_passes", 0)) < 3]
            window_by_id = {seg["segment_id"]: seg for seg in processing_window}
            buffer = []


def _accumulate_card(cards: list[dict], seen_titles: set[str], card: dict) -> None:
    """Retain a surfaced card, de-duped by (case-folded) title so re-surfacing across beats doesn't
    duplicate it in the final meeting doc."""
    title = (card.get("title") or "").strip()
    if not title:
        return
    key = title.casefold()
    if key in seen_titles:
        return
    seen_titles.add(key)
    cards.append(card)


def _emit_turn(stream: _Stream, out_topic: str, turn: Callable[[], Iterator[dict]], tid: str) -> None:
    for ev in turn():
        stream.xadd(out_topic, {"event": json.dumps({**ev, "turn_id": tid})})
    stream.xadd(out_topic, {"event": json.dumps({"type": "turn-complete", "turn_id": tid})})


def _emit_beat(stream: _Stream, out_topic: str, card_turn, segments: list[dict], n: int) -> None:
    _emit_turn(stream, out_topic, lambda: card_turn(segments), f"beat{n}")


# ── post-meeting WRITE turn: distill the surfaced cards into the kg meeting entity ────────────────

_CARD_GROUP = {  # card kind → the section it lands under in the doc
    "person": "Attendees", "company": "Companies", "product": "Products",
}

MEETING_DOC_PROMPT = (
    "The meeting has ENDED. Author or update the knowledge-graph entity for it as a SINGLE markdown "
    "file at the EXACT path `kg/entities/meeting/{native}.md` in this workspace (create parent dirs if "
    "needed). This must be IDEMPOTENT — if the file already exists, UPDATE it in place (do not create a "
    "duplicate or a new path).\n\n"
    "The file MUST have this exact YAML frontmatter (between `---` fences) as the very first lines:\n"
    "---\n"
    "type: meeting\n"
    "id: {native}\n"
    "title: {title}\n"
    "meeting_id: {meeting_id}\n"
    "session_uid: {session_uid}\n"
    "platform: {platform}\n"
    "date: {date}\n"
    "---\n\n"
    "After the frontmatter, write a 2-4 line plain-English SUMMARY of the meeting (no transcript — a "
    "distilled summary), then the surfaced entities grouped under `## Attendees`, `## Companies`, "
    "`## Topics`, `## Decisions`, `## Actions` headings, each entry a `[[wikilink]]` (omit a heading if "
    "it has no entries). Here are the entities surfaced during the meeting (JSON):\n\n{cards}\n\n"
    "Do NOT copy the raw transcript. When done, write/edit ONLY that one file."
)


def meeting_doc_turn(
    work: Path, cards: list[dict], *, native: str, meeting_id: str, session_uid: str,
    platform: str, date: str, title: str, model: str | None = None,
) -> Iterator[dict]:
    """The post-meeting WRITE turn: ONE governed turn (commit=True, Write/Edit allowed,
    session_continuity=False so it never touches the chat session) that authors/updates the meeting
    entity at ``kg/entities/meeting/<native>.md`` — a distilled summary + the surfaced cards as grouped
    wikilinks. Idempotent (re-running updates rather than duplicates)."""
    prompt = MEETING_DOC_PROMPT.format(
        native=native, title=title, meeting_id=meeting_id, session_uid=session_uid,
        platform=platform, date=date, cards=json.dumps(cards, ensure_ascii=False),
    )
    yield from run_turn_over_workspace(
        work, prompt, model=model, allowed_tools=["Read", "Write", "Edit"],
        commit=True, session_continuity=False,
    )


def main() -> None:  # pragma: no cover — the container entrypoint (wired in tests via serve())
    import redis

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


if __name__ == "__main__":  # pragma: no cover
    main()
