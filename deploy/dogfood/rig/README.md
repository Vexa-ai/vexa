# deploy/dogfood/rig — the rehearsal rig (NOT a service, NOT for main)

This directory exists for one reason: **the code the product rehearsal runs on must have
provenance.** Until this commit it did not — 46 MCP tools, the whole in-conversation sign-in,
flows and workspace surfaces lived as a single file on one host, with no branch, no commit
and no backup. A rehearsal against that validates nothing you can ship, and one disk loses a
day of product decisions.

## What this is

`vexa_control_mcp.py` is a **prototype MCP server** built on 2026-08-30 on the storm rig,
hardened by pointing cold-start agents at it and fixing everything they hit. It went wide
where the real service is deliberately narrow: sign-in inside the conversation, the
`whats_waiting` protocol entry point, flows authoring and durable scheduling, workspace and
the propose/validate knowledge lifecycle, composed deep-links into the terminal.

**It is not the product and it must never become one.** The product MCP service is
`core/meetings/services/mcp/` — deployed, gated, tested, tools as thin FastAPI routes. This
prototype's job is to be the stage the four-act rehearsal runs on, and then to be read,
ported and deleted. Two of its findings have already been adopted upstream: the
`asked_by_a_human` gate on speaking (taken FROM the real service, not invented here) and the
operating doctrine now in `VEXA_INSTRUCTIONS`.

## Why this branch never merges

`mcp-rehearsal-rig` is a provenance branch. Merging it would create the second server the
founder explicitly refused. What merges is the PORT — slice by slice, into the real service,
each slice carrying only what the rehearsal proved worth carrying.

## Known, and deliberate, differences from anything shippable

- **`VEXA_RIG_MODE`** (default on here) enables a `token=` argument fallback and a
  `GET /do/<tool>` bridge. Both put a credential in a query string: right for a fetch-only
  agent on a private host, wrong anywhere requests are logged. `VEXA_RIG_MODE=0` disables both.
- **Identity proves mailbox control and nothing more.** Federation is the upgrade an
  organisation will require.
- **`workspace_write` is a dev double** — agent-api exposes no HTTP write, so this reaches the
  volume directly. That missing endpoint is the real gap behind first-class remote workspaces.
- **Mail is a double** (mailpit): nothing leaves the host.

## Running it

`rig.sh status|up|restart|down` supervises the pieces; `flows-up.sh` brings up the flows API
and worker as processes so edits are live on restart. Both expect the dogfood stack from
`deploy/dogfood/` to be running.
