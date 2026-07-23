# v0.12.19 — development witness ledger

Date: 2026-07-23 · human witness: Dmitriy Grankin · delivered surface: local dashboard

## Accepted transcription verdict

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

## #934 production-hardening witness

A1–A4 are green by offline red→green fixtures and independent review. The live row is still open:

1. launch a fresh disposable release workload;
2. admit it to a real meeting and speak a final identifiable phrase;
3. leave the bot alone until the silence verdict;
4. observe `completed(left_alone)` and worker/pod exit within the declared bound;
5. verify the final phrase and recording completion survived teardown.

Do **not** use or terminate production meeting 24667. It remains the prod-owner continuity
sentinel and must survive the preceding rollout unchanged.

## Remaining release witness

- exact-head flat oracle rerun;
- lite and compose delivered-shape validation;
- immutable-image stage witness after v0.12.18 hands over stage;
- generated `witness.json` only after a stable v0.12.19 candidate exists.
