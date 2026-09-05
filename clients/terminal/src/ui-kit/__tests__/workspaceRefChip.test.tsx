/** THE CHIP THAT OPENED THE WRONG ROOM — end to end, from the words in a reply to the workspace the
 *  click actually asks for.
 *
 *  Founder, 2026-09-05: *"workspace links can open wrong workspaces — we have unique workspace but
 *  it does not work."* The resolver tests next door pin the rules; this pins the PATH THROUGH THE
 *  UI, because every hop was capable of losing the name on its own: the transform decides whether a
 *  word becomes a chip at all, the chip decides what it dispatches, and the resolver decides which
 *  room that lands in. A reply is the input because a reply is where the founder met it.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { transformDocRefs } from "../MdxDoc";
import { WorkspaceRef, invalidateDocLinkCaches, primeKnownWorkspaces, resolveDocRef } from "../docLinks";
import { OPEN_ENTITY_EVENT } from "../../canvas/actions";

// Two team rooms the reader belongs to; only ONE of them is mounted in this chat. `README.md` is
// the file every workspace has, which is what made the wrong answer look like a right one.
const trees: Record<string, string[]> = {
  "": ["README.md"],
  _global: ["README.md"],
  "vexa-team-3183d1": ["README.md"],
  // "vexa-team-a2a023" absent on purpose — parked, so its tree read answers empty (the 403)
};
const active = [
  { slug: "seed", path: "/workspaces/57", primary: true },
  { slug: "_global", path: "/workspaces/_global" },
  { slug: "vexa-team-3183d1", path: "/workspaces/vexa-team-3183d1" },
];
const memberships = [
  { workspace_id: "vexa-team-3183d1", role: "owner" },
  { workspace_id: "vexa-team-a2a023", role: "contributor" },
];
vi.mock("../../surfaces/workspaceApi", () => ({
  listWorkspaceTree: vi.fn(async (opts?: { slug?: string }) => trees[opts?.slug ?? ""] ?? []),
  readActiveSet: vi.fn(async () => ({ subject: "57", active })),
  listSharedMemberships: vi.fn(async () => memberships),
}));

interface OpenDetail { path?: string; wikilink?: string; slug?: string; exact?: boolean; docPath?: string }
let opened: OpenDetail[] = [];
const capture = (e: Event) => { opened.push((e as CustomEvent<OpenDetail>).detail); };

beforeEach(async () => {
  opened = [];
  window.addEventListener(OPEN_ENTITY_EVENT, capture);
  invalidateDocLinkCaches();
  await primeKnownWorkspaces();
});
afterEach(() => { window.removeEventListener(OPEN_ENTITY_EVENT, capture); cleanup(); });

// The shape of a reply the agent actually writes: one room named in bold, the other in backticks.
const REPLY = "I wrote the note into **vexa-team-a2a023** — your other room, `vexa-team-3183d1`, is untouched.";

describe("a canned reply → chips", () => {
  it("chips BOTH rooms, including the one this chat has not focused", () => {
    const out = transformDocRefs(REPLY);
    expect(out).toContain('<WorkspaceRef token="vexa-team-a2a023" />');
    expect(out).toContain('<WorkspaceRef token="vexa-team-3183d1" />');
  });

  it("still chips nothing it cannot name — an unknown room stays prose", () => {
    expect(transformDocRefs("I wrote it into **vexa-team-ffffff** instead"))
      .toBe("I wrote it into **vexa-team-ffffff** instead");
  });
});

describe("a chip → the room it names", () => {
  it("THE BUG: the parked room's chip asks for the PARKED room's README, exactly", async () => {
    render(<WorkspaceRef token="vexa-team-a2a023" />);
    fireEvent.click(screen.getByRole("link", { name: /vexa-team-a2a023/ }));

    expect(opened).toHaveLength(1);
    expect(opened[0]).toMatchObject({ path: "README.md", slug: "vexa-team-a2a023", exact: true });

    // and the resolver the shell hands it to agrees — no search, no primary, no neighbour
    expect(await resolveDocRef(opened[0], { path: opened[0].docPath, slug: opened[0].slug }))
      .toEqual({ path: "README.md", slug: "vexa-team-a2a023" });
  });

  it("does not over-correct: the FOCUSED room's chip still opens the focused room", async () => {
    render(<WorkspaceRef token="vexa-team-3183d1" />);
    fireEvent.click(screen.getByRole("link", { name: /vexa-team-3183d1/ }));
    expect(await resolveDocRef(opened[0], { path: opened[0].docPath, slug: opened[0].slug }))
      .toEqual({ path: "README.md", slug: "vexa-team-3183d1" });
  });

  it("and `personal` still means the desk — the private baseline, addressed with no slug", async () => {
    render(<WorkspaceRef token="personal" />);
    fireEvent.click(screen.getByRole("link", { name: /personal/ }));
    expect(opened[0]).toMatchObject({ path: "README.md", slug: undefined, exact: true });
    expect(await resolveDocRef(opened[0], { path: opened[0].docPath, slug: opened[0].slug }))
      .toEqual({ path: "README.md", slug: undefined });
  });

  it("an unknown token renders PLAIN TEXT — never a chip that opens somebody else's room", () => {
    const { container } = render(<WorkspaceRef token="vexa-team-ffffff" />);
    expect(container.textContent).toBe("vexa-team-ffffff");
    expect(screen.queryByRole("link")).toBeNull();
    expect(opened).toHaveLength(0);
  });
});
