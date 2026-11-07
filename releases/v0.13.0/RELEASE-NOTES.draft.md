## v0.13.0 — the minutes line (alpha)

The chat is the interface to your meetings. Ask a question and the agent answers from your own
transcripts, documents and calendar. One emailed link signs you in and opens the question already
composed. Flows react to meeting events — a prepare message before, minutes after — and you compose a
flow from named steps without a rebuild. Agents reach your meetings over authenticated MCP with
scoped, delegated tokens. Workspaces hold the documents the agent reads and writes.

New tiers in the Helm chart: `agent-api`, `terminal`, `flows`, and a workspaces volume.

**Alpha.** This build has not been through a witness pass and is published as a candidate for
evaluation, not for production.

**Not in this build:** deletion and retention controls; runtime-editable agent prompts; the shipped
MCP does not yet verify delegation tokens.

### Images

Published from candidate `v0.13.0-alpha.3` (build run 34053444728 at `a549fef5e`) and aliased to
`v0.13.0` without a rebuild: eleven images, 21 platform identities — the bot is amd64-only.
The packet is `releases/v0.13.0/candidate-images.json`, validated against the published bytes by
run 34056694611 with every leg green.
