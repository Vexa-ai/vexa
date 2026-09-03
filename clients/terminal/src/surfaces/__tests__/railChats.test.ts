/** The flat rail, tested at its function boundary — three decisions, all pure.
 *
 *  UNION   : stored chats ∪ live meetings-as-rows. A meeting nobody has opened still shows; a
 *            meeting with two chats shows twice, because the CHAT is the row, not the meeting.
 *  ORDER   : recency, max(last activity, meeting start), newest first — with running meetings on
 *            top, which is the half of the rule the formula alone does not produce.
 *  FILTER  : one chip. Default = what you touched + what is live or still ahead. All = everything,
 *            which is where never-touched auto-created chats live.
 *
 *  Plus the one-way migration off the dead project registry: a project's chats become flat chats
 *  that inherit its workspace set, and the old key is left on disk as the backup. */
import { beforeEach, describe, expect, it } from "vitest";
import type { MeetingMock } from "../meetingModel";
import {
  CHATS_KEY, PROJECTS_KEY, ORG_CHAT_ID, PERSONAL_CHAT_ID,
  artifactKey, chatForRow, loadChats, markTouched, meetingChatId, migrateProjects, pruneStale, railRows,
  visibleRows, whenShort, forgetHistory, touchHistory, withHome, stripForRecord, HISTORY_CAP,
  type Chat, type LegacyProject,
} from "../../minutes/chats";
import { T, maxPagesW } from "../../minutes/tokens";

const T0 = Date.UTC(2026, 8, 1, 12, 0, 0);          // a fixed "now" — nothing here reads the clock
const at = (mins: number) => new Date(T0 + mins * 60000).toISOString();

const meeting = (id: string, title: string, live_status: string, startMins: number) =>
  ({ id, title, status: live_status === "active" ? "live" : "past", live_status, native_id: `n-${id}`, start_time: at(startMins) } as unknown as MeetingMock);

const LIVE = meeting("m-live", "Standup — daily", "active", -12);
const UPCOMING = meeting("m-prep", "Acme — pricing review", "scheduled", 75);
const HELD = meeting("m-post", "Blue Light Card — discovery", "completed", -1500);

const chat = (over: Partial<Chat> & { id: string }): Chat => ({
  label: over.id, workspaces: ["personal", "_global"], artifacts: [],
  createdAt: T0, lastActivityAt: T0, ...over,
});

const labels = (rows: { label: string }[]) => rows.map((r) => r.label);

describe("railRows — stored chats UNION live meetings", () => {
  it("a meeting with no chat yet still shows, as a derived row", () => {
    const rows = railRows([], [HELD]);
    expect(rows).toHaveLength(1);
    expect(rows[0].chatId).toBeNull();
    expect(rows[0].meetingId).toBe("m-post");
    expect(rows[0].label).toBe("Blue Light Card");    // the title's own qualifier is dropped
  });

  it("a chat bound to a meeting REPLACES its derived row — one row, not two", () => {
    const rows = railRows([chat({ id: meetingChatId("m-post"), label: "Blue Light Card", meeting: "m-post" })], [HELD]);
    expect(rows).toHaveLength(1);
    expect(rows[0].chatId).toBe("meet-m-post");
  });

  it("two chats on ONE meeting are two rows — the chat is the row", () => {
    const rows = railRows([
      chat({ id: "meet-m-post", label: "Blue Light Card", meeting: "m-post", lastActivityAt: T0 }),
      chat({ id: "pchat-2", label: "BLC — security answers", meeting: "m-post", lastActivityAt: T0 - 60000 }),
    ], [HELD]);
    expect(rows).toHaveLength(2);
    expect(labels(rows)).toEqual(["Blue Light Card", "BLC — security answers"]);
  });

  it("a chat over no meeting is an ordinary row, and keeps its own workspaces", () => {
    const rows = railRows([chat({ id: "c-org", label: "Organisation · setup", workspaces: ["_global"] })], []);
    expect(rows[0].meetingId).toBeNull();
    expect(rows[0].workspaces).toEqual(["_global"]);
  });

  it("a chat whose meeting is not in the list degrades to a plain row rather than vanishing", () => {
    const rows = railRows([chat({ id: "meet-gone", label: "Old sync", meeting: "m-gone" })], [LIVE]);
    expect(labels(rows)).toContain("Old sync");
    expect(rows.find((r) => r.label === "Old sync")?.live).toBe(false);
  });
});

