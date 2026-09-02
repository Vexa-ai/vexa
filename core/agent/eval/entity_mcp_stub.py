#!/usr/bin/env python3
"""A stdlib-only stdio MCP server exposing `entity_upsert` over a mounted workspace — FOR MEASUREMENT.

WHY THIS EXISTS. The offline write-back A/B ran without any MCP, so the phase always took the
FALLBACK path and hand-wrote each page as markdown. That is the expensive path — roughly four times
the tokens of a tool call, because the model types the whole card instead of naming its fields — and
once the card landed it dominated the numbers: pages per fixture swung between 2 and 12 on the same
budget, and the measurement was reporting the cost of the path production does not take.

So the harness serves the tool itself. No rig, no HTTP, no running stack, no dependency: MCP stdio
is newline-delimited JSON-RPC 2.0 over a pipe, and the three methods a tool server needs are
`initialize`, `tools/list` and `tools/call`. The implementation behind it is the SAME
`shared.entities.upsert_entity` the endpoint calls, so what this measures is what ships.

It is a measuring instrument and is never part of a deployment: nothing imports it, and it writes
only into the workspace directory it is given.

    python3 entity_mcp_stub.py <workspace-path>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.entities import (CARD_SECTIONS, EntityRefused, KINDS,  # noqa: E402
                             tool_sections_text, upsert_entity, write_index)

WORKSPACE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()

TOOL = {
    "name": "entity_upsert",
    "description": (
        "Record what you just learned about a person, company, meeting, project or decision. One "
        "call creates the page or updates it in place. THE PAGE IS A CARD, not a log: a one-line "
        "`summary`, the sections below filed with `fields`, `## Connected` links both ways, "
        "`## Sources`, `## Open questions`, and `## Timeline` last for dated events.\n\n"
        "SECTIONS AND FIELDS, by kind:\n" + tool_sections_text() + "\n\n"
        "`facts` with no `section` land in the Timeline. `source` is required."),
    "inputSchema": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(KINDS)},
            "name": {"type": "string"},
            "summary": {"type": "string"},
            "fields": {"type": "object", "additionalProperties": True},
            "facts": {"type": "array", "items": {"type": "string"}},
            "section": {"type": "string"},
            "connections": {"type": "array", "items": {"type": "string"}},
            "open_questions": {"type": "array", "items": {"type": "string"}},
            "source": {"type": "string"},
        },
        "required": ["kind", "name", "source"],
    },
}


def call(args: dict) -> dict:
    try:
        result = upsert_entity(
            WORKSPACE, str(args.get("kind") or ""), str(args.get("name") or ""),
            args.get("facts") or [], str(args.get("source") or ""),
            summary=str(args.get("summary") or ""),
            fields=args.get("fields") if isinstance(args.get("fields"), dict) else None,
            section=str(args.get("section") or ""),
            connections=args.get("connections") or (),
            open_questions=args.get("open_questions") or ())
    except EntityRefused as e:
        return {"refused": str(e),
                "do": "fix the fact, do not retry the same call — the refusal is the rule"}
    except Exception as e:                                        # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
    write_index(WORKSPACE, WORKSPACE.name)
    return result


def main() -> int:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:                       # a notification — nothing to answer
            continue
        if method == "initialize":
            version = (msg.get("params") or {}).get("protocolVersion") or "2024-11-05"
            result = {"protocolVersion": version, "capabilities": {"tools": {}},
                      "serverInfo": {"name": "entities-stub", "version": "0"}}
        elif method == "tools/list":
            result = {"tools": [TOOL]}
        elif method == "tools/call":
            params = msg.get("params") or {}
            if params.get("name") != TOOL["name"]:
                result = {"content": [{"type": "text", "text": "no such tool"}], "isError": True}
            else:
                out = call(params.get("arguments") or {})
                result = {"content": [{"type": "text", "text": json.dumps(out)}],
                          "isError": bool(out.get("error") or out.get("refused"))}
        else:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid,
                                         "error": {"code": -32601, "message": "method not found"}}) + "\n")
            sys.stdout.flush()
            continue
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
