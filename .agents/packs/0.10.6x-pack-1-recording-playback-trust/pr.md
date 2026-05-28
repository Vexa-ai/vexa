# Pack PR: [Pack] PACK 1 - Recording Playback Trust

Pack epic: https://github.com/Vexa-ai/vexa/issues/356
Pack id: `0.10.6x-pack-1-recording-playback-trust`
Release: `0.10.6.x replay`
Base branch: `v0.10.6^{}`
Integration branch: `codex/release-0.10.6x-pack-integration`
Evidence: `.agents/packs/0.10.6x-pack-1-recording-playback-trust/`

## Outcomes

CEO: Customers can open a completed meeting and trust that playback represents the full meeting, not a misleading first chunk.

CTO: Recording finalization has one canonical master artifact and all public playback routes point to that artifact without racing post-meeting writers.

User: A completed meeting plays back through the canonical recording path end-to-end.

## Scope

- #314
- #311
- recording portions of #331/#347/#348

## Out of scope

- Current uncommitted product changes in the local worktree are excluded from this pack.
- Dead release evidence folders are excluded as source truth.
- Deprecated harness evidence is excluded; reusable checks must live in product tests or dedicated skills.
- The removed realtime transcript regression pack is not replay scope here.

## Blast radius

Meeting API recording metadata/finalizer/storage, API Gateway recording proxy, dashboard playback, completed-meeting support load.

## Validation

Synthetic: pass
- Meeting API recording/finalizer tests: `14 passed`
- API Gateway recording route tests: `11 passed`
- Dashboard canonical master API tests: `3 passed`
- Dashboard recording refresh signature tests: `1 passed`
- Dashboard touched-file lint: `0 errors, 15 warnings`
- Dashboard production build: pass

Compose: pass
- Full isolated Compose build and upstream smoke passed.
- Playback smoke: master route `200`, raw range `206`.

Lite: pass
- Isolated Lite lane served gateway/dashboard surfaces.
- Local-storage playback smoke: master route `200`, raw range `206`.

Live/human: blocked
- Required operating rule: hot human-in-the-loop validation must run against both Compose and Lite targets before this PR can leave draft.
- Compose run `compose-bvf-rzuj-kwj-20260523T155551Z` validated live transcript/webhook/recording evidence, but human playback/finalization observation failed: the dashboard remained processing/no-playback in the expected window.
- Fix commit `6aae0fa` refreshes dashboard recording readiness when an existing recording gains completed/playback metadata.
- Compose rerun `compose-bvf-rzuj-kwj-rerun-20260523T163114Z` was blocked before speech because all bots stayed `awaiting_admission` until timeout. Cleanup completed.
- Compose post-fix rerun `compose-bvf-rzuj-kwj-postfix-20260523T165318Z` was invalid/fail: bots were admitted, but self-completed after 8/16 TTS turns; human playback observation showed `Preparing audio...` with `0:00 / 0:00`. Backend/fresh-load evidence later showed the master was completed and playable, so the next validation must start from a hard-refreshed patched dashboard client.
- Follow-up fix `c56b7d5` reconciles the audio player from `HTMLMediaElement.readyState`/`duration` after listener attachment so browser-cached or already-loaded media cannot leave React state stuck at `Preparing audio...`. Verification: focused ESLint passed; dashboard recording Vitest `4 passed`; dashboard build passed; patched Compose dashboard rebuilt; fresh `/meetings/11` load had `readyState=4`, `duration=120.116`, no media error, and Play advanced the recording.
- Follow-up fix `5cef87b` proxies recording playback through the dashboard same-origin raw media route instead of giving the browser a direct MinIO public endpoint. Verification: dashboard recording Vitest `5 passed`; focused ESLint passed with only existing meeting-page warnings; dashboard build passed; patched Compose dashboard rebuilt; meeting 14 machine deployment proof passed with raw route `206`, `ffprobe duration=39.356`, headless Chromium `readyState=4`, visible `0:39`, no media error, and playback advancing.
- Follow-up fix `a7ba8f1` stabilizes browser playback delivery: audio WebM masters/raw routes now use `audio/webm`, and the dashboard audio player no-ops identical fragment duration updates so metadata/playback cannot spin the React media listener loop. Verification: meeting-api focused tests `10 passed`; dashboard recording Vitest `5 passed`; focused ESLint passed with only existing meeting-page warnings; dashboard build passed; patched Compose meeting-api/dashboard rebuilt; app-controlled browser on `/meetings/14` remained responsive with same-origin raw `src`, `readyState=4`, `duration=39.356`, visible `0:00 / 0:39`, no media error, and muted playback advanced to `currentTime=0.587054`.
- Lite hot validation still needs to run after the fix.

Hardenloop: completed with caveat
- Zero normalized release blockers.
- Decision: `incomplete_coverage` because several scanners were unavailable locally.
- Coverage caveat recorded in `.agents/releases/0.10.6.x-replay/state.md`.

Code review: completed with fix
- Found and fixed a blocker where the dashboard returned the JSON master endpoint as the media source instead of the resolved presigned/raw media URL.
- Fix commit: `78c3e81`
- Verification: focused Vitest `3 passed`; focused lint passed; dashboard build passed.
- Follow-up hot-failure fixes: `6aae0fa`, `c56b7d5`, `5cef87b`, `a7ba8f1`
- Verification: focused Vitest `5 passed`; focused lint passed; dashboard build passed.

## Evidence checklist

- [x] `pack.json`
- [x] `runtime.json`
- [x] `ops/ops.jsonl`
- [x] `tests`
- [x] `compose`
- [x] `lite`
- [x] `compose/human-eyeball.md`
- [x] `lite/human-eyeball.md`
- [x] `human/overall-functionality.md`
- [x] `hardenloop`
- [x] `review.md`
- [x] `pr.md`

## Stitching notes

- Shared files must be merged by hunk and reviewed against adjacent pack edits.
- If stitching exposes a behavior bug, route it back to the responsible pack instead of hiding a stitch-time code change.
- This epic is not a release sign-off; it is the delivery contract for one independently reviewable pack.

## Review checklist

- [x] Pack branch starts from `v0.10.6^{}`.
- [x] Only this pack's committed reuse hunks are replayed.
- [ ] Hot human-in-the-loop checks pass against Compose and Lite targets.
- [x] Compose gate is passed or explicitly marked not required in PR evidence.
- [x] Lite gate is passed or explicitly marked not required in PR evidence.
- [x] Hardenloop is run for the pack.
- [x] Code review completed and blocker fixed.
- [x] PR body links this epic and evidence root.
- [x] Reviewer can map each reused hunk back to the commit list above.
