# `core/mcp/manifests` — the destination's tool manifests (stubs, no behaviour)

**Founder rulings, 2026-09-02.** One MCP server, at the gateway, assembled from tool manifests each
owned by the domain that owns the door. A **no-agents** product must be deployable — gateway +
meetings + flows + identity — carrying the meetings tools plus `whats_waiting`. A **separate paid
per-seat product** must compose onto this line without touching OSS code. And **`whats_waiting` is
flows**: one forward to the pending-reactions projection, nothing unioned at the edge.

These files are that shape written down, ahead of the move. **Nothing reads them yet.** They are
committed here, in one place, so the split can be reviewed as a diff before any code moves; on the
move each goes to its `owner` directory and is served by that domain at `served_at`.

| file | domain | source | `depends_on` | tools | door |
|---|---|---|---|---:|---|
| `meetings.mcp.tools.v1.json` | meetings | oss | identity | 17 | meeting-api |
| `agent.mcp.tools.v1.json` | agent | oss | identity | 24 | agent-api |
| `flows.mcp.tools.v1.json` | flows | oss | identity | 11 | flows-api |
| `identity.mcp.tools.v1.json` | identity | oss | — | 6 | admin-api |
| `gateway.mcp.tools.v1.json` | gateway | oss | identity | 3 | none — edge-owned |
| `rehearse.mcp.tools.v1.json` | rehearse | oss | all four | 3 | the dev harness |
| `mounted.example.mcp.tools.v1.json` | billing | **mounted** | identity | 2 | a private deployment's, never this repo's |

64 tools, each named exactly once. `mcp.tools.v1.schema.json` is the format.

## `depends_on` is the whole architecture in one field

**Identity is the only domain everyone depends on** (founder ruling). Meetings, flows and agent each
point at identity and at nothing else, so every one of the eight configurations — identity plus any
subset of the three — is a product. A tool declares `requires` (the domains it needs) and is ABSENT
from `tools/list` where they are not deployed; a manifest declares `depends_on`, which may name
identity and itself and nothing more. Enumerating deployment NAMES was the earlier shape and it was
wrong the way every enumeration of a product matrix is wrong: the configuration nobody named is the
one that breaks.

The three couple to each other by exactly two mechanisms, both one-way: **events published into
flows** (fire-and-forget — a missing flows domain is tolerated and the publisher still succeeds) and
**flow steps naming an agent** (a missing agent domain makes the step "not present", never an error).

## Three things a manifest does that are easy to miss

**It carries no schema and no description.** Both are derived from the bound route's OpenAPI
operation — the mechanism `core/meetings/services/mcp` already runs on (`operation_id` +
`FastApiMCP`). A manifest with its own copy would be a second place to write one thing, and the two
would disagree the first time either changed.

**`publishes_events` is how domains compose, not the gateway.** A domain never contributes to another
domain's tool. It publishes a fact into the flows intake, and a flow **definition** in `behavior/`
decides what waits and what a person is told. That is why `whats_waiting` is a single forward: a
"subscribe" prompt is `onboarding.completed → human step`, an admin-editable definition, not a branch
inside a tool nobody can change without a deploy.

**`entitlement` may be declared by at most one manifest in a deployment**, and none is the normal
case. No manifest => no hook => `entitled(subject)` is always true, which is exactly the OSS product.
The paid product is an addition; this repo never carries a gate shipped dark.

## The routes that do not exist yet

`meeting_seed`, `captions_to_segments`, `zoom_transcript_to_segments`, `transcript_terms`,
`start_onboarding`, `confirm_login`, `auth_link`, `auth_claim`, `bot_schedule`, `workspace_regime`,
`settings` — each carries a `note` saying so. Plus the three events `whats_waiting` needs before its
non-reaction sources survive the move (`core/mcp/README.md` §4). That list is what the move actually
is: today they are tool bodies composing across doors, and the manifest is where the debt is legible
instead of spread across a file nobody reads to the end.
