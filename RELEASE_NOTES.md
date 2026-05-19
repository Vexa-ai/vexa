# Vexa v0.10.6.1 - recording trust, meeting actions, lifecycle hotfix

Release date: 2026-05-19

This is a focused hotfix release. It restores trust in completed-meeting
playback, live meeting actions, bot lifecycle convergence, and
billing-sensitive post-meeting completion hooks without changing public webhook
contracts.

## Highlights

### Recording playback is canonical again

- Multi-chunk recordings now finalize into one canonical master recording
  instead of letting dashboard playback stop at the first chunk.
- `playback_url` in `meeting.data` is the source of truth for dashboard audio
  and video playback.
- Bot-exit finalization runs before terminal status flips, closing the race
  where a completed meeting could point at stale or partial recording data.
- The dead relational `recordings` / `media_files` read path was removed after
  archive/restore tooling was added for rollback discipline.

### Live meeting actions are back on the production-shaped path

- `/speak` and TTS playback were revalidated through the same bot command path
  used by live meetings.
- TTS language auto-detection was verified for English, Spanish, Russian, and
  Japanese, including warm-cache behavior.
- Teams can continue through the "without AV" join path.
- Voice-agent mode no longer implicitly turns on the bot camera/avatar.
  `camera_enabled` is now an explicit opt-in.
- Bot acceptance signals are persisted into `meeting.data` so operator review
  can distinguish admission, roster visibility, audio route, speaker signal,
  captions, and camera capability.

### Lifecycle and billing hooks are idempotent

- Browser-session DELETE now converges to terminal meeting state instead of
  leaving sessions stuck in `stopping`.
- Internal post-meeting completion hooks claim an outbound-event ledger entry
  before sending, with stable event IDs and retry metadata.
- Public webhook payloads, event names, and HMAC behavior stay compatible.
- No new database column was introduced for the post-meeting hook latch; the
  claim state lives in existing `meeting.data`.

### Public artifact shape

- Public image tag: `0.10.6.1-260519-2028`.
- GitHub PR: [#331](https://github.com/Vexa-ai/vexa/pull/331).
- Included images:
  `api-gateway`, `admin-api`, `runtime-api`, `meeting-api`, `mcp`,
  `dashboard`, `tts-service`, `vexa-lite`, and `vexa-bot`.
- `agent-api` remains no-ship for v0.10.x and is intentionally excluded from
  public promotion loops.

## Validation

- Local meeting-api tests: `268 passed, 10 skipped`.
- Local API-gateway tests: `90 passed, 4 skipped`.
- Final LKE smoke on `0.10.6.1-260519-2028`: `46/46` report JSON files pass.
- PR checks are green: unit/integration jobs, transcript rendering, CodeQL, and
  GitGuardian.
- Human validation:
  - `/speak` was heard clearly in a live Teams meeting.
  - transcript quality replacement samples passed.
  - dashboard playback and transcript sync passed with caveats.
  - bot join/acceptance and dashboard stop behavior passed.
  - manual latency was accepted after input-quality caveat review.

## Known caveats carried forward

- [#318](https://github.com/Vexa-ai/vexa/issues/318) - Zoom Web can still show
  zero-VAD/no-transcript behavior when audio is joined but Zoom no longer
  exposes the expected DOM audio stream surface.
- [#339](https://github.com/Vexa-ai/vexa/issues/339) - Zoom Web audio-join
  confirmation can fail after the toolbar appears joined, causing
  `needs_human_help` and zero transcripts.

Both caveats were observed as pre-existing Zoom Web reliability surfaces rather
than v0.10.6.1 regressions. Teams and Google Meet validation passed for the
hotfix behaviors in this release.

## Upgrade notes

- For Kubernetes/Helm deployments, pin service images with
  `global.imageTag=0.10.6.1-260519-2028` and set the bot image to
  `vexaai/vexa-bot:0.10.6.1-260519-2028`.
- For compose/lite users, use images from the same immutable tag or the
  promoted release tag once the release promotion step completes.
- Keep existing webhook consumers unchanged; public webhook contract
  compatibility is part of the release guarantee.