describe("railRows — recency, newest first, live on top", () => {
  it("the running meeting leads even though an upcoming one starts later", () => {
    expect(labels(railRows([], [UPCOMING, HELD, LIVE])))
      .toEqual(["Standup", "Acme", "Blue Light Card"]);
  });

  it("a chat sorts on max(last activity, meeting start) — activity can lift an old meeting", () => {
    const rows = railRows([
      chat({ id: "meet-m-post", label: "Blue Light Card", meeting: "m-post", lastActivityAt: T0 + 30 * 60000 }),
    ], [HELD, UPCOMING]);
    expect(labels(rows)).toEqual(["Acme", "Blue Light Card"]);   // upcoming (+75m) still later than +30m
    expect(rows[1].when).toBe(T0 + 30 * 60000);                  // …but the chat's activity won over the start
  });

  it("plain chats interleave with meetings by pure recency — there are no buckets", () => {
    const rows = railRows([
      chat({ id: "a", label: "yesterday", lastActivityAt: T0 - 24 * 60 * 60000 }),
      chat({ id: "b", label: "ten minutes ago", lastActivityAt: T0 - 10 * 60000 }),
    ], [HELD, UPCOMING]);
    expect(labels(rows)).toEqual(["Acme", "ten minutes ago", "yesterday", "Blue Light Card"]);
  });

  it("a meeting with no start time sorts last rather than to the top", () => {
    const noTime = { id: "m-x", title: "Undated", status: "past", live_status: "completed" } as unknown as MeetingMock;
    expect(labels(railRows([], [noTime, HELD]))).toEqual(["Blue Light Card", "Undated"]);
  });

  it("the label of a live row reads 'live' instead of a clock time", () => {
    expect(railRows([], [LIVE], T0)[0].whenLabel).toBe("live");
    expect(railRows([], [HELD], T0)[0].whenLabel).not.toBe("live");
  });
});

describe("whenShort — ONE token, because a name needs the room more than a timestamp does", () => {
  it("today is a clock, this week a weekday, further out a date", () => {
    expect(whenShort(T0 - 3 * 3600000, { now: T0 })).toMatch(/^\d{1,2}:\d{2}( ?[AP]M)?$/);
    expect(whenShort(T0 - 2 * 86400000, { now: T0 })).toMatch(/^[A-Za-z]{3}$/);
    expect(whenShort(T0 - 40 * 86400000, { now: T0 })).toMatch(/^\d{1,2} [A-Za-z]{3}$|^[A-Za-z]{3} \d{1,2}$/);
  });

  it("a different year carries the year, so an old chat is never mistaken for a recent one", () => {
    expect(whenShort(T0 - 400 * 86400000, { now: T0 })).toMatch(/2[45]/);
  });

  it("live wins over any timestamp, and an unknown time says nothing", () => {
    expect(whenShort(T0, { live: true, now: T0 })).toBe("live");
    expect(whenShort(0, { now: T0 })).toBe("");
  });

  it("a meeting row is labelled with the MEETING's time, not the chat's last activity", () => {
    const rows = railRows([chat({ id: "meet-m-post", label: "Blue Light Card", meeting: "m-post", lastActivityAt: T0 })], [HELD], T0);
    expect(rows[0].whenLabel).toBe(whenShort(Date.parse(at(-1500)), { now: T0 }));
    expect(rows[0].when).toBe(T0);          // …while the SORT still uses the later of the two
  });
});

