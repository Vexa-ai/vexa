/** G2r — the inline-style RATCHET (terminal design guidelines §8, G2/G2r).
 *
 *  An inline `style` may carry layout geometry only; colour, type, spacing, border, radius, shadow,
 *  layer and motion belong to primitives and tokens. Until every surface has migrated,
 *  `src/style-budget.json` records how many banned keys each file still writes, and this test
 *  fails when a file goes UP (or a new file starts above zero). Going down is the migration; when a
 *  file's count drops, lower its budget in the same commit (`UPDATE_STYLE_BUDGET=1 npx vitest run
 *  styleBudget` rewrites the file with the current counts, and refuses to raise any). */
import { readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..", "..");
const BUDGET_FILE = join(SRC, "style-budget.json");

export const BANNED = new Set([
  "color", "background", "backgroundColor", "backgroundImage", "border", "borderTop", "borderRight", "borderBottom", "borderLeft",
  "borderColor", "borderWidth", "borderStyle", "borderRadius", "fontSize", "fontWeight", "fontFamily", "fontStyle", "letterSpacing",
  "lineHeight", "padding", "paddingTop", "paddingRight", "paddingBottom", "paddingLeft", "paddingInline", "paddingBlock",
  "margin", "marginTop", "marginRight", "marginBottom", "marginLeft", "marginInline", "marginBlock", "boxShadow", "zIndex",
  "outline", "textTransform", "transition", "animation", "textDecoration",
]);

/** Count banned keys in object literals that are (or flow into) a `style`: JSX `style={{…}}`, and
 *  object literals typed or named as styles (`const x: CSSProperties = {…}`, spreads in style). A
 *  conservative over-count: every property named in BANNED inside an object literal that sits in a
 *  `style` attribute or in a variable whose type annotation mentions CSSProperties. */
export function countBanned(source: string, file = "x.tsx"): number {
  const sf = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, file.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  let n = 0;
  const countObj = (o: ts.ObjectLiteralExpression) => {
    for (const p of o.properties) {
      if (ts.isPropertyAssignment(p) || ts.isShorthandPropertyAssignment(p)) {
        const name = p.name && (ts.isIdentifier(p.name) || ts.isStringLiteral(p.name)) ? p.name.text : "";
        if (BANNED.has(name)) n++;
      }
    }
  };
  const inStyleContext = (node: ts.Node): boolean => {
    for (let p: ts.Node | undefined = node.parent; p; p = p.parent) {
      if (ts.isJsxAttribute(p)) return p.name.getText(sf) === "style";
      if (ts.isVariableDeclaration(p) || ts.isPropertyDeclaration(p)) return !!p.type && /CSSProperties/.test(p.type.getText(sf));
      if (ts.isAsExpression(p) || ts.isSatisfiesExpression(p)) { if (/CSSProperties/.test(p.type.getText(sf))) return true; }
      if (ts.isArrowFunction(p) || ts.isFunctionDeclaration(p)) { if (p.type && /CSSProperties/.test(p.type.getText(sf))) return true; }
      if (ts.isSourceFile(p)) return false;
    }
    return false;
  };
  const visit = (node: ts.Node) => {
    if (ts.isObjectLiteralExpression(node) && inStyleContext(node)) countObj(node);
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return n;
}

function* files(dir: string): Generator<string> {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) { if (name !== "__tests__" && name !== "node_modules") yield* files(p); }
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) && !name.endsWith(".d.ts")) yield p;
  }
}

export function currentCounts(): Record<string, number> {
  const out: Record<string, number> = {};
  for (const f of files(SRC)) {
    const n = countBanned(readFileSync(f, "utf8"), f);
    if (n > 0) out[f.slice(SRC.length + 1)] = n;
  }
  return Object.fromEntries(Object.entries(out).sort(([a], [b]) => a.localeCompare(b)));
}

/** G1 — raw colour literals (hex, rgb/rgba, hsl) outside tokens.css. Brand marks and the diagram
 *  palette carry them by design; everything else is a token that has not been named yet. */
const COLOUR_RE = /#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b(?![\w-])|\brgba?\(\s*\d|\bhsla?\(\s*\d/g;
export function countColours(source: string): number {
  return (source.replace(/\/\*[\s\S]*?\*\/|(^|[^:])\/\/[^\n]*/g, "$1").match(COLOUR_RE) ?? []).length;
}
/** G5 — `outline: none` / `outline: 0` written outside the primitives' stylesheets: the focus ring
 *  is restored globally (controls.css), and no new site may remove it again. */
