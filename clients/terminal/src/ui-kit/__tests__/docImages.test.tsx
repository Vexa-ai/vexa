/** IMAGES ON PAGES (Vexa-ai/vexa#1612).
 *
 *  Founder, 2026-09-06, on a customer workspace README the agent had written: the page showed
 *  `![OeNB logo](…)` as its alt text and a broken-image icon — *"we want to be able images"*.
 *
 *  The claims, in the order they matter:
 *
 *   1. a WORKSPACE path renders a picture, through the scoped asset route and carrying the doc's
 *      workspace — a page open in a shared room must not read its picture out of the reader's own;
 *   2. a REMOTE url renders NOTHING from that host. It is named as external and offered, because
 *      the whole point is that a document in a customer's workspace never sends their browser to a
 *      third party;
 *   3. taking the offer STORES the bytes AND rewrites the page, so the next reader (and every
 *      export of that document) gets the workspace copy;
 *   4. both renderers agree — MDX and the plain-Markdown fallback are one component, so a doc that
 *      fails to compile still shows the picture and still offers the fetch. The fallback is where
 *      this was worst: it matched `![alt](src)` as a LINK and printed a stray `!`.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { Markdown } from "../Markdown";
import { MdxDoc } from "../MdxDoc";
import { DocMetaContext } from "../docRefs";
import { DocImage, rewriteImageReference, workspaceAssetUrl } from "../docImages";
import { WORKSPACE_COMMIT_EVENT } from "../../canvas/actions";

const fetchWorkspaceAsset = vi.fn(async () => ({
  path: "assets/oenb-logo.svg", bytes: 12, source: "https://oenb.at/logo.svg",
  content_type: "image/svg+xml",
}));
const readWorkspaceFile = vi.fn(async () => "# Note\n\n![OeNB logo](https://oenb.at/logo.svg)\n");
const writeWorkspaceFile = vi.fn(async () => ({ path: "kg/note.md", written: true }));

vi.mock("../../surfaces/workspaceApi", () => ({
  // the chips resolve against a workspace snapshot; nothing here is about them, so the world is empty
  listWorkspaceTree: vi.fn(async () => []),
  readActiveSet: vi.fn(async () => ({ subject: "57", active: [] })),
  listSharedMemberships: vi.fn(async () => []),
  fetchWorkspaceAsset: (...a: unknown[]) => fetchWorkspaceAsset(...(a as [])),
  readWorkspaceFile: (...a: unknown[]) => readWorkspaceFile(...(a as [])),
  writeWorkspaceFile: (...a: unknown[]) => writeWorkspaceFile(...(a as [])),
}));

const inDoc = (node: React.ReactNode, meta: { path?: string; slug?: string } = {}) =>
  render(<DocMetaContext.Provider value={meta}>{node}</DocMetaContext.Provider>);

/** MDX compiles asynchronously, so the placeholder is not there on the first tick — and `waitFor`
 *  only retries a callback that THROWS, which is why this asserts inside it rather than returning a
 *  `null` the runner would happily accept as a result. */
const placeholder = async (): Promise<HTMLElement> =>
  waitFor(() => {
    const el = document.querySelector<HTMLElement>("[data-external-image]");
    expect(el).toBeTruthy();
    return el!;
  });

beforeEach(() => { fetchWorkspaceAsset.mockClear(); readWorkspaceFile.mockClear(); writeWorkspaceFile.mockClear(); });
afterEach(cleanup);

describe("a workspace path is a picture, served by the page's own door", () => {
  it("renders an <img> pointed at the asset route", async () => {
    inDoc(<MdxDoc>{"![OeNB logo](assets/oenb-logo.svg)"}</MdxDoc>, { path: "kg/note.md" });
    const img = await screen.findByAltText<HTMLImageElement>("OeNB logo");
    expect(img.getAttribute("src")).toBe("/api/workspace/asset?path=assets%2Foenb-logo.svg");
    expect(img.getAttribute("data-workspace-image")).toBe("assets/oenb-logo.svg");
  });

  it("carries the doc's WORKSPACE — a shared page reads its picture from the shared room", async () => {
    inDoc(<MdxDoc>{"![logo](assets/l.png)"}</MdxDoc>, { path: "README.md", slug: "vexa-team-3183d1" });
    const img = await screen.findByAltText<HTMLImageElement>("logo");
    expect(img.getAttribute("src")).toContain("slug=vexa-team-3183d1");
  });

  it("resolves a relative path against the doc that names it", async () => {
    inDoc(<MdxDoc>{"![chart](./img/q3.png)"}</MdxDoc>, { path: "kg/entities/company/oenb.md" });
    const img = await screen.findByAltText<HTMLImageElement>("chart");
    expect(decodeURIComponent(img.getAttribute("src") ?? ""))
      .toContain("path=kg/entities/company/img/q3.png");
  });

  it("builds the same url the renderer uses", () => {
    expect(workspaceAssetUrl("assets/a b.png", { slug: "_global" }))
      .toBe("/api/workspace/asset?path=assets%2Fa%20b.png&slug=_global");
  });
});

