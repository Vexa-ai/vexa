# v0.12.18 compound production + OSS delivery packet

This is the immutable content packet for the v0.12.18 delivery transaction.
It does not authorize credentials, cluster writes, tag creation, registry
writes, GitHub Release publication, or issue changes.

The approval receipt outside this repository must bind:

- the exact merged `Vexa-ai/vexa` commit that contains this packet;
- the exact reviewed `Vexa-ai/vexa-platform` production head;
- the platform deployment-input fingerprint;
- `releases/v0.12.18/candidate-images.json`;
- `releases/v0.12.18/RELEASE-NOTES.md`;
- the canonical biz ownership readback and the Kubernetes Lease readback.

Production delivery already holds green at revision 103. The founder's
2026-07-24 ruling decouples OSS publication from post-deploy platform
credential remediation #125: the public boundary completes when the exact
approved OSS tag, stable aliases, moving aliases, GitHub Release, and issue
close-backs all read back. No image rebuild or additional production/stage
mutation is authorized by that ruling.

## Production anchor

Current production is deliberately held at Helm revision `103`:

- source receipt: `Vexa-ai/vexa-platform@3f31acd74c339e44e3bf195d02535526eff222df`;
- core image tag: `v0.12.18-260723.stage2`;
- live manifest SHA-256:
  `1349a35787d791d13e2b8465ecb5b7f9656adc47367de461434d2da6a4d1092f`;
- live values SHA-256:
  `d5fd4569ba56de42e439455beffbe9db478bb01d0e383d434d3af4d2c74ea514`;
- Deployments: `14/14` Ready;
- production new-spawn witness: meeting `24740`, bot digest
  `sha256:9442a44558fd48950208cbef40673cc7a0b2feb41f380964fc74a0e25bf18fae`;
- continuity sentinel: meeting `24667`, same UID, Running/Ready, zero
  restarts, retained #934 truth;
- shared transcription: public health `200`, primary `false`, Cloudflare
  fallback functional, direct BBB not exercised, retained counters `22 → 22`.

Two production legs were prepared but remain deliberately unexecuted and are
not prerequisites for the approved OSS publication:

- leg 5 changes only `Deployment/vexa-platform-webapp`;
- leg 6 changes only `Deployment/vexa-platform-vexa-runtime`, updating
  `BROWSER_IMAGE` for future spawns without replacing any existing meeting
  Pod;
- exact platform candidate head submitted for independent review:
  `b30f5d5fcafb1e12ba75e8449e82879de475055f`;
- deployment-input fingerprint:
  `sha256:23617eb740568821c4440f6dd11e1de52fb6324dc0b7a3f9b4fce5a8f4162fa8`;
- image index:
  `sha256:4da42b396623bfe863c207a01f7175773a441b1c4bc70b4d342189fb205aa741`;
- production amd64 manifest:
  `sha256:297fc3cb778a39e293255222a932647c57f6b591e2032428c16c6789b55b5b44`;
- source revision:
  `67a39b7e366fd6aa794ee3134c3aadd925b8d9bc`;
- prepared future-spawn Bot descriptor:
  `sha256:a7f8feae7870b722e3542fb7cb054ff7c092e62f4c5a6b6a3b63e52f8cd1fe47`;
- object additions/deletions: `0/0` across both legs.

Rollback before either remaining leg is Helm revision `103`, with the manifest
and values hashes above. After each green leg, that revision becomes the nearer
rollback anchor. The whole-release emergency anchor remains revision `99`:

- manifest:
  `7c5739b83a06f798f6a59b7da38a827f34406cff4bcfcce97cff383b775e3110`;
- values:
  `7073fc08227587531c9405d04e62eb12cda49e8cdc850eed99837457726fdfa2`.

## OSS source and image identity

The exact public tag target is the reviewed merge of this integration into
`Vexa-ai/vexa/main`. The compound approval must name its full SHA after that
merge exists. No earlier RC, candidate-build, validation, or preparation SHA
is the public tag target.

