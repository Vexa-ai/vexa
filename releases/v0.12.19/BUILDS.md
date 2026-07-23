# v0.12.19 — build and validation ledger

Date: 2026-07-23 · status: **DEV IN PROGRESS — NOT STAGE-ELIGIBLE YET**

## Source provenance

- Canonical v0.12.18 base:
  `83e4c9308c4f9c95d50d43caf5235e62bf3b99e4`
- Current implementation head:
  `781deaf79b43b004198ddb22fd2792a386561303`
- Branch: `codex/rc-01219-transcription`
- Scope impact: bot, shared record-chunker, mixed/gmeet transcription fixtures and evaluation,
  plus one MCP protocol test. No schema, database, public API, or architecture-carrier change.
- Stage remains owned by v0.12.18. No stage or production mutation has been made.

## Validation ledger

| leg | current result | evidence / remaining work |
|---|---|---|
| Record-chunker build + tests | ✅ green | two suites; pending-data-before-final regression included |
| Bot build | ✅ green | TypeScript + shipped browser bundle |
| Complete bot test suite | ✅ green | includes headless page boundary, replay, mock fidelity, recording, teardown races |
| #934 independent diff review | ✅ pass | two tail-ordering findings corrected before commit |
| MCP service | ✅ green | PR #891 targeted 1/1; integrated service 74/74 |
| Flat transcript oracle | ⏳ rerun required | prior accepted arm: WER .1235, CER .0911, word yield .952, 37 segments, 85 STT calls, 0 faults |
| Full repository gates | ⏳ final-head rerun required | an in-flight pre-fix run was intentionally invalidated; it also exposed unrelated local DB-seal formatting drift to interpret |
| Lite build + run | ⏳ pending | required by `dev:builds` |
| Compose build + run | ⏳ pending | required by `dev:builds` |
| Immutable RC images | ⏳ pending | tag and manifest/index digests will be recorded after final v0.12.18 handoff/rebase |
| Stage | ⛔ not requested | wait for explicit v0.12.18 stage handoff |

## Release assembly hold

Do not bump the three version stamps or collect changelog fragments until v0.12.18 completes its
handoff. The current tree contains fragments owned by both trains; v0.12.18 must consume its own
fragments first. After the final rebase, v0.12.19 will set root version/appVersion/docs-reflects,
collect only its surviving fragments, rerun every invalidated gate, and build exact-head images.
