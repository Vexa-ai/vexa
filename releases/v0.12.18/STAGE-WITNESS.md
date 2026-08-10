# v0.12.18 — post-storm stage witness

Date: 2026-07-23 · stage Helm revision `65` · human lens: Dmitriy Grankin
· system lens: dashboard, bot, meeting-api, Redis, Postgres, and Kubernetes

Target artifacts:

- core release images: `v0.12.18-260723.stage2`
- bot: `vexaai/vexa-bot:v0.12.18-260723.stage2`
- Terminal: `vexaai/v012-terminal:v0.12.18-260723.stage2`
- dashboard: `vexaai/dashboard:0.10.6.3.15-260723-platformonly`

The autonomous storm had already held twice with zero v0.12.18 blockers. This
leg tests the remaining human claim on the final staged artifacts: spoken words
must paint in the hosted dashboard while the meeting is active, without a
reload. This is the acceptance surface named by #895 and served in hosted prod.

## Expected

1. Send the staged bot to a Google Meet through the staged dashboard.
2. Admit the bot and speak two sentences.
3. Observe the same transcript updates in the unreloaded dashboard and the
   backend-owned Redis stream while the meeting is active.
4. Stop through the dashboard and observe typed terminal truth, final recording
   flush, durable transcript rows, and workload exit.

## Actual

Meeting `google_meet/eph-zmwc-avh`, database id `13626`, workload
`vexa-mtg-13626-d506f7f7`.

| act | human lens | system lens | verdict |
|---|---|---|---|
| release identity | dashboard header and sidebar showed only `v0.12.18`, linked to the v0.12.18 release | `/api/config` supplied `platformVersion=0.12.18` from the rev-65 dashboard image | green |
| spawn | dashboard accepted the meeting and opened the waiting view | workload ran the exact staged bot image | green |
| lobby | dashboard rendered Requested → Joining → Waiting and asked the host to admit the bot | bot emitted the matching callbacks and was not prematurely reaped during the five-minute human delay | green |
| admit | host admitted the guest | admitted at `21:04:34.640Z`; capture and recording started; speaker resolved to `Dmitriy Grankin` | green |
| live paint | while status was `Active`, the unreloaded dashboard painted confirmed text and the in-progress draft in italics | `tc:meeting:13626` held 9 sampled updates; the first completed segment carried `absolute_start_time=2026-07-23T21:05:03.146Z` and the same visible text | **green** |
| stop | dashboard Stop + confirmation returned the page to post-meeting state | bot emitted `completed(stopped)` from `active`; final recording chunk uploaded; DB became `completed`; pod reached `Succeeded` | green |
| durable truth | the completed dashboard retained the transcript | Postgres held 2 final segments with the same speaker/text, including `And it's doing its job fine and I have no issues with it, looks good to me.` | green |

## Routed findings

### Staged Terminal is half-enabled

An exploratory run on meeting `13624` found the staged OSS Terminal stuck on
`Reconnecting to live stream…` even though its transcript persisted. The
deployment has:

```text
AGENT_API_URL=http://vexa-platform-vexa-agent-api:8100
```

The same live Helm values have `vexa.agentApi.enabled=false`, and
`vexa-staging` has no agent-api Deployment or Service. The Terminal therefore
cannot establish its authorized live-stream path. The finding is recorded on
[vexa-platform#113](https://github.com/Vexa-ai/vexa-platform/issues/113#issuecomment-5063303746),
with the release-blocker scope corrected in the
[follow-up](https://github.com/Vexa-ai/vexa-platform/issues/113#issuecomment-5063360898):
hosted prod deliberately disables Terminal + agent-api, and #895 claims the
hosted dashboard, which passed above.

### Jitsi meeting detail crashes

The user also ran Jitsi meeting `13627`. Its transcript API returned 42 valid
segments with named speakers, but the staged dashboard detail collapsed to
`This page couldn't load` on two attempts. The Google Meet page above is the
same-session negative control. Filed as
[Vexa-ai/vexa#937](https://github.com/Vexa-ai/vexa/issues/937) for the next
train. Jitsi remains community-supported and hosted was explicitly outside its
delivery floor, so this finding is not promoted into the v0.12.18 gate.

## Cleanup / final state

- lifecycle truth for the claimed leg: `active -> completed(stopped)`
- recording: completed, final chunk accepted
- durable transcript segments: 2
- live Redis updates sampled: 9
- claimed-leg workload: `Succeeded`
- stage deployments: 10/10 Ready
- production mutations: none

## Verdict

**GREEN — the v0.12.18 post-storm product witness passes on the hosted prod
surface at Helm revision 65.**

The human and system lenses agree on lobby state, admission, live no-reload
transcript paint, durable transcript truth, recording, typed completion, and
workload exit. The release behavior is eligible for the next hop.

This verdict does not waive the separate platform promotion gate:
vexa-platform#113 must still deliver the reconciled prod chart (including
#116) and the exposed stage credentials must be rotated before any prod
mutation.
