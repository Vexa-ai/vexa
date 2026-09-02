#!/usr/bin/env node
// gate:isolation (P2) — @vexa/terminal is a Next.js app; its boundary is: every import is
// (a) intra-package — a relative path OR the `@/*` tsconfig alias (→ ./src/*), (b) a Node/
// browser builtin, or (c) a DECLARED dep in package.json. An undeclared bare/npm import →
// violation (the app must declare what it pulls in, so it installs + builds standalone).
// Dynamic `import(`${expr}`)` (template literals) are not static specifiers and are skipped.
// ESM ("type":"module" not set on this app, but node runs .js as ESM here via the import syntax
// — the gate invokes `node scripts/check-isolation.js`).
import { readFileSync, readdirSync } from "node:fs";
import { join, relative, dirname } from "node:path";
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

let files = 0;
const violations = [];
(function walk(d) {
  for (const e of readdirSync(d, { withFileTypes: true })) {
    const p = join(d, e.name);
    if (e.isDirectory()) walk(p);
    else if (e.name.endsWith(".ts") || e.name.endsWith(".tsx")) {
      files++;
      const src = stripComments(readFileSync(p, "utf8"));
      for (const m of src.matchAll(/from\s+['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\)/g)) {
        const spec = m[1] || m[2];
        if (spec.includes("${")) continue;                             // a `from "${x}"` substring inside a template/string literal, not a real import
        if (spec.startsWith(".") || spec.startsWith("@/")) continue;    // intra-package (relative or @/* alias)
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
console.log(`✅ ISOLATION VERIFIED — scanned ${files} files in src/; every import intra-package (./ or @/), builtin, or declared dep.`);
