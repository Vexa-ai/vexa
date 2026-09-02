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
import shutil
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


# ── the active mount set (WP-A1.1) — declared VERBATIM to the model so it never guesses where to write ──

def active_mounts() -> list[dict]:
    """The dispatch's ordered active mount set from ``VEXA_MOUNTS`` (``[{slug,path,role,write,primary}]``).
    A dispatch that predates the set (no ``VEXA_MOUNTS``) falls back to the single private baseline at
    ``VEXA_WORKSPACE_PATH`` — identical to today's one-workspace behavior."""
    raw = os.environ.get("VEXA_MOUNTS")
    if raw:
        try:
            data = json.loads(raw)
            mounts = [m for m in data if isinstance(m, dict) and m.get("path")] if isinstance(data, list) else []
            if mounts:
                return mounts
        except (ValueError, TypeError):
            log.warning("VEXA_MOUNTS is not valid JSON — falling back to the private baseline")
    path = os.environ.get("VEXA_WORKSPACE_PATH", "/workspace")
    return [{"slug": Path(path).name, "path": path, "role": "private", "write": True, "primary": True}]


def _tier_label(m: dict) -> str:
    """The mount's TIER + write-rule, declared VERBATIM so the model never guesses where it may write
    (AMENDMENT 4 three-tier stack). Derived from role/primary/write, not from the slug."""
    role = m.get("role", "private")
    if role == "global":
        # The org tier is ro for everyone EXCEPT the platform-elevated admin subjects
        # (VEXA_GLOBAL_ADMIN_SUBJECTS): their setup conversation is its one sanctioned writer.
        # Declare what the mount ACTUALLY is — a rw bind described as ro reads as an injection
        # attempt and a well-behaved model rightly refuses it.
        if m.get("write"):
            return ("GLOBAL SYSTEM tier — READ-WRITE for THIS session only: the platform's admin "
                    "allowlist elevated you as the org tier's one sanctioned writer (the admin "
                    "setup conversation). Commit each change; everyone else mounts this ro.")
        return "GLOBAL SYSTEM tier — READ-ONLY (platform behaviour/skills/tools; never write here)"
    if role == "system":
        return ("PRIVATE SYSTEM tier — read-write (who you're helping via `identity.md`, your"
                " chats/sessions, settings, routines; private, never shared)")
    if role == "room":
        # The post-meeting MEETING ROOM: another attendee's own desk, mounted read-only for this one
        # meeting. Declared explicitly (rather than falling through to the generic line below)
        # because the model must know whose notes these are and that they are not its own: the mount
        # is bound :ro, so a write attempt fails at the filesystem, but a mount described vaguely
        # invites the attempt in the first place.
        who = (m.get("room") or {}).get("subject") or "another attendee"
        mtg = (m.get("room") or {}).get("meeting_id") or "this meeting"
        return (f"MEETING ROOM — READ-ONLY: {who}'s own workspace, mounted for meeting {mtg} only. "
                f"Read it to ground the shared write-up; NEVER write here, and never copy anything "
                f"out of it that the meeting itself did not cover.")
    if m.get("primary"):
        return "your PRIVATE baseline (durable personal memory) — read-write"
    writable = "read-write" if m.get("write", True) else "READ-ONLY (do not write here)"
    return f"{role} workspace — {writable}"


def _continuity_root(work: Path) -> Path:
    """Where chat continuity (session pointers + transcripts) LIVES: the PRIVATE SYSTEM mount
    (``_system``) when the dispatch declares one, else the turn's workspace. The flat model can
    point the turn's cwd at a SHARED workspace (Personal off -> first active mount), and chat
    conversations are private to the subject — anchoring them to the cwd both LEAKS them onto the
    shared volume and strands them where ``workspace_reader.history`` (which reads the subject's
    own tree) can't see them: the "chats list but don't load" bug."""
    for m in active_mounts():
        if m.get("role") == "system" and m.get("path"):
            return Path(m["path"])
    return work


