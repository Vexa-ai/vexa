#!/usr/bin/env node
// gate:isolation — every import must stay inside the package: intra-package,
// Node builtins, or a DECLARED dep (dependencies OR devDependencies). A leaf:
// pure browser MediaRecorder driver, no @vexa/* deps.
const fs = require('fs'), path = require('path');
const SRC = path.join(__dirname, '..', 'src');
const pkg = require('../package.json');
const deps = new Set([...Object.keys(pkg.dependencies || {}), ...Object.keys(pkg.devDependencies || {})]);
const builtins = new Set(require('module').builtinModules);
let files = 0, violations = [];
(function walk(d){ for (const e of fs.readdirSync(d, {withFileTypes:true})) {
  const p = path.join(d, e.name);
  if (e.isDirectory()) walk(p);
  else if (e.name.endsWith('.ts')) {
    files++;
    const src = fs.readFileSync(p, 'utf8');
    for (const m of src.matchAll(/from\s+['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\)/g)) {
      const spec = m[1] || m[2];
      if (spec.startsWith('.')) continue;                 // intra-package
      const scoped = spec.startsWith('@') ? spec.split('/').slice(0,2).join('/') : spec.split('/')[0];
      if (builtins.has(spec) || builtins.has(scoped)) continue;
      if (deps.has(spec) || deps.has(scoped)) continue;
      violations.push(`${path.relative(SRC,p)} → ${spec}`);
    }
  }
}})(SRC);
if (violations.length) { console.error('❌ ISOLATION VIOLATION:\n  ' + violations.join('\n  ')); process.exit(1); }
console.log(`✅ ISOLATION VERIFIED — scanned ${files} files in src/; every import intra-package, builtin, or declared dep.`);
