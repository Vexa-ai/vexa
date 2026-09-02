/** PRD DECISION 30 — the terminal writes the human surface to the session record.
 *
 *  What the person is looking at should be a FACT the server holds, not narration the client
 *  staples to the front of their message every turn. These pin the three properties that make that
 *  trade safe, and the flag that keeps both halves in step.
 */
import { describe, expect, it, vi } from "vitest";
import {
  promptCarriesActiveContext, readSurface, SURFACE_RECORD_LIVE, surfaceBody, syncSurface,
  type Surface,
} from "../surfaceSync";

const SURFACE: Surface = {
  chat: { id: "meet-97", kind: "meeting" },
  meeting: { id: "97", phase: "post" },
  view: { workspace: "acme", path: "drafts/prd.md", title: "prd" },
  strip: {
    history: [{ workspace: "", path: "a.md", title: "a", at: 1 }],
    pins: [{ workspace: "grp-showb", path: "README.md", title: "grp-showb" }],
  },
  navigator: { open: true, workspace: "acme" },
};

describe("the wire shape", () => {
  it("carries chat, meeting, view, strip and navigator — the whole surface, not just the strip", () => {
    const body = JSON.parse(surfaceBody(SURFACE));
    expect(Object.keys(body).sort()).toEqual(["chat", "meeting", "navigator", "strip", "view"]);
    expect(body.strip.pins[0]).toEqual({ workspace: "grp-showb", path: "README.md", title: "grp-showb" });
    expect(body.strip.history[0].at).toBe(1);
  });
});

describe("syncSurface", () => {
  it("coalesces a burst into ONE write, keeping the LAST state", async () => {
    // navigation is bursty — walking a folder touches several pages — and one PUT per change is
    // the write storm that turned a close-loop into 519 requests this morning.
    vi.useFakeTimers();
    const fetcher = vi.fn(async () => new Response("{}", { status: 200 }));
    const live = { ...SURFACE, view: { workspace: "", path: "z.md", title: "z" } };
    syncSurface("s1", SURFACE, { fetcher: fetcher as unknown as typeof fetch, debounceMs: 300 });
    syncSurface("s1", live, { fetcher: fetcher as unknown as typeof fetch, debounceMs: 300 });
    await vi.advanceTimersByTimeAsync(400);
    vi.useRealTimers();
    if (!SURFACE_RECORD_LIVE) { expect(fetcher).not.toHaveBeenCalled(); return; }
    expect(fetcher).toHaveBeenCalledTimes(1);
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/sessions/s1/surface");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body)).view.path).toBe("z.md");
  });

  it("is INERT until the route lands — and that is the same flag that keeps the prompt prefix", () => {
    // dropping the "Active context" narration before the server fact exists would leave the agent
    // knowing LESS than it does today. One flag flips both halves together.
    expect(promptCarriesActiveContext()).toBe(!SURFACE_RECORD_LIVE);
  });

  it("never throws when the write fails — a failed record must not reach the reader", async () => {
    vi.useFakeTimers();
    const boom = vi.fn(async () => { throw new Error("offline"); });
    expect(() => syncSurface("s2", SURFACE, { fetcher: boom as unknown as typeof fetch, debounceMs: 10 })).not.toThrow();
    await vi.advanceTimersByTimeAsync(50);
    vi.useRealTimers();
  });
});

describe("readSurface — the server wins only when the local strip is empty", () => {
  it("returns null on any refusal rather than a half record", async () => {
    const no = async () => new Response("nope", { status: 404 });
    expect(await readSurface("s1", no as unknown as typeof fetch)).toBeNull();
  });

  it("returns null for a body that is not a surface", async () => {
    const bad = async () => new Response(JSON.stringify({ chat: { id: "x" } }), { status: 200 });
    expect(await readSurface("s1", bad as unknown as typeof fetch)).toBeNull();   // no `strip`
  });

  it("parses a real one when the record is live", async () => {
    const ok = async () => new Response(JSON.stringify(SURFACE), { status: 200 });
    const got = await readSurface("s1", ok as unknown as typeof fetch);
    if (!SURFACE_RECORD_LIVE) { expect(got).toBeNull(); return; }
    expect(got?.strip.history[0].path).toBe("a.md");
  });
});
