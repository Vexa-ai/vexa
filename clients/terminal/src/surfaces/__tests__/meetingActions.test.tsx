/** Behavioral test for the dropdown ACTION→TRANSITION map (meeting.tsx `actionsFor`).
 *
 *  For each REAL status the row offers a specific action set, and each action fires EXACTLY ONE endpoint
 *  with the right method + body. We assert both the offered set and the fetch each `run()` performs.
 *  The `scheduled`-intent body uses the same flat `intent` PUT the producer (meeting-api) accepts.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { actionsFor } from "../meeting";
import type { MeetingMock } from "../meetingModel";

const NATIVE = "abc-defg-hij";

function row(live_status: string): MeetingMock {
  return {
    id: NATIVE,
    native_id: NATIVE,
    title: "Google Meet · " + NATIVE,
    when: "now",
    status: "past",
    live_status,
    platform: "Google Meet",
    has_recording: false,
    docs: [],
    participants: [],
    mentioned: [],
    actions: [],
    transcript: [],
    insights: [],
  } as MeetingMock;
}

let fetchMock: ReturnType<typeof vi.fn>;
function lastFetch() {
  const c = fetchMock.mock.calls.at(-1)!;
  return { url: String(c[0]), init: (c[1] ?? {}) as RequestInit, body: c[1]?.body ? JSON.parse(String(c[1].body)) : undefined };
}

beforeEach(() => {
  fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({}) }) as Response);
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});
afterEach(() => vi.restoreAllMocks());

describe("actionsFor — offered action sets per status", () => {
  const ids = (s: string) => actionsFor(row(s)).map((a) => a.id);

  it("idle → Schedule + Send now + Delete", () => expect(ids("idle")).toEqual(["schedule", "send", "delete"]));
  it("scheduled → Send now + Cancel + Delete", () => expect(ids("scheduled")).toEqual(["send", "cancel", "delete"]));
  it("link-less planned rows → row-id actions only (no native path exists)", () => {
    const linkless = (s: string) => actionsFor({ ...row(s), native_id: undefined, id: "42" });
    expect(linkless("idle").map((a) => a.id)).toEqual(["delete"]);
    expect(linkless("scheduled").map((a) => a.id)).toEqual(["cancel", "delete"]);
  });
  it("active → Stop only", () => expect(ids("active")).toEqual(["stop"]));
  it("joining/awaiting/needs_help/stopping → Stop only", () => {
    for (const s of ["requested", "joining", "awaiting_admission", "needs_help", "stopping"]) {
      expect(ids(s)).toEqual(["stop"]);
    }
  });
  it("completed/failed/stopped → Re-send", () => {
    for (const s of ["completed", "failed", "stopped"]) expect(ids(s)).toEqual(["resend"]);
  });
});

describe("actionsFor — each action fires the correct endpoint+body", () => {
  it("scheduled→Cancel PUTs intent:idle to the intent route", () => {
    actionsFor(row("scheduled")).find((a) => a.id === "cancel")!.run();
    const { url, init, body } = lastFetch();
    expect(url).toBe(`/api/meetings/google_meet/${NATIVE}/intent`);
    expect(init.method).toBe("PUT");
    expect(body).toEqual({ intent: "idle" });
  });

  it("idle→Send now POSTs the bot launch to the gateway-fronted /api/bots", () => {
    actionsFor(row("idle")).find((a) => a.id === "send")!.run();
    const { url, init, body } = lastFetch();
    expect(url).toBe("/api/bots");
    expect(init.method).toBe("POST");
    expect(body).toEqual({ platform: "google_meet", native_meeting_id: NATIVE, meeting_url: `https://meet.google.com/${NATIVE}`, bot_name: "Vexa" });
  });

  it("active→Stop DELETEs the bot by platform+native (the gateway /api/bots route)", () => {
    actionsFor(row("active")).find((a) => a.id === "stop")!.run();
    const { url, init } = lastFetch();
    expect(url).toBe(`/api/bots/google_meet/${NATIVE}`);
    expect(init.method).toBe("DELETE");
  });

  it("active→Stop uses the meeting's REAL platform (Teams), not a hardcoded google_meet", () => {
    actionsFor({ ...row("active"), platform: "teams" }).find((a) => a.id === "stop")!.run();
    const { url, init } = lastFetch();
    expect(url).toBe(`/api/bots/teams/${NATIVE}`);
    expect(init.method).toBe("DELETE");
  });

  it("active→Stop reports network failures instead of throwing — as user truth, raw on the console", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const onFailure = vi.fn();
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    await expect(actionsFor(row("active")).find((a) => a.id === "stop")!.run(onFailure)).resolves.toBeUndefined();

    // UI channel: the presented truth, never the fetch engine's message.
    expect(onFailure).toHaveBeenCalledWith({
      actionId: "stop",
      actionLabel: "Stop",
      native: NATIVE,
      message: "Couldn't reach the Vexa server — check that the stack is running.",
    });
    // Operator channel: the raw plumbing stays on the console (P18).
    expect(warn).toHaveBeenCalledWith("meeting action failed", expect.objectContaining({ actionId: "stop", message: "Failed to fetch" }));
  });

  it("a 404 on Stop yields the HUMAN no-longer-active message + a reconciling re-snapshot — never the raw JSON body (issue #674)", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const onFailure = vi.fn();
    fetchMock.mockResolvedValueOnce({
      ok: false, status: 404, statusText: "Not Found", url: `/api/bots/google_meet/${NATIVE}`,
      json: async () => ({ detail: "No active meeting found for this bot in google_meet with ID abc-defg-hij" }),
    } as unknown as Response);

    await actionsFor(row("active")).find((a) => a.id === "stop")!.run(onFailure);

    const { message } = onFailure.mock.calls[0][0];
    expect(message).toBe("This meeting is no longer active — refreshing the list.");
    expect(message).not.toContain("404");
    expect(message).not.toContain("{");
    // reconcile: the finally-path re-snapshot was requested so the control self-corrects
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("/api/meetings"))).toBe(true);
    // the raw upstream detail is preserved on the operator channel
    expect(warn).toHaveBeenCalledWith("meeting action failed", expect.objectContaining({ message: expect.stringContaining("No active meeting found") }));
  });

  it("a 409 on Send maps to the human already-has-a-bot line", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const onFailure = vi.fn();
    fetchMock.mockResolvedValueOnce({
      ok: false, status: 409, statusText: "Conflict", url: "/api/bots",
      json: async () => ({ detail: "An active or requested meeting already exists" }),
    } as unknown as Response);

    await actionsFor(row("idle")).find((a) => a.id === "send")!.run(onFailure);

    expect(onFailure.mock.calls[0][0].message).toBe("That meeting already has a bot.");
  });

  it("idle→Schedule PUTs intent:scheduled with an ISO `at`", () => {
    const at = "2026-06-25T18:00:00.000Z";
    vi.spyOn(window, "prompt").mockReturnValue("2026-06-25 18:00");
    vi.spyOn(Date.prototype, "toISOString").mockReturnValue(at);
    actionsFor(row("idle")).find((a) => a.id === "schedule")!.run();
    const { url, init, body } = lastFetch();
    expect(url).toBe(`/api/meetings/google_meet/${NATIVE}/intent`);
    expect(init.method).toBe("PUT");
    expect(body.intent).toBe("scheduled");
    expect(body.at).toBe(at);
  });

  it("completed→Re-send POSTs the bot launch", () => {
    actionsFor(row("completed")).find((a) => a.id === "resend")!.run();
    const { url } = lastFetch();
    expect(url).toBe("/api/bots");
  });

  it("planned→Delete DELETEs by ROW id (works link-less)", () => {
    actionsFor({ ...row("idle"), native_id: undefined, id: "42" }).find((a) => a.id === "delete")!.run();
    const { url, init } = lastFetch();
    expect(url).toBe("/api/meetings/42");
    expect(init.method).toBe("DELETE");
  });

  it("link-less scheduled→Cancel PATCHes scheduled_at:null by ROW id", () => {
    actionsFor({ ...row("scheduled"), native_id: undefined, id: "42" }).find((a) => a.id === "cancel")!.run();
    const { url, init, body } = lastFetch();
    expect(url).toBe("/api/meetings/42");
    expect(init.method).toBe("PATCH");
    expect(body).toEqual({ scheduled_at: null });
  });

  it("send uses the row's REAL meeting_url when present (zoom/teams need it)", () => {
    actionsFor({ ...row("scheduled"), platform: "zoom", native_id: "1234567890", meeting_url: "https://us02web.zoom.us/j/1234567890?pwd=x" })
      .find((a) => a.id === "send")!.run();
    const { url, body } = lastFetch();
    expect(url).toBe("/api/bots");
    expect(body.platform).toBe("zoom");
    expect(body.meeting_url).toBe("https://us02web.zoom.us/j/1234567890?pwd=x");
  });
});
