#!/usr/bin/env node
// gate:isolation (P2) — every import must stay inside the package: intra-package, a Node builtin, or
// a DECLARED dep. @vexa/zoom-sdk-capture converts native SDK audio into the shared CaptureV1Sink: it
// may import @vexa/capture-codec and Node builtins only. It never imports the native runtime, the join
// module or the bot; the runtime's capture port is injected (P17, ADR-0039).
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
        const bare = spec.startsWith("node:") ? spec.slice(5) : spec;   // node:fs ≡ fs
        const scoped = bare.startsWith("@") ? bare.split("/").slice(0, 2).join("/") : bare.split("/")[0];
        if (spec.startsWith("node:") || builtins.has(bare) || builtins.has(scoped)) continue;  // node:test is node:-only       // Node builtin (± node: prefix)
        if (deps.has(spec) || deps.has(bare) || deps.has(scoped)) continue;  // declared dep
        violations.push(`${relative(SRC, p)} → ${spec}`);
      }
    }
  }
})(SRC);
if (violations.length) { console.error("❌ ISOLATION VIOLATION:\n  " + violations.join("\n  ")); process.exit(1); }
console.log(`✅ ISOLATION VERIFIED — scanned ${files} files in src/; every import intra-package, builtin, or declared dep.`);
