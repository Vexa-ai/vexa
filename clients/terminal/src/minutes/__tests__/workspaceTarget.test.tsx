/** A CHAT HAS A TARGET WORKSPACE: WRITES GO THERE (Vexa-ai/vexa#1611) — the client half.
 *
 *  Founder walk, 2026-09-06 13:58Z. He was in a chat whose header chip read `personal` while the
 *  whole conversation was about a customer's workspace, and the files landed on his desk:
 *
 *      *"it creates files in the wrong workspace, we need so that the thing knew the workspace of
 *      writing, if it's specified. We have this "personal" and we probably should be able to set a
 *      workspace that we are targeting (other workspaces still available to read and even to write,
 *      if explicit ask and purpose)"*
 *
 *  `workspaces[]` is what the chat can REACH; `target` is where it WORKS. The server half — the
 *  session record, the prompt line every turn carries, the turn's cwd and the tools' default slug —
 *  is pinned in `core/agent/tests/test_chat_workspace_target.py` and
 *  `core/agent/tests/test_page_and_target_routes.py`. What can break quietly on THIS side is the
 *  record forgetting the field, the chip not saying which one it is, and the rail not showing a
 *  conversation that is working somewhere other than the desk.
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, act } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { ContextBar, isTargetChip } from "../ContextBar";
import { Rail } from "../Rail";
import {
  chatsFromSessions, mergeChats, newChat, railRows, setTarget, type Chat, type Row,
} from "../chats";

vi.mock("../../ui-kit/WsLink", () => ({
  // The registry lookup is a network read and its own component's job. What matters here is that
  // the chip and the row ask for a NAME rather than printing a slug (#1585/#1602) — so the stand-in
  // renders a name and the tests read it back.
  WorkspaceName: ({ slug }: { slug: string }) =>
    <span>{slug === "bank-4040f6" ? "Example Bank" : slug}</span>,
}));

afterEach(cleanup);
// The header names a workspace only once the server says this reader may read it, and calls it the
// target only once the server says they may write it (contextBarServerView.test.tsx). Here the
// reader is a writing member of every workspace in the set.
vi.mock("../../ui-kit/wsLinks", () => ({
  workspaceBySlug: async (slug: string) => ({ id: "aaaaaaaaaa", name: slug, kind: "group", slug,
    access: "readable", writable: true }),
}));
const settle = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });


const BANK = "bank-4040f6";
const chat = (over: Partial<Chat> = {}): Chat =>
  ({ ...newChat("Example Bank onboarding", ["personal", "_global", BANK]), ...over });

// ── the record ───────────────────────────────────────────────────────────────────────────────────

describe("the chat record carries where it writes", () => {
  it("has no target until something chooses one — which IS the personal desk", () => {
    // The default is an ABSENCE, not the string "personal": every record written before the field
    // has it, and "no target" and "the desk" have to be one state or the merge would have to pick
    // a winner between two spellings of the same thing.
    expect(chat().target).toBeUndefined();
  });

  it("takes one, and moves it", () => {
    const one = setTarget([chat({ id: "c1" })], "c1", BANK);
    expect(one[0].target).toBe(BANK);
    expect(setTarget(one, "c1", "personal")[0].target).toBe("personal");
  });

  it("clears back to the desk on an empty string", () => {
    const one = setTarget([chat({ id: "c1", target: BANK })], "c1", "");
    expect(one[0].target).toBeUndefined();
  });

  it("refuses a workspace the chat is not over", () => {
    // A chip on a mount the panel does not have and the next turn will not carry. The server makes
    // the same refusal, so the two halves cannot answer differently about the same chat.
    const one = setTarget([chat({ id: "c1", workspaces: ["personal"] })], "c1", BANK);
    expect(one[0].target).toBeUndefined();
  });

  it("leaves the mount set alone — reach and where-the-work-lands are different questions", () => {
    const one = setTarget([chat({ id: "c1" })], "c1", BANK);
    expect(one[0].workspaces).toEqual(["personal", "_global", BANK]);
  });

  it("reads the server's answer, and treats null as the desk", () => {
    const [a, b] = chatsFromSessions([
      { session: "s1", workspaces: [BANK], target: BANK, touched: true },
      { session: "s2", workspaces: ["personal"], target: null, touched: true },
    ]);
    expect(a.target).toBe(BANK);
    expect(b.target).toBeUndefined();
  });

  it("keeps a chip this reader just clicked, and takes the server's for a chat it has not seen", () => {
    const local = [chat({ id: "c1", target: BANK }), chat({ id: "c2", target: undefined })];
    const server = [chat({ id: "c1", target: "grp-copperline" }), chat({ id: "c2", target: "grp-copperline" })];
    const merged = mergeChats(local, server);
    expect(merged.find((c) => c.id === "c1")?.target).toBe(BANK);
    expect(merged.find((c) => c.id === "c2")?.target).toBe("grp-copperline");
  });

  it("puts it on the rail row", () => {
    const rows = railRows([chat({ id: "c1", target: BANK, touched: true })], []);
    expect(rows[0].target).toBe(BANK);
  });
});

// ── the header chip ──────────────────────────────────────────────────────────────────────────────

const sel = (target?: string) => ({
  kind: "chat" as const, chatId: "c1", label: "Example Bank onboarding",
  workspaces: ["personal", "_global", BANK], target,
});

async function bar(target: string | undefined, onSetTarget = vi.fn()) {
  const { container } = render(
    <ContextBar sel={sel(target)} flavor="chat" memberships={[]}
      onAddWorkspace={vi.fn()} onRemoveWorkspace={vi.fn()} onSetTarget={onSetTarget} />);
  await settle();
  return { container, onSetTarget };
}

const chipFor = (c: HTMLElement, slug: string) => c.querySelector(`[data-ws="${slug}"]`) as HTMLElement;

describe("the header chip is visibly the target", () => {
  it("marks the desk when nothing else has been chosen", async () => {
    const { container } = await bar(undefined);
    expect(chipFor(container, "personal").getAttribute("data-target")).toBe("1");
    expect(chipFor(container, BANK).getAttribute("data-target")).toBeNull();
  });

  it("marks the chosen one, and only it", async () => {
    const { container } = await bar(BANK);
    expect(chipFor(container, BANK).getAttribute("data-target")).toBe("1");
    expect(chipFor(container, "personal").getAttribute("data-target")).toBeNull();
  });

  it("says so to a screen reader too, not only in a colour", async () => {
    const { container } = await bar(BANK);
    expect(chipFor(container, BANK).getAttribute("aria-current")).toBe("true");
  });

  it("hides the two mounted in every chat — a constant is not information", async () => {
    const { container } = await bar(BANK);
    expect(chipFor(container, "_global")).toBeNull();
  });

  it("makes another one the target when the person clicks it", async () => {
    const { container, onSetTarget } = await bar(undefined);
    fireEvent.click(container.querySelector(`[data-ws-target="${BANK}"]`) as HTMLElement);
    expect(onSetTarget).toHaveBeenCalledWith(BANK);
  });

  it("goes back to the desk the same way — it is a place, not the absence of one", async () => {
    const { container, onSetTarget } = await bar(BANK);
    fireEvent.click(container.querySelector('[data-ws-target="personal"]') as HTMLElement);
    expect(onSetTarget).toHaveBeenCalledWith("personal");
  });

  it("does nothing when the person clicks the one that is already the target", async () => {
    const { container, onSetTarget } = await bar(BANK);
    fireEvent.click(container.querySelector(`[data-ws-target="${BANK}"]`) as HTMLElement);
    expect(onSetTarget).not.toHaveBeenCalled();
  });

  it("shows the workspace's NAME, never its slug (#1585/#1602)", async () => {
    const { container } = await bar(BANK);
    expect(chipFor(container, BANK).textContent).toContain("Example Bank");
    expect(chipFor(container, BANK).textContent).not.toContain("4040f6");
  });

  it("still lets a workspace be removed from the chat's focus", async () => {
    const onRemoveWorkspace = vi.fn();
    const { container } = render(
      <ContextBar sel={sel(BANK)} flavor="chat" memberships={[]}
        onAddWorkspace={vi.fn()} onRemoveWorkspace={onRemoveWorkspace} onSetTarget={vi.fn()} />);
    await settle();
    fireEvent.click(container.querySelector(`[data-ws="${BANK}"] [aria-label^="Remove"]`) as HTMLElement);
    expect(onRemoveWorkspace).toHaveBeenCalledWith(BANK);
  });

  it("answers which chip is the target without rendering anything", async () => {
    expect(isTargetChip(undefined, "personal")).toBe(true);
    expect(isTargetChip(undefined, BANK)).toBe(false);
    expect(isTargetChip(BANK, BANK)).toBe(true);
  });
});

// ── the rail row ─────────────────────────────────────────────────────────────────────────────────

const row = (over: Partial<Row> = {}): Row => ({
  key: "c:c1", chatId: "c1", meetingId: null, label: "Example Bank onboarding", when: 1, whenLabel: "now",
  live: false, upcoming: false, status: null, touched: true, workspaces: ["personal"], ...over,
});

const rail = (rows: Row[]) => render(
  <Rail rows={rows} hidden={0} all={false} onAll={vi.fn()} selKey={null} onSelect={vi.fn()}
    onNewChat={vi.fn()} onDeleteChat={vi.fn()} />);

describe("the rail says which conversation is working somewhere else", () => {
  it("marks the row with its target — as data, not a text chip (founder 2026-10-14: the chips ate the names)", () => {
    const { container } = rail([row({ target: BANK })]);
    const r = container.querySelector(`[data-row-target="${BANK}"]`);
    expect(r).toBeTruthy();
    expect(r!.querySelector(".vx-tag")).toBeNull();
  });

  it("says nothing on a row that writes to the desk", () => {
    // Nearly every chat is this one, so a tag saying so on nearly every row is chrome — the same
    // argument `IMPLICIT_MOUNTS` makes about `_global`. The row that is NOT the ordinary case is
    // the information, and it is the case the founder lost a morning's files to.
    const { container } = rail([row()]);
    expect(container.querySelector("[data-row-target]")).toBeNull();
  });

  it("keeps the row's own name beside it, and a finished meeting adds no status word", () => {
    const { container } = rail([row({ target: BANK, status: "held" })]);
    expect(screen.getByText("Example Bank onboarding")).toBeTruthy();
    expect(container.querySelector("[data-row-target]")).toBeTruthy();
    expect(container.querySelector("[data-row-status]")).toBeNull();
  });
});

// ── the shell's one writer ───────────────────────────────────────────────────────────────────────

describe("the shell moves it through ONE writer", () => {
  /** Wiring rather than a decision, so what is pinned is that every route goes through the same
   *  function — the chip's click, the `focus` event a `workspace_new`/`workspace_target` produced,
   *  and the removal of the workspace that was the target. A second writer here is how the chip,
   *  the record and the server's answer would come to disagree. */
  const shell = readFileSync(join(__dirname, "..", "MinutesShell.tsx"), "utf8");

  it("writes the record, the selection, the draft and the server together", () => {
    expect(shell).toMatch(/const chooseTarget = /);
    expect(shell).toMatch(/persist\(\(prev\) => setTarget\(prev, id, next\)\)/);
    expect(shell).toMatch(/void setChatTarget\(id, next\)/);
  });

  it("takes the target on the focus event, after the mount is in", () => {
    expect(shell).toMatch(/setChatTargetRef\.current\(wid, \{ justMounted: true \}\)/);
  });

  it("gives the writes back to the desk when the target is removed from the chat", () => {
    expect(shell).toMatch(/if \(sel\.target === id\) chooseTarget\(""\)/);
  });
});