def _adopt_legacy_continuity(chat_root: Path, work: Path, session: str) -> None:
    """MIGRATE-ON-READ for the continuity-carrier move (ADR-0028's write-side twin): threads recorded
    BEFORE chats anchored to ``_system`` live under the turn's then-cwd. When the anchored pointer is
    absent, adopt the thread — pointer AND transcript — from the first mount dir that has it, so
    moving the carrier never forks a conversation ("this is the first message I'm seeing" on turn 2).
    Same adoption discipline ``_session_file`` already applies to the legacy single-thread file."""
    target = chat_root / ".claude" / "sessions" / f"{session}.session"
    if target.exists():
        return
    candidates = [work] + [Path(m["path"]) for m in active_mounts() if m.get("path")]
    for root in candidates:
        if root == chat_root:
            continue
        src = root / ".claude" / "sessions" / f"{session}.session"
        try:
            if not src.exists():
                continue
            sid = src.read_text().strip()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(sid + "\n" if sid else "")
            # the transcript must move WITH the pointer — a resumed sid whose jsonl is missing under
            # the new projects link is an alien id (the stale-resume retry silently starts fresh)
            if sid:
                for t in (root / ".claude" / "projects").glob(f"*/{sid}.jsonl"):
                    dst = chat_root / ".claude" / "projects" / t.parent.name / t.name
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if not dst.exists():
                        shutil.copyfile(t, dst)
            return
        except OSError:
            continue


def kg_links_preamble() -> str:
    """Entity references must be ACTIONABLE in the client. Chat replies and workspace docs render
    ``[[Title]]`` as a clickable entity chip and workspace file paths as links (the terminal resolves
    both across every mounted workspace — clients/terminal/src/ui-kit/docLinks.tsx). A plain-text
    mention of a known entity is a dead end, so this rule ships on EVERY turn, not just multi-mount
    ones — old workspaces whose seeded CLAUDE.md predates it get the behaviour too.

    The WORKSPACE rule (founder, 2026-09-01) is here for a reason worth stating: asked to "reference
    workspace with its readme", a reply named the workspace in bold and pasted
    ``/workspaces/vexa-team-3183d1/README.md`` as inline code — "no reference, and when reference
    it's not interactive". The renderer now recognizes all three spellings, but a pasted absolute
    path is still the worst of them: it is the worker's private filesystem showing through, it means
    nothing to the reader, and it breaks the moment the mount moves. Name the workspace; the client
    turns the name into the door to its README."""
    return (
        "## Referencing knowledge (always)\n\n"
        "Your replies render in a client that turns entity references into clickable chips:\n"
        "- Whenever you mention a person, company, organization, project, meeting, or task that has"
        " (or that you are creating) an entity doc under `kg/entities/`, write it as `[[Title]]` —"
        " in chat replies AND in workspace docs alike. Never mention a known entity as plain text.\n"
        "- Reference other workspace files by their path in backticks (e.g. `kg/dashboards/plan.md`)"
        " or a markdown link — both are clickable.\n"
        "- Name a WORKSPACE by its slug (e.g. `vexa-team-3183d1`) or link its README"
        " (`<slug>/README.md`) — the client makes the name open that README, which is the"
        " workspace's dashboard. Never introduce a workspace in bold with nothing behind it.\n"
        "- Write paths workspace-RELATIVE (`README.md`, `kg/entities/person/jane-liu.md`). Never"
        " paste the absolute mount path you see on disk (`/workspaces/…`) — it is your filesystem,"
        " not the reader's, and it says nothing they can use.\n"
        "- Put the reference OUTSIDE code fences and never backtick a `[[wikilink]]` — a fenced"
        " block is literal text and renders dead.\n"
        "- Don't write `[[wikilinks]]` for things that have no entity doc (they render as inert"
        " 'not found' chips) — create the entity first, or use plain text.\n"
        "- `kg/templates/` and any doc whose frontmatter carries `template: true` are SHAPES, not"
        " records: never list them as entities or meetings, never `[[wikilink]]` them, never cite"
        " them in a brief and never count them as prior context. If you hold nothing on a subject,"
        " say so — a shape is not a substitute for knowledge you do not have.\n\n"
    )


