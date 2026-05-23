---
services:
- meeting-api
- dashboard
- api-gateway
---
# Intentionally un-gated until DoD checks are wired (Phase 5).

**DoDs:** see [`./dods.yaml`](./dods.yaml) · Gate: **confidence ≥ 0%** (un-gated until Phase 5)

## Why

A user reviewing a transcribed meeting can scan a visual timeline of frame thumbnails and jump the video to any moment with one click — without watching the full recording. Similar to Otter's Automated Slide Capture, but as a standalone gallery on the meeting detail page, reusing the existing `master.webm` and MinIO storage already present in Vexa. Opt-in via `SNAPSHOTS_ENABLED` so no surprise on upgrade.

## What

Post-recording worker (`frame_extractor.py` in `meeting-api`) extracts one frame every 30 seconds from `master.webm` via ffmpeg, downscales with Pillow, uploads WebP thumbnails to MinIO under `recordings/{user_id}/{recording_id}/{session_uid}/frames/{seq:06d}.webp`, and writes one row per thumbnail to a new `recording_frames` table. A `GET /recordings/{id}/frames` endpoint returns presigned URLs (15-min TTL). A `<SnapshotsGallery>` React component renders the grid on the meeting detail page with click-to-seek on the existing video player. Zero new npm packages.

## Locked Design Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | `SNAPSHOTS_ENABLED=false` default (opt-in) | Vexa's philosophy is opt-in; default-on breaks existing self-hosted deployments on upgrade. |
| 2 | Presigned URL TTL = 15 minutes + JIT IntersectionObserver minting | Short TTL limits leak window; JIT avoids batch-signing N URLs the user never scrolls to (avoids ~90 ms event-loop blocking on 180-frame galleries). |
| 3 | `meeting_id` hard FK to `meetings.id` (ON DELETE CASCADE) + `recording_id` non-FK Integer | Production runs in `meeting_data` JSONB mode where no `recordings` SQL rows exist; hard FK to `recordings` would break on container start. |
| 4 | Extend `meeting-api` (no new container) | Matches the `recording_finalizer.py` precedent; no message broker exists; bounded work (~1-2 min per meeting). |
| 5 | Trigger on `media_file.is_final && finalized_by=='recording_finalizer.master'` (NOT `meeting.status`) | Documented race in `recording_finalizer.py`: status flips before master is fully finalized. |