#!/usr/bin/env node
/**
 * design-codemod — moves inline style literals onto the token utilities (terminal design guidelines
 * §2; src/ui-kit/primitives/utilities.css), SNAPPING every value to the scale.
 *
 *   node scripts/design-codemod.mjs src/surfaces/workspace.tsx [more files…] [--dry]
 *
 * For each JSX `style={{ … }}` whose properties are STATIC literals, the properties it understands
 * (font size and weight, line height, mono/sans family, text colour, background, border, radius,
 * padding and margin) become classes — `fontSize: 11.5, color: "var(--t3)"` → `t-xs c-3` — and
 * leave the style object; what it does not understand stays inline. A value that is an expression
 * (a ternary, a variable) is left alone, so behaviour never changes; an element whose `className`
 * is already an expression is skipped whole. Values are snapped: 37 font sizes collapse onto 6,
 * paddings onto the 4px grid, radii onto 4 / 6 / 10 / 14 / full. That snapping is the point — the
 * migration is how a surface comes onto the scale — so review the diff visually (`/design`, app.dev).
 *
 * Not a runtime dependency: uses the `typescript` compiler API already in devDependencies.
 */
import { readFileSync, writeFileSync } from "node:fs";
import ts from "typescript";

const SPACE = [[0, "0"], [2, "0_5"], [4, "1"], [6, "1_5"], [8, "2"], [12, "3"], [16, "4"], [20, "5"], [24, "6"], [32, "8"], [48, "12"], [64, "16"]];
const snapSpace = (px) => SPACE.reduce((b, s) => (Math.abs(s[0] - px) < Math.abs(b[0] - px) ? s : b))[1];
const snapSize = (px) => (px <= 12.5 ? "xs" : px <= 13.5 ? "sm" : px <= 15 ? "md" : px <= 18 ? "lg" : px <= 22 ? "xl" : "2xl");
const snapWeight = (w) => (w < 450 ? "400" : w < 580 ? "500" : "600");
const snapRadius = (r) => (r === 0 ? "0" : r >= 99 ? "full" : r <= 4.5 ? "sm" : r <= 8 ? "md" : r <= 12 ? "lg" : "xl");
const snapLine = (l) => (l <= 1.35 ? "tight" : l <= 1.5 ? "snug" : "normal");
const COLOR = { "--t1": "c-1", "--text-1": "c-1", "--t2": "c-2", "--text-2": "c-2", "--t3": "c-3", "--text-3": "c-3", "--accent": "c-accent", "--accent-text": "c-accent", "--danger": "c-danger", "--danger-text": "c-danger", "--warn": "c-warning", "--warning": "c-warning", "--green": "c-success", "--success": "c-success", "--blue": "c-info", "--info": "c-info", "--violet": "c-meeting", "--meeting": "c-meeting", "--on-accent": "c-on-accent" };
const BG = { "--bg": "bg-0", "--surface-0": "bg-0", "--sidebar": "bg-1", "--rail": "bg-1", "--surface-1": "bg-1", "--panel": "bg-2", "--surface-2": "bg-2", "--panel2": "bg-3", "--surface-3": "bg-3", "--accent": "bg-accent", "--accentbg": "bg-accent-tint", "--dangerbg": "bg-danger-tint", "--warnbg": "bg-warning-tint", "--greenbg": "bg-success-tint", "--bluebg": "bg-info-tint", "--violetbg": "bg-meeting-tint", "--danger": "bg-danger", "--green": "bg-success", "--warn": "bg-warning" };
const varOf = (v) => /^var\((--[\w-]+)\)$/.exec(String(v).trim())?.[1];
const px = (v) => (typeof v === "number" ? v : /^(-?\d+(?:\.\d+)?)px$/.exec(String(v).trim())?.[1] !== undefined ? Number(/^(-?\d+(?:\.\d+)?)px$/.exec(String(v).trim())[1]) : null);