const OUTLINE_RE = /outline\s*:\s*["']?(none|0)\b/g;
export function countOutlineNone(source: string): number { return (source.match(OUTLINE_RE) ?? []).length; }

function* allFiles(dir: string): Generator<string> {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) { if (name !== "__tests__" && name !== "node_modules") yield* allFiles(p); }
    else if (/\.(tsx?|css)$/.test(name) && !/\.test\.tsx?$/.test(name) && !name.endsWith(".d.ts")) yield p;
  }
}
function countsBy(fn: (s: string) => number, skip: (rel: string) => boolean): Record<string, number> {
  const out: Record<string, number> = {};
  for (const f of allFiles(SRC)) {
    const rel = f.slice(SRC.length + 1);
    if (skip(rel)) continue;
    const n = fn(readFileSync(f, "utf8"));
    if (n > 0) out[rel] = n;
  }
  return Object.fromEntries(Object.entries(out).sort(([a], [b]) => a.localeCompare(b)));
}
const RATCHETS: { id: string; key: "files" | "rawColours" | "outlineNone"; counts: () => Record<string, number> }[] = [
  { id: "G2 inline style keys", key: "files", counts: currentCounts },
  { id: "G1 raw colours", key: "rawColours", counts: () => countsBy(countColours, (r) => r === "app/tokens.css" || r.startsWith("ui-kit/primitives/")) },
  { id: "G5 outline:none", key: "outlineNone", counts: () => countsBy(countOutlineNone, (r) => r.startsWith("ui-kit/primitives/") || r.startsWith("ui-kit/layout/")) },
];

describe("G2r — the design budgets only go down (G2 inline style keys, G1 raw colours, G5 outline:none)", () => {
  const budget = JSON.parse(readFileSync(BUDGET_FILE, "utf8")) as Record<string, unknown> & { files: Record<string, number>; rawColours?: Record<string, number>; outlineNone?: Record<string, number> };
  const now = Object.fromEntries(RATCHETS.map((r) => [r.key, r.counts()])) as Record<string, Record<string, number>>;
  const init = process.env.UPDATE_STYLE_BUDGET === "init";
  for (const r of RATCHETS) if (init && !budget[r.key]) { (budget as Record<string, unknown>)[r.key] = now[r.key]; }
  if (init) writeFileSync(BUDGET_FILE, JSON.stringify(budget, null, 2) + "\n");

  for (const r of RATCHETS) {
    it(`${r.id}: no file goes over its budget (a new file: zero)`, () => {
      const b = (budget[r.key] ?? {}) as Record<string, number>;
      const over = Object.entries(now[r.key]).filter(([f, n]) => n > (b[f] ?? 0)).map(([f, n]) => `${f}: ${n} > ${b[f] ?? 0}`);
      expect(over).toEqual([]);
    });
    it(`${r.id}: a file that went down has its budget lowered`, () => {
      const b = (budget[r.key] ?? {}) as Record<string, number>;
      const slack = Object.entries(b).filter(([f, n]) => (now[r.key][f] ?? 0) < n).map(([f, n]) => `${f}: budget ${n}, now ${now[r.key][f] ?? 0}`);
      if (process.env.UPDATE_STYLE_BUDGET === "1" && slack.length) {
        const fresh = JSON.parse(readFileSync(BUDGET_FILE, "utf8"));
        const lowered = Object.fromEntries(Object.entries(b).map(([f, n]) => [f, Math.min(n, now[r.key][f] ?? 0)]).filter(([, n]) => (n as number) > 0));
        fresh[r.key] = lowered;
        writeFileSync(BUDGET_FILE, JSON.stringify(fresh, null, 2) + "\n");
        return;
      }
      expect(slack).toEqual([]);
    });
  }

  it("the counter sees what it should", () => {
    expect(countBanned(`const a = <div style={{ display: "flex", color: "red", padding: 4 }} />;`)).toBe(2);
    expect(countBanned(`const s: CSSProperties = { fontSize: 12, gap: 4 };`)).toBe(1);
    expect(countBanned(`const o = { color: "red" };`)).toBe(0);
  });
});

describe("planted budget violations are caught (Phase 3a)", () => {
  it("a new raw colour, a one-off font size and a removed focus ring each count", () => {
    expect(countColours(`<span style={{ color: "#ff0000" }} />`)).toBe(1);
    expect(countColours(`/* #ffffff in a comment */ const ok = 1;`)).toBe(0);
    expect(countBanned(`const a = <span style={{ fontSize: 17 }} />;`)).toBe(1);
    expect(countOutlineNone(`<input style={{ outline: "none" }} />`)).toBe(1);
  });
});
