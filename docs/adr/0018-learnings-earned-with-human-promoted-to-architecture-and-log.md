# ADR 0018 — Learnings are earned with the human and promoted to the architecture + a learnings log

**Status:** accepted · 2026-06-20 · composes ADR-0014/0017 (§8)

## Context

Unexpected objective-closures produced real learnings this milestone (P18–P21, the validation economy,
the report discipline), but they were captured *only* as scattered ADRs. Two gaps: (1) a learning's
provenance — the *surprise → root-cause* narrative — wasn't recorded in one running place; and (2)
learnings that became a **practice** rather than a numbered principle (e.g. "write-capable agents on a
dirty tree destroy uncommitted work") had nowhere to live at all.

## Decision

- **A learning is *earned with the human.*** It is produced by an **unexpected** objective-closure
  (ADR-0017), interpreted *with* the human (the ground-truth interpreter) — never minted autonomously
  from an instrument alone.
- **A learning is promoted *twice*:**
  1. **To the architecture** — codified as a principle + its gate + an ADR, so it *bites* (P9). Applies
     when the learning is a generalizable rule.
  2. **To the learnings log** (`docs/LEARNINGS.md`) — the chronological ledger of `surprise → root-cause
     → learning → promotion`, appended every time. Applies **always**, including for learnings that
     become a *practice* or a *candidate* (single instance, below the principle bar) and so have no
     P-number.
- The two are complementary: the **ADR** is the durable *decision*; the **log** is the running *record*
  and the index of where each learning landed.

## Consequences

- `docs/LEARNINGS.md` is appended on every unexpected closure; it is the single place to read "what we've
  learned and where it went." Backfilled with this milestone's twelve learnings.
- Candidate/practice learnings are no longer lost — they wait in the log until a second instance promotes
  them to a principle.
- Closes the loop's tail: §8 step 5 now ends at *both* destinations, not just the architecture.
