#!/usr/bin/env node
// gate:isolation (P2) — every import must stay inside the package: intra-package, a Node builtin, or
// a DECLARED dep. @vexa/remote-browser is a Node brick: it may pull `playwright` / `playwright-extra`
// (the persistent-context launch) + Node builtins (child_process/fs/path — local + S3-via-aws-CLI
// session store) — never another brick's internals, never the bot/service (one-way rule: services
// import bricks). Carved from vexa-bot/core/src/{s3-sync,browser-session,constans}.ts.
// CommonJS (the package is "type":"commonjs", so this .js loads as CJS — `require`, not `import`);
// the gate runs `node scripts/check-isolation.js`.
const { readFileSync, readdirSync } = require("node:fs");
const { join, relative } = require("node:path");
const { builtinModules } = require("node:module");

const SRC = join(__dirname, "..", "src");
const pkg = JSON.parse(readFileSync(join(__dirname, "..", "package.json"), "utf8"));
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
        if (builtins.has(bare) || builtins.has(scoped)) continue;       // Node builtin (± node: prefix)
        if (deps.has(spec) || deps.has(bare) || deps.has(scoped)) continue;  // declared dep
        violations.push(`${relative(SRC, p)} → ${spec}`);
      }
    }
  }
})(SRC);
if (violations.length) { console.error("❌ ISOLATION VIOLATION:\n  " + violations.join("\n  ")); process.exit(1); }
console.log(`✅ ISOLATION VERIFIED — scanned ${files} files in src/; every import intra-package, builtin, or declared dep.`);