_GLOBAL_CONTEXT_FILES = ("CLAUDE.md", "PURPOSE", "README.md")
_GLOBAL_CONTEXT_MAX_CHARS = 48_000


def global_context_preamble(mounts: list[dict]) -> str:
    """Load the organisation tier into the turn, rather than merely telling the model it exists.

    Agent harnesses auto-load instructions from the current working directory, but ``_global`` is a
    sibling mount. Without this bridge the model consults it only when a user explicitly says
    "global", which makes Personal onboarding ask for organisation facts already known centrally.
    Read the small authoritative entry files on every turn so live _global edits take effect on the
    next message. The cap bounds prompt growth while keeping the beginning of each authored file.
    """
    mount = next((m for m in mounts if m.get("role") == "global" or m.get("slug") == "_global"), None)
    if not mount:
        return ""
    root = Path(str(mount["path"]))
    remaining = _GLOBAL_CONTEXT_MAX_CHARS
    sections: list[str] = []
    for name in _GLOBAL_CONTEXT_FILES:
        path = root / name
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if not content.strip() or remaining <= 0:
            continue
        excerpt = content[:remaining]
        remaining -= len(excerpt)
        sections.append(f"### `{path}`\n\n{excerpt.rstrip()}")
    if not sections:
        return (
            "## Organisation context (mandatory)\n\n"
            f"Before reasoning, read `{root}/CLAUDE.md`, `{root}/PURPOSE`, and `{root}/README.md` "
            "when present. Use organisation facts proactively; do not ask the user for facts already "
            "recorded in `_global`.\n\n"
        )
    return (
        "## Organisation context (mandatory; loaded from `_global`)\n\n"
        "This is the shared organisational ground for every turn. Apply its instructions and use its "
        "facts proactively, including while working in Personal. Before asking for a company, employer, "
        "organisation, terminology, policy, or objective, answer from this context when it already settles "
        "the question. Personal context identifies the person; it does not erase organisation context.\n\n"
        + "\n\n".join(sections)
        + "\n\n"
    )


def mounts_preamble(mounts: list[dict]) -> str:
    """A prompt preamble that DECLARES every mount in the THREE-TIER stack to the model VERBATIM — names,
    paths, tiers, roles, write rules — plus the default write-routing policy (WP-A1.2). The agent must
    never guess where it may read/write. Enforcement is minimal in this WP (per-mount commit with the
    principal as author); the routing rule is STATED. A single private mount ⇒ no preamble (nothing to
    disambiguate — the legacy one-workspace turn is unchanged)."""
    if len(mounts) <= 1:
        return ""
    lines = ["## Your mounted workspaces", "",
             "This turn mounts a STACK of workspaces (the three-tier mount stack). Each is a separate git"
             " repo; every writable one is committed independently after the turn:",
             ""]
    for m in mounts:
        lines.append(f"- `{m['path']}` — **{m.get('slug')}** ({_tier_label(m)})")
        # A per-workspace PURPOSE (stored in the workspace, travels when shared) tells the agent what THIS
        # workspace is for — so a composition (Personal + a deal ws + a dept ws) self-explains where to write.
        purpose = (m.get("purpose") or "").strip()
        if purpose:
            lines.append(f"    - Purpose: {purpose}")
    lines += [
        "",
        "Write-routing policy:",
        "- Platform behaviour/skills/tools live in the GLOBAL SYSTEM tier (`_global`) — READ-ONLY, never write it.",
        "- Chats/sessions/settings, and who you're helping (`identity.md`) → the PRIVATE SYSTEM tier (`_system`).",
        "- If `_system/identity.md` still has no user name, ASK the user their name early and record it there"
        " (the full profile — company, role, relationships — belongs in the Personal baseline's `self: true`"
        " person entity, not here).",
        "- Personal notes/drafts and anything the user marks private → your PRIVATE baseline mount.",
        "- Content produced FOR a shared/community space (shared notes, common docs, shared entities) →"
        " the matching shared mount (only if it is read-write).",
        "- When a workspace states a Purpose (above), let it decide where content belongs — write material"
        " that matches a workspace's purpose into THAT workspace.",
        "- Never write to a READ-ONLY mount.",
        # SCOPED to file operations (2026-09-01). Unqualified, this line reads as a rule about
        # WRITING, and the model applied it to its reply prose too: asked to reference a workspace,
        # it pasted `/workspaces/<slug>/README.md` — the founder's "no reference, and when reference
        # it's not interactive". Reading and writing files needs the absolute path; a sentence
        # addressed to a human never does.
        "When you READ OR WRITE a file, always use ABSOLUTE paths under the mount you intend — do"
        " not guess or invent mount paths. This is about file operations only: in the text of your"
        " REPLY, reference workspaces and docs by name or workspace-relative path (see § Referencing"
        " knowledge) — a mount path pasted into a sentence is your filesystem, not the reader's.",
        "",
    ]
    return "\n".join(lines)


