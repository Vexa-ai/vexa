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

describe("G2r — inline style budget only goes down", () => {
  const budget = JSON.parse(readFileSync(BUDGET_FILE, "utf8")) as { files: Record<string, number> };
  const now = currentCounts();
  if (process.env.UPDATE_STYLE_BUDGET === "init" && Object.keys(budget.files).length === 0) {
    writeFileSync(BUDGET_FILE, JSON.stringify({ ...budget, files: now }, null, 2) + "\n");
    budget.files = now;
  }

  it("no file writes more banned style keys than its budget (a new file: zero)", () => {
    const over = Object.entries(now).filter(([f, n]) => n > (budget.files[f] ?? 0)).map(([f, n]) => `${f}: ${n} > ${budget.files[f] ?? 0}`);
    if ((process.env.UPDATE_STYLE_BUDGET === "1" && over.length === 0) || (process.env.UPDATE_STYLE_BUDGET === "init" && Object.keys(budget.files).length === 0)) {
      writeFileSync(BUDGET_FILE, JSON.stringify({ ...budget, files: now }, null, 2) + "\n");
    }
    expect(over).toEqual([]);
  });

  it("the budget is not stale: a file that went down has its budget lowered", () => {
    const slack = Object.entries(budget.files).filter(([f, n]) => (now[f] ?? 0) < n).map(([f, n]) => `${f}: budget ${n}, now ${now[f] ?? 0}`);
    expect(slack).toEqual([]);
  });

  it("the counter sees what it should", () => {
    expect(countBanned(`const a = <div style={{ display: "flex", color: "red", padding: 4 }} />;`)).toBe(2);
    expect(countBanned(`const s: CSSProperties = { fontSize: 12, gap: 4 };`)).toBe(1);
    expect(countBanned(`const o = { color: "red" };`)).toBe(0);
  });
});
