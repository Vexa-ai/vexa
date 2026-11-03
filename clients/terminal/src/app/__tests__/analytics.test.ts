/** analytics — endpoint normalization (keeps GA dimensions low-cardinality) + the gtag forwarding that
 *  no-ops when GA isn't configured (so call sites never have to guard). */
import { describe, it, expect, vi, afterEach } from "vitest";
import { endpointLabel, track, gaReady } from "../../platform/analytics";

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

describe("analytics never sends a query string (12-B: /join?i= is a sign-in-capable invite)", () => {
  it("scrubbedLocation keeps origin and path only", async () => {
    const { scrubbedLocation } = await import("../../platform/analytics");
    expect(scrubbedLocation("https://app.example/join?i=SECRET-INVITE#x")).toBe("https://app.example/join");
    expect(scrubbedLocation("https://app.example/api/auth/redeem?t=tok&s=1")).toBe("https://app.example/api/auth/redeem");
    expect(scrubbedLocation("/join?i=SECRET")).toBe("/join");
  });

  it("a page view on /join?i= carries no token", async () => {
    const { gaPageview } = await import("../../platform/analytics");
    const gtag = vi.fn();
    (window as unknown as { gtag: typeof gtag }).gtag = gtag;
    window.history.pushState({}, "", "/join?i=SECRET-INVITE");
    gaPageview("/join");
    window.history.pushState({}, "", "/");
    const params = gtag.mock.calls[0][2] as Record<string, string>;
    expect(JSON.stringify(gtag.mock.calls)).not.toContain("SECRET-INVITE");
    expect(params.page_location).toMatch(/\/join$/);
  });

  it("the load's gtag config sets page_location and page_referrer to origin + path", async () => {
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const src = readFileSync(join(__dirname, "..", "AnalyticsScript.tsx"), "utf8");
    expect(src).toMatch(/gtag\('config','\$\{GA_ID\}',\{page_location:location\.origin\+location\.pathname,page_referrer:/);
    expect(src).not.toMatch(/gtag\('config','\$\{GA_ID\}'\);/);
  });
});
