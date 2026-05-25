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

  it("infers public URL from request host + gatewayHostPort when internal is an internal-service hostname", () => {
    const out = resolveBrowserApiUrl({
      internalApiUrl: "http://api-gateway:8000",
      requestHost: "localhost:18056",
      requestProto: "http",
      gatewayHostPort: "18056",
    });
    expect(out.apiUrl).toBe("http://localhost:18056");
    expect(out.publicApiUrl).toBe("http://localhost:18056");
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
