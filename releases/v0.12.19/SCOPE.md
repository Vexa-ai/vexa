# v0.12.19 — admitted scope

**Release position:** transcription quality, validated contributions, and critical production
hardening.

Date: 2026-07-24 · delivered base: annotated tag `v0.12.18`
(`b878dc9ab2bbd4d9f94d29f424147fca4bee3e0e`) peeling exactly to
`1b62993e7e97c6ee04a5dcb116f7749ec74169df` · rebased value head:
`ede4f873f55950bc7286a63549eb3ab72f6012d8`

This is the frozen admission ledger. The train entered candidate-stage before the public
v0.12.18 tag existed; the accepted 77-commit value line has now been replayed from the earlier
`83e4c9308c4f9c95d50d43caf5235e62bf3b99e4` base onto the exact delivered tag. `git range-diff`
maps all 77 commits one-for-one as unchanged and the complete binary product delta has the same
SHA-256 before and after rebase:
`8e395e4ec919cdef4e684bba81eacb3e0cdc781cf0663b44df6efaf5488b264c`.
No new product value is admitted during final assembly.

## Admitted

| item | value | admission basis | credit |
|---|---|---|---|
| #869 / #847 / #848 | transcription framework and fixture corpus | mandated accepted transcription line; deterministic replay/oracle evidence | author: Dmitriy Grankin |
| #871 / #850 | mixed capture delivers the audio it hears | continuous-source duty 65.0% → 99.9%; live Zoom cross-check | author: Dmitriy Grankin |
| #872 / #849 | attribution quality is measured | four offline attribution signals and live counters | author: Dmitriy Grankin |
| #873 / #852 | Zoom bot watcher/layout correction | same-room live 0 → 105 crossed hints; extension remained the negative control | author: Dmitriy Grankin |
| #880 / #854 | known-truth transcript oracle | repeatable word/speaker score without a meeting | author: Dmitriy Grankin |
| #882 / #868 | mixed-lane binder quality | provisional words 31.9% → 2.4%; truth attribution 0.947 → 1.000 | author: Dmitriy Grankin |
| PR #891 / #888 | real MCP client witnesses the mounted transport | implementation-complete, non-author value-signed, exact head approved, 74/74 integrated tests | contributor: @adity982 · validator: @DmitriyG228 |
| #934 | bounded post-verdict teardown | explicit prod incident; A1–A4 green, source A5 green, and pre-rebase immutable-image Kubernetes cleanup green; post-rebase delivered-shape recheck remains | reporter/author: Dmitriy Grankin · independent agent witness recorded issue-native |

The six transcription PRs are historical review/evidence lineages. Their accepted commits are
assembled linearly on this release branch; v0.12.17 is not a publishable train or tag.

## Inherited, not a v0.12.19 delta

- #890 stable unknown-speaker display is carried by delivered v0.12.18. Only internal `seg_N`
  labels become `Speaker`;
  real names and unique repaint keys survive. This is orthogonal to deferred Teams real-name
  attribution. Its release close-back remains with the v0.12.18 publication owner.
- #674 terminal WebSocket handling is implemented by maintainer PR #761
  (`680f0828b730252e72d0e4cf5e0df4bb9757eacf`) in the delivered v0.12.18 base. Contributor
  @rainhotel supplied the complete author-side acceptance map; independent WS-drop and 404/409
  browser-boundary rows are green on `efc684a8`. Contributor custody and credit remain intact.
  v0.12.19 owns only the final immutable Helm WS/login + stale-control delivered-shape row.

## Technical prerequisite carried without independent product admission

- `e89317f8` / #839 supplies the draft/confirmation identity invariant required at the point of
  introduction by admitted flat-quality commit `69852f67`. The prerequisite remains in the source
  line, but #839's product promise, milestone, lifecycle acceptance, and changelog claim stay owned
  by v0.12.20. Final assembly therefore does not publish the #839 changelog fragment.

## Explicitly excluded or held

| item | ruling |
|---|---|
| #870, #896 | MS Teams real-name speaker attribution deferred by the human witness; transcription content ships |
| #886 | cadence acceptance incomplete: confirmed text remains 6.2s against a ≤5s target |
| #935 | valid prod-critical candidate, but still incoming, unimplemented, and requires a broader multi-replica rehearsal |
| #923 | large Valkey/deployment-surface change; not cheap to revalidate in this train |
| #928 | no non-author value adoption or diff approval |
| #902 | 29-file / 1,042-addition transcription change without a non-author value sign |
| #887 | live platform legs remain incomplete |
| #881 | no non-author value sign; admission behavior is outside the accepted transcription objective |
| #884, #796 | governance/release mechanics, outside the narrow product objective |
| #926, #927 | current signals are not prepared, implemented, or value-validated for this train |
| #839, #844, #841, #861, #552, #937 | owned by v0.12.20 — meeting lifecycle and operator truth; #839 code is carried only under the prerequisite ruling above |

Scope is frozen. Only a release blocker or a new prod-critical finding may change this list.
