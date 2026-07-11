---
name: Prepared issue (contributor-prepared)
about: Prepare an issue to the delivery standard yourself — a maintainer reviews and stamps it `state: ready`. See docs/DELIVERY.md.
labels: ["state: prepared"]
---

<!-- The body IS the delivery constitution applied (docs/DELIVERY.md D5/D6/D10).
     A maintainer stamps `state: ready` only when every section below holds. -->

### Why this matters
<!-- Dry, factual business stakes: what a user loses, what depends on it, what was observed and where. No drama. -->

### Value this issue delivers
> <!-- ONE sentence a human can witness: the smallest holistic thing someone recognizes as "this delivers value to me". -->

### Where we are (honest)
<!-- Current-code facts, file:line where known. Old reports contribute symptoms only. -->

### The components (validation waypoints of the ONE PR that delivers this issue)
- [ ] **C1** — <business-named waypoint>

## C1 · <name>
**Target: ONE module or ONE seam — `path/to/it` (a solution needing two modules is two components).**
**Value:** <what this component makes true>
**Prepared solution:** <steps ready to execute — files, changes, why. Alternates welcome, never required.>
**Along the way:** <the forks: possible problems → their solutions. "Implemented as written and it doesn't work" is a result we want — sign it INVALIDATED.>
**Early validation:** <the check at this module's own altitude — its harness/fixture lane, red→green.>

### The acceptance table — present these observations and your PR merges
<!-- The floor that guarantees merge — never a ceiling on value. Discriminating (red→green), controlled (negative control shown red), anchored (shas/ids/timestamps), complete (no-regression row). -->
| # | Observation | Negative control (shown RED) | Anchor |
|---|---|---|---|
| A1 |  |  |  |
| A- | No-regression: touched lanes + repo gates green at head | — | CI at head sha |

### How this issue closes (the live validation — the human part)
<!-- Scale to the observation: speaker behavior 2–5 people; join/API one operator; a parser none. Never more humans than the observation needs. A non-author signs the value attestation; the originating reporter is the preferred signer. -->

### Authorship
Any tools, agents included — this issue is written to be handed to one. No agent co-author
trailers: what you ship is yours — full responsibility, honored as full authorship and credit.
