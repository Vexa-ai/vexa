"""meeting_steering — per-STATE preambles for meeting-focused chat turns (design-spec
meeting-lifecycle-v2, W4).

A chat grounded in a meeting behaves differently by the meeting's lifecycle phase:

  prep  (intent: idle/scheduled)      — no transcript exists; steer toward preparation
        (agenda, attendee research, a brief written into the bound workspace).
  live  (requested→active→stopping)   — fold the live transcript; answer from it.
  post  (completed/failed/stopped)    — fold the PROCESSED notes (cleaned transcript);
        steer toward recap, decisions, action items, follow-ups.

The templates below are the hardcoded FALLBACK. The governed control surface is the
platform-operated ``_global`` workspace (the same repo `system_mounts.global_mount`
serves read-only into every worker turn): an operator file at

    {VEXA_GLOBAL_SYSTEM_WORKSPACE_PATH}/agents/meeting-lifecycle.md

with ``## prep`` / ``## live`` / ``## post`` / ``## schedule`` / ``## workspace_focus`` H2
sections overrides the matching template
— edit the one _global repo and every agent's chat behavior changes next turn, no deploy.
FAIL LOUD, DEGRADE SAFE: a malformed override (unknown placeholder, unreadable file)
logs a warning and falls back to the built-in template; it never breaks a chat turn.

Templates are ``str.format``-style over a FIXED placeholder set (extra placeholders in an
override raise at format time → caught → loud log + fallback):
  prep/live/post:   {title} {when} {platform} {native} {workspace}
  schedule:         (none — a fixed steering sentence after the <schedule> block)
  workspace_focus:  {name} {slug} {purpose} {readme}
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger("agent_api.meeting_steering")

# ── phase model (mirrors the meetings-domain lifecycle; terminal sends the raw status) ──
PREP_STATUSES = {"idle", "scheduled"}
LIVE_STATUSES = {"requested", "joining", "awaiting_admission", "active", "needs_help", "stopping"}
POST_STATUSES = {"completed", "failed", "stopped"}


def phase_for(status: "str | None") -> str:
    """Map a raw meeting status to a chat-grounding phase. ABSENT/unknown status → "live":
    a legacy client that sends no status keeps today's exact behavior (live fold)."""
    s = (status or "").strip().lower()
    if s in PREP_STATUSES:
        return "prep"
    if s in POST_STATUSES:
        return "post"
    return "live"


# ── fallback templates (the _global override file replaces these per section) ──────────
DEFAULT_TEMPLATES: dict[str, str] = {
    "prep": (
        "You are helping the user PREPARE for the upcoming meeting \"{title}\" ({platform}/{native})"
        "{when}. The meeting has not happened yet — there is no transcript. {workspace}"
        "Default moves: draft an agenda, research the attendees and companies in the knowledge "
        "workspace, and write or update a one-page prep brief. FIRST check kg/entities/meeting/ for "
        "an existing note or brief for this meeting — if one exists you are CONTINUING that brief: "
        "read it and fold new information in, never start over or drift to generic assistance. "
        "Research PROACTIVELY: when workspace knowledge is thin, search public sources (the web) for "
        "the attendees, their organizations, and the topic without waiting to be asked — and mark "
        "what is public-sourced vs. confirmed by the user. Ground every claim in workspace "
        "knowledge or named public sources; say plainly when you don't have prior context. Seeded EXAMPLE entities "
        "(frontmatter `example: true` — e.g. the shipped Jane Liu / Acme Corp demo) exist only to "
        "show how knowledge is kept: never cite them as records or count them as prior context. "
        "If you don't yet know who the user is (no `self: true` person entity), ask — and invite "
        "them to share a LinkedIn profile or a short intro so the brief and future knowledge are "
        "tailored to them; if their calendar is synced, infer what you can from their schedule "
        "(recurring groups, attendee domains) and confirm it instead of starting blank.\n\n"
    ),
    "live": (
        "You are assisting in a live meeting ({platform}/{native}). Its live transcript so far is "
        "below — answer the user's question from it. Don't paste the transcript back verbatim.\n\n"
        "<transcript>\n{transcript}\n</transcript>\n\n"
    ),
    "post": (
        "The meeting \"{title}\" ({platform}/{native}) has ended{failed}. Its {source} is below — "
        "answer from it. Default moves: recap what was decided, extract action items with owners, "
        "and draft follow-ups on request. Don't paste the transcript back verbatim.\n\n"
        "<transcript>\n{transcript}\n</transcript>\n\n"
    ),
    # Ambient terminal-state digest (context bundle): one steering sentence AFTER the
    # <schedule> block build_schedule_digest rendered. No placeholders.
    "schedule": (
        "The user's meeting schedule is in <schedule> above (times are in their timezone). "
        "Resolve phrases like \"my next meeting\" or \"today\" from it and refer to meetings by "
        "title. It is a compact digest — for depth, read the meeting's notes files or ask.\n\n"
    ),
    # Workspace focus (the manage panel is the active tab): purpose + README head folded below.
    "workspace_focus": (
        "The user is looking at the workspace \"{name}\" ({slug}).{purpose} Its README head is "
        "below — ground answers about this workspace in it and in the workspace's files.\n\n"
        "<readme>\n{readme}\n</readme>\n\n"
    ),
}

