/**
 * python-matrix.mjs — the `python` matrix in .github/workflows/gates.yml, read and held to the
 * population gate:python runs (`node scripts/gates.mjs python-packages`), and the path from a python
 * leg to the required `gates` check held to what it must be.
 *
 * The matrix exists for wall-clock only: run in series the population took 29-46 min in one job, past
 * the merge queue's 30-minute check window. A matrix is a second list of the packages, and a second
 * list drifts: a package added to the tree and not to the matrix would simply stop being tested in CI,
 * green, while `node scripts/gates.mjs python` still runs it locally. checkPythonMatrix is the rule
 * that makes that drift a red build instead (scripts/python-matrix.test.mjs).
 *
 * The workflow is PARSED (the `yaml` package), never matched line by line. A text rule reads one
 * spelling of a key, and YAML has many: `"if":`, `'if':`, `if :`, a flow map, an anchor. GitHub reads
 * every one of them as the key. Every rule below is an allow-list over the parsed object: a key, a step
 * or a value that is not listed is refused, whatever it is spelled like.
 */
import { parse } from "yaml";

// The parsed workflow, or the reason it does not parse (duplicate keys included).
export function parseWorkflow(text) {
  try { return { wf: parse(text, { uniqueKeys: true }), errs: [] }; }
  catch (e) { return { wf: null, errs: [`gates.yml does not parse: ${String(e.message).split("\n")[0]}`] }; }
}
const isMap = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const keysOf = (v) => (isMap(v) ? Object.keys(v) : []);

// { entries: [{ group, selectors: [...] }], errs: [...] } for the `python` job's matrix. Each entry is a
// map of exactly `group` (a slug) and `select` (a space-separated selector list); any other entry is an
// error, never skipped, so no shape of the YAML can hide a group from the check.
export function readPythonMatrix(text) {
  const { wf, errs } = parseWorkflow(text);
  if (!wf) return { entries: [], errs };
  const job = wf.jobs?.python;
  if (!isMap(job)) return { entries: [], errs: ["gates.yml has no `python` job"] };
  const include = job.strategy?.matrix?.include;
  if (!Array.isArray(include)) return { entries: [], errs: ["the `python` job has no `strategy.matrix.include` list"] };
  const entries = [];
  for (const e of include) {
    const ok = isMap(e) && keysOf(e).sort().join(",") === "group,select" &&
      typeof e.group === "string" && /^[a-z0-9][a-z0-9-]*$/.test(e.group) && typeof e.select === "string" && e.select.trim();
    if (ok) entries.push({ group: e.group, selectors: e.select.trim().split(/\s+/) });
    else errs.push(`unreadable matrix entry (write it as \`- { group: <name>, select: "<dir> <dir>@<k>/<n> ..." }\`): ${JSON.stringify(e)}`);
  }
  if (!entries.length && !errs.length) errs.push("the `python` job's matrix lists no groups");
  return { entries, errs };
}

