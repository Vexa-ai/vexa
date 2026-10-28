/** A FILE REFERENCE NAMES ITS WORKSPACE, OR IT IS NOT A LINK (founder ruling 2026-10-18).
 *
 *  The bug: in the chat "Build Vexa narrative and launch plan" (writing to the Narrative workspace)
 *  the reply listed `README.md` beside the four files it had written; the chip opened "The
 *  organisation › README.md". A bare path was looked for in `_global` first, then every mount, and
 *  every workspace has a README. Founder: *"we must require full paths to specific workspaces, we do
 *  not want to guess which readme this should open"*.
 *
 *  Pinned end to end, from the words in a reply to what a click asks the shell to open:
 *   - a bare path in a chat never opens anything (no chip, no event) — in either renderer;
 *   - the same path inside a page opens in THAT page's workspace;
 *   - a qualified reference (`[[page:narrative/README.md]]`, a mount path) opens the one it names.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { DocMetaContext, DocPath, Wikilink, invalidateDocLinkCaches, metaOfOpen, resolveDocRef } from "../docLinks";
import { Markdown } from "../Markdown";
import { parseUniversalRef } from "../universalRefs";
import { pageForDocRef } from "../../minutes/roomView";
import { OPEN_ENTITY_EVENT } from "../../platform";

const trees: Record<string, string[]> = {
  "": ["README.md"],
  _global: ["README.md"],
  narrative: ["README.md", "narratives.md", "landscape.md", "playbook.md", "ideas.md"],
};
const active = [
  { slug: "seed", path: "/workspaces/57", primary: true },
  { slug: "narrative", path: "/workspaces/narrative" },
];
vi.mock("../../data/workspaceApi", () => ({
  listWorkspaceTree: vi.fn(async (opts?: { slug?: string }) => trees[opts?.slug ?? ""] ?? []),
  readActiveSet: vi.fn(async () => ({ subject: "57", active })),
  listSharedMemberships: vi.fn(async () => []),
}));

interface OpenDetail { path?: string; slug?: string; docPath?: string; exact?: boolean }
let opened: OpenDetail[] = [];
const capture = (e: Event) => { opened.push((e as CustomEvent<OpenDetail>).detail); };

beforeEach(() => {
  opened = [];
  invalidateDocLinkCaches();
  window.addEventListener(OPEN_ENTITY_EVENT, capture);
});
afterEach(() => { window.removeEventListener(OPEN_ENTITY_EVENT, capture); cleanup(); });

/** What the shell does with a click: resolve, then turn it into the page it opens (or nothing). */
async function landing(d: OpenDetail) {
  return pageForDocRef(d, await resolveDocRef(d, metaOfOpen(d)).catch(() => null));
}

describe("a bare ref never opens another workspace's file", () => {
  it("THE BUG: `README.md` in a chat reply is plain text, not a chip, and a click asks for nothing", async () => {
    const { container } = render(<DocPath path="README.md" />);
    const code = container.querySelector("code[data-unqualified]");
    expect(code?.textContent).toBe("README.md");
    expect(screen.queryByRole("link")).toBeNull();
    fireEvent.click(code!);
    await new Promise((r) => setTimeout(r, 0));
    expect(opened).toHaveLength(0);
  });

  it("the plain-Markdown renderer agrees — backticked and linked spellings both stay unlinked", () => {
    const { container } = render(<Markdown>{"Wrote `README.md` and [the plan](playbook.md)."}</Markdown>);
    expect(container.querySelectorAll("[data-unqualified]")).toHaveLength(2);
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("`[[README.md]]` written as a wikilink is no different", () => {
    const { container } = render(<Wikilink title="README.md" />);
    expect(container.querySelector("[data-unqualified]")?.textContent).toBe("README.md");
  });

  it("even if something dispatches a bare path, the shell opens nothing — not the home README, not _global", async () => {
    expect(await landing({ path: "README.md" })).toBeNull();
  });
});

describe("a qualified ref opens the right one", () => {
  it("`[[page:narrative/README.md]]` is a page reference into Narrative (the server answers it)", () => {
    expect(parseUniversalRef("[[page:narrative/README.md|README.md]]"))
      .toMatchObject({ kind: "page", id: "narrative/README.md", label: "README.md" });
  });

  it("a mount path opens the workspace it names", async () => {
    render(<DocPath path="/workspaces/narrative/README.md" />);
    fireEvent.click(screen.getByRole("link"));
    expect(opened).toHaveLength(1);
    expect(await landing(opened[0])).toEqual({ path: "README.md", slug: "narrative", label: "README" });
  });

  it("inside a Narrative PAGE, a bare `README.md` is that page's workspace — the context it was written in", async () => {
    render(<DocMetaContext.Provider value={{ path: "playbook.md", slug: "narrative" }}><DocPath path="README.md" /></DocMetaContext.Provider>);
    const link = await waitFor(() => screen.getByRole("link"));
    fireEvent.click(link);
    expect(opened[0]).toMatchObject({ path: "README.md", slug: "narrative" });
    expect(await landing(opened[0])).toEqual({ path: "README.md", slug: "narrative", label: "README" });
  });
});
