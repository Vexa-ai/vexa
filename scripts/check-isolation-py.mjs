#!/usr/bin/env node
/**
 * The Python modular-monolith boundary checker — the Python twin of the TS bricks'
 * scripts/check-isolation.js + .dependency-cruiser.cjs (ARCHITECTURE.md §3/§4, P2/P3).
 *
 * One DRY scan over every Python package's src/**\/*.py drives BOTH Python gates:
 *   --mode=isolation : a sibling-package import is a RED unless the import is the package's
 *                      OWN top-level module, a DECLARED pyproject dependency, or an entry in the
 *                      explicit ALLOWED_EDGES table below. (stdlib / third-party deps are not
 *                      sibling modules → never flagged.) Reports the offending file path.
 *   --mode=graph     : encode the allowed cross-package DAG. The graph of REAL src→src edges must
 *                      be acyclic AND every edge must be a listed ALLOWED_EDGES entry; an unlisted
 *                      edge or a cycle is a RED.
 *
 * GREEN-ON-EMPTY: no Python packages → green (mirrors the other gates).
 *
 * The 7 v0.12 top-level Python packages and their src/ module names:
 *   runtime_kernel · gateway · gateway_conformance · meeting_api ·
 *   admin_api · identity_core · agent_api
 * (P2 folded the standalone transcription_collector INTO meeting_api — that package is gone.)
 *
 * Why a curated allowed-edges table instead of reading pyproject `dependencies`: the only legit
 * cross-package edges (gateway_conformance → gateway, gateway_conformance → meeting_api) are wired
 * via pytest `pythonpath`, NOT declared as pip dependencies — so the table is the authority.
 * runtime_kernel must import NOTHING from the others (kernel depends on nothing above).
 */
