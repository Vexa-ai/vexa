# tool.v1 — the tool/cred-governance descriptor — UNSEALED (stub)

Declares a capability a unit may use: its `scope` (→ `--allowedTools`), its `grant` (`auto` = call
freely; `gate` = per-call human approval surfaced as a `proactive-card.v1` action), its `cred_ref` (a
`secret://...` SecretsPort **reference**, never a value — P15), how it attaches (`transport: mcp|builtin`;
**MCP is the attachment mechanism**), and its governance `barriers` (`mnpi`, `info-barrier`, …). The
unit's `unit.v1.tools` is the allow-set; barriers + `identity.v1` `canAccess` (default-deny) decide
attachment; the sandbox/no-egress layer makes air-gap real. This is where governance is **declared and
enforced**, not the model.

**Status: UNSEALED** (stub) — sealed in the tools MVP. `gate:schema` validates its golden.
