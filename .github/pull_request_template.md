<!-- The PR carries TWO artifacts, judged on different axes (docs/DELIVERY.md D8):
     the OBSERVATION BUNDLE answers "is the value real?"; the DIFF answers "is it correct and safe?".
     A diff with no bundle is not reviewable. -->

**Delivers issue:** #

## Observation bundle (the record of your harnessed loop)
<!-- One entry per component: what you ran, what you saw with your own eyes, what it told you
     about the next step. Your claim heartbeats are the natural front of this. A component that
     proved unnecessary, with evidence, is a completed waypoint. -->

- **C1 —** ran: … · saw: … · concluded: …

## Acceptance floor
<!-- Map each row of the issue's acceptance table to its evidence (red→green outputs with base+head
     shas, negative controls shown red, anchors). Rows you exceeded with NEW witnessed value:
     welcome — describe them, that's the system working. -->

| Row | Evidence |
|---|---|
| A1 |  |

## Security checks (required on the diff)
<!-- Dependency/licence scan, secrets scan, SAST where it applies — show the runs.
     The maintainer runs the closing security bundle before release. -->

## Validation request
<!-- Who should witness the value (any competent non-author; the originating reporter preferred)
     and what they'll watch. The attestation must corroborate with the instrument channels —
     a human/instrument divergence blocks merge until reconciled, and is a finding. -->

## Authorship
Sole author: the human submitting this. No agent co-author trailers (D13).
Tooling disclosure (optional, welcome, never an attribution): …
