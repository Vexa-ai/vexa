import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { useRef } from "react";

// ONE ACTIONS AREA ON A MEETING / VOICE NOTE PAGE (#1783) — founder: "where is stop bot, delete, etc?"
const state = vi.hoisted(() => ({ refresh: vi.fn(), notes: vi.fn(), update: vi.fn() }));
vi.mock("../../surfaces/liveMeetings", () => ({ refreshMeetings: state.refresh, useLiveMeetings: () => [] }));
vi.mock("../../surfaces/MeetingBundleActions", () => ({ exportable: () => false, ExportMeetingButton: () => null }));
vi.mock("../meetingNote", () => ({ createMeetingNotes: state.notes, updateMeetingNotes: state.update }));
vi.mock("../../surfaces/defaultBotName", () => ({ defaultBotName: () => "Vexa" }));
import { meetingActs } from "../meetingActions";
import { MeetingActionsBar, MEETING_ACT_EVENT } from "../MeetingActionsBar";
import { resetMeetingDeletions } from "../meetingDeletion";

const meet = (over: Record<string, unknown> = {}) => ({ id: "42", native_id: "abc-defg-hij", platform: "Google Meet",
  status: "past", live_status: "completed", participants: [], ...over }) as any;

function Bar({ m }: { m: any }) {
  const ref = useRef<HTMLDivElement>(null);
  return <div ref={ref}><button data-call-name onClick={() => state.refresh("renamed")}>Weekly</button><MeetingActionsBar meeting={m} header={ref} /></div>;
}

beforeEach(() => { resetMeetingDeletions(); vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 204 }))); state.notes.mockResolvedValue("kg/meetings/x.md"); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

describe("which acts a meeting offers", () => {
  const ids = (acts: { id: string; primary?: boolean }[]) => acts.map((a) => (a.primary ? `*${a.id}` : a.id));
  it("live meeting: Stop bot leads; no Delete while it is happening", () => {
    expect(ids(meetingActs(meet({ status: "live", live_status: "active" }), { canChangeLanguage: true }))).toEqual(["*stop-bot", "rename", "language", "share"]);
  });
  it("ended meeting: Update notes and Send bot again lead; Delete in the menu", () => {
    expect(ids(meetingActs(meet(), { canExport: true }))).toEqual(["*notes", "*send-again", "rename", "share", "export", "delete"]);
  });
  it("a voice note this tab records: Pause and Stop; after it ends no Send bot again", () => {
    const v = meet({ platform: "voice_note", live_status: "active", status: "live" });
    expect(ids(meetingActs(v, { recording: { paused: false } }))).toEqual(["*pause", "*stop-note", "rename", "share"]);
    expect(ids(meetingActs(v, { recording: { paused: true } }))[0]).toBe("*resume");
    expect(ids(meetingActs(meet({ platform: "voice_note" })))).toEqual(["*notes", "rename", "share", "delete"]);
  });
  it("a Vexa call's transcriber starts and ends with the call: never Stop bot, never Send bot again", () => {
    const c = meet({ platform: "vexa_call", native_id: "call-0c90e2a5fcff" });
    expect(ids(meetingActs({ ...c, status: "live", live_status: "active" }))).toEqual(["rename", "share"]);
    expect(ids(meetingActs(c))).toEqual(["*notes", "rename", "share", "delete"]);
  });
  it("a meeting still saving offers Delete (it waits) but not notes yet", () => {
    expect(ids(meetingActs(meet({ live_status: "stopping" })))).toEqual(["rename", "share", "delete"]);
  });
});

describe("the page's actions area", () => {
  it("Stop bot calls the user-stop route", async () => {
    render(<Bar m={meet({ status: "live", live_status: "active" })} />);
    fireEvent.click(screen.getByRole("button", { name: "Stop bot" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/bots/google_meet/abc-defg-hij", { method: "DELETE" }));
  });

  it("Send bot again posts the same link; Update notes creates then writes the notes", async () => {
    render(<Bar m={meet({ meeting_url: "https://meet.google.com/abc-defg-hij" })} />);
    fireEvent.click(screen.getByRole("button", { name: "Send bot again" }));
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    const [url, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/bots");
    expect(JSON.parse(String(init.body))).toEqual({ platform: "google_meet", native_meeting_id: "abc-defg-hij", meeting_url: "https://meet.google.com/abc-defg-hij", bot_name: "Vexa" });
    fireEvent.click(screen.getByRole("button", { name: "Update notes" }));
    await waitFor(() => expect(state.update).toHaveBeenCalledWith("42", "kg/meetings/x.md"));
  });

  it("the … menu holds the rest; Rename presses the title's own control; Delete confirms once", async () => {
    render(<Bar m={meet()} />);
    fireEvent.click(screen.getByRole("button", { name: "More meeting actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Rename/ }));
    expect(state.refresh).toHaveBeenCalledWith("renamed");
    fireEvent.click(screen.getByRole("button", { name: "More meeting actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Delete meeting/ }));
    expect(screen.getByText(/transcript, notes and recording are deleted for good/)).toBeTruthy();
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Delete meeting" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/meetings/42", { method: "DELETE" }));
    await waitFor(() => expect(screen.queryByRole("toolbar", { name: "Meeting actions" })).toBeNull());
  });

  it("takes an act sent from the rail", async () => {
    render(<Bar m={meet({ status: "live", live_status: "active" })} />);
    const detail = { meetingId: "42", act: "stop-bot", handled: false };
    window.dispatchEvent(new CustomEvent(MEETING_ACT_EVENT, { detail }));
    expect(detail.handled).toBe(true);
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/bots/google_meet/abc-defg-hij", { method: "DELETE" }));
  });
});
