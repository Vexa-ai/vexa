// Regression tests for gate:db-budget's two source scans in gates.mjs (#529, #702).
// Run: node --test scripts/gates.test.mjs   (CI: the gates.yml `static` job runs scripts/*.test.mjs
// directly — scripts/ is not a workspace package, so `pnpm test` never reaches these files)
//
// These plant real files in the checkout and run the real gate as a subprocess, deliberately: the
// defect class here lives in the SHELL PIPELINE, not the parse. A scan that strips the filename
// (`grep -h`) silently disarms every path-based filter downstream of it, and the bare numbers it
// emits still parse perfectly — so a test that stubs the grep and feeds the parse a fixture would
// stay green through exactly the bug it was written to catch. The planted file IS the input
// population: `git grep --untracked` reads the working tree, so a file on disk is a real input.

import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { writeFileSync, readFileSync, rmSync, mkdirSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

// admin-api is declared in deploy/db-budget.json (pool_size 5 / max_overflow 10), so a literal
// planted here is compared against a real ceiling. agent-api is a real service dir that is NOT
// declared — a create_async_engine there is what a phantom-service error would be read from.
const TEST_FILE = "core/identity/services/admin-api/tests/test_zz_planted_pool.py";
const PROD_FILE = "core/identity/services/admin-api/src/admin_api/zz_planted_pool.py";
const PHANTOM_TEST_FILE = "core/agent/services/agent-api/tests/test_zz_planted_engine.py";

// two literals on ONE line, both far above every declared ceiling: if either is counted the gate
// must red, and the pair also pins that neither hides behind the other.
const POOL_LITERALS = "configure(url, pool_size=20, max_overflow=30)\n";

// Plants a file, runs fn, and removes EVERYTHING it created — including any directory it had to
// make. An empty leftover dir is invisible to `git status` (git tracks files) but very visible to
// gate:readme, which reads the filesystem: a fixture that leaks one reds a sibling gate later, in
// another run, with no trace of who dropped it. So remember the topmost ancestor that did not
// already exist and prune from there.
function withPlanted(relPath, body, fn) {
  const abs = join(ROOT, relPath);
  const dir = dirname(abs);
  let prune = null;
  for (let d = dir; !existsSync(d); d = dirname(d)) prune = d;
  mkdirSync(dir, { recursive: true });
  writeFileSync(abs, body);
  try {
    return fn();
  } finally {
    rmSync(abs, { force: true });
    if (prune) rmSync(prune, { recursive: true, force: true });
  }
}

function runDbBudget() {
  try {
    return { green: true, out: execFileSync("node", ["scripts/gates.mjs", "db-budget"], { cwd: ROOT, encoding: "utf8" }) };
  } catch (e) {
    return { green: false, out: `${e.stdout || ""}${e.stderr || ""}` };
  }
}

const rx = (s) => new RegExp(s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));

// ── the population: production source counts, tests do not ──────────────────────────────────────

test("vacuity control: the committed tree is green (a red here invalidates every row below)", () => {
  const r = runDbBudget();
  assert.equal(r.green, true, `the clean tree already reds — these fixtures prove nothing:\n${r.out}`);
});

test("a pool literal in a test file does NOT red the budget (a test holds no production connections)", () => {
  const r = withPlanted(TEST_FILE, POOL_LITERALS, runDbBudget);
  assert.equal(r.green, true, `a literal in ${TEST_FILE} red the connection budget:\n${r.out}`);
});

test("negative control: the SAME literal in production source DOES red the budget", () => {
  const r = withPlanted(PROD_FILE, POOL_LITERALS, runDbBudget);
  assert.equal(r.green, false, "the gate no longer catches an under-stated budget — the scan is inert");
  assert.match(r.out, /pool_size=20/);
  assert.match(r.out, /max_overflow=30/); // -o keeps the second literal on the line its own record
});

test("the under-count error names the file:line it read the literal from, not just a number", () => {
  const r = withPlanted(PROD_FILE, POOL_LITERALS, runDbBudget);
  assert.match(r.out, rx(`${PROD_FILE}:1`));
});

