# ZAKI Minutes downstream

This repository is the meeting-capture engine for ZAKI. It is an Apache-2.0 downstream of
[Vexa](https://github.com/Vexa-ai/vexa), preserving upstream history and architecture while adding
the narrow ZAKI product boundary: visible meeting capture, tenant-scoped transcript storage, a
read-only agent surface, retention/erasure, and hub-controlled metering.

## Baseline and status

- GitHub fork: <https://github.com/ProjectNuggets/zaki-minutes-engine>
- Upstream remote: <https://github.com/Vexa-ai/vexa>
- Baseline tag: `v0.12.2`
- Baseline commit: `034ad78b718b9338182fd80288547419b44337b3`
- License: Apache-2.0; upstream notices and history remain intact.
- Activation: off. No ZAKI chart, secret, DNS, database, object store, or cluster resource exists.
- Current objective: establish a reproducible, gate-green fork before adding ZAKI contracts.

The `v0.12.2` tag contains the first post-`v0.12.1` delivery batch, including an edge guard, fresh
install fixes, Jitsi support, and release-pipeline work. Unlike
[the documented `v0.12.1` release](https://github.com/Vexa-ai/vexa/releases/tag/v0.12.1), the
[`v0.12.2` tag](https://github.com/Vexa-ai/vexa/releases/tag/v0.12.2) did not yet have a full GitHub
release-note/assets bundle when this fork was created on 2026-07-13. We therefore treat it as a
source baseline to verify ourselves, not as production-readiness evidence.

## Why Vexa remains the base

Primary-source review on 2026-07-13 found Vexa to be the best fit for ZAKI's constraints:

| Option | Fit | Decision |
|---|---|---|
| [Vexa](https://github.com/Vexa-ai/vexa) | Apache-2.0; self-hosted; Meet, Teams, Zoom, and now Jitsi; speaker-attributed transcription; per-meeting workers; Compose and Helm; own Postgres/Redis/object storage; API compatibility harness | Selected. It matches ZAKI's Kubernetes, data-control, and forkability requirements. |
| [Attendee](https://github.com/attendee-labs/attendee) | Mature bot API and active releases; simpler Django deployment | Rejected as a fork base because its [Elastic License 2.0](https://github.com/attendee-labs/attendee/blob/main/LICENSE) prohibits providing a substantial set of its functionality as a hosted or managed service. |
| [MeetingBot](https://github.com/meetingbot/meetingbot) | Open source and multi-platform | Rejected: AWS/Terraform-centric deployment and LGPL obligations are a weaker fit than Vexa's Apache-2.0 Kubernetes path. |
| Hosted bot APIs | Fastest operational start | Rejected as the engine foundation: they are not forkable and move the raw meeting data plane outside ZAKI's control. A hosted Vexa-compatible endpoint may still be used for a synthetic Package 1 adapter test. |
| Build the bot fleet from scratch | Maximum control | Rejected: browser admission, media capture, per-platform churn, teardown, and transcription operations are the expensive part Vexa already provides. |

Relevant upstream evidence:

- Vexa describes a self-hosted Apache-2.0 bot and transcription API for Meet, Teams, and Zoom, with
  the transcription API usable independently from its agent lane:
  <https://github.com/Vexa-ai/vexa>
- Vexa documents Lite, Compose, and production Kubernetes/Helm deployment paths:
  <https://docs.vexa.ai/deployment>
- `v0.12.1` records published-image validation for Lite, Compose, bot spawn, Kubernetes/Helm, and
  the `0.10` compatibility suite:
  <https://github.com/Vexa-ai/vexa/releases/tag/v0.12.1>
- Attendee's current license text contains the hosted-service restriction:
  <https://github.com/attendee-labs/attendee/blob/main/LICENSE>

## ZAKI product boundary

Vexa supplies meeting mechanics. ZAKI supplies product policy. The fork must preserve these
boundaries:

1. The Minutes store owns meeting metadata, audio, transcripts, and spoke summaries.
2. The ZAKI agent is the only writer of the brain. The Minutes engine never receives brain
   credentials and never writes durable brain state.
3. Agent access is read-only through the versioned ZAKI cross-spoke contract, protected by a
   dedicated Minutes read token and path/header/item tenant agreement.
4. Transcript content is untrusted data, not instructions. It is bounded, non-cacheable, and never
   written verbatim to brain memory, traces, logs, or workspace artifacts.
5. Visible bot identity, tenant capture attestation, agent-read opt-in, TTL enforcement, deletion,
   erasure receipts, and backup expiry are activation prerequisites.
6. Hub provisioning, wallet metering, webhook idempotency, and user-facing state remain hub-owned.
7. Every feature is default-off until its contract, isolation, erasure, and live-meeting gates pass.

The exact `/api/zaki/read/v1` Minutes profile is not added yet. Four WP-15 decisions—common versus
spoke-specific envelope fields, expired-item status, provenance sufficiency, and byte-cap scope—must
be adjudicated before that published contract is sealed.

## Downstream discipline

- `upstream/main` remains the source remote; `origin` is ProjectNuggets.
- ZAKI changes land as small commits on `codex/*` branches and preserve upstream module boundaries.
- Upstream updates are reviewed tag-by-tag. Never merge a moving branch directly into a deployable
  ZAKI release.
- For an update, record old/new tags and SHAs, inspect release evidence and license changes, merge or
  rebase in an isolated worktree, then run `node scripts/gates.mjs all` plus ZAKI contract tests.
- Published images will use immutable ZAKI tags and digests. Upstream `latest` is never deployable.
- Upstream security advisories and dependency/license gates are reviewed before promotion.

Architecture decision: [ADR-0029](docs/adr/0029-zaki-minutes-vexa-downstream.md).
