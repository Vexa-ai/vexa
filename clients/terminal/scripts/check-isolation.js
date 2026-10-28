#!/usr/bin/env node
// gate:isolation (P2) — @vexa/terminal is a Next.js app; its boundary is: every import is
// (a) intra-package — a relative path OR the `@/*` tsconfig alias (→ ./src/*), (b) a Node/
// browser builtin, or (c) a DECLARED dep in package.json. An undeclared bare/npm import →
// violation (the app must declare what it pulls in, so it installs + builds standalone).
// ESM — the app declares `"type": "module"`, so node loads this .js as ESM directly. Without that
// declaration node still reparsed it as ESM, but emitted a ~230-char MODULE_TYPELESS_PACKAGE_JSON
// warning on stderr, and gate:isolation only forwards the first 300 chars of a failing brick's
// stderr: the warning pushed the actual violation list out of the diagnostic. A gate whose failure
// message is a warning about module resolution tells the reader nothing about what it caught.
import { readFileSync, readdirSync } from "node:fs";
import { join, relative, dirname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { builtinModules } from "node:module";

const here = dirname(fileURLToPath(import.meta.url));
const ROOT = join(here, "..");
const SRC = join(ROOT, "src");
const pkg = JSON.parse(readFileSync(join(ROOT, "package.json"), "utf8"));
const deps = new Set([...Object.keys(pkg.dependencies || {}), ...Object.keys(pkg.devDependencies || {})]);
const builtins = new Set(builtinModules);
/** Comments are not code, and this gate reads them as code without this.
 *
 *  ⚠ 2026-09-02: the gate failed the whole push on three files whose "undeclared dependencies" were
 *  `a link was clicked` and `this turn did not come from an arrival` — English, inside doc comments,
 *  because the specifier regex matches the word `from` followed by anything quoted no matter where
 *  it sits. Prose about a link, in a file about links, is exactly the prose these files should
 *  contain, and a gate that forbids it is not protecting the boundary — it is taxing the comments.
 *
 *  It is the same shape as the defects this gate exists to catch: it reported a real-looking
 *  violation, with file names and specifiers, and was wrong. A gate that contradicts observable
 *  state should be read before it is obeyed.
 *
 *  Strips block and line comments, leaving string literals intact (a `//` inside a quote is not a
 *  comment, and a URL in a string must not eat the rest of its line). Deliberately small: this is a
 *  boundary check, not a parser, and every import it needs to see survives this.
 */
function stripComments(src) {
  let out = "";
  let quote = null;          // the quote char we are inside, or null
  let block = false;         // inside a /* … */
  for (let i = 0; i < src.length; i++) {
    const c = src[i], n = src[i + 1];
    if (block) { if (c === "*" && n === "/") { block = false; i++; } continue; }
    if (quote) {
      if (c === "\\") { out += c + (n ?? ""); i++; continue; }   // an escape consumes its pair
      if (c === quote) quote = null;
      out += c;
      continue;
    }
    if (c === "/" && n === "*") { block = true; i++; continue; }
    if (c === "/" && n === "/") { while (i < src.length && src[i] !== "\n") i++; out += "\n"; continue; }
    if (c === "'" || c === '"' || c === "`") quote = c;
    out += c;
  }
  return out;
}

/** The import forms this gate must see, and only those.
 *
 *  ⚠ 2026-09-02, second occurrence: the specifier regex was `from\s+['"]…['"]` with no anchor, so
 *  it read the words `from "…"` as an import ANYWHERE they appeared — including inside a string
 *  literal and inside a regular-expression literal. Stripping comments (below) fixed the prose
 *  half; a regex literal is code, survives that strip, and failed a push the same day. Both are
 *  the same defect: a gate reporting a real-looking violation, with a file name and a specifier,
 *  and being wrong.
 *
 *  A static import or re-export is a STATEMENT: it begins a line. Anchoring at line start is what
 *  separates the keyword from the same six letters quoted inside an expression — and the clause
 *  may span lines but never crosses a quote or a semicolon, so it cannot run out of its own
 *  statement into the next one.
 *
 *  1. `import … from "x"` / `export … from "x"` — the multi-line `{ a, b }` clause included.
 *  2. `import "x"` — the side-effect form, which the previous regex did not see at all.
 *  3. `require("x")`, wherever it sits — a call, not a statement, so it cannot be anchored; the
 *     preceding-character guard keeps `myrequire(` out.
 *
 *  Deliberately NOT a form: a bare `import("x")`. In TypeScript those same characters are also the
 *  TYPE-QUERY form — `Content: import("mdx/types").MDXContent` (ui-kit/MdxDoc.tsx:268) names a type
 *  package that is never installed as a runtime dependency and must not be reported as one. Telling
 *  a type query from a dynamic import needs a parser, which this is deliberately not, and no file in
 *  src/ loads a runtime module that way.
 *
 *  Known and accepted: a multi-line template literal containing a line that itself begins with
 *  `import … from "…"` (a code sample in a string) still matches form 1. Nothing in this tree does
 *  that, and separating it needs a real parser, which this is deliberately not.
 */
const IMPORT_SPECIFIER = new RegExp([
  String.raw`^[ \t]*(?:import|export)\b(?:[^;'"\n]|\n)*?\bfrom\s*['"]([^'"\n]+)['"]`,
  String.raw`^[ \t]*import\s+['"]([^'"\n]+)['"]`,
  String.raw`(?<![\w$.])require\(\s*['"]([^'"\n]+)['"]\s*\)`,
].join("|"), "gm");

let files = 0;
const violations = [];
/** FOLDER CYCLES (architecture pass 4, S7). dependency-cruiser's no-circular works per FILE, so
 *  top-level folders of src/ can import each other through different files and no gate sees it:
 *  canvas/ ⇄ minutes/ did, through a shared vocabulary of event names that lived in canvas/actions.
 *  That vocabulary now lives in platform/ (events.ts, turnMarks.ts), below every surface.
 *
 *  This records which folder imports which (production files only; tests may reach across) and
 *  refuses ANY cycle among folders — every strongly connected component of two or more, a chain
 *  a → b → c → a as much as a pair — and prints the cycle's path. Until Vexa-ai/vexa#1783 it refused
 *  only mutual pairs, against an allow-list of eight known ones; that work emptied the list, so the
 *  list is gone: no cycle is allowed. The folders form layers, each importing only the ones below:
 *  platform < contributions · data < ui-kit < surfaces < minutes < canvas, with workbench above
 *  surfaces and app above all. What a lower folder renders from a higher one it reaches by kind
 *  through contributions/, never by import. */
const folderEdges = new Map();   // "from" -> Set("to")
const folderOf = (abs) => { const r = relative(SRC, abs); return r.startsWith("..") ? null : r.split(sep)[0]; };
const isTest = (p) => p.includes(`${sep}__tests__${sep}`) || /\.test\.tsx?$/.test(p);
/** A lazy `import("../x")` is a runtime edge too: ui-kit once reached surfaces/ and minutes/ that way
 *  so that no static cycle showed. Relative specifiers only, so the type-query ambiguity above does not
 *  arise for a package name. */
const LAZY_RELATIVE = /(?<![\w$.])import\(\s*['"](\.[^'"\n]+)['"]\s*\)/g;
const noteEdge = (p, spec) => {
  const target = spec.startsWith("@/") ? join(SRC, spec.slice(2)) : resolve(dirname(p), spec);
  const from = folderOf(p), to = folderOf(target);
  if (isTest(p) || !from || !to || from === to || from.includes(".") || to.includes(".")) return;
  if (!folderEdges.has(from)) folderEdges.set(from, new Set());
  folderEdges.get(from).add(to);
};
(function walk(d) {
  for (const e of readdirSync(d, { withFileTypes: true })) {
    const p = join(d, e.name);
    if (e.isDirectory()) walk(p);
    else if (e.name.endsWith(".ts") || e.name.endsWith(".tsx")) {
      files++;
      const src = stripComments(readFileSync(p, "utf8"));
      for (const m of src.matchAll(LAZY_RELATIVE)) noteEdge(p, m[1]);
      for (const m of src.matchAll(IMPORT_SPECIFIER)) {
        const spec = m[1] || m[2] || m[3];
        if (spec.includes("${")) continue;                             // a `from "${x}"` substring inside a template/string literal, not a real import
        if (spec.startsWith(".") || spec.startsWith("@/")) { noteEdge(p, spec); continue; }  // intra-package (relative or @/* alias)
        const bare = spec.startsWith("node:") ? spec.slice(5) : spec;
        const scoped = bare.startsWith("@") ? bare.split("/").slice(0, 2).join("/") : bare.split("/")[0];
        if (builtins.has(bare) || builtins.has(scoped)) continue;       // builtin (± node: prefix)
        if (deps.has(spec) || deps.has(bare) || deps.has(scoped)) continue;  // declared dep
        violations.push(`${relative(SRC, p)} → ${spec}`);
      }
    }
  }
})(SRC);
if (violations.length) { console.error("❌ ISOLATION VIOLATION (undeclared dep):\n  " + violations.join("\n  ")); process.exit(1); }
// Tarjan: every strongly connected component of the folder graph. Size ≥ 2 is a cycle (a folder
// never edges to itself — noteEdge drops from === to).
const sccs = [];
{
  const nodes = new Set([...folderEdges.keys(), ...[...folderEdges.values()].flatMap((t) => [...t])]);
  const index = new Map(), low = new Map(), stack = [], on = new Set();
  let i = 0;
  const visit = (v) => {
    index.set(v, i); low.set(v, i); i++; stack.push(v); on.add(v);
    for (const w of [...(folderEdges.get(v) || [])].sort()) {
      if (!index.has(w)) { visit(w); low.set(v, Math.min(low.get(v), low.get(w))); }
      else if (on.has(w)) low.set(v, Math.min(low.get(v), index.get(w)));
    }
    if (low.get(v) === index.get(v)) {
      const comp = [];
      let w;
      do { w = stack.pop(); on.delete(w); comp.push(w); } while (w !== v);
      if (comp.length > 1) sccs.push(comp.sort());
    }
  };
  for (const v of [...nodes].sort()) if (!index.has(v)) visit(v);
}
/** One concrete cycle through a component, as a path that returns to its start: BFS from the
 *  component's first folder back to itself, staying inside the component. */
const cyclePath = (comp) => {
  const inside = new Set(comp), start = comp[0];
  const prev = new Map(), queue = [start];
  const seen = new Set();
  while (queue.length) {
    const v = queue.shift();
    for (const w of [...(folderEdges.get(v) || [])].sort()) {
      if (!inside.has(w)) continue;
      if (w === start) { const path = [start]; for (let u = v; u !== start; u = prev.get(u)) path.splice(1, 0, u); return [...path, start].join(" -> "); }
      if (!seen.has(w)) { seen.add(w); prev.set(w, v); queue.push(w); }
    }
  }
  return comp.join(", ");
};
if (sccs.length) {
  console.error("❌ FOLDER CYCLE (src/ folders that reach each other through imports):\n  " +
    sccs.map((c) => `${cyclePath(c)}   [component: ${c.join(", ")}]`).join("\n  ") +
    "\n  Move what both need below both (platform/ holds shared vocabulary), or have the lower folder reach the higher one by kind through contributions/.");
  process.exit(1);
}
console.log(`✅ ISOLATION VERIFIED — scanned ${files} files in src/; every import intra-package (./ or @/), builtin, or declared dep; no folder cycle (the src/ folders are layered).`);
