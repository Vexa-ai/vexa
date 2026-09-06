/** THE RAIL READS THE SERVER'S NAME FOR A ROW (Vexa-ai/vexa#1602).
 *
 *  The founder's rail, 2026-09-06 12:50Z, after #1591 derived it from the server's sessions. Four
 *  rows read `Active context: the u…`; beside them `[vexa-job:extend…`, `[minutes-review…` and
 *  `[prep] They click…`. A person never typed any of that — a row was labelled with the session's
 *  first user text, and the first user text of most sessions is machinery.
 *
 *  THE RULE LIVES ON THE SERVER, and that is the design, not an implementation detail: one rule
 *  computed once means the rail, a second window and anything else reading `/api/sessions` agree by
 *  construction rather than by three implementations of it. So what these pin is the CLIENT'S half
 *  — it reads `label`, it falls back safely when a server predates it, and it treats a machinery
 *  label already sitting in this browser's storage as the placeholder it is.
 *
 *  The server half is pinned in `core/agent/tests/test_rail_labels.py`.
 */
import { beforeEach, describe, expect, it } from "vitest";

import {
  chatsFromSessions, isMachineryLabel, isPlaceholderLabel, mergeChats, nameChat, newChat, railRows,
  type Chat, type ServerSession,
} from "../chats";
import type { MeetingMock } from "../../surfaces/meetingModel";

const NOW = Date.parse("2026-09-06T12:50:00Z");
const secs = (iso: string) => Date.parse(iso) / 1000;

/** the founder's rows, as the index held them: `_truncate_title(<composed prompt>)`, cut at 60 */
const STORED_ACTIVE = "Active context: the user is viewing the workspace file kg/e…";
const STORED_JOB = "[vexa-job:extend:personal/kg/entities/person/james-spadafo…";
const STORED_PREP = "[prep] They clicked through from a prepare email about **DN…";

const session = (over: Partial<ServerSession> & { session: string }): ServerSession => ({
  title: null, label: null, created: secs("2026-09-06T08:00:00Z"),
  last_active: secs("2026-09-06T09:00:00Z"),
  workspaces: null, scaffold: null, touched: true, ...over,
});

const chat = (over: Partial<Chat> & { id: string }): Chat => ({
  ...newChat("New chat", ["personal", "_global"], { id: over.id, now: NOW }), ...over,
});

beforeEach(() => {
  localStorage.clear();
});

// ── what a label is ──────────────────────────────────────────────────────────────────────────────

describe("never a bracket, never a mark, never Active context", () => {
  it("recognises the shapes the founder was shown, and only those", () => {
    for (const l of [STORED_ACTIVE, STORED_JOB, STORED_PREP, "[vexa-machinery] x", "[extend] …"]) {
      expect(isMachineryLabel(l)).toBe(true);
    }
    for (const l of ["welcome", "Workspace setup", "what's my company called?", "setup global",
                     "DNA TSC 2026-09-04", "Extend: personal/kg/entities/person/ada.md",
                     "what is [this] about?", ""]) {
      expect(isMachineryLabel(l)).toBe(false);
    }
  });

  it("makes machinery a THIRD placeholder — a name nobody chose", () => {
    expect(isPlaceholderLabel(STORED_ACTIVE)).toBe(true);
    expect(isPlaceholderLabel("New chat")).toBe(true);
    expect(isPlaceholderLabel("Pricing for Kaar")).toBe(false);
  });
});

// ── the derivation ───────────────────────────────────────────────────────────────────────────────

