# LEARNINGS — the running ledger

Every **unexpected** objective-closure (reality ≠ expectation) is interpreted **with the human** as a
*learning*, and promoted **twice**: to the **architecture** (a principle + its gate + an ADR) *and* to
this log (ADR-0017/0018). ADRs capture the *decision*; this captures the *surprise → root-cause →
promotion* narrative, chronologically — including learnings that became a *practice*, not a numbered
principle.

| # | Date | Surprise (expected → actual) | Root-cause / gap | Learning | Promoted to |
|---|---|---|---|---|---|
| 1 | 06-19 | "no transcript" → STT **HTTP 402** out-of-balance, swallowed silently | dependency failure had no observable surface | **fail loud + attributable** — surface every dependency fault | P18 · ADR-0010 · `gate:fault-surfacing` |
| 2 | 06-19 | gate-green lane "done" → gmeet **never tested** (solo), youtube intermittent, STT dead | structural green (L1–L3) read as "works" | **prove at the altitude of the claim** — user-facing needs L4 | P19 · ADR-0011 · `gate:eval-baseline` (ADD) |
| 3 | 06-19 | `/transcripts`·`/recordings`·`/ws` assumed safe → **wide open** to any localhost reader | `canAccess` seam designed (ADR-0003) but never wired → rotted | **complete mediation** — authorize every access, default-deny | P20 · ADR-0012 · `gate:access` (ADD) |
| 4 | 06-19 | "Listening — capturing **0 stream(s)**" → state asserted from Start, no frames flowing | UI state from *intent*, not observed evidence | **report state from evidence, not intent** | P21 · ADR-0013 · `gate:client-liveness` |
| 5 | 06-19 | eval `capture` called a **healthy gmeet "unhealthy"** | instrument judged ch999 (mixed lane); gmeet uses per-participant ch0..N — wrong success-definition | **instruments are cheap but approximate** — can mis-define / mis-interpret | ADR-0014 · fixed `eval/src/capture.mjs` |
| 6 | 06-19 | "I updated the balance" / "gmeet doesn't transcribe" → 402 (wrong account) / solo tape | trusted human reports as definitive | **human is ground truth but fallible** — cross-validate both ways | ADR-0014 |
| 7 | 06-19 | audit subagent ran on the dirty tree → **destroyed the uncommitted `bot/` brick** (data loss) | write-capable agents on a dirty working tree | isolate audits read-only / in a worktree; **uncommitted work is at risk** | *practice* (candidate: single-source-of-truth) |
| 8 | 06-19 | "both vexa-secrets STT tokens = `32c5…`" → stage (44-char) ≠ prod (32-char); stage **403** | two secret sources drifted silently | single source of truth, **committed & reconciled** | *candidate principle* (held, single instance) |
| 9 | 06-19 | improvising next steps → drift risk without a path | no standing plan / mode discipline | **plan ⇄ execute**; a current plan always exists | ADR-0015 · RELEASE-PLAN.md |
| 10 | 06-20 | "P2-2a done — L3-validated" → hid that tests use **fakes**, no real broker | a report shipped an *evaluation*, not facts | **report facts, not evaluations** | ADR-0016 |
| 11 | 06-20 | report had no reference frame → result not assessable | objective not stated | **goal vs objective**; a report assesses *result vs objective* | ADR-0017 |
| 12 | 06-20 | learnings lived only in scattered ADRs → no running ledger | learnings not logged | **learnings earned with the human, promoted to architecture + this log** | ADR-0018 · this file |
