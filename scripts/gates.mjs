#!/usr/bin/env node
/**
 * The vexa 0.12 gate suite (ARCHITECTURE.md §4). Each gate is GREEN-ON-EMPTY and becomes
 * real as content lands — "an artifact exists only when gate-green" (P9).
 * Usage: node scripts/gates.mjs [readme|isolation|exports|graph|schema|all]
 */
import { readdirSync, existsSync, readFileSync, writeFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { execSync } from "node:child_process";
import { createHash } from "node:crypto";

const ROOT = process.cwd();
const SKIP = new Set(["node_modules", "dist", ".turbo", "__pycache__"]);
const skippable = (name) => name.startsWith(".") || SKIP.has(name);
const rel = (p) => p.slice(ROOT.length + 1) || ".";
const fail = (msgs) => { for (const m of msgs) console.error("  ✗ " + m); return false; };

function walkDirs(dir = ROOT, acc = []) {
  for (const name of readdirSync(dir)) {
    if (skippable(name)) continue;
    const p = join(dir, name);
    let s; try { s = statSync(p); } catch { continue; }
    if (s.isDirectory()) {
      if (existsSync(join(p, ".gateignore"))) continue;   // vendored subtree — opted out of the per-dir gates (refactor pending)
      acc.push(p); walkDirs(p, acc);
    }
  }
  return acc;
}
const packageDirs = () => walkDirs().filter((d) => existsSync(join(d, "package.json")));

// a published contract is a `<domain>/contracts/X.vN` dir carrying JSON Schema file(s)
const contractVersionDirs = () => walkDirs().filter(
  (d) => /(^|\/)contracts\/[^/]+\.v\d+$/.test(rel(d).replace(/\\/g, "/")) &&
         readdirSync(d).some((f) => f.endsWith(".schema.json"))
);
// the seal hash of a contract = sha256 over its (name-sorted) *.schema.json bytes
function schemaHash(d) {
  const h = createHash("sha256");
  for (const f of readdirSync(d).filter((f) => f.endsWith(".schema.json")).sort())
    h.update(f + "\0").update(readFileSync(join(d, f)));
  return h.digest("hex");
}

// gate:readme (P12) — every non-ignored dir (incl. root) has a non-empty README.md
function gateReadme() {
  const dirs = [ROOT, ...walkDirs()];
  const missing = dirs.filter((d) => {
    const r = join(d, "README.md");
    return !existsSync(r) || readFileSync(r, "utf8").trim().length === 0;
  });
  if (missing.length) return fail(missing.map((d) => `missing/empty README: ${rel(d)}/`));
  console.log(`  ✓ gate:readme — ${dirs.length} dirs each carry a README`);
  return true;
}

// gate:exports (P6) — every LIBRARY package locks its front door with "exports".
// "private": true packages are not published libraries (CLI tools, harnesses, apps) → exempt.
function gateExports() {
  const libs = packageDirs().filter((d) => {
    try { return !JSON.parse(readFileSync(join(d, "package.json"), "utf8")).private; }
    catch { return true; }   // unreadable → still check it (will be flagged below)
  });
  const bad = libs.filter((d) => {
    try { return !JSON.parse(readFileSync(join(d, "package.json"), "utf8")).exports; }
    catch { return true; }
  });
  if (bad.length) return fail(bad.map((d) => `library package without "exports": ${rel(d)}`));
  console.log(`  ✓ gate:exports — ${libs.length} library package(s) lock their front door`);
  return true;
}

// gate:isolation (P2) — run every brick's own check-isolation
function gateIsolation() {
  const found = walkDirs()
    .map((d) => [d, join(d, "scripts", "check-isolation.js")])
    .filter(([, s]) => existsSync(s));
  for (const [d, s] of found) {
    try { execSync(`node ${JSON.stringify(s)}`, { stdio: "pipe" }); }
    catch (e) { return fail([`isolation failed in ${rel(d)}: ${(e.stdout || e.stderr || e).toString().slice(0, 300)}`]); }
  }
  console.log(`  ✓ gate:isolation — ${found.length} brick(s) checked`);
  return true;
}

// gate:graph (P3) — acyclic + allowed-edges via dependency-cruiser, once packages exist
function gateGraph() {
  if (!packageDirs().length) { console.log("  ✓ gate:graph — no packages yet (green-on-empty)"); return true; }
  const targets = ["runtime", "meetings", "agent", "identity", "gateway", "integrations", "clients", "sdks", "schemas", "tools"]
    .filter((d) => existsSync(join(ROOT, d)));
  try { execSync(`npx depcruise --config .dependency-cruiser.cjs --no-progress ${targets.join(" ")}`, { stdio: "pipe" }); }
  catch (e) { return fail([`dependency-cruiser:\n${(e.stdout || e.stderr || e).toString()}`]); }
  console.log("  ✓ gate:graph — acyclic + allowed-edges");
  return true;
}

// gate:schema (P4/P8) — schemas/*.v1 goldens conform on both languages (real in Stage 1)
function gateSchema() {
  const contracts = walkDirs().filter(
    (d) => /(^|\/)contracts\/[^/]+\.v\d+$/.test(rel(d).replace(/\\/g, "/")) && existsSync(join(d, "validate.mjs"))
  );
  if (!contracts.length) { console.log("  ✓ gate:schema — no contracts yet (green-on-empty)"); return true; }
  for (const d of contracts) {
    try { execSync(`node ${JSON.stringify(join(d, "validate.mjs"))} --check`, { stdio: "pipe" }); }
    catch (e) { return fail([`schema ${rel(d)}:\n${(e.stdout || e.stderr || e).toString()}`]); }
  }
  console.log(`  ✓ gate:schema — ${contracts.length} contract(s) conform (goldens ≡ schema)`);
  return true;
}

// gate:contract-version (P4) — a published `.vN` is FROZEN once sealed. `contracts.seal.json` pins
// each sealed contract's schema by hash; this gate fails if a sealed schema changed. The fix routes
// through a human: a BREAKING change adds the next version dir (X.v2, leaving X.v1 intact); a
// BACK-COMPATIBLE change re-seals (`pnpm seal:contracts`) — a one-line seal diff that rides a
// `lane:contract` review. Unsealed contracts (still in development) are reported, not failed.
const SEAL_FILE = join(ROOT, "contracts.seal.json");
function gateContractVersion() {
  const dirs = contractVersionDirs();
  if (!dirs.length) { console.log("  ✓ gate:contract-version — no contracts yet (green-on-empty)"); return true; }
  const seal = existsSync(SEAL_FILE) ? JSON.parse(readFileSync(SEAL_FILE, "utf8")) : {};
  const changed = [], unsealed = [];
  for (const d of dirs) {
    const key = rel(d).replace(/\\/g, "/");
    if (!(key in seal)) { unsealed.push(key); continue; }
    if (seal[key] !== schemaHash(d)) changed.push(key);
  }
  if (changed.length) return fail(changed.map((k) =>
    `sealed contract changed: ${k} — a published .vN is frozen. BREAKING change → add the next version (vN+1); ` +
    `BACK-COMPATIBLE change → re-seal with \`pnpm seal:contracts\` in a lane:contract human-reviewed PR.`));
  const note = unsealed.length ? `; ${unsealed.length} unsealed (in development): ${unsealed.join(", ")}` : "";
  console.log(`  ✓ gate:contract-version — ${dirs.length - unsealed.length} sealed contract(s) frozen${note}`);
  return true;
}

// gate:python — pytest in every Python package (a dir with pyproject.toml + tests/)
function gatePython() {
  const pkgs = walkDirs().filter((d) => existsSync(join(d, "pyproject.toml")) && existsSync(join(d, "tests")));
  if (!pkgs.length) { console.log("  ✓ gate:python — no Python packages yet (green-on-empty)"); return true; }
  for (const d of pkgs) {
    try { execSync("uv run pytest -q", { cwd: d, stdio: "pipe" }); }
    catch (e) { return fail([`pytest ${rel(d)}:\n${(e.stdout || e.stderr || e).toString()}`]); }
  }
  console.log(`  ✓ gate:python — ${pkgs.length} package(s) · pytest green`);
  return true;
}

// gate:node — build + unit-test every workspace TS package via turbo (mirrors gate:python)
function gateNode() {
  const pkgs = packageDirs().filter((d) => {
    try { return !!JSON.parse(readFileSync(join(d, "package.json"), "utf8")).scripts?.build; }
    catch { return false; }
  });
  if (!pkgs.length) { console.log("  ✓ gate:node — no buildable packages yet (green-on-empty)"); return true; }
  try { execSync("npx turbo run build test --output-logs=errors-only", { cwd: ROOT, stdio: "pipe" }); }
  catch (e) { return fail([`turbo build/test:\n${(e.stdout || e.stderr || e).toString().slice(-2000)}`]); }
  console.log(`  ✓ gate:node — ${pkgs.length} package(s) · build + test green`);
  return true;
}

// gate:licenses (P17) — every resolved dep is OSS-licence-clean (FINOS Cat A/B/X). Uses pnpm's
// built-in licence index (no added dependency to vet — itself a P17 win). Cat A (permissive) passes;
// Cat B (LGPL/MPL/EPL) must be listed in license-exceptions.json; Cat X (GPL/AGPL/SSPL/BSL/…) and
// any unclassified licence fail the build. B is checked before X so LGPL never trips the GPL match.
function gateLicenses() {
  let raw;
  try { raw = execSync("pnpm licenses list --json", { cwd: ROOT, stdio: ["ignore", "pipe", "ignore"] }).toString(); }
  catch (e) { raw = (e.stdout || "").toString(); }
  if (!raw.trim()) { console.log("  ✓ gate:licenses — no resolved deps yet (green-on-empty)"); return true; }
  let data; try { data = JSON.parse(raw); } catch { return fail(["`pnpm licenses list --json` returned non-JSON — run `pnpm install` first"]); }
  const A = [/^MIT/, /^Apache-2\.0/i, /^BSD\b/, /^BSD-/, /^ISC/, /^0BSD/, /^Unlicense/, /^CC0-/, /^CC-BY-/, /^Python-2\.0/, /^BlueOak/, /^Zlib/i, /^MIT-0/, /^WTFPL/i, /OR CC0-1\.0/];
  const B = [/LGPL/i, /^MPL/i, /^EPL/i];                                          // weak copyleft — needs a logged exception
  const X = [/(^|[^L])GPL/i, /AGPL/i, /SSPL/i, /\bBSL\b/i, /Business Source/i, /Elastic-/i, /Commons.?Clause/i, /Proprietary/i, /UNLICENSED/];
  const exFile = join(ROOT, "license-exceptions.json");
  const exceptions = existsSync(exFile) ? (JSON.parse(readFileSync(exFile, "utf8")).categoryB || []) : [];
  const excepted = (name) => exceptions.some((e) => name === e.package || name.startsWith(e.package));
  const bad = [], flagged = [];
  for (const [lic, pkgs] of Object.entries(data)) {
    const names = pkgs.map((p) => p.name);
    if (A.some((re) => re.test(lic))) continue;
    if (B.some((re) => re.test(lic))) {
      const unlisted = names.filter((n) => !excepted(n));
      if (unlisted.length) bad.push(`Cat-B ${lic} needs an entry in license-exceptions.json: ${unlisted.join(", ")}`);
      else flagged.push(`${lic} (${names.join(", ")})`);
      continue;
    }
    if (X.some((re) => re.test(lic))) { bad.push(`FORBIDDEN (Cat X) ${lic}: ${names.join(", ")} — replace this dependency`); continue; }
    bad.push(`unclassified licence "${lic}": ${names.join(", ")} — classify it in scripts/gates.mjs or replace the dep`);
  }
  if (bad.length) return fail(bad);
  const total = Object.values(data).reduce((n, p) => n + p.length, 0);
  console.log(`  ✓ gate:licenses — ${total} deps OSS-clean (Cat A${flagged.length ? `; ${flagged.length} Cat-B by exception: ${flagged.join("; ")}` : ""})`);
  return true;
}

const GATES = { readme: gateReadme, isolation: gateIsolation, exports: gateExports, graph: gateGraph, schema: gateSchema, "contract-version": gateContractVersion, python: gatePython, node: gateNode, licenses: gateLicenses };
const which = process.argv[2] || "all";

// `seal` (not a gate) — (re)freeze the current published contracts into contracts.seal.json.
// Run when sealing Stage 1 or when re-sealing a back-compatible change (lane:contract review).
if (which === "seal") {
  const seal = {};
  for (const d of contractVersionDirs().sort()) seal[rel(d).replace(/\\/g, "/")] = schemaHash(d);
  writeFileSync(SEAL_FILE, JSON.stringify(seal, null, 2) + "\n");
  console.log(`sealed ${Object.keys(seal).length} contract(s) → ${rel(SEAL_FILE)}`);
  process.exit(0);
}
const run = which === "all" ? Object.keys(GATES) : [which];
if (run.some((g) => !GATES[g])) { console.error(`unknown gate: ${which}`); process.exit(2); }
console.log(`\n▶ gates: ${run.join(", ")}`);
const ok = run.map((g) => GATES[g]()).every(Boolean);
console.log(ok ? "\n✅ gates green\n" : "\n❌ gates failed\n");
process.exit(ok ? 0 : 1);
