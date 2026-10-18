/** "Saves to X" is the server's word, not the browser's.
 *
 *  A chat's stored set can name a workspace the signed-in reader has no claim on — a set restored
 *  from another account's browser state, a group they were removed from. The header names a
 *  workspace only when the server says this reader can read it, and says "Saves to X" only when
 *  the server says this reader can write X. Otherwise: Personal, and X is not mentioned at all. */
import { describe, expect, it, vi, afterEach } from "vitest";
import { render, cleanup, waitFor, act } from "@testing-library/react";

import { ContextBar, namedSet, shownTarget } from "../ContextBar";

const GROUP = "team-abc123";
const VIEWED = "viewer-ws";
const MINE = "mine-ws";

vi.mock("../../ui-kit/wsLinks", () => ({
  workspaceBySlug: async (slug: string) => {
    if (slug === GROUP) return { id: null, name: null, kind: null, slug, access: "not-yours", writable: false };
    if (slug === VIEWED) return { id: "aaaaaaaaaa", name: "Viewed", kind: "group", slug, access: "readable", writable: false };
    if (slug === MINE) return { id: "bbbbbbbbbb", name: "Mine", kind: "group", slug, access: "readable", writable: true };
    return null;
  },
}));
vi.mock("../../ui-kit/WsLink", () => ({
  WorkspaceName: ({ slug }: { slug: string }) => <span>{slug === MINE ? "Mine" : slug === VIEWED ? "Viewed" : slug}</span>,
}));

afterEach(cleanup);

const bar = (workspaces: string[], target?: string) => render(
  <ContextBar sel={{ kind: "chat", chatId: "c1", label: "A chat", workspaces, target }} flavor="chat"
    memberships={[]} onAddWorkspace={vi.fn()} onRemoveWorkspace={vi.fn()} onSetTarget={vi.fn()} />);

describe("the header follows the server's view of this reader", () => {
  it("a workspace the reader has no claim on is not named, and Personal is the target", async () => {
    const { container } = bar(["personal", "_global", GROUP], GROUP);
    await act(async () => { await Promise.resolve(); });
    expect(container.textContent).not.toContain(GROUP);
    const desk = container.querySelector('[data-ws="personal"]') as HTMLElement;
    expect(desk.getAttribute("data-target")).toBe("1");
    expect(desk.textContent).toContain("Saves to");
  });

  it("a readable workspace the reader may not write is shown, never as the target", async () => {
    const { container } = bar(["personal", VIEWED], VIEWED);
    await waitFor(() => expect(container.querySelector(`[data-ws="${VIEWED}"]`)).not.toBeNull());
    expect(container.querySelector(`[data-ws="${VIEWED}"]`)?.getAttribute("data-target")).toBeNull();
    expect(container.querySelector('[data-ws="personal"]')?.getAttribute("data-target")).toBe("1");
  });

  it("a writable workspace is the target once the server says so", async () => {
    const { container } = bar(["personal", MINE], MINE);
    await waitFor(() => expect(container.querySelector(`[data-ws="${MINE}"]`)?.getAttribute("data-target")).toBe("1"));
    expect(container.querySelector(`[data-ws="${MINE}"]`)?.textContent).toContain("Saves to Mine");
  });

  it("the rules, without rendering", () => {
    const seen = {
      [GROUP]: { access: "not-yours" as const, writable: false },
      [MINE]: { access: "readable" as const, writable: true },
    };
    expect(namedSet(["personal", GROUP, MINE], seen)).toEqual(["personal", MINE]);
    expect(shownTarget(GROUP, seen)).toBe("personal");
    expect(shownTarget(MINE, seen)).toBe(MINE);
    expect(shownTarget(MINE, {})).toBe("personal");            // unknown yet: not a guess
    expect(shownTarget(undefined, seen)).toBe("personal");
    expect(shownTarget(GROUP, seen, true)).toBe("_global");     // the admin account's one target
  });
});
