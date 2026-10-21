"""TOOL ANNOTATIONS — every tool in `tools/list` says what kind of action it is (#1646).

MCP clients read `annotations` to decide what runs without a per-call confirmation and what always
prompts: a transcript fetch should not prompt, a bot dispatch or `speak_in_meeting` should. The
directories that list MCP servers require the hints on every tool, and OpenAI's requires
`readOnlyHint`, `destructiveHint` and `openWorldHint` to be EXPLICIT — an absent hint means the
spec default, and the spec defaults (`destructiveHint: true`, `openWorldHint: true`) are wrong for
most of this surface.

ONE REGISTRY, TWO SOURCES. The fourteen built-in tools are this edge's own routes, so their
annotations live here, next to nothing else. An ASSEMBLED tool belongs to the domain that owns the
door behind it, so its manifest entry may carry `title` + `annotations` itself (that is the
authority: the domain knows what its route does); this registry is the fallback for a manifest
that does not say. Both are applied in ONE place — :func:`apply` — after `FastApiMCP` has derived
the tool list and before the transport is mounted, so the `types.Tool` objects its
`list_tools` handler returns are the annotated ones.

AN UNANNOTATED TOOL FAILS CLOSED. A tool neither source describes (a mounted private manifest that
predates this field, say) is served with the most cautious hints — not read-only, destructive,
open-world — so a client prompts before calling it, and a warning names it. The contract test
(`tests/test_tool_annotations.py`) refuses the suite when any tool this repository ships reaches
that fallback.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Mapping, Optional

from mcp import types as mcp_types

logger = logging.getLogger(__name__)

#: The hints a client may NOT be left to default. `idempotentHint` is set on every tool here too,
#: but the spec gives it a harmless default (false), so it is not part of the contract.
REQUIRED_HINTS = ("readOnlyHint", "destructiveHint", "openWorldHint")
HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
#: MCP tool names are capped at 64 characters by the directories (and several clients).
MAX_NAME = 64


def _read(title: str, *, open_world: bool = False) -> Dict[str, Any]:
    return {"title": title, "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": open_world}


def _write(title: str, *, destructive: bool, idempotent: bool, open_world: bool) -> Dict[str, Any]:
    return {"title": title, "readOnlyHint": False, "destructiveHint": destructive,
            "idempotentHint": idempotent, "openWorldHint": open_world}


#: name -> {title, readOnlyHint, destructiveHint, idempotentHint, openWorldHint}.
#
# `openWorldHint` is true only where the tool's effect leaves Vexa: a bot entering or leaving a
# third-party meeting, speech heard by people in it, a ticket filed in an external tracker. Reading
# Vexa's own store of a meeting (transcript, chat, recordings) is a closed world.
REGISTRY: Dict[str, Dict[str, Any]] = {
    # ── read-only ────────────────────────────────────────────────────────────────────────────
    # Pure: parses a string, no network hop at all.
    "parse_meeting_link": _read("Parse meeting link"),
    "get_bot_status": _read("Get bot status"),
    "list_meetings": _read("List meetings"),
    "get_meeting_transcript": _read("Get meeting transcript"),
    "search_transcripts": _read("Search transcripts"),
    "get_meeting_chat": _read("Read meeting chat"),
    "list_recordings": _read("List recordings"),
    "get_recording": _read("Get recording"),
    # flows (fallbacks — the flows manifest carries its own; see core/flows/mcp.tools.v1.json)
    "whats_waiting": _read("What's waiting"),
    "timeline": _read("Timeline"),
    "flows_list": _read("List flows"),
    "reactions_list": _read("List reactions"),
    "friction_so_far": _read("List my friction reports"),
    # ── writes that add, not remove ──────────────────────────────────────────────────────────
    # Puts a bot into a third-party meeting; a second call for the same meeting is refused (409)
    # rather than repeated, but the first one is a new participant in someone else's call.
    "request_meeting_bot": _write("Send bot to meeting", destructive=False, idempotent=False,
                                  open_world=True),
    # Merges keys into the meeting's metadata and sets its title; the same call twice leaves the
    # same state. A key sent as null deletes THAT key only — a scoped, caller-named edit, never a
    # loss of anything the caller did not name — so it is classed additive, not destructive.
    "annotate_meeting": _write("Annotate meeting", destructive=False, idempotent=True,
                               open_world=False),
    # PUT of a live bot's transcription config (language, task) — the same body twice is one state.
    "update_bot_config": _write("Update bot configuration", destructive=False, idempotent=True,
                                open_world=False),
    # Files a ticket in the deployment's ticket sink — an external tracker when configured.
    "report_issue": _write("Report issue to Vexa", destructive=False, idempotent=False,
                           open_world=True),
    "report_friction": _write("Report friction", destructive=False, idempotent=False,
                              open_world=False),
    # ── destructive / irreversible ───────────────────────────────────────────────────────────
    # Ends the bot's presence in the meeting: transcription of the rest of it is gone for good.
    # Stopping an already-stopped bot changes nothing more, hence idempotent.
    "stop_bot": _write("Remove bot from meeting", destructive=True, idempotent=True,
                       open_world=True),
    # Speech heard by real people cannot be taken back.
    "speak_in_meeting": _write("Speak in meeting", destructive=True, idempotent=False,
                               open_world=True),
    # resume | retry | cancel | wake on one reaction. `cancel` ends a pending reaction for good
    # (flows_api.signal_reaction -> cancel), and retry/resume re-run a flow's side effects, so the
    # tool is NOT additive-only: destructive, and not idempotent (a second retry runs it again).
    "reaction_signal": _write("Steer a reaction", destructive=True, idempotent=False,
                              open_world=False),
}

#: The fail-closed fallback for a tool no source describes: a client prompts before calling it.
CAUTIOUS = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False,
            "openWorldHint": True}


def _humanize(name: str) -> str:
    return name.replace("_", " ").strip().capitalize() or name


def _from_manifest(entry: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """`{title, hints…}` from an assembled tool's manifest entry, or None when it says nothing."""
    if not entry:
        return None
    ann = entry.get("annotations") if isinstance(entry.get("annotations"), Mapping) else {}
    hints = {k: ann[k] for k in HINTS if isinstance(ann.get(k), bool)}
    title = entry.get("title") or ann.get("title")
    if not hints and not title:
        return None
    out: Dict[str, Any] = dict(hints)
    if isinstance(title, str) and title.strip():
        out["title"] = title.strip()
    return out