class _Stream(Protocol):
    """The slice of redis the harness needs (XADD out, XREAD in) — a fake satisfies it in tests."""

    def xadd(self, name: str, fields: dict) -> str: ...
    def xread(self, streams: dict, count: int = 1, block: int | None = None) -> list: ...
    # xrevrange is OPTIONAL (serve() falls back to "$" when the stream object lacks it — older fakes):
    # it anchors the in-topic read at the boot-time tail so a message XADDed while the entrypoint turn
    # runs is consumed after it instead of lost ("$" only sees entries added after the first xread).


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


def _principal_author() -> tuple[str, str] | None:
    """The dispatch PRINCIPAL (name, email) for commit attribution (D4) — the authenticated human whose
    input drove the turn, stamped into the worker env by the dispatcher. Absent ⇒ None (git falls back to
    its configured identity, and the committer is still the platform via ``_commit_env``)."""
    name = (os.environ.get("VEXA_PRINCIPAL_NAME") or "").strip()
    email = (os.environ.get("VEXA_PRINCIPAL_EMAIL") or "").strip()
    if name and email:
        return name, email
    return None


def _extra_mount_paths(work: Path) -> list[Path]:
    """The WRITABLE mounts OTHER than the primary ``work`` — the additional repos a turn may have written,
    each committed independently after the turn (WP-A1.2). READ-ONLY mounts (the ``_global`` GLOBAL SYSTEM
    tier) are EXCLUDED — agents never write, and thus never commit, ``_global`` (AMENDMENT 4)."""
    extras: list[Path] = []
    for m in active_mounts():
        p = Path(m["path"])
        if not m.get("primary") and m.get("write", True) and p != work:
            extras.append(p)
    return extras


# The MCP server id the delegated vexa toolbelt is attached under. It is also the allow-set prefix
# (`mcp__vexa`), so the two must agree — hence one constant, not two string literals.
VEXA_MCP_SERVER = "vexa"

# THE ALLOW-SET IS NAMED, TOOL BY TOOL — AND THIS DID NOT DO WHAT IT WAS WRITTEN TO DO.
#
# It was landed to stop the tools arriving DEFERRED: discoverable by a tool-search step, callable
# only after the model loads one. That round trip is where the failures live — a measured 1 in 8
# dispatches on Haiku never completes it, and the model says so in its own words ("the tool appears
# in the deferred MCP tools list, but I don't have a direct function invocation"), then writes a
# confident note from the title instead, or writes nothing and leaves the step waiting.
#
# MEASURED AFTER THE CHANGE: the tools are named in --allowedTools and ToolSearch STILL precedes
# the first call. Deferral is the harness's own context management, not a permission gate, so the
# allow-set cannot reach it. The stall is NOT fixed by this and remains open.
#
# The list is kept because it is right on its own terms — least privilege, and a worker offered
# only the surface it uses — but it buys no reliability, and anyone reading this looking for the
# fix to the deferred round trip should keep looking. The two things that DO hold the line are the
# grounding gate in process_meeting (a note not in the transcript never ships) and its fail-fast
# (a finished turn with no note fails in seconds, with the agent's own words, instead of after
# fifteen minutes).
#
# The list is the MEASURED 21-tool union from deploy/dogfood/rig/TOOL-USAGE.md — every tool real
# delegated workers called (14) or reached for by name without calling (7) across 45 sessions. It
# is deliberately not all 53: the other 32 are the person-agent's own surface (registration,
# identity, deeplinks, operator bot control) and a worker has no business being offered them.
#
# `workspace_write` is here despite measuring zero calls, and the same document says why: workers
# write the workspace 63 times a turn through the volume mount, so the write side looks unused only
# because it is being reached another way. A worker without the mount needs it on day one.
#
# A tool the server does not serve is inert in an allow-set, so this list ageing behind the server
# costs nothing; a tool the server gains and this list lacks is still permitted by the prefix on
# the first line. It degrades in both directions.
VEXA_MCP_TOOLS = (
    # called by real workers, most-used first
    "whats_waiting", "meeting_transcript", "meetings_list", "workspace_tree", "workspaces",
    "bot_send", "bot_stop", "meeting_info", "company_context", "workspace_new", "workspace_read",
    "report_friction", "flows_list", "propose",
    # reached for by name but never successfully called — the same surface, one step later
    "flow_lifecycle", "flows_submit", "start_onboarding", "validate", "vexa_overview",
    "workspace_purpose", "mark_scaffolded",
    # the write side, unused only because the volume mount hides it
    "workspace_write",
)


