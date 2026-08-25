# Namer corpus scorecard — `fix/namer-roster-settle` vs its parent commit

Every Teams tape reachable to this workspace, replayed through both namers with identical
inputs. Names are pseudonymized per tape (`P1`, `P2`, …); the map is operator-side.

**No regressions.** No track that the pre-fix namer named correctly is named wrong,
or left unnamed, by the fix.

## Totals (scored tracks only)

| | correct | wrong | unnamed |
|---|---|---|---|
| baseline | 27 | 1 | 2 |
| fix | 29 | 0 | 1 |

Ground truth on 26424 is corroborated by all three independent lines — mechanical
hint × csrc correlation, the settled-window control replay, and the label-blind
vocative / self-identification judge of Vexa-ai/vexa#1224 (flagged precision 1.0 in
Vexa-ai/vexa#1225). Every other tape rests on the first two.

Corpus: **25 tapes**, 48 tracks, of which **30 carry established ground truth** and 18 are
excluded. Tracks whose name changed between the arms: **2**.

## Corpus

| tape | source | image | duration | tracks | truthed | csrc edges | hints | roster |
|---|---|---|---|---|---|---|---|---|
| 25690 | local-harvest | `v0.12.22-rc.1` | 62:45 | 2 | 2 | 692 | 1405 | 10 |
| 26026 | prod-signal-tape | `v0.12.22-rc.4` | 3:11 | 1 | 1 | 62 | 87 | 4 |
| 26027 | local-harvest | `v0.12.22-rc.4` | 9:49 | 6 | 1 | 390 | 924 | 25 |
| 26040 | prod-signal-tape | `v0.12.22-rc.4` | 1:48 | 1 | 1 | 20 | 51 | 4 |
| 26042 | prod-signal-tape | `v0.12.22-rc.4` | 21:12 | 2 | 2 | 252 | 870 | 9 |
| 26043 | prod-signal-tape | `v0.12.22-rc.4` | 42:01 | 2 | 2 | 918 | 2179 | 11 |
| 26062 | prod-signal-tape | `v0.12.22-rc.4` | 0:49 | 1 | 1 | 20 | 28 | 4 |
| 26063 | prod-signal-tape | `v0.12.22-rc.4` | 0:40 | 1 | 0 | 14 | 0 | 1 |
| 26064 | prod-signal-tape | `v0.12.22-rc.4` | 66:29 | 1 | 1 | 750 | 2838 | 8 |
| 26073 | prod-signal-tape | `v0.12.23-rc.3` | 0:44 | 1 | 1 | 2 | 46 | 4 |
| 26086 | prod-signal-tape | `v0.12.23-rc.3` | 14:23 | 1 | 0 | 14 | 0 | 5 |
| 26088 | prod-signal-tape | `v0.12.23-rc.5` | 10:55 | 1 | 1 | 18 | 368 | 4 |
| 26112 | local-harvest | `v0.12.23-rc.6` | 6:48 | 5 | 0 | 130 | 270 | 13 |
| 26121 | local-harvest | `v0.12.23-rc.6` | 17:03 | 3 | 2 | 260 | 825 | 12 |
| 26123 | local-harvest | `v0.12.23-rc.6` | 40:53 | 4 | 2 | 678 | 2114 | 15 |
| 26132 | local-harvest | `v0.12.23-rc.6` | 57:42 | 2 | 2 | 1073 | 3373 | 7 |
| 26164 | local-harvest | `v0.12.23-rc.6` | 0:50 | 1 | 1 | 2 | 28 | 5 |
| 26299 | prod-signal-tape | `v0.12.23-rc.10.packet2` | 13:11 | 2 | 2 | 100 | 178 | 9 |
| 26345 | prod-signal-tape | `v0.12.23-rc.10.packet2` | 0:56 | 1 | 1 | 22 | 37 | 4 |
| 26357 | prod-signal-tape | `v0.12.23-rc.10.packet2` | 0:37 | 1 | 0 | 12 | 0 | 4 |
| 26424 | prod-signal-tape | `v0.12.23-rc.10.packet2` | 28:07 | 2 | 2 | 330 | 1253 | 9 |
| m30-final | local-harvest | `8d7f624c` | 7:10 | 2 | 1 | 153 | 189 | 0 |
| m34-final | local-harvest | `453d37c1` | 10:38 | 2 | 2 | 199 | 220 | 9 |
| m34-hot | local-harvest | `453d37c1` | 4:45 | 2 | 1 | 135 | 110 | 6 |
| m36 | local-harvest | `453d37c1` | 4:45 | 1 | 1 | 56 | 88 | 3 |

## Every track

