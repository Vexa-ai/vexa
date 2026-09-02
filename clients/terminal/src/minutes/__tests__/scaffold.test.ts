/** THE SCAFFOLD → the chat record (PRD §5.5 step 3).
 *
 *  One record per arrival, rendered by the terminal and consumed by the agent. These tests pin the
 *  three things that decide what a person sees when they click a link: what the wire is allowed to
 *  say, what that becomes as a chat, and what happens when it will not open.
 *
 *  The wire shape is owned by a separate worker's branch. `parseScaffold` is the ONLY place that
 *  knows it, so these tests are also the thing that will fail first — loudly and in one file — if
 *  the real interface differs from the expected one.
 */
import { describe, expect, it } from "vitest";
import { fetchScaffold, localScaffold, parseScaffold, refusalCopy, scaffoldToChat } from "../scaffold";

const WIRE = {
  id: "sc_abc123",
  kind: "post-meeting",
  meeting: 95,
  phase: "post",
  workspaces: ["_global", "personal"],
  refs: {
    title: "DNA TSC — 3 August",
    when: "Sep 2, 10:12",
    organizer: "dmitry@vexa.ai",
    participants: ["a@x.test", "b@x.test"],
    participant_names: { "a@x.test": "Ann X", "b@x.test": 7 },
    state: { desk: "new", group: "warm" },
  },
  opening_preset: "minutes-review",
  opening_text: "[minutes-review] Someone clicked through …",
  tabs: ["meeting:note", "meeting:transcript"],
  focus: "meeting:note",
  provenance: "post_meeting flow",
  redeemed_at: null,
};

describe("parseScaffold", () => {
  it("reads the record, and coerces the ROW id to a string", () => {
    const s = parseScaffold(WIRE)!;
    expect(s.id).toBe("sc_abc123");
    expect(s.meeting).toBe("95");           // number on the wire, string everywhere here
    expect(s.phase).toBe("post");
    expect(s.workspaces).toEqual(["_global", "personal"]);
    expect(s.refs.state).toEqual({ desk: "new", group: "warm" });
    // a non-string name is dropped rather than rendered as "7"
    expect(s.refs.participantNames).toEqual({ "a@x.test": "Ann X" });
  });

  it("refuses a record it cannot open, rather than opening an empty chat", () => {
    expect(parseScaffold({ ...WIRE, id: "" })).toBeNull();
    expect(parseScaffold({ ...WIRE, opening_text: "   " })).toBeNull();
    expect(parseScaffold(null)).toBeNull();
    expect(parseScaffold("nope")).toBeNull();
  });

  it("an unknown phase is NULL, never guessed", () => {
    // §5.5: phase is resolved server-side at OPEN. A client that guessed it would reintroduce the
    // exact lie the rule exists to stop — a "prep" link clicked after the meeting.
    expect(parseScaffold({ ...WIRE, phase: "whenever" })!.phase).toBeNull();
    expect(parseScaffold({ ...WIRE, phase: undefined })!.phase).toBeNull();
  });

  it("survives a record with everything optional missing", () => {
    const s = parseScaffold({ id: "x", opening_text: "hi" })!;
    expect(s.meeting).toBeNull();
    expect(s.workspaces).toEqual([]);
    expect(s.refs.participants).toEqual([]);
    expect(s.tabs).toEqual([]);
  });
});