def mcp_delegation_config(work: Path) -> "tuple[str | None, list[str]]":
    """Materialize the worker's AUTHENTICATED vexa MCP attachment → (mcp-config path, extra allow-set).

    The dispatcher minted a short-lived delegation token for this dispatch and handed it over in the
    env (``VEXA_MCP_DELEGATION_TOKEN`` + ``VEXA_MCP_URL``); this turns that into the ``.mcp.json`` the
    harness attaches with ``--mcp-config`` + ``--strict-mcp-config``. Returns ``(None, [])`` when either
    is absent — the pre-delegation behaviour, an unauthenticated worker with no MCP, not a failure.

    THE TOKEN TRAVELS IN A HEADER, never in the URL. The rig accepts both (``?c=<tok>`` is its
    setup-link dialect), but a credential in a query string leaks into every access log and proxy trace
    it passes through, and this one crosses a public hostname.

    It is written under ``.claude/`` because that directory is GITIGNORED in the workspace seed — the
    post-turn ``git add -A`` in ``run_harness_turn`` commits every changed mount, so a credential
    written anywhere else in the workspace would be committed and synced to the workspace store. The
    file is chmod 600 for the same reason the env var is not echoed: it is a bearer credential.
    """
    url = (os.environ.get("VEXA_MCP_URL") or "").strip()
    token = (os.environ.get("VEXA_MCP_DELEGATION_TOKEN") or "").strip()
    if not url or not token:
        return None, []
    cfg = {"mcpServers": {VEXA_MCP_SERVER: {
        "type": "http", "url": url, "headers": {"Authorization": f"Bearer {token}"},
    }}}
    d = work / ".claude"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "mcp.json"
    path.write_text(json.dumps(cfg))
    try:
        path.chmod(0o600)
    except OSError:  # a store backend that does not carry modes — the attachment still stands
        pass
    return str(path), [f"mcp__{VEXA_MCP_SERVER}",
                       *(f"mcp__{VEXA_MCP_SERVER}__{t}" for t in VEXA_MCP_TOOLS)]


