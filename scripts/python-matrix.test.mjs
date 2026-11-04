// The `python` matrix in .github/workflows/gates.yml covers exactly what gate:python covers, and the
// one check branch protection and the merge queue require is still `gates`.
// Run: node --test scripts/python-matrix.test.mjs   (CI: the `static` job's gate script tests)
//
// Why it exists: in one job, in series, the Python population took 29-46 min (gates runs
// 41794533276, 41803777655), past the merge queue's 30-minute check window (ruleset merge-queue-main,
// check_response_timeout_minutes: 30), so any PR in the queue could time out. The matrix splits it.
// These rows keep the split honest: a package missing from the matrix would go untested in CI while
// staying green, and a renamed aggregate would leave branch protection waiting on a check that
// never reports.
import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { guardTree, REPO } from "./test-tree.mjs";
import { parse } from "yaml";
import { readPythonMatrix, checkPythonMatrix, gatingErrors } from "./python-matrix.mjs";

guardTree();

const WORKFLOW = readFileSync(join(REPO, ".github", "workflows", "gates.yml"), "utf8");
const WF = parse(WORKFLOW);
const population = () => JSON.parse(execFileSync("node", [join(REPO, "scripts", "gates.mjs"), "python-packages"], { cwd: REPO, encoding: "utf8" }));

// ── the real tree ────────────────────────────────────────────────────────────────────────────────

test("every package gate:python runs is in exactly one group of the gates.yml `python` matrix", () => {
  const pkgs = population();
  assert.ok(pkgs.length > 0, "gate:python found no packages: the check below would prove nothing");
  const { entries, errs } = readPythonMatrix(WORKFLOW);
  assert.deepEqual(errs, [], "the `python` matrix could not be read");
  assert.deepEqual(checkPythonMatrix(pkgs, entries), []);
});

test("each `python` matrix leg runs the gate on its own selection, every leg reports, and none outlives the queue window", () => {
  const job = WF.jobs.python;
  assert.ok(job.steps.some((s) => s.run === "node scripts/gates.mjs python ${{ matrix.select }}"),
    "the leg must hand its matrix selection to gate:python, or every leg runs the whole population (or nothing)");
  assert.equal(job.strategy["fail-fast"], false, "fail-fast would cancel the other legs at the first red one, hiding their reds (F209)");
  assert.ok(Number.isInteger(job["timeout-minutes"]), "the `python` job carries no timeout-minutes: a leg that grows would stall the queue instead of failing by name");
  assert.ok(job["timeout-minutes"] < 30, `timeout-minutes ${job["timeout-minutes"]} is not under the merge queue's 30-minute window`);
});

test("the required check is still `gates`: one job by that id, no display name, joining python with static and node", () => {
  const job = WF.jobs.gates;
  assert.ok(job, "no `gates` job: branch protection and the merge queue require a `gates` check");
  assert.equal(job.name, undefined, "a job-level name would rename the check context branch protection requires");
  for (const leg of ["static", "python", "node"]) assert.ok(job.needs.includes(leg), `\`gates\` no longer needs ${leg}`);
  assert.equal(job.if, "always()", "without if: always() a failed leg SKIPS `gates`, and a skipped required check reads as satisfied");
  assert.equal(job.steps[0].env.R_PYTHON, "${{ needs.python.result }}", "`gates` does not read the python legs' result");
});

test("the docker fixture image is pulled in the one leg that runs core/runtime, and only there", () => {
  const step = WF.jobs.python.steps.find((s) => s.run === "docker pull alpine");
  assert.ok(step, "no `docker pull alpine` step: core/runtime's docker-backend test needs the image pre-pulled");
  const group = (String(step.if || "").match(/^matrix\.group == '([a-z0-9-]+)'$/) || [])[1];
  assert.ok(group, `the docker pull step should run in one named leg (if: matrix.group == '<group>'), found: ${step.if || "no condition"}`);
  const { entries } = readPythonMatrix(WORKFLOW);
  const holder = entries.find((e) => e.selectors.some((s) => s.replace(/@.*$/, "") === "core/runtime"));
  assert.equal(holder?.group, group, "the docker pull runs in a different leg from core/runtime");
});

