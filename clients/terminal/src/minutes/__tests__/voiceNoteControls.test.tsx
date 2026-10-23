/** A voice note's meeting controls (#1783): while this tab records it, the controls are the
 *  recorder's (time, Pause, Stop through the socket); otherwise Stop ends it like a bot. */
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
const rec = vi.hoisted(() => ({
  state: { phase: "recording", nativeId: "vn-abc", sentSec: 65, receivedSec: 64, maxSec: 1800, level: 0.2, paused: false } as Record<string, unknown>,
  stop: vi.fn(async () => {}), pause: vi.fn(), end: vi.fn(),
  meeting: { id: "42", native_id: "vn-abc", platform: "voice_note", status: "live", live_status: "active" } as Record<string, unknown>,
}));
vi.mock("../../surfaces/liveMeetings", () => ({
  useLiveMeetings: () => [rec.meeting],
  useLiveMeetingsConnection: () => true, refreshMeetings: vi.fn(),
}));
vi.mock("../voiceNote", async (orig) => ({
  ...(await orig<typeof import("../voiceNote")>()),
  useVoiceNote: () => rec.state, stopVoiceNote: rec.stop, pauseVoiceNote: rec.pause, endVoiceNote: rec.end,
}));
import { MeetingControls } from "../MeetingControls";
beforeEach(() => { vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 204 }))); });
afterEach(() => {
  cleanup(); vi.unstubAllGlobals(); rec.stop.mockClear(); rec.pause.mockClear(); rec.end.mockClear();
  rec.meeting = { id: "42", native_id: "vn-abc", platform: "voice_note", status: "live", live_status: "active" };
});

describe("voice note controls", () => {
  it("show the running time and stop through the recorder, never the bot route", async () => {
    rec.state = { ...rec.state, phase: "recording", nativeId: "vn-abc" };
    render(<MeetingControls meetingId="42" />);
    expect(screen.getByRole("status").textContent).toContain("Recording 1:05 / 30:00");
    expect(screen.queryByText("Stop bot")).toBeNull();
    fireEvent.click(screen.getByText("Pause"));
    expect(rec.pause).toHaveBeenCalledWith(true);
    fireEvent.click(screen.getByText("Stop"));
    expect(rec.stop).toHaveBeenCalled();
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });
  it("a note recording elsewhere is stopped like a bot", async () => {
    rec.state = { ...rec.state, phase: "idle", nativeId: undefined };
    render(<MeetingControls meetingId="42" />);
    fireEvent.click(screen.getByText("Stop"));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/bots/voice_note/vn-abc", { method: "DELETE" }));
  });

  // The meeting view is where a phone shows a note (the rail is a closed drawer there), so whatever
  // the recorder knows is said HERE too — never a silent "live · 0 lines".
  it("say why this note failed, in the meeting view", () => {
    rec.state = { ...rec.state, phase: "error", nativeId: "vn-abc", error: "Your sign-in was not accepted for recording." };
    render(<MeetingControls meetingId="42" />);
    expect(screen.getByRole("alert").textContent).toContain("sign-in was not accepted");
  });
  it("say the microphone is silent while recording", () => {
    rec.state = { ...rec.state, phase: "recording", nativeId: "vn-abc", warning: "No audio from your microphone." };
    render(<MeetingControls meetingId="42" />);
    expect(screen.getByText(/No audio from your microphone/)).toBeTruthy();
  });
  it("say the microphone is connecting before the recorder is ready", () => {
    rec.state = { ...rec.state, phase: "starting", nativeId: "vn-abc", warning: undefined };
    render(<MeetingControls meetingId="42" />);
    expect(screen.getByText(/Connecting the microphone/)).toBeTruthy();
  });
  it("release the recorder when the note ended on the server", () => {
    rec.state = { ...rec.state, phase: "recording", nativeId: "vn-abc", warning: undefined };
    rec.meeting = { ...rec.meeting, status: "post", live_status: "completed" };
    render(<MeetingControls meetingId="42" />);
    expect(rec.end).toHaveBeenCalledWith("vn-abc");
  });
});
