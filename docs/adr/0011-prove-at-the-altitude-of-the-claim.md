# ADR 0011 — Prove at the altitude of the claim (P19)

**Status:** accepted · 2026-06-19 · introduces **P19**

## Context

Several capabilities this milestone were marked "done" on **structural / contract green** (L1–L3)
and turned out not to work when actually run:

- the desktop+extension "live-see" lane was closed as done, yet Google Meet had never been tested
  with a second participant, YouTube transcription was intermittent, and the STT service was returning
  `402` — none visible until we drove the live system;
- a "recording.v1 Python↔TS conformance gate" was recorded as delivered, but the Python twin and the
  gate **did not exist** (only the TS side was tested).

The L1–L4 validation pyramid already exists as a *mechanism* (§5), and P8/P9 say "green or it didn't
happen." The gap: nothing bound a **claim** to the **altitude of proof** it requires. "Green" was read
as "works," when it only meant "L1–L3 structure is consistent." For a user-facing behaviour, that is
necessary but not sufficient.

## Decision

Adopt **P19 — Prove at the altitude of the claim.**

- A capability is "done" only when proven at the level it operates. A **user-facing behaviour** requires
  **L4** evidence — a live run scored by the `eval/` harness against ground truth, recorded as a
  committed artifact that meets/▮exceeds the lane's baseline. Structural/contract green (L1–L3) is
  necessary, not sufficient.
- **Name the altitude.** A "green" claim states *which* level it rests on (L1 contract · L2 unit · L3
  integration · L4 live). The proof obligation scales with the claim's blast radius.
- **Gate it (`gate:eval-baseline`, ADD).** Each user-facing lane (gmeet · zoom · teams · youtube · the
  bot end-to-end) carries a recorded L4 eval artifact; the lane is not "done" without it. The eval
  harness becomes a *required gate*, not a manual convenience.

## Consequences

- Marking a lane done now costs an L4 run (the eval rig: a working STT, a real/synthetic meeting). That
  cost **is the point** — it is exactly what surfaces "STT unpaid," "gmeet untested," "stream not
  minted." Cheaper to pay per-lane than to discover in production.
- Honest status: until its L4 artifact exists, a lane is "code-complete, L4 pending," not "done." The
  two over-claims above are reclassified accordingly.
- Two adjacent gaps surfaced in the same trace — **bounded resources / backpressure** and **single
  source of truth (committed + reconciled)** — are noted as *candidate* principles but **not adopted**
  yet: each rests on a single instance, below the bar for a gated rule (we adopt a principle only when a
  gap recurs *and* no existing principle covers it).