describe("scaffoldToChat", () => {
  it("a meeting scaffold lands in the MEETING's own chat, with the phase's tab names", () => {
    const rec = scaffoldToChat(parseScaffold(WIRE)!, { native: "abc-defg-hij" });
    expect(rec.id).toBe("meet-95");        // not a parallel conversation
    expect(rec.label).toBe("");            // the rail names it from the meeting
    expect(rec.meeting).toBe("95");
    expect(rec.artifacts).toEqual([
      { path: "kg/entities/meeting/abc-defg-hij.md", label: "Minutes" },  // post → Minutes
      { kind: "meeting", path: "95", label: "Transcript" },
    ]);
    expect(rec.focus).toBe("|kg/entities/meeting/abc-defg-hij.md");
  });

  it("without the native id the note is DROPPED, not guessed at", () => {
    // a tab pointing at a guessed path opens a page that can never load — worse than one fewer tab
    const rec = scaffoldToChat(parseScaffold(WIRE)!, { native: null });
    expect(rec.artifacts).toEqual([{ kind: "meeting", path: "95", label: "Transcript" }]);
  });

  it("a scaffold with no meeting opens its own chat over its declared workspaces", () => {
    const s = parseScaffold({
      ...WIRE, meeting: null, kind: "admin-setup", opening_preset: "setup-global",
      workspaces: ["_global"], tabs: ["_global/README.md", "_global/MISSING.md"], focus: "_global/README.md",
      refs: { ...WIRE.refs, title: "" },
    })!;
    const rec = scaffoldToChat(s);
    expect(rec.id).toBe("scaffold-sc_abc123");
    expect(rec.label).toBe("setup global");
    expect(rec.meeting).toBeUndefined();
    expect(rec.artifacts.map((a) => a.path)).toEqual(["README.md", "MISSING.md"]);
    expect(rec.focus).toBe("_global|README.md");
  });

  it("empty workspaces fall back to a real mount set rather than nothing", () => {
    const rec = scaffoldToChat(parseScaffold({ id: "x", opening_text: "hi" })!);
    expect(rec.workspaces).toEqual(["_global", "personal"]);
  });
});

describe("localScaffold — the hand link composes through the SAME path", () => {
  it("`?ask=&meeting=` produces a record scaffoldToChat renders identically", () => {
    const sc = localScaffold({
      preset: "prep", openingText: "[prep] …", meeting: "95", phase: "prep",
      workspaces: ["_global", "personal"], tabs: ["meeting:note"], focus: "meeting:note",
    });
    expect(sc.kind).toBe("hand-link");
    const rec = scaffoldToChat(sc, { native: "abc-defg-hij" });
    expect(rec.id).toBe("meet-95");
    // prep → the same file under the name the reader needs today
    expect(rec.artifacts).toEqual([{ path: "kg/entities/meeting/abc-defg-hij.md", label: "Brief" }]);
  });
});

describe("fetchScaffold — a refusal is returned, never thrown", () => {
  const res = (status: number, body: unknown) => ({
    ok: status >= 200 && status < 300, status, json: async () => body,
  }) as unknown as Response;

  it("404 is not-found, 403 is forbidden, 500 is unavailable", async () => {
    const r404 = await fetchScaffold("x", async () => res(404, { detail: "no" }));
    const r403 = await fetchScaffold("x", async () => res(403, { detail: "not yours" }));
    const r500 = await fetchScaffold("x", async () => res(500, {}));
    expect(r404.ok === false && r404.refusal.reason).toBe("not-found");
    expect(r403.ok === false && r403.refusal.reason).toBe("forbidden");
    expect(r500.ok === false && r500.refusal.reason).toBe("unavailable");
  });

  it("a network fault is unavailable, not a crash", async () => {
    const r = await fetchScaffold("x", async () => { throw new Error("offline"); });
    expect(r.ok === false && r.refusal.reason).toBe("unavailable");
    expect(r.ok === false && r.refusal.detail).toBe("offline");
  });

  it("an id that is not an id never reaches the network", async () => {
    let called = false;
    const r = await fetchScaffold("../../etc/passwd", async () => { called = true; return res(200, WIRE); });
    expect(called).toBe(false);
    expect(r.ok === false && r.refusal.reason).toBe("malformed");
  });

  it("a 200 that is not a scaffold is malformed — not an empty chat", async () => {
    const r = await fetchScaffold("x", async () => res(200, { id: "x" }));   // no opening_text
    expect(r.ok === false && r.refusal.reason).toBe("malformed");
  });

  it("a good one parses", async () => {
    const r = await fetchScaffold("sc_abc123", async () => res(200, WIRE));
    expect(r.ok && r.scaffold.id).toBe("sc_abc123");
  });
});

describe("refusalCopy — every refusal states a state and a next move", () => {
  it("names whose the link is, and what to do, for each reason", () => {
    for (const reason of ["not-found", "forbidden", "unavailable", "malformed"] as const) {
      const c = refusalCopy({ reason, status: 0, detail: "" });
      expect(c.title.length).toBeGreaterThan(10);
      expect(c.body.length).toBeGreaterThan(10);
      // never a stack trace, never a status code in the reader's face
      expect(c.title + c.body).not.toMatch(/HTTP|undefined|null|\[object/);
    }
    expect(refusalCopy({ reason: "forbidden", status: 403, detail: "" }).title).toMatch(/someone else/i);
  });
});
