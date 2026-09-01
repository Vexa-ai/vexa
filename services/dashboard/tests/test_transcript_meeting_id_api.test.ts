import { afterEach, describe, expect, it, vi } from "vitest";
import { vexaAPI } from "@/lib/api";

function transcriptResponse(id: number) {
  return {
    ok: true,
    json: async () => ({
      id,
      platform: "zoom",
      native_meeting_id: "89237402037",
      status: "active",
      start_time: "2026-07-23T19:31:00Z",
      end_time: null,
      segments: [],
    }),
  };
}

describe("vexaAPI.getMeetingWithTranscripts", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("uses the exact meeting-row endpoint when an internal meeting id is supplied", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(transcriptResponse(13614)));

    const result = await vexaAPI.getMeetingWithTranscripts(
      "zoom",
      "89237402037",
      "13614"
    );

    expect(fetch).toHaveBeenCalledWith("/api/vexa/transcripts/by-id/13614");
    expect(result.meeting.id).toBe("13614");
  });

  it("retains native-id lookup when no internal meeting id is supplied", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(transcriptResponse(13616)));

    await vexaAPI.getMeetingWithTranscripts("zoom", "89237402037");

    expect(fetch).toHaveBeenCalledWith(
      "/api/vexa/transcripts/zoom/89237402037"
    );
  });
});
