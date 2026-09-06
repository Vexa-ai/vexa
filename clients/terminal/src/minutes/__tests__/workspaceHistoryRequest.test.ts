/** WHAT THE PAGE FILTER ACTUALLY PUTS ON THE WIRE (Vexa-ai/vexa#1628 point 2).
 *
 *  Founder, 2026-09-06, on `_global/README.md`: under *This page only* the list showed
 *  `kg/entities/decision/who-can-see-what.md — added` and `asks/prep.md` — commits that have nothing
 *  to do with the page in front of him. His reading: *"either the route ignores `path=` or the
 *  client never sends it."*
 *
 *  The panel's own test asserts that `readWorkspaceHistory` is CALLED with the path, and the route's
 *  pytest asserts that a `path=` narrows the answer. Neither of them covers the seam between: the
 *  URL this function builds. A mocked module cannot drop a query parameter, and that is exactly the
 *  way it could have been dropped — so the one test nobody had written is the one that reads the
 *  request.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { readWorkspaceHistory } from "../../surfaces/workspaceApi";

const answer = (over: Record<string, unknown> = {}) => ({
  ok: true, status: 200,
  json: async () => ({ slug: "oenb-b5e60c", branch: "main", path: null, limit: 11, commits: [], ...over }),
});

/** Every URL the call reached for, in order. */
function watchFetch(): string[] {
  const seen: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => { seen.push(String(url)); return answer(); }) as unknown as typeof fetch);
  return seen;
}

afterEach(() => { vi.unstubAllGlobals(); });

describe("the history request", () => {
  it("carries `path` when the reader asked for this page only", async () => {
    const seen = watchFetch();

    await readWorkspaceHistory("oenb-b5e60c", { path: "README.md", limit: 11 });

    expect(seen).toEqual(["/api/workspaces/oenb-b5e60c/git/history?path=README.md&limit=11"]);
  });

  it("carries no `path` at all when it is the whole workspace — never an empty one", async () => {
    const seen = watchFetch();

    await readWorkspaceHistory("oenb-b5e60c", { limit: 11 });

    expect(seen).toEqual(["/api/workspaces/oenb-b5e60c/git/history?limit=11"]);
    expect(seen[0]).not.toContain("path=");
  });

  it("escapes the slug and the path rather than pasting them into a URL", async () => {
    const seen = watchFetch();

    await readWorkspaceHistory("a b/c", { path: "kg/entities/acme & co.md", limit: 11 });

    expect(seen[0]).toBe("/api/workspaces/a%20b%2Fc/git/history?path=kg%2Fentities%2Facme+%26+co.md&limit=11");
  });

  it("refuses a 200 that is not a history rather than rendering an empty one", async () => {
    // "nothing ever happened in this workspace" is a claim, and a malformed body is not evidence for it.
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ slug: "x" }) })) as unknown as typeof fetch);

    await expect(readWorkspaceHistory("oenb-b5e60c", { path: "README.md" })).rejects.toThrow(/commits/);
  });
});
