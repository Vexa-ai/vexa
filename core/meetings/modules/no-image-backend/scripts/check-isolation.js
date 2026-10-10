#!/usr/bin/env node
// gate:isolation (P2) — the stand-in for sharp imports nothing but Node built-ins: it exists so that
// nothing is loaded, and a dependency here would defeat it.
'use strict';
const { readFileSync, readdirSync } = require('node:fs');
const { join } = require('node:path');
const { builtinModules } = require('node:module');

const SRC = join(__dirname, '..', 'src');
const builtins = new Set(builtinModules);
const violations = [];
let files = 0;
for (const name of readdirSync(SRC)) {
  if (!/\.c?js$/.test(name)) continue;
  files++;
  const src = readFileSync(join(SRC, name), 'utf8');
  for (const m of src.matchAll(/require\(\s*['"]([^'"]+)['"]\s*\)|import\(\s*['"]([^'"]+)['"]\s*\)|from\s+['"]([^'"]+)['"]/g)) {
    const spec = m[1] || m[2] || m[3];
    if (spec.startsWith('.')) continue;
    if (spec.startsWith('node:') || builtins.has(spec)) continue;   // node:test exists only with the prefix
    violations.push(`${name} → ${spec}`);
  }
}
if (violations.length) { console.error('❌ ISOLATION VIOLATION:\n  ' + violations.join('\n  ')); process.exit(1); }
console.log(`✅ ISOLATION VERIFIED — ${files} file(s); Node built-ins only.`);
