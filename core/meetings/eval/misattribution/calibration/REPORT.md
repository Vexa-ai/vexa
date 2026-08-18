# Calibration + first sweep — 2026-08-18

Corpus: the meetings of one owning production user, read-only. 777 meetings
inventoried, 766 with a stored transcript, **107 judged** (≥2 distinct speaker
labels and ≥20 segments), 32,568 segments.

Refs [#1224](https://github.com/Vexa-ai/vexa/issues/1224),
[#1221](https://github.com/Vexa-ai/vexa/issues/1221).

## 1. Calibration — the two witnessed meetings

Ground truth is the true owner of each virtual track, established independently
of this tool: for 26424 by the offline replay in #1221 (csrc separation with
zero flips, DOM-hint × CSRC purity 84.8% / 88.1%, settled-window namer replay);
for 26298 by the channel-purity measurement in the same issue.

| | 26424 (teams, csrc lane) | 26298 (gmeet, channel lane) |
|---|---|---|
| known failure | clean two-way swap, both tracks | per-segment label churn, 12 of 114 segments |
| segments | 252 | 114 |
| judge flags emitted | 3 | 2 |
| scorer contradictions | 3 | 0 |
| **flagged precision** | **1.0** (3/3) | no flag raised → **0 false positives** |
| track precision | 1.0 (2/2) | n/a — no track-level error to find |
| track recall | 1.0 (2/2) | n/a |
| segment recall | 0.0119 (3/252) | 0.0 (0/12) |

**Combined flagged precision across both calibration meetings: 1.0 (3 / 3).**
Zero false positives. The gate in #1224 is met.

### What 26298 teaches, and it is not a failure of precision

The judge did fire twice on 26298 — both genuine vocatives — and the scorer
correctly declined to call either an error, because both segments carried the
label consistent with the vocative. Meanwhile all 12 truly mislabeled segments
are short backchannels: *"in any way"*, *"Yeah, and"*, *"So it's good"*,
*"token on, yeah"*. None contains a name, so no linguistic signal can reach
them at any prompt quality.

That is the structural ceiling of this method and it is worth stating plainly:
**vocative and self-identification find swapped *tracks*, not churning
*labels*.** The GMeet per-chunk naming failure — where the label is stamped
from a global "which tile is glowing" check and flips mid-track — is precisely
the failure mode these two signals cannot see. It needs the mechanical lens
(channel purity on the tape), and the GMeet lane records no csrc/observations
to run that lens against. Two independent findings, same root: #1221's capture
gap is what caps GMeet at silver.

Segment recall is therefore reported, not optimised. Driving it up would mean
admitting weaker signal classes, which is exactly the trade the scope guard
refuses.

## 2. Corpus inventory

| lane | meetings | segments | usable flags | contradictions | tape tier |
|---|---|---|---|---|---|
| `csrc` (CSRC virtual channels, teams) | 2 | 301 | 3 | 3 | 2 × A |
| `channel` (gmeet per-participant `ch-N`) | 15 | 2,727 | 15 | 0 | 5 × B, 10 × none |
| `mixed` (legacy single-stream + DOM hints) | 3 | 1,434 | 3 | 1 | 3 × A |
| `unkeyed` (session-scoped segment ids) | 24 | 3,932 | 23 | 2 | none |
| `none` (**no `segment_id` field at all**) | 63 | 24,174 | 0 | 0 | none |

Tape tiers: **A** = `captured-signal` + `csrc` + `observations` (mechanically
replayable); **B** = `captured-signal` only, no cross-check signal. 35 signal
tapes exist in total for this user, all from meeting 26025 onward; 14 are tier A.

**The largest single finding of the sweep is that last row.** 63 of 107 judged
meetings — 24,174 of 32,568 segments, 74% of the corpus — store no
`segment_id` on their segments at all. The judge ran on them and returned
evidence; every row was dropped at ingest because there is nothing to join it
to. Of 162 raw judge flags, **118 were discarded for this reason alone**, and
44 survived. Those meetings cannot host a fixture, cannot be regression-tested,
and cannot contribute to a fleet metric, no matter what the pipeline does to
them. Restoring a stable per-segment id to that lane would multiply this
fixture set several-fold at zero additional judging cost.

Meetings with ≥2 labels but <20 segments, and single-label meetings, were not
judged: a single-label transcript has nothing to contradict.

## 3. Fixtures produced

5 fixtures — **2 gold, 3 silver**. Full detail in
[`../fixtures/manifest.json`](../fixtures/manifest.json).

| fixture | meeting | platform | lane | tier | evidence |
|---|---|---|---|---|---|
| `mis-26424-csrc-201` | 26424 | teams | csrc | **gold** | 2 vocatives naming the track's own label |
| `mis-26424-csrc-840` | 26424 | teams | csrc | **gold** | 1 vocative naming the track's own label |
| `mis-26042-turn` | 26042 | teams | mixed | silver | 1 vocative — a greeting addressed to the person the segment is attributed to |
| `mis-15496-…` | 15496 | google_meet | unkeyed | silver | 1 vocative |
| `mis-12304-…` | 12304 | google_meet | unkeyed | silver | 1 vocative |

Both gold fixtures are the 26424 swap, which #1221 already established the
rc.17 candidate does **not** fix (track 201 is accepted as the wrong name at
+65.5s on 3.8s of evidence, before the roster has settled, and acceptance is
never revisited). This manifest makes that a standing regression gate rather
than a one-off investigation.

`mis-26042-turn` is the interesting silver. Its tape carries csrc frames and
observations — the audio **was** separable, cleanly, into two tracks (csrc 830
and 201, 253 frames) — but the lane that produced its transcript emitted one
undifferentiated `turn` bucket, so no segment can be tied back to a track. The
evidence is a greeting addressed to the very person the segment is attributed
to. It stays silver because "which track" has no answer in that lane, not
because the evidence is weak.

## 4. Judge provenance

Prompt sha256 is recorded in the manifest; the chunk size is 120 segments; the
judge is label-blind and the scorer is deterministic, so re-running `ingest` →
`score` → `build_manifest` on the stored responses reproduces this manifest
exactly (`--backend prepared`).

This run used the `prepared` backend: no `ANTHROPIC_API_KEY` was provisioned on
the operator's machine and the `claude -p` CLI could not authenticate, so the
model was driven through the session harness against the tool's exact generated
prompts, and its responses were written to each bundle's `responses/` directory.
The `anthropic` backend is the intended path for scheduled runs and needs only
the key.

## 5. Known limitations

- **Out-of-roster names are not pseudonymized.** Redaction covers roster
  participants — the speaker labels and the declared attendees. A third party
  merely mentioned in conversation keeps their name in the judge's input. They
  are never evidence (they are not on the roster, so `ingest` drops any flag
  naming them) and nothing about them is committed, but the judge input is not
  a fully anonymized artifact and must not be treated as one.
- **Exact collisions between a participant name and a common word are
  unavoidable.** A participant named "Will" means every modal verb is redacted.
  The fuzzy path is guarded by a common-word list; the exact path cannot be.
- **Track verdicts on `mixed` / `unkeyed` lanes are session-scoped**, since
  those lanes emit one bucket for the whole meeting. The meaningful unit there
  is the flagged segment, not the "track".
