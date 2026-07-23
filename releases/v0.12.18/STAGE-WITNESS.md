# v0.12.18 — post-storm stage witness

Date: 2026-07-23 · stage Helm revision `65` · human lens: Dmitriy Grankin
· system lens: bot, meeting-api, Postgres, Kubernetes, and the staged Terminal

Target artifacts:

- core release images: `v0.12.18-260723.stage2`
- bot: `vexaai/vexa-bot:v0.12.18-260723.stage2`
- Terminal: `vexaai/v012-terminal:v0.12.18-260723.stage2`
- dashboard: `vexaai/dashboard:0.10.6.3.15-260723-platformonly`

The autonomous storm had already held twice with zero v0.12.18 blockers. This
leg tests the remaining human claim on the final staged artifacts: spoken words
must paint in the open Terminal while the meeting is active, without a reload.

## Expected

1. Send the staged bot to a Google Meet through the staged Terminal.
2. Admit the bot and speak two sentences.
3. Observe the same transcript segments in the open Terminal and in durable
   backend state while the meeting is active, without reloading.
4. Stop through the Terminal and observe typed terminal truth, final recording
   flush, and workload exit.

## Actual

Meeting `google_meet/eph-zmwc-avh`, database id `13624`, workload
`vexa-mtg-13624-830280cc`.

| act | human lens | system lens | verdict |
|---|---|---|---|
| spawn | Terminal accepted the meeting and exposed `Stop bot` | `POST /bots` returned `201`; workload ran the exact staged bot image | green |
| admit | host admitted the guest | admitted at `20:46:41.949Z`; per-speaker capture started; speaker resolved to `Dmitriy Grankin` | green |
| speak | host spoke the witness sentences | Postgres persisted 7 segments; `This transcript appeared without reloading.` was exact; recording chunks uploaded with HTTP 200 | green backend |
| live paint | open Terminal showed `Reconnecting to live stream…`, `Meeting stream disconnected; reconnecting`, `0 in the room`, and no transcript | meeting remained active and the durable segment count advanced to 7 | **red** |
| stop | `Stop bot` changed to `Send bot again` | bot emitted `completed(stopped)` from `active`; final recording chunk uploaded; DB became `completed` with reason `stopped`; pod reached `Succeeded` | green |
| recap hydration | after the stop transition, without an explicit page reload, the Terminal displayed the persisted transcript | all 7 segments remained durable | green recap, **not** live paint |

The first requested sentence was transcribed as `Weakness is life after.` rather
than `The stage witness is live after the storm.` The second requested sentence
was exact. This run therefore does not claim sentence-perfect transcription.

## Point of introduction

The staged Terminal has:

```text
AGENT_API_URL=http://vexa-platform-vexa-agent-api:8100
```

The same live Helm values have `vexa.agentApi.enabled=false`, and
`vexa-staging` has no agent-api Deployment or Service. The Terminal therefore
cannot establish its authorized live-stream path. This is distinct from #895:
the failing leg is Google Meet, capture and persistence are healthy, and the
segments hydrate after completion.

The finding and repair acceptance are recorded on
[Vexa-ai/vexa-platform#113](https://github.com/Vexa-ai/vexa-platform/issues/113#issuecomment-5063303746).
No production mutation was made.

## Cleanup / final state

- lifecycle truth: `active -> completed(stopped)`
- recording: completed, final chunk accepted
- transcript segments: 7
- workload: `Succeeded`
- stage deployments: 10/10 Ready

## Verdict

**RED — v0.12.18 is not ready for prod on Helm revision 65.**

The post-storm human witness proves the bot, Google Meet capture, STT,
persistence, recording, and graceful stop. It disproves the remaining
Terminal value claim: transcript segments do not paint live while the meeting
is active. Repair the stage live-stream wiring, then repeat this exact
no-reload act before deploying to prod.