describe("visibleRows — one chip: touched + live/upcoming, or everything", () => {
  const chats = [
    chat({ id: "main", label: "Personal", touched: true, lastActivityAt: T0 }),
    chat({ id: "auto-1", label: "Weekly digest", touched: false, lastActivityAt: T0 - 60000 }),
  ];
  const rows = railRows(chats, [LIVE, UPCOMING, HELD]);

  it("default hides a never-touched auto-created chat", () => {
    expect(labels(visibleRows(rows, false))).not.toContain("Weekly digest");
  });

  it("default keeps live and upcoming meetings even though nobody has touched them", () => {
    const shown = labels(visibleRows(rows, false));
    expect(shown).toContain("Standup");
    expect(shown).toContain("Acme");
  });

  it("default hides a HELD meeting nobody has touched — the archive is what All is for", () => {
    expect(labels(visibleRows(rows, false))).not.toContain("Blue Light Card");
  });

  it("All shows everything, in the same order", () => {
    expect(visibleRows(rows, true)).toEqual(rows);
    expect(labels(visibleRows(rows, true))).toEqual(labels(rows));
  });

  it("touching a chat is what makes it survive the default filter", () => {
    const touched = markTouched(chats, "auto-1", T0);
    expect(labels(visibleRows(railRows(touched, []), false))).toContain("Weekly digest");
  });

  it("markTouched leaves the array identical when the id is unknown", () => {
    expect(markTouched(chats, "nope")).toBe(chats);
  });

  it("the SELECTED row never vanishes under the reader, whatever the filter says", () => {
    const key = rows.find((r) => r.label === "Blue Light Card")!.key;
    expect(labels(visibleRows(rows, false, key))).toContain("Blue Light Card");
    expect(labels(visibleRows(rows, false, null))).not.toContain("Blue Light Card");
  });
});

describe("maxPagesW — the pages panel's range, one place both bounds meet", () => {
  it("wants 60% of the viewport", () => {
    expect(maxPagesW(2560)).toBe(1536);
  });

  it("but never squeezes the conversation below its floor — the cap binds on a SMALL window", () => {
    // F61 narrowed the rail 397 → 240, which gave 157px back: at 1440 the 60% want now FITS, where
    // it used to be clipped by the conversation floor. That is the founder's change paying off, so
    // the invariant is asserted where it still bites rather than deleted.
    expect(maxPagesW(1440)).toBe(Math.round(1440 * 0.6));
    expect(maxPagesW(1280)).toBe(1280 - T.railW - T.chatMin);
    expect(maxPagesW(1280)).toBeLessThan(1280 * 0.6);
  });

  it("never returns less than the minimum, however small the window", () => {
    expect(maxPagesW(600)).toBe(T.pagesMin);
    expect(maxPagesW(320)).toBe(T.pagesMin);
  });

  it("respects the absolute ceiling on a very wide screen", () => {
    expect(maxPagesW(6000)).toBe(T.pagesMax);
  });
});

describe("chatForRow — first open materialises a meeting's chat", () => {
  it("mints `meet-<meetingId>`, so the row lands on the session that meeting always had", () => {
    const row = railRows([], [HELD])[0];
    const c = chatForRow([], row, [HELD], T0);
    expect(c.id).toBe("meet-m-post");
    expect(c.meeting).toBe("m-post");
    expect(c.label).toBe("Blue Light Card");
  });

  it("opening is not touching — the materialised chat starts untouched", () => {
    const row = railRows([], [HELD])[0];
    expect(chatForRow([], row, [HELD], T0).touched).toBe(false);
  });

  it("an existing chat is returned as-is rather than re-minted", () => {
    const existing = chat({ id: "meet-m-post", label: "renamed by hand", meeting: "m-post", touched: true });
    const row = railRows([existing], [HELD])[0];
    expect(chatForRow([existing], row, [HELD], T0)).toBe(existing);
  });
});

