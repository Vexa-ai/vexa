// @vitest-environment node
/** The terminal pages carry a Content-Security-Policy with a per-response nonce. */
import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { contentSecurityPolicy, newNonce } from "../contentSecurityPolicy";
import { middleware, config } from "../../middleware";

const directives = (csp: string) =>
  Object.fromEntries(csp.split(";").map((d) => d.trim().split(/\s+/)).map(([k, ...v]) => [k, v]));

describe("the terminal pages' policy", () => {
  it("runs a script only with this response's nonce or from one that had it", () => {
    const d = directives(contentSecurityPolicy("abc"));
    expect(d["script-src"]).toContain("'nonce-abc'");
    expect(d["script-src"]).toContain("'strict-dynamic'");
    expect(d["script-src"]).not.toContain("'unsafe-inline'");
  });

  it("closes base, object and foreign frames", () => {
    const d = directives(contentSecurityPolicy("abc"));
    expect(d["base-uri"]).toEqual(["'none'"]);
    expect(d["object-src"]).toEqual(["'none'"]);
    expect(d["frame-src"]).toEqual(["'self'"]);
  });

  it("a nonce is fresh on every response", () => {
    const a = newNonce(), b = newNonce();
    expect(a).not.toBe(b);
    expect(a.length).toBeGreaterThanOrEqual(22);
  });

  it("the middleware sets the policy on the response and hands the nonce to the page", () => {
    const res = middleware(new NextRequest("https://app.example.test/w/notes"));
    const csp = res.headers.get("content-security-policy") ?? "";
    const nonce = /'nonce-([^']+)'/.exec(csp)?.[1];
    expect(nonce).toBeTruthy();
    expect(res.headers.get("x-middleware-request-x-nonce")).toBe(nonce);
  });

  it("API routes and static assets are not pages", () => {
    const re = new RegExp(`^${config.matcher[0].source}$`);
    expect(re.test("/w/notes")).toBe(true);
    expect(re.test("/")).toBe(true);
    expect(re.test("/api/tokens")).toBe(false);
    expect(re.test("/_next/static/chunks/a.js")).toBe(false);
    expect(re.test("/b/123/vnc.html")).toBe(false);
  });
});
