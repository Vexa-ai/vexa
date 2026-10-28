/** PRD DECISION 30 — the terminal writes the human surface to the session record.
 *
 *  What the person is looking at should be a FACT the server holds, not narration the client
 *  staples to the front of their message every turn. These pin the three properties that make that
 *  trade safe, the ONE gate that flips both halves, and — the reason this file was rewritten — what
 *  the release may claim about the decision.
 *
 *  ⚠ THE PREVIOUS VERSION OF THIS FILE COULD NOT FAIL. Three of its seven tests short-circuited on
 *  `if (!SURFACE_RECORD_LIVE) { …; return; }` and one asserted the implementation against itself
 *  (`expect(promptCarriesActiveContext()).toBe(!SURFACE_RECORD_LIVE)`), so setting the flag to
 *  `true` left all seven green — and all 1110 client tests with them — while the PUT went to a
 *  route that does not exist and the agent lost the only narration it had. The gate is now a
 *  PARAMETER: every test below names the state it is testing, and both states are tested.
 */
import { describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
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

describe("what the release may claim about decision 30", () => {
  it("is NOT shipped — and this flag is the claim, so flipping it is a release decision", () => {
    // The server half does not exist: agent-api serves `GET /api/sessions` and
    // `GET /api/sessions/<id>/history`, and nothing under `/api/sessions/<id>/surface`. One line
    // flips two irreversible things at once — the PUT starts going to a 404, and the prompt DROPS
    // its "Active context" narration, which is today the agent's only knowledge of the open page.
    //
    // Before this may become `true`, in the SAME commit:
    //   1. `PUT`/`GET /api/sessions/<id>/surface` exists on agent-api and its field names match
    //      the `Surface` interface (the shape is pinned by "the wire shape" below);
    //   2. `readSurface` has a load-time caller — it has none today (review R-C17);
    //   3. this test moves with it, so the code and the claim cannot disagree unnoticed.
    expect(SURFACE_RECORD_LIVE).toBe(false);
  });

  it("keeps exactly ONE mechanism live, for either state of the gate", () => {
    // The narration and the record are two answers to one question. Running both would let the
    // agent be told two different things about the same screen; running neither is what shipping
    // the record switched off would have done. Both states are asserted so neither can drift.
    expect(promptCarriesActiveContext(true)).toBe(false);
    expect(promptCarriesActiveContext(false)).toBe(true);
    expect(promptCarriesActiveContext()).toBe(!SURFACE_RECORD_LIVE);   // the default IS the gate
  });
});

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
    const opts = { fetcher: fetcher as unknown as typeof fetch, debounceMs: 300, live: true };
    syncSurface("s1", SURFACE, opts);
    syncSurface("s1", live, opts);
    await vi.advanceTimersByTimeAsync(400);
    vi.useRealTimers();
    expect(fetcher).toHaveBeenCalledTimes(1);
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/sessions/s1/surface");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body)).view.path).toBe("z.md");
  });

  it("writes NOTHING while the record is off — the inert half of the same gate", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn(async () => new Response("{}", { status: 200 }));
    syncSurface("s3", SURFACE, { fetcher: fetcher as unknown as typeof fetch, debounceMs: 300, live: false });
    await vi.advanceTimersByTimeAsync(400);
    vi.useRealTimers();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("takes the module gate when the caller does not name one", async () => {
    // The default is not decoration: it is what MinutesShell and chat.tsx get. While the record is
    // not shipped this must be silent, and it must be silent BECAUSE of the flag, not by accident.
    vi.useFakeTimers();
    const fetcher = vi.fn(async () => new Response("{}", { status: 200 }));
    syncSurface("s4", SURFACE, { fetcher: fetcher as unknown as typeof fetch, debounceMs: 300 });
    await vi.advanceTimersByTimeAsync(400);
    vi.useRealTimers();
    expect(fetcher.mock.calls.length).toBe(SURFACE_RECORD_LIVE ? 1 : 0);
  });

  it("never throws when the write fails — a failed record must not reach the reader", async () => {
    vi.useFakeTimers();
    const boom = vi.fn(async () => { throw new Error("offline"); });
    expect(() => syncSurface("s2", SURFACE, { fetcher: boom as unknown as typeof fetch, debounceMs: 10, live: true }))
      .not.toThrow();
    await vi.advanceTimersByTimeAsync(50);
    vi.useRealTimers();
    expect(boom).toHaveBeenCalledTimes(1);   // it really did try; the throw was swallowed
  });
});

describe("readSurface — the server wins only when the local strip is empty", () => {
  it("returns null on any refusal rather than a half record", async () => {
    const no = async () => new Response("nope", { status: 404 });
    expect(await readSurface("s1", no as unknown as typeof fetch, true)).toBeNull();
  });

  it("returns null for a body that is not a surface", async () => {
    const bad = async () => new Response(JSON.stringify({ chat: { id: "x" } }), { status: 200 });
    expect(await readSurface("s1", bad as unknown as typeof fetch, true)).toBeNull();   // no `strip`
  });

  it("parses a real one when the record is live", async () => {
    const ok = async () => new Response(JSON.stringify(SURFACE), { status: 200 });
    const got = await readSurface("s1", ok as unknown as typeof fetch, true);
    expect(got?.strip.history[0].path).toBe("a.md");
    expect(got?.strip.pins[0].workspace).toBe("grp-showb");
  });

  it("does not even ask while the record is off", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify(SURFACE), { status: 200 }));
    expect(await readSurface("s1", fetcher as unknown as typeof fetch, false)).toBeNull();
    expect(fetcher).not.toHaveBeenCalled();
  });
});

/** THE SHELL MUST NOT DO THE WORK OF A RECORD NOBODY HOLDS.
 *
 *  READ THE SOURCE, on the precedent of `minutes/__tests__/noDefaults.test.ts`. The finding is that
 *  the effect *reads as wired*: it recomputed `orderHistory(pages)` and built the whole body on
 *  every change of chat, view, strip and navigator, and handed it to a `syncSurface` that dropped
 *  it on the floor. Nothing observable changes either way — which is exactly why no behavioural
 *  test could have caught it, and why it survived into a release review as a decision the PRD
 *  counted as shipped (R-C09).
 */
describe("MinutesShell — decision 30 is gated at the caller, not only inside the module", () => {
  const shell = readFileSync(join(__dirname, "..", "..", "minutes", "MinutesShell.tsx"), "utf8");

  it("reads the gate itself and returns before building a surface", () => {
    // ⚠ assembled, never written out as a `from "…"` literal: gate:isolation reads every quoted
    // specifier in the file as an import, and a regex that spells one out fails the whole push
    // (the same gate's own header records it doing this to three files' prose on 2026-09-02).
    const line = shell.split("\n").find((l) => l.startsWith("import ") && l.includes("surfaceSync"));
    expect(line).toBeDefined();
    expect(line).toContain("SURFACE_RECORD_LIVE");
    expect(shell).toContain("if (!SURFACE_RECORD_LIVE) return;");
  });

  it("keeps ONE spelling of the gate — the shell never re-declares or negates its own copy", () => {
    // the `RUNNERS`/`HARNESS_RUNNERS` rule one level down: two copies of one switch is how a
    // half-flipped decision ships.
    expect(shell).not.toMatch(/SURFACE_RECORD_LIVE\s*=[^=]/);
  });
});
