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
 *  `deploy/dogfood/rig/tests/test_workspace_target.py`. What can break quietly on THIS side is the
 *  record forgetting the field, the chip not saying which one it is, and the rail not showing a
 *  conversation that is working somewhere other than the desk.
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
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
    <span>{slug === "oenb-4040f6" ? "Austrian National Bank" : slug}</span>,
}));

afterEach(cleanup);

const OENB = "oenb-4040f6";
const chat = (over: Partial<Chat> = {}): Chat =>
  ({ ...newChat("OeNB onboarding", ["personal", "_global", OENB]), ...over });

// ── the record ───────────────────────────────────────────────────────────────────────────────────

describe("the chat record carries where it writes", () => {
  it("has no target until something chooses one — which IS the personal desk", () => {
    // The default is an ABSENCE, not the string "personal": every record written before the field
    // has it, and "no target" and "the desk" have to be one state or the merge would have to pick
    // a winner between two spellings of the same thing.
    expect(chat().target).toBeUndefined();
  });

  it("takes one, and moves it", () => {
    const one = setTarget([chat({ id: "c1" })], "c1", OENB);
    expect(one[0].target).toBe(OENB);
    expect(setTarget(one, "c1", "personal")[0].target).toBe("personal");
  });

  it("clears back to the desk on an empty string", () => {
    const one = setTarget([chat({ id: "c1", target: OENB })], "c1", "");
    expect(one[0].target).toBeUndefined();
  });

  it("refuses a workspace the chat is not over", () => {
    // A chip on a mount the panel does not have and the next turn will not carry. The server makes
    // the same refusal, so the two halves cannot answer differently about the same chat.
    const one = setTarget([chat({ id: "c1", workspaces: ["personal"] })], "c1", OENB);
    expect(one[0].target).toBeUndefined();
  });

  it("leaves the mount set alone — reach and where-the-work-lands are different questions", () => {
    const one = setTarget([chat({ id: "c1" })], "c1", OENB);
    expect(one[0].workspaces).toEqual(["personal", "_global", OENB]);
  });

  it("reads the server's answer, and treats null as the desk", () => {
    const [a, b] = chatsFromSessions([
      { session: "s1", workspaces: [OENB], target: OENB, touched: true },
      { session: "s2", workspaces: ["personal"], target: null, touched: true },
    ]);
    expect(a.target).toBe(OENB);
    expect(b.target).toBeUndefined();
  });

  it("keeps a chip this reader just clicked, and takes the server's for a chat it has not seen", () => {
    const local = [chat({ id: "c1", target: OENB }), chat({ id: "c2", target: undefined })];
    const server = [chat({ id: "c1", target: "grp-ilm" }), chat({ id: "c2", target: "grp-ilm" })];
    const merged = mergeChats(local, server);
    expect(merged.find((c) => c.id === "c1")?.target).toBe(OENB);
    expect(merged.find((c) => c.id === "c2")?.target).toBe("grp-ilm");
  });

  it("puts it on the rail row", () => {
    const rows = railRows([chat({ id: "c1", target: OENB, touched: true })], []);
    expect(rows[0].target).toBe(OENB);
  });
});

// ── the header chip ──────────────────────────────────────────────────────────────────────────────

const sel = (target?: string) => ({
  kind: "chat" as const, chatId: "c1", label: "OeNB onboarding",
  workspaces: ["personal", "_global", OENB], target,
});

function bar(target: string | undefined, onSetTarget = vi.fn()) {
  const { container } = render(
    <ContextBar sel={sel(target)} flavor="chat" memberships={[]}
      onAddWorkspace={vi.fn()} onRemoveWorkspace={vi.fn()} onSetTarget={onSetTarget} />);
  return { container, onSetTarget };
}

const chipFor = (c: HTMLElement, slug: string) => c.querySelector(`[data-ws="${slug}"]`) as HTMLElement;

