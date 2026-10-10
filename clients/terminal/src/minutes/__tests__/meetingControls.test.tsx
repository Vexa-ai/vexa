import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
const state = vi.hoisted(() => ({ status: "completed", deleted: false, refresh: vi.fn() }));
vi.mock("../../surfaces/liveMeetings", () => ({
  useLiveMeetings: () => [{ id: "42", native_id: "native-zoom", platform: "zoom", status: state.status === "active" ? "live" : "past", live_status: state.status, artifacts_deleted: state.deleted }],
  useLiveMeetingsConnection: () => true, refreshMeetings: state.refresh,
}));
import { MeetingControls, MeetingDeleteButton } from "../MeetingControls";
import { resetMeetingDeletions } from "../meetingDeletion";
beforeEach(() => {
  resetMeetingDeletions();
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
    expect(screen.queryByRole("button", { name: "Delete meeting data" })).toBeNull();
    fireEvent.click(screen.getByText("Stop bot"));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/bots/zoom/native-zoom", { method: "DELETE" }));
  });
  it("is one compact row: play, time, scrubber and download as icon buttons, no hint line and no mute", async () => {
    render(<MeetingControls meetingId="42" showDelete={false} />);
    const row = await screen.findByRole("group", { name: "Recording player" });
    const names = [...row.querySelectorAll("button,input")].map(el => el.getAttribute("aria-label"));
    expect(names).toEqual(["Play recording", "Seek recording", "Download audio"]);
    for (const b of row.querySelectorAll("button")) {
      expect(b.textContent).toBe("");                       // icons, not words
      expect(b.getAttribute("title")).toBeTruthy();         // every icon button has a tooltip
      expect(b.querySelector("svg")).not.toBeNull();
    }
    expect(screen.getByRole("button", { name: "Play recording" }).getAttribute("title")).toMatch(/click transcript text to play from there/);
    expect(screen.queryByText(/Click transcript text/)).toBeNull();
    expect(screen.queryByRole("button", { name: /mute/i })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete meeting data" })).toBeNull();
  });
  it("the header's Delete removes the player through the typed confirmation", async () => {
    const { container } = render(<><MeetingDeleteButton meetingId="42" size="header" /><MeetingControls meetingId="42" showDelete={false} /></>);
    await waitFor(() => expect(container.querySelector("audio")).not.toBeNull());
    const del = screen.getByRole("button", { name: "Delete meeting data" });
    expect(del.getAttribute("data-tone")).toBe("danger");
    fireEvent.click(del);
    fireEvent.change(screen.getByLabelText("Type delete to confirm"), { target: { value: "delete" } });
    fireEvent.click(screen.getByRole("button", { name: "Delete permanently" }));
    await screen.findByText("Meeting audio, transcript and fixtures deleted. Saved pages are kept.");
    expect(container.querySelector("audio")).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete meeting data" })).toBeNull();
  });
});