describe("migrateProjects — the project registry flattens, one way", () => {
  const legacy: LegacyProject[] = [
    { id: "personal", name: "Personal", set: ["personal"], builtin: "personal", chats: [{ id: "pchat-a", label: "onboarding" }] },
    { id: "org", name: "Organisation", set: ["_global"], chats: [{ id: "org-setup", label: "setup" }] },
    { id: "proj-1", name: "Acme", set: ["acme-ws", "_global"], chats: [{ id: "pchat-b", label: "pricing" }, { id: "pchat-c", label: "security" }] },
  ];

  it("every project chat becomes a flat chat", () => {
    // …and ONLY the registry's own chats: the personal project's built-in "main" row used to be
    // reconstructed here, and is not any more (F34) — it was a row nobody made, and `pruneStale`
    // deletes that id on the very next line of `loadChats`.
    expect(migrateProjects(legacy, T0).map((c) => c.id))
      .toEqual(["pchat-a", "org-setup", "pchat-b", "pchat-c"]);
  });

  it("each chat inherits its project's set as its own workspaces", () => {
    const out = migrateProjects(legacy, T0);
    expect(out.find((c) => c.id === "pchat-b")?.workspaces).toEqual(["acme-ws", "_global"]);
    expect(out.find((c) => c.id === "org-setup")?.workspaces).toEqual(["_global"]);
  });

  it("the project NAME survives as a label qualifier, so three 'setup' chats stay distinguishable", () => {
    const out = migrateProjects(legacy, T0);
    expect(out.find((c) => c.id === "pchat-b")?.label).toBe("Acme · pricing");
    expect(out.find((c) => c.id === "pchat-a")?.label).toBe("onboarding");     // Personal needs no qualifier
  });

  it("plants no 'main' row of its own (F34)", () => {
    expect(migrateProjects(legacy, T0).some((c) => c.id === PERSONAL_CHAT_ID)).toBe(false);
  });

  it("migrated chats are touched — the old UI could not tell hand-made from auto-created", () => {
    expect(migrateProjects(legacy, T0).every((c) => c.touched)).toBe(true);
  });

  it("the registry's own order survives as the rail's newest-first order", () => {
    expect(labels(railRows(migrateProjects(legacy, T0), [])))
      .toEqual(["onboarding", "Organisation · setup", "Acme · pricing", "Acme · security"]);
  });

  it("a project with no set, and duplicate ids, do not produce broken rows", () => {
    const out = migrateProjects([
      { id: "x", name: "X", chats: [{ id: "dup", label: "one" }] },
      { id: "y", name: "Y", chats: [{ id: "dup", label: "two" }] },
    ], T0);
    expect(out).toHaveLength(1);
    expect(out[0].workspaces).toEqual(["personal", "_global"]);
  });

  it("an empty registry migrates to nothing", () => {
    expect(migrateProjects([], T0)).toEqual([]);
  });
});

