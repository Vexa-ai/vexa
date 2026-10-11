/** The live meeting header's transcription language: shown while the meeting is live, switchable by
 *  the owner or an editor of its workspace only, and a refusal stays beside the control. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { MeetingMock } from "../../surfaces/meetingModel";

const state = vi.hoisted(() => ({ meeting: {} as unknown, refresh: vi.fn(), role: "reader" }));
vi.mock("../../surfaces/liveMeetings", () => ({
  useLiveMeetings: () => [state.meeting],
  useLiveMeetingsConnection: () => true,
  refreshMeetings: state.refresh,
}));
vi.mock("../../surfaces/workspaceApi", () => ({
  listSharedMemberships: async () => [{ workspace_id: "ws-1", role: state.role }],
}));

import { MeetingPageHeader } from "../MeetingPageHeader";

const NATIVE = "abc-defg-hij";
function meeting(over: Partial<MeetingMock> = {}): MeetingMock {
  return {
    id: "42", native_id: NATIVE, title: "Google Meet · " + NATIVE, when: "now", status: "live", live_status: "active",
    platform: "Google Meet", participants: [], mentioned: [], actions: [], transcript: [], insights: [], docs: [],
    transcription_language: { language: "de", allowed_languages: [], source: "user" },
    ...over,
  };
}
const shown = () => document.querySelector("[data-meeting-language]")?.textContent ?? null;
const mount = () => render(<MeetingPageHeader meetingId="42" body="# Weekly sync" path="meetings/42.md" />);

let calls: { url: string; init?: RequestInit }[];
function stubFetch(answer: (url: string) => Response) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => { calls.push({ url: String(url), init }); return answer(String(url)); }));
}
const configPuts = () => calls.filter((c) => c.url.endsWith("/config") && c.init?.method === "PUT");

beforeEach(() => {
  state.meeting = meeting();
  state.refresh.mockReset();
  state.role = "reader";
  stubFetch(() => new Response(JSON.stringify({ recordings: [] }), { status: 200 }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("meeting header — transcription language", () => {
  it("shows the language the bot runs with", () => {
    mount();
    expect(shown()).toBe("German");
  });

  it("the owner switches it: PUT /config with the replacing setting, then the list refreshes", async () => {
    stubFetch((url) => url.endsWith("/config")
      ? new Response(JSON.stringify({ meeting_id: 42, platform: "google_meet", native_meeting_id: NATIVE, transcription_language: { language: "en", allowed_languages: [], source: "meeting" } }), { status: 202 })
      : new Response(JSON.stringify({ recordings: [] }), { status: 200 }));
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Change language" }));
    fireEvent.change(screen.getByLabelText("Transcribe in"), { target: { value: "en" } });
    fireEvent.click(screen.getByRole("button", { name: "Switch language" }));
    await waitFor(() => expect(shown()).toBe("English"));
    const puts = configPuts();
    expect(puts).toHaveLength(1);
    expect(puts[0].url).toBe(`/api/bots/google_meet/${NATIVE}/config`);
    expect(JSON.parse(puts[0].init!.body as string)).toEqual({ language: "en", allowed_languages: null });
    expect(state.refresh).toHaveBeenCalled();
  });

  it("an editor of the bound workspace gets the control on a shared meeting", async () => {
    state.role = "contributor";
    state.meeting = meeting({ shared: true, workspace_id: "ws-1" });
    mount();
    expect(await screen.findByRole("button", { name: "Change language" })).toBeTruthy();
  });

  it("a reader sees the language and no control", async () => {
    state.role = "reader";
    state.meeting = meeting({ shared: true, workspace_id: "ws-1" });
    mount();
    expect(shown()).toBe("German");
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByRole("button", { name: "Change language" })).toBeNull();
    expect(screen.queryByLabelText("Language")).toBeNull();
  });

  it("a 403 says who may change it, keeps the previous value shown and the choice open", async () => {
    stubFetch((url) => url.endsWith("/config")
      ? new Response(JSON.stringify({ detail: "forbidden" }), { status: 403 })
      : new Response(JSON.stringify({ recordings: [] }), { status: 200 }));
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Change language" }));
    fireEvent.change(screen.getByLabelText("Transcribe in"), { target: { value: "en" } });
    fireEvent.click(screen.getByRole("button", { name: "Switch language" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/owner or an editor/);
    expect(shown()).toBe("German");
    expect((screen.getByLabelText("Transcribe in") as HTMLSelectElement).value).toBe("en");
    expect(state.refresh).not.toHaveBeenCalled();
  });

  it("shows a busy state while the switch runs", async () => {
    let release: (r: Response) => void = () => {};
    vi.stubGlobal("fetch", vi.fn((url: string) => String(url).endsWith("/config")
      ? new Promise<Response>((r) => { release = r; })
      : Promise.resolve(new Response("{}", { status: 200 }))));
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Change language" }));
    fireEvent.change(screen.getByLabelText("Transcribe in"), { target: { value: "en" } });
    fireEvent.click(screen.getByRole("button", { name: "Switch language" }));
    const busy = await screen.findByRole("button", { name: "Switching…" });
    expect((busy as HTMLButtonElement).disabled).toBe(true);
    release(new Response(JSON.stringify({ transcription_language: { language: "en", allowed_languages: [], source: "meeting" } }), { status: 202 }));
    await waitFor(() => expect(shown()).toBe("English"));
  });

  it("is not shown outside the live phase", () => {
    state.meeting = meeting({ status: "past", live_status: "completed" });
    mount();
    expect(shown()).toBeNull();
    expect(screen.queryByRole("button", { name: "Change language" })).toBeNull();
  });

  it("is not shown for a planned meeting either", async () => {
    state.meeting = meeting({ live_status: "scheduled", status: "past" });
    mount();
    await waitFor(() => expect(shown()).toBeNull());
  });
});
