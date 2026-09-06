/** The usage signal, from the panel's side (founder refinement, 2026-09-02).
 *
 *  Three properties, and two of them are about restraint: it reports an ID (never a slug, because
 *  the README links by id and a slug does not survive a rename), it never reports the same page
 *  twice in a session, and it never lets a failure reach the person reading the document.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { reportOpened, resetReportedPages } from "../deskTouch";
import { invalidateWsLinkCaches } from "../../ui-kit/wsLinks";

let fetchMock: ReturnType<typeof vi.fn>;

function routes(map: Record<string, unknown>) {
  fetchMock = vi.fn(async (url: string) => {
    const key = Object.keys(map).find((k) => String(url).startsWith(k));
    if (!key) throw new Error(`no route: ${url}`);
    return { ok: true, status: 200, json: async () => map[key] } as unknown as Response;
  });
  globalThis.fetch = fetchMock as unknown as typeof fetch;
}
const posts = () => fetchMock.mock.calls.filter((c) => String(c[0]) === "/api/desk/touch");
/** `reportOpened` is fire-and-forget over two dynamic imports, so there is nothing to await. Poll
 *  the recorded calls instead of sleeping a guessed number of milliseconds — a fixed sleep either
 *  flakes on a cold module cache or slows every run to the worst case. */
async function settle(expected = 0): Promise<void> {
  for (let i = 0; i < 60; i++) {
    await new Promise((r) => setTimeout(r, 2));
    if (posts().length >= expected && i > 4) return;
  }
}

const WORLD = {
  "/api/workspace/active": { subject: "126", active: [] },
  "/api/workspaces/by-slug/126": { id: "aaaaaaaaaa", name: "olga@spi.com", kind: "desk", slug: "126", access: "readable" },
  "/api/workspaces/by-slug/grp": { id: "bbbbbbbbbb", name: "ASWF DNA Project", kind: "group", slug: "grp", access: "readable" },
  "/api/desk/touch": { recorded: true },
};

beforeEach(async () => {
  invalidateWsLinkCaches();
  resetReportedPages();
  // Warm the module cache so the first assertion is not racing vitest's first dynamic import.
  await import("../../surfaces/workspaceApi");
});
afterEach(() => vi.restoreAllMocks());

describe("reportOpened", () => {
  it("reports the workspace ID and the path, never the slug", async () => {
    routes(WORLD);
    reportOpened("grp", "kg/entities/person/cottalango-leon.md");
    await settle(1);
    expect(posts()).toHaveLength(1);
    expect(JSON.parse(String((posts()[0][1] as RequestInit).body)))
      .toEqual({ workspace: "bbbbbbbbbb", path: "kg/entities/person/cottalango-leon.md" });
  });

  it("resolves the reader's OWN desk when no slug is given", async () => {
    routes(WORLD);
    reportOpened(undefined, "README.md");
    await settle(1);
    expect(JSON.parse(String((posts()[0][1] as RequestInit).body)))
      .toEqual({ workspace: "aaaaaaaaaa", path: "README.md" });
  });

  it("does not report the same page twice — a tab strip re-render is not a second use", async () => {
    routes(WORLD);
    reportOpened("grp", "README.md");
    await settle(1);
    reportOpened("grp", "README.md");
    await settle(1);
    expect(posts()).toHaveLength(1);
  });

  it("reports a different page in the same workspace", async () => {
    routes(WORLD);
    reportOpened("grp", "README.md");
    await settle(1);
    reportOpened("grp", "kg/INDEX.md");
    await settle(2);
    expect(posts()).toHaveLength(2);
  });

  it("says nothing about a workspace this reader cannot read", async () => {
    routes({ ...WORLD, "/api/workspaces/by-slug/grp": { id: "bbbbbbbbbb", name: "x", kind: "group", access: "not-yours" } });
    reportOpened("grp", "README.md");
    await settle();
    expect(posts()).toHaveLength(0);
  });

  it("never throws, and never rejects, when everything fails", async () => {
    fetchMock = vi.fn(async () => { throw new Error("offline"); });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    expect(() => reportOpened("grp", "README.md")).not.toThrow();
    await settle();
  });

  it("ignores an empty path", async () => {
    routes(WORLD);
    reportOpened("grp", "   ");
    await settle();
    expect(posts()).toHaveLength(0);
  });
});
