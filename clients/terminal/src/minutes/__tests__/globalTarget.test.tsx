/** ONLY THE ADMIN ACCOUNT HAS THE COMPANY LAYER — the client half.
 *
 *  Founder ruling 2026-10-11: *"let's change so that admin is a special account that has global only
 *  and this being the only one that has control to global"*. It replaces #1616's shape, where the
 *  instance admin — a person, on their everyday account — could aim any chat at `_global` from the
 *  header's `+` menu. Now:
 *
 *    * the ADMIN ACCOUNT (identity's `admin_account`) sees one chip, Company, always the target, and
 *      no `+` menu — it has no desk, no workspace and nothing to add;
 *    * every everyday account — the instance admin's included — is never offered Company and never
 *      shows its chip, whatever the chat's record says.
 *
 *  The server half — the mount table, the refusals and the cwd — is pinned in
 *  `core/agent/tests/test_admin_account.py` and `core/agent/tests/test_global_target.py`.
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { render, cleanup, fireEvent } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { ContextBar, GLOBAL_MOUNT, focusSet } from "../ContextBar";
import { buildWorkspaces } from "../navigatorApi";
import { COMPANY_WORD } from "../vocabulary";

// The real component's contract: a NAME from the registry wins, the caller's fallback comes next,
// and the slug is the last resort nobody should ever see. `_global` is absent here on purpose.
const NAMED: Record<string, string> = { "bank-4040f6": "Example Bank" };
vi.mock("../../ui-kit/WsLink", () => ({
  WorkspaceName: ({ slug, fallback }: { slug: string; fallback?: string }) =>
    <span>{NAMED[slug] ?? fallback ?? slug}</span>,
}));

afterEach(cleanup);

const BANK = "bank-4040f6";

const sel = (target?: string, workspaces = ["personal", "_global", BANK]) => ({
  kind: "chat" as const, chatId: "c1", label: "Example Bank onboarding", workspaces, target,
});

function bar(opts: {
  adminAccount?: boolean; target?: string; workspaces?: string[];
  memberships?: { workspace_id: string; role: string }[];
} = {}) {
  const onTargetGlobal = vi.fn();
  const onSetTarget = vi.fn();
  const onAddWorkspace = vi.fn();
  const { container } = render(
    <ContextBar sel={sel(opts.target, opts.workspaces)} flavor="chat"
      memberships={(opts.memberships ?? []) as never[]}
      onAddWorkspace={onAddWorkspace} onRemoveWorkspace={vi.fn()} onSetTarget={onSetTarget}
      adminAccount={opts.adminAccount} onTargetGlobal={onTargetGlobal} />);
  const plus = () => container.querySelector('[aria-label="Add a workspace to this chat"]') as HTMLElement | null;
  const openMenu = () => fireEvent.click(plus() as HTMLElement);
  return { container, onTargetGlobal, onSetTarget, onAddWorkspace, openMenu, plus };
}

const chipFor = (c: HTMLElement, slug: string) => c.querySelector(`[data-ws="${slug}"]`) as HTMLElement;
const chips = (c: HTMLElement) => [...c.querySelectorAll("[data-ws]")].map((n) => n.getAttribute("data-ws"));

// ── the admin account: Company, and nothing else ─────────────────────────────────────────────────

describe("the admin account's header is the company layer and nothing else", () => {
  it("shows one chip, Company, whatever the chat's record holds", () => {
    const { container } = bar({ adminAccount: true, target: undefined, workspaces: ["personal", BANK] });
    expect(chips(container)).toEqual([GLOBAL_MOUNT]);
    expect(chipFor(container, GLOBAL_MOUNT).textContent).toBe(COMPANY_WORD);
  });

  it("the chip is always the target, and says so to a screen reader", () => {
    const { container } = bar({ adminAccount: true });
    expect(chipFor(container, GLOBAL_MOUNT).getAttribute("data-target")).toBe("1");
    expect(chipFor(container, GLOBAL_MOUNT).getAttribute("aria-current")).toBe("true");
  });

  it("offers no + menu — it has no desk, no workspace and nothing to add", () => {
    const { plus } = bar({ adminAccount: true, memberships: [{ workspace_id: BANK, role: "owner" }] });
    expect(plus()).toBeNull();
  });

  it("opens the company layer on the chip's click, and carries no ×", () => {
    const { container, onTargetGlobal, onSetTarget } = bar({ adminAccount: true });
    fireEvent.click(container.querySelector(`[data-ws-target="${GLOBAL_MOUNT}"]`) as HTMLElement);
    expect(onTargetGlobal).toHaveBeenCalledTimes(1);
    expect(onSetTarget).not.toHaveBeenCalled();
    expect(chipFor(container, GLOBAL_MOUNT).querySelector('[aria-label^="Remove"]')).toBeNull();
  });

  it("falls back to a word, never to the slug (#1585/#1602)", () => {
    const { container } = bar({ adminAccount: true });
    expect(chipFor(container, GLOBAL_MOUNT).textContent).not.toContain("_global");
  });
});

// ── everyday accounts: never Company ─────────────────────────────────────────────────────────────

describe("an everyday account is never offered Company", () => {
  for (const [name, adminAccount] of [["an everyday account", false], ["an unanswered probe", undefined]] as const) {
    it(`${name}: the + menu has no Company row`, () => {
      const { container, openMenu } = bar({ adminAccount });
      openMenu();
      const items = [...container.querySelectorAll('[role="menuitem"]')].map((n) => n.textContent);
      expect(items).not.toContain(COMPANY_WORD);
      expect(container.querySelector('[data-ctx="global"]')).toBeNull();
    });

    it(`${name}: no Company chip, even on a record aimed there`, () => {
      const { container } = bar({ adminAccount, target: GLOBAL_MOUNT });
      expect(chipFor(container, GLOBAL_MOUNT)).toBeNull();
    });
  }

  it("still says so with nothing to add", () => {
    const { container, openMenu } = bar({ adminAccount: false });
    openMenu();
    expect(container.textContent).toContain("No other workspace to add");
  });

  it("leaves every other chip exactly as it was", () => {
    const { container, onSetTarget } = bar({ adminAccount: false });
    fireEvent.click(container.querySelector(`[data-ws-target="${BANK}"]`) as HTMLElement);
    expect(onSetTarget).toHaveBeenCalledWith(BANK);
    expect(chipFor(container, BANK).querySelector('[aria-label^="Remove"]')).toBeTruthy();
  });
});

// ── the rule, without rendering anything ─────────────────────────────────────────────────────────

describe("which chips a chat shows", () => {
  it("hides both implicit mounts for everybody else", () => {
    expect(focusSet(["personal", "_global", "_system", BANK])).toEqual(["personal", BANK]);
    expect(focusSet(["personal", "_global"], { admin: false, target: GLOBAL_MOUNT })).toEqual(["personal"]);
  });

  it("is the company layer alone for the admin account", () => {
    expect(focusSet(["personal", BANK], { admin: true })).toEqual([GLOBAL_MOUNT]);
    expect(focusSet([], { admin: true, target: "_system" })).toEqual([GLOBAL_MOUNT]);
  });
});

describe("the navigator", () => {
  const active = [
    { slug: "_global", repo: "", ref: "", role: "global", path: "/w/_global", write: true, primary: false, name: "Acme" },
  ];

  it("lists only the company layer for the admin account — no desk row", () => {
    const rows = buildWorkspaces({ active: active as never, subject: "7", adminAccount: true,
                                   memberships: [{ workspace_id: BANK, name: "Example Bank" }] });
    expect(rows.map((r) => r.key)).toEqual(["_global"]);
    expect(rows[0].name).toBe("Acme");
  });

  it("keeps the desk first for everybody else", () => {
    expect(buildWorkspaces({ active: [], subject: "7" })[0].kind).toBe("desk");
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

  it("asks the server whether this is the admin account, and believes only a literal yes", () => {
    expect(shell).toMatch(/setIsAdminAccount\(\(d as \{ admin_account\?: boolean \} \| null\)\?\.admin_account === true\)/);
    expect(shell).toContain("adminAccount={isAdminAccount}");
    expect(shell).not.toMatch(/isAdmin=\{/);
  });
});
