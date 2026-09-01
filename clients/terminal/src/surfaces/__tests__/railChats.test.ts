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
  CHATS_KEY, PROJECTS_KEY, ORG_CHAT_LABEL, PERSONAL_CHAT_ID,
  chatForRow, loadChats, markTouched, meetingChatId, migrateProjects, railRows, visibleRows, whenShort,
  type Chat, type LegacyProject,
} from "../../minutes/chats";

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
    const rows = railRows([chat({ id: "org-setup", label: ORG_CHAT_LABEL, workspaces: ["_global"] })], []);
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

describe("whenShort — ONE token, because the rail is 248px wide", () => {
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
    expect(migrateProjects(legacy, T0).map((c) => c.id))
      .toEqual([PERSONAL_CHAT_ID, "pchat-a", "org-setup", "pchat-b", "pchat-c"]);
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
    expect(out.find((c) => c.id === "org-setup")?.label).toBe(ORG_CHAT_LABEL);
  });

  it("the Personal project's built-in 'main' row is reconstructed — it was never in chats[]", () => {
    const out = migrateProjects(legacy, T0);
    expect(out[0].id).toBe(PERSONAL_CHAT_ID);
    expect(out[0].workspaces).toEqual(["personal"]);
  });

  it("migrated chats are touched — the old UI could not tell hand-made from auto-created", () => {
    expect(migrateProjects(legacy, T0).every((c) => c.touched)).toBe(true);
  });

  it("the registry's own order survives as the rail's newest-first order", () => {
    expect(labels(railRows(migrateProjects(legacy, T0), [])))
      .toEqual(["Personal", "onboarding", ORG_CHAT_LABEL, "Acme · pricing", "Acme · security"]);
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

  it("the two structural rows always exist, and are touched so the filter cannot hide admin", () => {
    const out = loadChats(T0);
    const org = out.find((c) => c.label === ORG_CHAT_LABEL);
    expect(out.find((c) => c.id === PERSONAL_CHAT_ID)?.touched).toBe(true);
    expect(org?.touched).toBe(true);
    expect(org?.workspaces).toEqual(["_global"]);
  });

  it("a corrupt stored list falls back to the seeds instead of throwing", () => {
    localStorage.setItem(CHATS_KEY, "{not json");
    expect(loadChats(T0).map((c) => c.id)).toEqual([PERSONAL_CHAT_ID, "org-setup"]);
  });
});