describe("a remote url is named, never loaded", () => {
  it("renders a placeholder that says it is external and offers to fetch it", async () => {
    inDoc(<MdxDoc>{"![OeNB logo](https://oenb.at/logo.svg)"}</MdxDoc>, { path: "kg/note.md" });
    const box = await placeholder();
    expect(box).toBeTruthy();
    expect(box.textContent).toContain("External image");
    expect(box.textContent).toContain("oenb.at");
    // NOTHING is requested from that host: no <img> exists at all until somebody asks
    expect(document.querySelector("img")).toBeNull();
    expect(box.querySelector("[data-image-fetch]")).toBeTruthy();
  });

  it("taking the offer stores the bytes AND rewrites the page's reference", async () => {
    const commits = vi.fn();
    window.addEventListener(WORKSPACE_COMMIT_EVENT, commits);
    inDoc(<MdxDoc>{"![OeNB logo](https://oenb.at/logo.svg)"}</MdxDoc>,
          { path: "kg/note.md", slug: "vexa-team-3183d1" });
    const button = (await placeholder()).querySelector<HTMLButtonElement>("[data-image-fetch]")!;
    button.click();
    await waitFor(() => expect(writeWorkspaceFile).toHaveBeenCalled());
    expect(fetchWorkspaceAsset).toHaveBeenCalledWith("https://oenb.at/logo.svg", { slug: "vexa-team-3183d1" });
    const [path, body] = writeWorkspaceFile.mock.calls[0] as unknown as [string, string];
    expect(path).toBe("kg/note.md");
    expect(body).toContain("![OeNB logo](assets/oenb-logo.svg)");
    expect(body).not.toContain("https://oenb.at/logo.svg");
    // …and the shell is told the open document moved, so the page in front of the reader is re-read
    // rather than left showing a placeholder for a picture that is now in the workspace
    await waitFor(() => expect(commits).toHaveBeenCalled());
    window.removeEventListener(WORKSPACE_COMMIT_EVENT, commits);
  });

  it("shows the workspace copy as soon as it is stored, without waiting for the re-read", async () => {
    // DocImage directly, not through MdxDoc: a recompile of the MDX tree (which happens once, when
    // the workspace snapshot lands) replaces every component in it and would reset this state — the
    // durable answer is the re-read asserted above, and this is the half-second before it.
    render(<DocMetaContext.Provider value={{}}>
      <DocImage src="https://oenb.at/logo.svg" alt="OeNB logo" />
    </DocMetaContext.Provider>);
    document.querySelector<HTMLButtonElement>("[data-image-fetch]")!.click();
    const img = await screen.findByAltText<HTMLImageElement>("OeNB logo");
    expect(img.getAttribute("src")).toContain("assets%2Foenb-logo.svg");
  });

  it("rewrites every occurrence, because the reader asked about the image and not about one line", () => {
    const src = "![a](https://x/i.png)\n\ntext\n\n<img src=\"https://x/i.png\" />";
    expect(rewriteImageReference(src, "https://x/i.png", "assets/i.png"))
      .toBe("![a](assets/i.png)\n\ntext\n\n<img src=\"assets/i.png\" />");
  });
});

describe("the plain-Markdown fallback shows the same picture", () => {
  it("no longer prints a stray `!` and a link where an image was", async () => {
    inDoc(<Markdown>{"![OeNB logo](assets/oenb-logo.svg)"}</Markdown>, { path: "kg/note.md" });
    const img = await screen.findByAltText<HTMLImageElement>("OeNB logo");
    expect(img.getAttribute("src")).toContain("assets%2Foenb-logo.svg");
    expect(document.body.textContent).not.toContain("!");
  });

  it("offers the same fetch for a remote one", async () => {
    inDoc(<Markdown>{"![logo](https://oenb.at/logo.svg)"}</Markdown>, { path: "kg/note.md" });
    const box = await placeholder();
    expect(box.querySelector("[data-image-fetch]")).toBeTruthy();
    expect(document.querySelector("img")).toBeNull();
  });

  it("still renders an ordinary link as a link", () => {
    inDoc(<Markdown>{"[OeNB](https://oenb.at)"}</Markdown>);
    expect(document.querySelector("[data-external-image]")).toBeNull();
    expect(screen.getByText("OeNB")).toBeTruthy();
  });
});
