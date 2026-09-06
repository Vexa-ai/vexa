/** THE ADMIN CAN AIM A CHAT AT THE COMPANY LAYER (Vexa-ai/vexa#1616) — the client half.
 *
 *  Founder, 2026-09-06 15:20Z, as the admin, looking at the header's `+` menu (it offered `OeNB`
 *  and "Attach existing repo…"):
 *
 *      *"as admin i should just have global as option to choose here as workspace to write to"*
 *
 *  `_global` was hidden from the chip by the first walk's ruling — which was about ORDINARY
 *  MEMBERS, for whom it is mounted in every chat and therefore a constant. For the admin it is not
 *  a constant: it is the one workspace they may write that nobody chose. So it is offered in the
 *  `+` menu, it wears a chip while the chat is aimed at it, and clicking that chip opens
 *  `_global/README.md`. For everybody else nothing appears, and nothing can.
 *
 *  The server half — the turn's line naming the company layer with the rule that applies there, and
 *  the refusal that stops a non-admin session pointing a chat at `_global` — is pinned in
 *  `core/agent/tests/test_global_target.py`.
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { render, cleanup, fireEvent } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { ContextBar, GLOBAL_MOUNT, focusSet } from "../ContextBar";
import { COMPANY_WORD } from "../vocabulary";

// The real component's contract, which is what these tests are about: a NAME from the registry
// wins, the caller's fallback comes next, and the slug is the last resort nobody should ever see.
// `_global` is absent here on purpose — the tier has no name until the setup conversation writes
// the company's, and that gap is exactly where a chip would print `_global` without a fallback.
const NAMED: Record<string, string> = { "oenb-4040f6": "Austrian National Bank" };
vi.mock("../../ui-kit/WsLink", () => ({
  WorkspaceName: ({ slug, fallback }: { slug: string; fallback?: string }) =>
    <span>{NAMED[slug] ?? fallback ?? slug}</span>,
}));

afterEach(cleanup);

const OENB = "oenb-4040f6";

const sel = (target?: string, workspaces = ["personal", "_global", OENB]) => ({
  kind: "chat" as const, chatId: "c1", label: "OeNB onboarding", workspaces, target,
});

function bar(opts: {
  admin?: boolean; target?: string; workspaces?: string[];
  memberships?: { workspace_id: string; role: string }[];
} = {}) {
  const onTargetGlobal = vi.fn();
  const onSetTarget = vi.fn();
  const onAddWorkspace = vi.fn();
  const { container } = render(
    <ContextBar sel={sel(opts.target, opts.workspaces)} flavor="chat"
      memberships={(opts.memberships ?? []) as never[]}
      onAddWorkspace={onAddWorkspace} onRemoveWorkspace={vi.fn()} onSetTarget={onSetTarget}
      isAdmin={opts.admin} onTargetGlobal={onTargetGlobal} />);
  const openMenu = () =>
    fireEvent.click(container.querySelector('[aria-label="Add a workspace to this chat"]') as HTMLElement);
  return { container, onTargetGlobal, onSetTarget, onAddWorkspace, openMenu };
}

const chipFor = (c: HTMLElement, slug: string) => c.querySelector(`[data-ws="${slug}"]`) as HTMLElement;
const globalEntry = (c: HTMLElement) => c.querySelector('[data-ctx="global"]') as HTMLElement | null;

// ── the + menu ───────────────────────────────────────────────────────────────────────────────────

describe("the + menu offers the company layer, to the admin and to nobody else", () => {
  it("offers it to the admin", () => {
    const { container, openMenu } = bar({ admin: true });
    openMenu();
    expect(globalEntry(container)).toBeTruthy();
    // The same name the chip will wear — one word for one place, resolved the same way.
    expect(globalEntry(container)?.textContent).toBe(COMPANY_WORD);
  });

  it("does not offer it to anyone else", () => {
    const { container, openMenu } = bar({ admin: false });
    openMenu();
    expect(globalEntry(container)).toBeNull();
  });

  it("does not offer it when nobody has said who this is", () => {
    // `is_admin` is three-valued upstream and this prop is not. An unknown answer must not open a
    // door the server would then slam — so the offer is made on a literal `true` and nothing else.
    const { container, openMenu } = bar({});
    openMenu();
    expect(globalEntry(container)).toBeNull();
  });

  it("does not offer it when the chat already writes there — a row that changes nothing reads as broken", () => {
    const { container, openMenu } = bar({ admin: true, target: GLOBAL_MOUNT });
    openMenu();
    expect(globalEntry(container)).toBeNull();
  });

  it("aims the chat at it on the click, and closes the menu", () => {
    const { container, openMenu, onTargetGlobal } = bar({ admin: true });
    openMenu();
    fireEvent.click(globalEntry(container) as HTMLElement);
    expect(onTargetGlobal).toHaveBeenCalledTimes(1);
    expect(container.querySelector('[role="menu"]')).toBeNull();
  });

  it("stops saying the menu is empty when it is not", () => {
    const { container, openMenu } = bar({ admin: true });
    openMenu();
    expect(container.textContent).not.toContain("No other workspace to add");
  });

  it("still says so to everyone else with nothing to add", () => {
    const { container, openMenu } = bar({ admin: false });
    openMenu();
    expect(container.textContent).toContain("No other workspace to add");
  });

  it("lists it above the workspaces somebody was invited to", () => {
    const { container, openMenu } = bar({
      admin: true, workspaces: ["personal"], memberships: [{ workspace_id: OENB, role: "owner" }],
    });
    openMenu();
    const items = [...container.querySelectorAll('[role="menuitem"]')].map((n) => n.textContent);
    expect(items[0]).toBe(COMPANY_WORD);
    expect(items).toContain("Austrian National Bank");
  });
});

// ── the chip ─────────────────────────────────────────────────────────────────────────────────────

describe("the chip is on screen exactly while the chat writes there", () => {
  it("appears for the admin once the chat is aimed at it", () => {
    const { container } = bar({ admin: true, target: GLOBAL_MOUNT });
    expect(chipFor(container, GLOBAL_MOUNT)).toBeTruthy();
  });

  it("stays hidden for the admin while the chat writes somewhere else", () => {
    // The rail's rule, one level in: nearly every chat writes to the desk, so a chip saying so on
    // nearly every chat is chrome. The case that is NOT ordinary is the information.
    const { container } = bar({ admin: true, target: OENB });
    expect(chipFor(container, GLOBAL_MOUNT)).toBeNull();
  });

  it("never appears for anyone else, whatever the record says", () => {
    const { container } = bar({ admin: false, target: GLOBAL_MOUNT });
    expect(chipFor(container, GLOBAL_MOUNT)).toBeNull();
  });

  it("is visibly the target, and says so to a screen reader", () => {
    const { container } = bar({ admin: true, target: GLOBAL_MOUNT });
    expect(chipFor(container, GLOBAL_MOUNT).getAttribute("data-target")).toBe("1");
    expect(chipFor(container, GLOBAL_MOUNT).getAttribute("aria-current")).toBe("true");
  });

  it("falls back to a word, never to the slug (#1585/#1602)", () => {
    // In the running product the registry answers and the chip wears the company's own name. This
    // pins the gap under it: until something knows that name, the chip must still not read
    // `_global`, which is a directory showing through in the one place names belong.
    const { container } = bar({ admin: true, target: GLOBAL_MOUNT });
    expect(chipFor(container, GLOBAL_MOUNT).textContent).toBe(COMPANY_WORD);
    expect(chipFor(container, GLOBAL_MOUNT).textContent).not.toContain("_global");
  });

  it("opens it on the click — the chip is the door, not only a label", () => {
    // It is the target already, so the ordinary "make this the target" click is a no-op. A chip
    // that names a place and refuses to open it is worse than no chip.
    const { container, onTargetGlobal, onSetTarget } = bar({ admin: true, target: GLOBAL_MOUNT });
    fireEvent.click(container.querySelector(`[data-ws-target="${GLOBAL_MOUNT}"]`) as HTMLElement);
    expect(onTargetGlobal).toHaveBeenCalledTimes(1);
    expect(onSetTarget).not.toHaveBeenCalled();
  });

  it("carries no × — it is mounted in every chat, so removing it is not a thing that can happen", () => {
    const { container } = bar({ admin: true, target: GLOBAL_MOUNT });
    expect(chipFor(container, GLOBAL_MOUNT).querySelector('[aria-label^="Remove"]')).toBeNull();
  });

  it("leaves every other chip exactly as it was", () => {
    const { container, onSetTarget } = bar({ admin: true, target: GLOBAL_MOUNT });
    fireEvent.click(container.querySelector(`[data-ws-target="${OENB}"]`) as HTMLElement);
    expect(onSetTarget).toHaveBeenCalledWith(OENB);
    expect(chipFor(container, OENB).querySelector('[aria-label^="Remove"]')).toBeTruthy();
  });
});

// ── the rule, without rendering anything ─────────────────────────────────────────────────────────

describe("which chips a chat shows", () => {
  it("hides both implicit mounts by default — the ruling this issue narrows, not the one it undoes", () => {
    expect(focusSet(["personal", "_global", "_system", OENB])).toEqual(["personal", OENB]);
  });

  it("adds the company layer for an admin aimed at it", () => {
    expect(focusSet(["personal", "_global"], { admin: true, target: GLOBAL_MOUNT }))
      .toEqual(["personal", GLOBAL_MOUNT]);
  });

  it("adds it even when the mount set never carried it", () => {
    // A row restored from the server can hold the target without `_global` in `workspaces` — and a
    // header that stayed silent then is the exact failure #1611 exists to end.
    expect(focusSet(["personal"], { admin: true, target: GLOBAL_MOUNT }))
      .toEqual(["personal", GLOBAL_MOUNT]);
  });

  it("never adds `_system`, for anybody", () => {
    expect(focusSet(["personal", "_system"], { admin: true, target: "_system" })).toEqual(["personal"]);
  });

  it("adds nothing for a non-admin, and nothing for an admin aimed elsewhere", () => {
    expect(focusSet(["personal", "_global"], { admin: false, target: GLOBAL_MOUNT })).toEqual(["personal"]);
    expect(focusSet(["personal", "_global"], { admin: true, target: OENB })).toEqual(["personal"]);
  });
});

// ── the shell's wiring ───────────────────────────────────────────────────────────────────────────

describe("the shell mounts it, aims at it, and opens it — in that order", () => {
  /** Wiring rather than a decision, so what is pinned is the ORDER: `chooseTarget` refuses a target
   *  the chat is not over, so a mount that arrived second would be a chip that paints a state the
   *  record refuses to keep. */
  const shell = readFileSync(join(__dirname, "..", "MinutesShell.tsx"), "utf8");
  const handler = shell.slice(shell.indexOf("onTargetGlobal={"), shell.indexOf("onAttachRepo={(id)"));

  it("adds the mount before it takes the target", () => {
    expect(handler).toMatch(/setWorkspaces\(\(ws\) => \(ws\.includes\(GLOBAL_MOUNT\)/);
    expect(handler).toMatch(/chooseTarget\(GLOBAL_MOUNT, \{ justMounted: true \}\)/);
    // The CALLS, not the prose above them — the comment names both in the other order.
    expect(handler.indexOf("setWorkspaces((ws)"))
      .toBeLessThan(handler.indexOf("chooseTarget(GLOBAL_MOUNT,"));
  });

  it("opens the company layer's README, and does not defer to a focus the reader chose", () => {
    expect(handler).toMatch(/openPage\(\{ path: "README\.md", slug: GLOBAL_MOUNT/);
    expect(handler).toContain("readerChoseFocus.current = true");
  });

  it("asks the server who this is, and believes only a literal yes", () => {
    expect(shell).toMatch(/setIsAdmin\(\(d as \{ is_admin\?: boolean \} \| null\)\?\.is_admin === true\)/);
  });
});
