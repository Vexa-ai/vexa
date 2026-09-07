#!/usr/bin/env node
/** core/flows isolation (P2): the ENGINE (src/flows) imports stdlib only at module scope —
 *  no domains, no third-party, no steps/defs. Steps/defs may import the engine, never a domain
 *  package directly (HTTP only). Mirrors the terminal's check-isolation contract. */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";
const __dirname = dirname(fileURLToPath(import.meta.url));

const ROOT = join(__dirname, "..");
const STDLIB = new Set(["__future__","dataclasses","typing","json","time","uuid","sqlite3",
  "threading","pathlib","os","sys","random","re","math","functools","itertools","collections",
  "contextlib","abc","enum","logging","urllib","http","hashlib"]);

function pyFiles(dir, acc = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) pyFiles(p, acc);
    else if (name.endsWith(".py")) acc.push(p);
  }
  return acc;
}
function topImports(src) {
  const out = [];
  for (const line of src.split("\n")) {
    if (/^\s/.test(line)) continue;                       // lazy (function-body) imports are allowed
    let m = /^import\s+([\w.]+)/.exec(line) || /^from\s+([\w.]+)\s+import/.exec(line);
    if (m) out.push(m[1].split(".")[0]);
  }
  return out;
}
const bad = [];
for (const f of pyFiles(join(ROOT, "src", "flows"))) {
  for (const mod of topImports(readFileSync(f, "utf8"))) {
    if (mod === "flows" || mod === "" || STDLIB.has(mod)) continue;
    bad.push(`${f.slice(ROOT.length + 1)} → ${mod}`);
  }
}
for (const pkg of ["flows_steps", "flows_defs"]) {
  for (const f of pyFiles(join(ROOT, "src", pkg))) {
    for (const mod of topImports(readFileSync(f, "utf8"))) {
      if (["meeting_api","agent","runtime_kernel","control_plane"].includes(mod))
        bad.push(`${f.slice(ROOT.length + 1)} → ${mod} (domains are HTTP-only from steps)`);
    }
  }
}
// The NO-SLEEP LAW (live-witness lesson: one sleeping step froze the whole runner): step and
// flow code may never sleep or busy-poll — every wait is a Wait. Enforced statically here.
for (const pkg of ["flows_steps", "flows_defs"]) {
  for (const f of pyFiles(join(ROOT, "src", pkg))) {
    const src = readFileSync(f, "utf8");
    if (/\btime\.sleep\s*\(/.test(src))
      bad.push(`${f.slice(ROOT.length + 1)} → time.sleep (steps never sleep; return Wait)`);
  }
}
if (bad.length) { console.error("❌ FLOWS ISOLATION VIOLATION:\n  " + bad.join("\n  ")); process.exit(1); }
console.log("✅ flows isolation verified — engine is stdlib-pure at import; steps reach domains by HTTP only");
