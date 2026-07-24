# v0.12.19 — development witness ledger

Date: 2026-07-24 · accountable human: Dmitriy Grankin · current delivered surface:
canonical candidate-stage/pre-rebase

## Preserved pre-rebase transcription observations

**Ship the transcription improvement. Defer MS Teams speaker attribution.**

| platform | human observation | machine lens | verdict |
|---|---|---|---|
| Zoom | “this bot looks good to me”; extension negative control also looked correct | live bot transcript rendered with the corrected capture-time path and Zoom speaker watcher | ✅ accepted |
| Jitsi | “imperfect but fine”; speaker labels appeared late | `jitsi/2026-07-23-cycle-08-live-witness` | ✅ transcription accepted · late attribution deferred |
| MS Teams | transcript content was improved; speaker attribution remained poor | meeting 13632 completed; 41 segments (Anna 20, Boris 16, Speaker 5); content recall .796, precision .906, final-label accuracy .506 | ✅ transcription accepted · attribution explicitly deferred |

Evidence roots:

- `/Users/dmitriygrankin/vexa-test-rig/fixtures/hot-debug/jitsi/2026-07-23-cycle-08-live-witness`
- `/Users/dmitriygrankin/vexa-test-rig/fixtures/hot-debug/teams/2026-07-23-cycle-12-chrome150-live-witness`

The Teams run used a Chrome 150 evaluation-only browser override to clear a local SDP
incompatibility. That override is not a release runtime change.

These observations calibrate the accepted value line, whose complete binary delta is unchanged
after rebase. They are not a final witness signature for the post-rebase artifact.

## Remaining human-quality row

The only human-owned quality row is a Jitsi transcript on the final immutable post-rebase
candidate. The agent must deliver the running stage dashboard and instrumental evidence; the human
alone records the quality verdict. No verdict is present yet, and none may be synthesized from the
pre-rebase observations above.

## #934 production-hardening witness

A1–A4 are green by offline red→green fixtures and independent review. A fresh source workload
observed `silence verdict → completed(left_alone)` in 1.641s and worker exit in 3.010s while
preserving the final phrase and recording-final signal. Candidate-stage rows 13634/13635 proved
the immutable pre-rebase bot image through Kubernetes, meeting-api/Postgres terminal persistence,
recording finalization, exit 0, and zero-bot cleanup.

After the post-rebase image deploy, repeat the delivered-shape cleanup seam. The human does not
need to re-witness this backend row; issue-native machine evidence owns it.

Do **not** use or terminate production meeting 24667. It remains the prod-owner continuity
sentinel and must survive the preceding rollout unchanged.

## #674 contributor custody and remaining row

Contributor @rainhotel supplied the author-side acceptance map. Independent non-author evidence
is green for WS disconnect/reconnect state, exact 404/409 human copy, raw-JSON exclusion, and
meetings re-snapshot. The Helm secret gap was the separately fixed #676, not a #674 prerequisite.

The release owner retains one post-rebase delivered-shape row: immutable Helm login/WS attachment
plus stale-control/404 reconciliation. Contributor custody and credit remain @rainhotel; no further
contributor work or human screenshot is requested.

## Remaining release witness and closure

- exact-head Lite and Compose validation;
- immutable post-rebase images and stage delivery rows;
- the one human Jitsi quality verdict;
- generated `witness.json` only after that delivered candidate exists and every batch row is
  resolved.
