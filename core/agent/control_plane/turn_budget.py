"""turn_budget.py — how many tool calls one chat turn may make, as company policy (Vexa-ai/vexa#1783).

THE KEY, in `_global/POLICIES.md` front matter:

    chat_turn_max_tool_calls   40   tool calls a chat turn may make (per call window when the
                                    deployment continues chat turns automatically)

40 is today's number (`llm/openai_agent._DEFAULT_MAX_TOOL_CALLS`), so the seed changes nothing.

THE ADMINISTRATOR SETS IT; EVERYONE ELSE MAY ONLY NARROW. A workspace (the chat's target, through
its own `POLICIES.md`) and a person (their desk's `POLICIES.md`) may write the same key, and the
turn gets the smallest answer — a value above `_global`'s is clipped to it, never honoured. A
budget is a cost and a safety margin: the person paying for it is the company, so only the company
raises it.

SOFT, CONSERVATIVE. A value this module cannot read is the default (40), never "no limit"; an
unreadable narrowing layer is ignored. Unlike the connection keys nothing here grants an action, so
refusing every turn over a typo would cost more than it protects.

`effective` answers ``None`` when no layer says anything — the dispatch then stamps nothing and
the worker keeps the deployment's own dial (`VEXA_AGENT_MAX_TOOL_CALLS[_CHAT]`), exactly as before.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Optional

from control_plane import front_page

logger = logging.getLogger("agent_api.turn_budget")

POLICIES_FILE = "POLICIES.md"
KEY = "chat_turn_max_tool_calls"
DEFAULT = 40
#: The most an administrator may allow one chat turn. Far above any turn seen in use.
CEILING = 1000
#: The env key the worker's harness reads for a chat turn (`llm/openai_agent._calls_budget`).
ENV_KEY = "VEXA_AGENT_MAX_TOOL_CALLS_CHAT"


def _read(dir_) -> Optional[str]:
    if not dir_:
        return None
    try:
        from workspaces.shared import workspace_paths as wpaths  # noqa: PLC0415
        d = Path(dir_)
        if not d.is_dir():
            return None
        return wpaths.read_text_inside(d, POLICIES_FILE, max_bytes=1_000_000)
    except Exception:  # noqa: BLE001 — an unreadable layer says nothing
        return None


def _value(raw: Optional[str], key: str = KEY, ceiling: int = CEILING) -> "tuple[bool, Optional[int]]":
    """``(present, value)``: whether the key is on file, and its whole number (None = unreadable)."""
    if not raw:
        return False, None
    value = str(front_page.front_matter(raw).get(key, "") or "").strip()
    if not value:
        return False, None
    if value.isdigit() and 0 < int(value) <= ceiling:
        return True, int(value)
    return True, None


def _effective(global_dir, narrowers: Iterable, key: str, default: int, ceiling_max: int) -> Optional[int]:
    present, g = _value(_read(global_dir), key, ceiling_max)
    if present and g is None:
        logger.warning("%s in _global/%s is not a whole number from 1 to %d; using %d",
                       key, POLICIES_FILE, ceiling_max, default)
    ceiling = g if g is not None else default
    answer = ceiling if present else None
    for d in narrowers or ():
        has, n = _value(_read(d), key, ceiling_max)
        if has and n is not None and n < ceiling:
            ceiling = n
            answer = n
    return answer


def effective(global_dir, narrowers: Iterable = ()) -> Optional[int]:
    """The chat turn's budget, or ``None`` when no layer has an answer (see the module docstring)."""
    return _effective(global_dir, narrowers, KEY, DEFAULT, CEILING)


# ── THE SHELL TOOL'S WALL-CLOCK LIMIT (live app.dev 2026-10-15) ──────────────────────────────────
# `shell_timeout_sec` — seconds one shell command the agent runs may take before it is killed (its
# whole process group) and the agent is told it timed out (`llm/shell_guard.sh`). Same layering as
# the tool-call budget: `_global` sets it, a workspace or a person may only narrow it. Nothing on
# file stamps nothing and the worker keeps its own default (120 s, `llm.claude_code`).
SHELL_KEY = "shell_timeout_sec"
SHELL_DEFAULT = 120
SHELL_CEILING = 3600
SHELL_ENV_KEY = "VEXA_SHELL_TIMEOUT_SEC"


def shell_timeout(global_dir, narrowers: Iterable = ()) -> Optional[int]:
    """The shell tool's limit in seconds for this turn, or ``None`` when no layer says one."""
    return _effective(global_dir, narrowers, SHELL_KEY, SHELL_DEFAULT, SHELL_CEILING)
