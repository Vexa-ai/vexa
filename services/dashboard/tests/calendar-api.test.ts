import { afterEach, describe, expect, it, vi } from "vitest";
import { calendarAPI } from "../src/lib/calendar-api";

afterEach(() => vi.unstubAllGlobals());

describe("calendarAPI", () => {
  it("creates a named feed through the authenticated same-origin proxy", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id: "work-1", name: "Work", ics_url_set: true,
      ics_url_masked: "calendar.google.com/….ics", auto_join: true, enabled: true,
    }), { status: 201, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await calendarAPI.create({ name: "Work", ics_url: "https://secret.example/work.ics", auto_join: true });
    expect(fetchMock).toHaveBeenCalledWith("/api/vexa/user/calendars", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ name: "Work", ics_url: "https://secret.example/work.ics", auto_join: true }),
    }));
  });

  it("disconnects one connection and syncs by opaque connection id", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        last_sync: "2026-08-14T10:30:00Z", last_error: null,
      }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await calendarAPI.disconnect("work/1");
    await calendarAPI.sync("work/1");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/vexa/user/calendars/work%2F1");
    expect(fetchMock.mock.calls[0][1]).toEqual(expect.objectContaining({ method: "DELETE" }));
    expect(fetchMock.mock.calls[1][0]).toBe("/api/vexa/user/calendars/work%2F1/sync");
  });

  it("surfaces the backend's safe validation message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: "ics_url must be an http(s) URL" }),
      { status: 422, headers: { "Content-Type": "application/json" } },
    )));
    await expect(calendarAPI.create({ name: "Bad", ics_url: "file:///secret", auto_join: true }))
      .rejects.toThrow("ics_url must be an http(s) URL");
  });
});
