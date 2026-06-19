# ADR 0007 — The dashboard is vendored as acknowledged debt (out of workspace + gates)

**Status:** accepted · 2026-06-19 · concerns **P6, P12, P17**

## Context

The 0.11 web dashboard is a large Next.js app (~27k LOC, ~47-dep tree) and a *leaf consumer* of the
public API. Refactoring it into the v0.12 brick model (one front door, license-clean tree, per-dir
READMEs) is real work that should not block the meetings capture plane — the architectural risk of the
UI is low (it imports no platform internals; it talks HTTP/WS to the gateway).

## Decision

**Vendor the dashboard wholesale into `clients/dashboard/` and carve it out of the gates as logged debt,
to be paid down in the dashboard phase — not silently.**

- **Out of the pnpm workspace:** `pnpm-workspace.yaml` excludes it (`!clients/dashboard`); it keeps its own
  npm install and build, so it does not pollute the workspace lockfile or `gate:node`.
- **Out of `gate:licenses` (P17):** its dependency tree is not yet audited against the OSS allowlist
  (ADR-0004); excluded for now, audited in full as part of the de-vendor.
- **The debt is explicit.** It is recorded here and in ADR-0004; this is the difference between a logged,
  bounded exception and architectural rot (P9 — a rule bends *on the record*, never by convention).

## Consequences

- **Trade-off accepted:** temporary non-compliance with **P6** (one public front door — the vendored tree
  deep-imports freely) and **P17** (its transitive licences are unverified). Both are bounded to this one
  leaf and gated out, not leaking into the rest of the tree.
- The dashboard phase de-vendors it: bring it into the workspace (or formalize a standalone build), run
  `gate:licenses` over its tree, add the README surface (P12), and wire it to the real meeting-api +
  gateway. Closing this ADR is that phase's definition of done.
- Until then, treat `clients/dashboard` as an opaque vendored artifact: changes inside it are not gate-checked.
