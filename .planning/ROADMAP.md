# Vexa Local Deploy — Release Roadmap

## Release: local-snapshots-v1
**Goal:** Get recording, transcription, and frame snapshots working in local deploy.

**Status:** In progress

### Phase 1: Bug Fixes (develop) ✅ MOSTLY DONE
- [x] BUG-4: Integer overflow on snowflake IDs (BigInteger fix)
- [x] BUG-6: SNAPSHOTS_ENABLED env var default
- [x] BUG-2: CAPTURE_MODES env var default
- [x] BUG-3: Frame extractor race condition (manual re-run works)
- [ ] BUG-1: Transcription fails (upstream bot #355)
- [ ] BUG-5: Bot doesn't auto-leave (low priority)

### Phase 2: Verification (validate)
- [ ] End-to-end test: create meeting → record → extract frames → view in dashboard
- [ ] Verify deferred transcription works as workaround for BUG-1
- [ ] Verify dashboard renders frame snapshots correctly

### Phase 3: Ship (ship)
- [ ] Document all env vars and configuration
- [ ] Clean up .env duplicates
- [ ] Commit BigInteger model changes to branch

## Key Decisions
- **D-07:** `recording_id` in RecordingFrame is NOT a FK (JSONB mode has no recordings SQL rows)
- **D-25:** SNAPSHOTS_ENABLED feature flag (opt-in, default false)
- **D-26:** `recording_id` uses BigInteger for snowflake IDs (bot generates >int32)
- **D-33:** Thumbnail size 320x180 WebP quality 75
- **D-34:** Dual FPS heuristic: 1fps/5s (<5min), 1fps/30s (>=5min)
- **D-36:** Idempotent frame extraction (DB check + first-frame MinIO check)
- **D-37:** Isolated failure (frames_status='failed' on error, not crash meeting)