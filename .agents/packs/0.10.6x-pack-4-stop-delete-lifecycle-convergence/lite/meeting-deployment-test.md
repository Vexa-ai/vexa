# Lite Live Meeting Deployment Test

Status: pass (machine), pending human eyeball

Run `vexa-meeting-deployment-test` against the pack-specific Lite lane using the user-approved Google Meet URL and `https://httpbin.org/post` webhook target.

Latest pass after the pack 4 Lite supervisord `INTERNAL_API_SECRET` fix landed (commit `3838b02`) and after harness stagger + transcription URL fixes:

- run id: `meeting-test-20260524-100200`
- case id: `lite-bvf-r6`
- meeting URL: `https://meet.google.com/bvf-rzuj-kwj?authuser=0&hs=49`
- Lite image: `vexaai/vexa-lite:0.10.6.2.1-pack4-260524-0858`
- Lite gateway: `http://localhost:46471`
- Lite dashboard: `http://localhost:46470/meetings/13`
- Lite container: `vexa-0-10-6x-pack-4-stop-delete-lifecycle-convergence-lite`
- listener bot: `13` (`listener-test-lite-bvf-r6`)
- speaker bots: `14` (`Maya Chen-lite-bvf-r6`), `15` (`Leo Santos-lite-bvf-r6`)
- meeting status: `completed`
- machine transcript / WebSocket verdict: pass; WS probe subscribed to listener meeting id `13` and observed transcript events with text.
- transcript content score: `34 segments` written; `14/19` key anchors matched (highest score of any lane this session).
- speaker identification: per-bot labels visible in transcript.
- webhook delivery verdict: pass; meeting-api delivered 4 status events.
- recording machine verdict: `recording_status=completed`, `recording_id_present=true`. Master playback URL not populated — pack 4 does not carry pack 1's recording finalizer fixes; expected and out of pack 4 scope.
- cleanup verdict: pass; bots stopped, no residual `meeting-*` containers.
- evidence directory: `.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/lite/meeting-test-runs/meeting-test-20260524-100200`

Supporting fixes surfaced and landed during this validation:

- `3838b02` (pack 4): add `INTERNAL_API_SECRET="lite-internal-secret"` to `meeting-api` program env in `deploy/lite/supervisord.conf`. Without this, every pack-4-introduced internal callback endpoint returned `503 INTERNAL_API_SECRET not configured` and bot lifecycle callbacks could not be delivered in Lite.
- `d510dbf` (authority skill): stagger speaker bot deploys 5s apart in `meeting-tts.sh` to avoid the Chromium browserContext race that killed one speaker per run.
- `263e623` (authority skill): poll `playback_url` with retries in `webhook-summary.json` writer so evidence reflects post-finalizer state.

Follow-up required before PR-ready status:

- Human eyeball verdict (basic functionality + pack blast-radius) per the updated develop skill.
- Live audio and recording surface confirmation are user-only checks.
