# ADR 0003 — Data, secrets, multitenancy & the deferred-seam approach (P15, P16)

**Status:** accepted · 2026-06-18

## Context
A meeting product holds sensitive transcripts + user secrets, and must serve both self-host
(sovereign, single-org) and a future managed multi-org offering — without refactoring the current
single-user DB now.

## Decision

**Two distinct layers — do not conflate.**
- **Tenant isolation = the org boundary**, a *deploy-time* choice: DB/instance-per-tenant (sovereign;
  reuses the single-tenant design) or row-level `org_id` + RLS (managed SaaS). The current
  single-user DB is correct for self-host — not debt.
- **Access control = intra-org sharing**, the universal layer: owner + access-grants + visibility,
  enforced by a `canAccess(subject, resource, action)` port on the **three read paths** — API, live
  WS subscribe, and the agent. Sharing a transcript = one grant row; per-tenant encryption ⇒ no
  re-encryption to share.

**Data & secrets (P15).**
- Data (transcripts/recordings/workspace) → per-tenant envelope encryption (crypto-shreddable, BYOK).
- Secrets → a vault behind a port; auth tokens hashed, integration/user keys encrypted.
- The agent gets **scoped, brokered, audited** secret access — never raw keys in its workspace or logs.

**Deferred-seam approach (P16).**
- Build none of the above now. Install **ports with passthrough defaults** (crypto, secrets, audit,
  `canAccess`, tenant-resolution) and **thread `tenantId`/`ownerId`/`visibility` into every contract**
  (optional, defaulted). Real adapters + the additive `access_grants` table land later — drop-in.

## Consequences
- The DB is untouched now; multitenancy + sharing are additive future builds, enabled by the contract
  fields + the ports being wired today.
- Cross-org (federation) sharing is explicitly out of scope.
