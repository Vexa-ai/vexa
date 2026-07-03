# ADR 0012 — Complete mediation: authorize every access, default-deny (P20)

**Status:** accepted · 2026-06-19 · introduces **P20** · builds on ADR-0003

## Context

ADR-0003 separated two layers and named the universal one: **access control** = intra-org sharing,
enforced by a `canAccess(subject, resource, action)` port on the **three read paths** — API, live WS
subscribe, and the agent. That port was *designed* but **never wired**. Today the all-in-one desktop
serves:

- `GET /bots` — every meeting, no owner check;
- `GET /transcripts/{p}/{n}` and `GET /recordings/{p}/{n}` — any meeting's content;
- `WS /ws subscribe` — any meeting's live stream;

with no authorization at all (same-origin / localhost is the only defence). **P15** protects data *at
rest* (envelope encryption, secrets-as-a-class) but says nothing about *who may read it*. So an
authorization rule existed only in an ADR's prose and rotted — exactly what **P9** warns about.

## Decision

Adopt **P20 — Complete mediation: authorize every access, default-deny.**

- Every read/write of a user-owned resource passes a `canAccess(subject, resource, action)` check at
  **every** path (API · live subscribe · agent). The default is **owner-only**; no path may bypass it.
- **Wire the seam now, defer the policy (P16).** A `canAccess` port lands now with a default adapter
  (owner-only; for the single-user all-in-one desktop that adapter is trivially "the one local user").
  The three read paths route through it. Real sharing — `owner_id` / `visibility` contract fields + an
  `access_grants` table — is added **additively** when sharing ships (ADR-0003), drop-in behind the
  same port.
- **Gate it (`gate:access`, ADD).** A test asserts each read path **denies** an unauthorized request.
  A path with no `canAccess` call is a red gate, not a review nit.

## Consequences

- The desktop ships a thin owner-only adapter on localhost now; the **cloud meeting-api inherits the
  seam** rather than reinventing (or forgetting) authorization when it lands (P3). One place to make
  sharing correct later.
- Trade-off: a small port + a deny-test per path now, versus retrofitting authorization across every
  read path (and every future one) under deadline later — the classic "complete mediation is cheap up
  front, ruinous to bolt on" result (Saltzer–Schroeder).
- Cross-org (federation) sharing stays out of scope (ADR-0003).