| tape | track | truth | purity | baseline | fix | control | verdict b→f |
|---|---|---|---|---|---|---|---|
| 25690 | csrc:1043 | P2 | 0.80 | P2 | P2 | P2 | correct → correct |
| 25690 | csrc:201 | P1 | 0.99 | P1 | P1 | P1 | correct → correct |
| 26026 | csrc:201 | P1 | 1.00 | P1 | P1 | — | correct → correct |
| 26027 | csrc:1479 | — | — | — | — | — | excluded → excluded |
| 26027 | csrc:1905 | — | — | P2 | P2 | — | excluded → excluded |
| 26027 | csrc:201 | — | — | P1 | P1 | P1 | excluded → excluded |
| 26027 | csrc:2331 | — | — | P5 | P5 | P5 | excluded → excluded |
| 26027 | csrc:414 | — | — | P6 | P6 | P6 | excluded → excluded |
| 26027 | csrc:627 | P4 | 0.85 | P4 | P4 | P4 | correct → correct |
| 26040 | csrc:201 | P1 | 1.00 | P1 | P1 | — | correct → correct |
| 26042 | csrc:201 | P2 | 0.88 | P2 | P2 | P2 | correct → correct |
| 26042 | csrc:830 | P1 | 0.91 | P1 | P1 | P1 | correct → correct |
| 26043 | csrc:201 | P1 | 0.81 | P1 | P1 | P1 | correct → correct |
| 26043 | csrc:840 | P2 | 0.82 | P2 | P2 | P2 | correct → correct |
| 26062 | csrc:201 | P1 | 1.00 | P1 | P1 | — | correct → correct |
| 26063 | csrc:201 | — | — | — | — | — | excluded → excluded |
| 26064 | csrc:201 | P1 | 1.00 | P1 | P1 | P1 | correct → correct |
| 26073 | csrc:201 | P1 | 1.00 | P1 | P1 | — | correct → correct |
| 26086 | csrc:201 | — | — | — | — | — | excluded → excluded |
| 26088 | csrc:201 | P1 | 1.00 | P1 | P1 | — | correct → correct |
| 26112 | csrc:1053 | — | — | P2 | P2 | P2 | excluded → excluded |
| 26112 | csrc:1266 | — | — | P1 | P1 | P1 | excluded → excluded |
| 26112 | csrc:201 | — | — | — | — | — | excluded → excluded |
| 26112 | csrc:414 | — | — | P1 | P1 | P1 | excluded → excluded |
| 26112 | csrc:840 | — | — | — | — | — | excluded → excluded |
| 26121 | csrc:1266 | P3 | 0.85 | P3 | P3 | P3 | correct → correct |
| 26121 | csrc:627 | — | — | P1 | P1 | P1 | excluded → excluded |
| 26121 | csrc:840 | P2 | 0.81 | P2 | P2 | P2 | correct → correct |
| 26123 | csrc:1053 | — | — | P4 | P4 | P4 | excluded → excluded |
| 26123 | csrc:201 | P3 | 0.82 | P3 | P3 | P3 | correct → correct |
| 26123 | csrc:414 | — | — | P1 | P1 | P1 | excluded → excluded |
| 26123 | csrc:840 | P2 | 0.85 | P2 | P2 | P2 | correct → correct |
| 26132 | csrc:201 | P1 | 0.83 | P1 | P1 | P1 | correct → correct |
| 26132 | csrc:414 | P2 | 0.86 | — | — | — | unnamed → unnamed |
| 26164 | csrc:201 | P1 | 1.00 | P1 | P1 | — | correct → correct |
| 26299 | csrc:201 | P1 | 1.00 | P1 | P1 | P1 | correct → correct |
| 26299 | csrc:627 | P2 | 1.00 | P2 | P2 | P2 | correct → correct |
| 26345 | csrc:201 | P1 | 1.00 | P1 | P1 | — | correct → correct |
| 26357 | csrc:201 | — | — | — | — | — | excluded → excluded |
| 26424 | csrc:201 | P1 | 0.85 | P2 | P1 | P1 | wrong → correct **Δ** |
| 26424 | csrc:840 | P2 | 0.88 | — | P2 | P2 | unnamed → correct **Δ** |
| m30-final | csrc:1266 | P1 | 1.00 | P1 | P1 | — | correct → correct |
| m30-final | csrc:201 | — | — | — | — | — | excluded → excluded |
| m34-final | csrc:201 | P1 | 0.89 | P1 | P1 | P1 | correct → correct |
| m34-final | csrc:414 | P3 | 1.00 | P3 | P3 | — | correct → correct |
| m34-hot | csrc:201 | — | — | — | — | — | excluded → excluded |
| m34-hot | csrc:414 | P2 | 1.00 | P2 | P2 | P2 | correct → correct |
| m36 | csrc:201 | P1 | 1.00 | P1 | P1 | — | correct → correct |

