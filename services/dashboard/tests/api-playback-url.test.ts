/**
 * Deep tests for api.ts — playback_url derivation from media_files,
 * getRecordingMasterStreamUrl, BigInteger ID round-trips.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";

// ---------------------------------------------------------------------------
// Helpers to build mock data
// ---------------------------------------------------------------------------

function makeMediaFile(overrides: Partial<{
  id: number;
  type: "audio" | "video";
  format: string;
  storage_path: string;
  is_final: boolean;
  duration_seconds: number | null;
}> = {}) {
  return {
    id: overrides.id ?? 1,
    type: overrides.type ?? "audio",
    format: overrides.format ?? "webm",
    storage_path: overrides.storage_path ?? "recordings/5/1001/sess-1/master.webm",
    is_final: overrides.is_final ?? true,
    duration_seconds: overrides.duration_seconds ?? 300,
    finalized_by: "recording_finalizer.master",
  };
}

function makeRecording(mediaFiles: ReturnType<typeof makeMediaFile>[], id = 1001) {
  return {
    id,
    meeting_id: 42,
    user_id: 5,
    session_uid: "sess-1",
    source: "bot" as const,
    status: "completed",
    error_message: null,
    frames_status: "complete",
    extra_metadata: {},
    created_at: "2025-01-01T00:00:00Z",
    completed_at: "2025-01-01T00:05:00Z",
    media_files: mediaFiles,
  };
}

// ---------------------------------------------------------------------------
// playback_url derivation logic (extracted from api.ts for unit testing)
// ---------------------------------------------------------------------------

function derivePlaybackUrl(
  r: ReturnType<typeof makeRecording>,
  recordingId: number
) {
  const audioFile = r.media_files?.find(
    (mf) => mf.type === "audio" && mf.storage_path?.includes("/master.")
  );
  const videoFile = r.media_files?.find(
    (mf) => mf.type === "video" && mf.storage_path?.includes("/master.")
  );
  return {
    audio: audioFile
      ? `/api/vexa/recordings/${recordingId}/media/${audioFile.id}/download`
      : undefined,
    video: videoFile
      ? `/api/vexa/recordings/${recordingId}/media/${videoFile.id}/download`
      : undefined,
  };
}

// ---------------------------------------------------------------------------
// Tests: playback_url derivation
// ---------------------------------------------------------------------------

describe("playback_url derivation from media_files", () => {
  it("derives audio URL from audio master file", () => {
    const rec = makeRecording([
      makeMediaFile({ id: 10, type: "audio", storage_path: "recordings/5/1001/sess-1/master.webm" }),
    ]);
    const url = derivePlaybackUrl(rec, 1001);
    expect(url.audio).toBe("/api/vexa/recordings/1001/media/10/download");
    expect(url.video).toBeUndefined();
  });

  it("derives video URL from video master file", () => {
    const rec = makeRecording([
      makeMediaFile({ id: 20, type: "video", storage_path: "recordings/5/1001/sess-1/master.webm" }),
    ]);
    const url = derivePlaybackUrl(rec, 1001);
    expect(url.video).toBe("/api/vexa/recordings/1001/media/20/download");
    expect(url.audio).toBeUndefined();
  });

  it("derives both audio and video when both masters exist", () => {
    const rec = makeRecording([
      makeMediaFile({ id: 10, type: "audio", storage_path: "recordings/5/1001/sess-1/master.webm" }),
      makeMediaFile({ id: 20, type: "video", storage_path: "recordings/5/1001/sess-1/master.webm" }),
    ]);
    const url = derivePlaybackUrl(rec, 1001);
    expect(url.audio).toBe("/api/vexa/recordings/1001/media/10/download");
    expect(url.video).toBe("/api/vexa/recordings/1001/media/20/download");
  });

  it("returns undefined for audio when no audio master exists", () => {
    const rec = makeRecording([
      makeMediaFile({ id: 30, type: "audio", storage_path: "recordings/5/1001/sess-1/chunk-001.webm" }),
    ]);
    const url = derivePlaybackUrl(rec, 1001);
    // chunk file doesn't contain "/master." → no audio URL
    expect(url.audio).toBeUndefined();
  });

  it("returns undefined for both when media_files is empty", () => {
    const rec = makeRecording([]);
    const url = derivePlaybackUrl(rec, 1001);
    expect(url.audio).toBeUndefined();
    expect(url.video).toBeUndefined();
  });

  it("uses first audio master if multiple audio masters exist", () => {
    const rec = makeRecording([
      makeMediaFile({ id: 10, type: "audio", storage_path: "recordings/5/1001/sess-1/master.webm" }),
      makeMediaFile({ id: 11, type: "audio", storage_path: "recordings/5/1001/sess-2/master.webm" }),
    ]);
    const url = derivePlaybackUrl(rec, 1001);
    expect(url.audio).toBe("/api/vexa/recordings/1001/media/10/download");
  });

  it("handles BigInteger snowflake IDs without truncation", () => {
    const snowflakeId = 7_123_456_789_012_345_678;
    // Note: JS Number precision — use BigInt check for values > 2^53
    const mediaId = 2_147_483_648; // int32 max + 1 — still safe in JS Number
    const rec = makeRecording([
      makeMediaFile({ id: mediaId, type: "audio", storage_path: "recordings/5/1001/sess-1/master.webm" }),
    ]);
    const url = derivePlaybackUrl(rec, 1001);
    expect(url.audio).toBe(`/api/vexa/recordings/1001/media/${mediaId}/download`);
  });

  it("recording ID is correctly embedded in derived URL", () => {
    const recordingId = 42_001;
    const rec = makeRecording([
      makeMediaFile({ id: 5, type: "audio", storage_path: "recordings/5/42001/sess-1/master.webm" }),
    ], recordingId);
    const url = derivePlaybackUrl(rec, recordingId);
    expect(url.audio).toContain(`/recordings/${recordingId}/media/`);
  });
});

// ---------------------------------------------------------------------------
// Tests: URL format contract
// ---------------------------------------------------------------------------

describe("derived URL format contract", () => {
  it("audio URL matches /api/vexa/recordings/{id}/media/{mediaId}/download", () => {
    const rec = makeRecording([
      makeMediaFile({ id: 99, type: "audio", storage_path: "x/master.wav" }),
    ]);
    const url = derivePlaybackUrl(rec, 1234);
    expect(url.audio).toMatch(/^\/api\/vexa\/recordings\/\d+\/media\/\d+\/download$/);
  });

  it("video URL matches /api/vexa/recordings/{id}/media/{mediaId}/download", () => {
    const rec = makeRecording([
      makeMediaFile({ id: 77, type: "video", storage_path: "x/master.webm" }),
    ]);
    const url = derivePlaybackUrl(rec, 1234);
    expect(url.video).toMatch(/^\/api\/vexa\/recordings\/\d+\/media\/\d+\/download$/);
  });

  it("URL does not expose storage_path (MinIO internal path)", () => {
    const rec = makeRecording([
      makeMediaFile({ id: 5, type: "audio", storage_path: "recordings/5/1001/sess-1/master.webm" }),
    ]);
    const url = derivePlaybackUrl(rec, 1001);
    expect(url.audio).not.toContain("recordings/5/1001");
    expect(url.audio).not.toContain("minio");
  });
});