test("a path that spells a literal is not parsed as one (the match is the record's final field)", () => {
  // The file must CONTAIN a real literal or git grep emits no record for it at all and the parse is
  // never reached — the fixture has to produce `…/pool_size=99_zz.py:1:pool_size=3`. pool_size=3 is
  // UNDER the declared 5, so this can only red if the parse reads the 99 out of the path.
  const weird = "core/identity/services/admin-api/src/admin_api/pool_size=99_zz.py";
  const r = withPlanted(weird, "configure(url, pool_size=3)\n", runDbBudget);
  assert.equal(r.green, true, `the "99" in the path was parsed as a declared pool:\n${r.out}`);
});

// ── the population's edges: filename heuristics have counterexamples in this tree ───────────────

test("a production file whose path merely CONTAINS test_ is still counted (latest_pool.py)", () => {
  const prod = "core/identity/services/admin-api/src/admin_api/latest_pool_zz.py";
  const r = withPlanted(prod, POOL_LITERALS, runDbBudget);
  assert.equal(r.green, false, `"latest_pool_zz.py" was read as a test — a production pool literal
    dropped out of the production budget, the one direction this gate must not fail:\n${r.out}`);
});

test("a production file using the *_test.py suffix is still counted (config_test.py's shape)", () => {
  const prod = "core/identity/services/admin-api/src/admin_api/zz_config_test.py";
  const r = withPlanted(prod, POOL_LITERALS, runDbBudget);
  assert.equal(r.green, false, `"zz_config_test.py" was read as a test, but core/agent/control_plane/
    config_test.py proves that suffix names production source in this tree:\n${r.out}`);
});

// ── the sibling scan reads the same population (shared _isTestPath) ─────────────────────────────

test("a test-only create_async_engine does not invent a service in the budget", () => {
  const r = withPlanted(PHANTOM_TEST_FILE, "engine = create_async_engine(FAKE_URL)\n", runDbBudget);
  assert.equal(r.green, true, `a test fixture phantomed agent-api into the connection budget:\n${r.out}`);
});

// ════════════════════════════════════════════════════════════════════════════════════════════════
// #653 — gate:image-licenses and gate:runtime-parity. Same plant-and-run-the-real-gate discipline: a
// gate that never reds on the input it was written to catch is theatre. These edit a tracked deploy
// file in place, run the real gate as a subprocess, and restore — so each RED is the actual pipeline
// firing, not a stubbed parse. Every fixture is paired with the vacuity control that the clean tree
// is green (a red there would invalidate the fixture below it).
// ════════════════════════════════════════════════════════════════════════════════════════════════

function runGate(name) {
  try { return { green: true, out: execFileSync("node", ["scripts/gates.mjs", name], { cwd: ROOT, encoding: "utf8" }) }; }
  catch (e) { return { green: false, out: `${e.stdout || ""}${e.stderr || ""}` }; }
}
// Temporarily replace `find`→`repl` in a tracked file, run fn, always restore the exact original bytes.
function withEdited(relPath, find, repl, fn) {
  const abs = join(ROOT, relPath);
  const orig = readFileSync(abs, "utf8");
  const edited = orig.replace(find, repl);
  assert.notEqual(edited, orig, `fixture setup: pattern not found in ${relPath} — the test would prove nothing`);
  writeFileSync(abs, edited);
  try { return fn(); } finally { writeFileSync(abs, orig); }
}


// ── gate:config-contract check 6 — the shared lite entrypoint (F130) ────────────────────────────
// Checks 3 and 4 walk compose, helm and the supervisord program env in BOTH directions.
// deploy/lite/entrypoint.sh was read ONLY as check 3's fallback, so it was the one surface, in the
// one direction, that nothing walked: an export whose declaration AND reader were both deleted left
// no refusal and no warning. Measured on the tip before the fix — the same plant was green.
// entrypoint.sh is touched by no other test file, so the in-place edit below stays inside this
// file's sequential run.
const LITE_ENTRYPOINT = "deploy/lite/entrypoint.sh";

