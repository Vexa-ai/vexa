"""agent_config.py — the GOVERNED, workspace-driven config for per-agent behavior.

The user/agent steers the real-time meeting copilot through a VISIBLE, git-governed file in their
workspace: ``agents/meeting.md`` (a per-agent config home — NOT a dotfile, so it shows in the Files
tree and the user can read+edit it; seeded from the workspace template). It is YAML frontmatter (the
knobs) + a natural-language body (steering merged into the copilot prompt).

This module is deliberately small, isolated, and unit-testable: it has no redis/docker/claude
dependency. Parsing is TOLERANT — a missing file, missing/partial frontmatter, or malformed YAML each
fall back PER-KEY to the current code defaults, and the body (if any) is always taken as steering.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

# The default live-meeting model. Env can pin an operator-selected route; the hard-coded fallback keeps
# high-frequency meeting beats on a zero-cost OpenRouter route.
DEFAULT_MEETING_MODEL = os.environ.get("VEXA_MEETING_MODEL") or "openrouter/free"

# A model named in config must be on this allowlist. Anything else falls back to the default with a log
# line; the config file is governed but user-editable, so a typo cannot silently pin an unexpected route.
MODEL_ALLOWLIST: frozenset[str] = frozenset({
    "openrouter/free",
    "deepseek/deepseek-v4-flash",
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-20250514",
    "claude-sonnet-4-5-20250929",
    "claude-opus-4-20250514",
    "claude-opus-4-1-20250805",
})

DEFAULT_CARD_KINDS: tuple[str, ...] = ("person", "company", "product")
DEFAULT_CADENCE_SEGMENTS = 4


@dataclass(frozen=True)
class MeetingConfig:
    """The resolved meeting-copilot knobs (every field has a code default; see ``load_meeting_config``)."""

    enabled: bool = True
    model: str = DEFAULT_MEETING_MODEL
    cadence_segments: int = DEFAULT_CADENCE_SEGMENTS
    card_kinds: list[str] = field(default_factory=lambda: list(DEFAULT_CARD_KINDS))
    write_meeting_doc: bool = True
    steering: str = ""


_FRONTMATTER = re.compile(r"^\s*---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)

# Where the per-agent config lives in the workspace (visible, git-governed).
MEETING_CONFIG_PATH = "agents/meeting.md"


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter-dict, body). Tolerant: no fence ⇒ ({}, whole-text-as-body); bad yaml ⇒
    ({}, body)."""
    m = _FRONTMATTER.match(text)
    if not m:
        return {}, text.strip()
    raw_fm, body = m.group(1), m.group(2)
    try:
        data = yaml.safe_load(raw_fm)
    except yaml.YAMLError:
        log.warning("agents/meeting.md: malformed YAML frontmatter — using defaults")
        return {}, body.strip()
    if not isinstance(data, dict):
        return {}, body.strip()
    return data, body.strip()


def _as_bool(val: object, default: bool) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in {"true", "yes", "1", "on"}
    return default


def _as_model(val: object) -> str:
    if isinstance(val, str) and val.strip():
        candidate = val.strip()
        if candidate in MODEL_ALLOWLIST:
            return candidate
        log.warning(
            "agents/meeting.md: model %r not in allowlist — falling back to default %r",
            candidate, DEFAULT_MEETING_MODEL,
        )
    return DEFAULT_MEETING_MODEL


def _as_cadence(val: object) -> int:
    try:
        n = int(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_CADENCE_SEGMENTS
    return n if n >= 1 else DEFAULT_CADENCE_SEGMENTS


def _as_card_kinds(val: object) -> list[str]:
    if isinstance(val, (list, tuple)):
        kinds = [str(k).strip().lower() for k in val if str(k).strip()]
        if kinds:
            return kinds
    return list(DEFAULT_CARD_KINDS)


def load_meeting_config(work: Path) -> MeetingConfig:
    """Read ``<work>/agents/meeting.md`` and return the resolved ``MeetingConfig`` with PER-KEY
    fallback to the code defaults. Absent file ⇒ all defaults. Tolerant of bad YAML / no frontmatter
    (body, if any, is still used as steering)."""
    path = Path(work) / MEETING_CONFIG_PATH
    if not path.exists():
        return MeetingConfig()
    try:
        text = path.read_text()
    except OSError:
        return MeetingConfig()

    fm, body = _split_frontmatter(text)
    return MeetingConfig(
        enabled=_as_bool(fm.get("enabled"), True),
        model=_as_model(fm.get("model")),
        cadence_segments=_as_cadence(fm.get("cadence_segments")),
        card_kinds=_as_card_kinds(fm.get("card_kinds")),
        write_meeting_doc=_as_bool(fm.get("write_meeting_doc"), True),
        steering=body,
    )
