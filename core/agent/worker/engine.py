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

This module holds the ONE engine; ``worker.worker`` re-exports it so existing
``from worker.worker import X`` imports keep resolving. (A second module, ``worker.meeting`` — the
live meeting copilot — sat beside it until PRD decision 34 removed that pipeline.)
"""
from __future__ import annotations

import hashlib
import itertools
import json
import pathlib
import logging
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Iterator, Protocol

from llm import (
    HarnessPort,
    auth_error_event,
    close_event_stream,
    harness_from_env,
    looks_like_auth_failure,
    preflight_provider_guard,
    provider_host,
    run_harness_turn,
)
from llm import jobs as llm_jobs
from llm.errors import _AUTH_SIGNATURE_RE  # noqa: F401 — re-exported for the worker.worker shim
from shared.seeding import resolve_seed_dir, seed_workspace, validate_seed
# PRD decision 31 §1 — WHERE THIS PERSON IS IN TIME, on every dispatch (used in the preamble list
# in `run_turn_over_workspace`). Imported rather than written here: the work is an HTTP read and a
# cache, not prompt text, and what it returns is rendered by the flows route — the same rendering
# the control-MCP `timeline` tool gets, so a chat and a machinery note cannot disagree about when
# a meeting was.
#
# It stays its OWN call rather than a section of somebody else’s block: decision 30 §2’s
# human-surface block (which chat, which meeting, which page is open) is composed by another hand,
# and one surface with two writers is the failure `graph/sg/Operating-Loops.md` names in a line.
# Whoever composes that block appends what this returns.
from shared.timeline import timeline_preamble  # noqa: F401 — re-exported for the turn prompt
# THE STREAM NAMES (Vexa-ai/vexa#1610). The worker is handed its two topics and never its unit id;
# `unit_of_topic` reads it back out and `inbox_cursor_key` spells the one key this loop writes, so
# the reader that answers "what is still queued for this chat" is looking where the writer wrote.
from shared import units as shared_units
# THE JOB RUNNER (Vexa-ai/vexa#1584) — a long act that does not hold the chat. It sits above the
# harness on purpose, so `serve` gets background work for every runner rather than one adapter's.
from worker import jobs as worker_jobs
from worker.friction import (disbelieved_capability, fallback_session, friction_preamble,
                             mcp_unreachable,
                             report as report_friction, scan_turn, spawn_gap)

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
        # DECISION 22 — a workspace is a DESK, and this is the sentence a person reads when the
        # mount stack is quoted back at them (it renders verbatim into the chat as `**seed** (…)`).
        # The founder saw "seed" in his own chat on 2026-09-02: a seed is what the platform copied
        # to make the thing, not what the thing IS afterwards. Only the DESCRIPTION changes here —
        # the slug, the path and every code identifier keep saying what they say, because renaming
        # those would move data. Same word as `control_plane.scaffolds.WORKSPACE_WORD` and the
        # terminal's `minutes/vocabulary.ts`; four spellings of one noun, each naming the others.
        return "your DESK — your private baseline, durable personal memory — read-write"
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


# ── the imperative gate (F162, ledger 2026-09-02 14:17Z-14:30Z) ────────────────────────────────────
# Ledger entries F161/F162/F166/F169: in a live-meeting copilot chat the founder wrote "send bot"
# FOUR TIMES. The turn answered each with a workspace `propose` call (Objective/membership
# questions), a WebSearch, a WebFetch — never `bot_send`. Two causes were found; this fixes the
# worker-side one (the other is a `bots_running` state-truth fix in the rig, not this file): the
# composed prompt put the person's own words LAST, after the mounts/entity/global-context
# preambles below — and those preambles themselves nudge the model toward onboarding ("ASK the user
# their name early") and proactive `propose` behaviour. An explicit operational imperative buried
# under six preambles' worth of onboarding/propose framing lost to that framing, repeatedly.
#
# This preamble ships FIRST — before every other concern in `run_turn_over_workspace`'s composed
# prompt, including the MCP-status note — so an imperative is the first thing the model reads, not
# the last. It fires on the person's OWN words only (`prompt`, not the preamble text that follows),
# to avoid ever matching itself or another preamble's prose.
_IMPERATIVE_PATTERNS: tuple[tuple["re.Pattern[str]", str, str], ...] = (
    (re.compile(r"\bsend\s+(the\s+)?bot\b", re.I), "bot_send", "send the bot"),
    (re.compile(r"\bschedule\s+(the\s+)?bot\b", re.I), "bot_schedule", "schedule the bot"),
    (re.compile(r"\bjoin\s+(the\s+)?(meeting|call)\b", re.I), "bot_send", "join the meeting"),
    (re.compile(r"\bstop\s+record(ing)?\b", re.I), "bot_stop", "stop recording"),
    (re.compile(r"\bstop\s+(the\s+)?bot\b", re.I), "bot_stop", "stop the bot"),
)


def imperative_preamble(prompt: str) -> str:
    """If the person's own message names an operational imperative — send/stop/schedule the bot,
    join, stop recording — say so FIRST, in words that outrank every onboarding/propose/write-back
    concern the rest of the composed prompt carries. Empty string when nothing matches: an ordinary
    chat turn gets no extra framing at all."""
    matched: list[tuple[str, str]] = []
    seen_tools: set[str] = set()
    for pat, tool, phrase in _IMPERATIVE_PATTERNS:
        if pat.search(prompt or "") and tool not in seen_tools:
            matched.append((tool, phrase))
            seen_tools.add(tool)
    if not matched:
        return ""
    lines = [
        "## An operational imperative is in this message — act on it FIRST",
        "",
        "The person's own words below name at least one of: send/stop/schedule the bot, join the "
        "meeting, stop recording. Call the matching tool NOW, before any onboarding question, any "
        "`propose` call, any web search, and before the write-back phase. Answer with what the tool "
        "actually returned — never a scaffold question, a search, or a description of what you would "
        "do, in its place.",
        "",
    ]
    for tool, phrase in matched:
        lines.append(f"- \"{phrase}\" -> call `{tool}` first.")
    lines.append("")
    return "\n".join(lines) + "\n"


def voice_preamble() -> str:
    """READ SILENTLY. Ships on EVERY turn, for the same reason kg_links does — it is a rule about how
    the agent speaks, not about what this particular turn is.

    ⚠ It was first written into the composed-opening machinery note (scaffolds.MACHINERY_NOTE), which
    covers only turns a link composed. The founder then watched a `+` chat narrate its way through
    the middle of a conversation — "Let me research…", "Let me dig at the source…", "Let me read the
    actual repo" — because a chat he opened himself is not a composed opening and never saw the rule.
    A rule about voice that only reaches some turns is a rule about nothing: the person cannot tell
    which kind of turn they are in, and neither should they have to.

    The step line under the composer already shows the tool and the count. Announcing the same thing
    in prose is the product saying twice, worse, what it has already said once."""
    return (
        "## How you speak\n\n"
        "Read, search and open whatever you need SILENTLY. Every sentence you emit is addressed to "
        "the person: never narrate your own tool use — no \"Let me read…\", \"Let me look at…\", "
        "\"I'll start by…\", \"I've got what I need\". The client already shows which tool is "
        "running and how many steps have passed, so prose about it is the same fact told twice and "
        "worse.\n\n"
        "Keep anything you do say between steps to a minimum, and make it something the person "
        "gains by reading — a finding, a decision, a question. If the only content is that you are "
        "still working, say nothing: the step line is saying it already.\n\n"
    )


def kg_links_preamble(mounts: "list[dict] | None" = None) -> str:
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
        # PRD decision 26.2. The rule is stated here AND enforced in `shared/entities.py` on the
        # way to disk. Both, deliberately: a rule the model follows most of the time plus a
        # rewrite that is always right beats either alone — and the model is the half that can
        # write the link into a CHAT REPLY, where no writer runs.
        "- Linking to a page in ANOTHER of your mounted workspaces, write"
        " `[[ws:<workspace-id>/<entity-id>]]` — the id from the list above, the entity id from that"
        " page's frontmatter `id`. Never a bare title across workspaces: a title resolves in"
        " whichever workspace is searched first, and it dies when either one is renamed. For a file"
        " with no entity id, `[[ws:<workspace-id>/<path>]]`.\n"
        "- Don't write `[[wikilinks]]` for things that have no entity doc (they render as inert"
        " 'not found' chips) — create the entity first, or use plain text.\n"
        "- `kg/templates/` and any doc whose frontmatter carries `template: true` are SHAPES, not"
        " records: never list them as entities or meetings, never `[[wikilink]]` them, never cite"
        " them in a brief and never count them as prior context. If you hold nothing on a subject,"
        " say so — a shape is not a substitute for knowledge you do not have.\n\n"
    )


def page_verbs_preamble() -> str:
    """THE THREE THINGS THAT CAN HAPPEN TO A PAGE, named together (Vexa-ai/vexa#1621).

    ⚠ THIS EXISTS BECAUSE OF WHAT AN AGENT DID WITH ONLY ONE OF THEM. Asked to *"remove from
    personal"* — seven pages of a customer dossier being moved off the desk into that customer's own
    workspace — the turn held `workspace_write`, which creates or overwrites, and two read-only
    verbs. So it OVERWROTE each page with a one-line pointer and reported the move as done; the
    founder was then told that "removed" meant "collapsed", with the seven files still on the desk
    (friction `fr_a373e9448d2909a6`). A model reaches for the nearest verb it holds, and the nearest
    verb to a delete it does not have is a write.

    Named as a SET rather than left to the tool list for the reason `entity_upsert` is named in the
    write-back prompt: the tools arrive DEFERRED (see `harness_subprocess_env`), so a verb the
    prompt does not mention is one the model has to go looking for before it can believe it exists —
    and a turn that does not believe a delete exists does not search for one, it improvises.

    Ships on EVERY dispatch, like `kg_links_preamble`: this is about a class of act, not about a
    particular mount, and the turns most likely to tidy a desk are ordinary chat turns with no
    composed opening at all."""
    return (
        "## Moving and removing pages\n\n"
        "A page can be written, moved or removed, and there is a verb for each — never improvise one "
        "out of another:\n"
        "- `workspace_write(path, content, slug)` — create it, or replace what is there.\n"
        "- `workspace_move(path, to, slug, to_slug)` — take the page to another path, or to another "
        "workspace. Inside one workspace a pointer is left behind so existing links still land; "
        "across workspaces it is a write in the target and a removal in the source.\n"
        "- `workspace_delete(path, slug)` — take the page away.\n\n"
        "Both are COMMITS in the workspace, so a removal is history and never a loss — you do not "
        "need to keep a copy, leave a stub, or ask before removing a page somebody asked you to "
        "remove. **Never fake either one by overwriting a page with a note saying it moved**: the "
        "file is still there, and the person who asked you to remove it now believes it is gone.\n"
        "Refused where you may not write: the `_system` tier always, `_global` unless you are the "
        "org admin, a shared workspace you only read. A refusal is the answer — say it, do not "
        "route around it.\n\n"
    )


# The rule text of PRD decision 24, and the index it is a rule about. One string, because the rule
# is unreadable without the list and the list is inert without the rule.
_ENTITY_INDEX_MAX_CHARS = 12_000


def entity_index_preamble(mounts: list[dict]) -> str:
    """The entity index of every mounted workspace, plus the rule that makes it actionable.

    Decision 24 (founder, 2026-09-02): *"how to update the agent so that it updates entities whenever
    there is a chance for that? so it does not hesitate creating pages"*. Hesitation had two causes
    and this preamble removes the first: the agent could not see what already existed, so every
    mention of a name was a choice between a duplicate page and no page, and it reliably chose no
    page. `entity_upsert` removes the second (one call, create-or-append).

    Ships on EVERY dispatch for the reason `kg_links_preamble` does — a rule that reaches only some
    turns is a rule about nothing, and the turns most likely to learn a name (a chat the person
    opened themselves) are exactly the ones no composed opening covers.

    The list is read from the generated `kg/INDEX.md` when it is there (refreshed by every upsert)
    and rendered live from the directory when it is not, so a first dispatch into a workspace that
    has never been upserted still sees what it holds instead of being told nothing exists."""
    from workspaces.shared.entities import INDEX_PATH, render_index
    from workspaces.shared.workspace_id import workspace_id_of

    blocks: list[str] = []
    for m in mounts:
        if not m.get("write", True):
            continue                     # `_global` is the org tier — entities belong on a desk
        root = Path(str(m.get("path") or ""))
        slug = str(m.get("slug") or root.name)
        try:
            f = root / INDEX_PATH
            listing = f.read_text(encoding="utf-8", errors="replace") if f.exists() else ""
            if not listing.strip():
                listing = render_index(root, slug)
        except OSError:
            continue
        # THE ID IS IN THE HEADING because the rule above is unusable without it. An agent told
        # to write `[[ws:<workspace-id>/…]]` and never shown a workspace id will either invent one
        # or fall back to a bare title — and a bare title across workspaces is the defect.
        wid = m.get("id") or workspace_id_of(root) or ""
        label = str(m.get("name") or "").strip()
        head = f"### `{slug}`"
        if wid:
            head += f" — workspace id `{wid}`"
        if label and label != slug:
            head += f" ({label})"
        blocks.append(f"{head}\n\n{listing.strip()[:_ENTITY_INDEX_MAX_CHARS]}")
    if not blocks:
        return ""
    return (
        "## Entities you already hold, and the rule about writing them\n\n"
        "**A name without a page gets one NOW.** The moment this turn learns something durable about "
        "a person, company, meeting, project or decision, call `entity_upsert(kind, name, facts, "
        "source)` — it creates the page if it does not exist and appends a dated entry if it does, "
        "so there is nothing to check first and nothing to merge by hand. Call it on a maybe: a fact "
        "the page already carries writes nothing.\n"
        "- **Facts carry a source.** Only what was said in this conversation or read from a file, a "
        "tool result or a transcript, each with where it came from. A fact with no source is refused.\n"
        "- **Gaps go to `kg/MISSING.md`, never invented.** What you would have to guess is written "
        "there as an open question, not onto the page.\n\n"
        + "\n\n".join(blocks) + "\n\n")


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


def active_target() -> str:
    """This chat's TARGET WORKSPACE slug, or ``""`` for the person's own desk (Vexa-ai/vexa#1611).

    ``VEXA_TARGET_WORKSPACE`` is stamped by ``dispatch.build_unit_env`` exactly when the chat has a
    target — a POSITIVE signal, never inferred from which mount is ``primary``, because primary also
    answers for a room run's group desk and for an ordinary subject's own baseline."""
    return (os.environ.get("VEXA_TARGET_WORKSPACE") or "").strip()


def mounts_preamble(mounts: list[dict], target: str = "") -> str:
    """A prompt preamble that DECLARES every mount in the THREE-TIER stack to the model VERBATIM — names,
    paths, tiers, roles, write rules — plus the default write-routing policy (WP-A1.2). The agent must
    never guess where it may read/write. Enforcement is minimal in this WP (per-mount commit with the
    principal as author); the routing rule is STATED. A single private mount ⇒ no preamble (nothing to
    disambiguate — the legacy one-workspace turn is unchanged).

    ``target`` MARKS ONE OF THEM (Vexa-ai/vexa#1611). agent-api says the same fact in the person's
    vocabulary — *"target workspace: <name> — writes go here unless asked otherwise"* — and this says
    it in the model's: which PATH a file operation with no better instruction belongs under. Two
    sentences, one fact, and neither derives it independently: both are handed the same slug."""
    if len(mounts) <= 1:
        return ""
    want = str(target or "").strip()
    lines = ["## Your mounted workspaces", "",
             "This turn mounts a STACK of workspaces (the three-tier mount stack). Each is a separate git"
             " repo; every writable one is committed independently after the turn:",
             ""]
    for m in mounts:
        mark = " — **this chat's target: writes go here unless asked otherwise**" if (
            want and m.get("slug") == want) else ""
        lines.append(f"- `{m['path']}` — **{m.get('slug')}** ({_tier_label(m)}){mark}")
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
        *(["- THE TARGET MARKED ABOVE WINS over the two rules before it: it is where this conversation"
           " is working, and a write elsewhere needs the person to have asked for it in this turn,"
           " with its purpose. Say where you wrote."] if want else []),
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


# ── the write-back phase (PRD decision 24.2) ─────────────────────────────────────────────────────
#
# "A gate, not advice." The grounding gate is the precedent and the measurement behind it is the
# argument: asked in a prompt to read the transcript, Haiku did it about half the time; made to
# prove it, depth went 0.00 → 0.90. Asking the model to record entities *while* it answers competes
# with answering, and answering wins. So it is asked afterwards, separately, when nothing else is
# in flight — and the person's answer is never delayed by it, because their answer has already
# streamed by the time this runs.
#
# THE SAME MARK the composed openings carry. It is now READ from `shared/marks.py` rather than
# retyped: this image COPYs `core/agent/shared` (worker/Dockerfile) and this module already imports
# from it, so "the three sides ship in different images" was never a reason the PYTHON copies had to
# diverge. The terminal's TypeScript copy still cannot import it and is compared by gate:fact-parity.
from shared.marks import MACHINERY_MARK  # noqa: E402 — re-export; see shared/marks.py

# THE PHASE'S OWN MARK (F51). The write-back phase runs in the SAME harness session as the turn,
# so its prompt and its reply land in the transcript the history reader serves — and the founder read
# them back as his own conversation: an empty agent turn, a USER bubble saying "Continue from where
# you left off.", then "No response requested — write-back complete." A phase exchange is machinery,
# and machinery must be recognisable in the RECORD rather than guessed at from its prose.
#
# Distinct from `MACHINERY_MARK` and not a replacement for it: a composed OPENING is machinery whose
# ANSWER the person read and must keep, while a phase exchange is machinery whose answer nobody was
# ever shown. Suppressing everything after a `MACHINERY_MARK` turn would delete the first real reply
# of every scaffolded chat. One mark per meaning. `control_plane.workspace_reader.history` drops a
# turn carrying this one and every agent turn up to the next thing a person actually said.
# ONE literal, two names: `shared.marks.PHASE_MARK` is what control_plane calls it.
from shared.marks import WRITEBACK_MARK  # noqa: E402 — re-export; see shared/marks.py

# THE THIRD MARK (Vexa-ai/vexa#1584): this act runs as a background JOB, not on this turn. Written
# by `control_plane/chat_intents.job_prefix`, read here in `run_message`. Same module, same reason
# — a decision that changes how a prompt is run has to be legible in the prompt.
from shared.marks import act_label, read_job_mark, turn_namespace  # noqa: E402 — see shared/marks.py

# AND THE SALVAGE FOR A TURN NOBODY TYPED THAT CARRIES NO MARK (Vexa-ai/vexa#1605): a flow's
# composed kick names its own kind in its first bracket. One implementation, so what this
# records and what `workspace_reader.history` serves cannot answer differently.
from shared.chat_label import composed_label  # noqa: E402 — see shared/chat_label.py

# ⚠ THE FALLBACK IS NOT A TEST CONVENIENCE — it is the majority case for some deployments. A
# dispatch with no delegation token gets NO MCP at all (`mcp_delegation_config` returns `(None,
# [])`), so a phase whose only instruction names `entity_upsert` would be a wasted model call on
# every one of those turns. Measured 2026-09-02 on a DNA fixture with the tool absent: the phase
# ran for 58 seconds and created nothing. The workspace is mounted either way; the file is the
# floor, the tool is the fast path, and the SHAPE has to be stated because the two paths must
# produce the same page (`shared/entities.py` is the other spelling of it).
def entity_file_shape() -> str:
    """What to write when `entity_upsert` is not there — the SAME card the tool renders.

    ⚠ THIS TEXT TAUGHT THE FLAT PAGE. It was written when a page WAS a title and a stack of dated
    bullets, and it kept saying so after decision 24.6 changed the shape: measured on the offline
    A/B, every page the fallback produced came out flat — a heading, a date, a paragraph — while the
    tool path rendered cards. A fallback that produces a different shape is not a fallback, it is a
    second format nobody asked for, and the sections here are GENERATED from `CARD_SECTIONS` so this
    cannot go stale a second time."""
    from workspaces.shared.entities import CARD_SECTIONS

    per_kind = "; ".join(f"{k} → " + " / ".join(v) for k, v in CARD_SECTIONS.items())
    return (
        "If `entity_upsert` is not available to you, write the page yourself — the workspace is "
        "mounted — and write THE SAME CARD it would have written, never a dated log.\n"
        "`kg/entities/<kind>/<slug>.md`, kind one of person/company/meeting/project/decision, slug "
        "kebab-case of the name. Frontmatter `type`, `id`, `title`, `aliases: []`, "
        "`created: <today>`, `sources: [<source>]`; then `# <Name>`; then ONE plain line saying what "
        "it is; then these sections for its kind, each with `- ` bullets, each bullet ending "
        "` — source: <source>`:\n"
        f"  {per_kind}\n"
        "then `## Connected` with `- [[Other Name]] — <relation>` for everything it touches (and add "
        "the matching line on the other page when that page exists), `## Sources`, `## Open "
        "questions`, and `## Timeline` LAST with `### <today>` over anything that is genuinely a "
        "dated event. Put each fact in its section: a page that is all Timeline is the shape we are "
        "moving away from. Never rewrite what is already on the page.\n"
        # Measured: with no `entity_upsert` the model writes the whole file by hand, and on one DNA
        # fixture it spent the entire budget on two lavish pages where the other fixture got eleven.
        # Padding is also the counter-rule broken — a section filled to look complete is a section
        # carrying something nobody said. An empty heading is the honest answer and it costs nothing.
        "Keep each page SHORT: one or two bullets a section, only what this turn actually said or "
        "read, and leave a section empty when the turn learned nothing for it. Never pad a page to "
        "look complete — every page you do not write is a name nobody can look up.\n\n")


_ENTITY_FILE_SHAPE = entity_file_shape()

# ⚠ THE FIRST VERSION OF THIS ASKED FOR JUDGEMENT AND GOT HESITATION — the exact thing the founder
# named. Told to record "what this turn learned about a person, company, meeting, project or
# decision", the phase answered `nothing new` on a turn that had just written a note naming six
# people and three organisations, and explained itself: *"further pages for individual TSC members
# or companies can be added as the project develops and additional facts about them become relevant
# beyond their participation in this kickoff."* Every clause of that is a reasonable editorial
# judgement, and every clause is the reason the graph stays empty.
#
# ⚠ THE SECOND VERSION WAS RIGHT AND SLOW. Asking the model to find the names cost 118-136s on
# Haiku against a 31-47s answer, so the worker stayed busy three times as long and every message the
# person sent meanwhile queued behind it. Finding names is a regex over text the turn already
# produced; only the FACTS need a model. So the list arrives already made (`missing_names`), the
# phase is told to take it in order, and a turn whose names all have pages never reaches a model.
def desk_mounts(mounts: "list[dict] | None" = None) -> "tuple[dict | None, list[dict]]":
    """`(the desk, the group workspaces)` out of a mount set — whose README is maintained, and what
    the `## Workspaces` section lists.

    The DESK is the writable primary mount: a person's own workspace, the one the panel opens by
    default. Groups are the other writable non-system mounts. `_global` is neither — it is the
    organisation tier, read-only, and nobody's desk.

    MOVED UP, ahead of `writeback_prompt` (which now calls it via `_writeback_workspace_note`):
    `WRITEBACK_PROMPT = writeback_prompt(...)` a few dozen lines down runs at IMPORT time, so
    `desk_mounts` has to already be bound in the module namespace by the time that line executes —
    definition order matters here in a way it does not for a function only ever called later."""
    ms = [m for m in (mounts if mounts is not None else active_mounts()) if isinstance(m, dict)]
    normal = [m for m in ms if m.get("write", True) and m.get("role") not in ("global", "system")
              and m.get("slug") not in ("_global", "_system")]
    desk = next((m for m in normal if m.get("primary")), None)
    groups = [m for m in normal if m is not desk]
    return desk, groups


def _writeback_workspace_note(mounts: "list[dict] | None" = None) -> str:
    """WHERE these pages belong, named — never left for the model to guess (F196/F198/F200).

    `entity_upsert` writes to the caller's personal desk unless `slug=` names a mount explicitly,
    and nothing about the call shape hints that a slug exists to pass. Measured live: a turn spent
    entirely inside one shared workspace (`zenith-c172ae`) still had the phase write three pages to
    the personal desk instead — and a second turn, told the desk's `slug`, still 403'd because
    `entity_upsert` had defaulted the wrong way once already and the model saw no reason to name it
    explicitly this time either. The fix is not a smarter default; it is saying the slug out loud.

    Exactly one writable shared workspace is the common case this turn is trying to fix and the
    only one safe to give an instruction rather than a menu — naming ANY slug when more than one is
    active would be a guess, and the phase must never guess which one a name belongs to."""
    # `desk_mounts` already keeps only the writable, non-system/global mounts (its own `normal`
    # filter reads `m.get("write", True)`) — `groups` needs no second filter here.
    desk, groups = desk_mounts(mounts)
    if not groups:
        return ""
    if len(groups) == 1:
        g = groups[0]
        slug = g.get("slug") or g.get("id") or ""
        label = g.get("name") or slug
        return (
            f"\nTHIS TURN IS IN THE SHARED WORKSPACE `{slug}`" + (f" ({label})" if label != slug else "")
            + f" — pass `slug=\"{slug}\"` explicitly on every `entity_upsert` call below so each "
            "page lands there, not on your personal desk (which is where a call with no `slug` "
            "goes). Only leave `slug` off for something that is genuinely personal and not part "
            "of this shared work.\n"
        )
    listed = ", ".join(f"`{g.get('slug') or g.get('id')}`" for g in groups)
    return (
        f"\nMORE THAN ONE SHARED WORKSPACE IS ACTIVE ({listed}). A call to `entity_upsert` with no "
        "`slug` lands on your personal desk, never on one of these — pass `slug=\"<the one this "
        "name belongs to>\"` explicitly for every name that is shared work, and leave it off only "
        "for something genuinely personal.\n"
    )


def image_rule() -> str:
    """THE ONE LINE ABOUT PICTURES, wherever a page is written (Vexa-ai/vexa#1624).

    Founder, 2026-09-06, on the OeNB README: the page carried a Wikimedia address the agent had
    invented, and it answers 404. `shared/page_images.py` catches that on the way in — the reference
    never reaches the page — but a rule that only exists as an enforcement teaches nothing: the
    agent's next turn writes the same guess, has it removed again, and never learns why the picture
    it described is not there.

    Its own function because that is all it is: one sentence, said in the write-back phase and in
    the asks that write pages (`behavior/asks/{create,extend,setup-global}.md`), with nothing else
    about the phase mixed into it."""
    return ("An image address you have not fetched or checked is a GUESS: never write one you have "
            "not seen answer. Use `fetch_asset` to bring a picture into the workspace and reference "
            "it relatively; when you cannot find the real file, write the sentence without it.")


def writeback_prompt(candidates: list[str], mounts: "list[dict] | None" = None) -> str:
    """The phase's one model call, with the work already identified.

    No "list what you learned" step survives here: that step was both the slow half and the half
    that hesitated. The names are given, in order, and the only question left is what this turn can
    honestly say about each and where it read it.

    ``mounts`` defaults to the dispatch's own active set (``active_mounts()``) — passed explicitly
    at the call site so the workspace this phase names is provably the SAME set the turn itself
    saw, not a second read that could disagree with it."""
    named = "\n".join(f"- {c}" for c in candidates)
    return (
        MACHINERY_MARK + " " + WRITEBACK_MARK
        + " Write-back phase — not a message from the person, and nothing you say "
        "here reaches them. Do not address them, do not summarise the turn, do not ask anything.\n\n"
        "These names came up in the turn you just finished and NONE of them has a page yet:\n\n"
        + named + "\n\n"
        "Give each one a page, in the order listed, with `entity_upsert(kind, name, ...)` — one call "
        "per name, starting immediately, no preamble and no re-reading. `kind` is one of person, "
        "company, meeting, project, decision.\n"
        + _writeback_workspace_note(mounts) + "\n"
        # Decision 24.6. The founder's word for a page written as a stack of dated bullets was
        # "flat", and the tool grew `summary`/`fields`/`section` so it need not be. The phase is
        # where most pages are born, so if the phase does not use them nothing else will.
        "WRITE A CARD, NOT A LOG. Give each page a `summary` — one plain line saying what it is — "
        "and put each fact in its section with `fields` (the tool description lists the fields for "
        "each kind: a person has `role`, `company`, `cares_about`, `relationship`; a company has "
        "`what`, `people`, `relationship`). A person's employer passed as `company` links the two "
        "pages in BOTH directions, which is the whole point of writing it there rather than in a "
        "sentence. Use `facts` only for something that is genuinely a dated event; it goes to the "
        "page's Timeline, and a page that is all Timeline is the shape we are moving away from.\n\n"
        "`source` is required and is where you read it: the meeting, the mail, the file, the "
        "person's message. What you would have to guess goes in `open_questions`, as the question. "
        "One sentence of fact is enough to start a page.\n\n"
        "Do not judge whether a name is important enough or whether it is covered somewhere else. A "
        "meeting note is NOT a substitute for a page — it records an occasion, a page records a "
        "subject, and the point is that the next turn can find the subject.\n\n"
        "For a MEETING, pass `dates` as well — `held_at` when you know it ran, "
        "`report_delivered_at` when you know its write-up reached them, `scheduled_at` when it is "
        "still ahead (ISO-8601 or epoch, any subset, only what this turn actually knows). Those "
        "three fields are what the desk's `Now` section and `timeline` both read, so a meeting "
        "that ran with no write-up shows as an open commitment without anyone writing a sentence "
        "about it. A meeting page with no `dates` is a page nothing can order.\n\n"
        + _ENTITY_FILE_SHAPE +
        "A name you cannot say one sourced thing about does NOT get a page: append it to "
        "`kg/MISSING.md` as one line — the name and what you would need to know — in a single "
        "write at the end. Never invent a fact to fill a page.\n\n"
        + image_rule() + "\n\n"
        "Work only from this turn. You have a hard budget: no exploration, no reading files you "
        "have not already read.")


# Kept as a module constant because the tests and the docs name it, and because a phase with an
# empty candidate list is unreachable by construction — `should_write_back` refuses it first.
WRITEBACK_PROMPT = writeback_prompt(["<the names the pre-pass found>"])


# Read/Glob/Grep to check what a page already says, Write/Edit for `kg/MISSING.md`, and the entity
# verb itself. Deliberately NOT the research tools: this phase records what the turn already learned,
# and a phase that can go and look things up is a second turn wearing a bookkeeping name.
WRITEBACK_TOOLS = ("Read", "Glob", "Grep", "Write", "Edit")


def refresh_desk_readme(mounts: "list[dict] | None" = None) -> "dict | None":
    """Regenerate the desk README's generated sections and commit them (PRD decision 26.4).

    Runs at the END of a turn, in the same phase as the entity write-back, because it is the same
    act one level up: the write-back records what the turn learned, this makes the desk SHOW it.

    Founder, 2026-09-02: the desk README is *"the thing where they have what they generally need —
    mostly links to the other cards in different workspaces"*. So the refresh is handed EVERY mount,
    not just the desk: a card in the group is linked in its `ws:` id form and a card here stays a
    plain title. And it is handed the usage signal — what the panel reported this person actually
    opening (`.vexa/touches.json`, mirrored there by agent-api) — because a list of links is only
    useful if the ones they use are at the top. Ranking by last-modified alone ranks by what the
    AGENT wrote, which is close to the opposite.

    Fails soft and returns None. A README section is the least important thing a turn produces; a
    turn that answered the person and then died updating a bulleted list has lost far more than the
    list is worth."""
    from shared import desk_readme
    from workspaces.shared.entities import commit_entity
    from workspaces.shared.workspace_id import read_touches, workspace_id_of

    desk, groups = desk_mounts(mounts)
    if not desk or not desk.get("path"):
        return None
    # THE SECOND COMMITTER, and the one that fires even when the write-back phase has nothing to
    # do: this runs on EVERY turn and commits `README.md` whenever a section changed. On a
    # post-meeting room run against a group-less meeting the writable primary IS the organiser's
    # own desk, so decision 26.4's refresh moved HEAD and decision 22's detector failed the
    # meeting (F103, then again on meeting 150 — three commits reading `175: README.md — updated`).
    # Same rule as the write-back's roots above, and it has to be stated twice because these are two
    # different writers on one surface — the group's README is still maintained here, which is
    # decision 22's group half in as many words.
    if room_run() and str(desk.get("role") or "private") == "private":
        return None
    # AND NEVER A MOUNT THIS TURN MAY NOT WRITE. `build_mount_set` now demotes every desk the subject
    # owns on a room run (Vexa-ai/vexa#1606), so on a group-less room run `desk_mounts` hands back
    # nothing at all and this function has already returned. This line is what makes that TRUE rather
    # than incidental: regeneration reads the write bit, so a desk that arrives read-only through any
    # route — a room, a viewer's group, a future producer — is not regenerated and not committed.
    # Stated explicitly because `desk_mounts` defaults an ABSENT write key to True, and the rule that
    # matters here is the R-A15 one: a write bit that has to be present to be true cannot be lost.
    if not desk.get("write", False):
        return None
    root = Path(str(desk["path"]))
    if not root.is_dir():
        return None
    home_id = desk.get("id") or workspace_id_of(root) or ""
    listed, feed = [], [{"path": str(root), "id": home_id}]
    for g in groups:
        wid = g.get("id") or workspace_id_of(str(g.get("path") or ""))
        if wid:
            listed.append({"id": wid, "name": g.get("name") or g.get("slug")})
            feed.append({"path": str(g.get("path") or ""), "id": wid})
    try:
        out = desk_readme.update_readme(root, mounts=feed, workspaces=listed, home_id=home_id,
                                        touches=read_touches(root),
                                        name=str(desk.get("name") or ""))
    except OSError as e:
        log.warning("desk README refresh failed: %s: %s", type(e).__name__, e)
        return None
    if out.get("changed"):
        # BY PATHSPEC. `git commit` commits the INDEX, so a bare add+commit here would sweep in
        # whatever the turn's own commit path had staged in the same repo.
        commit_entity(root, [desk_readme.README], subject_path=desk_readme.README,
                      created=False, author=_principal_author())
    return out


def writeback_enabled() -> bool:
    return (os.environ.get("VEXA_WRITEBACK") or "1").strip().lower() not in ("0", "false", "off", "no")


def writeback_min_tokens() -> int:
    """The cheap-turn floor, configurable. A bare "thanks" or "yes" with no tool call learned
    nothing, and a write-back phase on it is a whole model call spent proving that."""
    try:
        return int(os.environ.get("VEXA_WRITEBACK_MIN_TOKENS", "40"))
    except ValueError:
        return 40


def writeback_budget() -> "tuple[int, float]":
    """``(max tool calls, max seconds)`` for the phase — a HARD budget, not a hope.

    The tool cap is what keeps the phase inside the answer's own order of magnitude: each
    `entity_upsert` is a model round trip, and the third digit of pages a turn writes is worth less
    than the person's next message not queueing behind it. Eight is six names plus the MISSING write
    plus slack — and the measure's target is three pages per turn, so a capped phase still scores
    full marks while the raw page count drops. That trade is deliberate and it is reported.

    ⚠ THE SECONDS ARE A DEADLINE, NOT A DURATION, and 22 is not 30 by accident. `bounded` can only
    check the clock BETWEEN events, so a model round trip that starts one second inside the deadline
    runs to completion outside it: the phase overshoots by up to one round trip. Measured on Haiku
    with the deadline at 30 the phase took 35.1s and 37.1s — correctly truncated, and over budget
    anyway. 22 leaves room for the round trip that is already in flight. Interrupting one mid-flight
    would need a thread per turn to buy back six seconds, which is not a trade worth making in a
    worker."""
    def _int(name, default):
        try:
            return int(os.environ.get(name, str(default)))
        except ValueError:
            return default
    return _int("VEXA_WRITEBACK_MAX_TOOL_CALLS", 8), float(_int("VEXA_WRITEBACK_MAX_SECONDS", 22))


#: The two verbs that take a page AWAY from a mounted desk (Vexa-ai/vexa#1621), and the argument
#: each one names the departing page in. `workspace_move`'s wire spelling is `from` (the HTTP body's
#: field), the rig's tool signature spells it `path` because `from` is a Python keyword — both are
#: read, because the same act reaching this loop under two names is exactly how a rule comes to hold
#: on one runner and not the other.
_REMOVAL_TOOLS = {"workspace_delete": ("path",), "workspace_move": ("from", "path")}


def removed_page_slugs(tool: str, args: object) -> set:
    """The entity slugs a removal call takes off the desk — the write-back phase's exclusion list.

    ⚠ WITHOUT THIS THE PHASE UNDOES THE TURN. `writeback_candidates` asks "which names in this turn
    has no mounted desk got a page for" — and a page the turn just DELETED is, by construction, a
    name with no page. So the phase's own next act is to write it back, one beat after the person
    asked for it to go: the founder says *"remove from personal"*, the agent removes it, and the
    bookkeeping puts it back with a fresh dated entry. The name is in `said` because the turn had to
    talk about the page in order to remove it.

    A page MOVED is the same shape from here: it is gone from where it was, and if it landed in a
    workspace this dispatch does not mount, no root will find it either.

    The slug is the file STEM, which is what `entities.known_slugs` compares against — the same
    answer `slugify(name)` gives for the name on the page, because that is how the page was named."""
    if tool.rsplit("__", 1)[-1] not in _REMOVAL_TOOLS or not isinstance(args, dict):
        return set()
    keys = _REMOVAL_TOOLS[tool.rsplit("__", 1)[-1]]
    out = set()
    for k in keys:
        rel = str(args.get(k) or "").strip()
        if not rel:
            continue
        stem = rel.replace("\\", "/").rsplit("/", 1)[-1]
        stem = stem[:-3] if stem.lower().endswith(".md") else stem
        if stem:
            out.add(stem)
    return out


def writeback_candidates(texts, mounts: list[dict] | None = None,
                         removed: "set | None" = None) -> list[str]:
    """THE PRE-PASS — the phase's cheap half, in code, before any model is asked anything.

    Names out of what the turn already produced (the person's message, the agent's answer, the tool
    results), minus everything the mounted desks already have a page for. An empty list means the
    phase has nothing to do, and that is by far the commonest turn: it now costs a regex instead of
    a two-minute model call.

    ``removed`` — the slugs this turn deleted or moved away (`removed_page_slugs`). Filtered AFTER
    `missing_names` rather than passed into it, so the shared entity module keeps one meaning of
    "missing" and this stays a fact about THIS TURN, which is the only place that knows it."""
    from workspaces.shared.entities import missing_names

    # A ROOM RUN DOES NO BOOKKEEPING ON THE SUBJECT'S OWN DESK (decision 22, F103). The post-meeting
    # turn writes ONE shared artefact whose home is the meeting row; `drop_to_attendees` puts it on
    # every desk in the room afterwards, organiser included. Decision 24 ("the agent writes entities
    # as a phase of every turn") is right everywhere else and met this run with nothing in between:
    # the phase authored pages into the organiser's desk after the turn, the commit moved HEAD, and
    # `process_meeting`'s own decision-22 detector failed the meeting and its minutes mail. Both
    # rehearsal states that reach a completed meeting died there.
    #
    # NARROWED, NOT DISABLED. The GROUP desk is the one desk a room run maintains, so it stays a
    # target — and with the organiser's desk demoted for group meetings (dispatch.build_mount_set)
    # the two cases come out right on their own: a group run has exactly one candidate root, a
    # group-less run has none and the phase's fourth gate declines it.
    #
    # The write bit alone cannot express this. On a group-less room run the subject's desk is
    # writable ON PURPOSE — the runtime needs a writable cwd to create `<cwd>/.claude` at all
    # (F59) — so "writable" there means "the process can start", not "the turn may author here".
    #
    # ...and NEVER THE SYSTEM TIERS, room or no room. `_system` is chats, sessions, settings and
    # identity — `desk_mounts` one screen up excludes it from "the desk" for exactly this reason —
    # and `_global` is the organisation's, read-only for everyone but the admin's setup turn. They
    # were reachable here only because both are writable, and the room narrowing would otherwise
    # have made `_system` the LAST root standing on a group-less room run: entity pages authored
    # into the private system tier, which is worse than what this is fixing.
    #
    # DEFAULT FALSE (R-A15). Every other mount consumer reads the bit explicitly; this one
    # defaulted a missing key to writable, so a mount that LOST it — a fake, a future producer,
    # the read-only room mounts of a run with no primary — was silently promoted to a write
    # target for the phase. A write bit that has to be present to be true cannot be lost.
    in_room = bool(room_run())
    roots = [Path(str(m.get("path") or "")) for m in (mounts if mounts is not None else active_mounts())
             if m.get("write") and m.get("path")
             and str(m.get("role") or "private") not in ("system", "global")
             and m.get("slug") not in ("_system", "_global")
             and not (in_room and str(m.get("role") or "private") == "private")]
    if not roots:
        return []
    names = missing_names(roots, [t for t in texts if t])
    if not removed:
        return names
    from workspaces.shared.entities import slugify
    # PREFIX, not equality — the same test `missing_names` makes against the slugs a desk already
    # holds. A name clipped out of prose ("Zenith SI" for "Zenith SIG") slugifies to a prefix of the
    # page's own slug, and a truncated echo of a page just deleted is no more worth writing than the
    # page itself was.
    kept = []
    for n in names:
        s = slugify(n)
        if s and any(str(r).startswith(s) for r in removed):
            continue
        kept.append(n)
    return kept


def should_write_back(prompt: str, tool_calls: int, *, min_tokens: int | None = None,
                      upserts: int = 0, candidates: "list[str] | None" = None) -> bool:
    """Four gates, cheapest first, and three of them cost no model call.

    1. the switch;
    2. **the turn already did it** — a turn that called `entity_upsert` itself (the note step does)
       has already paid for the write-back, and a phase after it is a second model call to discover
       that. This is the gate that removes the phase from exactly the turns that need it least;
    3. cheap on BOTH counts — no tool call AND the person said very little. Either signal alone is a
       turn that can have learned something: a long message carries facts with no tool call, and a
       short one ("who is Olga?") can pull a whole dossier through one. The floor is on the PERSON's
       words, never on the agent's reply;
    4. **nothing to write** — `candidates` empty. Passing `None` skips this gate (the caller has not
       run the pre-pass), which is only the tests and the legacy call shape.
    """
    if not writeback_enabled():
        return False
    if upserts > 0:
        return False
    floor = writeback_min_tokens() if min_tokens is None else min_tokens
    if tool_calls <= 0 and len((prompt or "").split()) < floor:
        return False
    if candidates is not None and not candidates:
        return False
    return True


def bounded(events: Iterator[dict], *, max_tool_calls: int, max_seconds: float) -> Iterator[dict]:
    """Stop the phase at its budget and CLOSE the generator, which kills the harness subprocess.

    A budget the phase is merely told about is a budget it exceeds — the same lesson as the
    grounding gate, one layer down. Closing the generator is what makes it real: `run_harness_turn`
    sees `GeneratorExit` at its yield, and `llm.claude_code._exec_subprocess` now terminates the CLI
    rather than waiting on it forever.

    The cost of stopping early is the phase's own post-turn commit, which never runs. It is a small
    cost by construction: `entity_upsert` commits each page through the endpoint as it goes, and a
    file written by the fallback path is swept up by the next turn's commit."""
    t0 = time.monotonic()
    calls = 0
    gen = iter(events)
    try:
        for ev in gen:
            yield ev
            if ev.get("type") == "tool-call":
                calls += 1
            if calls >= max_tool_calls:
                yield {"type": "writeback-truncated", "reason": "tool-call budget",
                       "tool_calls": calls}
                return
            if time.monotonic() - t0 >= max_seconds:
                yield {"type": "writeback-truncated", "reason": "time budget",
                       "seconds": round(time.monotonic() - t0, 1), "tool_calls": calls}
                return
    finally:
        close_event_stream(gen)


def writeback_events(events: Iterator[dict]) -> Iterator[dict]:
    """The phase's STEP LINES, never its prose. Tool calls and their results pass through tagged
    `phase: "writeback"` so the client can show that bookkeeping happened; text, the artifact tab
    and the terminating `done` are dropped.

    The `artifact` event is dropped for the same reason the text is: it steals the right panel onto
    an entity page the person did not ask to see, one turn after the document they did."""
    try:
        for ev in events:
            t = ev.get("type")
            if t in ("message-delta", "artifact", "done"):
                continue
            yield {**ev, "phase": "writeback"}
    finally:
        # `bounded` is underneath and the CLI subprocess is underneath that: whoever stops reading
        # THIS generator must reach both. See `llm.ports.close_event_stream`.
        close_event_stream(events)


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


# ── THE PERSON'S OWN WORDS, AS THEIR OWN FIELD (F47) ─────────────────────────────────────────────
#
# The control plane marks the boundary between everything it folded in front of a turn and the
# person's actual sentence with this exact comment (``control_plane.api.CONTEXT_SENTINEL``). The
# literal is DUPLICATED here rather than imported: the worker ships in its own image and never
# imports the control plane. ``tests/test_user_text_field.py`` asserts the two literals are
# byte-identical, so a rename on either side is a failing test instead of a silent regression —
# the same discipline ``MACHINERY_MARK`` already carries across its three languages.
CONTEXT_SENTINEL = "<!--vexa:user-input-below-->"


def human_half(prompt: str) -> str:
    """The person's own words inside a composed turn prompt.

    A MACHINE boundary and nothing else: everything up to and including the sentinel is grounding
    the control plane folded in, and the remainder is what the person typed. No sentinel means the
    control plane folded nothing in front of them, so the whole prompt IS their words — which is the
    shape a plain turn takes when no meeting, schedule or workspace context applies, and precisely
    the shape whose history broke on 2026-09-02. Nothing here reads English."""
    i = prompt.rfind(CONTEXT_SENTINEL)
    return prompt[i + len(CONTEXT_SENTINEL):].lstrip() if i >= 0 else prompt


def _prompt_key(composed: str) -> str:
    """The lookup key for one turn: a digest of the EXACT string handed to the harness. The harness
    writes that string into its transcript verbatim (``llm.claude_code`` sends it as the first
    stream-json user message), so the history reader can find this record by hashing what it read —
    without knowing how the prompt was assembled, which preambles were in it, or what it says."""
    return hashlib.sha256(composed.encode("utf-8")).hexdigest()


# One line per turn; the reader loads the file whole, so it is capped. A thread that outlives the
# cap loses its OLDEST records first, and a turn with no record degrades to exactly today's
# behaviour (the terminal's fallback strip) — never to a wrong bubble.
USER_TEXT_KEEP = 400


def _turns_file(chat_root: Path, session: str) -> Path:
    """The per-thread turn sidecar, beside the continuity pointer:
    ``.claude/sessions/<session>.turns.jsonl``. Same FROZEN on-disk contract ``_session_file``
    carries — ``control_plane.workspace_reader.history`` is the reader on the other end."""
    return chat_root / ".claude" / "sessions" / f"{session}.turns.jsonl"


def record_user_text(chat_root: Path, session: str, composed: str, user_text: str) -> None:
    """Write down what the PERSON said on this turn, keyed by the prompt the MODEL was given.

    WHY THIS EXISTS, and why it is a field rather than a better parser. Chat history is read back
    out of the harness transcript, which stores the prompt the CLI was GIVEN: the voice, kg-links,
    mounts, entity-index and global-context preambles from this file, then the control plane's
    grounding, then the sentence. The terminal reconstructed the human half by STRIPPING all of
    that — one cut at the sentinel when the control plane had emitted one, otherwise regexes
    written against the preambles' own wording. Both are derived from text this file owns, and
    nothing checked either. So when the preamble set changed on 2026-09-02, every stored turn in
    the founder's chat rendered as a grey USER bubble containing ``## Referencing knowledge
    (always)``, the mount stack and the write-routing policy, with his own sentence at the bottom.
    Reconstruction by stripping cannot be made safe; it can only be made unnecessary. The worker is
    the last place that still holds the two halves apart, so it writes the human half down here and
    the reader serves it verbatim. The composed prompt is what the model saw; this is what the
    person typed, and the two never have to agree about English again.

    FAIL-SOFT ON PURPOSE. A sidecar that cannot be written costs the turn nothing — history falls
    back to the old strip. Bookkeeping never takes down a turn somebody is waiting on."""
    if not user_text.strip():
        return
    line = json.dumps({"key": _prompt_key(composed), "user_text": user_text,
                       "ts": time.time()}, ensure_ascii=False)
    f = _turns_file(chat_root, session)
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        prior = f.read_text(encoding="utf-8").splitlines() if f.exists() else []
        kept = [ln for ln in prior if ln.strip()]
        kept.append(line)
        if len(kept) > USER_TEXT_KEEP:
            kept = kept[-USER_TEXT_KEEP:]
            f.write_text("\n".join(kept) + "\n", encoding="utf-8")
        else:
            with f.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except OSError as e:
        log.warning("could not record user_text for session=%s (%s) — history will fall back to "
                    "stripping the composed prompt", session, e)


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
# The list is DATA, in this domain: `core/agent/worker/mcp_tools.v1.json`. Everything that used to
# be written in comments here — why `workspace_write` is in it on zero measured calls, why
# `entity_upsert` and the transcript chips are named rather than left to the prefix, and why the
# list degrades safely in both directions — is in that file, beside the values it explains.
VEXA_MCP_TOOLS = tuple(json.loads(
    pathlib.Path(__file__).with_name("mcp_tools.v1.json").read_text(encoding="utf-8"))["tools"])


def room_run() -> str:
    """The meeting this turn is the post-meeting run FOR, or `""` for every other dispatch.

    Stamped by `control_plane.dispatch.build_unit_env` exactly when a room was authorised, so it
    is a POSITIVE signal the platform always emits rather than an inference from the mount shape.
    The mount shape cannot answer this: a room whose other attendees have no desks yet resolves to
    zero `role: "room"` mounts — the small-team case — and "are there room mounts?" would then say
    `no` on a run that is one."""
    return (os.environ.get("VEXA_ROOM_MEETING") or "").strip()


def room_toolbelt(tools: list[str]) -> list[str]:
    """The MCP allow-set for a post-meeting turn: everything except the bot verbs.

    The meeting is OVER. `bot_send`, `bot_stop`, `bot_say`, `bot_schedule` and `bots_running` can
    do nothing useful about a room that has finished, and offering them is not neutral: on
    2026-09-02 the post-meeting agent for uid 133 read the meeting as still live and called
    `bot_stop` four times in one turn, each answered with a bare `{"stopped": false, "status":
    404}` that reads as a transient failure rather than a terminal state (F104). A tool that
    cannot help is a tool that can be looped on.

    Advertised is not the same as callable, and both matter: the harness will not offer what is
    not in `--allowedTools`, so this removes the option rather than relying on the model declining
    it. `bot_stop` itself also learned to answer the state — the two fixes are independent because
    the MCP serves callers this allow-set never reaches."""
    return [t for t in tools if not t.startswith(f"mcp__{VEXA_MCP_SERVER}__bot")]


def _delegation_dir(work: Path) -> "Path | None":
    """The first writable home for the delegation credential, or None.

    Three candidates, in order, each keeping the two properties that matter — the file must not be
    committable, and it must be private to this subject:

      1. ``<cwd>/.claude`` — gitignored by the workspace seed. The normal answer.
      2. the PRIVATE SYSTEM tier's ``.claude`` — read-write by contract (it is where chat
         continuity already anchors), private, and outside every desk the turn may commit.
      3. a per-subject directory under the system temp dir — outside every mount, so no `git add`
         can reach it, and gone when the container is.

    Returning None is the honest floor: a turn with no toolbelt is a turn that will say so when it
    is asked to read a transcript, and the grounding gate then fails LOUDLY. A turn that never
    starts says nothing at all."""
    import tempfile
    candidates = [work / ".claude", _continuity_root(work) / ".claude",
                  Path(tempfile.gettempdir()) / f"vexa-{work.name}" / ".claude"]
    seen: set[Path] = set()
    for cand in candidates:
        if cand in seen:
            continue
        seen.add(cand)
        try:
            cand.mkdir(parents=True, exist_ok=True)
            probe = cand / ".w"
            probe.write_text("")          # mkdir succeeds on an existing dir under a ro mount
            probe.unlink()
            return cand
        except OSError as e:
            log.warning("delegation config dir %s unusable (%s) — trying the next candidate", cand, e)
    return None


def _file_spawn_gap(url: str, token: str) -> None:
    """A toolbelt the dispatch INTENDED and did not get, filed at spawn (ledger F70).

    It is filed here, and not left to the model, for the reason F70 exists: a session with no tools
    cannot call `report_friction`, so the one failure that silences the reporting channel is the one
    it can never report. `spawn_gap` returns None for a turn that was never meant to have a toolbelt,
    so the ordinary un-delegated dispatch files nothing."""
    try:
        rec = spawn_gap(url=url, token=token, config_written=False,
                        session=fallback_session(), subject=os.environ.get("VEXA_OWNER", ""))
        if rec:
            report_friction(rec, subject=os.environ.get("VEXA_OWNER", ""))
    except Exception as e:  # noqa: BLE001 — never worth a turn
        log.warning("friction: could not file the spawn gap (%s)", e)


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

    WHERE it goes has FALLBACKS, because on 2026-09-02 this function killed the process. The cwd was
    a read-only mount, ``mkdir`` raised ``OSError: [Errno 30] Read-only file system``, nothing caught
    it, and the worker exited(1) before the model ever ran — while the caller sat polling for a reply
    that could not come. The mount mode is fixed in ``dispatch.py``; this is the belt, and it is the
    more important half: **the LOCATION of a credential is never worth a turn.** See
    ``_delegation_dir``.
    """
    url = (os.environ.get("VEXA_MCP_URL") or "").strip()
    token = (os.environ.get("VEXA_MCP_DELEGATION_TOKEN") or "").strip()
    if not url or not token:
        _file_spawn_gap(url, token)
        return None, []
    cfg = {"mcpServers": {VEXA_MCP_SERVER: {
        "type": "http", "url": url, "headers": {"Authorization": f"Bearer {token}"},
    }}}
    d = _delegation_dir(work)
    if d is None:
        log.warning("no writable directory for the vexa MCP delegation config — running this turn "
                    "WITHOUT the toolbelt rather than not at all")
        _file_spawn_gap(url, token)
        return None, []
    path = d / "mcp.json"
    path.write_text(json.dumps(cfg))
    try:
        path.chmod(0o600)
    except OSError:  # a store backend that does not carry modes — the attachment still stands
        pass
    return str(path), [f"mcp__{VEXA_MCP_SERVER}",
                       *(f"mcp__{VEXA_MCP_SERVER}__{t}" for t in VEXA_MCP_TOOLS)]


def _mcp_endpoint(mcp_config: str) -> "tuple[str, dict] | None":
    """The ``(url, headers)`` an attached ``.mcp.json`` actually points at, or None.

    Reads the FILE, not a separately-read env var: ``mcp_config`` is the exact attachment about to
    be handed to the harness, so this checks what the turn will really get rather than a
    ``VEXA_MCP_URL`` that could in principle disagree with it (or be unset, on a caller that built
    the file some other way — the delegation seam is not the only writer of this shape, `shared.
    tools.ToolGrant` is another)."""
    try:
        cfg = json.loads(Path(mcp_config).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    servers = cfg.get("mcpServers") if isinstance(cfg, dict) else None
    if not isinstance(servers, dict) or not servers:
        return None
    server = servers.get(VEXA_MCP_SERVER)
    if not isinstance(server, dict):
        server = next((v for v in servers.values() if isinstance(v, dict)), None)
    if not isinstance(server, dict):
        return None
    url = str(server.get("url") or "").strip()
    if not url:
        return None  # a stdio/command server — nothing for an HTTP preflight to dial
    headers = server.get("headers")
    return url, ({str(k): str(v) for k, v in headers.items()} if isinstance(headers, dict) else {})


def _first_sse_json(raw: str) -> "dict | None":
    """The first parseable JSON payload out of an ``event-stream`` body's ``data:`` lines.

    A streamable-HTTP MCP server may answer a POST with a plain JSON body or with SSE — the
    transport lets the server choose per response — so the preflight below has to read a JSON-RPC
    envelope out of either shape without caring which one it got."""
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        chunk = line[len("data:"):].strip()
        if not chunk:
            continue
        try:
            return json.loads(chunk)
        except json.JSONDecodeError:
            continue
    return None


# 5 tries, delays 1+2+4+8 = 15s BETWEEN them — "bounded backoff over ~15s" (F153). The transport is
# STATELESS BY DESIGN (PRD 40.10: a client reconnects), so a preflight `initialize` answered AT
# ALL — a result or a JSON-RPC error, both prove the wire is alive — counts as success; only a
# connection-level failure (refused, timed out, torn down mid-response, not JSON-RPC-shaped) counts
# against the budget.
MCP_PREFLIGHT_DELAYS: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)


def mcp_preflight(url: str, headers: dict, *, delays: tuple[float, ...] = MCP_PREFLIGHT_DELAYS,
                  timeout: float = 3.0,
                  sleep: Callable[[float], None] = time.sleep) -> "tuple[bool, str]":
    """Confirm the delegated MCP server answers `initialize` BEFORE a turn runs on it — the guard
    `spawn_gap`'s own docstring names and could not see from the spawn side: "the harness dropped
    the server after init. That is invisible from here... a `tools unavailable` guard belongs in
    the harness adapter."

    WHY THIS EXISTS (F153, founder hit it live 2026-09-03 ~13:46Z). The control server is stateless
    by design and restarts routinely; each turn is already a FRESH `claude`/`codex` subprocess that
    re-reads `mcp_config` and re-attaches from scratch (`build_argv` / `_mcp_config` both take the
    file path fresh, every call) — so a restart BETWEEN turns should be invisible. It was not: a
    turn's subprocess attached to a server that was mid-restart, got nothing back, and the harness
    silently ran the whole turn with no vexa tools. The model then told the founder its own guess
    ("the workspace-creation tool isn't available in this session anymore") instead of the truth
    ("the control server is not answering right now") — nothing upstream of the model could tell
    the difference, because nothing checked.

    This runs the SAME handshake the harness is about to run — a JSON-RPC `initialize` POST, with
    retries, from the WORKER process, before the harness ever starts — so a still-down server is
    known before a model call burns itself finding out the hard way, and a server that came back is
    confirmed rather than assumed. Returns ``(True, "")`` the moment anything JSON-RPC-shaped
    answers; ``(False, <detail>)`` once the retry budget (default ~15s) is spent. The caller decides
    what a `False` means for the turn — this function only tells the truth about the wire."""
    body = json.dumps({
        "jsonrpc": "2.0", "id": "preflight", "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "vexa-agent-worker-preflight", "version": "1"}},
    }).encode("utf-8")
    detail = "unreachable"
    attempts = len(delays) + 1
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, data=body, method="POST", headers={
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
                **headers,
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                ctype = r.headers.get("content-type", "") or ""
                raw = r.read().decode("utf-8", "replace")
            payload = (_first_sse_json(raw) if "text/event-stream" in ctype
                      else (json.loads(raw) if raw.strip() else None))
            if isinstance(payload, dict) and "jsonrpc" in payload:
                return True, ""
            detail = f"malformed response from {url}: {raw[:200]!r}"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            detail = f"{type(e).__name__}: {e}"
        if i < len(delays):
            sleep(delays[i])
    return False, detail


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
    # session_continuity=False: never read/write the shared chat session — a machinery turn must NOT
    # pollute the user's chat conversation memory.
    resume = _resume_id(chat_root, sess_file, harness) if session_continuity else None
    allowed = allowed_tools or ["Read", "Write", "Edit"]
    # THE PER-TURN MCP GUARD (F153). This turn's `mcp_config` was intended by the caller (a URL was
    # minted); confirm it is actually LIVE before handing it to the harness rather than hoping the
    # fresh subprocess's own attach succeeds. See `mcp_preflight` for the incident this closes.
    # `mcp_config` is intentionally reassigned to None on failure: a confirmed-dead attachment is
    # never handed to the harness — offering it only costs the CLI its own connection timeout for
    # nothing — and the turn runs plainly WITHOUT the toolbelt rather than silently discovering the
    # gap mid-turn.
    intended_mcp = mcp_config is not None
    mcp_ok, mcp_detail, mcp_url = True, "", ""
    if intended_mcp:
        endpoint = _mcp_endpoint(mcp_config)
        if endpoint is None:
            mcp_ok, mcp_detail = False, f"no usable mcp endpoint in {mcp_config}"
        else:
            mcp_url, mcp_headers = endpoint
            # `delays=MCP_PREFLIGHT_DELAYS` is explicit, not the default parameter, ON PURPOSE: a
            # default is bound once at import time, so a test (or a future operator override)
            # patching the module constant would silently fail to reach a call that relied on the
            # default instead of a fresh lookup of the name at call time.
            mcp_ok, mcp_detail = mcp_preflight(mcp_url, mcp_headers, delays=MCP_PREFLIGHT_DELAYS)
        if not mcp_ok:
            log.warning("agent-api worker: vexa MCP preflight failed after retries (%s) — running "
                        "this turn WITHOUT the toolbelt rather than a silent stall", mcp_detail)
            try:
                report_friction(mcp_unreachable(
                    url=mcp_url, detail=mcp_detail, attempts=len(MCP_PREFLIGHT_DELAYS) + 1,
                    session=session or fallback_session(),
                    subject=os.environ.get("VEXA_OWNER", "")),
                    subject=os.environ.get("VEXA_OWNER", ""))
            except Exception as e:  # noqa: BLE001 — a friction report is never worth a turn
                log.warning("friction: could not file the mcp-unreachable gap (%s)", e)
            yield {"type": "mcp-unavailable", "server": VEXA_MCP_SERVER, "detail": mcp_detail}
            mcp_config = None
    # Declare the mount set to the model VERBATIM (WP-A1.1) + the write-routing policy (WP-A1.2), so the
    # agent never guesses where it may read/write. Single-mount turns get no mounts preamble; the
    # kg-links rule ([[wikilinks]] render as actionable entity chips) applies to EVERY turn.
    mounts = active_mounts()
    author = _principal_author()
    extras = _extra_mount_paths(work)
    # THE MODEL IS TOLD, NOT LEFT TO GUESS (F153's second half). A silent toolbelt gap is exactly
    # how the model came to tell the founder a plausible-sounding excuse instead of the truth — so a
    # turn that lost its MCP attachment says so in its own opening context, in words the model can
    # repeat verbatim instead of inventing its own.
    mcp_status_note = "" if mcp_ok else (
        "## Vexa toolbelt unavailable this turn\n\n"
        "The control server did not answer after retries — none of the `mcp__vexa__*` tools are "
        "attached this turn. Do not claim to have created, read, or changed anything through them; "
        "tell the person plainly that the connection is down. It reattaches fresh next turn.\n\n"
    )
    # F162 — the imperative gate goes FIRST, ahead of even the MCP-status note: see its definition
    # for the ledger incident this closes (four unanswered "send bot"s, lost under six preambles'
    # worth of onboarding/propose framing).
    turn_prompt = (imperative_preamble(prompt)
                   + mcp_status_note + voice_preamble() + friction_preamble() + kg_links_preamble(mounts)
                   + page_verbs_preamble()
                   + mounts_preamble(mounts, active_target())
                   + entity_index_preamble(mounts) + timeline_preamble()
                   + global_context_preamble(mounts)
                   + prompt)
    # THE PERSON'S HALF, WRITTEN DOWN SEPARATELY (F47) — see `record_user_text` for why the history
    # reader must never have to find it again by parsing the composed prompt above. Recorded BEFORE
    # the harness runs, because a turn that dies mid-stream still leaves its user turn in the
    # transcript, and that turn is exactly the one that would render as the machinery prompt.
    # `session_continuity=False` writes nothing: such a turn is not a conversation anybody reads back.
    #
    # AN ACT IS NOT A SENTENCE (Vexa-ai/vexa#1588). `human_half` cuts at the sentinel, which on a
    # turn somebody TYPED leaves their words and on a turn they PRESSED leaves the whole composed
    # preset — so Extend recorded its own instruction block as the person's speech and the chat
    # painted it back at them in a grey bubble. `act_label` reads the mark the control plane
    # already wrote and returns the short label instead; the PROMPT is untouched either way.
    #
    # AND A FLOW-DISPATCHED TURN IS NOT A SENTENCE EITHER (Vexa-ai/vexa#1605). Same failure, another
    # caller: `process_meeting` posts a whole instruction block to `/api/chat`, and `human_half`
    # hands all of it back as the person's speech. agent-api now marks such a turn with its flow and
    # step, which `act_label` reads; `composed_label` covers the older shape, where the only thing
    # naming the composer is the `[kind]` its body opens with.
    if session and session_continuity:
        _half = human_half(prompt)
        record_user_text(chat_root, session, turn_prompt,
                         act_label(prompt) or composed_label(_half) or _half)
    # WHICH KIND OF TURN THIS IS (Vexa-ai/vexa#1622). This function is the single funnel every
    # governed turn passes through, and the only place that can see all three signals at once — the
    # job flag (`_job_turn` set it on this thread), the flow mark on the prompt, and the platform's
    # room stamp. The harness reads the result to size its tool-call budget: a chat sentence, an
    # Extend, a whole post-meeting run and a flow step are four different amounts of work and were
    # all billed at 40 calls, which is how the founder's chats stopped dead three times in a row.
    #
    # Set here, not cleared here as a correctness measure: EVERY turn passes this line before it
    # runs, so the mark cannot be stale for anything that reads it. The `finally` below clears it
    # anyway, because a thread-local nobody clears is a thread-local somebody later misreads.
    llm_jobs.mark_turn_kind("job" if llm_jobs.in_job()
                            else "flow" if turn_namespace(prompt) == "flow"
                            else "room" if room_run() else "chat")
    gen = run_harness_turn(work, turn_prompt, harness, allowed_tools=allowed, session=resume, model=model,
                           commit=commit, author=author, extra_mounts=extras, mcp_config=mcp_config)
    first = next(gen, None)
    # A FIRST EVENT THAT IS ALREADY `done.ok=False` means the harness refused the RESUME (an alien or
    # stale session id) — heal it by running the same prompt with no session. `reason` is what says
    # this is NOT that case (F89): a turn that stopped on its own budget also reports ok=False, and
    # re-running it from scratch would burn the budget again and answer no better.
    if (resume and first is not None and first.get("type") == "done"
            and not first.get("ok", True) and not first.get("reason")):
        if sess_file.exists():
            sess_file.unlink()
        # The refused-resume turn is ABANDONED here — reap its CLI now rather than leaving a second
        # harness subprocess to whatever the interpreter does with an unreferenced generator.
        close_event_stream(gen)
        gen = run_harness_turn(work, turn_prompt, harness, allowed_tools=allowed, session=None, model=model,
                               commit=commit, author=author, extra_mounts=extras, mcp_config=mcp_config)
        first = next(gen, None)
    captured: str | None = None
    # THE TURN'S OWN ROUGH EDGES (PRD decision 33 §1). Only the event types the scan reads are kept
    # — a turn's full stream is unbounded and this is a footnote, not a recorder. `turn-truncated`
    # joined them for Vexa-ai/vexa#1622: without it the scan sees only the refusal the budget caused
    # and files a record naming no tool and saying "not run", which is what all four of the
    # founder's auto-filed reports said.
    tool_events: list[dict] = []
    try:
        for ev in (gen if first is None else itertools.chain([first], gen)):
            if ev.get("type") in ("tool-call", "tool-result", "turn-truncated"):
                tool_events.append(ev)
            if ev.get("type") == "done" and ev.get("sessionId"):
                captured = ev["sessionId"]
            if ev.get("type") == "done" and ev.get("reason"):
                # WHAT THE TURN GAVE UP (F89). Before this the budget/trim events had no consumer at
                # all, so a turn that stopped halfway looked in every log exactly like one that
                # finished.
                log.warning("turn incomplete for session=%s: %s", session or "-", ev["reason"])
            if ev.get("type") == "done" and intended_mcp:
                # THE TYPED SIGNAL ON THE TURN'S OWN OUTCOME (F153) — never only a log line. A
                # consumer reading only `done` events (the desk, an SSE relay, a test) can tell a
                # degraded turn from a normal one without re-deriving it from a missing tool-call.
                ev.setdefault("mcp_ok", mcp_ok)
            yield ev
    finally:
        # `itertools.chain` does not forward a close to what it chains, and neither does the `for`.
        # The budget closes THIS generator when the write-back phase runs out; the CLI underneath it
        # must go with it. See `llm.ports.close_event_stream`.
        close_event_stream(gen)
        llm_jobs.mark_turn_kind("")
    # AFTER the stream, never inside it: a report is a footnote to a turn, and a turn must not
    # stall on one. `report` never raises (see worker/friction.py) — this try is the belt.
    try:
        for rec in scan_turn(tool_events, session=session,
                             subject=os.environ.get("VEXA_OWNER", ""), workspace=work.name):
            report_friction(rec, subject=os.environ.get("VEXA_OWNER", ""))
    except Exception as e:  # noqa: BLE001 — a friction report is never worth a turn
        log.warning("friction: the auto-file scan failed (%s)", e)
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
          harness: HarnessPort | None = None, writeback: TurnFn | None = None,
          tools: "list[str] | None" = None, job: TurnFn | None = None,
          jobs_dir: "Path | None" = None, jobs_session: str = "",
          inbox_cursor: "str | None" = None) -> None:
    """Run the entrypoint turn (if any), then serve interactive messages on ``in_topic`` until idle.

    Each turn's UnitEvents are XADD'd to ``out_topic`` (tagged with a turn id), followed by a
    ``turn-complete`` marker. An empty blocking read (idle) returns — the process exits and the
    container is reaped (TTL-on-idle). A ``{"type":"stop"}`` message exits immediately.

    Every turn opens with a ``turn-accepted`` event — the worker's LIVENESS ACK. It flips the UI
    off "Starting agent" the moment the turn is picked up (long before the first model token) and
    is the evidence the dispatcher's warm-delivery watchdog waits on: no accepted event = the
    message was NOT taken (worker exited in the race window) → the dispatcher respawns. A warm
    in-topic message carries a ``nonce`` the ack echoes so the watchdog can match ITS delivery.

    F161 — THE ACK IS FOR THE QUEUE, NOT FOR THE DESK. The write-back phase's actual model call is
    bookkeeping that trails the answer the person already read — it is not part of what the next
    queued message is waiting on. Fired synchronously it used to hold `run_message` open for up to
    its own ~22-37s budget (`writeback_budget`'s own docstring), during which the outer loop below
    could not `xread` the next message at all, let alone ack it — `_ACK_DEADLINE_SEC` (dispatch.py)
    is 10s, so any turn that triggered write-back queued its successor behind a warning that reads
    as "something is wrong" when the truth is "the worker is still finishing the LAST turn's
    paperwork". Fix: `run_message` (below) runs its cheap, model-free write-back PRE-PASS inline —
    it decides in a regex, not a round trip, whether there is anything expensive to do — and only
    when that pre-pass says yes does the model call + desk-refresh + `turn-complete` trio move to a
    background TRAILER, so `run_message` can return immediately and the outer loop is free to
    `xread`, ack and start the NEXT turn's main work while the trailer finishes. Every turn whose
    pre-pass says no (by far most of them) keeps running fully synchronously — unchanged from
    before this fix, byte for byte. Two trailers CAN overlap (a fast turn following a slow
    write-back) — they share `_desk_lock` so the desk mutations (write-back's tool calls,
    `refresh_desk_readme`) never interleave; nothing else about a trailer needs to block the next
    turn. `_join_trailers()` is called before every exit path so a daemon thread killed by
    TTL-on-idle reaping never eats a commit mid-flight.

    `jobs_session` — WHOSE JOBS THESE ARE (Vexa-ai/vexa#1613). The register directory lives under
    the person's continuity root, which every one of their chats shares, so a boot scan that did not
    know its own name reported another conversation's LIVE jobs as restart casualties — into a
    chat that never asked for one — and deleted the records on the way. Every job event now says
    which session owns it and the boot scan takes only its own; see `worker/jobs.JobRunner`.

    `inbox_cursor` — HOW FAR THIS WORKER HAS READ, PUBLISHED (Vexa-ai/vexa#1610). The in-topic is the
    chat's INBOX: everything submitted is XADD'd to it the moment it is submitted, whether or not a
    turn is running, and this loop takes entries off it in order. So "what is still queued" is
    exactly "the entries after my cursor" — and the chat has to be able to ask that on a cold load,
    from another device, after a reload, with nobody streaming anything. This is the key the answer
    is written to (`shared/units.inbox_cursor_key`), by the one process that owns the cursor. Written
    through `stream.set` when the stream has one, so every fake in the tests is unaffected and a
    deployment whose stream cannot hold a key simply keeps the previous behaviour with no inbox view.

    `job` — BACKGROUND JOBS (Vexa-ai/vexa#1584), the same idea taken one step further and applied to
    the WORK rather than to its paperwork. A prompt carrying the job mark never runs here at all:
    `JobRunner` takes it, this loop emits one line and completes the turn, and the act runs on its
    own thread with its own harness session (`job` is built with `session_continuity=False`) while
    the person carries on asking things. `jobs_dir` is the register that lets a restart report the
    jobs it killed. Both optional: with no `job` turn injected there are no jobs, a marked prompt
    runs inline exactly as it does today, and nothing else in this function changes.
    """
    _desk_lock = threading.Lock()
    _trailers: list[threading.Thread] = []

    def _took(cursor: list, entry_id: str) -> None:
        """Advance the cursor AND say where it now is (Vexa-ai/vexa#1610).

        ONE CALL, THREE SITES — the outer loop, the job drain and the injection drain each move the
        cursor, and a site that moved it without publishing would leave the chat showing a queued row
        for something that had already run. Fail-soft: the inbox VIEW is furniture, the turn is what
        the person is waiting for."""
        cursor[0] = entry_id
        setter = getattr(stream, "set", None) if inbox_cursor else None
        if setter is None:
            return
        try:
            setter(inbox_cursor, entry_id)
        except Exception as e:  # noqa: BLE001
            log.warning("could not publish the inbox cursor (%s: %s) — queued rows may lag", type(e).__name__, e)

    # BACKGROUND JOBS (Vexa-ai/vexa#1584). A marked act — Create, Extend, or anything the model
    # hands to `spawn_job` — does not run here at all: the runner takes it, this loop emits one line
    # and moves straight on to the next message. Off entirely when the caller injects no job turn,
    # which is every test and every deployment that has not wired one.
    _jobs = None if job is None else worker_jobs.JobRunner(
        emit=lambda ev: stream.xadd(out_topic, {"event": json.dumps(ev)}),
        turn=job, register_dir=jobs_dir, session=jobs_session)

    def _spawn_from_tool(kind: str, target: str, brief: str) -> "tuple[bool, str]":
        """`spawn_job`'s answer to the model — and since Vexa-ai/vexa#1610 it is always an ACCEPTED
        one: an act on a busy target queues behind it, an identical brief joins it. The model is
        told which of the three happened so it can say so; none of them is a failure."""
        ev = _jobs.spawn(kind, target, brief)          # type: ignore[union-attr]
        t = ev.get("type")
        if t == "job-collapsed":
            return True, (f"that exact job is already running on {target} ({ev.get('job_id')}) — "
                          f"this joined it rather than doing the same work twice; it posts one line "
                          f"when it lands")
        if t == "job-queued":
            return True, (f"queued behind the job already running on {target} ({ev.get('job_id')}). "
                          f"It runs next, on its own brief, and posts its own line.")
        return True, (f"started as a background job ({ev.get('job_id')}). Answer whatever else the "
                      f"person asked — the job posts its own line when it lands.")

    if _jobs is None:
        # Cleared, not left standing: the spawner is process-global, so a serve() with no job turn
        # must not inherit a previous one's runner.
        llm_jobs.set_spawner(None)
    else:
        # Whatever the last process was running when it died is reported once, here, and forgotten.
        _jobs.cancelled_at_boot()
        # The harness's own `spawn_job` reaches the SAME runner: one register, one refusal rule,
        # one set of events, whichever half of the product asked.
        llm_jobs.set_spawner(_spawn_from_tool)

    def _join_trailers() -> None:
        """Block until every in-flight trailer has finished — called on every way `serve()` can
        return, so idle-reap or a `stop` message can never kill a write-back mid-commit to save a
        few seconds nobody asked to save."""
        while _trailers:
            _trailers.pop().join()

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
                # A MARKED ACT IS A JOB, NEVER STEERING (Vexa-ai/vexa#1594). Injected, its mark would
                # reach the running model as prose: the act would never spawn, the person would read
                # their own plumbing, and the press would look like it worked. `_drain_jobs` takes
                # these — leave it, and everything behind it, where it is.
                if read_job_mark(text) is not None:
                    return
                if not inject(text):
                    return  # no active stdin — leave queued
                _took(cursor, entry_id)
                # satisfy the dispatcher's warm-delivery watchdog: the injected message WAS taken
                if msg.get("nonce"):
                    stream.xadd(out_topic, {"event": json.dumps({"type": "turn-accepted", "nonce": msg["nonce"], "injected": True})})
                stream.xadd(out_topic, {"event": json.dumps({"type": "user-injected", "text": text})})

    # A JOB DOES NOT WAIT FOR THE TURN IN FRONT OF IT (Vexa-ai/vexa#1594).
    #
    # Founder walk 2026-09-06: *"extend this page button does not work when chat is working"*. The
    # act reached the in-topic and then sat in it. This loop is ONE thread and `run_message` holds it
    # for the whole of a chat turn — so an act whose entire contract is that it runs BESIDE the chat,
    # on its own thread with its own harness session (`llm/JOBS.md`), was not even READ until the
    # turn it happened to land behind had finished.
    #
    # So between the running turn's output events the in-topic is drained for MARKED messages, and
    # only those: the job spawns now, its turn is acknowledged and completed now, and the model that
    # is mid-answer is not touched at all. The cursor is ONE position, so the drain stops at the
    # first entry it may not take — consuming past an ordinary message to reach an act behind it
    # would swallow the ordinary message, which is the failure this whole issue is about.
    #
    # Unlike `_drain_inject` this is not behind a flag. Injection changes what the running turn is
    # being told and a deployment may reasonably not want it; a job touches nothing the turn owns.
    def _drain_jobs(cursor: list) -> None:
        if _jobs is None:
            return
        nonlocal n
        while True:
            try:
                resp = stream.xread({in_topic: cursor[0]}, count=8, block=None)
            except Exception:  # noqa: BLE001 — a drain that cannot read simply drains nothing
                return
            took = False
            for _name, entries in resp or []:
                for entry_id, fields in entries:
                    try:
                        msg = json.loads(fields.get("turn", "{}"))
                    except ValueError:
                        return
                    if msg.get("type") == "stop":
                        return          # stopping is the outer loop's, and so is everything after it
                    if read_job_mark(msg.get("prompt", "")) is None:
                        return          # an ordinary message — the outer loop's turn to take it
                    _took(cursor, entry_id)
                    took = True
                    n += 1
                    # `cursor=None`: this call takes the job branch below and returns without
                    # streaming anything, so it neither drains again nor re-enters here.
                    run_message(msg.get("prompt", ""), f"t{n}", nonce=msg.get("nonce"))
            if not took:
                return

    def run_message(prompt: str, turn_id: str, nonce: str | None = None, cursor: list | None = None) -> None:
        ack: dict = {"type": "turn-accepted", "turn_id": turn_id}
        if nonce:
            ack["nonce"] = nonce
        stream.xadd(out_topic, {"event": json.dumps(ack)})
        # A MARKED ACT NEVER REACHES THE MODEL ON THIS TURN. The acknowledgement is composed by the
        # runner, not asked for — a turn that had to ask a model for its own "I'll say when it's
        # there" would be the two-minute wait it exists to remove, at a tenth of the length. So:
        # spawn (or refuse), say the one line, complete. The job runs on its own thread from here.
        asked = read_job_mark(prompt) if _jobs is not None else None
        if asked is not None:
            kind, target, brief = asked
            ev = _jobs.spawn(kind, target, brief, turn_id=turn_id)   # emits job-started/job-refused
            stream.xadd(out_topic, {"event": json.dumps(
                {"type": "message-delta", "text": ev.get("line") or "", "turn_id": turn_id})})
            stream.xadd(out_topic, {"event": json.dumps(
                {"type": "turn-complete", "turn_id": turn_id})})
            return
        tool_calls, upserts = 0, 0
        # What the phase's pre-pass reads: the person's message and the agent's answer, and NOT the
        # tool results.
        #
        # ⚠ THE TOOL RESULTS ARE NOT AVAILABLE HERE, and the version that thought they were invented
        # people. What reaches this seam is `llm.claude_code._short(content, 80)` — an 80-character
        # PREVIEW — so a name straddling the cut arrives as a fragment. Measured on a second turn
        # over a populated desk, the pre-pass proposed "James Spadaf", "James Spad", "Technical
        # Stee" and "DNA TSC Inaugural Meetin": none has a page, none ever would, and each one
        # dragged a model call it was supposed to prevent — 2 of 2 turns, exactly the gate failing
        # open. A truncated string is not a source of names. It may confirm one; it may never
        # introduce one, and confirmation buys nothing the complete text has not already given.
        said: list[str] = [prompt]
        # WHAT THIS TURN TOOK AWAY (Vexa-ai/vexa#1621) — the write-back phase must not put it back.
        # Recorded on the CALL (that is where the path is) and only counted on a SUCCESSFUL result,
        # the same success-only discipline `llm.claude_code` applies to every other event it derives
        # from a tool: a refused delete removed nothing, and suppressing a page for it would cost a
        # write nobody asked to lose.
        pending_removals: dict[str, set] = {}
        removed_slugs: set = set()
        for ev in turn(prompt):
            t = ev.get("type")
            if t == "tool-call":
                tool_calls += 1
                tool_name = str(ev.get("tool") or "")
                if tool_name.endswith("entity_upsert"):
                    upserts += 1
                gone = removed_page_slugs(tool_name, ev.get("args"))
                if gone:
                    pending_removals[str(ev.get("callId") or "")] = gone
            elif t == "tool-result":
                gone = pending_removals.pop(str(ev.get("callId") or ""), None)
                if gone and ev.get("ok"):
                    removed_slugs |= gone
            elif t == "message-delta" and ev.get("text"):
                said.append(ev["text"])
            stream.xadd(out_topic, {"event": json.dumps({**ev, "turn_id": turn_id})})
            if cursor is not None:
                _drain_jobs(cursor)
                _drain_inject(cursor)
        # THE WRITE-BACK PHASE (decision 24.2). It runs AFTER the answer has streamed — the person
        # is already reading — and before `turn-complete`, so its tool calls land as step lines on
        # the turn they belong to instead of on nothing. A phase that raises must not lose the
        # turn: the answer is delivered either way, and bookkeeping is not worth a dead session.
        #
        # THE PRE-PASS RUNS FIRST AND IS FREE. Only a turn that touched a name no desk has a page
        # for reaches a model at all; every other turn now costs a regex where it used to cost two
        # minutes of worker time with the person's next message queued behind it.
        # F70 — THE TURN THAT REFUSED A TOOL IT WAS HOLDING. Runs before the write-back phase and
        # before `turn-complete`, so the corrected answer reaches the person on the same turn rather
        # than as a second message they have to read as a retraction.
        #
        # ONCE, by construction: there is no loop here, and the corrected turn is streamed inline
        # rather than fed back through the detector. A correction that produces another refusal is a
        # real problem to file, not a loop to run. It fires only when the tool is actually in this
        # session's list — with the tool absent the refusal was true and the turn was right.
        if tools:
            try:
                _refused = disbelieved_capability(prompt, "".join(said), tools)
            except Exception:  # noqa: BLE001 — a detector must never cost a delivered answer
                _refused = None
            if _refused:
                log.warning("f70: the turn refused %s while holding it — correcting once", _refused)
                try:
                    report_friction({
                        "kind": "capability-hallucination", "tool": _refused,
                        "what": f"the turn said it lacked {_refused} with the tool in its list",
                        "prompt": prompt[:400], "reply": "".join(said)[:400],
                    }, subject=os.environ.get("VEXA_OWNER", ""))
                except Exception as e:  # noqa: BLE001
                    log.warning("f70: could not file the friction record (%s)", e)
                # The correction is ONE LINE and the original request follows it verbatim: the turn
                # failed on a belief about itself, not on understanding what was asked.
                for ev in turn(f"You do have `{_refused}`. Use it now.\n\n{prompt}"):
                    if ev.get("type") == "message-delta" and ev.get("text"):
                        said.append(ev["text"])
                    stream.xadd(out_topic, {"event": json.dumps({**ev, "turn_id": turn_id})})

        # F161 fix — see the module docstring above `_join_trailers`. THE PRE-PASS DECIDES WHETHER
        # THERE IS ANYTHING EXPENSIVE TO DEFER, and it is cheap by construction (`writeback_candidates`'s
        # own docstring: "a regex... where it used to cost two minutes of worker time") — no model
        # call happens here, so it runs INLINE, on the turn that is about to ack the next message
        # either way. Only when it decides write-back's actual model call WILL run does the rest of
        # the phase move to a background trailer; every other turn (by far the common case) keeps
        # running fully synchronously, byte-for-byte the pre-fix behaviour, which is what keeps
        # `test_entrypoint_then_interactive_then_idle`-style ordering assumptions intact for the
        # turns that were never the problem.
        run_writeback = False
        candidates: list[str] = []
        # DECISION 22 — a post-meeting run in the room writes NOTHING to the organiser's desk: the
        # report is the reply, and `drop_to_attendees` is the one writer of desks. The write-back
        # phase used to run here regardless, upserting every name the transcript mentioned onto
        # the desk (four pages on 2026-09-06), and the flow's own guard then failed the step for
        # exactly that. `room_run()` is the platform's positive signal for such a run.
        if writeback is not None and not room_run():
            try:
                if should_write_back(prompt, tool_calls, upserts=upserts):
                    candidates = writeback_candidates(said, removed=removed_slugs)
                run_writeback = should_write_back(prompt, tool_calls, upserts=upserts, candidates=candidates)
            except Exception as e:  # noqa: BLE001 — the pre-pass must never cost the turn either
                log.warning("write-back pre-pass failed on %s: %s: %s", turn_id, type(e).__name__, e)
                run_writeback = False

        def _finish_desk_and_complete() -> None:
            """Decision 26.4's refresh + `turn-complete` — the part every turn does regardless of
            whether write-back itself ran."""
            try:
                refresh_desk_readme()
            except Exception as e:  # noqa: BLE001
                log.warning("desk README refresh failed on %s: %s: %s", turn_id, type(e).__name__, e)
            # HOW MANY STEPS THE TURN TOOK, on the turn's own status event (Vexa-ai/vexa#1622).
            # This count already existed — it is what gates the write-back phase — and was thrown
            # away, so the only step count anywhere was one each browser derived for itself by
            # counting `tool-call` events off the stream. A server-side number is what lets a rail,
            # a second device or a reader of the log say how much work a turn did, and it is the
            # same number the budget was measured against.
            stream.xadd(out_topic, {"event": json.dumps(
                {"type": "turn-complete", "turn_id": turn_id, "steps": tool_calls})})

        if not run_writeback:
            # THE FAST PATH — no thread, no lock, no deferral: this is nearly every turn.
            _finish_desk_and_complete()
            return

        # THE TRAILER — only a turn whose write-back is actually about to spend a model round trip
        # (up to the ~37s `writeback_budget` docstring measured) takes this path. `run_message`
        # returns as soon as the thread starts, so the outer loop is free to `xread`/ack/start the
        # NEXT queued message right now rather than after this turn's paperwork. Two trailers CAN
        # overlap (a fast turn following a slow write-back) — `_desk_lock` serializes the desk
        # mutations (write-back's tool calls, `refresh_desk_readme`) between them; nothing else
        # about a trailer needs to block the next turn.
        def _trailer() -> None:
            with _desk_lock:
                try:
                    calls, secs = writeback_budget()
                    for ev in writeback_events(bounded(writeback(candidates),
                                                       max_tool_calls=calls, max_seconds=secs)):
                        stream.xadd(out_topic, {"event": json.dumps({**ev, "turn_id": turn_id})})
                except Exception as e:  # noqa: BLE001
                    log.warning("write-back phase failed on %s: %s: %s", turn_id, type(e).__name__, e)
                _finish_desk_and_complete()

        _trailers[:] = [t for t in _trailers if t.is_alive()]  # prune before the list grows forever
        th = threading.Thread(target=_trailer, daemon=True, name=f"trailer-{turn_id}")
        _trailers.append(th)
        th.start()

    # SKIP THE ENTRYPOINT'S OWN COPY — AND NOTHING ELSE.
    #
    # The dispatcher pre-delivers every chat message to the in topic BEFORE asking the runtime to
    # spawn, so on a COLD spawn the entrypoint prompt is ALSO sitting in the stream. Exactly one
    # entry must be skipped: the copy of the turn we are about to run as `t0`.
    #
    # ⚠ It used to anchor at the boot-time TAIL, which skips everything present at boot. Correct
    # when there is one message; silently wrong when there is more than one. Two rapid sends to a
    # cold session both returned 200, both landed in the stream, and only the entrypoint ran — the
    # second was discarded by the anchor as if it were the duplicate. Measured 2026-09-02 on a
    # scratch session: 2 entries in, one `turn-accepted` out.
    #
    # The dispatcher now stamps the delivery nonce on BOTH copies, so the duplicate is identifiable
    # rather than merely recent. Anchor at 0-0, drop the entry whose nonce matches the entrypoint,
    # and queue the rest to run in arrival order after it. No nonce (a session-only start, an older
    # dispatcher) falls back to the tail anchor — the previous behaviour, unchanged.
    entry_nonce = ((start.get("entrypoint") or {}).get("nonce") or "") if start else ""
    pending: list[str] = []          # boot-time messages that are NOT the entrypoint's copy
    last = "$"
    xrevrange = getattr(stream, "xrevrange", None)
    xrange_ = getattr(stream, "xrange", None)
    if entry_nonce and xrange_ is not None:
        try:
            present = xrange_(in_topic) or []
            for entry_id, fields in present:
                last = entry_id
                msg = json.loads(fields.get("turn", "{}"))
                if msg.get("nonce") == entry_nonce:
                    continue          # the entrypoint's own copy — it runs as t0 below
                text = msg.get("prompt", "")
                if text:
                    pending.append(text)
        except Exception:  # noqa: BLE001 — never a boot blocker; fall back below
            pending, last = [], "$"
    if last == "$" and xrevrange is not None:
        try:
            tail = xrevrange(in_topic, count=1)
            last = tail[0][0] if tail else "0-0"
        except Exception:  # noqa: BLE001 — tail anchoring is an upgrade, never a boot blocker
            last = "$"

    cursor = [last]  # shared with _drain_inject: mid-turn-consumed entries advance it
    # THE BOOT ANCHOR IS A TAKE (Vexa-ai/vexa#1610). Everything at or before it either ran as the
    # entrypoint or is queued below as `pending` — either way this worker has it, so the inbox view
    # must not go on calling it pending. Published before the first turn runs, so a chat that loads
    # while the entrypoint is still working already reads the truth.
    if last not in ("$", "0-0"):
        _took(cursor, last)
    first = start_prompt(start)
    n = 0
    if first:
        run_message(first, "t0", cursor=cursor)
    # …then everything that was already waiting, in the order it arrived. These are turns somebody
    # sent and got a 200 for; the only thing that made them disappear was the anchor.
    for text in pending:
        n += 1
        run_message(text, f"t{n}", cursor=cursor)

    while True:
        resp = stream.xread({in_topic: cursor[0]}, count=1, block=idle_ms)
        if not resp:
            # A JOB IS A THREAD IN THIS PROCESS, so an idle reap under one is a kill. Keep serving
            # while any job runs — the loop is doing nothing anyway, and the alternative is a page
            # that never gets written because nobody spoke for two minutes.
            if _jobs is not None and _jobs.busy():
                continue
            _join_trailers()  # a trailer still writing back must finish before the container reaps
            if _jobs is not None:
                _jobs.join_all()
            return  # idle → exit 0 → container reaped
        for _name, entries in resp:
            for entry_id, fields in entries:
                _took(cursor, entry_id)
                msg = json.loads(fields.get("turn", "{}"))
                if msg.get("type") == "stop":
                    _join_trailers()
                    if _jobs is not None:
                        _jobs.join_all()
                    return
                n += 1
                run_message(msg.get("prompt", ""), f"t{n}", nonce=msg.get("nonce"), cursor=cursor)


def main() -> None:  # pragma: no cover — the container entrypoint (wired in tests via serve())
    import redis

    work = Path(os.environ.get("VEXA_WORKSPACE_PATH", "/workspace"))
    model = os.environ.get("VEXA_AGENT_MODEL") or None
    # Boot preflight (WS1b): if a credential prefix and its base-url host obviously disagree, log a
    # loud warning NOW — before the first call — so a misconfigured harness is visible at container
    # start, not only as a runtime 401.
    _warn = preflight_provider_guard()
    if _warn:
        log.warning("agent-api worker: %s", _warn)
    client = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    out_topic = os.environ["VEXA_UNIT_OUT_TOPIC"]
    idle_ms = int(os.environ.get("VEXA_IDLE_TIMEOUT_SEC", "120")) * 1000

    # There is ONE worker mode. A `VEXA_TRANSCRIPT_STREAM` dispatch used to take a second branch here
    # — the live meeting COPILOT: tail `tc:meeting:{row}`, run a completion beat every N segments,
    # XADD "cleaned" notes onto `proc:meeting:{row}`, and write a transcript file into the workspace.
    # PRD decision 34 removed it: the product runs no model calls of its own beside the agent, and
    # nothing dispatches this kind any more (transcription_watcher stopped arming it). What is left
    # is the chat / routine / event turn: run the entrypoint, then serve interactive messages.
    #
    # Research-capable toolset: WEB search/fetch + the workspace tools. Writes are committed by
    # run_harness_turn. Override with VEXA_CHAT_TOOLS (comma-separated).
    chat_tools = (os.environ.get("VEXA_CHAT_TOOLS")
                  or "Read,Write,Edit,Glob,Grep,Bash,WebSearch,WebFetch,spawn_job").split(",")
    session = os.environ.get("VEXA_CHAT_SESSION") or DEFAULT_CHAT_SESSION
    # The delegated vexa MCP (meetings, transcripts, workspaces) — attached ONLY when the dispatcher
    # minted a token for this dispatch. Attaching the server is not enough: `--strict-mcp-config`
    # scopes WHICH servers exist, `--allowedTools` scopes what the model may CALL, so the server id
    # must enter the allow-set too or every tool call would stall on a permission prompt that no
    # human is there to answer.
    mcp_cfg, mcp_tools = mcp_delegation_config(work)
    room = room_run()
    if room:
        mcp_tools = room_toolbelt(mcp_tools)
    if mcp_cfg:
        chat_tools = chat_tools + mcp_tools
        log.info("agent-api worker: vexa MCP attached for owner=%s (delegated, scoped, short-lived)"
                 "%s", os.environ.get("VEXA_OWNER"),
                 f" — post-meeting room {room}, bot verbs withheld" if room else "")
    # One harness instance owns the whole warm worker lifetime. That makes the steering handle
    # instance-scoped (Codex JSON-RPC process / Claude stdin) instead of a vendor-global mailbox.
    import worker.worker as _w
    chat_harness: HarnessPort = getattr(_w, "harness_factory", harness_from_env)()
    harness_warning = chat_harness.preflight()
    if harness_warning:
        log.warning("agent-api worker: %s", harness_warning)

    def _job_turn(brief: str):
        """A BACKGROUND JOB'S TURN, on the job's own thread (Vexa-ai/vexa#1584, #1613).

        Two lines of difference from a chat turn, and both are about ownership:

        · ``session_continuity=False`` — a job runs its OWN harness session. It does not resume the
          conversation, does not move the continuity pointer and does not append to the transcript
          the history reader serves. Two turns writing one transcript is the same one-writer failure
          as two turns writing one page, and on `claude-code` a second ``--resume`` of a live
          session is not a supported shape at all. The price — the job's two lines are live-only —
          is stated in ``llm/JOBS.md`` rather than discovered.

        · ``mark_job_thread`` — set INSIDE the generator body, which runs on the first ``next()``
          and therefore on the thread ``JobRunner._run`` is iterating from. That is the whole point:
          the chat turn running beside this one in the same process must keep the per-turn budget,
          so the flag cannot be a process-wide env var. The harness reads it and gives the job a
          budget sized for an act rather than for a sentence."""
        llm_jobs.mark_job_thread(True)
        try:
            yield from run_turn_over_workspace(
                work, brief, model=model, allowed_tools=chat_tools, session=session,
                session_continuity=False, mcp_config=mcp_cfg, harness=chat_harness)
        finally:
            llm_jobs.mark_job_thread(False)

    serve(
        client, out_topic=out_topic, in_topic=os.environ["VEXA_UNIT_IN_TOPIC"],
        turn=lambda prompt: run_turn_over_workspace(work, prompt, model=model,
                                                    allowed_tools=chat_tools, session=session,
                                                    mcp_config=mcp_cfg, harness=chat_harness),
        # SAME session on purpose: the phase has to see what the turn just saw, and a fresh
        # session would have to be told the whole conversation to ask one bookkeeping question.
        # Small budget by TOOLSET rather than by a step cap the harness does not expose.
        #
        # A ROOM RUN REACHES THIS PHASE WITH NOTHING TO DO, by construction rather than by a switch
        # here: `writeback_candidates` refuses the subject's own desk as a target while a room is
        # open (F103), so the pre-pass returns an empty list and gate 4 declines. On a `#group:`
        # meeting the group's desk IS a legitimate target and the phase still runs against it.
        # `active_mounts()` PASSED EXPLICITLY (F196/F198/F200) — the same call `writeback_
        # candidates` used moments earlier to decide which desk(s) had anything missing, so the
        # workspace this prompt names cannot silently be a second, disagreeing read of it.
        writeback=lambda candidates: run_turn_over_workspace(
            work, writeback_prompt(candidates, active_mounts()), model=model,
            allowed_tools=[*WRITEBACK_TOOLS, *mcp_tools], session=session,
            mcp_config=mcp_cfg, harness=chat_harness),
        # A BACKGROUND JOB'S TURN, and the one line of difference that matters:
        # `session_continuity=False`. A job runs its OWN harness session — it does not resume the
        # conversation, does not move the continuity pointer and does not append to the transcript
        # the history reader serves. Two turns writing one transcript is the same one-writer failure
        # as two turns writing one page, and on `claude-code` a second `--resume` of a live session
        # is not a supported shape at all. The price — the job's two lines are live-only — is stated
        # in `llm/JOBS.md` rather than discovered.
        # …and A JOB IS NOT A TURN (Vexa-ai/vexa#1613). The mark is set INSIDE the generator, so it
        # is set on the thread that iterates it — the job's own thread — and the chat turn
        # running beside it in this same process keeps the per-turn budget it always had. The
        # harness reads it (`llm/jobs.in_job`) to pick a budget that fits an act rather than a
        # sentence: the founder's OeNB job ran 72 steps and then died on a 40-call turn budget.
        job=_job_turn,
        # The register that makes "a restart cancels them and the chat is told" true. It sits beside
        # the session pointers, under the private continuity root, which is already outside the
        # workspace commit — and is therefore SHARED by every chat this person has, which is why
        # each record and each event carries the session that owns it.
        jobs_dir=_continuity_root(work) / ".claude" / "jobs",
        jobs_session=session,
        # WHERE THIS WORKER SAYS HOW FAR IT HAS READ (Vexa-ai/vexa#1610). Derived from the in-topic
        # rather than handed in as a second environment variable: both sides can already compute it,
        # and a fact spelled in two places is one rename away from a reader looking at a key nobody
        # writes and honestly reporting an empty inbox.
        inbox_cursor=shared_units.inbox_cursor_key(
            shared_units.unit_of_topic(os.environ["VEXA_UNIT_IN_TOPIC"])),
        start=json.loads(os.environ.get("VEXA_START", "{}")), idle_ms=idle_ms,
        harness=chat_harness,
        # What this session can actually call — the F70 detector's third condition, and the
        # only one that makes acting on a refusal safe.
        tools=chat_tools,
    )
