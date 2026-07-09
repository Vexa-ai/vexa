/** Durable processed-notes hydration (the "notes vanish on bot stop" defect).
 *
 *  The copilot's notes are persisted by meeting-api's db-writer into the transcript response's
 *  `data.processed.views[]` (view id "copilot-notes", body `doc.notes`). The terminal used to render
 *  notes ONLY from the live SSE, so they vanished when the bot stopped. These tests pin the durable
 *  path: (a) extracting the copilot view from a response shaped like the REAL rc payload (fixture),
 *  (b) fetchDurableTranscript returning segments AND notes from one GET, and (c) the merge rule —
 *  live deltas over a hydrated seed update by note id, never duplicate.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { processedNotesOf, mergeNotesById, fetchDurableTranscript } from "../liveMeetings";
import fixture from "./fixtures/TranscriptResponse.processedViews.json";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("processedNotesOf", () => {
  it("extracts the copilot-notes view's notes from the real response shape", () => {
    const notes = processedNotesOf(fixture);
    expect(notes).toHaveLength(2);
    expect(notes[0]).toMatchObject({
      id: "seg-1",
      speaker: "Dmitry",
      chapter: "Kickoff",
      text: "I want to kick off with the Q3 roadmap.",
      t: 12.4,
      pass: 3,
      frozen: true,
    });
    expect(notes[1].id).toBe("seg-2");
  });

  it("ignores other views and returns [] when the copilot view is absent or malformed", () => {
    expect(processedNotesOf(undefined)).toEqual([]);
    expect(processedNotesOf({})).toEqual([]);
    expect(processedNotesOf({ data: { processed: { views: [] } } })).toEqual([]);
    expect(processedNotesOf({ data: { processed: { views: [{ id: "summary", doc: { notes: [{ id: "x", text: "y" }] } }] } } })).toEqual([]);
    expect(processedNotesOf({ data: { processed: { views: [{ id: "copilot-notes", doc: null }] } } })).toEqual([]);
  });

  it("drops notes without an id or text (the backend merge key / renderable body)", () => {
    const body = {
      data: { processed: { views: [{
        id: "copilot-notes",
        doc: { notes: [{ id: "", text: "no id" }, { id: "seg-9", text: "  " }, { id: "seg-10", text: "kept" }, "junk", null] },
      }] } },
    };
    const notes = processedNotesOf(body as never);
    expect(notes.map((n) => n.id)).toEqual(["seg-10"]);
  });
});

describe("mergeNotesById", () => {
  const seed = [
    { id: "seg-1", text: "persisted one" },
    { id: "seg-2", text: "persisted two" },
  ];

  it("live re-emit of a persisted note updates in place — no duplicate", () => {
    const live = [{ id: "seg-2", text: "refined two" }];
    const merged = mergeNotesById(seed, live);
    expect(merged.map((n) => n.id)).toEqual(["seg-1", "seg-2"]);
    expect(merged[1].text).toBe("refined two");
  });

  it("new live notes append after the seed in arrival order", () => {
    const live = [{ id: "seg-3", text: "new three" }, { id: "seg-1", text: "refined one" }];
    const merged = mergeNotesById(seed, live);
    expect(merged.map((n) => n.text)).toEqual(["refined one", "persisted two", "new three"]);
  });

  it("is the identity on either empty side (past meeting: seed only; fresh live: live only)", () => {
    expect(mergeNotesById(seed, [])).toEqual(seed);
    expect(mergeNotesById([], seed)).toEqual(seed);
  });
});

describe("fetchDurableTranscript", () => {
  it("returns transcript lines AND hydrated processed notes from one GET", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(fixture), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    // P0 (wrong-row hydration fix): fetch by the meetings-domain ROW id via /api/transcripts/by-id/{id},
    // not the native path (which resolves to the NEWEST row sharing a native).
    const { lines, notes } = await fetchDurableTranscript("42");
    expect(fetchMock).toHaveBeenCalledWith("/api/transcripts/by-id/42", { cache: "no-store" });
    expect(lines).toHaveLength(2);
    expect(lines[1]).toMatchObject({ speaker: "Anna", text: "Acme wants the SSO integration before renewal." });
    expect(notes).toHaveLength(2);
    expect(notes[0].text).toBe("I want to kick off with the Q3 roadmap.");
  });

  it("returns empties on HTTP error and on network failure", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("nope", { status: 500 })));
    expect(await fetchDurableTranscript("42")).toEqual({ lines: [], notes: [] });
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("offline"); }));
    expect(await fetchDurableTranscript("42")).toEqual({ lines: [], notes: [] });
  });
});
