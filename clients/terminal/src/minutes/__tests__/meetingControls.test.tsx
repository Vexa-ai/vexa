import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
const state = vi.hoisted(() => ({ status: "completed", deleted: false, refresh: vi.fn() }));
vi.mock("../../surfaces/liveMeetings", () => ({
  useLiveMeetings: () => [{ id: "42", native_id: "native-zoom", platform: "zoom", status: state.status === "active" ? "live" : "past", live_status: state.status, artifacts_deleted: state.deleted }],
  useLiveMeetingsConnection: () => true, refreshMeetings: state.refresh,
}));
import { MeetingControls } from "../MeetingControls";
beforeEach(() => {
  state.status = "completed";
  state.deleted = false;
  vi.stubGlobal("fetch", vi.fn(async (_url, init) => init?.method === "DELETE" ? new Response(null, { status: 204 }) : new Response(JSON.stringify({ recordings: [{ id: 7, media_files: [{ id: 9, type: "audio" }] }] }))));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
describe("meeting controls", () => {
  it("offers playback without autoplay and requires the exact confirmation word before deleting", async () => {
    const { container } = render(<MeetingControls meetingId="42" />);
    await waitFor(() => expect(container.querySelector("audio")).not.toBeNull());
    const audio = container.querySelector("audio")!;
    expect(audio.getAttribute("src")).toBe("/api/recordings/7/media/9/raw?type=audio");
    expect(audio.autoplay).toBe(false);
    expect(audio.preload).toBe("metadata");
    fireEvent.click(screen.getByRole("button", { name: "Delete meeting data" }));
    expect(vi.mocked(fetch).mock.calls.filter(c => c[1]?.method === "DELETE")).toHaveLength(0);
    const confirm = screen.getByRole("button", { name: "Delete permanently" }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Type delete to confirm"), { target: { value: "DELETE" } });
    expect(confirm.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Type delete to confirm"), { target: { value: "delete" } });
    expect(confirm.disabled).toBe(false);
    fireEvent.click(confirm);
    await screen.findByText("Meeting audio, transcript and fixtures deleted. Saved pages are kept.");
    expect(container.querySelector("audio")).toBeNull();
  });
  it("does not offer Delete again, or load a recording, for a meeting whose data was deleted", async () => {
    state.deleted = true;
    const { container } = render(<MeetingControls meetingId="42" />);
    expect(screen.queryByRole("button", { name: "Delete meeting data" })).toBeNull();
    expect(screen.getByText("Meeting audio, transcript and fixtures deleted. Saved pages are kept.")).toBeTruthy();
    expect(container.querySelector("audio")).toBeNull();
    expect(vi.mocked(fetch).mock.calls.filter(c => String(c[0]).startsWith("/api/recordings"))).toHaveLength(0);
  });
  it("stops the correct bot and does not offer deletion or playback for an active meeting", async () => {
    state.status = "active";
    render(<MeetingControls meetingId="42" />);
    expect(screen.queryByText("Delete meeting data")).toBeNull();
    fireEvent.click(screen.getByText("Stop bot"));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/bots/zoom/native-zoom", { method: "DELETE" }));
  });
});