/** One property → classes, or null when it is not ours (it stays inline). */
function classesFor(key, value) {
  switch (key) {
    case "fontSize": { const n = px(value); return n === null ? null : [`t-${snapSize(n)}`]; }
    case "fontWeight": { const n = typeof value === "number" ? value : Number(value); return Number.isFinite(n) ? [`fw-${snapWeight(n)}`] : null; }
    case "lineHeight": { const n = typeof value === "number" ? value : null; return n !== null && n < 3 ? [`lh-${snapLine(n)}`] : null; }
    case "fontFamily": { const v = varOf(value) ?? String(value); if (/mono/.test(v)) return ["f-mono"]; if (/sans/.test(v)) return ["f-sans"]; return value === "inherit" ? null : null; }
    case "color": { if (value === "inherit") return ["c-inherit"]; const v = varOf(value); return v && COLOR[v] ? [COLOR[v]] : null; }
    case "background": case "backgroundColor": {
      if (value === "transparent" || value === "none") return ["bg-none"];
      const v = varOf(value); return v && BG[v] ? [BG[v]] : null;
    }
    case "border": {
      if (value === "none" || value === 0 || value === "0") return ["bd-none"];
      const m = /^1px solid var\((--[\w-]+)\)$/.exec(String(value).trim());
      if (!m) return null;
      return ({ "--line": ["bd"], "--border-subtle": ["bd"], "--line2": ["bd-strong"], "--border": ["bd-strong"], "--accent": ["bd-accent"], "--danger": ["bd-danger"] })[m[1]] ?? null;
    }
    case "borderTop": case "borderBottom": case "borderLeft": case "borderRight": {
      const m = /^1px solid var\(--(line|border-subtle)\)$/.exec(String(value).trim());
      return m ? [`bd-${key.slice(6, 7).toLowerCase()}`] : null;
    }
    case "borderRadius": { const n = px(value) ?? (value === "50%" ? 999 : null); return n === null ? null : [`r-${snapRadius(n)}`]; }
    case "padding": case "margin": {
      const pre = key === "padding" ? "p" : "m";
      if (typeof value === "number") return [`${pre}-${snapSpace(value)}`];
      const parts = String(value).trim().split(/\s+/);
      const vals = parts.map((p) => (p === "auto" ? "auto" : px(p) ?? (p === "0" ? 0 : null)));
      if (vals.some((v) => v === null)) return null;
      const cls = (side, v) => (v === "auto" ? (pre === "m" && (side === "l" || side === "r") ? `m${side}-auto` : null) : `${pre}${side}-${snapSpace(v)}`);
      let t, r, b, l;
      if (vals.length === 1) { if (vals[0] === "auto") return null; return [`${pre}-${snapSpace(vals[0])}`]; }
      if (vals.length === 2) [t, r] = vals, b = t, l = r;
      else if (vals.length === 3) [t, r, b] = vals, l = r;
      else if (vals.length === 4) [t, r, b, l] = vals;
      else return null;
      const out = [cls("t", t), cls("r", r), cls("b", b), cls("l", l)];
      return out.some((c) => c === null) ? null : out;
    }
    case "paddingTop": case "paddingRight": case "paddingBottom": case "paddingLeft":
    case "marginTop": case "marginRight": case "marginBottom": case "marginLeft": {
      const pre = key.startsWith("padding") ? "p" : "m"; const side = key.replace(/^(padding|margin)/, "")[0].toLowerCase();
      if (value === "auto" && pre === "m") return [`m${side}-auto`];
      const n = px(value); return n === null ? null : [`${pre}${side}-${snapSpace(n)}`];
    }
    default: return null;
  }
}

function literal(node) {
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text;
  if (ts.isNumericLiteral(node)) return Number(node.text);
  if (ts.isPrefixUnaryExpression(node) && node.operator === ts.SyntaxKind.MinusToken && ts.isNumericLiteral(node.operand)) return -Number(node.operand.text);
  return undefined;
}

export function transform(source, file = "x.tsx") {
  const sf = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const edits = [];   // { start, end, text }
  let moved = 0;
  const visit = (node) => {
    // only DOM elements (lowercase tags): a component may take `style` and not `className`
    const tag = ts.isJsxAttributes(node) ? node.parent.tagName?.getText(sf) : "";
    if (ts.isJsxAttributes(node) && /^[a-z]/.test(tag ?? "")) {
      const attrs = node.properties;
      const style = attrs.find((a) => ts.isJsxAttribute(a) && a.name.getText(sf) === "style");
      const cn = attrs.find((a) => ts.isJsxAttribute(a) && a.name.getText(sf) === "className");
      const obj = style?.initializer && ts.isJsxExpression(style.initializer) && style.initializer.expression && ts.isObjectLiteralExpression(style.initializer.expression) ? style.initializer.expression : null;
      const cnOk = !cn || (cn.initializer && ts.isStringLiteral(cn.initializer));
      if (obj && cnOk) {
        const keep = []; const add = [];
        for (const p of obj.properties) {
          if (!ts.isPropertyAssignment(p)) { keep.push(p.getText(sf)); continue; }
          const key = p.name.getText(sf).replace(/^["']|["']$/g, "");
          const v = literal(p.initializer);
          const cls = v === undefined ? null : classesFor(key, v);
          if (cls) { add.push(...cls); moved++; } else keep.push(p.getText(sf));
        }
        if (add.length) {
          const existing = cn ? cn.initializer.text : "";
          const classes = [...new Set([...existing.split(/\s+/).filter(Boolean), ...add])].join(" ");
          if (cn) edits.push({ start: cn.getStart(sf), end: cn.getEnd(), text: `className="${classes}"` });
          const styleText = keep.length ? `style={{ ${keep.join(", ")} }}` : "";
          if (cn) edits.push({ start: style.getStart(sf), end: style.getEnd(), text: styleText });
          else edits.push({ start: style.getStart(sf), end: style.getEnd(), text: `className="${classes}"${styleText ? " " + styleText : ""}` });
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  edits.sort((a, b) => b.start - a.start);
  let out = source;
  for (const e of edits) out = out.slice(0, e.start) + e.text + out.slice(e.end);
  return { out, moved };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const files = process.argv.slice(2).filter((a) => !a.startsWith("--"));
  const dry = process.argv.includes("--dry");
  for (const f of files) {
    const src = readFileSync(f, "utf8");
    const { out, moved } = transform(src, f);
    if (!dry && out !== src) writeFileSync(f, out);
    console.log(`${f}: ${moved} propert${moved === 1 ? "y" : "ies"} onto utilities`);
  }
}
