/** PIN, HIDE AND DELETE on the rail (founder, 2026-10-11): *"need pinning here to keep on top and
 *  hide + delete … Also new chats should appear on top"*, then *"delete chat is not working"*.
 *  The server half is `core/agent/tests/test_rail_marks.py` and `test_chat_delete.py`. */
import { describe, expect, it, vi } from "vitest";

import { orderedRows } from "../chatOrder";
import { newChat, railRows, removeChat, visibleRows } from "../chats";
import { applyMarks, fetchMarks, markRow } from "../railMarks";
import type { MeetingMock } from "../../surfaces/meetingModel";

const NOW = Date.UTC(2026, 9, 11, 14, 0, 0);
const HELD = { id: "42", title: "Weekly sync", status: "past", live_status: "completed", native_id: "n-42",
  start_time: new Date(NOW - 2 * 86400000).toISOString() } as unknown as MeetingMock;

describe("delete", () => {
  it("deleting a meeting's chat used to leave the same row behind — the meeting's own", () => {
    // What the founder saw: the chat went, and the meeting came straight back as a derived row with
    // the same name and the same "held" tag. Removing the chat alone cannot remove that row.
    const chats = [{ ...newChat("", ["personal"], { id: "meet-42", meeting: "42", touched: true, now: NOW - 3600000 }) }];
    const before = railRows(chats, [HELD], NOW);
    const after = railRows(removeChat(chats, "meet-42"), [HELD], NOW);
    expect(before.map((r) => [r.key, r.label])).toEqual([["c:meet-42", "Weekly sync"]]);
    expect(after.map((r) => [r.key, r.label])).toEqual([["m:42", "Weekly sync"]]);
    // The server's delete hides `m:42` for this person (`hidden_meeting`), so the row leaves the list
    // and is listed only in the Hidden view.
    const marked = applyMarks(after, { pinned: [], hidden: ["m:42"] });
    expect(visibleRows(marked, true)).toEqual([]);
    expect(visibleRows(marked, false, null, true).map((r) => r.key)).toEqual(["m:42"]);
  });
});

describe("pin and hide", () => {
  it("a pinned row shows even under the default filter; a hidden one shows under neither", () => {
    const rows = applyMarks(railRows([], [HELD], NOW), { pinned: ["m:42"], hidden: [] });
    expect(visibleRows(rows, false).map((r) => r.key)).toEqual(["m:42"]);
    const hidden = applyMarks(rows, { pinned: ["m:42"], hidden: ["m:42"] });
    expect(visibleRows(hidden, false)).toEqual([]);
    expect(visibleRows(hidden, true)).toEqual([]);
  });

  it("a new chat sits under the pins and over everything older", () => {
    const chats = [
      newChat("Review email inbox", ["personal"], { id: "today", touched: true, now: NOW - 60000 }),
      newChat("Saturday", ["personal"], { id: "sat", touched: true, now: NOW - 2 * 86400000 }),
      newChat("Pinned old", ["personal"], { id: "old", touched: true, now: NOW - 9 * 86400000 }),
    ];
    const rows = applyMarks(railRows(chats, [], NOW), { pinned: ["c:old"], hidden: [] });
    expect(orderedRows(rows, ["c:sat", "c:old"], new Set(["c:old"])).map((r) => r.key)).toEqual(["c:old", "c:today", "c:sat"]);
  });

  it("reads and writes through the server, and a refusal throws so the old state stays", async () => {
    const ok = vi.fn(async () => new Response(JSON.stringify({ pinned: ["c:a"], hidden: [] }), { status: 200 }));
    expect(await markRow("c:a", { pinned: true }, ok as unknown as typeof fetch)).toEqual({ pinned: ["c:a"], hidden: [] });
    expect(ok).toHaveBeenCalledWith("/api/chat/marks", expect.objectContaining({ method: "POST", body: JSON.stringify({ row: "c:a", pinned: true }) }));
    expect(await fetchMarks(ok as unknown as typeof fetch)).toEqual({ pinned: ["c:a"], hidden: [] });
    const no = vi.fn(async () => new Response("{}", { status: 404 }));
    await expect(markRow("c:b", { hidden: true }, no as unknown as typeof fetch)).rejects.toThrow("404");
  });
});