test("nothing between a python leg and the required check can let a red leg pass", () => {
  assert.deepEqual(gatingErrors(WORKFLOW), []);
});

// Each row plants one way a leg could stop gating while every other row above stays green, into the
// real gates.yml text, and requires gatingErrors to name it.
const plant = (find, repl) => {
  const out = WORKFLOW.replace(find, repl);
  assert.notEqual(out, WORKFLOW, `fixture setup: ${find} not found in gates.yml, so the row would prove nothing`);
  return out;
};
const GATE_STEP = /^( {6}- run: node scripts\/gates\.mjs python \$\{\{ matrix\.select \}\}\n)/m;
const PY_JOB = /^( {2}python:\n)/m;
for (const [what, wf, rx] of [
  ["an `if:` under the gate step skips it in some legs", () => plant(GATE_STEP, "$1        if: matrix.group != 'services'\n"), /gate step carries `if:/],
  ["continue-on-error on the gate step", () => plant(GATE_STEP, "$1        continue-on-error: true\n"), /gate step carries `continue-on-error:/],
  ["continue-on-error on the python job", () => plant(PY_JOB, "$1    continue-on-error: true\n"), /`python` job carries `continue-on-error:/],
  ["a job-level `if:` on the python job", () => plant(PY_JOB, "$1    if: github.event_name != 'merge_group'\n"), /`python` job carries `if:/],
  ["the gate step written with its condition first", () => plant(GATE_STEP, "      - if: matrix.group != 'services'\n        run: node scripts/gates.mjs python ${{ matrix.select }}\n"), /gate step carries `if:/],
  ["a second matrix axis beside include", () => plant(/^( {6}matrix:\n)/m, "$1        skip: []\n"), /matrix holds `skip:/],
  ["continue-on-error on the gates aggregate", () => plant(/^( {2}gates:\n)/m, "$1    continue-on-error: true\n"), /`gates` job carries `continue-on-error:/],
  ["a step-level `if:` in the gates aggregate", () => plant(/^( {6}- name: All legs green\?\n)/m, "$1        if: needs.python.result == 'success'\n"), /`gates` job carries `if: needs/],
  ["the aggregate accepting anything but failure", () => plant('[ "$R_PYTHON" = "success" ]', '[ "$R_PYTHON" != "failure" ]'), /does not require needs\.python\.result == success/],
  ["the aggregate not reading the python result", () => plant(/^ +R_PYTHON: \$\{\{ needs\.python\.result \}\}\n/m, ""), /R_PYTHON: \$\{\{ needs\.python\.result \}\}/],
  ["the aggregate swallowing its own failure", () => plant('or was cancelled"; exit 1; }', 'or was cancelled"; exit 1; } || true'), /`\|\| true`/],
  // a skipped python result, with the success text still present on the line
  ["the aggregate also accepting a skipped python result", () => plant('[ "$R_PYTHON" = "success" ]', '{ [ "$R_PYTHON" = "success" ] || [ "$R_PYTHON" = "skipped" ]; }'), /does not require needs\.python\.result == success/],
  ["the aggregate accepting anything but a failed node leg", () => plant('[ "$R_NODE" = "success" ]', '[ "$R_NODE" != "failure" ]'), /does not require .*needs\.node\.result == success/],
  ["the aggregate accepting anything but a failed static leg", () => plant('[ "$R_STATIC" = "success" ]', '[ "$R_STATIC" != "failure" ]'), /does not require .*needs\.static\.result == success/],
  ["the aggregate overwriting the python result first", () => plant(/^( {10}echo "static=)/m, '          R_PYTHON=success\n$1'), /does not require needs\.python\.result == success/],
  ["a shell override on the aggregate step", () => plant(/^( {6}- name: All legs green\?\n)/m, '$1        shell: "true {0}"\n'), /`gates` job carries `shell:/],
  // a leg that runs the gate and collects without running a test
  ["env on the python job", () => plant(PY_JOB, '$1    env: { PYTEST_ADDOPTS: "--collect-only" }\n'), /`python` job carries `env:/],
  ["env on the gate step", () => plant(GATE_STEP, '$1        env: { PYTEST_ADDOPTS: "--collect-only" }\n'), /gate step carries `env:/],
  ["a shell override on the gate step", () => plant(GATE_STEP, '$1        shell: "true {0}"\n'), /gate step carries `shell:/],
  ["a default shell on the python job", () => plant(PY_JOB, '$1    defaults: { run: { shell: "true {0}" } }\n'), /`python` job carries `defaults:/],
  ["an earlier step writing $GITHUB_ENV", () => plant(GATE_STEP, '      - run: echo "PYTEST_ADDOPTS=--collect-only" >> "$GITHUB_ENV"\n$1'), /writes \$GITHUB_ENV/],
  ["a workflow-level env", () => plant(/^(permissions:\n {2}contents: read\n)/m, "$1env:\n  PYTEST_ADDOPTS: --collect-only\n"), /workflow-level `env:/],
  // the same keys in other YAML spellings, which GitHub reads as the real key (HIGH 1)
  ["a quoted `\"if\"` on the gate step", () => plant(GATE_STEP, `$1        "if": matrix.group != 'services'\n`), /gate step carries `if:/],
  ["a spaced `if :` on the gate step", () => plant(GATE_STEP, "$1        if : matrix.group != 'services'\n"), /gate step carries `if:/],
  ["a quoted `'continue-on-error'` on the python job", () => plant(PY_JOB, "$1    'continue-on-error': true\n"), /`python` job carries `continue-on-error:/],
  ["a quoted `\"continue-on-error\"` on the gates aggregate", () => plant(/^( {2}gates:\n)/m, '$1    "continue-on-error": true\n'), /`gates` job carries `continue-on-error: true`/],
  ["the gate step's key written twice", () => plant(GATE_STEP, "$1        if: always()\n        if: matrix.group != 'services'\n"), /does not parse/],
  ["working-directory on the gate step (HIGH 2)", () => plant(GATE_STEP, "$1        working-directory: deploy/storage\n"), /gate step carries `working-directory:/],
  // a job that runs the gate somewhere its env or image is set (MED 1)
  ["a container on the python job, with env", () => plant(PY_JOB, "$1    container: { image: 'python:3.12', env: { PYTEST_ADDOPTS: '-k smoke' } }\n"), /`python` job carries `container:/],
  ["services on the python job", () => plant(PY_JOB, "$1    services: { pg: { image: postgres, env: { PYTEST_ADDOPTS: '-k smoke' } } }\n"), /`python` job carries `services:/],
  // a step before the gate that sets its env in a spelling no text rule sees (MED 2)
  ["a step writing $GITHUB_ENV split across quotes", () => plant(GATE_STEP, `      - run: echo "PYTEST_ADDOPTS=-k smoke" >> "$GITHUB_"ENV\n$1`), /is not on the allowed list/],
  ["an action exporting a variable", () => plant(GATE_STEP, "      - uses: actions/github-script@60a0d83039c74a4aee543508d2ffcb1c3799cdea\n        with: { script: \"core.exportVariable('PYTEST_ADDOPTS', '-k smoke')\" }\n$1"), /is not on the allowed list/],
  ["a python one-liner writing GITHUB_ENV", () => plant(GATE_STEP, `      - run: python3 -c "import os; open(os.environ['GITHUB_ENV'], 'a').write('PYTEST_ADDOPTS=-k smoke\\n')"\n$1`), /is not on the allowed list/],
  ["an extra input on an allowed action", () => plant(/^( {8}with: \{ enable-cache: false \})\n/m, "        with: { enable-cache: false, python-version: '3.12' }\n"), /has `with: .*not the allowed/],
]) {
  test(`RED: ${what} is named`, () => {
    const errs = gatingErrors(wf());
    assert.ok(errs.length > 0, `gatingErrors saw nothing in a gates.yml where ${what}`);
    assert.match(errs.join("\n"), rx);
  });
}

// ── the rule, on planted matrices (each row fails if the rule stops seeing its defect) ────────────

const PKGS = ["core/a", "core/b", "deploy/c"];
const entry = (group, select) => ({ group, selectors: select.split(" ") });

test("vacuity: a matrix holding every package once, one of them in shards, is clean", () => {
  assert.deepEqual(checkPythonMatrix(PKGS, [entry("a-1", "core/a@1/2"), entry("a-2", "core/a@2/2"), entry("rest", "core/b deploy/c")]), []);
});

test("RED: a package in no group is named", () => {
  const errs = checkPythonMatrix(PKGS, [entry("a", "core/a"), entry("b", "core/b")]);
  assert.equal(errs.length, 1, errs.join("\n"));
  assert.match(errs[0], /^deploy\/c runs in no matrix group/);
});

test("RED: a package in two groups, or whole and sharded at once, is named", () => {
  assert.match(checkPythonMatrix(PKGS, [entry("x", "core/a core/b"), entry("y", "core/a deploy/c")]).join("\n"), /core\/a runs whole in 2 groups/);
  assert.match(checkPythonMatrix(PKGS, [entry("x", "core/a core/b deploy/c"), entry("y", "core/a@1/1")]).join("\n"), /core\/a runs both whole/);
});

test("RED: a shard set with a gap, a repeat, or two counts is named", () => {
  assert.match(checkPythonMatrix(PKGS, [entry("a-1", "core/a@1/3"), entry("a-3", "core/a@3/3"), entry("r", "core/b deploy/c")]).join("\n"),
    /core\/a@2\/3 runs in no matrix group/);
  assert.match(checkPythonMatrix(PKGS, [entry("a-1", "core/a@1/2"), entry("a-x", "core/a@1/2"), entry("a-2", "core/a@2/2"), entry("r", "core/b deploy/c")]).join("\n"),
    /core\/a@1\/2 runs in 2 groups/);
  assert.match(checkPythonMatrix(PKGS, [entry("a-1", "core/a@1/2"), entry("a-2", "core/a@2/3"), entry("r", "core/b deploy/c")]).join("\n"),
    /shards of different counts/);
});

test("RED: a selector naming no package, or a shard past its count, is named", () => {
  assert.match(checkPythonMatrix(PKGS, [entry("x", "core/a core/b deploy/c core/gone")]).join("\n"), /`core\/gone` is not a package gate:python runs/);
  assert.match(checkPythonMatrix(PKGS, [entry("x", "core/a@3/2 core/b deploy/c")]).join("\n"), /names a shard that does not exist/);
});

test("RED: an include entry in another shape is an error, not a skipped group", () => {
  // Any YAML spelling of { group, select } reads; an entry with another key or without `select` is an
  // error, so no entry can sit in the matrix unchecked.
  const wf = ["jobs:", "  python:", "    strategy:", "      matrix:", "        include:",
    '          - { group: a, select: "core/a" }', "          - group: b", '            select: "core/b"',
    '          - { group: c, select: "core/c", skip: true }', "          - { group: d }", "    steps: []", "  gates: {}", ""].join("\n");
  const { entries, errs } = readPythonMatrix(wf);
  assert.deepEqual(entries.map((e) => e.group), ["a", "b"]);
  assert.equal(errs.length, 2, errs.join("\n"));
  for (const e of errs) assert.match(e, /unreadable matrix entry/);
});

test("RED: a workflow whose `python` job has no matrix reads as an error (main before the split)", () => {
  const wf = ["jobs:", "  python:", "    runs-on: ubuntu-latest", "    steps:", "      - run: pnpm gate:python", "  gates:", ""].join("\n");
  assert.match(readPythonMatrix(wf).errs.join("\n"), /no `strategy\.matrix\.include` list/);
});
