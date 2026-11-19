/** R1 2.4 — Schedule a call from a room. The room plans the call (POST /api/rooms/{s}/plan); nothing
 *  is sent: the creator adds it to their own calendar (.ics or the copied invite) on their own click. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ScheduleCallDialog, nextSlot, parseInvitees } from "../rooms/ScheduleCallDialog";

let fetchMock: ReturnType<typeof vi.fn>;
const okJson = (body: unknown, status = 200) => ({ ok: true, status, json: async () => body }) as unknown as Response;
const planned = {
  session_id: "s_1", meeting: { id: 501, data: { title: "Launch sync" } }, ics: "/meetings/501/ics",
  calendar_draft: { summary: "Launch sync", start: "2026-10-20T13:00:00Z", end: "2026-10-20T13:30:00Z", attendees: ["ines@example.com"], description: "" },
};
beforeEach(() => { fetchMock = vi.fn(async () => okJson(planned, 201)); globalThis.fetch = fetchMock as unknown as typeof fetch; });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("nextSlot / parseInvitees", () => {
  it("defaults to the next whole hour at least 30 minutes away", () => {
    expect(nextSlot(new Date(2026, 9, 20, 13, 10))).toBe("2026-10-20T14:00");
    expect(nextSlot(new Date(2026, 9, 20, 13, 45))).toBe("2026-10-20T15:00");
  });
  it("splits on commas, semicolons and spaces; dedupes; names what is not an address", () => {
    expect(parseInvitees("a@x.io, B@x.io;a@x.io  nope")).toEqual({ ok: ["a@x.io", "b@x.io"], bad: ["nope"] });
  });
});

describe("ScheduleCallDialog", () => {
  it("plans the call in the room with the room's name, a start and an end — and says nothing was sent", async () => {
    render(<ScheduleCallDialog session="s_1" label="Launch sync" now={new Date(2026, 9, 20, 13, 10)} onClose={() => {}} />);
    fireEvent.click(screen.getByRole("radio", { name: "45 min" }));
    fireEvent.change(screen.getByPlaceholderText("name@example.com, …"), { target: { value: "ines@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Plan call" }));
    expect(await screen.findByText(/Nothing has been sent/)).toBeTruthy();
    const [url, init] = fetchMock.mock.calls.at(-1)! as [string, RequestInit];
    expect(url).toBe("/api/rooms/s_1/plan");
    const body = JSON.parse(String(init.body));
    expect(body.title).toBe("Launch sync");
    expect(body.invitees).toEqual(["ines@example.com"]);
    expect(new Date(body.end).getTime() - new Date(body.start).getTime()).toBe(45 * 60_000);
    expect(new Date(body.start).getTime()).toBe(new Date(2026, 9, 20, 14, 0).getTime());
    expect(screen.getByRole("link", { name: "Download .ics" }).getAttribute("href")).toBe("/api/meetings/501/ics");
    expect(fetchMock).toHaveBeenCalledTimes(1);   // planning is the only request: no send
  });
  it("refuses a bad address before asking the server", () => {
    render(<ScheduleCallDialog session="s_1" label="Launch sync" onClose={() => {}} />);
    fireEvent.change(screen.getByPlaceholderText("name@example.com, …"), { target: { value: "ines" } });
    fireEvent.click(screen.getByRole("button", { name: "Plan call" }));
    expect(screen.getByRole("alert").textContent).toContain("Not an email address: ines");
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it("says why when the server refuses", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 503, json: async () => ({ detail: { refused: "meetings_not_deployed" } }) } as unknown as Response);
    render(<ScheduleCallDialog session="s_1" label="Launch sync" onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Plan call" }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
  });
});

describe("Send from my calendar (PLAN-8H 8.4, `calendar_write`)", () => {
  const plan = async () => {
    render(<ScheduleCallDialog session="s_1" label="Launch sync" now={new Date(2026, 9, 20, 13, 10)} onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Plan call" }));
    await screen.findByText(/Nothing has been sent/);
  };

  it("only the creator's click creates the event in their calendar, with the call and agenda links, and records it", async () => {
    await plan();
    expect(fetchMock).toHaveBeenCalledTimes(1);                          // planning sent nothing
    fetchMock.mockResolvedValueOnce(okJson({ status: "event_created", event_id: "evt1abc", html_link: "https://calendar.example/e" }));
    fetchMock.mockResolvedValueOnce(okJson({ session_id: "s_1", meeting: { id: 501, data: { calendar_event_ref: "evt1abc" } } }));
    fireEvent.click(screen.getByRole("button", { name: "Send from my calendar" }));
    expect(await screen.findByText(/Sent from your calendar/)).toBeTruthy();
    const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(url).toBe("/api/connections/calendar/event/create");
    const ev = JSON.parse(String(init.body));
    expect(ev.title).toBe("Launch sync");
    expect(ev.attendees).toEqual(["ines@example.com"]);
    expect(ev.notify).toBe(true);
    expect(ev.start.date_time).toBe("2026-10-20T13:00:00Z");
    expect(ev.description).toMatch(/\/r\/chat\/s_1/);                    // the room's call link
    expect(ev.request_id).toMatch(/^[a-zA-Z0-9-]{8,80}$/);
    const [rec, recInit] = fetchMock.mock.calls[2] as [string, RequestInit];
    expect(rec).toBe("/api/rooms/s_1/plan/501/calendar");
    expect(JSON.parse(String(recInit.body))).toEqual({ event_ref: "evt1abc" });
  });

  it("a refusal (policy, permission) says why, records nothing, and keeps .ics and Copy invite", async () => {
    await plan();
    fetchMock.mockResolvedValueOnce({ ok: false, status: 403, json: async () => ({ detail: { status: "refused", policy_key: "calendar_notify_attendees", message: "Your organisation does not let Vexa send invitations." } }) } as unknown as Response);
    fireEvent.click(screen.getByRole("button", { name: "Send from my calendar" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/not sent|invitations/i);
    expect(fetchMock).toHaveBeenCalledTimes(2);                          // no record call
    expect(screen.getByRole("link", { name: "Download .ics" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copy invite" })).toBeTruthy();
  });

  it("is not offered with calendar_write off", async () => {
    const { setFeatureOverride } = await import("../../platform/features");
    setFeatureOverride({ calendar_write: false });
    await plan();
    expect(screen.queryByRole("button", { name: "Send from my calendar" })).toBeNull();
    setFeatureOverride("all");
  });
  it("names the room and its workspace, and prefills the invite with the workspace's members (trimmable)", async () => {
    fetchMock.mockImplementation(async (url: string) => String(url).startsWith("/api/workspace/members")
      ? okJson({ members: [{ subject: "1", role: "owner", email: "ann@x.io" }, { subject: "2", role: "contributor", email: "bob@x.io" }, { subject: "3", role: "viewer" }] })
      : okJson(planned, 201));
    render(<ScheduleCallDialog session="s_1" label="Weekly" workspace="dna-1" workspaceName="ASWF DNA" onClose={() => {}} />);
    expect(screen.getByText("In Weekly · ASWF DNA")).toBeTruthy();
    const field = screen.getByPlaceholderText("name@example.com, …") as HTMLInputElement;
    await waitFor(() => expect(field.value).toBe("ann@x.io, bob@x.io"));
    fireEvent.change(field, { target: { value: "bob@x.io" } });                     // the creator trims
    fireEvent.click(screen.getByRole("button", { name: "Plan call" }));
    await screen.findByText(/Nothing has been sent/);
    const [, init] = fetchMock.mock.calls.at(-1)! as [string, RequestInit];
    expect(JSON.parse(String(init.body)).invitees).toEqual(["bob@x.io"]);
  });
});