import { readdirSync, readFileSync, existsSync, statSync } from "node:fs";
import { join, relative, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SKIP = new Set(["node_modules", "dist", ".turbo", "__pycache__", ".venv"]);
const skippable = (n) => n.startsWith(".") || SKIP.has(n);

// The trees that may hold Python packages (do NOT touch services/ / bbb / prod).
const ROOTS = ["runtime", "gateway", "meetings", "identity", "agent"];

// ── allowed cross-package edges ───────────────────────────────────────────────────────────────
// importer-module → set of sibling modules it MAY import (src→src). Each edge carries a reason so
// the table reads as the seam contract. Everything not listed is forbidden.
const ALLOWED_EDGES = {
  // The gateway behavioural conformance harness (test lane) drives the SHIPPED apps: it imports the
  // production gateway + the production (now UNIFIED) meeting-api to assert against real code,
  // one-way. P2 folded the standalone transcription-collector INTO meeting-api, so the harness now
  // drives meeting_api.create_app (its collector module) one hop downstream of the gateway.
  gateway_conformance: {
    gateway: "test→prod: conformance drives the shipped gateway.create_app / gateway.run_multiplex / gateway.obs",
    meeting_api: "test→prod: conformance drives the shipped, unified meeting_api.create_app (folded-in collector, one hop downstream)",
  },
  // PRE-ALLOWED shared-DB-models edge: meeting-api MAY re-export admin_api.schema.models (the
  // SQLAlchemy source-of-truth). Kept declared so the gate stays green should that edge land, but
  // meeting-api currently does NOT take it — it mirrors the models in meeting_api.sessions.models
  // (the self-contained per-service mirror, like obs.py), so no real meeting_api → admin_api edge
  // exists today (gate:graph reports zero such edge).
  meeting_api: {
    admin_api: "shared SQLAlchemy models — admin_api.schema.models is the DB source-of-truth (pre-allowed; currently mirrored, not imported)",
  },
};

// ── discover packages: pyproject dir → { module, srcDir, deps } ───────────────────────────────
function walkDirs(dir, acc = []) {
  let names;
  try { names = readdirSync(dir); } catch { return acc; }
  for (const n of names) {
    if (skippable(n)) continue;
    const p = join(dir, n);
    let s; try { s = statSync(p); } catch { continue; }
    if (s.isDirectory()) { acc.push(p); walkDirs(p, acc); }
  }
  return acc;
}

// the top-level module of a package = the single dir under src/ holding __init__.py
function moduleOf(pkgDir) {
  const src = join(pkgDir, "src");
  if (!existsSync(src)) return null;
  const mods = readdirSync(src, { withFileTypes: true })
    .filter((e) => e.isDirectory() && existsSync(join(src, e.name, "__init__.py")))
    .map((e) => e.name);
  return mods.length === 1 ? mods[0] : (mods[0] || null);
}

// declared pip dependencies (best-effort parse of [project].dependencies) — used to let a future
// DECLARED sibling dep pass without a table entry (today none are declared, table is authority).
function declaredDeps(pkgDir) {
  const txt = readFileSync(join(pkgDir, "pyproject.toml"), "utf8");
  const m = txt.match(/dependencies\s*=\s*\[([\s\S]*?)\]/);
  if (!m) return new Set();
  const names = [...m[1].matchAll(/["']([^"'<>=!~ ]+)/g)].map((x) => x[1].toLowerCase());
  return new Set(names);
}

function discover() {
  const pkgs = [];
  for (const r of ROOTS) {
    const base = join(ROOT, r);
    if (!existsSync(base)) continue;
    for (const d of [base, ...walkDirs(base)]) {
      if (!existsSync(join(d, "pyproject.toml")) || !existsSync(join(d, "src"))) continue;
      const module = moduleOf(d);
      if (!module) continue;
      pkgs.push({ dir: d, module, srcDir: join(d, "src"), deps: declaredDeps(d) });
    }
  }
  return pkgs;
}

// every *.py under src/ → its imported top-level module names
function* pyFiles(dir) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    if (skippable(e.name)) continue;
    const p = join(dir, e.name);
    if (e.isDirectory()) yield* pyFiles(p);
    else if (e.name.endsWith(".py")) yield p;
  }
}
const IMPORT_RE = /^[ \t]*(?:from[ \t]+([A-Za-z_][\w.]*)[ \t]+import\b|import[ \t]+([A-Za-z_][\w.]*(?:[ \t]*,[ \t]*[A-Za-z_][\w.]*)*))/gm;
function imports(file) {
  const out = [];
  const src = readFileSync(file, "utf8");
  for (const m of src.matchAll(IMPORT_RE)) {
    if (m[1]) out.push(m[1]);                                  // from X[.y] import ...
    else if (m[2]) for (const part of m[2].split(",")) out.push(part.trim());  // import X, Y
  }
  return out.map((s) => s.split(".")[0]).filter(Boolean);      // top-level name only
}

// ── the scan ──────────────────────────────────────────────────────────────────────────────────
const pkgs = discover();
const SIBLINGS = new Set(pkgs.map((p) => p.module));
const mode = (process.argv.find((a) => a.startsWith("--mode=")) || "--mode=isolation").split("=")[1];

if (!pkgs.length) {
  console.log(`✅ PY-${mode.toUpperCase()} — no Python packages yet (green-on-empty)`);
  process.exit(0);
}

const violations = [];   // isolation: forbidden sibling imports
const edges = new Set();  // graph: "importer→imported" real src→src edges
let scanned = 0;

for (const pkg of pkgs) {
  for (const file of pyFiles(pkg.srcDir)) {
    scanned++;
    for (const top of imports(file)) {
      if (top === pkg.module) continue;          // own module (intra-package)
      if (!SIBLINGS.has(top)) continue;          // stdlib / third-party — not a sibling package
      // a sibling import. Allowed if a declared pip dep OR a listed allowed-edge.
      edges.add(`${pkg.module}→${top}`);
      const declared = pkg.deps.has(top) || pkg.deps.has(top.replace(/_/g, "-"));
      const listed = ALLOWED_EDGES[pkg.module]?.[top];
      if (!declared && !listed) {
        violations.push(`${relative(ROOT, file)} : ${pkg.module} → ${top} (forbidden cross-package import)`);
      }
    }
  }
}

if (mode === "isolation") {
  if (violations.length) {
    console.error("❌ PY-ISOLATION VIOLATION:\n  " + violations.join("\n  "));
    process.exit(1);
  }
  console.log(`✅ PY-ISOLATION VERIFIED — scanned ${scanned} src/*.py across ${pkgs.length} package(s); every sibling import is own-module, declared, or an allowed edge.`);
  process.exit(0);
}

if (mode === "graph") {
  const bad = [];
  // 1) every real edge must be a listed allowed-edge (or a declared pip dep).
  for (const e of edges) {
    const [from, to] = e.split("→");
    const fromPkg = pkgs.find((p) => p.module === from);
    const declared = fromPkg?.deps.has(to) || fromPkg?.deps.has(to.replace(/_/g, "-"));
    if (!ALLOWED_EDGES[from]?.[to] && !declared) bad.push(`unlisted edge: ${from} → ${to}`);
  }
  // 2) acyclic (DFS over the real edge set).
  const adj = {};
  for (const e of edges) { const [a, b] = e.split("→"); (adj[a] ||= []).push(b); }
  const WHITE = 0, GREY = 1, BLACK = 2;
  const color = {};
  const stack = [];
  let cycle = null;
  function dfs(n) {
    color[n] = GREY; stack.push(n);
    for (const m of adj[n] || []) {
      if (color[m] === GREY) { cycle = [...stack.slice(stack.indexOf(m)), m].join(" → "); return; }
      if (!color[m] && dfs(m)) return true;
    }
    color[n] = BLACK; stack.pop();
  }
  for (const n of Object.keys(adj)) if (!color[n]) { if (dfs(n)) break; if (cycle) break; }
  if (cycle) bad.push(`cycle: ${cycle}`);
  // 3) runtime_kernel must depend on NOTHING above (no outgoing edges at all).
  if (adj["runtime_kernel"]?.length) bad.push(`runtime_kernel must import nothing above — found: ${adj["runtime_kernel"].join(", ")}`);

  if (bad.length) { console.error("❌ PY-GRAPH VIOLATION:\n  " + bad.join("\n  ")); process.exit(1); }
  const list = [...edges].sort().join(", ") || "(none — all packages self-contained)";
  console.log(`✅ PY-GRAPH VERIFIED — ${edges.size} cross-package edge(s) acyclic + allow-listed: ${list}`);
  process.exit(0);
}

console.error(`unknown --mode: ${mode}`);
process.exit(2);