describe("artifacts — the open tabs ARE the chat record", () => {
  it("a tab's identity is workspace + path, because README.md exists in every workspace", () => {
    expect(artifactKey({ path: "README.md" })).not.toBe(artifactKey({ path: "README.md", slug: "_global" }));
    expect(artifactKey({ path: "README.md", slug: "acme" })).toBe(artifactKey({ path: "README.md", slug: "acme" }));
  });

  it("a PINNED tab set survives a reload, focus included", () => {
    localStorage.clear();
    const tabs = [{ path: "kg/entities/meeting/m1.md", label: "Minutes", pinned: true },
                  { path: "README.md", slug: "_global", label: "_global", pinned: true }];
    localStorage.setItem(CHATS_KEY, JSON.stringify([
      // touched, because the load path prunes a chat nobody wrote in (F35) — a saved tab set only
      // exists on a chat somebody actually worked in.
      { id: "c1", label: "Acme", workspaces: ["personal", "_global"], artifacts: tabs, focus: artifactKey(tabs[1]), touched: true, createdAt: T0, lastActivityAt: T0 },
    ]));
    const c = loadChats(T0).find((x) => x.id === "c1")!;
    // the pins survive, in order and still pinned. They also gain an `at`: a record written before
    // the strip became a history bar has no stamp, and ordering needs one — so the migration gives
    // them their stored order as their history order rather than inventing a time.
    expect(c.artifacts.map((a) => a.path)).toEqual(tabs.map((t) => t.path));
    expect(c.artifacts.every((a) => a.pinned)).toBe(true);
    expect(c.artifacts.every((a) => typeof a.at === "number")).toBe(true);
    expect(c.focus).toBe("_global|README.md");
  });

  it("an UNPINNED tab set is ORDERED and capped on load, not deleted (decision 28 as amended)", () => {
    // The first ruling dropped them; the amendment reframed the strip as a HISTORY bar, so these
    // are history that was never ordered — kept, ordered, and capped. The page that was in FRONT
    // lands at the right edge, where the reader left it.
    localStorage.clear();
    const tabs = [{ path: "a.md", label: "a" }, { path: "b.md", label: "b" }, { path: "c.md", label: "c" }];
    localStorage.setItem(CHATS_KEY, JSON.stringify([
      { id: "c1", label: "Acme", workspaces: ["personal", "_global"], artifacts: tabs, focus: "|b.md", touched: true, createdAt: T0, lastActivityAt: T0 },
    ]));
    const c = loadChats(T0).find((x) => x.id === "c1")!;
    expect(c.artifacts.map((a) => a.path)).toEqual(["a.md", "c.md", "b.md"]);
    expect(c.view?.path).toBe("b.md");
  });  it("tolerates the earlier build's bare-string artifacts instead of rendering junk tabs", () => {
    localStorage.clear();
    localStorage.setItem(CHATS_KEY, JSON.stringify([
      { id: "c1", label: "Acme", workspaces: ["personal"], artifacts: ["README.md", null, 7], touched: true, createdAt: T0, lastActivityAt: T0 },
    ]));
    expect(loadChats(T0).find((x) => x.id === "c1")!.artifacts).toEqual([]);
  });

  it("a chat with no artifacts is the signal to fall back to the room's own pages", () => {
    localStorage.clear();
    localStorage.setItem(CHATS_KEY, JSON.stringify([
      { id: "c1", label: "Acme", workspaces: ["personal"], artifacts: [], touched: true, createdAt: T0, lastActivityAt: T0 },
    ]));
    expect(loadChats(T0).every((c) => c.artifacts.length === 0)).toBe(true);
  });

  it("a stored `scaffold` is ALL OR NOTHING — a kind with no record id is dropped (F37)", () => {
    localStorage.clear();
    localStorage.setItem(CHATS_KEY, JSON.stringify([
      { id: "good", label: "Setup", workspaces: ["_global"], artifacts: [], touched: true,
        scaffold: { kind: "admin-setup", id: "S1" }, createdAt: T0, lastActivityAt: T0 },
      { id: "half", label: "Setup", workspaces: ["_global"], artifacts: [], touched: true,
        scaffold: { kind: "admin-setup" }, createdAt: T0, lastActivityAt: T0 },
      // the shape an older build wrote: a bare kind, with no record anywhere. It is exactly what
      // let a PLANTED row wear the admin flavour, so it is not re-admitted.
      { id: "legacy", label: "Setup", workspaces: ["_global"], artifacts: [], touched: true,
        scaffoldKind: "admin-setup", createdAt: T0, lastActivityAt: T0 },
    ]));
    const out = loadChats(T0);
    expect(out.find((c) => c.id === "good")?.scaffold).toEqual({ kind: "admin-setup", id: "S1" });
    expect(out.find((c) => c.id === "half")?.scaffold).toBeUndefined();
    expect(out.find((c) => c.id === "legacy")?.scaffold).toBeUndefined();
  });
});

