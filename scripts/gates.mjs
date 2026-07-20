#!/usr/bin/env node
/**
 * The vexa 0.12 gate suite (ARCHITECTURE.md §4). Each gate is GREEN-ON-EMPTY and becomes
 * real as content lands — "an artifact exists only when gate-green" (P9).
 * Usage: node scripts/gates.mjs [readme|isolation|isolation-py|exports|graph|graph-py|schema|
 *                                contract-version|config-contract|python|stack|node|health|access|
 *                                tracing|replay|telemetry|eval|licenses|compose|execution-env|
 *                                lite-makefile|all]
 */
import { readdirSync, existsSync, readFileSync, writeFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { execSync } from "node:child_process";
import { createHash } from "node:crypto";

const ROOT = process.cwd();
const SKIP = new Set(["node_modules", "dist", ".turbo", "__pycache__", "test-results", "playwright-report", "coverage"]);
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

// gate:docs-version (D6c) — the docs DECLARE which release they reflect, and it must equal the
// released control-plane. The `docs-reflects:` marker in docs/docs/changelog.mdx is asserted equal
// to Chart.yaml appVersion, so a release version-bump that forgets to advance the docs stamp reds
// CI — the docs cannot silently lag the release.
function gateDocsVersion() {
  const chart = join(ROOT, "deploy", "helm", "charts", "vexa", "Chart.yaml");
  const changelog = join(ROOT, "docs", "docs", "changelog.mdx");
  if (!existsSync(chart)) return fail(["gate:docs-version — Chart.yaml not found"]);
  if (!existsSync(changelog)) return fail(["gate:docs-version — docs/docs/changelog.mdx not found"]);
  const appV = (readFileSync(chart, "utf8").match(/^appVersion:\s*"?([^"\s]+)"?/m) || [])[1];
  const docsV = (readFileSync(changelog, "utf8").match(/docs-reflects:\s*([0-9A-Za-z.\-]+)/) || [])[1];
  if (!appV) return fail(["gate:docs-version — could not read appVersion from Chart.yaml"]);
  if (!docsV) return fail(["gate:docs-version — no `docs-reflects: <version>` marker in docs/docs/changelog.mdx"]);
  if (docsV !== appV) return fail([
    `gate:docs-version — docs reflect ${docsV} but the released appVersion is ${appV}.`,
    "   Update the `docs-reflects:` marker (+ the visible line) in docs/docs/changelog.mdx to match,",
    "   as part of the release version-bump — the docs must not lag the release.",
  ]);
  console.log(`  ✓ gate:docs-version — docs reflect v${docsV}, matching Chart.yaml appVersion`);
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

// gate:test-isolation (P2/P9) — the TEST lane obeys the SAME module boundary as prod: a test must not
// reach around a contract into a sibling package's internals. TS test files (`*.test.ts`) already live
// under each brick's src/ and so are covered by gate:isolation; this closes the Python gap by scanning
// every Python package's tests/ with the same allowed-edges rule (check-isolation-py.mjs --mode=test-isolation).
// Green-on-empty.
function gateTestIsolation() {
  const s = join(ROOT, "scripts", "check-isolation-py.mjs");
  try { execSync(`node ${JSON.stringify(s)} --mode=test-isolation`, { stdio: "pipe" }); }
  catch (e) { return fail([`python test-isolation:\n${(e.stdout || e.stderr || e).toString().slice(0, 1200)}`]); }
  console.log("  ✓ gate:test-isolation — no Python test imports a sibling module's internals (test lane gated, P2)");
  return true;
}

// gate:arch-report (P9) — the architecture-compliance map is GREEN: every modularity principle
// (P2·P3·P4·P6·P12) resolves to a passing gate. scripts/arch-report.mjs --check re-runs each and fails
// loud if any is red — so "fully modular" is a claim backed by mechanical evidence, and docs/docs/governance/arch-compliance.mdx
// is regenerable + current. Green-on-empty before the report generator lands.
function gateArchReport() {
  const s = join(ROOT, "scripts", "arch-report.mjs");
  if (!existsSync(s)) { console.log("  ✓ gate:arch-report — no report generator yet (green-on-empty)"); return true; }
  try { execSync(`node ${JSON.stringify(s)} --check`, { stdio: "pipe" }); }
  catch (e) { return fail([`arch-report:\n${(e.stdout || e.stderr || e).toString().slice(0, 900)}`]); }
  console.log("  ✓ gate:arch-report — every modularity principle maps to a green gate (P9)");
  return true;
}

// gate:parity (P4/P9) — the "on-par-with-main" claim is COMPLETE: every capability + api.v1 endpoint row
// in docs/PARITY-MAIN.md maps to a green proof (✅) — no row left unmapped (empty/TODO/gap/—) or red. Only
// the on-par sections (§1 capability matrix, §2 endpoints) are enforced; §3 enhancements may list ⏳ planned
// gates (A:V2/A:V3/Lane B). So "0.12 ≡ main" is a checked claim, not prose. Green-on-empty before the matrix lands.
function gateParity() {
  const f = join(ROOT, "docs", "PARITY-MAIN.md");
  if (!existsSync(f)) { console.log("  ✓ gate:parity — no parity matrix yet (green-on-empty)"); return true; }
  const text = readFileSync(f, "utf8");
  const enforced = text.split(/^## /m).filter((s) => /^[12] ·/.test(s.trim())).join("\n");
  const rows = enforced.split("\n").filter((l) =>
    l.trim().startsWith("|") && !/^\s*\|\s*-+/.test(l) && !/\|\s*Proof\s*\|/.test(l));
  const bad = [];
  for (const r of rows) {
    if (!r.includes("✅") || /\bTODO\b|\bgap\b|\bnone\b|⏳/i.test(r)) bad.push(r.trim().slice(0, 90));
  }
  if (!rows.length) return fail(["gate:parity — PARITY-MAIN.md has no capability/endpoint rows to check"]);
  if (bad.length) return fail(["gate:parity — unmapped/incomplete on-par row(s):", ...bad.map((b) => "    " + b)]);
  console.log(`  ✓ gate:parity — ${rows.length} capability/endpoint row(s) each map to a green proof (api.v1 ≡ main 1.5.0)`);
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
const ARCH_FILE = join(ROOT, "architecture.calm.json");
const ARCH_SEAL = join(ROOT, "architecture.seal.json");
const SCHEMA_SEAL = join(ROOT, "schema.seal.json");  // #db-seal — frozen DB schema (tables+columns)
// canonical hash of the chart (parsed → re-stringified, so formatting/whitespace doesn't churn the seal)
const archHash = () => createHash("sha256").update(JSON.stringify(JSON.parse(readFileSync(ARCH_FILE, "utf8")))).digest("hex");
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
  try { execSync(`bash ${JSON.stringify(runner)}`, { stdio: "pipe", env: { ...process.env, COMPOSE_DYNAMIC_PORTS: process.env.COMPOSE_DYNAMIC_PORTS || "1" } }); }
  catch (e) { return fail([`compose stack-readiness proof:\n${(e.stdout || e.stderr || e).toString().slice(-3000)}`]); }
  console.log("  ✓ gate:compose — REAL compose stack proven bot-ready (health·auth·transcript·recording·control-plane)");
  return true;
}

// gate:compose-stress (A:V2) — the control plane under CONCURRENT load (N mock bots at once): enforcement
// holds (max-bots never overspills), every FSM reaches terminal under contention. OPT-IN + green-or-skip:
// runs ONLY when COMPOSE_STRESS=1 (heavy → not in the routine `all`; `all` skips it green). Set
// MOCK_BOT=1 + BROWSER_IMAGE=mock-bot:dev too; delegates to the same real-stack runner (stress_test.py
// runs as part of the session). On a shared host (bbb) pass COMPOSE_PROJECT + MINIO_HOST_PORT to isolate.
function gateComposeStress() {
  if (process.env.COMPOSE_STRESS !== "1") {
    console.log("  ✓ gate:compose-stress — opt-in (COMPOSE_STRESS=1 + MOCK_BOT=1 + BROWSER_IMAGE=mock-bot:dev) → skip");
    return true;
  }
  return gateCompose();   // COMPOSE_STRESS=1 makes deploy/compose/tests/stress_test.py run on the live stack
}

// gate:compose-chaos (A:V3) — the control plane RECOVERS from injected dependency faults (a redis/meeting-api
// blip via docker pause/unpause): the FSM still reaches a clean terminal (retry/backoff), never a silent
// stall (P18). OPT-IN + green-or-skip: runs ONLY when COMPOSE_CHAOS=1 (heavy; not in the routine `all`).
// Set MOCK_BOT=1 + BROWSER_IMAGE=mock-bot:dev; delegates to the same real-stack runner (chaos_test.py).
function gateComposeChaos() {
  if (process.env.COMPOSE_CHAOS !== "1") {
    console.log("  ✓ gate:compose-chaos — opt-in (COMPOSE_CHAOS=1 + MOCK_BOT=1 + BROWSER_IMAGE=mock-bot:dev) → skip");
    return true;
  }
  return gateCompose();   // COMPOSE_CHAOS=1 makes deploy/compose/tests/chaos_test.py run on the live stack
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

// gate:eval-baseline (P19, Lane B / B:V2) — the WORKER-L4 eval is a standing, REUSABLE instrument: the
// bot-eval verdict oracle self-test (core/meetings/services/bot/eval/verify.sh — clean→PASS, misattr→FAIL→brick)
// passes OFFLINE, and the recorded ground truth (core/meetings/eval/BASELINE.md) exists. This gates that the
// instrument is ready + calibrated; the live L4 SCORING (the bot on a real meeting ≥ BASELINE) is the
// human-gated run (B:V1), reproduced offline on red via gate:replay. Green-on-empty before the harness lands.
function gateEvalBaseline() {
  const verify = join(ROOT, "core", "meetings", "services", "bot", "eval", "verify.sh");
  const baseline = join(ROOT, "core", "meetings", "eval", "BASELINE.md");
  if (!existsSync(verify)) { console.log("  ✓ gate:eval-baseline — no worker-eval harness yet (green-on-empty)"); return true; }
  if (!existsSync(baseline)) return fail(["gate:eval-baseline — core/meetings/eval/BASELINE.md (recorded L4 ground truth) missing"]);
  try { execSync(`bash ${JSON.stringify(verify)}`, { cwd: ROOT, stdio: "pipe" }); }
  catch (e) { return fail([`eval-baseline oracle self-test:\n${(e.stdout || e.stderr || e).toString().slice(-1500)}`]); }
  console.log("  ✓ gate:eval-baseline — worker-L4 eval oracle self-test passes + BASELINE.md recorded (reusable instrument; live score is B:V1)");
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
  const A = [/^MIT/, /^Apache-2\.0/i, /^BSD\b/, /^BSD-/, /^ISC/, /^0BSD/, /^Unlicense/, /^CC0-/, /^CC-BY-/, /^Python-2\.0/, /^BlueOak/, /^Zlib/i, /^MIT-0/, /^WTFPL/i, /^SIL OPEN FONT LICENSE/i, /OR CC0-1\.0/];
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

// gate:contract-conformance (P8) — the SHIPPED impl conforms to the sealed api.v1, BOTH directions.
// gate:schema proves goldens≡schema and gate:contract-version freezes the seal, but neither proves the
// RUNNING service implements the contract it serves — and it drifted BOTH ways (api.v1 declares routes
// meeting-api never implemented; the conformance harness drove a FAKE that masked it). This is the
// OFFLINE STRUCTURAL check, discovered by filename (mirrors gate:access), asserting per service:
//   • forward (impl ⊆ contract): every api.v1 route the real create_app() registers matches a declared
//     (path, method) — a route that drifts from the contract's spelling is a bug; and
//   • REVERSE (contract ⊆ impl, #591): for EVERY (path, method) the sealed api.v1 declares, the UNION of
//     the gateway edge + meeting-api it forwards to registers it, OR the route is audited in
//     core/gateway/contracts/api.v1/KNOWN_GAPS.json (owned-elsewhere prefix / reasoned known-gap, each
//     reported LOUDLY). A sealed route that is renamed/dropped and NOT audited → RED, listed by name.
//   • golden RESPONSE-SHAPE: the frozen golden examples drive the REAL response so a field RENAME
//     (running_bots → running) fails, not just a path removal.
// The KNOWN_GAPS.json ledger is the audited exception path: adding a row is a deliberate, diff-visible
// change in the sealed contracts dir (it is NOT a *.schema.json, so it does not move the api.v1 seal hash).
// RED if the proof is absent — an ungated contract is the gap this closes.
// L4 extension (bbb): live input-fuzzing (schemathesis vs the running OpenAPI) is the dynamic half.
function gateContractConformance() {
  const pkgs = pyPackages().filter((d) => existsSync(join(d, "tests", "test_contract_conformance.py")));
  if (!pkgs.length) return fail(["gate:contract-conformance — no tests/test_contract_conformance.py (api.v1↔impl conformance is unproven)"]);
  for (const d of pkgs) {
    try { execSync("uv run pytest -q tests/test_contract_conformance.py", { cwd: d, stdio: "pipe" }); }
    catch (e) { return fail([`contract-conformance ${rel(d)}:\n${(e.stdout || e.stderr || e).toString().slice(-1500)}`]); }
  }
  console.log(`  ✓ gate:contract-conformance — ${pkgs.length} service(s) conform to the sealed api.v1 (impl⊆contract + contract⊆impl + golden shapes; gaps audited in KNOWN_GAPS.json)`);
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
    ["agents",            /^test_.*\.py$/,                              ["core/agent/tests"]],
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

// gate:execution-env (ADR-0020) — the execution-target registry conforms. Planning resolves every stage's
// `Runs on:`/`Resources:` against this registry IN ADVANCE (the AGENTS.md rule); this gate is the mechanical
// half: the committed template (deploy/execution-targets.example.json) MUST exist + conform to
// execution-targets.v1, and the gitignored real file (deploy/execution-targets.json) is validated when present
// (CI sees only the template). Secrets are references only — enforced by the schema's secret_ref pattern (P14).
// GREEN-ON-EMPTY before the contract lands.
function gateExecutionEnv() {
  const v = join(ROOT, "deploy", "contracts", "execution-targets.v1", "validate.mjs");
  if (!existsSync(v)) { console.log("  ✓ gate:execution-env — no registry contract yet (green-on-empty)"); return true; }
  const example = join(ROOT, "deploy", "execution-targets.example.json");
  if (!existsSync(example)) return fail(["gate:execution-env — committed template deploy/execution-targets.example.json missing"]);
  const real = join(ROOT, "deploy", "execution-targets.json");
  const files = [example, ...(existsSync(real) ? [real] : [])];
  try { execSync(`node ${JSON.stringify(v)} ${files.map((f) => `--file ${JSON.stringify(f)}`).join(" ")}`, { stdio: "pipe" }); }
  catch (e) { return fail([`execution-env registry:\n${(e.stdout || e.stderr || e).toString().slice(-1500)}`]); }
  console.log(`  ✓ gate:execution-env — ${files.length} registry file(s) conform to execution-targets.v1${existsSync(real) ? "" : " (template only — real registry gitignored/absent)"}`);
  return true;
}

// gate:dataflow (P23) — data-flow ownership, the dimension the rest of the suite did not model. The
// architecture.calm.json registry (FINOS CALM) declares each data carrier's allowed writer-set and
// per-node controls. This gate: (a) checks the model is internally consistent; (b) enforces
// `render-only` controls — a reader must NOT re-derive a producer's data (e.g. no transcript clustering
// in a client); (c) best-effort diffs declared ownership against real code (who actually xadds/publishes
// each carrier). Cross-language, var-indirected writes are not always statically attributable, so (c)
// reports detected writers and hard-fails only on a clearly-attributable undeclared writer.
// GREEN-ON-EMPTY before architecture.calm.json lands.
function gateDataflow() {
  const file = join(ROOT, "architecture.calm.json");
  if (!existsSync(file)) { console.log("  ✓ gate:dataflow — no architecture.calm.json yet (green-on-empty)"); return true; }
  let model; try { model = JSON.parse(readFileSync(file, "utf8")); }
  catch (e) { return fail([`dataflow: architecture.calm.json is not valid JSON — ${e.message}`]); }

  const nodes = model.nodes || [], rels = model.relationships || [], flows = model.flows || [];
  const byId = new Map(nodes.map((n) => [n["unique-id"], n]));
  const relIds = new Set(rels.map((r) => r["unique-id"]));
  const errs = [];

  // (a0) seal — the chart is the asserted-true baseline; any change must be re-sealed (a deliberate,
  // reviewed act). Mirrors contracts.seal.json: `pnpm seal:arch` stamps the canonical hash; drift fails
  // here until re-sealed, so a silent edit to ownership/edges/flows can't slip through review.
  if (existsSync(ARCH_SEAL)) {
    const sealed = (JSON.parse(readFileSync(ARCH_SEAL, "utf8")) || {})["architecture.calm.json"];
    if (sealed && sealed !== archHash()) errs.push("seal: architecture.calm.json changed since last seal — review the diff, then run `pnpm seal:arch` (the chart is the asserted-true baseline; drift from it is deliberate-only)");
  }

  // (a) consistency: every relationship + flow transition references a declared node/relationship
  for (const r of rels) {
    const t = r["relationship-type"] || {}, refs = [];
    if (t.connects) refs.push(t.connects.source?.node, t.connects.destination?.node);
    for (const k of ["composed-of", "deployed-in"]) if (t[k]) { refs.push(t[k].container, ...(t[k].nodes || [])); }
    if (t.interacts) refs.push(...(t.interacts.nodes || []));
    for (const id of refs.filter(Boolean)) if (!byId.has(id)) errs.push(`relationship ${r["unique-id"]} -> unknown node '${id}'`);
  }
  for (const f of flows) for (const tr of (f.transitions || []))
    if (!relIds.has(tr["relationship-unique-id"])) errs.push(`flow ${f["unique-id"]} -> unknown relationship '${tr["relationship-unique-id"]}'`);

  // (a2) completeness — the model covers EVERY real service/module/contract/client (no drift), and no
  // node points at a path that no longer exists (no phantom). This is the anti-drift guard: add a module
  // without registering it here and CI goes red.
  const lsdirs = (p) => existsSync(join(ROOT, p)) ? readdirSync(join(ROOT, p)).filter((n) => { try { return statSync(join(ROOT, p, n)).isDirectory(); } catch { return false; } }) : [];
  const required = new Set();
  for (const dom of lsdirs("core")) {
    for (const s of lsdirs(`core/${dom}/services`)) required.add(`core/${dom}/services/${s}`);
    for (const m of lsdirs(`core/${dom}/modules`)) required.add(`core/${dom}/modules/${m}`);
    for (const c of lsdirs(`core/${dom}/contracts`)) if (/\.v\d+$/.test(c)) required.add(`core/${dom}/contracts/${c}`);
  }
  for (const c of lsdirs("deploy/contracts")) if (/\.v\d+$/.test(c)) required.add(`deploy/contracts/${c}`);
  // a client that is composed-of (a "mapped" client, e.g. terminal) must register every src/* concern
  // module too, so its internal modularity can't drift either; unmapped clients stay opaque webclients.
  const containers = new Set(rels.filter((r) => r["relationship-type"]?.["composed-of"]).map((r) => r["relationship-type"]["composed-of"].container));
  for (const cl of lsdirs("clients")) {
    required.add(`clients/${cl}`);
    const clNode = nodes.find((n) => n["node-type"] === "webclient" && (n.metadata || []).some((mm) => mm.path === `clients/${cl}`));
    if (clNode && containers.has(clNode["unique-id"]))
      for (const d of lsdirs(`clients/${cl}/src`)) required.add(`clients/${cl}/src/${d}`);
  }
  const modelPaths = new Set(nodes.flatMap((n) => (n.metadata || []).map((m) => m.path).filter(Boolean)));
  for (const r of [...required].sort()) if (!modelPaths.has(r)) errs.push(`completeness: '${r}' exists on disk but is not registered in architecture.calm.json`);
  for (const n of nodes) for (const m of (n.metadata || [])) if (m.path && !existsSync(join(ROOT, m.path))) errs.push(`completeness: node '${n["unique-id"]}' points at missing path '${m.path}'`);

  // path -> owning node (longest-prefix wins)
  const paths = nodes.filter((n) => (n.metadata || []).some((m) => m.path))
    .map((n) => ({ id: n["unique-id"], path: n.metadata.find((m) => m.path).path }))
    .sort((a, b) => b.path.length - a.path.length);
  const ownerOf = (f) => (paths.find((p) => f.startsWith(p.path)) || {}).id;
  const grepFiles = (re) => {
    try {
      return execSync(`grep -rlE ${JSON.stringify(re)} --include=*.py --include=*.ts --include=*.tsx core clients 2>/dev/null | grep -vE 'node_modules|/dist/|\\.test\\.|/tests/|/eval/' || true`,
        { cwd: ROOT, encoding: "utf8" }).split("\n").map((s) => s.trim()).filter(Boolean);
    } catch { return []; }
  };

  // (b) render-only: a forbidden symbol must not be defined/used inside the node's own source.
  // Enforced with NO waiver — any render-only violation (reader re-deriving a producer's data) turns RED.
  for (const n of nodes) {
    const rc = (n.controls || {})["render-only"]; if (!rc) continue;
    const np = (n.metadata || []).find((m) => m.path)?.path;
    for (const req of (rc.requirements || [])) for (const sym of (req.config?.["forbidden-symbols"] || [])) {
      const hits = grepFiles(sym).filter((f) => !np || f.startsWith(np));
      if (!hits.length) continue;
      errs.push(`render-only: '${n["unique-id"]}' re-derives producer data — '${sym}' found in ${hits.map(rel).join(", ")}`);
    }
  }

  // (c) single-writer reality diff — best-effort, line-level (carrier name + write op on ONE line).
  // Var-indirected writes (e.g. `xadd(out_topic, …)`) and helper-built keys aren't statically
  // attributable, so this is REPORT-ONLY: it prints the detected ownership map and a soft note on any
  // literal undeclared writer, but never hard-fails (precise cross-language attribution is out of scope
  // for a static gate; (b) render-only is the enforcing check for reader re-derivation).
  const opRe = { xadd: "x[aA]dd", publish: "publish", "db-write": "session\\.add|INSERT INTO|\\.insert\\(" };
  const grepLines = (re) => {
    try { return execSync(`grep -rnE ${JSON.stringify(re)} --include=*.py --include=*.ts --include=*.tsx core clients 2>/dev/null | grep -vE 'node_modules|/dist/|\\.test\\.|/tests/|/eval/' || true`,
      { cwd: ROOT, encoding: "utf8" }).split("\n").filter(Boolean); } catch { return []; }
  };
  const shared = [], report = [], undeclared = [];
  for (const n of nodes) {
    const own = (n.controls || {}).ownership; if (!own) continue;
    for (const req of (own.requirements || [])) {
      const { writers = [], match, op } = req.config || {}; if (!match) continue;
      if (writers.length > 1) shared.push(`${n["unique-id"]}[${writers.join("+")}]`);
      const mre = new RegExp(match);
      const actual = new Set(grepLines(opRe[op] || op)
        .filter((l) => mre.test(l.replace(/^[^:]*:\d+:/, "")))
        .map((l) => ownerOf(l.split(":")[0])).filter(Boolean));
      if (actual.size) report.push(`${n["unique-id"]}<-{${[...actual].join(",")}}`);
      for (const w of actual) if (!writers.includes(w)) undeclared.push(`${n["unique-id"]}<-${w} (declared [${writers.join(",")}])`);
    }
  }

  // (d) the concise DSL projection (docs/views/architecture.dsl) must reflect the chart — it is GENERATED
  //     (scripts/arch-dsl.mjs), never hand-edited, and is the always-in-context LLM view. A CALM edit that
  //     re-seals MUST regenerate it (`pnpm arch:dsl --write`) or this turns RED, so the projection never lies.
  try { execSync("node scripts/arch-dsl.mjs --check", { cwd: ROOT, stdio: "pipe" }); }
  catch (e) { errs.push(`dsl: docs/views/architecture.dsl is stale — run \`pnpm arch:dsl --write\` (generated from architecture.calm.json, never hand-edited)`); }

  if (errs.length) return fail(["dataflow (P23) — data-flow ownership violations:", ...errs.map((e) => "   " + e)]);
  const carriers = nodes.filter((n) => (n.controls || {}).ownership).length;
  console.log(`  ✓ gate:dataflow — ${nodes.length} nodes · ${rels.length} edges · ${carriers} carriers · complete + sealed (P23)`);
  if (report.length) console.log(`     detected writers: ${report.join(" ")}`);
  if (shared.length) console.log(`     shared-writer (review, P23 prefers one): ${shared.join(" ")}`);
  if (undeclared.length) console.log(`     note (advisory, attribution approximate): ${undeclared.join("; ")}`);
  return true;
}

// gate:config-contract (ADR-0026) — the config.v1 deployment-config contract holds for every ADOPTED
// service (the three planes: meeting-api · runtime · agent-api). Five checks per service:
//   1. its config.v1.json declaration conforms to the sealed schema (the contract's validate.mjs);
//   2. its vendored config_preflight.py is byte-identical to the canonical contract copy;
//   3. declaration → surfaces: every declared key appears in each deploy surface its `targets` names
//      (compose: the service's `environment:` block, or .env.example when the service is env_file-fed;
//      helm: `- name: KEY` in the service's deployment template; lite: the supervisord program's
//      environment= line or an entrypoint.sh export);
//   4. surfaces → declaration: every key a surface sets on the service is declared (or carried in the
//      declaration's `surface_only` list with a reason) — only process-plumbing vars are allowlisted;
//   5. undeclared-read scan: every literal os.getenv/os.environ read in the service's source names a
//      declared key (so a new env read MUST land in the declaration — the SSOT — to pass CI).
const CONFIG_CONTRACT_DIR = join(ROOT, "deploy", "contracts", "config.v1");
// process-plumbing vars a surface may set without a declaration entry (kept TIGHT + documented):
// interpreter/runtime wiring only, never product config.
const CONFIG_SURFACE_ALLOW = new Set(["PYTHONUNBUFFERED", "PYTHONPATH", "DISPLAY", "NODE_ENV", "HOSTNAME", "TZ", "PGTZ"]);
// literal env-read spellings the scanner recognizes (Python; `_os` aliases included by substring match)
const CONFIG_READ_RES = [
  /os\.getenv\(\s*["']([A-Z][A-Z0-9_]*)["']/g,
  /os\.environ\.get\(\s*["']([A-Z][A-Z0-9_]*)["']/g,
  /os\.environ\[\s*["']([A-Z][A-Z0-9_]*)["']\s*\]/g,
  /os\.environ\.setdefault\(\s*["']([A-Z][A-Z0-9_]*)["']/g,
];
const CONFIG_ADOPTED = [
  {
    service: "meeting-api",
    decl: "core/meetings/services/meeting-api/src/meeting_api/config.v1.json",
    preflight: "core/meetings/services/meeting-api/src/meeting_api/config_preflight.py",
    scan: ["core/meetings/services/meeting-api/src"],
    compose: "meeting-api", helm: ["deployment-meeting-api.yaml"], lite: "meeting-api",
  },
  {
    service: "runtime",
    decl: "core/runtime/src/runtime_kernel/config.v1.json",
    preflight: "core/runtime/src/runtime_kernel/config_preflight.py",
    scan: ["core/runtime/src"],
    compose: "runtime", helm: ["deployment-runtime.yaml"], lite: "runtime",
  },
  {
    service: "agent-api",
    decl: "core/agent/control_plane/config.v1.json",
    preflight: "core/agent/control_plane/config_preflight.py",
    scan: ["core/agent/control_plane", "core/agent/shared"],
    compose: "agent-api", helm: ["deployment-agent-api.yaml"], lite: "agent-api",
  },
  {
    // #526: the two services carrying today's fail-closed INTERNAL_API_SECRET guard.
    service: "admin-api",
    decl: "core/identity/services/admin-api/src/admin_api/config.v1.json",
    preflight: "core/identity/services/admin-api/src/admin_api/config_preflight.py",
    scan: ["core/identity/services/admin-api/src"],
    compose: "admin-api", helm: ["deployment-admin-api.yaml"], lite: "admin-api",
  },
  {
    service: "gateway",
    decl: "core/gateway/services/gateway/src/gateway/config.v1.json",
    preflight: "core/gateway/services/gateway/src/gateway/config_preflight.py",
    scan: ["core/gateway/services/gateway/src"],
    compose: "gateway", helm: ["deployment-gateway.yaml"], lite: "gateway",
  },
];

// docker-compose.yml is parsed line-wise (no YAML dep): a service block runs from `  name:` to the
// next 2-space key; its `environment:` list items are `- KEY=…`; `env_file:` marks .env-fed services.
function composeServiceEnv(service) {
  const text = readFileSync(join(ROOT, "deploy", "compose", "docker-compose.yml"), "utf8");
  const lines = text.split("\n");
  const start = lines.findIndex((l) => l.trimEnd() === `  ${service}:`);
  if (start < 0) return null;
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) {
    if (/^  [A-Za-z0-9_-]+:\s*$/.test(lines[i].trimEnd())) { end = i; break; }
  }
  const block = lines.slice(start, end);
  const keys = new Set();
  for (const l of block) { const m = l.match(/^\s*-\s*([A-Z][A-Z0-9_]*)=/); if (m) keys.add(m[1]); }
  const envFile = block.some((l) => l.trim() === "env_file:");
  return { keys, envFile };
}
const dotEnvExampleKeys = () => new Set(
  readFileSync(join(ROOT, "deploy", "compose", ".env.example"), "utf8").split("\n")
    .map((l) => (l.match(/^([A-Z][A-Z0-9_]*)=/) || [])[1]).filter(Boolean));
function helmEnvKeys(templates) {
  const keys = new Set();
  for (const t of templates) {
    const text = readFileSync(join(ROOT, "deploy", "helm", "charts", "vexa", "templates", t), "utf8");
    for (const m of text.matchAll(/-\s+name:\s+([A-Z][A-Z0-9_]*)\b/g)) keys.add(m[1]);
  }
  return keys;
}
function liteProgramEnv(program) {
  const text = readFileSync(join(ROOT, "deploy", "lite", "supervisord.conf"), "utf8");
  const m = text.split(new RegExp(`\\[program:${program}\\]`))[1];
  if (!m) return new Set();
  const section = m.split(/\n\[/)[0];
  const envLine = (section.match(/^environment=(.*)$/m) || [])[1] || "";
  return new Set([...envLine.matchAll(/([A-Z][A-Z0-9_]*)=/g)].map((x) => x[1]));
}
const liteEntrypointExports = () => new Set(
  [...readFileSync(join(ROOT, "deploy", "lite", "entrypoint.sh"), "utf8")
    .matchAll(/^export ([A-Z][A-Z0-9_]*)=/gm)].map((m) => m[1]));
function scanEnvReads(dirs) {
  const found = new Map(); // key -> first "file" it was seen in
  const walk = (dir) => {
    for (const name of readdirSync(dir)) {
      if (skippable(name) || name === "tests") continue;
      const p = join(dir, name);
      let s; try { s = statSync(p); } catch { continue; }
      if (s.isDirectory()) { walk(p); continue; }
      if (!name.endsWith(".py")) continue;
      const text = readFileSync(p, "utf8");
      for (const re of CONFIG_READ_RES) {
        re.lastIndex = 0;
        for (const m of text.matchAll(re)) {
          if (!found.has(m[1])) found.set(m[1], rel(p));
        }
      }
    }
  };
  for (const d of dirs) if (existsSync(join(ROOT, d))) walk(join(ROOT, d));
  return found;
}

function gateConfigContract() {
  if (!existsSync(CONFIG_CONTRACT_DIR)) { console.log("  ✓ gate:config-contract — no config.v1 contract yet (green-on-empty)"); return true; }
  const canonical = readFileSync(join(CONFIG_CONTRACT_DIR, "preflight.py"), "utf8");
  const envExample = dotEnvExampleKeys();
  const entrypointExports = liteEntrypointExports();
  const errs = [];
  let keyCount = 0, capCount = 0;
  for (const svc of CONFIG_ADOPTED) {
    const declPath = join(ROOT, svc.decl);
    if (!existsSync(declPath)) { errs.push(`${svc.service}: declaration missing (${svc.decl})`); continue; }
    // 1. schema conformance (the contract's own validator — same oracle as gate:schema)
    try { execSync(`node ${JSON.stringify(join(CONFIG_CONTRACT_DIR, "validate.mjs"))} --check --file ${JSON.stringify(declPath)}`, { stdio: "pipe" }); }
    catch (e) { errs.push(`${svc.service}: declaration does not conform:\n${(e.stdout || e.stderr || e).toString().slice(-800)}`); continue; }
    // 2. the vendored preflight is the canonical one, byte for byte
    if (!existsSync(join(ROOT, svc.preflight)) || readFileSync(join(ROOT, svc.preflight), "utf8") !== canonical)
      errs.push(`${svc.service}: ${svc.preflight} is missing or has drifted from deploy/contracts/config.v1/preflight.py (vendor it VERBATIM)`);
    const decl = JSON.parse(readFileSync(declPath, "utf8"));
    const declared = new Set((decl.keys || []).map((k) => k.key));
    const surfaceOnly = new Set((decl.surface_only || []).map((k) => k.key));
    keyCount += declared.size; capCount += Object.keys(decl.capabilities || {}).length;
    const compose = composeServiceEnv(svc.compose);
    if (!compose) { errs.push(`${svc.service}: compose service '${svc.compose}' not found`); continue; }
    const helm = helmEnvKeys(svc.helm);
    const lite = liteProgramEnv(svc.lite);
    // 3. declaration → surfaces (per the key's declared targets; default = all three)
    for (const k of decl.keys || []) {
      const targets = k.targets ?? ["compose", "helm", "lite"];
      if (targets.includes("compose") && !compose.keys.has(k.key) && !(compose.envFile && envExample.has(k.key)))
        errs.push(`${svc.service}: ${k.key} declared for compose but absent from the '${svc.compose}' environment block${compose.envFile ? " and .env.example" : ""}`);
      if (targets.includes("helm") && !helm.has(k.key))
        errs.push(`${svc.service}: ${k.key} declared for helm but absent from ${svc.helm.join("/")}`);
      if (targets.includes("lite") && !lite.has(k.key) && !entrypointExports.has(k.key))
        errs.push(`${svc.service}: ${k.key} declared for lite but absent from [program:${svc.lite}] env and entrypoint.sh exports`);
    }
    // 4. surfaces → declaration (explicit entries only; env_file feeds the whole .env by design)
    const surfaceSets = [["compose", compose.keys], ["helm", helm], ["lite", lite]];
    for (const [surface, keys] of surfaceSets) {
      for (const key of keys) {
        if (!declared.has(key) && !surfaceOnly.has(key) && !CONFIG_SURFACE_ALLOW.has(key))
          errs.push(`${svc.service}: ${surface} sets ${key} but the declaration does not carry it (declare it, or list it in surface_only with a reason)`);
      }
    }
    // 5. undeclared literal env reads in the service's source
    for (const [key, where] of scanEnvReads(svc.scan)) {
      if (!declared.has(key) && !CONFIG_SURFACE_ALLOW.has(key))
        errs.push(`${svc.service}: undeclared env read ${key} at ${where} — add it to ${svc.decl}`);
    }
  }
  if (errs.length) return fail(["config-contract (ADR-0026) — config.v1 violations:", ...errs.map((e) => "   " + e)]);
  console.log(`  ✓ gate:config-contract — ${CONFIG_ADOPTED.length} adopted service(s) · ${keyCount} declared keys · ${capCount} capabilities · declarations ≡ deploy surfaces ≡ code reads`);
  return true;
}

// gate:db-schema (#db-seal) — the DB schema is FROZEN in schema.seal.json. Any table/column add,
// drop, or change (in admin-api's models or meeting-api's mirror) trips this gate and requires a
// deliberate `pnpm seal:schema` re-seal — a human review step. This is the structural enforcement of
// "no unreviewed database changes": a stray migration or model edit can no longer land silently.
function _schemaDigest() {
  return JSON.parse(execSync("python3 scripts/schema_digest.py", { cwd: ROOT }).toString());
}

function _flattenSchema(d) {
  const flat = {};
  for (const [file, tables] of Object.entries(d)) {
    const svc = (file.match(/\/services\/([^/]+)\//) || [, file])[1];
    for (const [t, cols] of Object.entries(tables))
      for (const [c, def] of Object.entries(cols)) flat[`${svc}::${t}.${c}`] = def;
  }
  return flat;
}

function gateDbSchema() {
  if (!existsSync(SCHEMA_SEAL)) return fail(["gate:db-schema — schema.seal.json missing (run `pnpm seal:schema` to freeze the current DB schema)"]);
  let current;
  try { current = _schemaDigest(); }
  catch (e) { return fail([`gate:db-schema — could not compute the schema digest (python3 scripts/schema_digest.py):\n${(e.stdout || e.stderr || e).toString().slice(-600)}`]); }
  const cur = _flattenSchema(current);
  const old = _flattenSchema(JSON.parse(readFileSync(SCHEMA_SEAL, "utf8")));
  const errs = [];
  for (const k of Object.keys(cur)) if (!(k in old)) errs.push(`ADDED    ${k} = ${cur[k]}`);
  for (const k of Object.keys(old)) if (!(k in cur)) errs.push(`REMOVED  ${k}`);
  for (const k of Object.keys(cur)) if (k in old && cur[k] !== old[k]) errs.push(`CHANGED  ${k}: ${old[k]}  →  ${cur[k]}`);
  if (errs.length) return fail([
    "db-schema — the DB schema drifted from schema.seal.json. A deliberate change needs `pnpm seal:schema` + human review (lane:schema):",
    ...errs.map((e) => "   " + e),
  ]);
  const tables = new Set(Object.keys(cur).map((k) => k.split(".")[0]));
  console.log(`  ✓ gate:db-schema — ${Object.keys(cur).length} columns across ${tables.size} sealed table(s) match schema.seal.json`);
  return true;
}

// gate:db-budget (#529) — the connection-budget accounting the 2026-04-21 outage lacked. Every
// service that constructs a Postgres engine must be in deploy/db-budget.json; Σ(helm replicas ×
// per-service pool ceiling) + reserved must fit max_connections; and no service's code may set a
// pool_size/max_overflow HIGHER than its declared ceiling (so the budget can't silently under-count).
// Both scans below read the COMMITTED tree via `git grep --untracked` — tracked files plus new
// not-yet-added ones, never gitignored paths. The .venv/site-packages trees gate:python materializes
// under core/ match both patterns (numpy fixtures set pool_size=; sqlalchemy defines
// create_async_engine) and must not enter the budget. --untracked keeps a brand-new .py in scope.
//
// Both scans count a service's PRODUCTION source only. A test's engine is a throwaway no deployment
// runs: it holds zero production connections, so a pool literal in a test cannot under-state a budget
// whose unit is Σ(helm replicas × pool ceiling), and counting one could only invent a red against a
// service whose deployed pool is whatever its production source says. Excluding tests is therefore
// the contract gateDbBudget() already states in its own error text ("no non-test source constructs a
// DB engine there") — the two scans share one path test so they can never disagree on the population.
//
// A file is a test when it sits under a tests/ dir or its BASENAME starts with test_ — not merely
// when "test_" occurs somewhere in the path, which would drop production files (latest_pool.py) out
// of a production budget: the one direction this gate must never fail. pytest's other convention,
// the *_test.py suffix, is deliberately NOT a rule: core/agent/control_plane/config_test.py is
// production source (it implements the Settings → Models "Test" buttons), and excluding it would
// under-count for real. Filename heuristics have counterexamples in this tree; both are checked.
const _isTestPath = (p) => /(^|\/)tests?\//.test(p) || /(^|\/)test_[^/]*$/.test(p);

function _dbHoldingServices() {
  let out;
  try {
    out = execSync("git grep --untracked -l create_async_engine -- 'core/*.py'", { cwd: ROOT }).toString();
  } catch { return new Set(); }  // grep exit 1 = no matches
  const svcs = new Set();
  for (const path of out.split("\n")) {
    if (!path || _isTestPath(path)) continue;
    const m = path.match(/\/services\/([^/]+)\//);
    if (m) svcs.add(m[1]);
  }
  return svcs;
}

function _explicitPool() {
  // the largest explicit pool_size= / max_overflow= a service's production code sets and where, or {}
  // when every service relies on the framework default (the "silent default" #529 names). Used to
  // reject an under-stated budget. `-n -o`, never `-h`: the path has to survive the scan both to be
  // filtered on and to name the file in the error — a bare number is a verdict nobody can trace back
  // to a line. `-o` also splits two literals on one source line (pool_size=…, max_overflow=…) into
  // two records, so neither hides behind the other.
  let out = "";
  try {
    out = execSync(`git grep --untracked -noE '(pool_size|max_overflow) *= *[0-9]+' -- 'core/*.py' 2>/dev/null || true`, { cwd: ROOT }).toString();
  } catch { out = ""; }
  const found = {};
  for (const line of out.split("\n")) {
    // anchored to git grep's whole `path:lineno:match` record: -o makes the match the entire final
    // field, so a path that happens to spell "pool_size=5" can never be parsed as the literal.
    const m = line.match(/^(.+?):(\d+):(pool_size|max_overflow) *= *(\d+)$/);
    if (!m) continue;
    const [, path, lineno, key, val] = m;
    if (_isTestPath(path)) continue;
    const value = parseInt(val, 10);
    if (!found[key] || value > found[key].value) found[key] = { value, where: `${path}:${lineno}` };
  }
  return found;  // repo-wide (explicit overrides are rare); a match anywhere raises the floor
}

function _helmReplicas(key) {
  const vals = join(ROOT, "deploy", "helm", "charts", "vexa", "values.yaml");
  if (!existsSync(vals)) return null;
  const lines = readFileSync(vals, "utf8").split("\n");
  const start = lines.findIndex((l) => l.replace(/\s+$/, "") === `${key}:`);
  if (start < 0) return null;
  for (let i = start + 1; i < lines.length; i++) {
    if (/^[A-Za-z0-9_]+:/.test(lines[i])) break;               // next top-level key → left the block
    const m = lines[i].match(/^\s+replicaCount:\s*(\d+)/);
    if (m) return parseInt(m[1], 10);
  }
  return null;
}

function gateDbBudget() {
  const budgetPath = join(ROOT, "deploy", "db-budget.json");
  if (!existsSync(budgetPath)) return fail(["gate:db-budget — deploy/db-budget.json missing (the connection-budget accounting, #529)"]);
  let budget;
  try { budget = JSON.parse(readFileSync(budgetPath, "utf8")); }
  catch (e) { return fail([`gate:db-budget — deploy/db-budget.json is not valid JSON — ${e.message}`]); }

  const errs = [];
  const declared = new Set(Object.keys(budget.services || {}));
  const actual = _dbHoldingServices();
  for (const s of actual) if (!declared.has(s)) errs.push(`'${s}' constructs a Postgres engine but is absent from db-budget.json — account for it (replicas × pool)`);
  for (const s of declared) if (!actual.has(s)) errs.push(`db-budget lists '${s}' but no non-test source constructs a DB engine there — remove it or fix the name`);

  const explicit = _explicitPool();
  let total = Number(budget.reserved || 0);
  const rows = [];
  for (const [svc, cfg] of Object.entries(budget.services || {})) {
    const replicas = _helmReplicas(cfg.helm_key);
    if (replicas === null) { errs.push(`${svc}: could not read replicaCount for helm key '${cfg.helm_key}' in values.yaml`); continue; }
    if (explicit.pool_size && explicit.pool_size.value > cfg.pool_size)
      errs.push(`${svc}: code sets pool_size=${explicit.pool_size.value} (${explicit.pool_size.where}) but db-budget declares ${cfg.pool_size} (under-count)`);
    if (explicit.max_overflow && explicit.max_overflow.value > cfg.max_overflow)
      errs.push(`${svc}: code sets max_overflow=${explicit.max_overflow.value} (${explicit.max_overflow.where}) but db-budget declares ${cfg.max_overflow} (under-count)`);
    const ceiling = Number(cfg.pool_size || 0) + Number(cfg.max_overflow || 0);
    const conns = replicas * ceiling;
    total += conns;
    rows.push(`${svc} ${replicas}×${ceiling}=${conns}`);
  }
  const limit = Number(budget.max_connections);
  if (!errs.length && total > limit)
    errs.push(`Σ ${total} connections EXCEEDS max_connections ${limit} — [${rows.join(", ")}, reserved ${budget.reserved}]. Reduce replicas/pool, raise the DB limit, or enable pgbouncer.`);

  if (errs.length) return fail(["db-budget (#529, the 2026-04-21 outage shape) — connection-budget violations:", ...errs.map((e) => "   " + e)]);
  console.log(`  ✓ gate:db-budget — Σ ${total}/${limit} connections fits [${rows.join(", ")}, reserved ${budget.reserved}]`);
  return true;
}

// gate:lite-makefile (#581) — no comment line inside a `\`-continued recipe block in
// deploy/lite/Makefile. A bare `#` recipe line without a trailing `\` ENDS the continuation, so
// make runs the rest in a separate shell where the block's variables are lost (the empty-$IMG
// class: `docker run … $IMG` with no image); with a trailing `\` the shell comment swallows the
// continued line instead. Either way the block silently breaks — commentary belongs ABOVE the
// recipe, in `##` doc lines. Green-on-empty if the Makefile is absent.
function gateLiteMakefile() {
  const f = join(ROOT, "deploy", "lite", "Makefile");
  if (!existsSync(f)) { console.log("  ✓ gate:lite-makefile — no deploy/lite/Makefile (green-on-empty)"); return true; }
  const lines = readFileSync(f, "utf8").split("\n");
  const errs = [];
  let cont = false;  // the previous RECIPE line ended with `\` — we are inside a continued block
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const recipe = line.startsWith("\t");
    if (recipe && cont && line.trim().startsWith("#"))
      errs.push(`deploy/lite/Makefile:${i + 1} — comment line inside a \`\\\`-continued recipe block (orphans the rest of the block's shell state; move it above the recipe as a \`##\` doc line)`);
    cont = recipe && line.replace(/\s+$/, "").endsWith("\\");
  }
  if (errs.length) return fail(["lite-makefile (#581, the empty-$IMG class):", ...errs.map((e) => "   " + e)]);
  console.log("  ✓ gate:lite-makefile — no comment lines inside continued recipe blocks (deploy/lite/Makefile)");
  return true;
}

const GATES = { readme: gateReadme, "lite-makefile": gateLiteMakefile, "docs-version": gateDocsVersion, dataflow: gateDataflow, isolation: gateIsolation, "isolation-py": gateIsolationPy, exports: gateExports, graph: gateGraph, "graph-py": gateGraphPy, schema: gateSchema, "contract-version": gateContractVersion, "config-contract": gateConfigContract, "db-schema": gateDbSchema, "db-budget": gateDbBudget, python: gatePython, stack: gateStack, node: gateNode, health: gateHealth, access: gateAccess, tracing: gateTracing, replay: gateReplay, telemetry: gateTelemetry, eval: gateEval, licenses: gateLicenses, compose: gateCompose, "execution-env": gateExecutionEnv, "test-isolation": gateTestIsolation, "arch-report": gateArchReport, parity: gateParity, "compose-stress": gateComposeStress, "compose-chaos": gateComposeChaos, "eval-baseline": gateEvalBaseline, "contract-conformance": gateContractConformance };
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
// `seal-schema` (not a gate) — freeze the current DB schema (tables+columns) into schema.seal.json.
// Run ONLY after a deliberately-reviewed model change (the diff of schema.seal.json IS the review).
if (which === "seal-schema") {
  const digest = execSync("python3 scripts/schema_digest.py", { cwd: ROOT }).toString();
  writeFileSync(SCHEMA_SEAL, digest.endsWith("\n") ? digest : digest + "\n");
  const flat = _flattenSchema(JSON.parse(digest));
  const tables = new Set(Object.keys(flat).map((k) => k.split(".")[0]));
  console.log(`sealed DB schema — ${Object.keys(flat).length} columns across ${tables.size} table(s) → ${rel(SCHEMA_SEAL)}`);
  process.exit(0);
}
// `seal-arch` (not a gate) — stamp the chart's canonical hash as the new asserted-true baseline.
// Run after deliberately reviewing a change to architecture.calm.json (the diff is the review surface).
if (which === "seal-arch") {
  const h = archHash();
  writeFileSync(ARCH_SEAL, JSON.stringify({ "architecture.calm.json": h }, null, 2) + "\n");
  console.log(`sealed architecture.calm.json (${h.slice(0, 12)}…) → ${rel(ARCH_SEAL)}`);
  // Regenerate the concise DSL projection from the same baseline so it never lags the seal.
  execSync("node scripts/arch-dsl.mjs --write", { cwd: ROOT, stdio: "inherit" });
  process.exit(0);
}
const run = which === "all" ? Object.keys(GATES) : [which];
if (run.some((g) => !GATES[g])) { console.error(`unknown gate: ${which}`); process.exit(2); }
console.log(`\n▶ gates: ${run.join(", ")}`);
const ok = run.map((g) => GATES[g]()).every(Boolean);
console.log(ok ? "\n✅ gates green\n" : "\n❌ gates failed\n");
process.exit(ok ? 0 : 1);
