/** ws.v1 CONTRACT-CONFORMANCE for the terminal CONSUMER.
 *
 *  Pins gatewayWS.parseFrame to the SHARED ws.v1 golden — the same sealed frame the producer (meeting-api)
 *  and the other consumer (gateway) assert against:
 *      core/gateway/contracts/ws.v1/golden/MeetingStatus.scheduled.json
 *  (vendored verbatim into ./fixtures/ for the terminal package). If the contract drifts, this fails.
 */
import { describe, it, expect } from "vitest";
import { parseFrame } from "../gatewayWS";
import golden from "./fixtures/MeetingStatus.scheduled.json";

describe("parseFrame pinned to ws.v1 golden", () => {
  it("normalises the sealed golden frame to its flat fields", () => {
    const f = parseFrame(golden);
    expect(f).not.toBeNull();
    // The golden carries BOTH flat fields and the legacy-nested shape; the consumer reads the flat ones.
    expect(f).toEqual({
      meeting_id: golden.meeting_id,
      native: golden.native,
      status: golden.status,
      when: golden.when,
    });
  });

  it("reads the flat fields exactly as the golden declares them", () => {
    const f = parseFrame(golden)!;
    expect(f.status).toBe("scheduled");
    expect(f.native).toBe("abc-defg-hij");
    expect(f.meeting_id).toBe(42);
    expect(f.when).toBe("2026-06-25T18:00:00Z");
  });

  it("normalises a PURELY-nested legacy frame identically to the flat shape (terminal-bot-action-roundtrip)", () => {
    // The old producer emitted only {meeting:{id,native_id}, payload:{status}, ts} with NO flat fields.
    const nested = {
      type: "meeting.status",
      meeting: { id: 42, native_id: "abc-defg-hij" },
      payload: { status: "active" },
      ts: "2026-06-25T18:05:00Z",
    };
    expect(parseFrame(nested)).toEqual({
      meeting_id: 42, native: "abc-defg-hij", status: "active", when: "2026-06-25T18:05:00Z",
    });
  });

  it("normalises the full bot-action lifecycle progression to flat status strings (terminal-bot-action-roundtrip)", () => {
    const frame = (status: string) => ({ type: "meeting.status", meeting_id: 42, native: "abc-defg-hij", status });
    expect(["requested", "active", "completed"].map((s) => parseFrame(frame(s))!.status))
      .toEqual(["requested", "active", "completed"]);
  });

  it("rejects non-meeting.status frames", () => {
    expect(parseFrame({ ...golden, type: "transcription_segment" })).toBeNull();
    expect(parseFrame({ type: "meeting.status" })).toBeNull(); // no status anywhere
    expect(parseFrame(null)).toBeNull();
  });
});
