/** The catalogue's gates (terminal design guidelines §8–§9):
 *    G13 coverage — every component exported from the ui-kit front door has a registry entry, and
 *        every entry renders without throwing in both themes;
 *    G11 isolation — `src/app/design/**` imports only the ui-kit front door and its own files: no
 *        API client, no session, no auth, no fetch — so it can carry no user data (S5);
 *    the route is off in production unless VEXA_TERMINAL_DESIGN_CATALOGUE=1. */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import * as kit from "../../ui-kit";
import { REGISTRY } from "../design/registry";
import { catalogueEnabled } from "../design/gate";
import { Frame } from "../design/Frame";

const DESIGN = join(__dirname, "..", "design");
function* files(dir: string): Generator<string> {
  for (const n of readdirSync(dir)) { const p = join(dir, n); if (statSync(p).isDirectory()) yield* files(p); else if (/\.(tsx?|css)$/.test(n)) yield p; }
}

const isComponent = (name: string, v: unknown) =>
  /^[A-Z][a-z]/.test(name) && (typeof v === "function" || (typeof v === "object" && v !== null && "$$typeof" in v));

describe("G13 — catalogue coverage", () => {
  it("every component the ui-kit exports has a registry entry", () => {
    const exported = Object.entries(kit).filter(([n, v]) => isComponent(n, v)).map(([n]) => n);
    const covered = new Set(REGISTRY.flatMap((e) => e.components));
    expect(exported.filter((n) => !covered.has(n))).toEqual([]);
  });
  it("every registry entry names only real exports, and ids are unique", () => {
    const names = new Set(Object.keys(kit));
    expect(REGISTRY.flatMap((e) => e.components).filter((c) => !names.has(c))).toEqual([]);
    expect(new Set(REGISTRY.map((e) => e.id)).size).toBe(REGISTRY.length);
  });
  for (const e of REGISTRY) {
    it(`"${e.title}" renders in both themes`, () => {
      const html = renderToStaticMarkup(<Frame widths={e.widths}>{e.demo()}</Frame>);
      expect(html).toContain('data-theme="dark"');
      expect(html).toContain('data-theme="light"');
    });
  }
});

describe("G11 — the catalogue holds no user data", () => {
  const BANNED = [/from\s+["'][^"']*(surfaces|minutes|canvas|workbench|platform|contributions)\//, /apiClient|workspaceApi|next-auth|\bfetch\(|localStorage\.getItem\(["']vexa\.(?!shell\.catalogue)/];
  it("imports only the ui-kit front door, its own files, React, lucide and Next", () => {
    const bad: string[] = [];
    for (const f of files(DESIGN)) {
      const src = readFileSync(f, "utf8");
      for (const re of BANNED) if (re.test(src)) bad.push(`${f.slice(DESIGN.length + 1)}: ${re}`);
      for (const m of src.matchAll(/from\s+["']([^"']+)["']/g)) {
        const spec = m[1];
        const ok = spec === "react" || spec === "lucide-react" || spec.startsWith("next/") || spec === "next" || spec.startsWith("./") || spec === "../fixtures"
          || spec === "../../ui-kit" || spec === "../../../ui-kit";
        if (!ok) bad.push(`${f.slice(DESIGN.length + 1)} imports ${spec}`);
      }
    }
    expect(bad).toEqual([]);
  });
  it("fixtures are placeholders", () => {
    const src = readFileSync(join(DESIGN, "fixtures.ts"), "utf8");
    expect(src).toMatch(/Person Name/);
    expect(src).not.toMatch(/@(?!example)[a-z0-9-]+\.(com|ai|io)/i);
  });
});

describe("the route's gate", () => {
  it("is on outside production, and in production only with the flag", () => {
    expect(catalogueEnabled({ NODE_ENV: "development" })).toBe(true);
    expect(catalogueEnabled({ NODE_ENV: "production" })).toBe(false);
    expect(catalogueEnabled({ NODE_ENV: "production", VEXA_TERMINAL_DESIGN_CATALOGUE: "1" })).toBe(true);
    expect(catalogueEnabled({ NODE_ENV: "production", VEXA_TERMINAL_DESIGN_CATALOGUE: "true" })).toBe(false);
  });
  it("is noindex and skipped by analytics", () => {
    expect(readFileSync(join(DESIGN, "page.tsx"), "utf8")).toMatch(/robots:\s*\{\s*index:\s*false,\s*follow:\s*false\s*\}/);
    expect(readFileSync(join(__dirname, "..", "AnalyticsScript.tsx"), "utf8")).toMatch(/startsWith\("\/design"\)/);
  });
});
