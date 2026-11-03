// @vitest-environment node
/** Every response leaves with a Content-Security-Policy: the API, the `/b/` bot proxy, build assets
 *  and public files get the fixed one for their path when the route wrote none (server.mjs); a
 *  route's own policy is never replaced. */
import { readFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { afterEach, describe, expect, it } from "vitest";
import { API_POLICY, LIVE_VIEW_POLICY, STATIC_POLICY, applyFallbackPolicy, fallbackPolicy } from "../responsePolicy.mjs";

let server: Server | null = null;
afterEach(() => { server?.close(); server = null; });

async function serve(handler: (req: import("node:http").IncomingMessage, res: import("node:http").ServerResponse) => void) {
  server = createServer((req, res) => { applyFallbackPolicy(req, res); handler(req, res); });
  await new Promise<void>((r) => server!.listen(0, "127.0.0.1", r));
  return `http://127.0.0.1:${(server!.address() as AddressInfo).port}`;
}

describe("the fallback policy", () => {
  it("names one for every non-page path", () => {
    expect(fallbackPolicy("/api/meetings")).toBe(API_POLICY);
    expect(fallbackPolicy("/api/bot-accounts/sessions/0123456789abcdef0123456789abcdef/view/vnc.html")).toBe(LIVE_VIEW_POLICY);
    expect(fallbackPolicy("/b/12/vnc.html")).toBe(LIVE_VIEW_POLICY);
    expect(fallbackPolicy("/_next/static/chunks/a.js")).toBe(STATIC_POLICY);
    expect(fallbackPolicy("/vexa-logo.svg")).toBe(STATIC_POLICY);
  });

  it("runs nothing for an API answer or a static file, and no inline script in the live view", () => {
    for (const p of [API_POLICY, STATIC_POLICY]) {
      expect(p).toMatch(/default-src 'none'/);
      expect(p).not.toMatch(/script-src/);
    }
    expect(LIVE_VIEW_POLICY).toMatch(/script-src 'self'(;|$)/);
    for (const p of [API_POLICY, STATIC_POLICY, LIVE_VIEW_POLICY]) expect(p).not.toMatch(/unsafe-eval|\*/);
  });

  it("a response its route gave no policy leaves with one (implicit head and writeHead alike)", async () => {
    const base = await serve((req, res) => {
      if (req.url === "/api/x") { res.setHeader("content-type", "application/json"); res.end("{}"); return; }
      res.writeHead(200, { "content-type": "image/svg+xml" }); res.end("<svg/>");
    });
    expect((await fetch(`${base}/api/x`)).headers.get("content-security-policy")).toBe(API_POLICY);
    expect((await fetch(`${base}/icon.svg`)).headers.get("content-security-policy")).toBe(STATIC_POLICY);
  });

  it("positive control: a route's own policy is kept, whether set or passed to writeHead", async () => {
    const base = await serve((req, res) => {
      if (req.url === "/api/raw") { res.setHeader("Content-Security-Policy", "default-src 'none'; sandbox"); res.end("x"); return; }
      res.writeHead(200, { "Content-Security-Policy": "default-src 'self'" }); res.end("x");
    });
    expect((await fetch(`${base}/api/raw`)).headers.get("content-security-policy")).toBe("default-src 'none'; sandbox");
    expect((await fetch(`${base}/page`)).headers.get("content-security-policy")).toBe("default-src 'self'");
  });

  it("server.mjs installs it on every request", () => {
    const src = readFileSync(new URL("../../../../server.mjs", import.meta.url), "utf8");
    expect(src).toMatch(/import \{ applyFallbackPolicy \} from "\.\/src\/app\/api\/responsePolicy\.mjs"/);
    expect(src).toMatch(/applyFallbackPolicy\(req, res\);/);
  });
});
