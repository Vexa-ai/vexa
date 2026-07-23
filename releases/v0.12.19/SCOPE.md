# v0.12.19 — admitted scope

**Release position:** transcription quality, validated contributions, and critical production
hardening.

Date: 2026-07-23 · canonical base:
`83e4c9308c4f9c95d50d43caf5235e62bf3b99e4` (`origin/rc/0.12.18`) · implementation head:
`781deaf79b43b004198ddb22fd2792a386561303`

This is the pre-stage scope decision. The v0.12.19 milestone remains a candidate pool; only the
items below are admitted. Scope freezes when the train enters stage.

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
| #934 | bounded post-verdict teardown | explicit current prod-owner incident; narrow A1–A4 implementation; live A5 still required | reporter/author: Dmitriy Grankin · validator: pending |

The six transcription PRs are historical review/evidence lineages. Their accepted commits are
assembled linearly on this release branch; v0.12.17 is not a publishable train or tag.

## Inherited, not a v0.12.19 delta

- #890 stable unknown-speaker display is already carried by canonical v0.12.18 commit
  `e617d8395b162df60fdc4bc5754bc1a887bd1bda`: only internal `seg_N` labels become `Speaker`;
  real names and unique repaint keys survive. This is orthogonal to deferred Teams real-name
  attribution and does not move to v0.12.20.
- #674 terminal WebSocket handling is already implemented by maintainer PR #761
  (`680f0828b730252e72d0e4cf5e0df4bb9757eacf`) in the v0.12.18 base. It still lacks its
  non-author live WS-drop witness and has a contributor-claim conflict; no duplicate code or
  authorship credit is admitted here.

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
| #839, #844, #841, #861, #552, #937 | owned by v0.12.20 — meeting lifecycle and operator truth; must not be re-added here |

After stage entry, only a release blocker or a new prod-critical finding may change this list.
