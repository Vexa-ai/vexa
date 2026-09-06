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
import { DocImage, fetchFailureLine, findItInstruction, removeImageReference, rewriteImageReference, workspaceAssetUrl } from "../docImages";
import { WORKSPACE_COMMIT_EVENT } from "../../canvas/actions";

const fetchWorkspaceAsset = vi.fn(async () => ({
  path: "assets/oenb-logo.svg", bytes: 12, source: "https://oenb.at/logo.svg",
  content_type: "image/svg+xml",
}));
const readWorkspaceFile = vi.fn(async () => "# Note\n\n![OeNB logo](https://oenb.at/logo.svg)\n");
const writeWorkspaceFile = vi.fn(async () => ({ path: "kg/note.md", written: true }));

const postIntent = vi.fn(() => null);
vi.mock("../../minutes/extend", () => ({ postIntent: (...a: unknown[]) => postIntent(...(a as [])) }));

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

beforeEach(() => {
  fetchWorkspaceAsset.mockClear(); readWorkspaceFile.mockClear(); writeWorkspaceFile.mockClear();
  postIntent.mockClear();
});
afterEach(cleanup);

/** An `ApiError` as `surfaces/apiClient` mints one — read duck-typed by `fetchFailureLine`, which
 *  is why this is a shape and not an import. */
const apiFailure = (status: number, body?: unknown, detail = "") =>
  Object.assign(new Error(`/api/workspace/asset → ${status}`), { status, detail, body });

/** The placeholder after its offer has failed — the state everything in #1624 is about.
 *
 *  `DocImage` directly and not through `MdxDoc`, for the reason the stored-copy test above gives:
 *  the MDX tree recompiles once when the workspace snapshot lands and replaces every component in
 *  it, which resets the state this is about. The failure's DURABLE half — the page the reader
 *  commits — is asserted through the API mocks, exactly as the fetch's is. */
const afterFailedFetch = async (
  meta: { path?: string; slug?: string } = { path: "kg/note.md", slug: "vexa-team-3183d1" },
  err: unknown = apiFailure(424, { detail: { upstream_status: 404, url: "https://oenb.at/logo.svg" } }),
): Promise<HTMLElement> => {
  fetchWorkspaceAsset.mockRejectedValueOnce(err);
  render(<DocMetaContext.Provider value={meta}>
    <DocImage src="https://oenb.at/logo.svg" alt="OeNB logo" />
  </DocMetaContext.Provider>);
  document.querySelector<HTMLButtonElement>("[data-image-fetch]")!.click();
  await waitFor(() => expect(document.querySelector("[data-image-failed]")).toBeTruthy());
  return document.querySelector<HTMLElement>("[data-external-image]")!;
};

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

/** THE FAILED FETCH (Vexa-ai/vexa#1624). The founder pressed the offer on an address the agent had
 *  invented and got, in red: *Could not fetch it: /api/workspace/asset → 400: https://…
 *  answered 404.* A route, two status codes and a URL, printed at a person who can only read it as
 *  "the button is broken". What is true is one sentence, and there are exactly two moves from it. */