## Exclusions — every track the oracle could not truth, and why

No silent drops. A track is excluded when the mechanical oracle cannot establish who owns
it, never because the result was inconvenient; both arms are still replayed on it and any
behavioural difference is still reported above.

| tape | track | reason |
|---|---|---|
| 26027 | csrc:1479 | no truth: leading name purity 0.50 < 0.8 |
| 26027 | csrc:1905 | no truth: leading name purity 0.62 < 0.8 |
| 26027 | csrc:201 | no truth: leading name purity 0.50 < 0.8 |
| 26027 | csrc:2331 | no truth: leading name purity 0.69 < 0.8 |
| 26027 | csrc:414 | no truth: leading name purity 0.60 < 0.8 |
| 26063 | csrc:201 | no truth: 0 unambiguous hint instants (< 5) |
| 26086 | csrc:201 | no truth: 0 unambiguous hint instants (< 5) |
| 26112 | csrc:1053 | no truth: "P2" is 0.45 concentrated on this track (< 0.7) |
| 26112 | csrc:1266 | no truth: "P1" is 0.29 concentrated on this track (< 0.7) |
| 26112 | csrc:201 | no truth: 3 unambiguous hint instants (< 5) |
| 26112 | csrc:414 | no truth: "P1" is 0.62 concentrated on this track (< 0.7) |
| 26112 | csrc:840 | no truth: leading name purity 0.57 < 0.8 |
| 26121 | csrc:627 | no truth: leading name purity 0.78 < 0.8 |
| 26123 | csrc:1053 | no truth: leading name purity 0.76 < 0.8 |
| 26123 | csrc:414 | no truth: leading name purity 0.74 < 0.8 |
| 26357 | csrc:201 | no truth: 0 unambiguous hint instants (< 5) |
| m30-final | csrc:201 | no truth: "P1" is 0.09 concentrated on this track (< 0.7) |
| m34-hot | csrc:201 | no truth: "P2" is 0.14 concentrated on this track (< 0.7) |

### Row notes

- **26132 csrc:201** — The roster on this tape can NEVER settle. Its second participant's Teams display name is a lowercase topic handle, which `requireCanonicalDisplayName` correctly rejects as roster-polluting — so `roster.size` stays 1 while the transport carries 2 tracks, and `rosterSettled`'s not-provably-behind test is false for the whole meeting. This track's name therefore stays provisional to the end and is re-derived on every tick, and its owner-share sits at 0.71 against a 0.70 threshold (465.6 s of its own evidence against 188.5 s of bleed on the other track), so the verdict crosses the bar back and forth. Final name is correct in both arms and time-to-first-name is identical; the cost is four repaint cycles across 38 minutes.
- **26132 csrc:414** — Unnamed in BOTH arms, and not a namer-timing failure: this participant's Teams display name is a lowercase topic handle that `requireCanonicalDisplayName: true` refuses, on the pipeline's standing judgement that unknown beats a false attribution to a session subject. The mechanical oracle can still read the name off the DOM hints, which is why the row carries a truth at all. Out of scope for this fix.
- **26424 csrc:201** — The tape this fix was written against, and the founder witnessed it live. Accepted as P2 at +35 s on 3.8 s of evidence while the roster held one name and the second participant's first sighting was still 10 s away; permanent acceptance then made the following 93 % contrary evidence unusable.
- **26424 csrc:840** — The other half of the same failure: the name this track owns was locked to csrc:201, so exclusivity correctly refused it here and the track fell back to Speaker A for the whole meeting.

## Label churn — what publishing a revisable name costs

A retraction is a published human name that a later evaluation replaced. The pre-fix namer
cannot produce one: an evidence name was permanent. The fix trades that permanence for
correctness, and the trade is not free.

| | baseline | fix |
|---|---|---|
| retraction events, whole corpus | 0 | 5 |
| tracks whose label stream changed | — | 2 |

| tape | track | truth | fix label stream | final |
|---|---|---|---|---|
| 26132 | csrc:201 | P1 | P1@37.6s → Speaker A@38.7s → P1@1804.1s → Speaker A@1822.7s → P1@2029.7s → Speaker A@2053.4s → P1@2288.8s → Speaker A@2297s → P1@2306.6s | P1 (correct) |
| 26424 | csrc:201 | P1 | P2@35.1s → Speaker A@40s → P1@240.8s | P1 (correct) |

## Naming latency

| | |
|---|---|
| tracks where both arms published a name | 38 |
| of those, identical time-to-first-name | 38 |
| distinct first-name deltas (fix − baseline, s) | +0 |

