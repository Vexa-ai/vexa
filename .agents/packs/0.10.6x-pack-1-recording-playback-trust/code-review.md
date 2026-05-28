# PACK 1 Code Review

Status: pass

## Verdict

`pass` — human reviewer signed off on PR #364 split-view diff after one
prior fix landed and the second-pass finder draft was triaged.

- reviewer: Dmitry (`dmitry@vexa.ai`)
- timestamp: 2026-05-24
- review surface: PR #364 split-view diff on GitHub
- automated draft input: PR #364 comment [#4528719872](https://github.com/Vexa-ai/vexa/pull/364#issuecomment-4528719872)
  (10 candidate findings, 3-angle recall-biased finder sweep)

## Blast-radius surfaces reviewed

- `services/meeting-api/meeting_api/recording_finalizer.py` — master assembly, `finalize_recording_master` entry, inline JSONB recovery path, webm streaming master builder, WAV master builder.
- `services/meeting-api/meeting_api/recordings.py` — chunk-upload auth + binding; per-recording / per-master endpoints; master-key suffix guard; storage path / playback_url JSONB layout.
- `services/meeting-api/meeting_api/sweeps.py` — `_sweep_unfinalized_recordings` recovery loop; `recover_recordings_jsonb_from_storage` inline counterpart; `UNFINALIZED_RECORDINGS_MIN_AGE_SECONDS` cutoff.
- `services/meeting-api/meeting_api/storage.py` — bounded-memory list/download helpers; presigned URL generation.
- `services/api-gateway/main.py` — new `/recordings/{id}/master` proxy route; existing `/recordings/{id}/...` proxy passthrough.
- `services/dashboard/src/app/meetings/[id]/page.tsx` — post-meeting playback bar; `missingRequestedRecording` / `noAudioRecordingForMeeting` UI branches; per-segment Play buttons; finalizer-completion polling.
- `services/dashboard/src/lib/` — playback fragment client; audio player wiring.

## Findings history

### Fixed: Dashboard returned JSON endpoint as media source

- Severity: blocker
- Files: `services/dashboard/src/lib/api.ts`, `services/dashboard/tests/test_recording_master_api.test.ts`
- Commit: `78c3e81`

The dashboard helper called `/recordings/{id}/master?type=audio`, confirmed the backend returned a media URL, but then returned `/api/vexa/recordings/{id}/master?type=audio&proxy=1` as the `<audio>` source. The dashboard proxy has no `proxy=1` media special case, so the browser would load JSON instead of audio/video.

Fix: return absolute presigned object-store URLs directly and route relative `/recordings/{id}/media/{media_id}/raw` paths through `/api/vexa...`. The focused test now covers both local raw proxy and object-store presigned URL behavior.

### Second-pass finder draft

10 candidate findings posted on PR #364 (HIGH: token-claims bypass, missing SQL→JSONB migration, video master overwrite, sweep commit-then-finalize race; MEDIUM–HIGH: FastAPI `Query(regex=)` deprecation, WAV unbounded memory; MEDIUM: JWT TTL 7200s, dashboard polling forever, `playbackConnectionError` deadlock; LOW–MEDIUM: deleted upload-failure status mark). Triaged by reviewer; none promoted to blocker for this PR.

## Verification

- `tests/dashboard-recording-master-api-code-review-fix-rerun`: `3 passed`
- `tests/dashboard-touched-files-lint-code-review-fix-rerun`: passed
- `tests/dashboard-build-code-review-fix`: passed
- Live Compose r1 (meeting 15): 23 segs / 14:19 anchors, master.webm 4.8 MB, presigned playback URL serves audio/webm bytes end-to-end.
- Live Lite r3 (meeting 7): 28 segs / 13:19 anchors, master.webm 6.4 MB, playback URL populated in DB.
- Live Lite Teams (meeting 11): 6 chunks, master.webm 1.5 MB, finalizer self-heal wrote `playback_url` ~16 ms after meeting completed.

## Scope-bounded check

- [x] Diff aligns with pack 1 epic (#356) blast radius: recording finalization, master playback URL surfacing, dashboard playback UX trust.
- [x] No changes outside scope (no bot lifecycle, billing, identity surfaces).
- [x] No hidden stitch-time changes; everything lands in PR #364.

## Residual Risks

- Hot human-in-the-loop Compose and Lite validation remains pending.
- Hardenloop remains `incomplete_coverage` due to missing local scanners, with zero normalized release blockers.

## Post-pass follow-ups

After the human reviewer signed off, three additional commits landed on the pack branch. None of them re-opened the code-review verdict; they are recorded here so the diff in PR #364 is fully explained:

- `898b261 fix(dashboard): neutralize "missing recording" red banner` — `services/dashboard/src/app/meetings/[id]/page.tsx`: changes the red destructive banner to a neutral "Recording is finalizing..." state during the normal post-meeting finalization window. Pack 1 trust UX polish.
- `60644d2 fix(recordings): self-heal finalizer when bot exits before chunk-write` — `services/meeting-api/meeting_api/recording_finalizer.py` and `services/meeting-api/meeting_api/sweeps.py`: finalizer now inline-recovers JSONB from storage when the meeting record's recordings list is empty due to a bot-exit-vs-chunk-write race; the unfinalized-sweep min-age cutoff drops from 120s to 5s so the safety net catches real escapes fast. Same blast radius (`recording_finalizer.py`, `sweeps.py`).
- `5acf36c fix(pack-1): use @example.com (RFC 2606) instead of @example.invalid (RFC 6761)` — `ops/lite_playback_smoke.py` evidence-only fix: the seeded user email used `@example.invalid`, which Pydantic v2 EmailStr rejects, breaking `GET /admin/users` for any client after the stitched 0.10.6.3 candidate ran the smoke. Found by the autonomous stitch sweep. No product code touched.