// ── the path from a red python leg to a red required check ─────────────────────────────────────────
// Nothing on it may turn red into green, or make a leg run nothing and pass:
//   - the workflow's top-level keys are WORKFLOW_KEYS (no `env:` or `defaults:`, which reach every step);
//   - the python job's keys are PY_JOB_KEYS: no `if`, `continue-on-error`, `env`, `defaults`,
//     `container`, `services` or anything else;
//   - its strategy is `fail-fast: false` and a matrix of `include:` only;
//   - its steps are PY_STEPS, in order, each with exactly the listed keys. A step is matched by its
//     action (pinned to a commit) or by its exact `run`. Any other step could set what the gate step
//     runs ($GITHUB_ENV in any spelling, an action that exports a variable, a one-liner), so none is
//     allowed. The last of them is the gate step, which is `run:` and nothing else;
//   - the `gates` job is AGG_JOB exactly: no display name, `needs` the three legs, `if: always()`, one
//     step whose env is the three results and whose script is AGG_RUN.
// AGG_RUN is compared as TEXT, line by line, on purpose. It is the one shell script the required check
// rests on, and an equivalent spelling (`[[ … ]]`, `test`, a comment, an extra line) is refused rather
// than proved equivalent; the error names the line it expected. The env beside it is compared as a
// map, so its order does not matter.
// gate:python adds the runtime half: a selected run that passes no test is red (scripts/gates.mjs).
const WORKFLOW_KEYS = ["name", "run-name", "on", "permissions", "concurrency", "jobs"];
const PY_JOB_KEYS = ["name", "runs-on", "timeout-minutes", "strategy", "steps"];
// An action, pinned to a full commit sha (any sha: bumping the pin is not a gating change).
const pinned = (action) => ({ pinned: action });
const isPinned = (want) => isMap(want) && typeof want.pinned === "string";
const pinMatches = (v, want) => typeof v === "string" && v.startsWith(`${want.pinned}@`) && /^[0-9a-f]{40}$/.test(v.slice(want.pinned.length + 1));
const GATE_RUN = "node scripts/gates.mjs python ${{ matrix.select }}";
const PY_STEPS = [
  { uses: pinned("actions/checkout") },
  { uses: pinned("pnpm/action-setup") },
  { uses: pinned("actions/setup-node"), with: { "node-version": 22, cache: "pnpm" } },
  { uses: pinned("astral-sh/setup-uv"), with: { "enable-cache": false } },
  { run: "pnpm install --frozen-lockfile", env: { ONNXRUNTIME_NODE_INSTALL: "skip" } },
  { run: "docker pull alpine", if: "matrix.group == 'platform'" },
  { run: GATE_RUN },
];
const AGG_ENV = { R_STATIC: "${{ needs.static.result }}", R_PYTHON: "${{ needs.python.result }}", R_NODE: "${{ needs.node.result }}" };
const AGG_RUN = [
  'echo "static=$R_STATIC python=$R_PYTHON node=$R_NODE"',
  '[ "$R_STATIC" = "success" ] && [ "$R_PYTHON" = "success" ] && [ "$R_NODE" = "success" ] \\',
  '  || { echo "::error ::a gates leg failed or was cancelled"; exit 1; }',
  'echo "✓ all gate legs green"',
];

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const sameMap = (a, b) => isMap(a) && isMap(b) && same(Object.keys(a).sort(), Object.keys(b).sort()) && Object.keys(b).every((k) => same(a[k], b[k]));
const shown = (v) => (typeof v === "string" ? v : JSON.stringify(v));
const describe = (s) => Object.entries(s).map(([k, v]) => `${k}: ${isPinned(v) ? `${v.pinned}@<commit sha>` : shown(v)}`).join(", ");
// Errors for one step against its allowed shape: an extra key, a missing key, or a different value.
function stepErrors(step, want, where) {
  if (!isMap(step)) return [`${where} is not a step map: ${shown(step)}`];
  const errs = [];
  for (const k of keysOf(step)) if (!(k in want)) errs.push(`${where} carries \`${k}: ${shown(step[k])}\`, which its allowed shape (${describe(want)}) does not have`);
  for (const [k, v] of Object.entries(want)) {
    if (!(k in step)) errs.push(`${where} lacks \`${k}\` (allowed shape: ${describe(want)})`);
    else if (isPinned(v) ? !pinMatches(step[k], v) : (isMap(v) ? !sameMap(step[k], v) : step[k] !== v))
      errs.push(`${where} has \`${k}: ${shown(step[k])}\`, not the allowed \`${describe({ [k]: v })}\``);
  }
  return errs;
}

