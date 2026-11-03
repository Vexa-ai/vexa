# ADR 0042 — A gate refuses what it cannot see

**Status:** accepted · 2026-10-09 · for v0.13.2 ([#1784](https://github.com/Vexa-ai/vexa/pull/1784),
[#1783](https://github.com/Vexa-ai/vexa/issues/1783)) · applies P9 and P23

## Context

The fourth architecture pass on #1784 found the same defect in six places: a gate that passes what
it does not look at. Each was green, and each was green because it skipped something rather than
because the thing held.

- `gate:isolation` ran every brick's own `check-isolation.js`, so a brick without one was simply not
  checked. `zoom-sdk-capture` had none.
- dependency-cruiser's `no-circular` works per file, so two folders of the terminal could import
  each other through different files. `canvas/` and `minutes/` did.
- `gate:arch-report` rendered the compliance map without comparing it to the committed one, so a
  stale map passed.
- `gate:config-contract` checked each declaration alone, so one setting could be a secret in one
  service's declaration and not in another's. Four were.
- `gate:fact-parity` compared lists that extend a base list only with themselves, so an extended
  placeholder list could lose a base value.
- A contract's `validate.mjs` held its goldens, not the files that carry the contract. The route and
  MCP tool manifests had no contract at all.

## Decision

A gate fails on what it cannot see, instead of passing it:

1. **`gate:isolation`** refuses a package with no `scripts/check-isolation.js`, bar named, reasoned
   exemptions (`ISOLATION_EXEMPT`). The bot's check also scans `runtime/`.
2. **The terminal's isolation check** records which `src/` folder imports which (static imports and
   lazy relative `import()`) and refuses two folders that import each other, unless the pair is on
   `KNOWN_FOLDER_CYCLES`. The list is the debt as it stands: a broken pair must leave it, and no
   pair joins it.
3. **`gate:arch-report`** fails when the committed map is not what a fresh render writes
   (`6ff4db6ab`).
4. **`gate:config-contract`** refuses a key that one service declares secret and another does not.
5. **`gate:fact-parity`** holds an `extends` relation: a set fact that extends another holds every
   member of the base.
6. **A contract's `validate.mjs` holds every live instance it can find**, not only its goldens:
   `routes.v1` and `mcp.tools.v1` validate every manifest under `core/`.

`gate:vendor-payload`'s extension in the same pass is recorded in ADR-0039.

## Consequences

- A new brick, folder cycle, secret declaration or manifest is checked the day it lands, without
  anyone remembering to add it.
- The terminal carries eight known folder cycles. They are visible and cannot grow; breaking one is
  now a one-line change to the list.
- `core/meetings/eval` is exempt from the isolation check: it declares no dependencies, so there is
  no boundary for a check to hold yet.