describe("loadChats — migrate exactly once, and never write the old key", () => {
  beforeEach(() => localStorage.clear());

  it("reads the legacy registry when the new key is absent, and writes the flat list", () => {
    localStorage.setItem(PROJECTS_KEY, JSON.stringify([
      { id: "personal", name: "Personal", set: ["personal"], builtin: "personal", chats: [] },
      { id: "proj-1", name: "Acme", set: ["acme-ws", "_global"], chats: [{ id: "pchat-b", label: "pricing" }] },
    ]));
    const out = loadChats(T0);
    expect(out.map((c) => c.id)).toContain("pchat-b");
    expect(JSON.parse(localStorage.getItem(CHATS_KEY)!).map((c: Chat) => c.id)).toContain("pchat-b");
  });

  it("leaves the old key exactly as it was — it is the backup", () => {
    const raw = JSON.stringify([{ id: "proj-1", name: "Acme", set: ["acme-ws"], chats: [{ id: "pchat-b", label: "pricing" }] }]);
    localStorage.setItem(PROJECTS_KEY, raw);
    loadChats(T0);
    expect(localStorage.getItem(PROJECTS_KEY)).toBe(raw);
  });

  it("does not migrate a second time — a chat deleted after migrating stays deleted", () => {
    localStorage.setItem(PROJECTS_KEY, JSON.stringify([{ id: "proj-1", name: "Acme", set: ["acme-ws"], chats: [{ id: "pchat-b", label: "pricing" }] }]));
    loadChats(T0);
    const kept = (JSON.parse(localStorage.getItem(CHATS_KEY)!) as Chat[]).filter((c) => c.id !== "pchat-b");
    localStorage.setItem(CHATS_KEY, JSON.stringify(kept));
    expect(loadChats(T0).map((c) => c.id)).not.toContain("pchat-b");
  });

  // ── THE RAIL PLANTS NOTHING (founder ruling 2026-09-02, F34) ────────────────────────────────
  //
  //  Two rows used to be seeded — "Personal" and "Organisation setup" — and a cached company-layer
  //  hint decided when. The founder opened his rail, found three chats he had never made and asked
  //  the only question that matters: "where is it coming from? i did not create this chat, and i do
  //  not like this text." So there is no seeding, no hint, and no timing question left to get wrong.

  it("a fresh browser gets an EMPTY rail — nothing is planted, whatever the instance's state", () => {
    expect(loadChats(T0)).toEqual([]);
    // and it stays empty however the old hint would have read: there is no longer a value that
    // could make a row appear.
    localStorage.setItem("vexa.companyLayer.v1", "completed");
    expect(loadChats(T0)).toEqual([]);
    localStorage.setItem("vexa.companyLayer.v1", "missing");
    expect(loadChats(T0)).toEqual([]);
  });

  it("a corrupt stored list reads as an empty rail instead of throwing", () => {
    localStorage.setItem(CHATS_KEY, "{not json");
    expect(loadChats(T0)).toEqual([]);
  });
});

/** F35 — THE PRUNE. Deleting the seeding stops new plants and does nothing about the rows already
 *  in the founder's localStorage, and "clear your site data" is not a fix to hand a founder. So the
 *  load path removes what should never have been written:
 *
 *    · the two PLANTED ids, by id — they were stored `touched: true` deliberately, so a generic
 *      "drop the untouched ones" rule does not catch them and never could have;
 *    · every chat with no human turn and no scaffold record — the `+` chats. He had two: one from
 *      12:05 and one from 12:19, neither typed in.
 *
 *  And the thing that must NOT happen: his real "setup global" chat, which has turns, survives. */
