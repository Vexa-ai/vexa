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
  id: "OU5hWbkhdKt9tI2ulYxn4h1Zh8yy3HFm1S9Vd5NalCI",
  kind: "invite-offer",
  who: "priya@acme.test",
  meeting: "97",
  native: "abc-defg-hij",
  phase: "post",
  workspaces: ["_global", "u_priya", "grp-showb"],
  refs: {
    title: "Show B Lighting dailies",
    when: "14:00 CEST",
    when_epoch: 1756814400,
    organizer: "leo@acme.test",
    participants: ["leo@acme.test", "priya@acme.test"],
    participant_names: { "priya@acme.test": "Priya N", "leo@acme.test": 7 },
    state: { desk: "new", group: "new" },
  },
  opening_preset: "minutes-review-invite",
  opening_label: "minutes",
  opening_text: "[minutes-review] Someone clicked through about 97 …\n\n[vexa-machinery] composed by the product …",
  tabs: ["meeting:note", "meeting:transcript"],
  focus: "meeting:note",
  header: { title: "Show B Lighting dailies", flavor: "meeting · held", when: "14:00 CEST" },
  provenance: { flow: "post_meeting", step: "email_attendees", reaction_id: "812", minted_by: "u_leo" },
  provenance_line: "post_meeting · 812 · minted by u_leo · 2026-09-02T10:33:26Z",
  minted_at: "2026-09-02T10:33:26Z",
  redeemed_at: "2026-09-02T10:33:26Z",
  redeemed_by: "u_priya",
};

describe("parseScaffold", () => {
  it("reads the record — including the two fields the server ships differently than first assumed", () => {
    const s = parseScaffold(WIRE)!;
    expect(s.id).toBe("OU5hWbkhdKt9tI2ulYxn4h1Zh8yy3HFm1S9Vd5NalCI");
    expect(s.meeting).toBe("97");
    // NATIVE is a top-level field of the record. It is NOT the row id, and the client no longer
    // hunts the meetings list for it — which is what let the `?s=` path drop its wait on that list.
    expect(s.native).toBe("abc-defg-hij");
    // PROVENANCE on the wire is the object; the string is `provenance_line`. Reading the object
    // through a string coercion degraded to "" silently, which is why this is asserted by value.
    expect(s.provenance).toBe("post_meeting · 812 · minted by u_leo · 2026-09-02T10:33:26Z");
    expect(s.phase).toBe("post");
    expect(s.workspaces).toEqual(["_global", "u_priya", "grp-showb"]);
    expect(s.refs.when).toBe("14:00 CEST");
    expect(s.refs.state).toEqual({ desk: "new", group: "new" });
    // a non-string name is dropped rather than rendered as "7"
    expect(s.refs.participantNames).toEqual({ "priya@acme.test": "Priya N" });
    expect(s.redeemedAt).toBe("2026-09-02T10:33:26Z");
  });

  it("ignores the fields it does not consume rather than choking on them", () => {
    // who · opening_label · header · minted_at · redeemed_by all ride along; the parse must not
    // care, so the server can add facts without a client release.
    expect(parseScaffold(WIRE)).not.toBeNull();
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
    const rec = scaffoldToChat(parseScaffold(WIRE)!);   // native comes off the RECORD now
    expect(rec.id).toBe("meet-97");        // not a parallel conversation
    expect(rec.label).toBe("");            // the rail names it from the meeting
    expect(rec.meeting).toBe("97");
    expect(rec.artifacts).toEqual([
      { path: "kg/entities/meeting/abc-defg-hij.md", label: "Minutes" },  // post → Minutes
      { kind: "meeting", path: "97", label: "Transcript" },
    ]);
    expect(rec.focus).toBe("|kg/entities/meeting/abc-defg-hij.md");
  });

  it("without a native the note is DROPPED, not guessed at", () => {
    // a tab pointing at a guessed path opens a page that can never load — worse than one fewer tab
    const rec = scaffoldToChat(parseScaffold({ ...WIRE, native: null })!);
    expect(rec.artifacts).toEqual([{ kind: "meeting", path: "97", label: "Transcript" }]);
  });

  it("phase null keeps the meeting's own layout and never infers `post`", () => {
    // null is a REAL answer — "we could not read the row" — not a default. Inferring post would
    // name an unheld meeting's brief "Minutes".
    const s = parseScaffold({ ...WIRE, phase: null })!;
    expect(s.phase).toBeNull();
    const rec = scaffoldToChat(s);
    expect(rec.artifacts[0]).toEqual({ path: "kg/entities/meeting/abc-defg-hij.md", label: "Brief" });
  });

  it("a scaffold with no meeting opens its own chat over its declared workspaces", () => {
    const s = parseScaffold({
      ...WIRE, meeting: null, kind: "admin-setup", opening_preset: "setup-global",
      workspaces: ["_global"], tabs: ["_global/README.md", "_global/MISSING.md"], focus: "_global/README.md",
      refs: { ...WIRE.refs, title: "" },
    })!;
    const rec = scaffoldToChat(s);
    expect(rec.id).toBe("scaffold-OU5hWbkhdKt9tI2ulYxn4h1Zh8yy3HFm1S9Vd5NalCI");
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
      preset: "prep", openingText: "[prep] …", meeting: "95", native: "abc-defg-hij", phase: "prep",
      workspaces: ["_global", "personal"], tabs: ["meeting:note"], focus: "meeting:note",
    });
    expect(sc.kind).toBe("hand-link");
    const rec = scaffoldToChat(sc);
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
    const r = await fetchScaffold(WIRE.id, async () => res(200, WIRE));
    expect(r.ok && r.scaffold.id).toBe(WIRE.id);
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
    // 404 is ALSO the answer for "not yours" — the id is the capability, so a 403 would confirm to
    // a prober that a scaffold with that id exists. The not-found copy therefore has to cover both
    // without asserting either, and must not claim the link was used up: reading one redeems it and
    // it keeps resolving for its recipient.
    const nf = refusalCopy({ reason: "not-found", status: 404, detail: "" });
    expect(nf.title + nf.body).toMatch(/different address|meant for/i);
    expect(nf.title + nf.body).not.toMatch(/already been used/i);
  });
});


