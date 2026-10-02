"""The meeting-identity vocabulary — ONE name per concept, declared as data so a test can enforce it.

## Why this file exists

The MCP surface shipped three tools that ACCEPTED ``meeting_platform`` + ``meeting_id`` while every
tool RETURNED ``platform`` + ``native_meeting_id``. No tool's output could be fed into the next
tool's input, which is the one thing a model chaining calls will always try. That was found by a
reader who had just read the service README, and fixed.

**Hours later the same defect was reintroduced** — a new ``search_transcripts`` tool returned
``meeting_id`` holding the INTEGER row id, colliding with the deprecated alias for the NATIVE
STRING id. Feeding that tool's own output into ``get_meeting_transcript`` produced
``404 Meeting not found for platform google_meet and ID 1``.

Two occurrences in one day, by the same author, with the rule written down in between. The
conclusion is not "be more careful": a convention that lives only in prose and review is enforced
by nobody at 3am, and the failure is silent — every field is individually reasonable, and only the
JOIN between two tools is wrong. So the vocabulary is declared here as DATA and asserted by
``tests/test_identity_vocabulary.py`` against the surface the server actually emits.

## The vocabulary

Three concepts, three names, and they never overlap:

===================  =======  ===============================================================
name                 type     what it is
===================  =======  ===============================================================
``platform``         str      the meeting platform: google_meet | teams | zoom | jitsi
``native_meeting_id``str      the PLATFORM's own id — "abc-defg-hij", "9361792952021"
``meeting_db_id``    int      VEXA's internal row id. Never the platform's.
===================  =======  ===============================================================

``meeting_id`` is **not** in that table, deliberately: it is the name that meant all three at
different times, so it is retired as a concept. It survives only as a deprecated INPUT alias.

## The two rules a name must satisfy

1. **A deprecated alias may appear only as an INPUT, only alongside its canonical name, and only
   when its description says DEPRECATED.** A tool that accepts an alias without the canonical name
   forces callers onto the old vocabulary; one that does not mark it teaches the old vocabulary.
2. **A deprecated alias may NEVER appear in an OUTPUT.** This is the rule the search regression
   broke. Outputs are what the next call is built from, so an ambiguous output name is the bug —
   it is what turns "feed one tool's output into the next" from a promise into a 404.
"""
from __future__ import annotations

from typing import Any, Dict, List

#: concept -> the JSON type it always has. Nothing else may carry these names.
CANONICAL_IDENTITY: Dict[str, str] = {
    "platform": "string",
    "native_meeting_id": "string",
    "meeting_db_id": "integer",
}

#: retired name -> what to use instead. Accepted on input for backwards compatibility; never emitted.
DEPRECATED_ALIASES: Dict[str, str] = {
    "meeting_id": "native_meeting_id",
    "meeting_platform": "platform",
}


def check_tool_inputs(tools: List[Dict[str, Any]]) -> List[str]:
    """Rule 1 over an MCP ``tools/list`` payload. Returns human-readable violations."""
    violations: List[str] = []
    for tool in tools:
        name = tool.get("name", "?")
        props = (tool.get("inputSchema", {}) or {}).get("properties", {}) or {}
        for alias, canonical in DEPRECATED_ALIASES.items():
            if alias not in props:
                continue
            if canonical not in props:
                violations.append(
                    f"{name}: accepts deprecated `{alias}` but NOT canonical `{canonical}` — "
                    f"a caller feeding another tool's output has nowhere to put it"
                )
            description = (props[alias].get("description") or "").upper()
            if "DEPRECATED" not in description:
                violations.append(
                    f"{name}: `{alias}` is not marked DEPRECATED in its description, so the schema "
                    f"teaches the retired name as if it were current (use `{canonical}`)"
                )
    return violations


def check_output_names(tool_name: str, payload: Any) -> List[str]:
    """Rule 2 against a tool's actual RETURNED payload. Recurses into dicts and lists.

    Checks the alias names AND the types of the canonical ones — the search regression emitted a
    correctly-spelled name carrying the wrong kind of value, which a name-only check would pass.
    """
    violations: List[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, list):
            for item in node[:5]:            # a page is homogeneous; five is plenty
                walk(item, f"{path}[]")
            return
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            here = f"{path}.{key}" if path else key
            if key in DEPRECATED_ALIASES:
                violations.append(
                    f"{tool_name}: returns `{here}` — a deprecated alias must never be EMITTED "
                    f"(use `{DEPRECATED_ALIASES[key]}`); an ambiguous output name is what makes "
                    f"the next call fail"
                )
            elif key in CANONICAL_IDENTITY and value is not None:
                expected = CANONICAL_IDENTITY[key]
                actual = "integer" if isinstance(value, int) and not isinstance(value, bool) else (
                    "string" if isinstance(value, str) else type(value).__name__
                )
                if actual != expected:
                    violations.append(
                        f"{tool_name}: `{here}` is {actual} but `{key}` is always {expected} "
                        f"({value!r}) — the same name must not carry two kinds of value"
                    )
            walk(value, here)

    walk(payload, "")
    return violations
