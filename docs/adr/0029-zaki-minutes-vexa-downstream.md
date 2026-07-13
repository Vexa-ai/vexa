# ADR-0029 — Base ZAKI Minutes on a pinned Vexa downstream

**Status:** accepted for development; production activation not accepted  
**Date:** 2026-07-13

## Context

ZAKI needs a self-hosted meeting-capture spoke for Google Meet, Microsoft Teams, and Zoom. The engine
must be forkable, commercially usable as part of ZAKI, Kubernetes-compatible, tenant-isolated, and
capable of keeping raw transcripts inside a ZAKI-controlled data plane.

The earlier design selected Vexa's `0.10.6.x` line because upstream `main` was mid-rework. Upstream has
since released `v0.12.1` with documented Lite, Compose, bot-spawn, Helm, and `0.10` compatibility
validation, then tagged `v0.12.2` with security, installation, Jitsi, and release-pipeline changes.

Attendee is technically credible but its Elastic License 2.0 prohibits offering a substantial set of
the software's functionality as a hosted or managed service. MeetingBot is LGPL and AWS-centric.
Hosted APIs are not forkable and move the raw data plane outside ZAKI. Building a multi-platform bot
fleet from scratch would duplicate Vexa's highest-churn work.

Primary sources:

- <https://github.com/Vexa-ai/vexa>
- <https://docs.vexa.ai/deployment>
- <https://github.com/Vexa-ai/vexa/releases/tag/v0.12.1>
- <https://github.com/Vexa-ai/vexa/releases/tag/v0.12.2>
- <https://github.com/attendee-labs/attendee/blob/main/LICENSE>
- <https://github.com/meetingbot/meetingbot>

## Decision

Create `ProjectNuggets/zaki-minutes-engine` as a GitHub fork of Vexa and use upstream tag `v0.12.2`
at commit `034ad78b718b9338182fd80288547419b44337b3` as the development baseline.

Preserve upstream history, Apache-2.0 notices, modular boundaries, sealed contracts, and gate suite.
Keep the ZAKI delta narrow: tenant mapping, the accepted read-only Minutes profile, capture evidence,
retention/erasure, hub events/metering, and deployment adaptation. Vexa's own agent/workspace lane is
not the ZAKI brain and will not receive ZAKI brain credentials.

The fork remains default-off. A tag or passing unit suite is not production evidence; activation
requires tenant-isolation, retention, erasure, and consented live-meeting proof at the product boundary.

## Consequences

- ZAKI inherits a functioning multi-platform capture substrate and its operational complexity.
- Vexa's Apache-2.0 license permits the hosted downstream model; notices remain required.
- Upstream churn is controlled through explicit tag/commit pins and reviewed update batches.
- `v0.12.2` has less published release evidence than `v0.12.1`; ZAKI must run the full gate suite and
  cannot treat the upstream tag alone as a production-readiness claim.
- Contract changes are human/steward-gated. The WP-15 read profile is not sealed until its four open
  decisions are resolved.
- The separate hub and GitOps repositories remain the authorities for user policy and deployment.

