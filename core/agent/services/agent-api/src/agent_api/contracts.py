"""Contract loading + validation — the published seams the agent touches.

The agent CONSUMES ``meetings/contracts/transcript.v1`` and PRODUCES documents conforming to
``agent/contracts/workspace.v1``. Both are crossed as language-neutral JSON Schema **read by path**
(P4): we never import meetings (or any other domain's) Python — that ``meetings ⊥ agent`` boundary
is enforced by the language/format seam, not by trust.

Schemas are located by walking up to the monorepo root (the dir that holds ``meetings/`` and
``agent/``), so the package stays liftable regardless of where it is invoked from.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import jsonschema
from referencing import Registry, Resource

# The published seams, relative to the monorepo root.
_TRANSCRIPT_SCHEMA = Path("meetings/contracts/transcript.v1/transcript.schema.json")
_WORKSPACE_SCHEMA = Path("agent/contracts/workspace.v1/workspace.schema.json")
_INVOKE_SCHEMA = Path("agent/contracts/invoke.v1/invoke.schema.json")
_UNIT_SCHEMA = Path("agent/contracts/unit.v1/unit.schema.json")
_ROUTINE_SCHEMA = Path("agent/contracts/routine.v1/routine.schema.json")
_EVENT_SCHEMA = Path("agent/contracts/event.v1/event.schema.json")


def _repo_root() -> Path:
    """Walk up from this file to the dir that owns both ``meetings/`` and ``agent/``."""
    for parent in Path(__file__).resolve().parents:
        if (parent / _TRANSCRIPT_SCHEMA).exists() and (parent / _WORKSPACE_SCHEMA).exists():
            return parent
    raise FileNotFoundError(
        "could not locate the monorepo root (a dir containing both "
        "meetings/contracts/transcript.v1 and agent/contracts/workspace.v1)"
    )


@lru_cache(maxsize=None)
def _load(rel: Path) -> dict:
    return json.loads((_repo_root() / rel).read_text())


@lru_cache(maxsize=None)
def _validator(rel: Path, shape: str) -> jsonschema.Draft202012Validator:
    schema = _load(rel)
    registry = Registry().with_resource(schema["$id"], Resource.from_contents(schema))
    return jsonschema.Draft202012Validator(
        {"$ref": f"{schema['$id']}#/$defs/{shape}"}, registry=registry
    )


# ── transcript.v1 (CONSUMED) ─────────────────────────────────────────────────

def validate_transcription(payload: dict) -> None:
    """Validate a ``transcript.v1`` Transcription envelope. Raises ``ValidationError`` if non-conformant."""
    _validator(_TRANSCRIPT_SCHEMA, "Transcription").validate(payload)


def validate_segment(segment: dict) -> None:
    """Validate a single ``transcript.v1`` TranscriptSegment."""
    _validator(_TRANSCRIPT_SCHEMA, "TranscriptSegment").validate(segment)


def validate_session_end(payload: dict) -> None:
    """Validate a ``transcript.v1`` SessionEnd envelope (the meeting-completed trigger source)."""
    _validator(_TRANSCRIPT_SCHEMA, "SessionEnd").validate(payload)


def iter_segments(payload: dict) -> Iterable[dict]:
    """Validate a Transcription payload, then yield its segments (each also conformant)."""
    validate_transcription(payload)
    yield from payload.get("segments", [])


# ── workspace.v1 (PRODUCED) ──────────────────────────────────────────────────

def validate_entity_frontmatter(frontmatter: dict) -> None:
    """Validate emitted workspace frontmatter against ``workspace.v1`` EntityFrontmatter (P8)."""
    _validator(_WORKSPACE_SCHEMA, "EntityFrontmatter").validate(frontmatter)


# ── invoke.v1 (PRODUCED — the meeting→agent trigger) ─────────────────────────

def validate_invocation(payload: dict) -> None:
    """Validate an ``invoke.v1`` Invocation envelope (the trigger the bridge emits)."""
    _validator(_INVOKE_SCHEMA, "Invocation").validate(payload)


# ── unit.v1 (PRODUCED — the universal unit-invocation envelope) ──────────────

def validate_unit_invocation(payload: dict) -> None:
    """Validate a ``unit.v1`` Invocation envelope (the universal trigger the dispatcher emits).

    Supersedes ``invoke.v1`` for the unified unit: chat/routine/event/transcription all ride this.
    """
    _validator(_UNIT_SCHEMA, "Invocation").validate(payload)


# ── routine.v1 (PRODUCED — the saved recurring/event unit the user authors) ──

def validate_routine(payload: dict) -> None:
    """Validate a ``routine.v1`` Routine (the authoring entity that compiles to a schedule.v1 job)."""
    _validator(_ROUTINE_SCHEMA, "Routine").validate(payload)


# ── event.v1 (CONSUMED — the event-source → agent-api ingress) ───────────────

def validate_event(payload: dict) -> None:
    """Validate an ``event.v1`` Event (the envelope the ingress maps to a unit.v1 Invocation)."""
    _validator(_EVENT_SCHEMA, "Event").validate(payload)
