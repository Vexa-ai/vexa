# Compose Automated Playback Validation

Status: pass for meeting 14 playback failure case

Operator request: after the meeting 14 human-visible failure, switch to a machine validation path that proves the deployment is working without another human-in-the-loop checkpoint.

Target:

- Dashboard: `http://localhost:42260`
- Meeting: `http://localhost:42260/meetings/14`
- Native meeting id: `ios-njgt-nnh`
- Recording id: `605230459015`
- Media file id: `408435409101`

Failure reproduced from screenshot:

- UI displayed `Completed` but audio remained at `Preparing audio...` with `0:00 / 0:00`.
- Backend master route returned `200`, and the master object itself was valid WebM. This isolated the failure to browser delivery/state, not server finalization.

Fix chain:

- Product commit: `5cef87b fix(dashboard): proxy recording playback through dashboard`
- Dashboard still resolves the canonical `/recordings/{id}/master?type=audio` route.
- When the resolver returns `raw_url`, the dashboard now uses same-origin `/api/vexa/recordings/{id}/media/{media_id}/raw` for the audio `src`.
- The dashboard API media proxy streams the upstream response body and preserves `Accept-Ranges`, `Content-Range`, `Content-Length`, `Content-Type`, and `Content-Disposition`.
- Product commit: `a7ba8f1 fix(recordings): stabilize audio playback delivery`
- Meeting API and finalizer now serve audio WebM as `audio/webm` while keeping video WebM as `video/webm`.
- Dashboard audio duration reconciliation now no-ops identical duration values so loaded metadata cannot trigger a React listener/state churn after playback starts.

Machine validation results:

- Dashboard health: `ok`.
- Canonical master resolver: `200`, `media_file_id=408435409101`, `raw_url=/recordings/605230459015/media/408435409101/raw`.
- Same-origin dashboard raw route with `Range: bytes=0-1023`: `206 Partial Content`.
- Response headers initially included `Content-Range: bytes 0-1023/297059`, `Accept-Ranges: bytes`, `Content-Type: video/webm`; after `a7ba8f1`, the same route returns `Content-Type: audio/webm`.
- First 1024 bytes identified as WebM.
- Full same-origin raw download probed with `ffprobe`: `format_name=matroska,webm`, Opus audio, `duration=39.356000`, `size=297059`.
- Headless Chromium loaded `http://localhost:42260/meetings/14` with an authenticated dashboard cookie.
- Rendered audio source: `http://localhost:42260/api/vexa/recordings/605230459015/media/408435409101/raw`.
- Browser media state before playback: `readyState=4`, `networkState=1`, `duration=39.356`, `error=null`.
- Visible page text included the fixed audio duration `0:39`.
- Muted browser playback advanced to `currentTime=0.258382` with `error=null`.
- Captured media responses included raw route `206` responses with `Content-Range: bytes 0-297058/297059` and `bytes 294912-297058/297059`.
- App-controlled browser regression after `a7ba8f1`: `/meetings/14` remained responsive after playback, rendered `0:00 / 0:39` on fresh load, audio `src=http://localhost:42260/api/vexa/recordings/605230459015/media/408435409101/raw`, `readyState=4`, `duration=39.356`, `networkState=1`, `error=null`, and muted playback advanced to `currentTime=0.587054`.

Verdict:

- Machine deployment proof for the reported meeting 14 playback failure passes.
- This evidence does not claim the mandatory pack human gate is complete; it records the requested no-human validation path for delivering a working Compose deployment.
