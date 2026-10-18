/** A PAGE'S LITERAL `<img>` AND `<a>` GO THROUGH THE SAME DOORS AS ITS MARKDOWN.
 *
 *  MDX maps markdown-made elements through `components`, but a tag written as JSX in the page is
 *  rendered as-is. So `![x](https://…)` became DocImage (named, never loaded) while `<img src=https://…>`
 *  loaded straight from the remote host, and `<a href=…>` skipped ExternalLink's scheme check.
 *  Pinned here: both literal spellings render through the same components as the markdown ones. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";
import { MdxDoc } from "../MdxDoc";
import { DocMetaContext } from "../docRefs";

vi.mock("../../data/workspaceApi", () => ({
  listWorkspaceTree: vi.fn(async () => []),
  readActiveSet: vi.fn(async () => ({ subject: "57", active: [] })),
  listSharedMemberships: vi.fn(async () => []),
  fetchWorkspaceAsset: vi.fn(async () => ({})),
  readWorkspaceFile: vi.fn(async () => ""),
  writeWorkspaceFile: vi.fn(async () => ({})),
}));

const inDoc = (md: string) =>
  render(<DocMetaContext.Provider value={{ path: "kg/note.md" }}><MdxDoc>{md}</MdxDoc></DocMetaContext.Provider>);

const rendered = () => waitFor(() => {
  const el = document.querySelector(".vx-prose");
  expect(el).toBeTruthy();
  return el!;
});

afterEach(cleanup);

describe("a literal <img> in a page", () => {
  it("is named as external, never requested from the remote host", async () => {
    inDoc('Logo:\n\n<img src="https://remote.example/pixel.png" alt="logo" />\n');
    await rendered();
    await waitFor(() => expect(document.querySelector("[data-external-image]")).toBeTruthy());
    const imgs = Array.from(document.querySelectorAll("img"));
    expect(imgs.filter((i) => (i.getAttribute("src") ?? "").includes("remote.example"))).toHaveLength(0);
  });

  it("inline in a sentence too", async () => {
    inDoc('Here <img src="https://remote.example/inline.png" /> it is.\n');
    await rendered();
    await waitFor(() => expect(document.querySelector("[data-external-image]")).toBeTruthy());
    expect(document.querySelector('img[src*="remote.example"]')).toBeNull();
  });
});

describe("a literal <a> in a page", () => {
  it("an external link opens through ExternalLink (new tab, no opener)", async () => {
    inDoc('See <a href="https://remote.example/x">this</a>.\n');
    const prose = await rendered();
    const a = await waitFor(() => { const x = prose.querySelector('a[href="https://remote.example/x"]'); expect(x).toBeTruthy(); return x!; });
    expect(a.getAttribute("rel")).toContain("noopener");
    expect(a.getAttribute("target")).toBe("_blank");
  });

  it("an unsafe scheme keeps no live href", async () => {
    inDoc('See <a href="vbscript:run">this</a> and <a href="//other.example/x">that</a>.\n');
    const prose = await rendered();
    await waitFor(() => expect(prose.textContent).toContain("this"));
    expect(prose.querySelector('a[href^="vbscript"]')).toBeNull();
    expect(prose.querySelector('a[href^="//"]')).toBeNull();
  });
});
