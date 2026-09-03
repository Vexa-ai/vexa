# `core/mcp/manifests` — the destination's tool manifests (stubs, no behaviour)

**Founder ruling, 2026-09-02:** one MCP server, at the gateway, assembled from tool manifests each
owned by the domain that owns the door. No MCP-over-MCP, no separate `mcp-control` service. And a
**no-agents** product must be deployable — gateway + meetings + flows + identity, no `core/agent` at
all — carrying the meetings tools plus `whats_waiting`.

These files are that shape written down, ahead of the move. **Nothing reads them yet.** They are
committed here, in one place, so the split can be reviewed as a diff before any code moves; on the
move each one goes to its `owner` directory and is served by that domain at `served_at`.

| file | domain | tools | door |
|---|---|---:|---|
| `meetings.mcp.tools.v1.json` | meetings | 17 | meeting-api |
| `agent.mcp.tools.v1.json` | agent | 24 | agent-api |
| `flows.mcp.tools.v1.json` | flows | 11 | flows-api |
| `identity.mcp.tools.v1.json` | identity | 6 | admin-api |
| `gateway.mcp.tools.v1.json` | gateway | 3 | none — edge-owned |
| `rehearse.mcp.tools.v1.json` | rehearse | 3 | the rehearse package (dev profile) |

64 tools, each named exactly once. `mcp.tools.v1.schema.json` is the format.

## What a manifest does NOT carry

No JSON schema and no description. Both are **derived from the bound route's OpenAPI operation** —
the mechanism `core/meetings/services/mcp` already uses today (`operation_id` on a FastAPI route,
read by `FastApiMCP`). A manifest that carried its own copy would be a second place to write the
same thing, and the two would disagree the first time either changed. The manifest binds a NAME to a
ROUTE and states who may call it and where it exists; everything else the route already says.

## The nine tools whose route does not exist yet

`meeting_seed`, `captions_to_segments`, `zoom_transcript_to_segments`, `transcript_terms`,
`start_onboarding`, `confirm_login`, `auth_link`, `auth_claim`, `bot_schedule`, `workspace_regime`,
`settings` — each carries a `note` saying so. They are the work the move actually is: today they are
tool bodies composing across doors, and the manifest is where that debt is legible rather than
spread across a file nobody reads to the end.
