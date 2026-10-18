/** Overlapping identical /api GETs share one request (founder 2026-10-16: a home load sent
 *  /api/connections/list four times). */
import { describe, expect, it, vi } from "vitest";
import { dedupeKey, dedupedFetch } from "../fetchDedupe";

describe("dedupedFetch", () => {
  it("sends one request for overlapping identical GETs; each caller reads its own body", async () => {
    let release!: () => void;
    const base = vi.fn(() => new Promise<Response>((res) => { release = () => res(new Response('{"a":1}')); }));
    const f = dedupedFetch(base as never);
    const a = f("/api/connections/list"), b = f("/api/connections/list", { cache: "no-store" });
    release();
    expect(await (await a).json()).toEqual({ a: 1 });
    expect(await (await b).json()).toEqual({ a: 1 });
    expect(base).toHaveBeenCalledTimes(1);
    const c = f("/api/connections/list");
    release();
    await c;
    expect(base).toHaveBeenCalledTimes(2);                       // finished → the next one is fresh
  });
  it("never merges writes, streams, signals or other origins", () => {
    expect(dedupeKey("/api/chat", { method: "POST", body: "{}" })).toBeNull();
    expect(dedupeKey("/api/chat/stream?x=1")).toBeNull();
    expect(dedupeKey("/api/x", { signal: new AbortController().signal })).toBeNull();
    expect(dedupeKey("https://elsewhere.test/api/x")).toBeNull();
    expect(dedupeKey("/static/x.js")).toBeNull();
    expect(dedupeKey("/api/desktop")).toBe("/api/desktop");
  });
});
