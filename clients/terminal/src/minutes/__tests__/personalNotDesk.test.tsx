/** THE PERSON'S OWN WORKSPACE IS "PERSONAL", NEVER "DESK" (founder, 2026-10-11).
 *
 *  *"rename desk to personal to remove ambiguity"* — the navigator row said "Desk" while the chat's
 *  target chip said "Writes to personal". The words a person reads say Personal; ids, slugs and
 *  fields keep `desk`. Rendered through `PagesPanel`, because the claim is about what is on screen.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { PagesPanel } from "../PagesPanel";
import * as nav from "../navigatorApi";
import { eyebrow, placeWord } from "../workspaceFrontPage";
import { homeEntry } from "../chats";
import { WORKSPACE_WORD } from "../vocabulary";

vi.mock("../navigatorApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../navigatorApi")>()),
  loadNavWorkspaces: vi.fn(),
  loadNavTree: vi.fn(),
}));

const DESK_WORD = /\bdesks?\b/i;

beforeEach(async () => {
  localStorage.clear();
  const real = await vi.importActual<typeof import("../navigatorApi")>("../navigatorApi");
  // the real list, from a mount set whose home carries no name: the fallback is what is on screen
  vi.mocked(nav.loadNavWorkspaces).mockReset().mockResolvedValue(real.buildWorkspaces({}));
  vi.mocked(nav.loadNavTree).mockReset().mockResolvedValue(["README.md"]);
});
afterEach(cleanup);

describe("no navigator or header text says desk", () => {
  it("the navigator's own row reads Personal", async () => {
    const { container } = render(
      <PagesPanel pages={[{ path: "README.md", label: homeEntry([]).label }]} docPath="README.md"
                  onOpen={vi.fn()} body={"# Personal"} />);
    fireEvent.click(container.querySelector("[data-nav-toggle]") as HTMLElement);
    await screen.findAllByText("Personal");
    expect(container.textContent ?? "").not.toMatch(DESK_WORD);
  });

  it("the header words for a person's own workspace", () => {
    expect(eyebrow("desk")).toBe("Your personal workspace");
    for (const w of [eyebrow("desk"), placeWord("desk"), homeEntry([]).label ?? "", WORKSPACE_WORD]) {
      expect(w).not.toMatch(DESK_WORD);
    }
  });
});
