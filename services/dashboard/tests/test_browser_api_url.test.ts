import { describe, it, expect } from "vitest";
import { resolveBrowserApiUrl } from "@/lib/browser-api-url";

describe("resolveBrowserApiUrl — stitched-candidate regression coverage (pack 6 fix)", () => {
  it("falls back to same-origin when both configured + request host are loopback (lite single-port publish)", () => {
    // Regression: lite supervisord sets NEXT_PUBLIC_API_URL=http://localhost:8056 (container-internal
    // gateway port). Browser is at host port 41692 (dashboard). The configured loopback URL would
    // tell the browser to talk to localhost:8056 which is unreachable. The resolver must instead
    // return same-origin so Next.js /ws + /api rewrites carry the traffic.
    const out = resolveBrowserApiUrl({
      internalApiUrl: "http://localhost:8056",
      configuredPublicApiUrl: "http://localhost:8056",
      requestHost: "localhost:41692",
      requestProto: "http",
    });
    expect(out.apiUrl).toBe("");
    expect(out.publicApiUrl).toBe("");
  });

  it("rewrites configured loopback hostname to request hostname when request host is non-loopback", () => {
    const out = resolveBrowserApiUrl({
      internalApiUrl: "http://localhost:8056",
      configuredPublicApiUrl: "http://localhost:8056",
      requestHost: "vexa.example.com",
      requestProto: "https",
    });
    expect(out.apiUrl).toBe("http://vexa.example.com:8056");
    expect(out.publicApiUrl).toBe("http://vexa.example.com:8056");
  });

  it("keeps configured non-loopback URL as-is", () => {
    const out = resolveBrowserApiUrl({
      internalApiUrl: "http://api-gateway:8000",
      configuredPublicApiUrl: "https://api.vexa.ai",
      requestHost: "dashboard.vexa.ai",
      requestProto: "https",
    });
    expect(out.apiUrl).toBe("https://api.vexa.ai");
    expect(out.publicApiUrl).toBe("https://api.vexa.ai");
  });

  it("falls back to same-origin even with gatewayHostPort when internal is an internal-service hostname (compose multi-port publish regression)", () => {
    // Regression: compose publishes dashboard on :41688 and gateway on :41680 as
    // separate host ports. Some browser environments only expose the dashboard
    // port (browser sandboxes, single-port proxies). The dashboard already
    // rewrites /ws to the gateway service URL — telling the browser to bypass
    // that rewrite and connect directly to :41680 breaks WS in those
    // environments. Prefer same-origin so the dashboard's /ws + /api/vexa/*
    // rewrites carry the traffic.
    const out = resolveBrowserApiUrl({
      internalApiUrl: "http://api-gateway:8000",
      requestHost: "localhost:41688",
      requestProto: "http",
      gatewayHostPort: "41680",
    });
    expect(out.apiUrl).toBe("");
    expect(out.publicApiUrl).toBe("");
  });

  it("returns empty for internal-service URL without gatewayHostPort hint (same-origin fallback)", () => {
    const out = resolveBrowserApiUrl({
      internalApiUrl: "http://api-gateway:8000",
      requestHost: "dashboard.svc.cluster.local",
      requestProto: "http",
    });
    expect(out.apiUrl).toBe("");
    expect(out.publicApiUrl).toBe("");
  });
});
