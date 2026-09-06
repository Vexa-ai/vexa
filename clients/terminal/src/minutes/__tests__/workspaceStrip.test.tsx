/** THE WORKSPACE FRONT PAGE IS A HEADER STRIP (Vexa-ai/vexa#1628).
 *
 *  Founder, 2026-09-06, on `_global/README.md`, looking at what #1623 had just built: *"ups, the
 *  workspace panel should take only the header, 1/8 screen at max, the rest collapsed."*
 *
 *  Four claims, and each of them has a plausible wrong answer that would still look fine in a
 *  screenshot of an empty workspace:
 *
 *    · THE HEIGHT IS BOUNDED BY THE VIEWPORT, not by how little this particular workspace happens to
 *      have in it. A panel that fits today because the roster is empty is not a panel that obeys the
 *      rule; the cap has to be in the style, against `vh`, and it has to be inside 1/8.
 *    · NOTHING IS OPEN BY DEFAULT — and *closed* means the section's content is not in the document
 *      at all, not that it is hidden with CSS. A collapsed section that still renders is a section
 *      that still costs height the moment any style is wrong.
 *    · A DISCLOSURE OPENS AND CLOSES, and opening the next one puts the first away: two open
 *      sections are the beginning of the wall this issue exists to remove.
 *    · THE POSTURE IS REMEMBERED PER WORKSPACE AND IN THE BROWSER ONLY. Which section a person had
 *      open is not a fact about the workspace and never reaches the server.
 *
 *  Rendered through `PagesPanel`, like the panel's own tests, because the claim is about what a
 *  reader meets on the page — not about a component in isolation.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import * as api from "../../surfaces/workspaceApi";

vi.mock("../../surfaces/workspaceApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../surfaces/workspaceApi")>()),
  readWorkspaceBySlug: vi.fn(),
  listWorkspaceTree: vi.fn(),
  readWorkspaceHistory: vi.fn(),
  gitRemoteStatus: vi.fn(),
  readWorkspaceFile: vi.fn(),
  listSharedMemberships: vi.fn(),
  listWorkspaceMembers: vi.fn(),
}));

import { PagesPanel } from "../PagesPanel";
import { STRIP_MAX_VH } from "../WorkspaceReadmePanel";
import type { Page } from "../types";

const COMMITS = [
  { sha: "7f6b769", msg: "readme: link the entity", when: "2 hours ago", author: "126", kind: "you" as const, files: ["README.md"] },
];

const READ_ME: Page[] = [{ path: "README.md", slug: "oenb-b5e60c", label: "OeNB" }];

const panel = (over: Partial<Parameters<typeof PagesPanel>[0]> = {}) =>
  render(<PagesPanel pages={READ_ME} docPath="README.md" docSlug="oenb-b5e60c" onOpen={() => {}}
    body={"# OeNB\n\nThe workspace body."} {...over} />);

const strip = (c: HTMLElement) => c.querySelector("[data-ws-strip]") as HTMLElement | null;
const disclosures = (c: HTMLElement) => [...c.querySelectorAll<HTMLButtonElement>("[data-ws-disclosure]")];
const openIds = (c: HTMLElement) => [...c.querySelectorAll("[data-ws-section]")].map((s) => s.getAttribute("data-ws-section"));

beforeEach(() => {
  window.localStorage.clear();
  vi.mocked(api.readWorkspaceBySlug).mockResolvedValue({ id: "w1", name: "OeNB", kind: "group", slug: "oenb-b5e60c", access: "readable", writable: false });
  vi.mocked(api.listWorkspaceTree).mockResolvedValue(["README.md", "kg/entities/acme.md"]);
  vi.mocked(api.readWorkspaceHistory).mockResolvedValue({ slug: "oenb-b5e60c", branch: "main", path: null, limit: 11, commits: COMMITS });
  vi.mocked(api.gitRemoteStatus).mockResolvedValue({ has_home: true, remote: "origin", url: "https://github.com/oenb/kg", branch: "main", tracked: true, ahead: 2, behind: 0 });
  vi.mocked(api.readWorkspaceFile).mockResolvedValue("# Policies\n");
  vi.mocked(api.listSharedMemberships).mockResolvedValue([{ workspace_id: "oenb-b5e60c", role: "owner" }]);
  vi.mocked(api.listWorkspaceMembers).mockResolvedValue([{ subject: "126", role: "owner", email: "dmitry@vexa.ai" }]);
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
    ok: true, json: async () => (String(url).includes("/api/auth/me") ? { is_admin: false } : []),
  })) as unknown as typeof fetch);
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); window.localStorage.clear(); });

describe("the strip takes the header and no more", () => {
  it("caps itself against the VIEWPORT, and the cap is inside the founder's 1/8", async () => {
    // A viewport this test states rather than inherits: the claim is a fraction of the screen, so a
    // jsdom default of 768 would be measuring a coincidence.
    Object.defineProperty(window, "innerHeight", { value: 900, configurable: true });

    const { container } = panel();
    await waitFor(() => expect(strip(container)).toBeTruthy());

    const s = strip(container)!;
    expect(s.style.maxHeight).toBe(`${STRIP_MAX_VH}vh`);      // the cap is in the style, not in luck
    expect(s.style.overflow).toBe("hidden");                  // …and nothing leaks past it
    // …and the cap the style names is inside "1/8 screen at max" on the viewport above
    expect((STRIP_MAX_VH / 100) * window.innerHeight).toBeLessThanOrEqual(window.innerHeight / 8);
  });

  it("renders NO section content until a disclosure is opened", async () => {
    const { container } = panel();
    await waitFor(() => expect(disclosures(container).length).toBe(6));

    expect(openIds(container)).toEqual([]);
    // not hidden — absent: the roster, the remote and the commit list are not in the document
    expect(container.querySelector("[data-ws-history]")).toBeNull();
    expect(container.querySelector("[data-ws-member]")).toBeNull();
    expect(container.querySelector('[data-ws-fact="remote"]')).toBeNull();
    expect(container.querySelectorAll("[data-ws-act]")).toHaveLength(0);
    // …while the strip has already ANSWERED all six questions
    expect(disclosures(container).every((b) => b.getAttribute("aria-expanded") === "false")).toBe(true);
    expect(container.textContent).toContain("Shared workspace");
    expect(container.textContent).toContain("main, 2 ahead");
    expect(container.textContent).toContain("1 member, you owner");
  });

  it("opens the section its summary belongs to, closes it on a second click, and holds one at a time", async () => {
    const { container } = panel();
    await waitFor(() => expect(disclosures(container).length).toBe(6));

    fireEvent.click(container.querySelector('[data-ws-disclosure="github"]')!);
    expect(openIds(container)).toEqual(["github"]);
    await waitFor(() => expect(container.querySelector('[data-ws-fact="remote"]')).toBeTruthy());
    expect(container.querySelector('[data-ws-disclosure="github"]')!.getAttribute("aria-expanded")).toBe("true");

    // a second disclosure REPLACES it — two open sections are the wall this issue removed
    fireEvent.click(container.querySelector('[data-ws-disclosure="shared"]')!);
    expect(openIds(container)).toEqual(["shared"]);
    expect(container.querySelector('[data-ws-fact="remote"]')).toBeNull();

    // …and clicking the open one closes it
    fireEvent.click(container.querySelector('[data-ws-disclosure="shared"]')!);
    expect(openIds(container)).toEqual([]);
  });
});

describe("the open section is remembered per workspace, in the browser only", () => {
  it("comes back on the next visit to the SAME workspace, and nothing is open on another", async () => {
    const first = panel();
    await waitFor(() => expect(disclosures(first.container).length).toBe(6));
    fireEvent.click(first.container.querySelector('[data-ws-disclosure="history"]')!);
    expect(openIds(first.container)).toEqual(["history"]);
    cleanup();

    const again = panel();
    await waitFor(() => expect(openIds(again.container)).toEqual(["history"]));
    cleanup();

    // another workspace is another posture — and its default is still nothing open
    vi.mocked(api.readWorkspaceBySlug).mockResolvedValue({ id: "w2", name: "Acme", kind: "group", slug: "acme-1", access: "readable", writable: false });
    const other = panel({ pages: [{ path: "README.md", slug: "acme-1", label: "Acme" }], docSlug: "acme-1" });
    await waitFor(() => expect(disclosures(other.container).length).toBe(6));
    expect(openIds(other.container)).toEqual([]);
  });

  it("writes the posture nowhere but this browser", async () => {
    const { container } = panel();
    await waitFor(() => expect(disclosures(container).length).toBe(6));

    fireEvent.click(container.querySelector('[data-ws-disclosure="pages"]')!);

    expect(window.localStorage.getItem("vexa.wsreadme.open:oenb-b5e60c")).toBe("pages");
    // the server was asked nothing: every call this panel made was a READ of the workspace
    const posted = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls
      .filter((c) => (c[1] as RequestInit | undefined)?.method && (c[1] as RequestInit).method !== "GET");
    expect(posted).toEqual([]);
  });
});

describe("the strip is one tab stop with buttons inside it", () => {
  it("puts exactly one disclosure in the tab order, and the arrows move it", async () => {
    const { container } = panel();
    await waitFor(() => expect(disclosures(container).length).toBe(6));

    expect(strip(container)!.getAttribute("role")).toBe("toolbar");
    expect(disclosures(container).map((b) => b.tabIndex)).toEqual([0, -1, -1, -1, -1, -1]);
    expect(disclosures(container).every((b) => b.tagName === "BUTTON")).toBe(true);

    fireEvent.keyDown(strip(container)!, { key: "ArrowRight" });
    expect(disclosures(container).map((b) => b.tabIndex)).toEqual([-1, 0, -1, -1, -1, -1]);

    fireEvent.keyDown(strip(container)!, { key: "End" });
    expect(disclosures(container).map((b) => b.tabIndex)).toEqual([-1, -1, -1, -1, -1, 0]);
  });
});

describe("the strip says the six things, each as an answer", () => {
  it("kind · pages · last change · shared with · the repo · a commit count", async () => {
    const { container } = panel();
    await waitFor(() => expect(disclosures(container).length).toBe(6));

    const say = (id: string) => container.querySelector(`[data-ws-disclosure="${id}"]`)?.textContent ?? "";
    expect(say("this")).toContain("Shared workspace");
    expect(say("pages")).toContain("2 pages");
    expect(say("last")).toContain("readme: link the entity");
    expect(say("last")).toContain("126");
    expect(say("last")).toContain("2 hours ago");
    expect(say("shared")).toContain("1 member, you owner");
    expect(say("github")).toContain("main, 2 ahead");
    await waitFor(() => expect(say("history")).toContain("1 commit"));
  });

  it("says a read failed rather than rendering a zero", async () => {
    vi.mocked(api.listWorkspaceTree).mockRejectedValue(new Error("boom"));
    const { container } = panel();
    await waitFor(() => expect(disclosures(container).length).toBe(6));

    expect(container.querySelector('[data-ws-disclosure="pages"]')?.textContent).toContain("not readable");
    await screen.findByText("Could not count the pages.");
  });
});
