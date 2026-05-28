# Compose Human Eyeball Gate

Status: failed; machine deployment proof now passes; human rerun still required by pack policy

PACK 1 requires hot human-in-the-loop validation against the Compose target before the PR can leave draft. Prior machine checks remain useful supporting evidence, but they do not satisfy the human gate by themselves.

Live run attempted:

- Meeting: `https://meet.google.com/bvf-rzuj-kwj?authuser=0`
- Compose dashboard: `http://localhost:42260/meetings/5`
- Evidence: `human/compose-hot/compose-bvf-rzuj-kwj-20260523T155551Z/`
- Machine transcript/webhook/recording evidence: transcript delivery validated; 15/19 key anchors matched; webhook delivery succeeded; backend recording status became `completed` with canonical master media.
- Human observation: listener and speaker bots appeared; Maya/Leo names, audio, distinct voices, and multilingual checkpoints were observed; playback/finalization was marked failed because the dashboard remained in a processing/no-playback state in the expected window.

Fix applied after failed human observation:

- Product commit: `6aae0fa fix(dashboard): refresh finalized recording readiness`
- Cause addressed: dashboard post-meeting refresh compared recording count only. An existing recording can become playback-ready by gaining `playback_url`, `completed_at`, or finalized media metadata without changing count.
- Verification after fix: focused Vitest `4 passed`; focused ESLint passed; dashboard production build passed; patched Compose dashboard container rebuilt and healthy.

Rerun attempted:

- Evidence: `human/compose-hot/compose-bvf-rzuj-kwj-rerun-20260523T163114Z/`
- Result: blocked before speech because all three bots remained `awaiting_admission` until timeout. Runner cleanup stopped the bots. This is not a validation pass.

Post-fix rerun attempted:

- Evidence: `human/compose-hot/compose-bvf-rzuj-kwj-postfix-20260523T165318Z/`
- Dashboard: `http://localhost:42260/meetings/11`
- Result: invalid/fail. Bots were admitted and the first eight TTS turns were accepted, but the listener/speaker bots self-completed mid-script and later `/speak` calls returned `404`. Human PACK 1 playback observation failed: screenshot showed the audio controls stuck at `Preparing audio...` with `0:00 / 0:00`; playback did not start.
- Backend state for the same meeting later showed `recording_status=completed`, canonical master present, and `/recordings/756346522746/master?type=audio` returning `200`. A fresh page load of `/meetings/11` showed a ready `2:00` audio player and MinIO range requests `206`.
- Interpretation: the product still does not have a passing hot gate. The failed live page was likely using a stale pre-fix client session from before the dashboard container rebuild, so the next rerun must start from a hard-refreshed dashboard/new browser session after the patched dashboard is running.

Additional research/fix after the user reported hard refresh did not help:

- Product commit: `c56b7d5 fix(dashboard): reconcile ready recording audio`
- External browser-media docs confirm `duration` is unknown until metadata exists and `readyState >= HAVE_METADATA` is the browser signal that metadata-backed fields are initialized. The dashboard player previously relied on media events to clear loading. If the browser had already loaded cached/presigned media before React effects attached listeners, React state could remain stuck at `Preparing audio...` with `0:00 / 0:00`.
- Fix applied: the audio player now listens for `durationchange` and reconciles `duration`/loading state from the current `HTMLMediaElement.readyState` after listener attachment.
- Verification after fix: focused `audio-player.tsx` ESLint passed; focused dashboard recording tests `4 passed`; dashboard production build passed; Compose dashboard image was rebuilt and restarted. A fresh load of `http://localhost:42260/meetings/11` showed `readyState=4`, `duration=120.116`, no media error, no disabled Play button, and Play advanced `currentTime`.
- This is still not a passing Compose hot gate. A new live admitted run is required to prove the finalization-to-playable transition under the patched client.

Machine-only deployment validation after meeting 14 failed:

- Product commit: `5cef87b fix(dashboard): proxy recording playback through dashboard`
- Cause addressed: the dashboard was handing the browser a presigned MinIO URL on `localhost:42268`. That can be valid from backend/local automation but fail from a user browser or proxied dashboard deployment. The player now uses the dashboard same-origin raw route returned by the canonical master resolver.
- Streaming preserved: the dashboard proxy forwards media as a stream and preserves range headers instead of buffering the whole response.
- Evidence: `compose/automated-playback-validation.md`
- Result: pass for the reported meeting 14 playback failure. The same-origin raw route returned `206 Partial Content`, `ffprobe` reported `duration=39.356`, and headless Chromium loaded `/meetings/14` with `src=http://localhost:42260/api/vexa/recordings/605230459015/media/408435409101/raw`, `readyState=4`, `duration=39.356`, no media error, visible `0:39`, and playback advanced.
- This is a working-deployment machine proof, not a human gate pass.

Browser hang follow-up after the user reported `/meetings/14` did not respond:

- Product commit: `a7ba8f1 fix(recordings): stabilize audio playback delivery`
- Cause addressed: the server was healthy and the media object was valid, but playback could still wedge the browser. Audio-only WebM was served as `video/webm`, and the dashboard audio player could repeatedly replace identical fragment duration state after metadata/playback, churning the media listener effect.
- Verification after fix: meeting-api focused tests `10 passed`; dashboard recording Vitest `5 passed`; focused ESLint passed with only existing meeting-page warnings; dashboard production build passed; patched Compose meeting-api/dashboard rebuilt and restarted. Browser verification on `http://localhost:42260/meetings/14` returned immediately with same-origin raw `src`, `readyState=4`, `duration=39.356`, `networkState=1`, visible `0:00 / 0:39`, `error=null`, and muted playback advancing to `currentTime=0.587054`.
- This recovers the stuck browser page for the deployment proof. It still does not replace the mandatory human Compose/Lite hot gates.

Prior supporting machine checks:

- `compose/all-build-rerun-readable-files/`: full isolated Compose build and upstream smoke passed.
- `compose/recording-master-playback-smoke-rerun/`: canonical master route returned `200`; raw range playback returned `206`; presigned MinIO URL used the isolated public endpoint.
- `compose/ps-after-playback-smoke/`: services remained up and healthy after the playback smoke.

Required next evidence:

- Rerun Compose hot validation with admitted bots after commit `a7ba8f1`.
- Human confirmation that the same live page, loaded after the patch, transitions from processing to playable recording without a manual reload and that Play starts the canonical recording.
- Machine transcript/webhook/playback telemetry correlated to the same run.