describe("a failed fetch is a sentence and two acts, not a stack trace", () => {
  it("says what happened to the PICTURE, with the site's own status", async () => {
    const box = await afterFailedFetch();
    expect(box.textContent).toContain("This image does not exist at that address (the site answered 404).");
    expect(box.textContent).not.toContain("/api/workspace/asset");
    expect(box.textContent).not.toContain("424");
  });

  it("offers Find it and Remove the link, and nothing else", async () => {
    const box = await afterFailedFetch();
    expect(box.querySelector("[data-image-find]")).toBeTruthy();
    expect(box.querySelector("[data-image-drop]")).toBeTruthy();
    // the offer is gone: there is nothing left to fetch from an address that does not answer
    expect(box.querySelector("[data-image-fetch]")).toBeNull();
  });

  it("Find it queues a SAME-TARGET act naming the image and the page", async () => {
    const box = await afterFailedFetch();
    box.querySelector<HTMLButtonElement>("[data-image-find]")!.click();
    await waitFor(() => expect(postIntent).toHaveBeenCalled());
    const [intent] = postIntent.mock.calls[0] as unknown as [Record<string, string>];
    expect(intent.kind).toBe("extend");                       // a job on this chat, not a new one
    expect(intent.path).toBe("kg/note.md");                   // …on the page the reader is on
    expect(intent.workspace).toBe("vexa-team-3183d1");
    expect(intent.instruction).toContain("find the real OeNB logo image, fetch it into assets/, and fix the link");
    expect(intent.instruction).toContain("https://oenb.at/logo.svg");
  });

  it("Remove the link COMMITS the page without the image and keeps the words", async () => {
    readWorkspaceFile.mockResolvedValueOnce(
      "# OeNB\n\nThe logo is below.\n\n![OeNB logo](https://oenb.at/logo.svg)\n\nFounded in 1816.\n");
    const commits = vi.fn();
    window.addEventListener(WORKSPACE_COMMIT_EVENT, commits);
    const box = await afterFailedFetch();
    box.querySelector<HTMLButtonElement>("[data-image-drop]")!.click();
    await waitFor(() => expect(writeWorkspaceFile).toHaveBeenCalled());
    const [path, body] = writeWorkspaceFile.mock.calls[0] as unknown as [string, string];
    expect(path).toBe("kg/note.md");
    expect(body).not.toContain("https://oenb.at/logo.svg");
    expect(body).toContain("The logo is below.");
    expect(body).toContain("Founded in 1816.");
    await waitFor(() => expect(commits).toHaveBeenCalled());
    window.removeEventListener(WORKSPACE_COMMIT_EVENT, commits);
  });

  it("offers no act at all when the doc has no path — an act never guesses its target", async () => {
    const box = await afterFailedFetch({});
    expect(box.textContent).toContain("This image does not exist at that address");
    expect(box.querySelector("[data-image-find]")).toBeNull();
    expect(box.querySelector("[data-image-drop]")).toBeNull();
  });

  it("says the honest thing for each way a fetch can fail", () => {
    const up = (n: number) => apiFailure(n >= 500 ? 424 : 424, { detail: { upstream_status: n } });
    expect(fetchFailureLine(up(404), "oenb.at")).toContain("does not exist at that address");
    expect(fetchFailureLine(up(403), "oenb.at")).toBe("oenb.at will not hand this image over (it answered 403).");
    expect(fetchFailureLine(up(503), "oenb.at")).toBe("oenb.at is failing right now (it answered 503).");
    expect(fetchFailureLine(apiFailure(502, { detail: { upstream_status: null } }), "oenb.at"))
      .toBe("Nothing answered at oenb.at.");
    // our own refusal of the address is already a sentence written for a person — passed through
    expect(fetchFailureLine(apiFailure(400, { detail: "refusing 'redis' — that is an internal service name" },
      "refusing 'redis' — that is an internal service name"), "redis"))
      .toBe("refusing 'redis' — that is an internal service name");
  });

  it("takes the whole reference out, in both spellings, and leaves the page", () => {
    const src = "before\n\n![a](https://x/i.png)\n\nmiddle\n\n<img src=\"https://x/i.png\" alt=\"a\" />\n\nafter";
    const out = removeImageReference(src, "https://x/i.png");
    expect(out).not.toContain("https://x/i.png");
    expect(out).not.toContain("![a]");
    expect(out).not.toContain("<img");
    expect(out).toContain("before");
    expect(out).toContain("middle");
    expect(out).toContain("after");
  });

  it("names the picture by its own alt text, so the chat looks for the right thing", () => {
    expect(findItInstruction("OeNB logo", "https://x/i.svg"))
      .toBe("find the real OeNB logo image, fetch it into assets/, and fix the link — the page points at https://x/i.svg, which does not answer.");
    expect(findItInstruction(undefined, "https://x/i.svg")).toContain("find the real image");
  });
});
