# Compose Live Meeting Deployment Test

Status: pass (machine), pending human eyeball

Run `vexa-meeting-deployment-test` against the pack-specific Compose lane using the user-approved Google Meet URL and `https://httpbin.org/post` webhook target.

Pass after harness stagger fix landed in the shared skill (`d510dbf`).

- run id: `meeting-test-20260524-100350`
- case id: `compose-ios-pack1-r1`
- meeting URL: `https://meet.google.com/ios-njgt-nnh?authuser=0`
- listener bot: `15` (`listener-test-compose-ios-pack1-r1`)
- speaker bots: `16` (`Maya Chen-compose-ios-pack1-r1`), `17` (`Leo Santos-compose-ios-pack1-r1`)
- gateway: `http://localhost:42261`
- dashboard: `http://localhost:42260/meetings/15`
- meeting status: `completed`
- bot status at speak start: `3/3 active`
- machine transcript / WebSocket verdict: pass; WS probe observed transcript events with text.
- transcript content score: `23 segments` written; `14/19` key anchors matched.
- speaker identification: per-bot labels visible in transcript (`Maya Chen-compose-ios-pack1-r1`, `Leo Santos-compose-ios-pack1-r1`, `Dmitriy Grankin`).
- webhook delivery verdict: pass; meeting-api delivered 3 status events.
- **recording machine verdict: pass with master playback URL populated** — this is pack 1's core contract. `recording_status=completed`, `recording_id=659967604205`, master.webm 4,809,060 bytes at `/recordings/2/659967604205/e4fd17d4-0659-4ff5-b945-5f6eb802540d/audio/master.webm`. Presigned GET at `http://localhost:42261/recordings/659967604205/master?type=audio` returns 200 with content_type `audio/webm`.
- cleanup verdict: pass; all 3 bots stopped post-test.
- evidence directory: `.agents/packs/0.10.6x-pack-1-recording-playback-trust/compose/meeting-test-runs/meeting-test-20260524-100350`

UX polish surfaced during validation:

- `898b261` (pack 1): neutralize red "Recording was enabled, but no finalized recording artifact is available yet" banner. Replaced with the same neutral muted spinner used for the standard "Recording is processing..." state, text "Recording is finalizing...". A red destructive banner during normal post-meeting finalizer delay erodes the trust this pack is meant to deliver.

Pack 1 blast-radius observation:

- Master audio assembled by `recording_finalizer.master`, presigned URL returned by `/recordings/<media_file_id>/master`, content type `audio/webm`, file size matches finalizer log (`size_streamed`).
- Dashboard meeting page renders audio player bar at top of transcript pane (verified via authenticated browser session as `test@vexa.ai`).
- Per-segment Play buttons present on each transcript turn.

Follow-up required before PR-ready status:

- Human eyeball verdict (basic functionality + pack blast-radius) per the updated develop skill.
- Lite lane meeting test still pending; bots deployed twice on `meet.google.com/zzz-tvhv-qfz` but timed out waiting for human meet admission.