def resolve(name: str, manifest_entry: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """The annotations one tool is served with: manifest over registry, hint by hint; any required
    hint still missing takes its CAUTIOUS value (and is logged)."""
    registered = REGISTRY.get(name) or {}
    declared = _from_manifest(manifest_entry) or {}
    out: Dict[str, Any] = {**registered, **declared}
    missing = [h for h in HINTS if not isinstance(out.get(h), bool)]
    if missing:
        logger.warning("mcp tool %r has no annotation for %s — serving it with the cautious "
                       "default (not read-only, destructive, open-world)", name, ", ".join(missing))
        for h in missing:
            out[h] = CAUTIOUS[h]
    if not out.get("title"):
        out["title"] = _humanize(name)
    return out


def apply(tools: Iterable[mcp_types.Tool],
          manifest_entries: Optional[Mapping[str, Mapping[str, Any]]] = None) -> List[str]:
    """Set `title` + `annotations` on every tool IN PLACE. Returns the names that fell back to the
    cautious default for at least one required hint (empty is the contract)."""
    entries = manifest_entries or {}
    fell_back: List[str] = []
    for tool in tools:
        if len(tool.name) > MAX_NAME:
            logger.warning("mcp tool name %r is %d characters — directories cap names at %d",
                           tool.name, len(tool.name), MAX_NAME)
        entry = entries.get(tool.name)
        known = {**(REGISTRY.get(tool.name) or {}), **(_from_manifest(entry) or {})}
        if any(not isinstance(known.get(h), bool) for h in REQUIRED_HINTS):
            fell_back.append(tool.name)
        resolved = resolve(tool.name, entry)
        tool.title = resolved["title"]
        tool.annotations = mcp_types.ToolAnnotations(
            title=resolved["title"], **{h: resolved[h] for h in HINTS})
    return fell_back