def run_turn_over_workspace(
    work: Path, prompt: str, *, model: str | None = None, allowed_tools: list[str] | None = None,
    commit: bool = True, session_continuity: bool = True, session: str = DEFAULT_CHAT_SESSION,
    mcp_config: str | None = None,
    harness: HarnessPort | None = None,
) -> Iterator[dict]:
    """One governed agent turn over the mounted workspace SET: resume from the session file, DECLARE the
    active mounts to the model, drive ``run_harness_turn`` (which commits EACH changed mount, authored by
    the dispatch principal), and persist the captured session id. A stale resume (the harness session
    expired) retries fresh once.
    ``allowed_tools`` defaults to Read/Write/Edit; pass ``["Read"]`` for a propose-only (no-write) turn.
    ``session`` namespaces the continuity file so chat threads stay distinct (default ``"main"``)."""
    _ensure_repo(work)
    # Resolve the harness through the worker.worker seam at call time so a test patching
    # `worker.worker.harness_factory` reaches this call site (the harness was one module historically).
    import worker.worker as _w
    factory = getattr(_w, "harness_factory", harness_from_env)
    harness = harness or factory()
    chat_root = _continuity_root(work)  # chats are PRIVATE: _system when mounted, never a shared cwd
    harness.prepare(work, chat_root=chat_root)  # harness-specific continuity/skills wiring (durable)
    if session and session_continuity:
        _adopt_legacy_continuity(chat_root, work, session)  # migrate-on-read: pre-anchoring threads
    sess_file = _session_file(chat_root, session)
    # session_continuity=False (the meeting copilot): never read/write the shared chat session — its
    # card-extraction beats must NOT pollute the user's chat conversation memory.
    resume = _resume_id(chat_root, sess_file, harness) if session_continuity else None
    allowed = allowed_tools or ["Read", "Write", "Edit"]
    # Declare the mount set to the model VERBATIM (WP-A1.1) + the write-routing policy (WP-A1.2), so the
    # agent never guesses where it may read/write. Single-mount turns get no mounts preamble; the
    # kg-links rule ([[wikilinks]] render as actionable entity chips) applies to EVERY turn.
    mounts = active_mounts()
    author = _principal_author()
    extras = _extra_mount_paths(work)
    turn_prompt = kg_links_preamble() + mounts_preamble(mounts) + global_context_preamble(mounts) + prompt
    gen = run_harness_turn(work, turn_prompt, harness, allowed_tools=allowed, session=resume, model=model,
                           commit=commit, author=author, extra_mounts=extras, mcp_config=mcp_config)
    first = next(gen, None)
    if resume and first is not None and first.get("type") == "done" and not first.get("ok", True):
        if sess_file.exists():
            sess_file.unlink()
        gen = run_harness_turn(work, turn_prompt, harness, allowed_tools=allowed, session=None, model=model,
                               commit=commit, author=author, extra_mounts=extras, mcp_config=mcp_config)
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

