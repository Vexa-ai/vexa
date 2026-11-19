// @vitest-environment node
/** The terminal pages carry a Content-Security-Policy with a per-response nonce. */
import { readdirSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { canvasFrameContentSecurityPolicy, contentSecurityPolicy, newNonce } from "../contentSecurityPolicy";
import { middleware, config } from "../../middleware";
import { BLUR_ASSETS } from "../../minutes/callBlur";

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
    expect(d["style-src"]).toEqual(["'self'", "'nonce-abc'", "https://fonts.googleapis.com"]);
    expect(d["style-src-elem"]).toEqual(["'self'", "'nonce-abc'", "https://fonts.googleapis.com"]);
    expect(d["style-src-attr"]).toEqual(["'none'"]);
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

  it("turns no string into code: no 'unsafe-eval', WebAssembly alone for the call blur", () => {
    const d = directives(contentSecurityPolicy("abc", {}, false));
    expect(d["script-src"]).not.toContain("'unsafe-eval'");
    expect(d["script-src"]).toContain("'wasm-unsafe-eval'");
    // the development server keeps it for React's refresh runtime, and only there
    expect(directives(contentSecurityPolicy("abc", {}, true))["script-src"]).toContain("'unsafe-eval'");
  });

  it("a style ATTRIBUTE applies nowhere: the server renders none, and markup that carries one is refused", () => {
    for (const dev of [false, true]) {
      const d = directives(contentSecurityPolicy("abc", { livekitUrl: "wss://lk.example.test", gaMeasurementId: "G-X" }, dev));
      expect(d["style-src-attr"]).toEqual(["'none'"]);
      // and no directive it falls back to admits one either
      for (const k of ["style-src-attr", "style-src", "default-src"]) {
        expect(d[k] ?? [], k).not.toContain("'unsafe-inline'");
        expect(d[k] ?? [], k).not.toContain("'unsafe-hashes'");
      }
    }
  });

  it("the policy a production server sends carries no 'unsafe-eval'", () => {
    const before = process.env.NODE_ENV;
    try {
      (process.env as Record<string, string>).NODE_ENV = "production";
      for (const path of ["/", "/w/notes", "/canvas-frame/other"]) {
        const csp = middleware(new NextRequest(`https://app.example.test${path}`)).headers.get("content-security-policy") ?? "";
        expect(csp, path).not.toMatch(/'unsafe-eval'/);
        expect(csp, path).toMatch(/style-src-elem [^;]*'nonce-/);
        expect(csp, path).not.toMatch(/style-src-elem [^;]*'unsafe-inline'/);
        if (!path.startsWith("/canvas-frame")) expect(csp, path).toMatch(/style-src-attr 'none'(;|$)/);
      }
    } finally {
      (process.env as Record<string, string | undefined>).NODE_ENV = before;
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

  it("keeps the frame's DNS to itself", () => {
    const frame = middleware(new NextRequest("https://terminal.example.test/canvas-frame"));
    expect(frame.headers.get("X-DNS-Prefetch-Control")).toBe("off");
  });

  it("the page that frames it refuses a frame navigation to any other host", () => {
    expect(directives(contentSecurityPolicy("abc", {}))["frame-src"]).toEqual(["'self'"]);
  });

  it("is the policy the middleware serves /canvas-frame with, and only that path", () => {
    const frame = middleware(new NextRequest("https://terminal.example.test/canvas-frame"));
    expect(frame.headers.get("Content-Security-Policy")).toMatch(/sandbox allow-scripts/);
    expect(frame.headers.get("Content-Security-Policy")).toMatch(/connect-src 'none'/);
    const page = middleware(new NextRequest("https://terminal.example.test/"));
    expect(page.headers.get("Content-Security-Policy")).not.toMatch(/sandbox/);
  });
});

describe("the Meeting Canvas on an origin of its own (TERMINAL_CANVAS_ORIGIN)", () => {
  const separate = { canvasOrigin: "https://canvas.example.test", terminalOrigins: ["https://app.example.test"] };

  it("the pages frame the canvas origin and the live view by path, and never their own origin whole", () => {
    const d = directives(contentSecurityPolicy("abc", separate));
    expect(d["frame-src"]).toEqual(["https://canvas.example.test", "https://app.example.test/api/bot-accounts/sessions/"]);
    expect(d["frame-src"]).not.toContain("'self'");
    expect(d["frame-ancestors"]).toEqual(["'self'"]);
  });

  it("the frame lets only the terminal's public origins frame it", () => {
    const d = directives(canvasFrameContentSecurityPolicy("abc", separate));
    expect(d["frame-ancestors"]).toEqual(["https://app.example.test"]);
    expect(d.sandbox).toEqual(["allow-scripts"]);
    expect(d["connect-src"]).toEqual(["'none'"]);
    expect(d["form-action"]).toEqual(["'none'"]);
    expect(d["frame-src"]).toEqual(["'none'"]);
  });

  it("a script the view creates loads from the frame's own origin only — no 'strict-dynamic', on either origin", () => {
    // under 'strict-dynamic' a script element a view creates loads from ANY host, with whatever it
    // put in the URL (measured in Chromium 145: scripts/canvas-isolation.mjs "script → sink")
    for (const sources of [separate, {}]) {
      const d = directives(canvasFrameContentSecurityPolicy("abc", sources));
      expect(d["script-src"]).toEqual(["'self'", "'nonce-abc'", "'unsafe-eval'"]);
    }
  });

  it("with no canvas origin, or no terminal origin to let frame it, everything stays on this origin", () => {
    for (const sources of [{}, { canvasOrigin: "https://canvas.example.test", terminalOrigins: [] }]) {
      expect(directives(contentSecurityPolicy("abc", sources))["frame-src"]).toEqual(["'self'"]);
      expect(directives(canvasFrameContentSecurityPolicy("abc", sources))["frame-ancestors"]).toEqual(["'self'"]);
    }
  });
});

describe("which requests the policy middleware runs on (Next's own matcher)", () => {
  // the matcher exactly as Next compiles and applies it at build and run time
  const require = createRequire(import.meta.url);
  const { getMiddlewareMatchers } = require("next/dist/build/analysis/get-page-static-info");
  const { getMiddlewareRouteMatcher } = require("next/dist/shared/lib/router/utils/middleware-route-matcher");
  const matches = getMiddlewareRouteMatcher(getMiddlewareMatchers(config.matcher, {}));
  const runs = (path: string) => matches(path, { headers: {} } as never, {});
  const root = fileURLToPath(new URL("../../..", import.meta.url));

  it("covers every page, whatever its URL ends in", () => {
    for (const page of ["/", "/w/ws/notes", "/w/ws/notes.json", "/w/ws/data.txt", "/r/doc/x.svg",
      "/meetings/1.png", "/w/ws/a.xml", "/w/ws/font.woff2", "/w/ws/README.md", "/p/icon.svg"]) {
      expect(runs(page), page).toBe(true);
    }
  });

  it("leaves the API, the /b/ proxy, Next's assets and the real root files alone", () => {
    for (const path of ["/api/tokens", "/b/123/vnc.html", "/_next/static/chunks/a.js", "/_next/image"]) {
      expect(runs(path), path).toBe(false);
    }
  });

  it("names every real root file: everything in public/ and the app's root metadata files", () => {
    const files = [
      ...readdirSync(join(root, "public")),
      ...readdirSync(join(root, "src/app")).filter((f) =>
        /^(?:icon|apple-icon|favicon|opengraph-image|twitter-image|robots|sitemap|manifest)\b.*\.\w+$/.test(f)),
    ];
    expect(files.length).toBeGreaterThan(0);
    for (const f of files) expect(runs(`/${f}`), f).toBe(false);
  });

  it("names the files the build writes into public/ too: the call blur's runtime and model", () => {
    // scripts/copy-mediapipe.mjs writes public/mediapipe/ before every build and dev server, and the
    // production image serves it; a clean checkout has none, so the tree above cannot show it.
    for (const path of ["/mediapipe", "/mediapipe/README.md", `${BLUR_ASSETS.tasksVisionFileSet}/vision_wasm_internal.wasm`,
      `${BLUR_ASSETS.tasksVisionFileSet}/vision_wasm_internal.js`, BLUR_ASSETS.modelAssetPath]) {
      expect(runs(path), path).toBe(false);
    }
    for (const page of ["/mediapipes", "/w/mediapipe/notes"]) expect(runs(page), page).toBe(true);
  });
});