/** F27 — a chat says what it IS; the header does not deduce it from mount arithmetic.
 *
 *  The flavour read `workspaces.filter(w => w !== "_global").length === 0 ? "chat · admin" : "chat"`.
 *  That was true only while the company-setup conversation mounted `_global` alone — and the
 *  two-scaffold ruling (the first chat writes the company layer AND the admin's own desk) made it
 *  mount both, at which point the instance's most consequential conversation announced itself as an
 *  ordinary personal chat. The kind travels on the record instead. */
describe("the scaffold kind reaches the chat", () => {
  const base = {
    id: "S1", kind: "admin-setup", who: "a@b.test", meeting: null, phase: null,
    workspaces: ["_global", "u_admin"], refs: {}, opening_preset: "setup-global",
    // a record with no opening text is refused by design — it would open an empty chat
    opening_text: "[setup-global] …", tabs: [], focus: "", redeemed_at: null,
  };

  it("carries `admin-setup` even though the chat mounts a desk beside _global", () => {
    const chat = scaffoldToChat(parseScaffold(base)!);
    expect(chat.scaffold.kind).toBe("admin-setup");
    // the arithmetic the old rule used would have called this an ordinary chat
    expect(chat.workspaces.filter((w) => w !== "_global").length).toBeGreaterThan(0);
  });

  it("carries the kind for every other arrival too, so nothing has to special-case one", () => {
    for (const kind of ["post-meeting", "prep", "invite-offer", "catch-up", "group-setup"]) {
      expect(scaffoldToChat(parseScaffold({ ...base, kind })!).scaffold.kind).toBe(kind);
    }
  });

  /** F37 — the kind NEVER travels alone. The founder saw an `admin-setup`-flavoured row that had no
   *  scaffold behind it (the rail had PLANTED it), so the header fell through to a pre-scaffold
   *  branch and offered a research step that does not exist: "I explain this as stale code."
   *  Pairing the id with the kind is what makes that shape unconstructible rather than merely
   *  never rendered — there is no way to say "admin-setup" without naming the record it came from. */
  it("the record ID travels with the kind — the pair is one field, never two", () => {
    const chat = scaffoldToChat(parseScaffold(base)!);
    expect(chat.scaffold).toEqual({ kind: "admin-setup", id: "S1" });
    // the type says `scaffold: { kind, id }`, not `scaffoldKind?: string`: a kind with no id is
    // not a value this function can return.
    expect(Object.keys(chat.scaffold).sort()).toEqual(["id", "kind"]);
  });
});
