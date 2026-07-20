# ADR 0032 — Docs declare their release, and can't silently drift (D6c enforced)

**Status:** accepted · 2026-07-14 · enforces **D6c** ("docs ride the change; docs story
human-validated" — was TO BUILD) and **D1** (enforced, not aspirational)

## Context

D6c says docs ride every change and the docs story is validated — but nothing enforced it. Two
gaps: (1) the docs never stated **which release** they describe, so a reader couldn't tell if a
page was current; and (2) a PR could change user-facing behaviour and touch no docs, with no signal
— docs drift silently until someone notices.

## Decision

Two CI gates, both required on `main`.

**A — `gate:docs-version` (docs declare their release).** `docs/docs/changelog.mdx` carries a
`docs-reflects: <version>` marker (+ a visible "These docs reflect Vexa `vX.Y.Z`" line). The gate
(in `scripts/gates.mjs`, run in the `gates` suite on every PR/push) asserts it equals
`Chart.yaml appVersion`. So the release version-bump **must** also advance the docs stamp, or CI
reds — the docs cannot lag the release.

**B — `docs-current` (docs ride the change — the confirm-at-PR gate).** A required status check
(`scripts/docs-current-gate.mjs` + `.github/workflows/docs-current.yml`, on pull_request +
merge_group). A PR that touches a **product surface** (`core/` · `clients/` · `deploy/` · `libs/` ·
`package.json`) must make a conscious docs decision: **either** change `docs/**`, **or** carry a
`docs: none` label (the explicit "no docs needed" confirmation). No third option. Docs-only /
governance / test-only PRs are exempt.

The `docs: none` label is the machine form of the "request docs update confirm at PR" — every
user-facing PR states, on the record, that its docs impact was considered.

## Consequences

- **Docs never silently lag a release** (A) or a change (B). D6c is machinery, not a template note.
- **Best-effort surface detection.** The product-surface path filter mirrors `pr-value`'s; it errs
  toward *asking* (an internal refactor takes a one-word `docs: none` label). Over-ask, never miss.
- **Ordering:** the `docs-current` workflow must be on `main` before it's added to branch
  protection (else every PR blocks on a check that doesn't run yet) — same as ADR-0030's merge-card.
- **Not covered** (honest): the gate checks that a docs *decision was made*, not that the docs are
  *correct* — content accuracy stays the reviewer's call (D6c's human-validated half).

## Enforcement map

`docs/docs/governance/delivery.mdx`: the D6c row moves from *TO BUILD (bundle checker)* to
**have (CI: `gate:docs-version` + `docs-current`)**.
