// @vitest-environment node
/** The front door (server.mjs → frameDoor.mjs) answers the canvas host and the terminal before any
 *  route or log sees the URL. The way-out proof in a browser is scripts/canvas-isolation.mjs. */
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { REFUSED_HEADER, canvasDoorRefusal, isBuildAsset, refuse, terminalDoorRefusal } from "../frameDoor.mjs";

const staticDir = mkdtempSync(join(tmpdir(), "frame-door-"));
mkdirSync(join(staticDir, "chunks"), { recursive: true });
writeFileSync(join(staticDir, "chunks", "app.js"), "//");
const opts = { staticDir, dev: false };

const req = (url: string, headers: Record<string, string> = {}, method = "GET") => ({ url, method, headers });
/** What a sandboxed (opaque-origin) frame's request looks like: always cross-site. */
const fromFrame = (dest: string) => ({ "sec-fetch-site": "cross-site", "sec-fetch-dest": dest, "sec-fetch-mode": dest === "iframe" ? "navigate" : "no-cors" });

describe("the canvas host's door", () => {
  it("serves the frame document and the build's own files, and nothing else", () => {
    expect(canvasDoorRefusal(req("/canvas-frame", { "sec-fetch-dest": "iframe", "sec-fetch-site": "cross-site" }), opts)).toBeNull();
    expect(canvasDoorRefusal(req("/canvas-frame"), opts)).toBeNull();
    expect(canvasDoorRefusal(req("/_next/static/chunks/app.js", fromFrame("script")), opts)).toBeNull();
    for (const url of ["/", "/api/version", "/w/notes", "/_next/static/chunks/missing.js", "/_next/static/../../server.mjs",
      "/_next/static/chunks/%2e%2e/%2e%2e/x", "/_next/image", "/leak/abc", "/README.md"]) {
      expect(canvasDoorRefusal(req(url), opts), url).toEqual({ status: 404, reason: "canvas-host" });
    }
  });

  it("refuses any URL a view could put data into: a query on the frame or on a build file", () => {
    expect(canvasDoorRefusal(req("/canvas-frame?leak=secret", fromFrame("iframe")), opts)).toEqual({ status: 404, reason: "canvas-host" });
    expect(canvasDoorRefusal(req("/_next/static/chunks/app.js?leak=secret", fromFrame("script")), opts)).toEqual({ status: 404, reason: "canvas-host" });
  });

  it("is never a document of its own, and takes no writes", () => {
    expect(canvasDoorRefusal(req("/canvas-frame", { "sec-fetch-dest": "document" }), opts)).toEqual({ status: 404, reason: "canvas-host" });
    expect(canvasDoorRefusal(req("/canvas-frame", {}, "POST"), opts)).toEqual({ status: 405, reason: "canvas-host" });
  });
});

