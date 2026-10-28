/** EVERY WORKSPACE THE READER CAN WRITE IS IN THE "WRITES TO" MENU (founder, 2026-10-13).
 *
 *  The agent made "DNA check-in" in a chat. The Files pane listed it; the chat header's menu offered
 *  only the SHARED workspaces (its candidates were `memberships`, the shared-with-me listing), so a
 *  person's own private workspaces could never be chosen as where a chat writes. The candidates now
 *  include every workspace the server lists as writable for this reader (`all` on
 *  `/api/workspace/shared`), by its display name, and choosing one mounts it and makes it the target.
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { render, cleanup, fireEvent, act, screen } from "@testing-library/react";

import { ContextBar } from "../ContextBar";
import { writableFromListing } from "../../data/workspaceApi";

vi.mock("../../ui-kit/WsLink", () => ({ WorkspaceName: ({ slug }: { slug: string }) => <span>{slug}</span> }));
vi.mock("../../ui-kit/wsLinks", () => ({
  workspaceBySlug: async (slug: string) => ({ id: "aaaaaaaaaa", name: slug, kind: "group", slug,
    access: "readable", writable: true }),
}));

afterEach(cleanup);
const settle = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

const SEL = { kind: "chat" as const, chatId: "c1", label: "A chat", workspaces: ["personal", "_global"] };
const WRITABLE = [{ slug: "dna-check-in", name: "DNA check-in" }, { slug: "biz-b1c87f", name: "biz" }];

async function menu(onWriteTo = vi.fn(), onAddWorkspace = vi.fn()) {
  render(<ContextBar sel={SEL} flavor="chat" memberships={[{ workspace_id: "advisor-1", role: "viewer" }]}
    writable={WRITABLE} onWriteTo={onWriteTo} onAddWorkspace={onAddWorkspace} onRemoveWorkspace={vi.fn()}
    onSetTarget={vi.fn()} />);
  await settle();
  fireEvent.click(screen.getByRole("button", { name: "Workspaces for this chat" }));
  return { onWriteTo, onAddWorkspace };
}

describe("the Saves to menu", () => {
  it("lists the reader's own private workspaces by name, beside the shared ones", async () => {
    await menu();
    const rows = [...document.querySelectorAll("[data-ws-write]")].map((e) => e.textContent);
    expect(rows).toEqual(["biz", "DNA check-in"]);
    expect(document.body.textContent).toContain("advisor-1");
  });

  it("choosing one makes it where the chat writes", async () => {
    const { onWriteTo, onAddWorkspace } = await menu();
    fireEvent.click(document.querySelector('[data-ws-write="dna-check-in"]') as HTMLElement);
    expect(onWriteTo).toHaveBeenCalledWith("dna-check-in");
    expect(onAddWorkspace).not.toHaveBeenCalled();
  });
});

describe("the listing", () => {
  it("keeps what the reader can write and drops the personal and read-only rows", () => {
    expect(writableFromListing([
      { slug: "personal", name: "Personal", kind: "personal", role: "owner" },
      { slug: "dna-check-in", name: "DNA check-in", kind: "private", role: "owner" },
      { slug: "team-1", name: "Team", kind: "shared", role: "contributor" },
      { slug: "read-1", name: "Read", kind: "shared", role: "viewer" },
    ])).toEqual([{ slug: "dna-check-in", name: "DNA check-in" }, { slug: "team-1", name: "Team" }]);
  });
});
