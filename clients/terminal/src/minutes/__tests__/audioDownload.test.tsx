import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
const state = vi.hoisted(() => ({ recordings: [] as unknown[], media: async () => new Response(new Blob([new Uint8Array([26, 69, 223, 163, 1])]), { status: 200, headers: { "Content-Type": "audio/webm" } }) }));
vi.mock("../../surfaces/liveMeetings", () => ({
  useLiveMeetings: () => [{ id: "42", native_id: "84512345678", platform: "zoom", status: "past", live_status: "completed", start_time: "2026-10-09T14:00:00Z" }],
  useLiveMeetingsConnection: () => true, refreshMeetings: vi.fn(),
}));
import { MeetingControls } from "../MeetingControls";
import { audioExtension, audioFilename } from "../audioDownload";

let saved: { href: string; download: string }[] = [];
beforeEach(() => {
  saved = [];
  state.recordings = [{ id: 7, media_files: [{ id: 9, type: "audio", format: "webm" }] }];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => String(url).includes("/raw") ? state.media() : new Response(JSON.stringify({ recordings: state.recordings }))));
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:audio"), revokeObjectURL: vi.fn() }));
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { saved.push({ href: this.href, download: this.download }); });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("download audio", () => {
  it("saves the recording through the playback route, named <platform>-<native_id>-<date>.<ext>", async () => {
    render(<MeetingControls meetingId="42" />);
    fireEvent.click(await screen.findByRole("button", { name: "Download audio" }, { timeout: 5000 }));
    await waitFor(() => expect(saved).toHaveLength(1), { timeout: 5000 });
    expect(saved[0]).toEqual({ href: "blob:audio", download: "zoom-84512345678-2026-10-09.webm" });
    const call = vi.mocked(fetch).mock.calls.find(c => String(c[0]).includes("/raw"))!;
    // Same-origin, owner-scoped route; the session cookie authorizes it, no credential in the URL.
    expect(call[0]).toBe("/api/recordings/7/media/9/raw?type=audio&download=1");
    expect(String(call[0])).not.toMatch(/key|token/i);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows an explicit error when the download is refused, and saves nothing", async () => {
    state.media = async () => new Response(JSON.stringify({ detail: "Recording not found" }), { status: 404 });
    render(<MeetingControls meetingId="42" />);
    fireEvent.click(await screen.findByRole("button", { name: "Download audio" }, { timeout: 5000 }));
    expect((await screen.findByRole("alert", {}, { timeout: 5000 })).textContent).toBe("Could not download audio: the recording was not found.");
    expect(saved).toHaveLength(0);
  });

  it("shows an explicit error when the connection fails", async () => {
    state.media = async () => { throw new TypeError("network"); };
    render(<MeetingControls meetingId="42" />);
    fireEvent.click(await screen.findByRole("button", { name: "Download audio" }, { timeout: 5000 }));
    expect((await screen.findByRole("alert", {}, { timeout: 5000 })).textContent).toMatch(/connection failed/);
  });

  it("offers no download when the meeting has no recording", async () => {
    state.recordings = [];
    render(<MeetingControls meetingId="42" />);
    await screen.findByText("No audio recording available.");
    expect(screen.queryByRole("button", { name: "Download audio" })).toBeNull();
  });

  it("names the file from the real container", () => {
    expect(audioExtension("audio/wav", "webm")).toBe("wav");
    expect(audioExtension("application/octet-stream", "ogg")).toBe("ogg");
    expect(audioExtension(null, "../x")).toBe("bin");
    expect(audioFilename({ id: "5", platform: "Google Meet", native_id: "abc-defg-hij" }, "webm")).toBe("google-meet-abc-defg-hij.webm");
  });
});
