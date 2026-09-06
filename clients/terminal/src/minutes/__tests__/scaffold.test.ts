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
import { fetchScaffold, parseScaffold, refusalCopy, scaffoldToChat } from "../scaffold";
import { touchHistory, type Artifact } from "../chats";

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
    note_path: "kg/entities/meeting/2026-03-02-show-b-lighting-dailies.md",
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
    // the chat's HOME leads the strip (decision 28.5); the declared pages follow in the preset's
    // own order, which is the author's reading order
    expect(rec.artifacts[0]).toMatchObject({ path: "README.md", desk: true });
    expect(rec.artifacts.slice(1)).toMatchObject([
      { path: "kg/entities/meeting/2026-03-02-show-b-lighting-dailies.md", label: "Minutes" },   // post → Minutes, at the path the SERVER named
      { kind: "meeting", path: "97", label: "Transcript" },
    ]);
    expect(rec.focus).toBe("|kg/entities/meeting/2026-03-02-show-b-lighting-dailies.md");
  });

  it("without a note_path the note is DROPPED, not guessed at", () => {
    // a tab pointing at a guessed path opens a page that can never load — worse than one fewer
    // tab. The guess used to be `kg/entities/meeting/<native>.md`, and it was ALWAYS wrong; the
    // path is now the server's answer or there is no tab (F55).
    const rec = scaffoldToChat(parseScaffold(
      { ...WIRE, refs: { ...WIRE.refs, note_path: undefined } })!);
    expect(rec.artifacts.filter((a) => !a.desk))
      .toMatchObject([{ kind: "meeting", path: "97", label: "Transcript" }]);
    // and the native alone cannot resurrect it — that is the whole point
    expect(parseScaffold({ ...WIRE, refs: { ...WIRE.refs, note_path: undefined } })!.native)
      .toBe("abc-defg-hij");
  });

  it("phase null keeps the meeting's own layout and never infers `post`", () => {
    // null is a REAL answer — "we could not read the row" — not a default. Inferring post would
    // name an unheld meeting's brief "Minutes".
    const s = parseScaffold({ ...WIRE, phase: null })!;
    expect(s.phase).toBeNull();
    const rec = scaffoldToChat(s);
    expect(rec.artifacts.filter((a) => !a.desk)[0])
      .toMatchObject({ path: "kg/entities/meeting/2026-03-02-show-b-lighting-dailies.md", label: "Brief" });
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
    // `_global/README.md` is declared AND is not the home (the chat mounts no group and no desk
    // slug), so the strip carries the desk README first and the declared pair after it
    expect(rec.artifacts.map((a) => a.path)).toEqual(["README.md", "README.md", "MISSING.md"]);
    expect(rec.artifacts[0]).toMatchObject({ desk: true, label: "Desk" });
    expect(rec.artifacts[0].slug).toBeUndefined();   // the reader's OWN desk carries no slug
    expect(rec.focus).toBe("_global|README.md");
  });

  it("empty workspaces fall back to a real mount set rather than nothing", () => {
    const rec = scaffoldToChat(parseScaffold({ id: "x", opening_text: "hi" })!);
    expect(rec.workspaces).toEqual(["_global", "personal"]);
  });
});

/*  The `localScaffold` block lived here and is deleted with the function (F97). It proved a hand
 *  link composed through the same path as an emailed one — true, and now achieved by minting
 *  server-side instead, which `noDefaults.test.ts` asserts and the agent-api route tests cover. */

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

describe("the scaffold DELIVERS the strip (decision 28.5)", () => {
  it("`tabs` accepts a bare token OR {token, pinned} — the frontmatter is a file a human edits", () => {
    // asking someone to write `{token: …, pinned: false}` for the common case would be the format
    // serving the parser rather than the founder
    const s = parseScaffold({ ...WIRE, tabs: ["meeting:transcript", { token: "meeting:note", pinned: true }] })!;
    expect(s.tabs).toEqual([
      { token: "meeting:transcript", pinned: false },
      { token: "meeting:note", pinned: true },
    ]);
  });

  it("a pinned entry arrives as a CHAT pin, at the left edge, after the home", () => {
    const s = parseScaffold({
      ...WIRE, workspaces: ["_global", "u_priya", "grp-showb"],
      tabs: [{ token: "meeting:note", pinned: true }, "meeting:transcript"],
    })!;
    const rec = scaffoldToChat(s);
    // home first (the chat mounts a group, so the GROUP's README, not the desk), then the pin,
    // then the rest in the preset's own order
    expect(rec.artifacts[0]).toMatchObject({ path: "README.md", slug: "grp-showb", desk: true });
    expect(rec.artifacts[1]).toMatchObject({ path: "kg/entities/meeting/2026-03-02-show-b-lighting-dailies.md", pinned: true });
    expect(rec.artifacts[2]).toMatchObject({ kind: "meeting", path: "97" });
  });

  it("a DECLARED tab is a pinned tab, bare token or not (founder ruling 2026-09-06)", () => {
    // *"no need to create tabs, unless there is a pinned tab"*: declaring one IS asking for it, so
    // the wire's `pinned` flag no longer decides anything. Without this, a bare-token tab arrives
    // unpinned and the reader's FIRST click evicts it from the single preview slot — the set the
    // scaffold composed disappearing without anybody closing it.
    const s = parseScaffold({ ...WIRE, tabs: ["meeting:note", "meeting:transcript"] })!;
    expect(scaffoldToChat(s).artifacts.filter((a) => !a.desk).every((a) => a.pinned)).toBe(true);
  });

  it("and it survives the reader browsing past it", () => {
    const s = parseScaffold({ ...WIRE, tabs: ["meeting:note", "meeting:transcript"] })!;
    let strip = scaffoldToChat(s).artifacts as Artifact[];
    for (const n of ["a", "b", "c"]) strip = touchHistory(strip, { path: `kg/${n}.md`, label: n }, 10);
    expect(strip.map((a) => a.path)).toEqual([
      "README.md",                                                   // the home
      "kg/entities/meeting/2026-03-02-show-b-lighting-dailies.md",   // …then both declared tabs
      "97",
      "kg/c.md",                                                     // …then the one preview slot
    ]);
  });

  it("a chat with no group is at home on the reader's own desk", () => {
    const s = parseScaffold({ ...WIRE, workspaces: ["_global", "u_priya"], tabs: [] })!;
    expect(scaffoldToChat(s).artifacts[0]).toMatchObject({ path: "README.md", label: "Desk", desk: true });
  });

  it("a scaffold that names no focus opens on the HOME, never on a blank panel", () => {
    const s = parseScaffold({ ...WIRE, focus: "", workspaces: ["_global", "grp-showb"], tabs: [] })!;
    expect(scaffoldToChat(s).focus).toBe("grp-showb|README.md");
  });

  it("junk entries are dropped, and a malformed tabs list is simply empty", () => {
    expect(parseScaffold({ ...WIRE, tabs: [1, null, { pinned: true }, "  "] })!.tabs).toEqual([]);
    expect(parseScaffold({ ...WIRE, tabs: "nope" })!.tabs).toEqual([]);
  });
});
