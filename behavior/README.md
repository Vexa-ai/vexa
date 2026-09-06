# behavior — the product's voice (top-level, peer of the machinery)

THE BOUNDARY: **machinery is what compiles into the runtime; behavior is what the runtime
loads.** The images are the interpreter — this tree (and its private sibling) is the program.
Corollary: **machinery contains no prose.** Everything a human or an agent reads is behavior —
highest-level, diverse, and largely PROPRIETARY. This top-level tree holds only the PUBLISHED
showcase; the real voice is a private tree of the same shape, mounted at `VEXA_BEHAVIOR_DIR`
(the `_global` deployment pattern) and resolved before these files. Flow params override both.

**Not every subdirectory is present in every deployment, and the machinery must not assume one
is.** `mail/` and `flows/` ship with the no-agents minutes product (PRD decision 40.6: gateway +
meetings + flows + identity) because flows reads them on the paths that product runs. Everything
else is OPTIONAL CONTENT — an agent-only or terminal-only deployment mounts it, and a deployment
that does not run agents has no use for any of it and may carry none of it.

| | | ships with the no-agents product |
|---|---|---|
| `mail/` | the words every notification is built from | **yes** — flows sends the mail |
| `flows/` | flow compositions (canonical exports of the registry) | **yes** |
| `prompts/` | agent-turn kickoffs and instructions (showcase examples) | no — nothing dispatches a turn |
| `asks/` | the MCP edge's presets | no |
| `workspaces/` | the workspace seeds — what a new personal/shared/org workspace is born as | no |

This README used to list `prompts/` and `workspaces/` as though the tree were fixed, and the code
believed it: `flows_defs/production.py` read three files out of `prompts/` at MODULE IMPORT, so a
deployment carrying only what it needs could not import the flow definitions at all. **Absent
content is a fact about the deployment, not a broken build** — a reader resolves its file when the
step needs it, and a step that finds nothing reports `not_present` (decision 18a).

Machinery (core/, clients/) knows HOW; this tree knows WHAT TO SAY. It changes at content speed —
a git commit here or in the private tree, zero image rebuilds.

## Delivery

Behavior ships through **Vexa Delivery** (the enterprise BYOC conveyor): the private tree is
published into the signed channel as a digest-pinned content artifact — peer to the image
digests — which the customer verifies offline, admits through their own gate, and mounts as
`VEXA_BEHAVIOR_DIR`. A prompt change reaches a regulated cluster with the same ceremony,
receipts and break-glass audit as a code change; because behavior is pure data (no code over
the wire), the artifact is directly inspectable by the customer's reviewers. Spec/schema for
the artifact type belongs to the `vexa-delivery` repository (an M3+ item for its owning
session).