def serve(stream: _Stream, *, out_topic: str, in_topic: str, turn: TurnFn, start: dict, idle_ms: int,
          harness: HarnessPort | None = None) -> None:
    """Run the entrypoint turn (if any), then serve interactive messages on ``in_topic`` until idle.

    Each turn's UnitEvents are XADD'd to ``out_topic`` (tagged with a turn id), followed by a
    ``turn-complete`` marker. An empty blocking read (idle) returns — the process exits and the
    container is reaped (TTL-on-idle). A ``{"type":"stop"}`` message exits immediately.

    Every turn opens with a ``turn-accepted`` event — the worker's LIVENESS ACK. It flips the UI
    off "Starting agent" the moment the turn is picked up (long before the first model token) and
    is the evidence the dispatcher's warm-delivery watchdog waits on: no accepted event = the
    message was NOT taken (worker exited in the race window) → the dispatcher respawns. A warm
    in-topic message carries a ``nonce`` the ack echoes so the watchdog can match ITS delivery.
    """
    # Mid-turn injection (VEXA_MIDTURN_INJECT=1): between output events, drain the in-topic and hand
    # arriving user messages to the RUNNING harness through its runner-neutral steering seam. Claude
    # writes stream-json to its open stdin; Codex sends turn/steer to app-server. A message the active
    # runner cannot take is left IN the stream for the between-turns loop.
    def _drain_inject(cursor: list) -> None:
        enabled = getattr(harness, "midturn_enabled", lambda: False)
        inject = getattr(harness, "inject_user_message", lambda _text: False)
        if not enabled():
            return
        try:
            resp = stream.xread({in_topic: cursor[0]}, count=8, block=None)
        except Exception:  # noqa: BLE001
            return
        for _name, entries in resp or []:
            for entry_id, fields in entries:
                msg = json.loads(fields.get("turn", "{}"))
                text = msg.get("prompt", "")
                if msg.get("type") == "stop" or not text:
                    return  # leave stop (and everything after) for the outer loop
                if not inject(text):
                    return  # no active stdin — leave queued
                cursor[0] = entry_id
                # satisfy the dispatcher's warm-delivery watchdog: the injected message WAS taken
                if msg.get("nonce"):
                    stream.xadd(out_topic, {"event": json.dumps({"type": "turn-accepted", "nonce": msg["nonce"], "injected": True})})
                stream.xadd(out_topic, {"event": json.dumps({"type": "user-injected", "text": text})})

    def run_message(prompt: str, turn_id: str, nonce: str | None = None, cursor: list | None = None) -> None:
        ack: dict = {"type": "turn-accepted", "turn_id": turn_id}
        if nonce:
            ack["nonce"] = nonce
        stream.xadd(out_topic, {"event": json.dumps(ack)})
        for ev in turn(prompt):
            stream.xadd(out_topic, {"event": json.dumps({**ev, "turn_id": turn_id})})
            if cursor is not None:
                _drain_inject(cursor)
        stream.xadd(out_topic, {"event": json.dumps({"type": "turn-complete", "turn_id": turn_id})})

    # Anchor the in-topic cursor at the BOOT-TIME tail, before the entrypoint turn runs. The
    # dispatcher pre-delivers each chat message to the in topic BEFORE asking the runtime to spawn
    # (warm delivery): on a COLD spawn that same prompt arrives as the entrypoint, so everything
    # already in the stream at boot must be SKIPPED (no double turn) — while a message that lands
    # DURING the entrypoint turn (previously invisible to a "$" read, a lost turn) is consumed
    # right after it. Streams without history anchor at 0-0; a stream object without xrevrange
    # (older test fakes) keeps the legacy "$" behavior.
    last = "$"
    xrevrange = getattr(stream, "xrevrange", None)
    if xrevrange is not None:
        try:
            tail = xrevrange(in_topic, count=1)
            last = tail[0][0] if tail else "0-0"
        except Exception:  # noqa: BLE001 — tail anchoring is an upgrade, never a boot blocker
            last = "$"

    cursor = [last]  # shared with _drain_inject: mid-turn-consumed entries advance it
    first = start_prompt(start)
    if first:
        run_message(first, "t0", cursor=cursor)

    n = 0
    while True:
        resp = stream.xread({in_topic: cursor[0]}, count=1, block=idle_ms)
        if not resp:
            return  # idle → exit 0 → container reaped
        for _name, entries in resp:
            for entry_id, fields in entries:
                cursor[0] = entry_id
                msg = json.loads(fields.get("turn", "{}"))
                if msg.get("type") == "stop":
                    return
                n += 1
                run_message(msg.get("prompt", ""), f"t{n}", nonce=msg.get("nonce"), cursor=cursor)


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
        # P0 (cross-tenant leak fix): the transcript carrier is keyed by the meetings-domain ROW id
        # (VEXA_MEETING_NUMERIC_ID) — the transcript_stream tail is now that row id, NOT the native id.
        # The NATIVE id (human-readable, e.g. abc-defg-hij) is carried SEPARATELY in VEXA_MEETING_ID for
        # display + the readable kg doc name (nuance #1: kg/entities/meeting/{native}.md must survive).
        # Never derive `native` from the stream tail anymore (that is the row id); fall back to the tail
        # only when VEXA_MEETING_ID is somehow unset (older dispatcher), which at worst degrades the
        # display name, never the row-scoped isolation.
        row_id = os.environ.get("VEXA_MEETING_NUMERIC_ID") or transcript_stream.rsplit(":", 1)[-1]
        native = os.environ.get("VEXA_MEETING_ID") or row_id
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
        on_doc_committed = None
        dev_email = (os.environ.get("VEXA_POST_MEETING_DEV_EMAIL") or "").strip()
        if dev_email:
            # Explicit development adapter only. The structured env is schema-validated at worker
            # boot; a future production delivery service fills the same EmailSink port rather than
            # teaching the meeting loop SMTP or recipient policy.
            from worker.post_meeting import (
                DevSmtpEmailSink,
                MeetingCompletion,
                PostMeetingFault,
                PostMeetingNotifier,
                WorkspaceArtifactReader,
                parse_dev_notification_config,
                require_personal_recipient,
                require_personal_workspace,
            )
            notification = parse_dev_notification_config(dev_email)
            if doc_turn is None:
                raise PostMeetingFault(
                    source="config", kind="meeting-doc-disabled",
                    detail="VEXA_POST_MEETING_DEV_EMAIL requires agents/meeting.md write_meeting_doc=true",
                )
            require_personal_workspace(
                work,
                store_root=Path(os.environ.get("VEXA_WORKSPACE_MOUNT_TARGET", "/workspaces")),
                subject=os.environ["VEXA_OWNER"],
            )
            require_personal_recipient(
                notification.recipient,
                principal_email=os.environ.get("VEXA_PRINCIPAL_EMAIL", ""),
            )
            notifier = PostMeetingNotifier(
                WorkspaceArtifactReader(work),
                DevSmtpEmailSink(notification.smtp, sender=notification.sender),
                terminal_url=notification.terminal_url,
            )

            def on_doc_committed(commit: dict) -> None:
                receipt = notifier.notify(MeetingCompletion(
                    subject=os.environ["VEXA_OWNER"], meeting_id=row_id, native_id=native,
                    platform=platform, title=title, recipient=notification.recipient,
                    commit_sha=str(commit["commit_sha"]),
                ))
                log.info(
                    "post-meeting notification sent subject=%s meeting=%s commit=%s artifact=%s",
                    os.environ["VEXA_OWNER"], row_id, receipt.commit_sha, receipt.artifact_path,
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
            # P0 (cross-tenant leak fix): BOTH the processed-notes stream AND its cursor key on the
            # meetings-domain ROW id (VEXA_MEETING_NUMERIC_ID) — unique per meeting run, so neither a
            # re-sent bot on the same native link NOR a different tenant on the same link can ever
            # mix/clobber/read another meeting's processed doc. The meeting-api db-writer (which knows
            # its own row ids) drains proc:meeting:{row_id} into that meeting row's data JSONB (durable).
            # The cursor is now a position in the ROW-KEYED transcript stream tc:meeting:{row_id} (each
            # row has its own stream), so it too MUST be row-scoped — a shared native-keyed cursor would
            # resume one row from another row's position (and leak progress across tenants).
            proc_stream=f"proc:meeting:{row_id}",
            cursor_key=f"proc:meeting:{row_id}:cursor",
            on_proc_note=on_proc_note,
            on_envelope=on_envelope,
            on_doc_committed=on_doc_committed,
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
        # The delegated vexa MCP (meetings, transcripts, workspaces) — attached ONLY when the dispatcher
        # minted a token for this dispatch. Attaching the server is not enough: `--strict-mcp-config`
        # scopes WHICH servers exist, `--allowedTools` scopes what the model may CALL, so the server id
        # must enter the allow-set too or every tool call would stall on a permission prompt that no
        # human is there to answer.
        mcp_cfg, mcp_tools = mcp_delegation_config(work)
        if mcp_cfg:
            chat_tools = chat_tools + mcp_tools
            log.info("agent-api worker: vexa MCP attached for owner=%s (delegated, scoped, short-lived)",
                     os.environ.get("VEXA_OWNER"))
        # One harness instance owns the whole warm worker lifetime. That makes the steering handle
        # instance-scoped (Codex JSON-RPC process / Claude stdin) instead of a vendor-global mailbox.
        import worker.worker as _w
        chat_harness: HarnessPort = getattr(_w, "harness_factory", harness_from_env)()
        harness_warning = chat_harness.preflight()
        if harness_warning:
            log.warning("agent-api worker: %s", harness_warning)
        serve(
            client, out_topic=out_topic, in_topic=os.environ["VEXA_UNIT_IN_TOPIC"],
            turn=lambda prompt: run_turn_over_workspace(work, prompt, model=model,
                                                        allowed_tools=chat_tools, session=session,
                                                        mcp_config=mcp_cfg, harness=chat_harness),
            start=json.loads(os.environ.get("VEXA_START", "{}")), idle_ms=idle_ms,
            harness=chat_harness,
        )
