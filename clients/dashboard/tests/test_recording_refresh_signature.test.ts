import { describe, expect, it } from "vitest";
import type { Meeting, RecordingData } from "@/types/vexa";
import { recordingsStateSignature } from "@/stores/meetings-store";

function meetingWithRecording(overrides: Partial<RecordingData>): Meeting {
  return {
    id: "5",
    platform: "google_meet",
    platform_specific_id: "bvf-rzuj-kwj",
    status: "completed",
    start_time: "2026-05-23T15:55:51Z",
    end_time: "2026-05-23T16:19:14Z",
    bot_container_id: null,
    created_at: "2026-05-23T15:55:51Z",
    data: {
      recordings: [
        {
          id: 591809991140,
          meeting_id: 5,
          user_id: 2,
          session_uid: "session-a",
          source: "bot",
          status: "in_progress",
          created_at: "2026-05-23T15:56:45Z",
          completed_at: null,
          media_files: [],
          playback_url: null,
          ...overrides,
        },
      ],
    },
  } as Meeting;
}

describe("recordingsStateSignature", () => {
  it("changes when an existing recording becomes playback-ready without changing count", () => {
    const before = meetingWithRecording({});
    const after = meetingWithRecording({
      status: "completed",
      completed_at: "2026-05-23T16:19:14Z",
      playback_url: {
        audio: "/recordings/591809991140/master?type=audio",
        video: null,
      },
      media_files: [
        {
          id: 191580850820,
          type: "audio",
          format: "webm",
          storage_path: "recordings/2/591809991140/session-a/audio/master.webm",
          storage_backend: "minio",
          file_size_bytes: 3858006,
          duration_seconds: null,
          finalized_by: "recording_finalizer.master",
          is_final: true,
          created_at: "2026-05-23T16:18:47Z",
        },
      ],
    });

    expect(recordingsStateSignature(before)).not.toEqual(recordingsStateSignature(after));
  });
});