export function gatingErrors(text) {
  const { wf, errs } = parseWorkflow(text);
  if (!wf) return errs;
  for (const k of keysOf(wf)) if (!WORKFLOW_KEYS.includes(k)) errs.push(`the workflow carries a workflow-level \`${k}:\`: it reaches every gate step`);
  const py = wf.jobs?.python;
  if (!isMap(py)) return [...errs, "gates.yml has no `python` job"];
  for (const k of keysOf(py)) if (!PY_JOB_KEYS.includes(k)) errs.push(`the \`python\` job carries \`${k}:\`, which is not one of ${PY_JOB_KEYS.join(", ")}: it can skip, excuse or change what every leg runs`);
  if (py.name !== "python (${{ matrix.group }})") errs.push(`the \`python\` job is named \`${shown(py.name)}\`, not \`python (\${{ matrix.group }})\``);
  if (py["runs-on"] !== "ubuntu-latest") errs.push(`the \`python\` job runs on \`${shown(py["runs-on"])}\`, not ubuntu-latest`);
  if (!Number.isInteger(py["timeout-minutes"]) || py["timeout-minutes"] < 1 || py["timeout-minutes"] >= 30)
    errs.push(`the \`python\` job's timeout-minutes is \`${shown(py["timeout-minutes"])}\`: it must be a whole number under the merge queue's 30 minutes`);
  const st = py.strategy;
  for (const k of keysOf(st)) if (!["fail-fast", "matrix"].includes(k)) errs.push(`the \`python\` strategy holds \`${k}:\` beside fail-fast and matrix`);
  if (!isMap(st) || st["fail-fast"] !== false) errs.push("the `python` strategy is not `fail-fast: false`: the first red leg would cancel the others and hide their reds (F209)");
  for (const k of keysOf(st?.matrix)) if (k !== "include") errs.push(`the \`python\` matrix holds \`${k}:\` beside \`include:\`: another axis multiplies or empties the legs`);
  // Each step claims an allowed shape by its pinned action or its exact `run`; a step that claims none
  // is refused outright, and the claimed shapes must be the allowed list in order.
  const steps = Array.isArray(py.steps) ? py.steps : [];
  const GATE = PY_STEPS.length - 1;
  const claim = (s) => PY_STEPS.findIndex((w) => isMap(s) && ((isPinned(w.uses) && pinMatches(s.uses, w.uses)) || (w.run !== undefined && s.run === w.run)));
  steps.forEach((s, i) => {
    const c = claim(s);
    if (c < 0) errs.push(`step ${i + 1} of the \`python\` job (${shown(s)}) is not on the allowed list: a step there can run anything, and anything that writes $GITHUB_ENV reaches the gate step`);
    else if (c === GATE) { for (const k of keysOf(s)) if (k !== "run") errs.push(`the gate step carries \`${k}:\`: it can skip, excuse or change the gate and still pass`); }
    else errs.push(...stepErrors(s, PY_STEPS[c], `step ${i + 1} of the \`python\` job`));
  });
  if (!steps.some((s) => claim(s) === GATE)) errs.push(`the \`python\` job has no gate step written as \`run: ${GATE_RUN}\``);
  if (!same(steps.map(claim), PY_STEPS.map((_, i) => i))) errs.push(`the \`python\` job's steps are not the allowed list, in order: ${PY_STEPS.map((s) => describe(s)).join(" | ")}`);
  const agg = wf.jobs?.gates;
  if (!isMap(agg)) return [...errs, "gates.yml has no `gates` job"];
  for (const k of keysOf(agg)) if (!["needs", "if", "runs-on", "steps"].includes(k)) errs.push(`the \`gates\` job carries \`${k}: ${shown(agg[k])}\`: the required check is this job by its id, with nothing that renames, excuses or reshapes it`);
  if (!Array.isArray(agg.needs) || !same([...agg.needs].sort(), ["node", "python", "static"])) errs.push(`the \`gates\` job needs ${shown(agg.needs)}, not [static, python, node]`);
  if (agg.if !== "always()") errs.push(`the \`gates\` job carries \`if: ${shown(agg.if)}\`: its only condition is \`if: always()\`, or a failed leg skips it and a skipped required check reads as satisfied`);
  if (agg["runs-on"] !== "ubuntu-latest") errs.push(`the \`gates\` job runs on \`${shown(agg["runs-on"])}\`, not ubuntu-latest`);
  const aggSteps = Array.isArray(agg.steps) ? agg.steps : [];
  if (aggSteps.length !== 1) errs.push(`the \`gates\` job has ${aggSteps.length} steps; it is the one step \`All legs green?\``);
  const step = aggSteps[0];
  for (const k of keysOf(step)) if (!["name", "env", "run"].includes(k)) errs.push(`the \`gates\` job carries \`${k}: ${shown(step[k])}\` on its step: the step is name, env and run only`);
  if (step?.name !== "All legs green?") errs.push(`the \`gates\` step is named \`${shown(step?.name)}\`, not \`All legs green?\``);
  if (!sameMap(step?.env, AGG_ENV)) errs.push(`the \`gates\` step's env is not exactly ${Object.entries(AGG_ENV).map(([k, v]) => `${k}: ${v}`).join(", ")}: it is ${shown(step?.env ?? "absent")}`);
  const run = typeof step?.run === "string" ? step.run.replace(/\n$/, "").split("\n") : [];
  const at = AGG_RUN.findIndex((line, k) => run[k] !== line);
  if (at >= 0 || run.length !== AGG_RUN.length) {
    const k = at >= 0 ? at : AGG_RUN.length;
    errs.push(`the \`gates\` script does not require needs.python.result == success, needs.static.result == success and needs.node.result == success, exactly: line ${k + 1} is \`${run[k] ?? "(missing)"}\`, expected \`${AGG_RUN[k] ?? "(nothing more)"}\``);
  }
  if (/\|\|\s*true\b|exit 0\b/.test(run.join("\n"))) errs.push("the `gates` script carries `|| true` or `exit 0`, which can swallow a red leg");
  return errs;
}