describe("a server session becomes a rail row", () => {
  it("takes the label the server computed, over the title it stored", () => {
    const [c] = chatsFromSessions([session({
      session: "pchat-prep", title: STORED_PREP, label: "prepare",
    })], NOW);
    expect(c.label).toBe("prepare");
  });

  it("falls back to the title when a server one release behind sends no label", () => {
    const [c] = chatsFromSessions([{ session: "pchat-x", title: "Pricing for Kaar" } as ServerSession], NOW);
    expect(c.label).toBe("Pricing for Kaar");
  });

  it("never promotes a machinery TITLE, even from a server that predates the rule", () => {
    const [c] = chatsFromSessions([{ session: "pchat-old", title: STORED_ACTIVE } as ServerSession], NOW);
    expect(c.label).toBe("Chat");
  });

  it("treats an empty label as no name — the placeholder is this client's word, not the server's", () => {
    const [c] = chatsFromSessions([session({ session: "pchat-ctx", title: STORED_ACTIVE, label: "" })], NOW);
    expect(c.label).toBe("Chat");
  });

  it("still leaves a meeting's own chat to be named by its meeting", () => {
    // `meet-<row>` is this client's naming and `railRows` reads the meeting's title, so the row
    // follows a rename instead of freezing whatever the server computed at the time (#1591/#1597).
    const [c] = chatsFromSessions([session({ session: "meet-42", title: "whatever", label: "DNA TSC" })], NOW);
    expect(c.label).toBe("");
    expect(c.meeting).toBe("42");
  });
});

// ── the merge, which is where a stale browser meets the fix ─────────────────────────────────────

describe("a stored label from before the rule", () => {
  it("loses to the name the server now sends", () => {
    const merged = mergeChats(
      [chat({ id: "pchat-job", label: STORED_JOB })],
      chatsFromSessions([session({ session: "pchat-job", title: STORED_JOB,
                                   label: "Extend: personal/kg/entities/person/james-spadafo…" })], NOW));
    expect(merged[0].label).toBe("Extend: personal/kg/entities/person/james-spadafo…");
  });

  it("does not cost a person their own rename", () => {
    const merged = mergeChats(
      [chat({ id: "pchat-named", label: "Pricing for Kaar" })],
      chatsFromSessions([session({ session: "pchat-named", title: "what did we decide?",
                                   label: "what did we decide?" })], NOW));
    expect(merged[0].label).toBe("Pricing for Kaar");
  });

  it("is replaced by the person's first sentence, like any other placeholder", () => {
    const named = nameChat(chat({ id: "pchat-ctx", label: STORED_ACTIVE }), "and what about pricing?");
    expect(named.label).toBe("and what about pricing?");
  });
});

// ── the surface he was looking at ────────────────────────────────────────────────────────────────

describe("the rail itself", () => {
  const meetings: MeetingMock[] = [];

  it("shows no machinery even for a purely local row nothing has repaired", () => {
    const [row] = railRows([chat({ id: "pchat-ctx", label: STORED_ACTIVE })], meetings, NOW);
    expect(row.label).toBe("Chat");
  });

  it("renders the founder's rail as names", () => {
    const rows = railRows(chatsFromSessions([
      session({ session: "pchat-ctx0", title: STORED_ACTIVE, label: "" }),
      session({ session: "pchat-job", title: STORED_JOB,
                label: "Extend: personal/kg/entities/person/james-spadafo…" }),
      session({ session: "pchat-min", title: "[minutes-review] Someone clicked…", label: "minutes" }),
      session({ session: "pchat-prep", title: STORED_PREP, label: "prepare" }),
      session({ session: "scaffold-SC1", title: "welcome", label: "welcome" }),
      session({ session: "pchat-ws", title: "Workspace setup", label: "Workspace setup" }),
      session({ session: "pchat-comp", title: "what's my company called?", label: "what's my company called?" }),
      session({ session: "scaffold-SC2", title: "setup global", label: "setup global" }),
    ], NOW), meetings, NOW);
    expect(rows.map((r) => r.label).sort()).toEqual([
      "Chat", "Extend: personal/kg/entities/person/james-spadafo…", "Workspace setup",
      "minutes", "prepare", "setup global", "welcome", "what's my company called?",
    ]);
    for (const r of rows) expect(isMachineryLabel(r.label)).toBe(false);
  });
});