describe("pruneStale — the 2026-09-02 migration", () => {
  const planted = (id: string, label: string): Chat =>
    chat({ id, label, touched: true, createdAt: T0, lastActivityAt: T0 });
  /** What a `+` chat looks like once it has been persisted by the build that did persist them. */
  const plusChat = (id: string): Chat => chat({ id, label: "New chat", touched: false });
  /** His real one: a chat with turns, artifacts and a name he did not choose from a menu. */
  const REAL: Chat = chat({
    id: "askchat-mtjwoie7", label: "setup global", touched: true,
    workspaces: ["_global", "personal"],
    // pinned (decision 28): this fixture is about pruning PLANTED ROWS, and unpinned tabs would
    // now be collapsed on load — which is a different rule under test elsewhere.
    artifacts: [{ path: "README.md", slug: "_global", label: "README", pinned: true }, { path: "CHARTER.md", slug: "_global", label: "CHARTER", pinned: true }],
    focus: "_global|README.md",
  });
  /** A scaffolded arrival nobody has replied to yet — composed FOR someone, so it stays. */
  const SCAFFOLDED: Chat = chat({
    id: "scaffold-S1", label: "Setup", touched: false, scaffold: { kind: "admin-setup", id: "S1" },
  });

  it("drops the two planted ids even though both are stored as touched", () => {
    const out = pruneStale([planted(PERSONAL_CHAT_ID, "Personal"), planted(ORG_CHAT_ID, "Organisation setup"), REAL]);
    expect(out.map((c) => c.id)).toEqual([REAL.id]);
  });

  it("drops a `+` chat — no human turn and no scaffold behind it", () => {
    expect(pruneStale([plusChat("pchat-1205"), plusChat("pchat-1219"), REAL]).map((c) => c.id)).toEqual([REAL.id]);
  });

  it("KEEPS a real chat: turns, artifacts and focus all survive untouched", () => {
    const [kept] = pruneStale([REAL]);
    expect(kept).toEqual(REAL);
  });

  it("keeps a chat a SCAFFOLD composed, even before anyone has replied in it", () => {
    expect(pruneStale([SCAFFOLDED]).map((c) => c.id)).toEqual([SCAFFOLDED.id]);
  });

  it("is idempotent — running it again removes nothing more", () => {
    const all = [planted(PERSONAL_CHAT_ID, "Personal"), planted(ORG_CHAT_ID, "Organisation setup"),
                 plusChat("pchat-1205"), REAL, SCAFFOLDED];
    const once = pruneStale(all);
    expect(pruneStale(once)).toEqual(once);
    expect(pruneStale(pruneStale(once))).toEqual(once);
  });

  it("runs on load, so his rail is clean without him clearing storage", () => {
    localStorage.clear();
    localStorage.setItem(CHATS_KEY, JSON.stringify([
      planted(PERSONAL_CHAT_ID, "Personal"), planted(ORG_CHAT_ID, "Organisation setup"),
      plusChat("pchat-1205"), plusChat("pchat-1219"), REAL,
    ]));
    const out = loadChats(T0);
    expect(out.map((c) => c.id)).toEqual([REAL.id]);
    expect(out[0].label).toBe("setup global");
    expect(out[0].artifacts).toHaveLength(2);
  });
});

/** THE STRIP SURVIVES A RELOAD AS ITSELF — the persist→load round trip (decision 28).
 *
 *  The writer in MinutesShell mapped every entry to `pinned: true` and dropped `at` and `desk`.
 *  Three fields, and together they were the whole model: nothing could age out (the cap only evicts
 *  UNPINNED), the order was lost (`orderHistory` sorts on `at`), and the home stopped being the
 *  home. One literal nullified decisions 28, 28.4 and 28.5 — and nothing failed, because `Page` did
 *  not carry the fields, so dropping them was not a type error.
 *
 *  These drive the real `touchHistory` → store → `loadChats` path rather than the writer's literal,
 *  so they fail if the round trip loses any of the three however it is spelled.
 */
