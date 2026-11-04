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
import { jobBlock, readPythonMatrix, checkPythonMatrix, gatingErrors } from "./python-matrix.mjs";

guardTree();

const WORKFLOW = readFileSync(join(REPO, ".github", "workflows", "gates.yml"), "utf8");
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
  const job = jobBlock(WORKFLOW, "python").join("\n");
  assert.match(job, /^\s+- run: node scripts\/gates\.mjs python \$\{\{ matrix\.select \}\}\s*$/m,
    "the leg must hand its matrix selection to gate:python, or every leg runs the whole population (or nothing)");
  assert.match(job, /^\s+fail-fast: false\s*$/m, "fail-fast would cancel the other legs at the first red one, hiding their reds (F209)");
  const t = job.match(/^ {4}timeout-minutes: (\d+)\s*$/m);
  assert.ok(t, "the `python` job carries no timeout-minutes: a leg that grows would stall the queue instead of failing by name");
  assert.ok(Number(t[1]) < 30, `timeout-minutes ${t[1]} is not under the merge queue's 30-minute window`);
});

test("the required check is still `gates`: one job by that id, no display name, joining python with static and node", () => {
  const job = jobBlock(WORKFLOW, "gates");
  assert.ok(job, "no `gates` job: branch protection and the merge queue require a `gates` check");
  const text = job.join("\n");
  assert.doesNotMatch(text, /^ {4}name:/m, "a job-level name would rename the check context branch protection requires");
  const needs = text.match(/^ {4}needs: \[([^\]]*)\]\s*$/m);
  assert.ok(needs, "the `gates` job has no one-line `needs: [...]`");
  const legs = needs[1].split(",").map((s) => s.trim());
  for (const leg of ["static", "python", "node"]) assert.ok(legs.includes(leg), `\`gates\` no longer needs ${leg}`);
  assert.match(text, /^ {4}if: always\(\)\s*$/m, "without if: always() a failed leg SKIPS `gates`, and a skipped required check reads as satisfied");
  assert.match(text, /needs\.python\.result/, "`gates` does not read the python legs' result");
});

test("the docker fixture image is pulled in the one leg that runs core/runtime, and only there", () => {
  const job = jobBlock(WORKFLOW, "python");
  const at = job.findIndex((l) => /^\s+- run: docker pull alpine\s*$/.test(l));
  assert.ok(at >= 0, "no `docker pull alpine` step: core/runtime's docker-backend test needs the image pre-pulled");
  const rest = job.slice(at + 1);
  const step = rest.slice(0, Math.max(0, rest.findIndex((l) => /^ {6}- /.test(l))));
  const cond = step.find((l) => /^\s+if:/.test(l)) || "";
  const group = (cond.match(/^\s+if: matrix\.group == '([a-z0-9-]+)'\s*$/) || [])[1];
  assert.ok(group, `the docker pull step should run in one named leg (if: matrix.group == '<group>'), found: ${cond.trim() || "no condition"}`);
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
  ["the gate step written with its condition first", () => plant(GATE_STEP, "      - if: matrix.group != 'services'\n        run: node scripts/gates.mjs python ${{ matrix.select }}\n"), /no gate step/],
  ["a second matrix axis beside include", () => plant(/^( {6}matrix:\n)/m, "$1        skip: []\n"), /matrix holds `skip:/],
  ["continue-on-error on the gates aggregate", () => plant(/^( {2}gates:\n)/m, "$1    continue-on-error: true\n"), /`gates` job carries `continue-on-error:/],
  ["a step-level `if:` in the gates aggregate", () => plant(/^( {6}- name: All legs green\?\n)/m, "$1        if: needs.python.result == 'success'\n"), /`gates` job carries `if: needs/],
  ["the aggregate accepting anything but failure", () => plant('[ "$R_PYTHON" = "success" ]', '[ "$R_PYTHON" != "failure" ]'), /does not require needs\.python\.result == success/],
  ["the aggregate not reading the python result", () => plant(/^ +R_PYTHON: \$\{\{ needs\.python\.result \}\}\n/m, ""), /R_PYTHON: \$\{\{ needs\.python\.result \}\}/],
  ["the aggregate swallowing its own failure", () => plant('or was cancelled"; exit 1; }', 'or was cancelled"; exit 1; } || true'), /`\|\| true`/],
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
  const wf = ["jobs:", "  python:", "    strategy:", "      matrix:", "        include:",
    '          - { group: a, select: "core/a" }', "          - group: b", '            select: "core/b"', "    steps: []", "  gates:", ""].join("\n");
  const { entries, errs } = readPythonMatrix(wf);
  assert.deepEqual(entries.map((e) => e.group), ["a"]);
  assert.equal(errs.length, 2, errs.join("\n"));
  assert.match(errs[0], /unreadable matrix entry/);
});

test("RED: a workflow whose `python` job has no matrix reads as an error (main before the split)", () => {
  const wf = ["jobs:", "  python:", "    runs-on: ubuntu-latest", "    steps:", "      - run: pnpm gate:python", "  gates:", ""].join("\n");
  assert.match(readPythonMatrix(wf).errs.join("\n"), /no `strategy\.matrix\.include` list/);
});