test("config-contract vacuity: the committed lite entrypoint is green", () => {
  const r = runGate("config-contract");
  assert.equal(r.green, true, `the clean tree must be green or the fixture below proves nothing:\n${r.out}`);
});

test("an entrypoint.sh export that no adopted declaration carries is RED, named by file:line", () => {
  const r = withEdited(LITE_ENTRYPOINT, "\nexport ", "\nexport VEXA_PHANTOM_ENTRY=1\nexport ",
    () => runGate("config-contract"));
  assert.equal(r.green, false, `a phantom lite export passed the contract gate:\n${r.out}`);
  assert.match(r.out, /entrypoint\.sh:\d+ exports VEXA_PHANTOM_ENTRY/,
    `the failure must name the FILE AND LINE the operator has to open:\n${r.out}`);
});

const COMPOSE = "deploy/compose/docker-compose.yml";
const VALUES = "deploy/helm/charts/vexa/values.yaml";
const LITE = "deploy/lite/Dockerfile.lite";
const IMG_MANIFEST = "image-licenses.json";

// ── gate:runtime-parity ─────────────────────────────────────────────────────────────────────────

test("runtime-parity vacuity: the committed tree (all surfaces on Valkey 8) is green", () => {
  const r = runGate("runtime-parity");
  assert.equal(r.green, true, `the clean tree already reds — the fixtures below prove nothing:\n${r.out}`);
});

test("runtime-parity RED (#636): Lite reverting to apt redis-server (jammy 6.0.16) reds against XAUTOCLAIM", () => {
  const r = withEdited(LITE, "supervisor postgresql-client", "supervisor redis-server postgresql-client",
    () => runGate("runtime-parity"));
  assert.equal(r.green, false, "a surface pinned below XAUTOCLAIM's floor no longer reds — the #636 rung is inert");
  assert.match(r.out, /lite/);
  assert.match(r.out, /XAUTOCLAIM/);
});

test("runtime-parity RED (#637 class): a compose pin below a used command's floor reds", () => {
  const r = withEdited(COMPOSE, "image: valkey/valkey:8-alpine", "image: redis:6.0-alpine",
    () => runGate("runtime-parity"));
  assert.equal(r.green, false, "a compose engine pinned below the used-command floor no longer reds");
  assert.match(r.out, /compose/);
  assert.match(r.out, /6\.0/);
});

// ── gate:image-licenses ─────────────────────────────────────────────────────────────────────────

test("image-licenses vacuity: the committed tree (Valkey everywhere) is green", () => {
  const r = runGate("image-licenses");
  assert.equal(r.green, true, `the clean tree already reds — the fixtures below prove nothing:\n${r.out}`);
});

test("image-licenses RED: an undeclared pinned image (a stray redis:7.4) reds", () => {
  // redis:7.4 is exactly the source-available (RSALv2/SSPL) engine #653 keeps out; undeclared ⇒ loud red.
  const r = withEdited(COMPOSE, "image: valkey/valkey:8-alpine", "image: redis:7.4-alpine",
    () => runGate("image-licenses"));
  assert.equal(r.green, false, "an undeclared image pin sailed through — the 'green gate ships an un-audited component' hole is back");
  assert.match(r.out, /undeclared pinned image/);
  assert.match(r.out, /redis:7\.4/);
});

test("image-licenses RED: the Lite apt redis-server parity trap reds", () => {
  const r = withEdited(LITE, "supervisor postgresql-client", "supervisor redis-server postgresql-client",
    () => runGate("image-licenses"));
  assert.equal(r.green, false, "the Lite apt redis-server guard is inert");
  assert.match(r.out, /redis-server/);
});