# Workspace focus when there's no README — honest, still names the workspace.
NO_README_WORKSPACE_FOCUS = (
    "The user is looking at the workspace \"{name}\" ({slug}).{purpose} It has no README yet — "
    "ground answers in the workspace's files, and say when context is missing.\n\n"
)

# Live/post when the stream holds nothing — honest, never fabricated (fail loud).
NO_TRANSCRIPT_LIVE = (
    "You are assisting in a live meeting ({platform}/{native}), but no transcript has been "
    "captured yet. Tell the user the meeting has no transcript yet.\n\n"
)
NO_RECORD_POST = (
    "The meeting \"{title}\" ({platform}/{native}) has ended{failed}, but no transcript or "
    "processed notes were captured for it. Tell the user plainly that no record of this meeting "
    "exists — do not reconstruct or invent its content.\n\n"
)

_SECTION_RE = re.compile(r"^##\s+(prep|live|post|schedule|workspace_focus)\s*$", re.MULTILINE)


def _parse_sections(text: str) -> dict[str, str]:
    """``## prep|live|post`` H2 sections → template dict. YAML frontmatter is skipped; text
    before the first known section is ignored. Empty sections are dropped (fallback wins)."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :]
    out: dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(text))
    for i, m in enumerate(matches):
        body = text[m.end() : matches[i + 1].start() if i + 1 < len(matches) else len(text)].strip()
        if body:
            out[m.group(1)] = body + "\n\n"
    return out


# mtime-keyed cache: (path, mtime) → parsed sections. One file, tiny; a dict is plenty.
_cache: dict[str, tuple[float, dict[str, str]]] = {}

OVERRIDE_RELPATH = "agents/meeting-lifecycle.md"


def steering_templates(global_ws_path: "str | None" = None) -> dict[str, str]:
    """The effective per-phase templates: built-in defaults overlaid with the _global
    override file when present/parseable. ``global_ws_path`` defaults to the same env the
    _global mount uses (VEXA_GLOBAL_SYSTEM_WORKSPACE_PATH — config.v1-declared)."""
    root = (global_ws_path if global_ws_path is not None else os.environ.get("VEXA_GLOBAL_SYSTEM_WORKSPACE_PATH", "")).strip()
    if not root:
        return dict(DEFAULT_TEMPLATES)
    path = Path(root) / OVERRIDE_RELPATH
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return dict(DEFAULT_TEMPLATES)  # no override file — the normal case, not an error
    key = str(path)
    cached = _cache.get(key)
    if cached and cached[0] == mtime:
        sections = cached[1]
    else:
        try:
            sections = _parse_sections(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — a broken override must never break chat
            logger.warning("meeting-lifecycle override %s unreadable (%s) — using built-in templates", path, exc)
            sections = {}
        _cache[key] = (mtime, sections)
    return {**DEFAULT_TEMPLATES, **sections}


def render(phase: str, fields: dict[str, str], *, global_ws_path: "str | None" = None) -> str:
    """Format the phase template with ``fields``. An override with unknown placeholders
    fails LOUD in the log and falls back to the built-in template for that phase."""
    templates = steering_templates(global_ws_path)
    try:
        return templates[phase].format(**fields)
    except (KeyError, IndexError, ValueError) as exc:
        logger.warning("meeting-lifecycle override for '%s' has a bad placeholder (%s) — using built-in", phase, exc)
        return DEFAULT_TEMPLATES[phase].format(**fields)
