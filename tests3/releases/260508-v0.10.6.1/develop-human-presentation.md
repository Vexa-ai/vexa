# v0.10.6.1 Develop-Human Presentation

Purpose: make the release understandable enough to walk, challenge, and approve or bounce. This is not a signature form. The signature is only the record after the walkthrough.

## Opening

One-sentence release frame:

> v0.10.6.1 is a reliability release for the customer path: dashboard auth, real bot lifecycle, transcript delivery, recording playback, TTS audio, stuck browser sessions, GMeet rejection handling, and billing-control foundations.

Operator stance:

- Human judges the product experience.
- Machine proves the backend and log conditions.
- Anything confusing, surprising, or not customer-safe bounces to `develop-code`.

## Operator Observer Setup

Canonical harness:

- [tests3/human-validation-harness.md](/home/dima/dev/vexa-260508-v0.10.6.1/tests3/human-validation-harness.md)

Keep two observer terminals open while the human walks the product.

Terminal 1: release state and local stack:

```bash
python3 tests3/lib/stage.py current
python3 tests3/lib/stage.py next
docker ps --format '{{.Names}} {{.Status}} {{.Ports}}' | rg 'vexa|meeting|browser|tts|gateway|dashboard|transcription'
```

Terminal 2: compose customer-path logs:

```bash
docker compose -f deploy/compose/docker-compose.yml logs -f \
  api-gateway meeting-api runtime-api dashboard tts-service
```

Observer rule:

- Record only what the human sees and what logs/API state confirm.
- Do not fill `approved`, `signed_by`, `signed_at`, or sign blocks from observation alone.
- If a step is confusing, record the step and bounce to `develop-code`.
- For real-meeting transcript validation, the machine dispatches the bot and verifies the exact fresh `meeting_id`; old meeting evidence is readiness context only.

## Walkthrough Order

### 1. Two Dashboard Logins

Show:

- Lite: `http://127.0.0.1:3100/login`
- Compose: `http://127.0.0.1:3001/login`
- Login identity: `test@vexa.ai`

Proves:

- Browser auth works in both local deployments.
- Lite and compose cookies do not collide.
- Meeting list/detail pages are customer-usable.

Do not use:

- Lite meeting `171`; it is quarantined pre-fix evidence.

### 2. Compose Real Google Meet

Machine does:

- Dispatch a named bot to the Google Meet with `transcribe_enabled=true` and `recording_enabled=true`.
- Tell the human when the bot reaches `awaiting_admission`.
- Poll status, transcript endpoint, bot logs, and the registry check for the exact `meeting_id`.

Human does:

- Admit the named bot.
- Speak 30-60 seconds with recognizable phrases.
- Listen/look for obviously wrong product behavior.
- Wait for the machine verdict before signing this line.

Proves:

- Customer path reaches real meeting, not only API smoke.
- Transcript appears during or shortly after the meeting.
- Stop path leaves `active/stopping` cleanly.

Machine watches:

- Bot container lifecycle.
- Meeting status transitions.
- Transcript segment creation.
- Callback/finalizer/webhook errors.

Required machine verdict:

- `STATE=tests3/.state-compose LIVE_BOT_MEETING_ID=<fresh_id> bash tests3/tests/live-bot-transcript-pipeline.sh` passes.

### 3. Recording Playback

Show:

- The fresh stopped meeting, or completed meeting `10099` if still present.
- Finalizing state if recording is not ready.
- Playback, audio, and scrubbing once ready.

Proves:

- Dashboard uses canonical `playback_url`.
- Multi-chunk playback is not truncated to chunk zero.
- Not-ready state is clear instead of broken.

### 4. Speak / TTS

Show:

- Speak flow or `/speak` equivalent.
- Phrase: `This is the Vexa human gate audio check`.

Proves:

- TTS service returns valid audio.
- Bot actually plays audio into the meeting.
- Customer-visible success matches what is heard.

### 5. Requested Technical Walkthroughs

Cover briefly, using code/evidence only if challenged:

- Finalizer master-path race fix.
- GMeet rejection / waiting-room behavior.
- Voice-agent `cameraEnabled` band-aid and why #246 remains the real fix.
- Post-meeting webhook idempotency and billing dispatch-check foundation.
- Teams Continue-without-A/V: source path is hardened, but live Teams proof remains fixture-dependent unless a Teams URL is provided.

## Already Proven By Machine

- `SCOPE_LOCAL_PROOFS_ALL_GREEN`
- `LOCAL_HUMAN_*`
- `DASHBOARD_BROWSER_MEETINGS_AUTH_OK`
- `DASHBOARD_DETAIL_STALE_AUTH_RECOVERS`
- `DASHBOARD_AUTH_COOKIES_ISOLATED`
- `DASHBOARD_COMPLETED_RECORDING_PLAYBACK_READY`
- `LIVE_BOT_TRANSCRIPT_SEGMENTS_PRESENT`
- `SMOKE_BOT_TRANSCRIPTION_ROUNDTRIP`
- `TRANSCRIPTION_TOKEN_NO_PLACEHOLDER_FALLBACK`
- `PRE_RELEASE_SECURITY_DEPENDENCY_FLOORS`

Fresh delivery-ready checkpoint:

- `LOCAL_HUMAN_TARGET_URLS_READY`, containers, transcription-lb, recent logs, memory, env SSOT, cleanup, and dropped-table checks passed at `2026-05-14T08:05Z`.
- `DASHBOARD_COMPLETED_RECORDING_PLAYBACK_READY` and `LOCAL_HUMAN_BROWSER_HANDOFF_ENDPOINTS_SSOT` passed for `http://127.0.0.1:3001/meetings/10099`.
- `LIVE_BOT_TRANSCRIPT_SEGMENTS_PRESENT` passed for meeting `10099`: `6` recording chunk(s), `17` transcript segment(s).
- `PRE_RELEASE_SECURITY_DEPENDENCY_FLOORS` passed after GHSA-9wv6-78fw-fq5c was pulled into scope: dashboard PostCSS resolves to `8.5.10`; transcription-service no longer installs `python-multipart` and uses a bounded standard-library multipart parser.
- Full `release-validate LOCAL=1` passed after the advisory inclusion; report `tests3/reports/release-0.10.6-260514-0027.md`; `35` local scope proof cells green.

## Say / Do Not Say

Say:

- "Machine evidence says the local handoff is ready to walk."
- "The human job is to judge whether this feels like a working product path."
- "Teams no-A/V is source-hardened; live proof requires a Teams fixture URL."
- "Billing controls have foundations in this release; platform receiver hardening and cohort audit are being handled separately."

Do not say:

- "Everything is fixed."
- "Teams is fully proven live."
- "Billing reconciliation is complete."
- "Helm/prod is approved by this local walk."

## Bounce Conditions

Bounce to `develop-code` if any required item shows:

- login or dashboard navigation is confusing or broken;
- real GMeet bot cannot join/admit cleanly;
- transcript does not appear from fresh speech;
- stop leaves the meeting stuck;
- playback is silent, truncated, or misleading;
- TTS reports success but is not heard;
- logs show a customer-impacting error while the human is walking.

## Close

If the walk is good, the human writes their own rationale in `scope.md` / `local-human-checklist.yaml`. If not, record the specific failed step and bounce to `develop-code`.