// Every package in `packages` runs in exactly one place: whole in one group, or as shards 1..n of one
// n, each shard in exactly one group. Every selector names a package. Returns the list of errors.
export function checkPythonMatrix(packages, entries) {
  const errs = [];
  const known = new Set(packages);
  const groups = new Map();
  const whole = new Map();   // dir → [group]
  const shards = new Map();  // dir → [{ k, n, group }]
  for (const { group, selectors } of entries) {
    groups.set(group, (groups.get(group) || 0) + 1);
    for (const s of selectors) {
      const m = s.match(/^([^@]+?)\/?(?:@([1-9]\d*)\/([1-9]\d*))?$/);
      if (!m || !known.has(m[1])) { errs.push(`group ${group}: \`${s}\` is not a package gate:python runs`); continue; }
      if (m[2]) {
        const k = Number(m[2]), n = Number(m[3]);
        if (k > n) { errs.push(`group ${group}: \`${s}\` names a shard that does not exist`); continue; }
        shards.set(m[1], [...(shards.get(m[1]) || []), { k, n, group }]);
      } else whole.set(m[1], [...(whole.get(m[1]) || []), group]);
    }
  }
  for (const [g, c] of groups) if (c > 1) errs.push(`group ${g} is listed ${c} times`);
  for (const dir of packages) {
    const w = whole.get(dir) || [];
    const sh = shards.get(dir) || [];
    if (!w.length && !sh.length) { errs.push(`${dir} runs in no matrix group: CI would not test it`); continue; }
    if (w.length > 1) errs.push(`${dir} runs whole in ${w.length} groups (${w.join(", ")})`);
    if (w.length && sh.length) errs.push(`${dir} runs both whole (${w.join(", ")}) and as shards (${sh.map((x) => x.group).join(", ")})`);
    if (!sh.length) continue;
    const ns = [...new Set(sh.map((x) => x.n))];
    if (ns.length > 1) { errs.push(`${dir} is split into shards of different counts (${ns.map((n) => `/${n}`).join(", ")})`); continue; }
    const n = ns[0];
    for (let k = 1; k <= n; k++) {
      const at = sh.filter((x) => x.k === k);
      if (!at.length) errs.push(`${dir}@${k}/${n} runs in no matrix group: CI would not test that shard`);
      else if (at.length > 1) errs.push(`${dir}@${k}/${n} runs in ${at.length} groups (${at.map((x) => x.group).join(", ")})`);
    }
  }
  return errs;
}
