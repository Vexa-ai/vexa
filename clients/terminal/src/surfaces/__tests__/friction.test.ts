/** "Report this" — the person's half (PRD decision 33 §2; #1510).
 *
 *  What matters here is not that a POST happens; it is WHAT TRAVELS WITH IT. The whole design claim
 *  is that a person types one line and the client attaches everything else — so these assert the
 *  attachment, and that a failure to send is never an exception thrown at somebody who was already
 *  telling us the product is broken.
 *
 *  #1510: the response shape dropped `known`/`recurrence` (agent-api's own store, and the
 *  dedup/status machine that produced them, is gone — see `shared/friction.py`'s module docstring)
 *  in favour of `{id, recorded}`, the same shape flows' own `POST /friction` returns.
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { confirmation, reportFriction, surfaceOf, type FrictionSurface } from "../frictionApi";

const ok = (body: unknown) => ({ ok: true, json: async () => body }) as unknown as Response;

afterEach(() => { vi.unstubAllGlobals(); });

function capture(body: unknown = { id: "fr_1", recorded: true }) {
  const calls: { url: string; init: RequestInit }[] = [];
  vi.stubGlobal("fetch", (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return Promise.resolve(ok(body));
  });
  return calls;
}

describe("surfaceOf", () => {
  it("reads the tab's params — the resolved view slot, never a label", () => {
    const s = surfaceOf("meet-104", { kind: "doc", params: { path: "kg/x.md", slug: "dna" } });
    expect(s).toMatchObject({ chat: "meet-104", chatKind: "doc", path: "kg/x.md", workspace: "dna" });
  });

  it("omits what it does not know rather than guessing", () => {
    const s = surfaceOf("main", null);
    expect(s.path).toBeUndefined();
    expect(s.workspace).toBeUndefined();
    expect(s.chat).toBe("main");
  });

  it("carries a meeting when the tab is one", () => {
    expect(surfaceOf("meet-104", { kind: "meeting", params: { meetingId: "104" } }).meeting).toBe("104");
  });
});

describe("reportFriction", () => {
  it("sends one line plus the surface the person never had to describe", async () => {
    const calls = capture();
    const surface: FrictionSurface = {
      chat: "meet-104", chatKind: "meeting", workspace: "dna", path: "kg/x.md",
      meeting: "104", at: "turn", quote: "here is the write-up",
    };
    const filed = await reportFriction("  it opened the wrong page  ", surface);
    expect(filed).toEqual({ id: "fr_1", recorded: true });
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("/api/friction");
    const body = JSON.parse(String(calls[0].init.body));
    expect(body.reporter).toBe("person");
    expect(body.happened).toBe("it opened the wrong page");     // trimmed, and it is the report
    expect(body.session).toBe("meet-104");
    expect(body.context).toMatchObject({ workspace: "dna", path: "kg/x.md", meeting_id: "104" });
    expect(body.context.surface).toMatchObject({ chat: "meet-104", at: "turn", quote: "here is the write-up" });
    // the person is never asked to classify — the server infers `kind` from the words
    expect(body.kind).toBeUndefined();
  });

  it("falls back to a non-empty session when no chat is open (#1510: the route refuses none)", async () => {
    const calls = capture();
    await reportFriction("this page is wrong", { at: "page", path: "kg/x.md" });
    const body = JSON.parse(String(calls[0].init.body));
    expect(body.session).toBe("terminal");
  });

  it("sends nothing for an empty line", async () => {
    const calls = capture();
    expect(await reportFriction("   ", { at: "page" })).toBeNull();
    expect(calls).toHaveLength(0);
  });

  it("never throws when the report itself cannot be sent", async () => {
    vi.stubGlobal("fetch", () => Promise.reject(new Error("offline")));
    await expect(reportFriction("broken", { at: "page" })).resolves.toBeNull();
    vi.stubGlobal("fetch", () => Promise.resolve({ ok: false, status: 500 } as Response));
    await expect(reportFriction("broken", { at: "page" })).resolves.toBeNull();
  });

  it("treats a missing recorded field as success (flows' own shape carries no such ambiguity)", async () => {
    capture({ id: "fr_2" });
    const filed = await reportFriction("broken", { at: "page" });
    expect(filed).toEqual({ id: "fr_2", recorded: true });
  });
});

describe("confirmation", () => {
  it("says the same thing on every successful file — there is no more count to report", () => {
    expect(confirmation({ id: "a", recorded: true })).toBe("Filed. Thank you.");
    expect(confirmation({ id: "a", recorded: false })).toBe("Filed. Thank you.");
    expect(confirmation(null)).toContain("Couldn't send");
  });
});