The image build and source-tag identities remain deliberately distinct. Eight
frozen descriptors use the original candidate:

- base-eight image build source:
  `6b40fd49f9785c0b43ba28fe17e53753d96ff6da`;
- base-eight corrected validation source:
  `a4560ec8da1cd8aa3d8b9c91cde0af9352857f5a`;
- base-eight build run:
  <https://github.com/Vexa-ai/vexa/actions/runs/30033899550>;
- base-eight published-candidate validation:
  <https://github.com/Vexa-ai/vexa/actions/runs/30036135103>.

Bot and Lite use the bounded packet3 replacement run:

- source and validation SHA:
  `084cf51e7c83d12812b64307261ff21d4f92a96e`;
- run:
  <https://github.com/Vexa-ai/vexa/actions/runs/30068779645>;
- preflight artifact: all ten abandoned packet2 tags plus both packet3 targets
  returned conclusive authenticated `ABSENT` before any build;
- only Bot and Lite built; their dedicated Bot, Lite amd64, and native arm64
  validation lanes and descriptor receipt are green; stable publication was
  skipped.

`release/candidate-image-map.mjs check-source-inputs` proves, row by row, that
every path copied by each release Dockerfile is tree-identical between that
row's build source and the public tag target. A difference is a new candidate
and blocks aliasing. Documentation, workflow, receipt, and
release-governance changes do not enter an image.

For each frozen top-level descriptor, `candidate-images.json` also records the
selected `linux/amd64` and (where published) `linux/arm64` child manifests and
their image-config digests. Hosted runtime readback is therefore compared to
the exact platform identity, rather than incorrectly comparing a node's
single-platform imageID with a multi-platform index.

The stable publication path copies each descriptor in
`candidate-images.json` to `:v0.12.18` with
`docker buildx imagetools create --prefer-index=false`, then requires the
stable tag's top-level digest to equal the frozen candidate digest. It refuses
an existing mismatched stable tag. No build occurs. The normal release
validation then exercises `:v0.12.18`; only its green, human-gated promotion
moves `:v012`. `:latest` remains untouched.

Hosted production directly held the candidate-map admin API, runtime, meeting
API, and gateway descriptors, plus the stage2 Bot
`sha256:9442a44558fd48950208cbef40673cc7a0b2feb41f380964fc74a0e25bf18fae`.
The packet3 Bot
`sha256:a7f8feae7870b722e3542fb7cb054ff7c092e62f4c5a6b6a3b63e52f8cd1fe47`
and Lite are validation-only and were not hosted-deployed. Agent worker,
agent API, MCP, and Terminal are also OSS-only artifacts proven by the
published-candidate validation run; they are not claimed as
hosted-production workloads.

## Release population