describe("the terminal's door", () => {
  it("is never framed by another origin — the canvas frame's self-navigation included", () => {
    for (const url of ["/api/version?leak=secret", "/?leak=secret", "/api/bot-accounts/sessions/0/view/vnc.html?leak=x"]) {
      expect(terminalDoorRefusal(req(url, fromFrame("iframe")), opts), url).toEqual({ status: 403, reason: "framed-from-another-origin" });
      expect(terminalDoorRefusal(req(url, { "sec-fetch-site": "same-site", "sec-fetch-dest": "iframe" }), opts), url).not.toBeNull();
    }
  });

  it("serves no code or styling to another origin but the build's own files, exactly named", () => {
    for (const dest of ["script", "style", "font", "worker"]) {
      expect(terminalDoorRefusal(req("/api/version?leak=secret", fromFrame(dest)), opts), dest).toEqual({ status: 403, reason: "subresource-for-another-origin" });
    }
    expect(terminalDoorRefusal(req("/_next/static/chunks/app.js", fromFrame("script")), opts)).toBeNull();
    expect(terminalDoorRefusal(req("/_next/static/chunks/app.js?leak=secret", fromFrame("script")), opts)).not.toBeNull();
  });

  it("leaves its own pages, a followed link, a picture and a browser that sends no metadata alone", () => {
    expect(terminalDoorRefusal(req("/api/bot-accounts/sessions/0/view/vnc.html", { "sec-fetch-site": "same-origin", "sec-fetch-dest": "iframe" }), opts)).toBeNull();
    expect(terminalDoorRefusal(req("/canvas-frame", { "sec-fetch-site": "same-origin", "sec-fetch-dest": "iframe" }), opts)).toBeNull();
    expect(terminalDoorRefusal(req("/api/auth/redeem?t=x", { "sec-fetch-site": "cross-site", "sec-fetch-dest": "document" }), opts)).toBeNull();
    expect(terminalDoorRefusal(req("/vexa-logo.svg", { "sec-fetch-site": "cross-site", "sec-fetch-dest": "image" }), opts)).toBeNull();
    expect(terminalDoorRefusal(req("/api/version"), opts)).toBeNull();
  });

  it("does not serve /canvas-frame itself once a canvas host does", () => {
    expect(terminalDoorRefusal(req("/canvas-frame", { "sec-fetch-site": "same-origin", "sec-fetch-dest": "iframe" }), { ...opts, canvasConfigured: true }))
      .toEqual({ status: 404, reason: "canvas-on-its-own-origin" });
  });
});

describe("the build's own files", () => {
  it("are files under .next/static, named exactly, never a way out of it", () => {
    expect(isBuildAsset("/_next/static/chunks/app.js", opts)).toBe(true);
    expect(isBuildAsset("/_next/static/chunks", opts)).toBe(false);
    expect(isBuildAsset("/_next/static/../static/chunks/app.js", opts)).toBe(false);
    expect(isBuildAsset("/_next/data/x.json", opts)).toBe(false);
    expect(isBuildAsset("/_next/static/anything-compiling.js", { staticDir, dev: true })).toBe(true);
  });
});

describe("a refusal", () => {
  it("names what refused it, runs nothing, and never echoes the URL", () => {
    const head: { status?: number; headers?: Record<string, string>; body?: string } = {};
    const res = {
      headersSent: false,
      writeHead(status: number, headers: Record<string, string>) { head.status = status; head.headers = headers; },
      end(body: string) { head.body = body; },
    };
    refuse(res as never, { status: 403, reason: "framed-from-another-origin" });
    expect(head.status).toBe(403);
    expect(head.headers?.[REFUSED_HEADER]).toBe("framed-from-another-origin");
    expect(head.headers?.["Content-Security-Policy"]).toMatch(/default-src 'none'.*frame-ancestors 'none'/);
    expect(head.body).toBe("Refused\n");
  });
});

describe("server.mjs", () => {
  const src = readFileSync(fileURLToPath(new URL("../../../../server.mjs", import.meta.url)), "utf8");
  const body = src.slice(src.indexOf("const handleRequest = (viaCanvasPort) => (req, res)"));

  it("answers the door before anything reads the request, on both listeners", () => {
    const door = body.indexOf("canvasDoorRefusal(req, DOOR) : terminalDoorRefusal(req, DOOR)");
    expect(door).toBeGreaterThan(0);
    expect(door).toBeLessThan(body.indexOf("stampClientAddress(req"));
    expect(door).toBeLessThan(body.indexOf("handle(req, res)"));
    expect(src).toContain("createServer(handleRequest(false))");
    expect(src).toContain("createServer(handleRequest(true))");
  });

  it("refuses to start on a canvas setting it cannot serve, and warns when there is none", () => {
    expect(src).toMatch(/canvasStartupError\(process\.env\)[\s\S]{0,200}process\.exit\(1\)/);
    expect(src).toMatch(/canvasStartupWarning\(process\.env\)[\s\S]{0,80}console\.warn/);
  });
});