describe("a strip round-trips: plain pages age out, pins and the home stay", () => {
  const store = (chat: Partial<Chat>) => {
    localStorage.clear();
    localStorage.setItem(CHATS_KEY, JSON.stringify([{
      id: "c1", label: "C", workspaces: ["personal", "_global"], touched: true,
      createdAt: T0, lastActivityAt: T0, artifacts: [], ...chat,
    }]));
    return loadChats(T0).find((c) => c.id === "c1")!;
  };

  it("a PLAIN navigation lands unpinned — and therefore can age out", () => {
    let strip = withHome([], ["personal", "_global"]);
    strip = touchHistory(strip, { path: "a.md", label: "a" }, 10);
    const back = store({ artifacts: strip });
    const a = back.artifacts.find((x) => x.path === "a.md")!;
    expect(a.pinned).toBeFalsy();          // the regression made this `true`
    expect(a.at).toBe(10);                 // …and dropped this entirely
  });

  it("the CAP evicts the oldest plain page and keeps the pin and the home", () => {
    let strip = withHome([], ["personal", "_global"]);
    strip = touchHistory(strip, { path: "kept.md", label: "kept", pinned: true }, 1);
    for (let i = 2; i <= HISTORY_CAP + 3; i++) strip = touchHistory(strip, { path: `f${i}.md`, label: `f${i}` }, i);

    const back = store({ artifacts: strip });
    const paths = back.artifacts.map((x) => x.path);
    expect(back.artifacts[0].desk).toBe(true);                       // the home leads, still the home
    expect(paths).toContain("kept.md");                             // the pin survived the cap
    expect(back.artifacts.find((x) => x.path === "kept.md")!.pinned).toBe(true);
    expect(paths).not.toContain("f2.md");                           // the oldest plain page went
    expect(back.artifacts.filter((x) => !x.pinned && !x.desk)).toHaveLength(HISTORY_CAP);
  });

  it("the ORDER survives — oldest left, the page you were on at the right edge", () => {
    let strip = withHome([], ["personal", "_global"]);
    strip = touchHistory(strip, { path: "first.md", label: "first" }, 1);
    strip = touchHistory(strip, { path: "second.md", label: "second" }, 2);
    strip = touchHistory(strip, { path: "first.md", label: "first" }, 3);   // revisited → moves right

    const back = store({ artifacts: strip });
    expect(back.artifacts.map((x) => x.path)).toEqual(["README.md", "second.md", "first.md"]);
  });

  it("the home cannot be pinned away or forgotten by the round trip", () => {
    const strip = withHome([{ path: "a.md", label: "a", at: 5 }], ["personal", "_global"]);
    const back = store({ artifacts: strip });
    const home = back.artifacts.find((x) => x.desk)!;
    expect(home.path).toBe("README.md");
    expect(forgetHistory(back.artifacts, artifactKey(home)).some((x) => x.desk)).toBe(true);
  });
});

/** `stripForRecord` — what the persist writer stores. This is the line that carried the regression:
 *  it stamped `pinned: true` on every entry and dropped `at` and `desk`, so nothing could age out,
 *  the order was lost, and the home stopped being the home. It lived inside an effect, where no
 *  test could reach it; it is a named function now so a mutation of it fails here. */
describe("stripForRecord — a copy, never a re-decision", () => {
  it("preserves pinned, desk and at exactly as the strip holds them", () => {
    const strip = [
      { path: "README.md", label: "Desk", desk: true },
      { path: "kept.md", label: "kept", pinned: true, at: 1 },
      { path: "plain.md", label: "plain", at: 2 },
    ];
    expect(stripForRecord(strip)).toEqual(strip);
  });

  it("does NOT pin a plain page — the regression, pinned in place", () => {
    const [out] = stripForRecord([{ path: "plain.md", label: "plain", at: 7 }]);
    expect(out.pinned).toBeFalsy();
    expect(out.at).toBe(7);
    expect(out.desk).toBeFalsy();
  });

  it("keeps the home a home", () => {
    expect(stripForRecord([{ path: "README.md", label: "Desk", desk: true }])[0].desk).toBe(true);
  });
});
