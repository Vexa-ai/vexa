/** One read per page at a time (2026-10-18): the workspace page fired five identical
 *  `POST /api/desktop/page` (and as many workspace attention reads) at once, and the server did the
 *  same 15-30 s of work for each. Concurrent and just-answered reads now share one request. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { readPage, resetPageReads } from "../ReadmeView";
import { readWorkspaceAttention, resetAttention } from "../../desktop/home2/attentionApi";

afterEach(() => { resetPageReads(); resetAttention(); vi.unstubAllGlobals(); vi.useRealTimers(); });

const tree = { v: 1, workspace: "w", path: "README.md", nodes: [], refs: {} };

function slow(body: unknown, status = 200) {
  const resolvers: (() => void)[] = [];
  const f = vi.fn(() => new Promise<Response>((ok) => { resolvers.push(() => ok(new Response(JSON.stringify(body), { status }))); }));
  return { f, release: () => resolvers.splice(0).forEach((r) => r()) };
}

describe("readPage", () => {
  it("five concurrent reads of one page are one request, and all get its answer", async () => {
    const { f, release } = slow(tree);
    const reads = [1, 2, 3, 4, 5].map(() => readPage("w", "README.md", f as unknown as typeof fetch));
    release();
    const got = await Promise.all(reads);
    expect(f).toHaveBeenCalledTimes(1);
    expect(got.every((t) => t?.workspace === "w")).toBe(true);
  });

  it("an answer is reused briefly, then read again; another page is its own read", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    const f = vi.fn(async () => new Response(JSON.stringify(tree), { status: 200 }));
    const ff = f as unknown as typeof fetch;
    await readPage("w", "README.md", ff);
    await readPage("w", "README.md", ff);
    expect(f).toHaveBeenCalledTimes(1);
    await readPage("w", "notes.md", ff);
    expect(f).toHaveBeenCalledTimes(2);
    vi.setSystemTime(Date.now() + 6_000);
    await readPage("w", "README.md", ff);
    expect(f).toHaveBeenCalledTimes(3);
    await readPage("w", "README.md", ff, { force: true });
    expect(f).toHaveBeenCalledTimes(4);
  });

  it("a failed read is never kept", async () => {
    const f = vi.fn(async () => new Response("{}", { status: 503 }));
    expect(await readPage("w", "README.md", f as unknown as typeof fetch)).toBeNull();
    expect(await readPage("w", "README.md", f as unknown as typeof fetch)).toBeNull();
    expect(f).toHaveBeenCalledTimes(2);
  });
});

describe("readWorkspaceAttention", () => {
  it("the page and its view blocks share one read of the workspace", async () => {
    const { f, release } = slow({ generated_at: 1, needs: [], workspaces: { w: { needs: [], working: [], rooms_active: 0, live_call: null, last: null, rooms: [] } } });
    vi.stubGlobal("fetch", f);
    const reads = [1, 2, 3].map(() => readWorkspaceAttention("w"));
    release();
    const got = await Promise.all(reads);
    expect(f).toHaveBeenCalledTimes(1);
    expect(got.every((a) => Array.isArray(a?.rooms))).toBe(true);
    await readWorkspaceAttention("w");
    expect(f).toHaveBeenCalledTimes(1);
    const again = readWorkspaceAttention("w", { force: true });
    release();
    await again;
    expect(f).toHaveBeenCalledTimes(2);
  });
});
