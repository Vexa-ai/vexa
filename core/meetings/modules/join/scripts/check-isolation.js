#!/usr/bin/env node
/**
 * check-isolation.js — the "absolutely clear nothing else is touched" proof.
 *
 * Walks every .ts in src/ and fails if ANY import resolves outside this package.
 * Allowed: Node builtins, declared npm deps, and intra-package relative paths
 * that stay within src/. A single `../../` that climbs out of the package, or an
 * import of an undeclared module, is a hard failure.
 */
const fs = require("fs");
const path = require("path");

const PKG_ROOT = path.resolve(__dirname, "..");
const SRC = path.join(PKG_ROOT, "src");
const pkg = JSON.parse(fs.readFileSync(path.join(PKG_ROOT, "package.json"), "utf8"));
const allowedPkgs = new Set([
  ...Object.keys(pkg.dependencies || {}),
  ...Object.keys(pkg.devDependencies || {}),
]);
const NODE_BUILTINS = new Set([
  "fs", "path", "child_process", "net", "os", "util", "events", "stream",
  "crypto", "http", "https", "url", "assert", "buffer", "process",
]);

function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    return e.isDirectory() ? walk(p) : p.endsWith(".ts") ? [p] : [];
  });
}

const importRe = /(?:import|export)[^'"]*?from\s*['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\)/g;
const violations = [];

for (const file of walk(SRC)) {
  const body = fs.readFileSync(file, "utf8");
  let m;
  while ((m = importRe.exec(body))) {
    const spec = m[1] || m[2];
    if (!spec) continue;
    const rel = path.relative(PKG_ROOT, file);

    if (spec.startsWith(".")) {
      // relative: resolved target must stay inside src/
      const target = path.resolve(path.dirname(file), spec);
      if (!target.startsWith(SRC)) {
        violations.push(`${rel}: relative import escapes package → "${spec}"`);
      }
    } else {
      // bare: must be a Node builtin or a declared dependency
      const top = spec.startsWith("@") ? spec.split("/").slice(0, 2).join("/") : spec.split("/")[0];
      if (!NODE_BUILTINS.has(top) && !allowedPkgs.has(top)) {
        violations.push(`${rel}: undeclared external dependency → "${spec}"`);
      }
    }
  }
}

const fileCount = walk(SRC).length;
if (violations.length) {
  console.error(`\n❌ ISOLATION BROKEN — ${violations.length} import(s) reach outside @vexa/join:\n`);
  for (const v of violations) console.error("   " + v);
  console.error(`\nScanned ${fileCount} files. The join layer must depend on nothing but its own src + declared npm deps.\n`);
  process.exit(1);
}
console.log(`✅ ISOLATION VERIFIED — scanned ${fileCount} files in src/; every import stays inside the package (intra-package, Node builtins, or declared deps: ${[...allowedPkgs].join(", ")}).`);
