// @vitest-environment node
/** `/_next/image` is answered by server.mjs with a 404 and never reaches Next, whose optimizer would
 *  try to create `.next/cache` in the read-only runtime tree (EACCES, unhandled). */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { isImageOptimizerPath, refuseImageOptimizer } from "../imageOptimizer.mjs";

const SERVER = fileURLToPath(new URL("../../../server.mjs", import.meta.url));

function fakeRes() {
  const headers: Record<string, string> = {};
  return {
    statusCode: 200, body: "", headers,
    setHeader(k: string, v: string) { headers[k.toLowerCase()] = v; },
    end(b?: string) { this.body = b ?? ""; },
  };
}

describe("the image optimizer route", () => {
  it("matches the route with or without a query, and nothing else", () => {
    expect(isImageOptimizerPath("/_next/image?url=%2Fx.png&w=64&q=75")).toBe(true);
    expect(isImageOptimizerPath("/_next/image")).toBe(true);
    expect(isImageOptimizerPath("/_next/static/chunks/a.js")).toBe(false);
    expect(isImageOptimizerPath("/_next/images")).toBe(false);
    expect(isImageOptimizerPath("/")).toBe(false);
  });

  it("answers 404 itself", () => {
    const res = fakeRes();
    refuseImageOptimizer(res);
    expect(res.statusCode).toBe(404);
    expect(res.headers["cache-control"]).toBe("no-store");
  });

  it("is checked in server.mjs before the request reaches Next's handler", () => {
    const src = readFileSync(SERVER, "utf8");
    const body = src.slice(src.indexOf("createServer((req, res)"));
    const check = body.indexOf("isImageOptimizerPath(req.url)");
    expect(check).toBeGreaterThan(0);
    expect(check).toBeLessThan(body.indexOf("handle(req, res)"));
  });
});
