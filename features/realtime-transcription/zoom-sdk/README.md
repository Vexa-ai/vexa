---
services:
- meeting-api
- vexa-bot
---

**DoDs:** see [`./dods.yaml`](./dods.yaml) · Gate: **confidence ≥ 75**

# Zoom SDK (Native) Transcription

## Why

Zoom's native Meeting SDK exposes per-speaker raw audio directly from the C++ interface — no DOM, no ScriptProcessor, no PulseAudio routing. Speaker attribution comes from the SDK itself (`onActiveSpeakerChange` + `getUserInfo`), not inference.

This is the peer platform of [`zoom-web`](../zoom-web/) (Playwright on zoom.us, for setups without a Marketplace SDK app). Release 260422-zoom-sdk split what used to be a single `zoom` feature into two. Callers pick via the API: `platform=zoom_sdk` vs `platform=zoom_web`.

## What

Native SDK wrapper + Node.js bindings via node-addon-api.

```
C++: AudioDelegate -> onMixedAudioRawDataReceived (mixed PCM, 16kHz)
                    onOneWayAudioRawDataReceived (per-user PCM + user_id)
   |
   v  ThreadSafeFunction
Node: sdk-manager::startRecording handlers
   -> mixed goes to RecordingService.appendChunk
   -> per-user goes to handlePerUserAudio -> speaker-attribution pipeline
```

### Key files

| File | Role |
|------|------|
| `native/src/zoom_wrapper.cpp` | C++ addon: SDK init/auth/join + audio delegate |
| `sdk-manager.ts` | Node-side wrapper: retry loop, error diagnosis, lifecycle |
| `strategies/join.ts` | Initialize + authenticate + join meeting + join audio |
| `strategies/recording.ts` | Start/stop recording + per-user audio handling |
| `strategies/admission.ts` | Poll for active state (same-account admission is auto) |
| `strategies/leave.ts` | Graceful leave + SDK cleanup |

### Recording flow (post-Pack A)

1. `GetMeetingRecordingController()` — returns the controller the SDK exposes for raw-data mode.
2. `CanStartRawRecording()` — `SDKERR_NO_PERMISSION` = host hasn't granted local recording. Fall through to retry.
3. `RequestLocalRecordingPrivilege()` — sends request to host. Auto-approves if the Zoom account setting is correct (see §5 of [setup guide](../../../services/vexa-bot/docs/zoom-sdk-setup.md)).
4. `StartRawRecording()` — enables raw-data mode on the controller.
5. `audioHelper_->subscribe(&audioDelegate_)` — SDK now delivers mixed + per-user buffers.

`sdk-manager.ts::startRecording` polls steps 2-5 every 2s, up to 10s, so host auto-approval latency doesn't stall the bot.

## How

```bash
# 1. Set credentials in .env:
ZOOM_CLIENT_ID=<sdk-key>
ZOOM_CLIENT_SECRET=<sdk-secret>

# 2. POST /bots with explicit zoom_sdk platform:
curl -X POST "$GATEWAY_URL/bots" \
  -H "X-API-Key: $API_TOKEN" -H "Content-Type: application/json" \
  -d '{"platform":"zoom_sdk","native_meeting_id":"12345678901","passcode":"..."}'

# Or legacy alias (one cycle only, deprecated):
curl -X POST "$GATEWAY_URL/bots" \
  -d '{"platform":"zoom",...}'   # -> rewritten to zoom_sdk, X-Vexa-Deprecated-Platform header set
```

Setup guide: [`services/vexa-bot/docs/zoom-sdk-setup.md`](../../../services/vexa-bot/docs/zoom-sdk-setup.md) — Marketplace app creation, SDK binary layout, build via `scripts/build-zoom-sdk.sh`, Zoom account settings.

## DoD

Machine-readable definitions live in [`./dods.yaml`](./dods.yaml). Human-readable summary:

| # | Check | Weight | Baseline | Evidence |
|---|-------|--------|----------|----------|
| 1 | Bot joins and captures raw audio | 20 | TODO | `meeting-tts-zoom-sdk.sh` first clean run |
| 2 | Each GT line: correct speaker attributed | 20 | TODO | `score.json:speaker_accuracy >= 0.90` |
| 3 | Each GT line: content matches (≥70% similarity) | 20 | TODO | `score.json:avg_similarity >= 0.70` |
| 4 | No hallucinated segments (rate ≤ 0.45) | 8 | TODO | `score.json:hallucinations / gt_count` |
| 5 | No missed GT lines (completeness 100%) | 10 | TODO | `score.json:completeness == 1.0` |
| 6 | SDK API current (addon loads + auth valid) | 7 | PASS | Pack A static checks |
| 7 | Per-speaker raw audio forwarded (SDK-only) | 8 | PASS | `ZOOM_SDK_ON_ONE_WAY_AUDIO_IMPLEMENTED` static check |
| 8 | Recording privilege granted within 10s (SDK-only) | 7 | PASS | `ZOOM_SDK_PRIVILEGE_RETRY_LOOP` static check |

Gate: **confidence_min = 75** (verbatim from gmeet pre-refactor; parity target).

Items 1-5 populate from `tests3/testdata/zoom-sdk-compose-260422/pipeline/score.json` once Pack G's `meeting-tts-zoom-sdk.sh` has run against a configured same-account Zoom meeting. Items 6-8 are static-tier and already green against current code.

## Limitations

- **Unpublished apps = same-account only.** SDK code 63 on external meetings. Publish on Zoom Marketplace to lift (review process applies).
- **Linux x86_64 only.** SDK variants for other platforms are not wired into `scripts/build-zoom-sdk.sh`.
- **Raw-data license.** Some Zoom accounts require an explicit raw-data license from Zoom. Without it, `StartRawRecording` returns `SDKERR_NO_PERMISSION` even after host approval. `sdk-manager.ts` falls back to PulseAudio capture in that case (mixed audio only, no per-user attribution; DoDs 7/8 drop to SKIP).

## Release history

- **260422-zoom-sdk Packs A + E** (2026-04-22) — native recording flow
  landed (reporter-validated #150 patches) alongside this DoD sidecar
  authored from gmeet pre-refactor parity bar. First cycle where
  zoom-sdk is a gated feature; first cycle where `platform=zoom_sdk`
  is a first-class enum value.
