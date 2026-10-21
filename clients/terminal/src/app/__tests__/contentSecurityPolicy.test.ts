// @vitest-environment node
/** The terminal pages carry a Content-Security-Policy with a per-response nonce. */
import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { canvasFrameContentSecurityPolicy, contentSecurityPolicy, newNonce } from "../contentSecurityPolicy";
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

  it("names a source for everything the pages load, and defaults to this origin", () => {
    const d = directives(contentSecurityPolicy("abc", {}));
    expect(d["default-src"]).toEqual(["'self'"]);
    expect(d["connect-src"]).toEqual(["'self'"]);
    expect(d["img-src"]).toEqual(["'self'", "data:", "blob:"]);
    expect(d["style-src"]).toEqual(["'self'", "'unsafe-inline'", "https://fonts.googleapis.com"]);
    expect(d["font-src"]).toEqual(["'self'", "https://fonts.gstatic.com"]);
    expect(d["media-src"]).toEqual(["'self'"]);
    expect(d["worker-src"]).toEqual(["'self'", "blob:"]);
    expect(d["form-action"]).toEqual(["'self'"]);
    expect(d["frame-ancestors"]).toEqual(["'self'"]);
  });

  it("reaches a third party only for a feature this deployment turned on", () => {
    const d = directives(contentSecurityPolicy("abc", { livekitUrl: "wss://calls.example.test", gaMeasurementId: "G-TEST" }));
    expect(d["connect-src"]).toEqual(expect.arrayContaining(["'self'", "wss://calls.example.test", "https://calls.example.test",
      "https://www.google-analytics.com", "https://region1.google-analytics.com"]));
    expect(d["img-src"]).toEqual(expect.arrayContaining(["https://www.google-analytics.com"]));
    const local = directives(contentSecurityPolicy("abc", { livekitUrl: "ws://localhost:7880" }));
    expect(local["connect-src"]).toEqual(["'self'", "ws://localhost:7880", "http://localhost:7880"]);
  });

  it("a LiveKit address that is not a plain origin adds nothing", () => {
    for (const bad of ["wss://*.example.test", "javascript:alert(1)", "wss://a.test; script-src *", "not a url", "  "]) {
      expect(directives(contentSecurityPolicy("abc", { livekitUrl: bad }))["connect-src"]).toEqual(["'self'"]);
    }
  });

  it("no directive carries a wildcard or a bare scheme that admits any host", () => {
    for (const sources of [{}, { livekitUrl: "wss://calls.example.test", gaMeasurementId: "G-TEST" }]) {
      for (const [name, values] of Object.entries(directives(contentSecurityPolicy("abc", sources)))) {
        for (const v of values) {
          expect(v, `${name} ${v}`).not.toContain("*");
          expect(["http:", "https:", "ws:", "wss:"], `${name} ${v}`).not.toContain(v);
        }
      }
    }
  });

  it("reads LiveKit and GA from the environment the stack already sets", () => {
    const before = { ...process.env };
    try {
      process.env.LIVEKIT_URL = "wss://calls.example.test";
      process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID = "G-TEST";
      const d = directives(contentSecurityPolicy("abc"));
      expect(d["connect-src"]).toContain("wss://calls.example.test");
      expect(d["connect-src"]).toContain("https://www.google-analytics.com");
    } finally {
      process.env = before;
    }
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


describe("the Meeting Canvas frame's policy (S03T-1)", () => {
  it("sandboxes the document to scripts alone: an opaque origin, never allow-same-origin", () => {
    const d = directives(canvasFrameContentSecurityPolicy("abc"));
    expect(d["sandbox"]).toEqual(["allow-scripts"]);
    expect(canvasFrameContentSecurityPolicy("abc")).not.toMatch(/allow-same-origin/);
  });

  it("connects nowhere and loads nothing but its own scripts and styles", () => {
    const d = directives(canvasFrameContentSecurityPolicy("abc"));
    expect(d["default-src"]).toEqual(["'none'"]);
    expect(d["connect-src"]).toEqual(["'none'"]);
    expect(d["img-src"]).toEqual(["data:"]);
    expect(d["form-action"]).toEqual(["'none'"]);
    expect(d["frame-src"]).toEqual(["'none'"]);
    expect(d["frame-ancestors"]).toEqual(["'self'"]);
    expect(d["script-src"]).toContain("'nonce-abc'");
  });

  it("is the policy the middleware serves /canvas-frame with, and only that path", () => {
    const frame = middleware(new NextRequest("https://terminal.example.test/canvas-frame"));
    expect(frame.headers.get("Content-Security-Policy")).toMatch(/sandbox allow-scripts/);
    expect(frame.headers.get("Content-Security-Policy")).toMatch(/connect-src 'none'/);
    const page = middleware(new NextRequest("https://terminal.example.test/"));
    expect(page.headers.get("Content-Security-Policy")).not.toMatch(/sandbox/);
  });
});
