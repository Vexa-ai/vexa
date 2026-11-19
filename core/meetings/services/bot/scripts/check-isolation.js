#!/usr/bin/env node
// gate:isolation (P2) — a service composes its domain's bricks by their published packages.
// @vexa/bot may import @vexa/{join,remote-browser,recording,record-chunker,gmeet-pipeline,
// mixed-pipeline,transcribe-whisper,zoom-sdk-capture} + ajv/ajv-formats (invocation.v1 + lifecycle.v1
// boot validation, and the native runtime's sdk-join.v1/sdk-capture.v1 IPC) + declared devDeps — never
// another brick's internals, never another domain, and never a relative path out of this package.
// The orchestrator CORE imports only its own ports + node builtins; the @vexa/* fronts are
// touched only at the composition root (src/index.ts). Two trees are scanned: src/ (TypeScript) and
// runtime/ (the native meeting process, .mjs/.cjs). ESM (the package is "type":"module");
// the gate runs `node scripts/check-isolation.js`.
import { readFileSync, readdirSync } from "node:fs";
import { join, relative, dirname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { builtinModules } from "node:module";

const here = dirname(fileURLToPath(import.meta.url));
const PKG = join(here, "..");
const TREES = [[join(PKG, "src"), /\.ts$/], [join(PKG, "runtime"), /\.(mjs|cjs|js)$/]];
const pkg = JSON.parse(readFileSync(join(here, "..", "package.json"), "utf8"));
const deps = new Set([...Object.keys(pkg.dependencies || {}), ...Object.keys(pkg.devDependencies || {})]);
const builtins = new Set(builtinModules);
let files = 0;
const violations = [];
const walk = (d, pattern) => {
  for (const e of readdirSync(d, { withFileTypes: true })) {
    const p = join(d, e.name);
    if (e.isDirectory()) { if (e.name !== "node_modules" && e.name !== "native") walk(p, pattern); }
    else if (pattern.test(e.name)) {
      files++;
      const src = readFileSync(p, "utf8");
      for (const m of src.matchAll(/(?:from|import)\s+['"]([^'"]+)['"]|(?:require|import)\(\s*['"]([^'"]+)['"]\s*\)/g)) {
        const spec = m[1] || m[2];
        if (spec.startsWith(".")) {                         // intra-package, unless it climbs out
          if (!(resolve(dirname(p), spec) + sep).startsWith(PKG + sep)) violations.push(`${relative(PKG, p)} → ${spec} (outside the package)`);
          continue;
        }
        const bare = spec.startsWith("node:") ? spec.slice(5) : spec;   // node:fs ≡ fs
        const scoped = bare.startsWith("@") ? bare.split("/").slice(0, 2).join("/") : bare.split("/")[0];
        if (spec.startsWith("node:") || builtins.has(bare) || builtins.has(scoped)) continue;  // node:test is node:-only       // Node builtin (± node: prefix)
        if (deps.has(spec) || deps.has(bare) || deps.has(scoped)) continue;  // declared dep
        violations.push(`${relative(PKG, p)} → ${spec}`);
      }
    }
  }
};
for (const [tree, pattern] of TREES) walk(tree, pattern);
if (violations.length) { console.error("❌ ISOLATION VIOLATION:\n  " + violations.join("\n  ")); process.exit(1); }
console.log(`✅ ISOLATION VERIFIED — scanned ${files} files in src/ and runtime/; every import intra-package, builtin, or declared dep.`);
