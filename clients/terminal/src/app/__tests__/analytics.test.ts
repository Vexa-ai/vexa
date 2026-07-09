/** analytics — endpoint normalization (keeps GA dimensions low-cardinality) + the gtag forwarding that
 *  no-ops when GA isn't configured (so call sites never have to guard). */
import { describe, it, expect, vi, afterEach } from "vitest";
import { endpointLabel, track, gaReady } from "../analytics";

afterEach(() => { delete (window as unknown as { gtag?: unknown }).gtag; });

describe("analytics", () => {
  it("endpointLabel drops the query and collapses numeric ids + uuids", () => {
    expect(endpointLabel("/api/workspace/file?path=a%2Fb.md")).toBe("/api/workspace/file");
    expect(endpointLabel("/api/meetings/12345")).toBe("/api/meetings/:id");
    expect(endpointLabel("/api/x/3f2504e0-4f89-11d3-9a0c-0305e82c3301/y")).toBe("/api/x/:id/y");
    expect(endpointLabel("/api/sessions")).toBe("/api/sessions");  // nothing to collapse
  });

  it("track no-ops without gtag, then forwards once GA is loaded", () => {
    expect(gaReady()).toBe(false);
    expect(() => track("e", { a: 1 })).not.toThrow();   // safe before GA loads

    const gtag = vi.fn();
    (window as unknown as { gtag: typeof gtag }).gtag = gtag;
    expect(gaReady()).toBe(true);
    track("e", { a: 1 });
    expect(gtag).toHaveBeenCalledWith("event", "e", { a: 1 });
  });
});