test("image-licenses RED: a bundled component under a source-available licence (SSPL) is FORBIDDEN", () => {
  // The strict redistribution path: anything baked into a vexaai/* image must be Cat A (or B-with-reason).
  const inject = '"bundled": [\n    {\n      "name": "redis",\n      "license": "SSPLv1",\n      "artifact": "vexaai/vexa-lite",\n      "reason": "test fixture"\n    },';
  const r = withEdited(IMG_MANIFEST, '"bundled": [', inject, () => runGate("image-licenses"));
  assert.equal(r.green, false, "a source-available bundled component was not forbidden — the redistribution guard is inert");
  assert.match(r.out, /FORBIDDEN \(Cat X\)/);
  assert.match(r.out, /redis/);
});

const MINIO_JOB = "deploy/helm/charts/vexa/templates/job-minio-init.yaml";

test("image-licenses RED: an undeclared image pinned in a helm TEMPLATE (not just values) reds", () => {
  // The gate must read helm templates, not only compose + values — a literal `image:` in a template
  // is a real pin. An undeclared one must red, else the 'green gate ships an un-audited component' hole.
  // #1321 moved the mc image from a template literal to values (minio.mcImage) — the template
  // line is now templated. The test's subject is unchanged: inject a LITERAL pin into the
  // template and require the gate to read it.
  const r = withEdited(MINIO_JOB,
    "image: {{ .Values.minio.mcImage.repository }}:{{ .Values.minio.mcImage.tag }}",
    "image: somevendor/unaudited:1.2",
    () => runGate("image-licenses"));
  assert.equal(r.green, false, "an undeclared image in a helm template sailed through — the gate never read templates");
  assert.match(r.out, /undeclared pinned image/);
  assert.match(r.out, /somevendor\/unaudited/);
});

test("image-licenses RED: an undeclared structured Helm repository/tag pin reds", () => {
  const injected = [
    "reviewProbe:",
    "  image:",
    "    repository: somevendor/unaudited",
    "    tag: 1.2",
    "",
    "minio:",
  ].join("\n");
  const r = withEdited(VALUES, "minio:\n", injected, () => runGate("image-licenses"));
  assert.equal(r.green, false, "a structured Helm repository/tag image pin sailed through");
  assert.match(r.out, /undeclared pinned image/);
  assert.match(r.out, /somevendor\/unaudited:1\.2/);
});

test("image-licenses RED: an undeclared Dockerfile FROM pin reds", () => {
  const base = "FROM mcr.microsoft.com/playwright:v1.56.0-noble AS bot-builder";
  const injected = `FROM somevendor/unaudited:1.2 AS review-probe\n${base}`;
  const r = withEdited(LITE, base, injected, () => runGate("image-licenses"));
  assert.equal(r.green, false, "an undeclared Dockerfile FROM image pin sailed through");
  assert.match(r.out, /undeclared pinned image/);
  assert.match(r.out, /somevendor\/unaudited:1\.2/);
});

test("runtime-parity RED: the bare `apt install` form (not just apt-get) is caught too", () => {
  // A contributor who writes `apt install redis-server` (no -get) must not bypass the #636 guard.
  const inject = "RUN apt install -y redis-server\nFROM mcr.microsoft.com/playwright:v1.56.0-noble AS final";
  const r = withEdited(LITE, "FROM mcr.microsoft.com/playwright:v1.56.0-noble AS final", inject,
    () => runGate("runtime-parity"));
  assert.equal(r.green, false, "`apt install redis-server` (no -get) bypassed the parity guard");
  assert.match(r.out, /lite/);
  assert.match(r.out, /XAUTOCLAIM/);
});

// ════════════════════════════════════════════════════════════════════════════════════════════════
// #209 (F209) — gate:python used to `return fail(...)` INSIDE its per-package loop, so the first
// red package stopped the run and every later package was never even invoked. This is the
// mechanism behind #1434's "CI never executed core/flows, lite or the rig": #1473 fixed the
// package that happened to be red, not the loop that stopped after it. Real uv-backed Python
// packages are planted under scripts/ — walkDirs() reads the whole tree from ROOT, so a package
// planted here is a real input, not a stub — and the real gate runs as a subprocess, same
// plant-and-run discipline as the fixtures above.
// ════════════════════════════════════════════════════════════════════════════════════════════════

