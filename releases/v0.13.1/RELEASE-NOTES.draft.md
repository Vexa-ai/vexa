## v0.13.1 — the minutes line (alpha)

The chat is the interface to your meetings. Ask a question and the agent answers from your own
transcripts, documents and calendar. One emailed link signs you in and opens the question already
composed. Flows react to meeting events — a prepare message before, minutes after — and you compose a
flow from named steps without a rebuild. Agents reach your meetings over authenticated MCP with
scoped, delegated tokens. Workspaces hold the documents the agent reads and writes.

New tiers in the Helm chart: `agent-api`, `terminal`, `flows`, and a workspaces volume.

Since `v0.13.0`: a bot requested inside a workspace is the workspace's meeting — live for every
member, not only the person who asked it in (#1648, delivered by #1650). `vexaai/v012-terminal` now
ships the minutes variant, the same bundle the pilot was shown; both identity legs assert
`ai.vexa.terminal.mode=minutes` on the published image, on amd64 and arm64. Base-image and chart
pulls in the release workflows no longer read Docker Hub (#715); every identity and alias read still
does, because those verify our own published bytes.

**Alpha.** This build has not been through a witness pass and is published as a candidate for
evaluation, not for production.

**Not in this build:** deletion and retention controls; runtime-editable agent prompts; the shipped
MCP does not yet verify delegation tokens.

### Images

Published from candidate `v0.13.1-alpha.2` (build run 34096619850 at `036911efa`) and aliased to
`v0.13.1` without a rebuild: eleven images, 21 platform identities — the bot is amd64-only.
The packet is `releases/v0.13.1/candidate-images.json`.
