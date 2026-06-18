#!/usr/bin/env node
/**
 * The vexa 0.12 gate suite (ARCHITECTURE.md §4). Each gate is GREEN-ON-EMPTY and becomes
 * real as content lands — "an artifact exists only when gate-green" (P9).
 * Usage: node scripts/gates.mjs [readme|isolation|exports|graph|schema|all]
 */
import { readdirSync, existsSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { execSync } from "node:child_process";

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
    if (s.isDirectory()) { acc.push(p); walkDirs(p, acc); }
  }
  return acc;
}
const packageDirs = () => walkDirs().filter((d) => existsSync(join(d, "package.json")));

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

const GATES = { readme: gateReadme, isolation: gateIsolation, exports: gateExports, graph: gateGraph, schema: gateSchema, python: gatePython, node: gateNode };
const which = process.argv[2] || "all";
const run = which === "all" ? Object.keys(GATES) : [which];
if (run.some((g) => !GATES[g])) { console.error(`unknown gate: ${which}`); process.exit(2); }
console.log(`\n▶ gates: ${run.join(", ")}`);
const ok = run.map((g) => GATES[g]()).every(Boolean);
console.log(ok ? "\n✅ gates green\n" : "\n❌ gates failed\n");
process.exit(ok ? 0 : 1);
