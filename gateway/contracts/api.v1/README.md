# api.v1 — the public surface, frozen to vexa **main**

The REST + WS + MCP API the world builds against (eval harness, dashboard, SDKs, any
client). **`api.schema.json` is the OpenAPI 3.1 document emitted by vexa `main`'s
`services/api-gateway` — captured verbatim** (`info.title = "Vexa API Gateway"`,
`version = 1.5.0`) and **sealed** in `contracts.seal.json`, so every v0.12 service
(meeting-api, dashboard, bot) builds against the **real production surface**, never an
invented shape. This is the contract-first anchor: pin the API to main *before* the
services, or they drift (the rough meeting-api's hand-rolled 4-op shape is the cautionary
tale — it must be reconciled to the shapes below).

## What's pinned
- **Identity:** OpenAPI `3.1.0` · title `Vexa API Gateway` · version `1.5.0`.
- **Core paths** (asserted by `validate.mjs`): `GET/POST /bots`, `GET /bots/status`,
  `DELETE /bots/{platform}/{native_meeting_id}`, `PUT .../config`, `POST .../speak`,
  `GET /transcripts/{platform}/{native_meeting_id}`, `GET /recordings`,
  `GET /recordings/{recording_id}`, `GET /meetings`.
- **Shapes** (goldens conform to the frozen `#/components/schemas/*`): `MeetingResponse`,
  `MeetingListResponse`, `TranscriptionResponse`, `TranscriptionSegment`,
  `BotStatusResponse`. Canonical `MeetingStatus` enum =
  `[requested, joining, awaiting_admission, active, needs_human_help, stopping, completed, failed]`.

## The seam (what conforms to this)
| Consumer | How |
|---|---|
| `meetings/services/meeting-api` | its routes + response models MUST match these paths/shapes (rough cut owes reconciliation) |
| `clients/dashboard` | proxies `/bots`,`/meetings`,`/transcripts`,`/recordings` — the shapes here |
| `meetings/eval` | polls `GET /bots` (`meetings[].status`), reads `GET /transcripts/{p}/{n}` |

## Re-verify / re-capture
The frozen doc was captured from the deployed main (`api.cloud.vexa.ai/openapi.json`,
version-matched to `git show main:services/api-gateway/main.py` = 1.5.0). To re-verify drift:

```bash
curl -s https://api.cloud.vexa.ai/openapi.json | diff - gateway/contracts/api.v1/api.schema.json
node gateway/contracts/api.v1/validate.mjs
```

A **deliberate** main API bump → re-capture `api.schema.json` + `pnpm seal:contracts` on a
`lane:contract` human-reviewed PR (a breaking change opens `api.v2`, leaving v1 until no
consumer pins it). The frozen bytes are the spec; never edit them to match an implementation.
