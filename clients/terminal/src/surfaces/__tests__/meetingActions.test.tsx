/** Behavioral test for the dropdown ACTION→TRANSITION map (meeting.tsx `actionsFor`).
 *
 *  For each REAL status the row offers a specific action set, and each action fires EXACTLY ONE endpoint
 *  with the right method + body. We assert both the offered set and the fetch each `run()` performs.
 *  The `scheduled`-intent body uses the same flat `intent` PUT the producer (meeting-api) accepts.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { actionsFor } from "../meeting";
import type { MeetingMock } from "../mock";

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

  it("idle → Schedule + Send now", () => expect(ids("idle")).toEqual(["schedule", "send"]));
  it("scheduled → Send now + Cancel", () => expect(ids("scheduled")).toEqual(["send", "cancel"]));
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

  it("idle→Send now POSTs the bot launch", () => {
    actionsFor(row("idle")).find((a) => a.id === "send")!.run();
    const { url, init, body } = lastFetch();
    expect(url).toBe("/api/meeting/bot");
    expect(init.method).toBe("POST");
    expect(body).toEqual({ url: `https://meet.google.com/${NATIVE}` });
  });

  it("active→Stop POSTs the stop route with native+platform", () => {
    actionsFor(row("active")).find((a) => a.id === "stop")!.run();
    const { url, init, body } = lastFetch();
    expect(url).toBe("/api/meeting/stop");
    expect(init.method).toBe("POST");
    expect(body).toEqual({ native_id: NATIVE, platform: "google_meet" });
  });

  it("active→Stop reports network failures instead of throwing", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const onFailure = vi.fn();
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    await expect(actionsFor(row("active")).find((a) => a.id === "stop")!.run(onFailure)).resolves.toBeUndefined();

    expect(onFailure).toHaveBeenCalledWith({
      actionId: "stop",
      actionLabel: "Stop",
      native: NATIVE,
      message: "Failed to fetch",
    });
    expect(warn).toHaveBeenCalledWith("meeting action failed", expect.objectContaining({ actionId: "stop", message: "Failed to fetch" }));
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
    expect(url).toBe("/api/meeting/bot");
  });
});
