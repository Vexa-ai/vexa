// #1969: the dashboard refuses to be framed by other sites, except the /b/ proxy
// (the dashboard frames its own noVNC page there when the runtime apiUrl is empty).
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
// Next's own vendored path-to-regexp: the same compiler Next uses for headers() sources.
import { pathToRegexp } from "next/dist/compiled/path-to-regexp";

type Header = { key: string; value: string };
type HeaderRule = { source: string; headers: Header[] };

const ENV_KEYS = ["VEXA_API_URL", "NEXT_PUBLIC_BASE_PATH"] as const;
const saved: Record<string, string | undefined> = {};

async function loadConfig(basePath?: string) {
  process.env.VEXA_API_URL = "http://api-gateway:8000";
  if (basePath === undefined) delete process.env.NEXT_PUBLIC_BASE_PATH;
  else process.env.NEXT_PUBLIC_BASE_PATH = basePath;
  vi.resetModules();
  const mod = await import("../next.config");
  const config = mod.default as { basePath?: string; headers?: () => Promise<HeaderRule[]> };
  expect(typeof config.headers).toBe("function");
  const rules = await config.headers!();
  return { config, rules };
}

// Compile a source the way Next compiles custom routes (strict, case-insensitive, "/" delimiter),
// with the deployment basePath prefixed as Next does ("/" under basePath becomes the basePath itself).
function compile(source: string, basePath = "") {
  const full = basePath ? `${basePath}${source === "/" ? "" : source}` : source;
  return pathToRegexp(full, [], { strict: true, sensitive: false, delimiter: "/" });
}

function matchingRules(rules: HeaderRule[], path: string, basePath = "") {
  return rules.filter((r) => compile(r.source, basePath).test(path));
}

// Headers Next would send for a path: later rules overwrite earlier ones per key (setHeader).
function effectiveHeaders(rules: HeaderRule[], path: string, basePath = "") {
  const out = new Map<string, string>();
  for (const r of matchingRules(rules, path, basePath)) {
    for (const h of r.headers) out.set(h.key.toLowerCase(), h.value);
  }
  return out;
}

const FRAMED_PATHS = ["/", "/meetings", "/meetings/123", "/api/config", "/billing", "/browser-x", "/login", "/ws"];
const PROXY_PATHS = ["/b", "/b/", "/b/tok/vnc/vnc.html", "/b/tok/cdp", "/B/tok/vnc/vnc.html"];

beforeEach(() => {
  for (const k of ENV_KEYS) saved[k] = process.env[k];
});

afterEach(() => {
  for (const k of ENV_KEYS) {
    if (saved[k] === undefined) delete process.env[k];
    else process.env[k] = saved[k];
  }
  vi.resetModules();
});

describe("dashboard anti-framing headers (#1969)", () => {
  it("every rule carries exactly the two anti-framing headers with exact values", async () => {
    const { rules } = await loadConfig();
    expect(rules.length).toBeGreaterThan(0);
    for (const r of rules) {
      expect(r.headers).toEqual([
        { key: "X-Frame-Options", value: "DENY" },
        { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
      ]);
    }
  });

  it.each(FRAMED_PATHS)("%s gets X-Frame-Options DENY and frame-ancestors 'none', once each", async (p) => {
    const { rules } = await loadConfig();
    expect(matchingRules(rules, p).length).toBeGreaterThan(0);
    const h = effectiveHeaders(rules, p);
    expect(h.get("x-frame-options")).toBe("DENY");
    expect(h.get("content-security-policy")).toBe("frame-ancestors 'none'");
    expect(h.size).toBe(2);
  });

  it.each(PROXY_PATHS)("%s (the /b/ noVNC proxy) matches no anti-framing rule", async (p) => {
    const { rules } = await loadConfig();
    expect(matchingRules(rules, p)).toEqual([]);
  });

  it("basePath /x keeps the same sources, and Next's prefixing gives the same split", async () => {
    const { rules: plain } = await loadConfig();
    const { config, rules } = await loadConfig("/x");
    expect(config.basePath).toBe("/x");
    expect(rules.map((r) => r.source)).toEqual(plain.map((r) => r.source));

    for (const p of FRAMED_PATHS) {
      const full = p === "/" ? "/x" : `/x${p}`;
      const h = effectiveHeaders(rules, full, "/x");
      expect(h.get("x-frame-options"), full).toBe("DENY");
      expect(h.get("content-security-policy"), full).toBe("frame-ancestors 'none'");
    }
    for (const p of PROXY_PATHS) {
      expect(matchingRules(rules, `/x${p}`, "/x"), `/x${p}`).toEqual([]);
    }
  });
});