const PY_PYPROJECT = (name) => `[project]
name = "${name}"
version = "0.0.0"
requires-python = ">=3.10"
dependencies = []

[dependency-groups]
dev = ["pytest>=9.0.3"]

[tool.pytest.ini_options]
testpaths = ["tests"]
`;
const PY_TEST_PASS = "def test_zz_planted():\n    assert True\n";
const PY_TEST_FAIL = "def test_zz_planted():\n    assert False, \"planted failure\"\n";

// Plants N fixture Python packages (each { dir, pass }), runs fn, then removes every planted
// directory whole — each dir is fully owned by this helper (created here, nothing pre-existing
// under it), so a plain recursive rm cannot eat a sibling's files the way a partial-path prune
// could.
function withPlantedPyPkgs(specs, fn) {
  for (const { dir, pass } of specs) {
    const abs = join(ROOT, dir);
    mkdirSync(join(abs, "tests"), { recursive: true });
    writeFileSync(join(abs, "pyproject.toml"), PY_PYPROJECT(dir.replace(/[^a-z0-9]+/gi, "-")));
    writeFileSync(join(abs, "tests", "test_zz_planted.py"), pass ? PY_TEST_PASS : PY_TEST_FAIL);
  }
  try {
    return fn();
  } finally {
    for (const { dir } of specs) rmSync(join(ROOT, dir), { recursive: true, force: true });
  }
}

const PKG_A = "scripts/zz-gatepy-fixture-a";
const PKG_B = "scripts/zz-gatepy-fixture-b";

test("gate:python vacuity: the committed tree is green (a red here invalidates every row below)", () => {
  const r = runGate("python");
  assert.equal(r.green, true, `the clean tree already reds — these fixtures prove nothing:\n${r.out}`);
});

test("gate:python RED (#209): a single failing package still fails the gate", () => {
  const r = withPlantedPyPkgs([{ dir: PKG_A, pass: false }], () => runGate("python"));
  assert.equal(r.green, false, `a planted failing package did not red the gate:\n${r.out}`);
  assert.match(r.out, new RegExp(PKG_A.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
});

test("gate:python RED (#209): two failing packages are BOTH named in the one failure — the loop must not stop at the first", () => {
  const r = withPlantedPyPkgs([{ dir: PKG_A, pass: false }, { dir: PKG_B, pass: false }], () => runGate("python"));
  assert.equal(r.green, false, `two planted failing packages did not red the gate:\n${r.out}`);
  // both packages must appear — a `return` inside the loop would report only PKG_A (the first
  // walked) and PKG_B would never even be run, let alone named.
  assert.match(r.out, new RegExp(PKG_A.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")), `PKG_A missing from output:\n${r.out}`);
  assert.match(r.out, new RegExp(PKG_B.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")), `PKG_B missing — the gate stopped after the first red package:\n${r.out}`);
  // the failure names how many of the population went red, not just that "a" package did
  assert.match(r.out, /2\/\d+ package\(s\) failed pytest/, `no failing-package count in the output:\n${r.out}`);
});

test("gate:python: one red package alongside a green one still runs (and names) both — green does not mask red, red does not skip green", () => {
  const r = withPlantedPyPkgs([{ dir: PKG_A, pass: true }, { dir: PKG_B, pass: false }], () => runGate("python"));
  assert.equal(r.green, false, `a genuinely red package among green ones did not red the gate:\n${r.out}`);
  assert.match(r.out, new RegExp(PKG_B.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")), `the red package (PKG_B) is not named:\n${r.out}`);
  assert.doesNotMatch(r.out, new RegExp(PKG_A.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")), `the green package (PKG_A) was wrongly named as a failure:\n${r.out}`);
});

test("gate:python GREEN: multiple passing planted packages do not red the gate", () => {
  const r = withPlantedPyPkgs([{ dir: PKG_A, pass: true }, { dir: PKG_B, pass: true }], () => runGate("python"));
  assert.equal(r.green, true, `two genuinely green planted packages reded the gate:\n${r.out}`);
});
