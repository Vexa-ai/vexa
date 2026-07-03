#!/usr/bin/env node
// gate:isolation (P2) — @vexa/extension SOURCE-BUNDLES sibling capture bricks on purpose:
// build.mjs maps each `@vexa/<brick>` specifier to that brick's src/index.ts (esbuild alias),
// so the extension ships one self-contained bundle. Its boundary is therefore: every import is
// (a) intra-package (relative), (b) a Node/browser builtin, (c) a DECLARED dep, or (d) a
// `@vexa/*` specifier EXPLICITLY WIRED in build.mjs's alias map. An import of an UNWIRED brick
// (would silently fail to bundle) → violation. ESM; the gate runs `node scripts/check-isolation.js`.
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
// the @vexa specifiers explicitly wired for source-bundling in build.mjs
const buildMjs = readFileSync(join(ROOT, "build.mjs"), "utf8");
const wired = new Set([...buildMjs.matchAll(/['"](@vexa\/[^'"]+)['"]\s*:/g)].map((m) => m[1]));
let files = 0;
const violations = [];
(function walk(d) {
  for (const e of readdirSync(d, { withFileTypes: true })) {
    const p = join(d, e.name);
    if (e.isDirectory()) walk(p);
    else if (e.name.endsWith(".ts") || e.name.endsWith(".tsx")) {
      files++;
      const src = readFileSync(p, "utf8");
      for (const m of src.matchAll(/from\s+['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\)/g)) {
        const spec = m[1] || m[2];
        if (spec.startsWith(".")) continue;                 // intra-package
        if (wired.has(spec)) continue;                      // source-bundled brick (build.mjs alias)
        const bare = spec.startsWith("node:") ? spec.slice(5) : spec;
        const scoped = bare.startsWith("@") ? bare.split("/").slice(0, 2).join("/") : bare.split("/")[0];
        if (builtins.has(bare) || builtins.has(scoped)) continue;       // builtin (± node: prefix)
        if (deps.has(spec) || deps.has(bare) || deps.has(scoped)) continue;  // declared dep
        violations.push(`${relative(SRC, p)} → ${spec}`);
      }
    }
  }
})(SRC);
if (violations.length) { console.error("❌ ISOLATION VIOLATION (undeclared dep or unwired @vexa brick):\n  " + violations.join("\n  ")); process.exit(1); }
console.log(`✅ ISOLATION VERIFIED — scanned ${files} files; every import intra-package, builtin, declared dep, or a build.mjs-wired brick (${wired.size} wired).`);
