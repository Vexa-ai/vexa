/** The token gates (terminal design guidelines §8):
 *    G7  contrast — every declared text pair ≥ 4.5:1 and boundary pair ≥ 3:1, in BOTH themes;
 *    G3  every `var(--x)` in the terminal resolves to a token that exists, and no colour hides
 *        behind a fallback (`var(--red, #e57373)` rendered the fallback for months because `--red`
 *        was never defined — terminal UI audit §1.3);
 *    G1  raw colours outside tokens.css — REPORT ONLY until the surfaces migrate (Phase 3a turns it
 *        red); the count is printed so it can only be watched going down. */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { contrastRatio } from "../format/contrast";
import { parseTokens } from "../format/tokens";

const SRC = join(__dirname, "..", "..");
const TOKENS_CSS = readFileSync(join(SRC, "app", "tokens.css"), "utf8");
const GLOBALS_CSS = readFileSync(join(SRC, "app", "globals.css"), "utf8");
const PAIRS = JSON.parse(readFileSync(join(SRC, "app", "tokens.pairs.json"), "utf8")) as {
  surfaces: string[]; text: { fg: string; on: string | string[] }[]; boundary: { fg: string; on: string | string[] }[];
};
const T = parseTokens(TOKENS_CSS);

function* files(dir: string): Generator<string> {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) { if (name !== "__tests__" && name !== "node_modules") yield* files(p); }
    else if (/\.(tsx?|css)$/.test(name) && !/\.test\.tsx?$/.test(name)) yield p;
  }
}
const SOURCES = [...files(SRC)].map((f) => ({ rel: f.slice(SRC.length + 1), text: readFileSync(f, "utf8") }));

describe("G7 — contrast, computed from tokens.css in both themes", () => {
  const surfacesOf = (on: string | string[]) => (on === "surfaces" ? PAIRS.surfaces : on as string[]);
  for (const theme of ["dark", "light"] as const) {
    const tok = T[theme];
    it(`${theme}: text pairs ≥ 4.5:1`, () => {
      const fails: string[] = [];
      for (const p of PAIRS.text) for (const bg of surfacesOf(p.on)) {
        const r = contrastRatio(tok[p.fg], tok[bg]);
        if (r === null || r < 4.5) fails.push(`${p.fg} on ${bg}: ${r?.toFixed(2) ?? "unparseable"}`);
      }
      expect(fails).toEqual([]);
    });
    it(`${theme}: boundary pairs (control border, focus ring, selected bar) ≥ 3:1`, () => {
      const fails: string[] = [];
      for (const p of PAIRS.boundary) for (const bg of surfacesOf(p.on)) {
        const r = contrastRatio(tok[p.fg], tok[bg]);
        if (r === null || r < 3) fails.push(`${p.fg} on ${bg}: ${r?.toFixed(2) ?? "unparseable"}`);
      }
      expect(fails).toEqual([]);
    });
  }

  it("the old --t3 really did fail — the gate would have caught it", () => {
    expect(contrastRatio("#65656f", "#1a1b20")!).toBeLessThan(4.5);
  });

  it("the old names are aliases of the semantic tokens, so unmigrated surfaces follow them", () => {
    for (const [old, now] of [["--t3", "--text-3"], ["--bg", "--surface-0"], ["--panel2", "--surface-3"], ["--line", "--border-subtle"], ["--green", "--success"]]) {
      expect(T.dark[old]).toBe(T.dark[now]);
      expect(T.light[old]).toBe(T.light[now]);
    }
  });
});

describe("G3 — every token reference resolves", () => {
  // Variables defined outside tokens.css on purpose: globals.css (the dockview / allotment theme
  // hooks) and the fonts' own variables, set by next/font on <html>.
  const definedElsewhere = new Set([...GLOBALS_CSS.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]));
  const EXTERNAL = /^--(dv-|font-geist-|tw-|color-)/;
  // A component may define a custom property inline and read it in the same subtree.
  const definedInline = new Set(SOURCES.flatMap((s) => [...s.text.matchAll(/["'](--[\w-]+)["']\s*:/g)].map((m) => m[1])));

  it("no var(--x) names a token that does not exist", () => {
    const missing: string[] = [];
    for (const s of SOURCES) {
      if (s.rel === "app/tokens.css") continue;
      for (const m of s.text.matchAll(/var\((--[\w-]+)/g)) {
        const n = m[1];
        if (T.defined.has(n) || definedElsewhere.has(n) || definedInline.has(n) || EXTERNAL.test(n)) continue;
        missing.push(`${s.rel}: ${n}`);
      }
    }
    expect([...new Set(missing)]).toEqual([]);
  });

  it("no colour hides behind a var() fallback", () => {
    const bad: string[] = [];
    for (const s of SOURCES) {
      for (const m of s.text.matchAll(/var\(--[\w-]+\s*,\s*(#[0-9a-fA-F]{3,8}|rgba?\(|hsla?\()/g)) bad.push(`${s.rel}: ${m[0]}`);
    }
    expect(bad).toEqual([]);
  });
});

describe("G1 — raw colours outside tokens.css (report only until Phase 3a)", () => {
  it("is counted", () => {
    const ALLOW = new Set(["app/tokens.css", "app/AuthGate.tsx", "ui-kit/docDiagrams.tsx", "app/api/auth/redeem/route.ts", "surfaces/routines.tsx"]);
    let n = 0;
    const where: Record<string, number> = {};
    for (const s of SOURCES) {
      if (ALLOW.has(s.rel)) continue;
      const k = (s.text.match(/#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b(?![\w-])|rgba?\(\s*\d/g) ?? []).length;
      if (k) { n += k; where[s.rel] = k; }
    }
    console.info(`G1 report: ${n} raw colour literal(s) outside tokens.css in ${Object.keys(where).length} file(s)`);
    expect(n).toBeGreaterThanOrEqual(0);
  });
});
