# Compose Live Meeting Deployment Test

Status: pass (machine), pending human eyeball

Run `vexa-meeting-deployment-test` against the pack-specific Compose lane using the user-approved Google Meet URL and `https://httpbin.org/post` webhook target.

Latest pass after 3 supporting fixes (harness stagger, lite supervisor INTERNAL_API_SECRET, transcription URL config):

- run id: `meeting-test-20260524-100156`
- case id: `compose-qoh-r4`
- meeting URL: `https://meet.google.com/qoh-kidc-pfx?authuser=0&hs=49`
- listener bot: `25` (`listener-test-compose-qoh-r4`)
- speaker bots: `26` (`Maya Chen-compose-qoh-r4`), `27` (`Leo Santos-compose-qoh-r4`)
- gateway: `http://localhost:46461`
- dashboard: `http://localhost:46460/meetings/25`
- admin token source: `vexa_0-10-6x-pack-4-stop-delete-lifecycle-convergence_compose-admin-api-1`
- meeting status: `completed`
- bot status at speak start: `3/3 active`
- machine transcript / WebSocket verdict: pass; WS probe subscribed to listener meeting id `25` and observed transcript event(s) with text.
- transcript content score: `20 segments` written; `13/19` key anchors matched.
- speaker identification: per-bot labels visible in transcript (`Maya Chen-compose-qoh-r4`, `Leo Santos-compose-qoh-r4`, plus `Dmitriy Grankin` when human spoke).
- webhook delivery verdict: pass; meeting-api delivered 5 status events (`meeting.started`, `meeting.status_change`).
- recording machine verdict: `recording_status=completed`, `recording_id_present=true`. Master playback URL is NOT populated on this pack because pack 4 does not carry pack 1's recording finalizer/playback fixes; that is expected and is pack 1's scope, not pack 4's.
- cleanup verdict: pass; all 3 bots stopped, no `meeting-*` containers remain post-stop.
- evidence directory: `.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/compose/meeting-test-runs/meeting-test-20260524-100156`

Prior failed attempts (preserved for ops record):

- `compose-ios-r1` at `20260524-091532`: failed; speaker bot Maya Chen container exited code 1 within 3s due to concurrent Chromium browserContext race during 3 simultaneous bot deploys. Resolved by harness commit `d510dbf` (stagger speaker deploys 5s).
- `compose-qoh-r2` and `compose-qoh-r3`: superseded by `r4` after additional supporting fixes landed.

Follow-up required before PR-ready status:

- Human eyeball verdict (basic functionality + pack blast-radius) per the updated develop skill.
- Live meeting attendance confirmation, audio playback observation, and recording artifact observation are the user-only checks.
