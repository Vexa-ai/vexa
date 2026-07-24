# v0.12.19 — build and validation ledger

Date: 2026-07-24 · status: **FINAL ASSEMBLY IN PROGRESS — PRE-REBASE CANDIDATE LIVE ON STAGE**

## Source provenance

- Public annotated base tag: `v0.12.18`, tag object
  `b878dc9ab2bbd4d9f94d29f424147fca4bee3e0e`, peeling exactly to
  `1b62993e7e97c6ee04a5dcb116f7749ec74169df`.
- Frozen pre-rebase candidate:
  `efc684a8d88cdd30fd34a67f6a23b0c4f6c66347`.
- Rebased value head:
  `ede4f873f55950bc7286a63549eb3ab72f6012d8`.
- Final assembly branch: `codex/01219-final-assembly`.
- Rebase proof: 77/77 commits map `=` in `git range-diff`; the binary delta checksum is identical
  before and after:
  `8e395e4ec919cdef4e684bba81eacb3e0cdc781cf0663b44df6efaf5488b264c`.
- Scope impact: bot, shared record-chunker, mixed/gmeet transcription fixtures and evaluation,
  plus one MCP protocol test. No schema, database, public API, or architecture-carrier change.
- Canonical stage owner and live Lease both read
  `019f902f-056d-7491-ab5b-1a128eeb9565` / `0.12.19`; the pre-rebase candidate remains the live
  rollback/witness baseline until post-rebase images pass build gates. Production is untouched.

## Validation ledger

| leg | current result | evidence / remaining work |
|---|---|---|
| Rebase seam | ✅ green | exact `.18` peel; 77/77 unchanged; binary delta checksum identical |
| Record-chunker build + tests | ✅ preserved evidence | two suites; product delta byte-identical after rebase |
| Bot build + complete tests | ✅ preserved evidence | shipped bundle, headless boundary, replay, recording and teardown races on frozen value line |
| #934 independent diff review | ✅ pass | two tail-ordering findings corrected before commit |
| MCP service | ✅ green | PR #891 targeted 1/1; integrated service 74/74 |
| Secretless serialized flat transcript oracle | ✅ preserved quality evidence | WER .115, CER .080, 559/583 word yield .959, 75/75 calls, zero STT faults; throughput observational only |
| Exact-head repository gates | ⏳ invalidated | new combined source + version/docs assembly requires static, node, python and release-tool tests |
| Version/docs/changelog gates | ⏳ invalidated | three 0.12.19 stamps, fragment collection, docs-version and collector check |
| Lite build + run | ⏳ invalidated | exact post-rebase head required by `dev:builds` |
| Compose build + run | ⏳ invalidated | exact post-rebase head required by `dev:builds`; PR #946 harness fix is now on main |
| Immutable post-rebase images | ⏳ pending | unique candidate tag plus full index/platform digests required |
| Candidate-stage/pre-rebase | ✅ green | exact `efc684a8` images live; nine Deployments + Postgres Ready; zero runtime-managed bots |
| Post-rebase stage | ⏳ pending | deploy only after exact-head build gates; re-run #674 and #934 delivered-shape rows |
| Human Jitsi quality row | ⏳ human-owned | final immutable post-rebase candidate only; no synthetic verdict |

## Evidence intentionally not invalidated

The source transition does not erase calibrated human observations or the secretless quality
oracle: the product delta is byte-identical and the delivered `.18` change set is disjoint from
the transcription/runtime paths. Those receipts remain evidence for the accepted value line.
They do not substitute for exact-head package builds, post-rebase stage deployment, or the one
pending human Jitsi verdict.
