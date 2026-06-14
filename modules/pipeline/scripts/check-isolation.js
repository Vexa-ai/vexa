#!/usr/bin/env node
// gate:isolation — every import must stay inside the package (contract re-exports,
// intra-package, Node builtins, or declared deps). The browser modules are
// self-contained by construction; this proves no back-import into the bot crept in.
const fs = require('fs'), path = require('path');
const SRC = path.join(__dirname, '..', 'src');
const pkg = require('../package.json');
const deps = new Set([...Object.keys(pkg.devDependencies||{}), ...Object.keys(pkg.dependencies||{})]);
const builtins = new Set(require('module').builtinModules);
let files = 0, violations = [];
(function walk(d){ for (const e of fs.readdirSync(d, {withFileTypes:true})) {
  const p = path.join(d, e.name);
  if (e.isDirectory()) walk(p);
  else if (e.name.endsWith('.ts')) {
    files++;
    // Strip comments first — a prose `from "…"` in a comment is not an import.
    const src = fs.readFileSync(p, 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/(^|[^:])\/\/.*$/gm, '$1');
    for (const m of src.matchAll(/from\s+['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\)/g)) {
      const spec = m[1] || m[2];
      if (spec.startsWith('.')) continue;                 // intra-package
      const root = spec.split('/')[0].replace(/^@[^/]+\//,'');
      if (builtins.has(spec) || builtins.has(root)) continue;
      if (deps.has(spec) || deps.has('@'+spec)) continue;
      violations.push(`${path.relative(SRC,p)} → ${spec}`);
    }
  }
}})(SRC);
if (violations.length) { console.error('❌ ISOLATION VIOLATION:\n  ' + violations.join('\n  ')); process.exit(1); }
console.log(`✅ ISOLATION VERIFIED — scanned ${files} files in src/; every import intra-package, builtin, or declared dep.`);
