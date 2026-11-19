/** ADR-0048 — Enter on a message that @-mentions someone not in the chat asks once; the answer adds
 *  them through the participants API, into the new shared chat a private one becomes. */
import { describe, it, expect, vi } from "vitest";
import { ApiError } from "../../data/apiClient";
import { checkShare, shareWith } from "../shareFlow";
import { shareLines } from "../MentionUI";

const anna = { subject: "u_anna", label: "Anna", name: "Anna Berg" };
const preview = { session_id: "s-p", forks: true, routine: null, private_reads: [], participants: 1 };

function api(over: Record<string, unknown> = {}) {
  return {
    listParticipants: vi.fn().mockResolvedValue({ session_id: "s-p", you: "me", role: "owner",
      participants: [{ principal: "me", kind: "human", role: "owner" }] }),
    sharePreview: vi.fn().mockResolvedValue(preview),
    addParticipant: vi.fn().mockResolvedValue({ session_id: "s-new", participants: [] }),
    ...over,
  } as never;
}

describe("share on Enter", () => {
  it("no mention, or everyone already here: the message just goes", async () => {
    expect(await checkShare("pchat", [], api())).toEqual({ kind: "send" });
    const here = api({ listParticipants: vi.fn().mockResolvedValue({ session_id: "s-1", role: "owner",
      participants: [{ principal: "me" }, { principal: "u_anna" }] }) });
    expect((await checkShare("s-1", [anna], here)).kind).toBe("send");
  });

  it("a mention of someone not here asks, with what they will and will not see", async () => {
    const c = await checkShare("pchat", [anna], api());
    expect(c.kind).toBe("confirm");
    if (c.kind !== "confirm") return;
    const { title, lines } = shareLines(c.people, c.owner, c.preview);
    expect(title).toBe("Share this chat with Anna Berg?");
    expect(lines.join(" ")).toMatch(/joins this chat and sees what was said here/);
    expect(lines.join(" ")).toMatch(/Or start a new chat with them/);
    expect(lines.join(" ")).toMatch(/not your workspaces/);
    expect(lines.join(" ")).toMatch(/answers when mentioned \(@vexa\)/);
  });

  it("a routine that reads the owner's mail says so before sharing", () => {
    const { lines } = shareLines([anna], true, { ...preview, routine: { name: "inbox" }, private_reads: ["Gmail"] });
    expect(lines).toContain("This routine reads your Gmail. Shared, it can't — its runs would only use what everyone here can see.");
    expect(lines.join(" ")).toMatch(/only you pause, edit or delete it/);
  });

  it("a member who is not the owner is told only the owner adds", async () => {
    const c = await checkShare("s-1", [anna], api({ listParticipants: vi.fn().mockResolvedValue({
      session_id: "s-1", role: "member", participants: [{ principal: "o" }, { principal: "me" }] }) }));
    expect(c.kind === "confirm" && c.owner).toBe(false);
    if (c.kind === "confirm") expect(shareLines(c.people, false, null).lines[0]).toMatch(/Only the chat's owner/);
  });

  it("no session store: nothing to share with, the message goes", async () => {
    const c = await checkShare("pchat", [anna], api({ listParticipants: vi.fn().mockRejectedValue(new ApiError(503, "x", "/")) }));
    expect(c.kind).toBe("send");
  });

  it("the owner shares a private chat with its history, in place", async () => {
    const a = api({ addParticipant: vi.fn().mockResolvedValue({ session_id: "pchat", participants: [] }) });
    const two = { subject: "u_joe", label: "joe", name: "joe" };
    expect(await shareWith("pchat", [anna, two], "all", a)).toBe("pchat");
    const calls = (a as unknown as { addParticipant: { mock: { calls: unknown[][] } } }).addParticipant.mock.calls;
    expect(calls).toEqual([["pchat", { subject: "u_anna", history: "all" }], ["pchat", { subject: "u_joe" }]]);
  });

  it("…or starts a new chat with them, and every later add goes there", async () => {
    const a = api();
    const two = { subject: "u_joe", label: "joe", name: "joe" };
    expect(await shareWith("pchat", [anna, two], "none", a)).toBe("s-new");
    const calls = (a as unknown as { addParticipant: { mock: { calls: unknown[][] } } }).addParticipant.mock.calls;
    expect(calls).toEqual([["pchat", { subject: "u_anna", history: "none" }], ["s-new", { subject: "u_joe" }]]);
  });

  it("the confirmation names what the history read before it is shared", () => {
    const { lines } = shareLines([anna], true, { ...preview, private_reads: ["Gmail", "Calendar"] });
    expect(lines.join(" ")).toMatch(/Its history includes your Gmail and Calendar reads/);
  });

  it("sharing a chat that is already shared stays in it", async () => {
    const a = api({ addParticipant: vi.fn().mockResolvedValue({ session_id: "s-1", participants: [] }) });
    expect(await shareWith("s-1", [anna], null, a)).toBe("s-1");
  });
});
