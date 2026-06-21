#!/usr/bin/env node
/**
 * The vexa 0.12 gate suite (ARCHITECTURE.md §4). Each gate is GREEN-ON-EMPTY and becomes
 * real as content lands — "an artifact exists only when gate-green" (P9).
 * Usage: node scripts/gates.mjs [readme|isolation|isolation-py|exports|graph|graph-py|schema|
 *                                contract-version|python|stack|node|health|access|tracing|replay|
 *                                telemetry|eval|licenses|compose|all]
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

// recursive: does any non-ignored file under `dir` match `re`? (used by the named eval gates
// to discover harnesses by filename without hard-coding full paths)
function findFile(dir, re) {
  if (!existsSync(dir)) return false;
  for (const name of readdirSync(dir)) {
    if (skippable(name)) continue;
    const p = join(dir, name);
    let s; try { s = statSync(p); } catch { continue; }
    if (s.isDirectory()) { if (findFile(p, re)) return true; }
    else if (re.test(name)) return true;
  }
  return false;
}
// a Python service that stands up a FastAPI app (→ must answer gate:health). A worker carve
// (agent-api: spawned by the runtime, liveness = workload lifecycle) builds no app → exempt.
const hasFastApiApp = (d) => existsSync(join(d, "src")) && findFile(join(d, "src"), /\.py$/) &&
  (() => { try { execSync(`grep -rql "FastAPI(" ${JSON.stringify(join(d, "src"))}`, { stdio: "pipe" }); return true; } catch { return false; } })();
const pyPackages = () => walkDirs().filter((d) => existsSync(join(d, "pyproject.toml")) && existsSync(join(d, "tests")));

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
  const targets = ["core", "integrations", "clients", "sdks", "schemas", "tools"]
    .filter((d) => existsSync(join(ROOT, d)));
  try { execSync(`npx depcruise --config .dependency-cruiser.cjs --no-progress ${targets.join(" ")}`, { stdio: "pipe" }); }
  catch (e) { return fail([`dependency-cruiser:\n${(e.stdout || e.stderr || e).toString()}`]); }
  console.log("  ✓ gate:graph — acyclic + allowed-edges");
  return true;
}

// gate:isolation-py (P2, Python twin) — the Python modular-monolith boundary check. Mirrors the TS
// bricks' check-isolation.js: scans every Python package's src/**\/*.py imports; a sibling-package
// import is allowed ONLY if it is the package's own module, a declared pyproject dependency, or an
// entry in scripts/check-isolation-py.mjs's ALLOWED_EDGES table (the legit test→prod + shared-models
// edges). A forbidden cross-package import → RED, with the file path. Green-on-empty.
function gateIsolationPy() {
  const s = join(ROOT, "scripts", "check-isolation-py.mjs");
  try { execSync(`node ${JSON.stringify(s)} --mode=isolation`, { stdio: "pipe" }); }
  catch (e) { return fail([`python isolation:\n${(e.stdout || e.stderr || e).toString().slice(0, 1200)}`]); }
  console.log("  ✓ gate:isolation-py — every Python sibling import is own-module, declared, or an allowed edge");
  return true;
}

// gate:graph-py (P3, Python twin) — the Python allowed-edges DAG (the .dependency-cruiser.cjs intent
// for Python). Encodes: acyclic; runtime_kernel imports nothing above; every real src→src
// cross-package edge is an allow-listed entry; gateway_conformance → {gateway, meeting_api} only
// (P2 folded the collector into meeting_api). A cycle or an unlisted edge → RED. Shares the one scan
// with isolation-py (DRY). Green-on-empty.
function gateGraphPy() {
  const s = join(ROOT, "scripts", "check-isolation-py.mjs");
  try { execSync(`node ${JSON.stringify(s)} --mode=graph`, { stdio: "pipe" }); }
  catch (e) { return fail([`python graph:\n${(e.stdout || e.stderr || e).toString().slice(0, 1200)}`]); }
  console.log("  ✓ gate:graph-py — Python cross-package edges acyclic + allow-listed");
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

// gate:stack — the Group-1 backing-stack evals (postgres·redis·admin-api). A stack-eval package
// is a Python package (pyproject + tests/) whose tests/ carries a `test_stack_*.py`. Runs them via
// `uv run pytest`. AUTONOMOUS: the evals use testcontainers (ephemeral docker PG+Redis), no live
// stack. Green-on-empty. Where docker is absent the evals self-skip (pytest exit 0) → green-or-skip;
// where docker exists they must PASS. Fails loud with a trimmed message.
function gateStack() {
  const pkgs = walkDirs().filter((d) =>
    existsSync(join(d, "pyproject.toml")) && existsSync(join(d, "tests")) &&
    readdirSync(join(d, "tests")).some((f) => /^test_stack_.*\.py$/.test(f))
  );
  if (!pkgs.length) { console.log("  ✓ gate:stack — no stack-eval packages yet (green-on-empty)"); return true; }
  for (const d of pkgs) {
    try { execSync("uv run pytest -q tests", { cwd: d, stdio: "pipe" }); }
    catch (e) { return fail([`stack-eval ${rel(d)}:\n${(e.stdout || e.stderr || e).toString().slice(-2000)}`]); }
  }
  console.log(`  ✓ gate:stack — ${pkgs.length} backing-stack eval package(s) · testcontainers green-or-skip`);
  return true;
}

// gate:compose (P5) — the autonomous stack-readiness proof: bring up the REAL deploy/compose stack
// and prove it is ready to run the vexa bot. The harness (deploy/compose/tests/stack_test.py, driven
// by bin/stack-test) owns the full up→prove→down(-v) lifecycle; this gate just dispatches it.
// GREEN-OR-SKIP like gate:stack: detect docker (`docker info`); if absent → print a skip line +
// return green. GREEN-ON-EMPTY if the compose file is missing. When docker IS present it runs the
// ALWAYS-ON proof subset (health · auth surface · transcript dataflow · recording→minio · max-bots ·
// continue_meeting · join-retry-wiring) and fails LOUD on any assertion. The real bot-spawn proof
// (steps 3·6a — a live vexaai/vexa-bot:dev container reaching `joining`) is opt-in behind COMPOSE_BOT=1
// (slow/flaky for a routine gate), runnable via `make -C deploy/compose stack-test-bot`.
function gateCompose() {
  const composeFile = join(ROOT, "deploy", "compose", "docker-compose.yml");
  const runner = join(ROOT, "deploy", "compose", "bin", "stack-test");
  if (!existsSync(composeFile)) { console.log("  ✓ gate:compose — no compose stack yet (green-on-empty)"); return true; }
  try { execSync("docker info", { stdio: "pipe" }); }
  catch { console.log("  ✓ gate:compose — docker not available → skip (green-or-skip)"); return true; }
  if (!existsSync(runner)) return fail([`gate:compose — compose stack present but no readiness proof (deploy/compose/bin/stack-test missing)`]);
  try { execSync(`bash ${JSON.stringify(runner)}`, { stdio: "pipe" }); }
  catch (e) { return fail([`compose stack-readiness proof:\n${(e.stdout || e.stderr || e).toString().slice(-3000)}`]); }
  console.log("  ✓ gate:compose — REAL compose stack proven bot-ready (health·auth·transcript·recording·control-plane)");
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

// gate:health (P-ops) — every long-running HTTP service answers a conforming liveness /health.
// Discovers Python service packages that build a FastAPI app; each MUST ship tests/test_health.py
// and it MUST pass (asserting GET /health → 200 {status:"ok", service}). A worker carve with no
// app (agent-api) is correctly out of scope. NOT green-on-empty for a service that has an app but
// no health eval — that's a RED (a standing service with no liveness probe is a gap).
function gateHealth() {
  const svcs = pyPackages().filter(hasFastApiApp);
  if (!svcs.length) { console.log("  ✓ gate:health — no HTTP services yet (green-on-empty)"); return true; }
  const missing = svcs.filter((d) => !existsSync(join(d, "tests", "test_health.py")));
  if (missing.length) return fail(missing.map((d) => `HTTP service exposes no liveness eval: ${rel(d)}/tests/test_health.py missing`));
  for (const d of svcs) {
    try { execSync("uv run pytest -q tests/test_health.py", { cwd: d, stdio: "pipe" }); }
    catch (e) { return fail([`health ${rel(d)}:\n${(e.stdout || e.stderr || e).toString().slice(-1500)}`]); }
  }
  console.log(`  ✓ gate:health — ${svcs.length} HTTP service(s) answer a conforming /health`);
  return true;
}

// gate:access (P20) — the canAccess default-deny is PROVEN: at least one package ships
// tests/test_access.py and it passes (deny on the read paths, owner-allow). RED if absent — an
// unproven access layer is a security gap, not an empty no-op.
function gateAccess() {
  const pkgs = pyPackages().filter((d) => existsSync(join(d, "tests", "test_access.py")));
  if (!pkgs.length) return fail(["gate:access — no tests/test_access.py anywhere (canAccess default-deny is unproven)"]);
  for (const d of pkgs) {
    try { execSync("uv run pytest -q tests/test_access.py", { cwd: d, stdio: "pipe" }); }
    catch (e) { return fail([`access ${rel(d)}:\n${(e.stdout || e.stderr || e).toString().slice(-1500)}`]); }
  }
  console.log(`  ✓ gate:access — ${pkgs.length} access deny-test(s) green (default-deny, P20)`);
  return true;
}

// gate:tracing (O-OBS-1) — a synthetic multi-service request threads ONE trace_id through every
// hop's STRUCTURED log; every line conforms to logevent.v1; a freeform/untraced line fails. The
// logevent.v1 envelope must exist and the test_tracing.py eval must pass. RED if either is absent.
function gateTracing() {
  const hasLogevent = walkDirs().some((d) => /(^|\/)contracts\/logevent\.v\d+$/.test(rel(d).replace(/\\/g, "/")));
  if (!hasLogevent) return fail(["gate:tracing — logevent.v1 contract (the structured-log envelope) is missing"]);
  const pkgs = pyPackages().filter((d) => existsSync(join(d, "tests", "test_tracing.py")));
  if (!pkgs.length) return fail(["gate:tracing — no tests/test_tracing.py (distributed trace is unproven)"]);
  for (const d of pkgs) {
    try { execSync("uv run pytest -q tests/test_tracing.py", { cwd: d, stdio: "pipe" }); }
    catch (e) { return fail([`tracing ${rel(d)}:\n${(e.stdout || e.stderr || e).toString().slice(-1500)}`]); }
  }
  console.log(`  ✓ gate:tracing — trace_id threads every hop; logs conform to logevent.v1`);
  return true;
}

// gate:replay (O-TEL-2) — a stored captured-signal.v1/tape replays through the EXACT pipeline to
// its expected transcript, deterministically (same in ⇒ same out). Discovers any package exposing a
// `replay` script and runs it. RED if none — a replay loop with no proof is a gap. (Runs after
// gate:node so the pipeline dist it imports is freshly built.)
function gateReplay() {
  const pkgs = packageDirs().filter((d) => {
    try { return !!JSON.parse(readFileSync(join(d, "package.json"), "utf8")).scripts?.replay; }
    catch { return false; }
  });
  if (!pkgs.length) return fail(["gate:replay — no package exposes a `replay` harness (deterministic replay is unproven)"]);
  for (const d of pkgs) {
    try { execSync("pnpm run replay", { cwd: d, stdio: "pipe" }); }
    catch (e) { return fail([`replay ${rel(d)}:\n${(e.stdout || e.stderr || e).toString().slice(-2000)}`]); }
  }
  console.log(`  ✓ gate:replay — ${pkgs.length} deterministic replay harness(es) green (same in ⇒ same out)`);
  return true;
}

// gate:telemetry (O-TEL-1/3) — captured-signal.v1 + flagged-issue.v1 exist (their goldens conform
// via gate:schema), and the capture-bridge TelemetrySink tap is proven by src/telemetry.test.ts (a
// fed frame reaches the sink, conforms, round-trips through @vexa/capture-codec). RED if a contract
// or the tap test is absent. (Runs after gate:node for a fresh build.)
function gateTelemetry() {
  const need = ["captured-signal", "flagged-issue"];
  const miss = need.filter((n) => !walkDirs().some((d) => new RegExp(`(^|/)contracts/${n}\\.v\\d+$`).test(rel(d).replace(/\\/g, "/"))));
  if (miss.length) return fail(miss.map((n) => `gate:telemetry — ${n}.v1 contract is missing`));
  const taps = packageDirs().filter((d) => existsSync(join(d, "src", "telemetry.test.ts")));
  if (!taps.length) return fail(["gate:telemetry — no capture-bridge TelemetrySink unit test (src/telemetry.test.ts)"]);
  for (const d of taps) {
    try { execSync("pnpm exec tsx src/telemetry.test.ts", { cwd: d, stdio: "pipe" }); }
    catch (e) { return fail([`telemetry ${rel(d)}:\n${(e.stdout || e.stderr || e).toString().slice(-2000)}`]); }
  }
  console.log(`  ✓ gate:telemetry — captured-signal.v1 + flagged-issue.v1 present; capture tap proven`);
  return true;
}

// gate:eval (P-completeness) — the umbrella enforcer: EVERY essential path (Groups 2–8) ships an
// offline eval harness. This is a PRESENCE/completeness check (a path with no harness is RED); the
// harnesses' PASS/FAIL is enforced by the per-language + per-path runner gates above. Delete any
// path's eval and this gate goes red — "the autonomous eval IS the bar" cannot silently regress.
function gateEval() {
  const PATHS = [
    ["core-stack",        /^test_stack_.*\.py$/,                        ["core/identity/services/admin-api"]],
    ["observability",     /^test_tracing\.py$/,                         ["core/gateway/services/conformance"]],
    ["runtime",           /^test_(store|restart|scheduler|enforcement|health|kernel|profiles).*\.py$/, ["core/runtime"]],
    ["identity-access",   /^test_access\.py$/,                          ["core/identity"]],
    ["meeting-lifecycle", /^test_.*(lifecycle|machine|receiver).*\.py$/, ["core/meetings/services/meeting-api"]],
    ["webhooks",          /^test_.*webhook.*\.py$/,                     ["core/meetings/services/meeting-api"]],
    ["scheduling",        /^test_.*schedul.*\.py$/,                     ["core/meetings/services/meeting-api"]],
    ["api-surface",       /^test_api.*\.py$/,                           ["core/gateway/services/conformance"]],
    ["ws-protocol",       /^test_.*ws.*\.py$/,                          ["core/gateway/services/conformance"]],
    ["agents",            /^test_.*\.py$/,                              ["core/agent/services/agent-api"]],
    ["telemetry-tap",     /^telemetry\.test\.ts$/,                      ["core/meetings/services/bot"]],
    ["replay",            /^replay\.test\.ts$/,                         ["core/meetings/services/bot"]],
    ["bug-flag",          /^flag\.test\.mjs$/,                          ["core/meetings/eval"]],
  ];
  const missing = [];
  for (const [label, re, roots] of PATHS) {
    if (!roots.some((r) => findFile(join(ROOT, r), re))) missing.push(`${label} (no harness matching ${re} under ${roots.join(", ")})`);
  }
  if (missing.length) return fail(missing.map((m) => `essential path without an offline eval harness: ${m}`));
  console.log(`  ✓ gate:eval — all ${PATHS.length} essential paths ship an offline eval harness`);
  return true;
}

const GATES = { readme: gateReadme, isolation: gateIsolation, "isolation-py": gateIsolationPy, exports: gateExports, graph: gateGraph, "graph-py": gateGraphPy, schema: gateSchema, "contract-version": gateContractVersion, python: gatePython, stack: gateStack, node: gateNode, health: gateHealth, access: gateAccess, tracing: gateTracing, replay: gateReplay, telemetry: gateTelemetry, eval: gateEval, licenses: gateLicenses, compose: gateCompose };
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
