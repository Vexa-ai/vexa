import { describe, expect, it } from "vitest";
import { resolveBrowserApiUrl } from "../src/lib/browser-api-url";

describe("resolveBrowserApiUrl", () => {
  it("infers a browser-reachable Compose API URL from the request host", () => {
    expect(
      resolveBrowserApiUrl({
        internalApiUrl: "http://api-gateway:8000",
        requestHost: "172.238.172.154:3001",
        requestProto: "http",
        gatewayHostPort: "8056",
      })
    ).toEqual({
      apiUrl: "http://172.238.172.154:8056",
      publicApiUrl: "http://172.238.172.154:8056",
    });
  });

  it("normalizes a configured loopback public URL for remote self-hosted browsers", () => {
    expect(
      resolveBrowserApiUrl({
        internalApiUrl: "http://api-gateway:8000",
        configuredPublicApiUrl: "http://localhost:8056",
        requestHost: "203.0.113.10:3001",
        requestProto: "http",
      })
    ).toEqual({
      apiUrl: "http://203.0.113.10:8056",
      publicApiUrl: "http://203.0.113.10:8056",
    });
  });

  it("preserves an explicitly configured public API URL", () => {
    expect(
      resolveBrowserApiUrl({
        internalApiUrl: "http://api-gateway:8000",
        configuredPublicApiUrl: "https://api.cloud.vexa.ai/",
        requestHost: "dashboard.vexa.ai",
        requestProto: "https",
        gatewayHostPort: "8056",
      })
    ).toEqual({
      apiUrl: "https://api.cloud.vexa.ai",
      publicApiUrl: "https://api.cloud.vexa.ai",
    });
  });

  it("does not leak internal service URLs when no browser route can be inferred", () => {
    expect(
      resolveBrowserApiUrl({
        internalApiUrl: "http://api-gateway:8000",
        requestHost: "dashboard.example.test",
        requestProto: "https",
      })
    ).toEqual({
      apiUrl: "",
      publicApiUrl: "",
    });
  });
});