Milestone `#30` is open as `v0.12.18 — compound delivery pending`. Its 22
shipped-value issues (the 21-PR value map plus inherited #890) are open with
exactly `state: awaiting-evaluation`;
publication close-back is forbidden until the exact public tag, ten stable
aliases, GitHub Release, validation, and credit receipt exist. The five closed
milestone objects are the three v0.12.16 carry-ins `#798`, `#864`, and `#895`,
plus merged PR objects `#885` and `#894`; they are not evidence that the
compound delivery is complete.

The v0.12.18 value population is:

| issue | delivery PR | disposition at publication |
|---|---:|---|
| #532 | #894 | close-back with exact tag, images, coverage, and credit |
| #600 | #907 | close-back; live Teams witness |
| #718 | #912 | close-back |
| #795 | #920 | close-back |
| #803 | #903 | close-back with bounded-capture claim only |
| #809 | #906 | close-back |
| #840 | #918 | close-back; live denial witness |
| #846 | #917 + #932 | close-back; English path only, structural scan diagnostic |
| #856 | #932 | close-back; deterministic English locale |
| #862 | #919 | close-back; 420-second lobby witness |
| #865 | #885 | close-back |
| #889 | #913 | close-back and link #839; do not claim host prompt withdrawal |
| #892 | #910 | close-back |
| #893 | #911 | close-back with scheduling-boundary claim |
| #900 | #930 | close-back |
| #901 | #930 | close-back |
| #915 | #916 | close-back; machine-proven, live recurrence unobserved |
| #921 | #925 | close-back |
| #922 | #924 | close-back |
| #926 | #933 | close-back; machine-proven, live recurrence unobserved |
| #927 | #931 | close-back; reporter credit to Valerie Phoenix |

Additional custody row: `#890` is inherited implementation on the v0.12.18
source line, not a v0.12.19 delta. It is now open in milestone `#30` with the
other v0.12.18 values. Publication records the v0.12.18 delivery evidence and
closes it; it must not be claimed or closed by the v0.12.19 train.

Explicit survivors:

- `#839` — host-visible Meet lobby withdrawal;
- `#841` — hosted dashboard Delivery History consumer, currently routed to
  v0.12.20;
- `#934` — unbounded teardown after silence verdict;
- `#935` — stale replica terminal-truth corruption;
- `#937` — hosted Jitsi meeting detail rendering.
- `#942` — hosted Account/API-key compatibility incident, carried by
  `Vexa-ai/vexa-platform#127`; it is not part of the delivered/closing OSS
  population. `#941` is its closed duplicate.

Credits:

- Joseph Yaksich — PR #894;
- Felix-Ayush — PRs #885, #924, and #925;
- Dmitry Grankin — the remaining 17 value PRs and release witness;
- Valerie Phoenix — reporter of #927;
- Dmitry Grankin — validator of the four community-authored PRs.

No additional named non-author human validator is invented for the
Dmitry-authored PRs; exact-head machine evidence and the assembled live
witness are stated as such.

## Customer-facing readback oracle

Accepted production readback:

1. production revision 103 holds all 14 Deployments Ready;
2. admin API, runtime, meeting API, and gateway run the candidate-map
   descriptors;
3. production meetings 24740/24741 ran the stage2 Bot digest, admitted,
   transcribed, and completed; they do not witness packet3;
4. pre-existing bot continuity remained unchanged;
5. hosted Account/API-key incident #942 remains open and routed to
   `vexa-platform#127`; OSS publication does not claim it fixed;
6. the public marketing badge/webapp leg and Runtime packet3 leg remain
   unexecuted platform work and are not OSS publication evidence.

After OSS publication:

1. annotated tag `v0.12.18` resolves to the approved source SHA;
2. all ten `:v0.12.18` tags equal `candidate-images.json`;
3. release validation is green on those stable aliases;
4. all ten `:v012` aliases resolve to the same validated descriptors;
5. GitHub Release title/body equal `RELEASE-NOTES.md`;
6. published-source attestation and release guard are green;
7. every close-back/survivor/credit row reads back;
8. canonical ownership and the operational Lease still name the same owner
   until the complete delivery receipt is committed.

Publication operations are idempotent: an already-equal tag/alias/comment may
be confirmed; an existing unequal tag/alias or conflicting close-back stops
the transaction instead of being overwritten.

## Compound receipt gate

The previously missing canonical ownership record is now integrated by
`DmitriyG228/biz@510781d2ce29c00955eb8577c371938c47707569`.
Its `ownership.yaml` is byte-identical to the original atomic handoff
`a0ef714152a4af246254cfee12f1f411fc6a8f59`: stage and production both name
owner `df04a7fd-b154-4c88-889c-7fd31bd06bc8` and release `0.12.18`, matching
the operational production Lease.

The founder value sign is committed in `witness.json` and binds same-byte OSS
publication to this frozen candidate tuple. The lifecycle delivery receipt is
created only after this packet's public source SHA, digests, notes, population,
and current dual-custody readback are frozen. It begins in
`prod_live_oss_pending`; only the final all-readback commit may flip it to
`prod_delivery_complete`. Post-deploy credential remediation #125 and hosted
incident #942 remain open survivors and do not change that OSS receipt.
