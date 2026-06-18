#!/usr/bin/env node
// gate:isolation (P2) — every import must stay inside the package: intra-package,
// a Node builtin, or a DECLARED dep. @vexa/transcribe-whisper is the stt.v1 egress;
// it uses only Node/Web globals (fetch, Buffer, AbortController) — no external dep,
// no brick internals, no monolith back-import.
// ESM (the package is "type":"module"); the gate runs `node scripts/check-isolation.js`.
import { readFileSync, readdirSync } from "node:fs";
import { join, relative, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { builtinModules } from "node:module";

const here = dirname(fileURLToPath(import.meta.url));
const SRC = join(here, "..", "src");
const pkg = JSON.parse(readFileSync(join(here, "..", "package.json"), "utf8"));
const deps = new Set([...Object.keys(pkg.dependencies || {}), ...Object.keys(pkg.devDependencies || {})]);
const builtins = new Set(builtinModules);
let files = 0;
const violations = [];
(function walk(d) {
  for (const e of readdirSync(d, { withFileTypes: true })) {
    const p = join(d, e.name);
    if (e.isDirectory()) walk(p);
    else if (e.name.endsWith(".ts")) {
      files++;
      const src = readFileSync(p, "utf8");
      for (const m of src.matchAll(/from\s+['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\)/g)) {
        const spec = m[1] || m[2];
        if (spec.startsWith(".")) continue;                 // intra-package
        const scoped = spec.startsWith("@") ? spec.split("/").slice(0, 2).join("/") : spec.split("/")[0];
        if (builtins.has(spec) || builtins.has(scoped)) continue;
        if (deps.has(spec) || deps.has(scoped)) continue;
        violations.push(`${relative(SRC, p)} → ${spec}`);
      }
    }
  }
})(SRC);
if (violations.length) { console.error("❌ ISOLATION VIOLATION:\n  " + violations.join("\n  ")); process.exit(1); }
console.log(`✅ ISOLATION VERIFIED — scanned ${files} files in src/; every import intra-package, builtin, or declared dep.`);