describe("the header chip is visibly the target", () => {
  it("marks the desk when nothing else has been chosen", () => {
    const { container } = bar(undefined);
    expect(chipFor(container, "personal").getAttribute("data-target")).toBe("1");
    expect(chipFor(container, OENB).getAttribute("data-target")).toBeNull();
  });

  it("marks the chosen one, and only it", () => {
    const { container } = bar(OENB);
    expect(chipFor(container, OENB).getAttribute("data-target")).toBe("1");
    expect(chipFor(container, "personal").getAttribute("data-target")).toBeNull();
  });

  it("says so to a screen reader too, not only in a colour", () => {
    const { container } = bar(OENB);
    expect(chipFor(container, OENB).getAttribute("aria-current")).toBe("true");
  });

  it("hides the two mounted in every chat — a constant is not information", () => {
    const { container } = bar(OENB);
    expect(chipFor(container, "_global")).toBeNull();
  });

  it("makes another one the target when the person clicks it", () => {
    const { container, onSetTarget } = bar(undefined);
    fireEvent.click(container.querySelector(`[data-ws-target="${OENB}"]`) as HTMLElement);
    expect(onSetTarget).toHaveBeenCalledWith(OENB);
  });

  it("goes back to the desk the same way — it is a place, not the absence of one", () => {
    const { container, onSetTarget } = bar(OENB);
    fireEvent.click(container.querySelector('[data-ws-target="personal"]') as HTMLElement);
    expect(onSetTarget).toHaveBeenCalledWith("personal");
  });

  it("does nothing when the person clicks the one that is already the target", () => {
    const { container, onSetTarget } = bar(OENB);
    fireEvent.click(container.querySelector(`[data-ws-target="${OENB}"]`) as HTMLElement);
    expect(onSetTarget).not.toHaveBeenCalled();
  });

  it("shows the workspace's NAME, never its slug (#1585/#1602)", () => {
    const { container } = bar(OENB);
    expect(chipFor(container, OENB).textContent).toContain("Austrian National Bank");
    expect(chipFor(container, OENB).textContent).not.toContain("4040f6");
  });

  it("still lets a workspace be removed from the chat's focus", () => {
    const onRemoveWorkspace = vi.fn();
    const { container } = render(
      <ContextBar sel={sel(OENB)} flavor="chat" memberships={[]}
        onAddWorkspace={vi.fn()} onRemoveWorkspace={onRemoveWorkspace} onSetTarget={vi.fn()} />);
    fireEvent.click(container.querySelector(`[data-ws="${OENB}"] [aria-label^="Remove"]`) as HTMLElement);
    expect(onRemoveWorkspace).toHaveBeenCalledWith(OENB);
  });

  it("answers which chip is the target without rendering anything", () => {
    expect(isTargetChip(undefined, "personal")).toBe(true);
    expect(isTargetChip(undefined, OENB)).toBe(false);
    expect(isTargetChip(OENB, OENB)).toBe(true);
  });
});

// ── the rail row ─────────────────────────────────────────────────────────────────────────────────

const row = (over: Partial<Row> = {}): Row => ({
  key: "c:c1", chatId: "c1", meetingId: null, label: "OeNB onboarding", when: 1, whenLabel: "now",
  live: false, upcoming: false, status: null, touched: true, workspaces: ["personal"], ...over,
});

const rail = (rows: Row[]) => render(
  <Rail rows={rows} hidden={0} all={false} onAll={vi.fn()} selKey={null} onSelect={vi.fn()}
    onNewChat={vi.fn()} onDeleteChat={vi.fn()} />);

describe("the rail says which conversation is working somewhere else", () => {
  it("names the target workspace on the row", () => {
    const { container } = rail([row({ target: OENB })]);
    const tag = container.querySelector(`[data-row-target="${OENB}"]`);
    expect(tag?.textContent).toBe("Austrian National Bank");
  });

  it("says nothing on a row that writes to the desk", () => {
    // Nearly every chat is this one, so a tag saying so on nearly every row is chrome — the same
    // argument `IMPLICIT_MOUNTS` makes about `_global`. The row that is NOT the ordinary case is
    // the information, and it is the case the founder lost a morning's files to.
    const { container } = rail([row()]);
    expect(container.querySelector("[data-row-target]")).toBeNull();
  });

  it("keeps the row's own name and its meeting status beside it", () => {
    const { container } = rail([row({ target: OENB, status: "held" })]);
    expect(screen.getByText("OeNB onboarding")).toBeTruthy();
    expect(container.querySelector('[data-row-status="held"]')).toBeTruthy();
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
