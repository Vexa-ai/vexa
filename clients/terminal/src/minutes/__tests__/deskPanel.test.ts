/** The default right-panel page is the reader's DESK, and it is NAMED (PRD decision 26.4 · F49).
 *
 *  What it replaced: a chat with no focus opened `_global/README.md` — the ORGANISATION's page, the
 *  same for everybody — and the tabs were labelled by slug, so a desk's tab read `126`.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { deskLabel, deskPanelPages } from "../deskPanel";
import { invalidateWsLinkCaches } from "../../ui-kit/wsLinks";
import { WORKSPACE_WORD } from "../vocabulary";

let fetchMock: ReturnType<typeof vi.fn>;

/** Route by URL, because this module talks to two endpoints (the mount table and the registry). */
function routes(map: Record<string, unknown>, fail: string[] = []) {
  fetchMock = vi.fn(async (url: string) => {
    const key = Object.keys(map).find((k) => String(url).startsWith(k));
    if (!key || fail.some((f) => String(url).startsWith(f))) throw new Error(`no route: ${url}`);
    return { ok: true, status: 200, json: async () => map[key] } as unknown as Response;
  });
  globalThis.fetch = fetchMock as unknown as typeof fetch;
}

beforeEach(() => invalidateWsLinkCaches());
afterEach(() => vi.restoreAllMocks());

const ACTIVE = { "/api/workspace/active": { subject: "126", active: [] } };
const NAMES: Record<string, unknown> = {
  "/api/workspaces/by-slug/126": { id: "aaaaaaaaaa", name: "olga@spi.com", kind: "desk", slug: "126", access: "readable" },
  "/api/workspaces/by-slug/grp": { id: "bbbbbbbbbb", name: "ASWF DNA Project", kind: "group", slug: "grp", access: "readable" },
};


describe("deskPanelPages", () => {
  it("a chat with no workspaces opens the reader's own desk README, not the organisation's", async () => {
    routes({ ...ACTIVE, ...NAMES });
    const pages = await deskPanelPages([]);
    expect(pages[0]).toEqual({ path: "README.md", label: "olga@spi.com" });
    expect(pages[0].slug).toBeUndefined();                 // the reader's own — a no-slug read
    expect(pages.at(-1)).toEqual({ path: "README.md", slug: "_global", label: "_global" });
  });

  it("the desk still leads when the chat stresses a group, and the group is NAMED", async () => {
    routes({ ...ACTIVE, ...NAMES });
    const pages = await deskPanelPages(["grp", "_global"]);
    expect(pages.map((p) => p.label)).toEqual(["olga@spi.com", "ASWF DNA Project", "_global"]);
    expect(pages[1].slug).toBe("grp");
  });

  it("`personal` is the desk, not a second tab", async () => {
    routes({ ...ACTIVE, ...NAMES });
    const pages = await deskPanelPages(["personal"]);
    expect(pages.map((p) => p.label)).toEqual(["olga@spi.com", "_global"]);
  });

  it("`_system` is never a tab — always mounted, never chosen", async () => {
    routes({ ...ACTIVE, ...NAMES });
    const pages = await deskPanelPages(["_system"]);
    expect(pages.some((p) => p.slug === "_system")).toBe(false);
  });

  it("an unnamed workspace falls back to its slug — a worse label, never a blank tab", async () => {
    routes({ ...ACTIVE, "/api/workspaces/by-slug/": { id: "cccccccccc", name: null, kind: "group", access: "not-yours" } });
    const pages = await deskPanelPages(["grp"]);
    expect(pages[1].label).toBe("grp");
  });

  it("the panel still opens when the mount table cannot be read", async () => {
    routes({ ...NAMES }, ["/api/workspace/active"]);
    expect(await deskLabel()).toBe(WORKSPACE_WORD);
    const pages = await deskPanelPages([]);
    expect(pages[0]).toEqual({ path: "README.md", label: WORKSPACE_WORD });
  });
});
