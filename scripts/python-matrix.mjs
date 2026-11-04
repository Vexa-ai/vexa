/**
 * python-matrix.mjs — the `python` matrix in .github/workflows/gates.yml, read and held to the
 * population gate:python runs (`node scripts/gates.mjs python-packages`).
 *
 * The matrix exists for wall-clock only: run in series the population took 29-46 min in one job, past
 * the merge queue's 30-minute check window. A matrix is a second list of the packages, and a second
 * list drifts: a package added to the tree and not to the matrix would simply stop being tested in CI,
 * green, while `node scripts/gates.mjs python` still runs it locally. checkPythonMatrix is the rule
 * that makes that drift a red build instead (scripts/python-matrix.test.mjs).
 *
 * The matrix is written one entry per line, `- { group: <name>, select: "<selector> ..." }`, and the
 * reader accepts nothing else under `include:`: an entry in any other shape is an error, never skipped,
 * so reformatting the YAML cannot hide a group from the check.
 */

// The lines of one top-level job (`  <id>:` up to the next `  <id>:`) in a workflow's text.
export function jobBlock(text, id) {
  const lines = text.split("\n");
  const start = lines.findIndex((l) => l === `  ${id}:`);
  if (start < 0) return null;
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) if (/^ {2}[A-Za-z0-9_-]+:\s*$/.test(lines[i])) { end = i; break; }
  return lines.slice(start, end);
}

const ENTRY = /^\s+- \{ group: ([a-z0-9][a-z0-9-]*), select: "([^"]+)" \}\s*(?:#.*)?$/;

// { entries: [{ group, selectors: [...] }], errs: [...] } for the `python` job's matrix.
export function readPythonMatrix(text) {
  const block = jobBlock(text, "python");
  if (!block) return { entries: [], errs: ["gates.yml has no `python` job"] };
  const at = block.findIndex((l) => /^\s+include:\s*$/.test(l));
  if (at < 0) return { entries: [], errs: ["the `python` job has no `strategy.matrix.include` list"] };
  const indent = block[at].match(/^\s*/)[0].length;
  const entries = [];
  const errs = [];
  for (const line of block.slice(at + 1)) {
    if (!line.trim() || /^\s*#/.test(line)) continue;
    if (line.match(/^\s*/)[0].length <= indent) break;
    const m = line.match(ENTRY);
    if (m) entries.push({ group: m[1], selectors: m[2].trim().split(/\s+/) });
    else errs.push(`unreadable matrix entry (write it as \`- { group: <name>, select: "<dir> <dir>@<k>/<n> ..." }\`): ${line.trim()}`);
  }
  if (!entries.length && !errs.length) errs.push("the `python` job's matrix lists no groups");
  return { entries, errs };
}

// The path from a red python leg to a red required check, with nothing on it that can turn red into
// green: no `if:` or `continue-on-error` on the gate step or the python job, no `continue-on-error`
// anywhere in that job, nothing in its matrix but `include:` (another axis multiplies or empties the
// legs), and a `gates` aggregate that reads needs.python.result, fails unless it is exactly
// `success`, and carries no condition but its own `if: always()`. Returns the list of errors.
const GATE_STEP = /^ {6}- run: node scripts\/gates\.mjs python \$\{\{ matrix\.select \}\}\s*$/;
const indentOf = (l) => l.match(/^\s*/)[0].length;
export function gatingErrors(text) {
  const errs = [];
  const py = jobBlock(text, "python");
  if (!py) return ["gates.yml has no `python` job"];
  for (const l of py) {
    if (/^ {4}(if|continue-on-error):/.test(l)) errs.push(`the \`python\` job carries \`${l.trim()}\`: a leg it skips or excuses no longer gates`);
    else if (/^\s+continue-on-error:/.test(l)) errs.push(`a step of the \`python\` job carries \`${l.trim()}\`: a red step there reads as green`);
  }
  const at = py.findIndex((l) => GATE_STEP.test(l));
  if (at < 0) errs.push("the `python` job has no gate step written as `- run: node scripts/gates.mjs python ${{ matrix.select }}`");
  else {
    for (const l of py.slice(at + 1)) {
      if (!l.trim()) continue;
      if (indentOf(l) <= 6) break;
      if (/^\s+(if|continue-on-error):/.test(l)) errs.push(`the gate step carries \`${l.trim()}\`: a leg where it is skipped or excused passes without gating`);
    }
  }
  const m = py.findIndex((l) => /^ {6}matrix:\s*$/.test(l));
  if (m >= 0) {
    for (const l of py.slice(m + 1)) {
      if (!l.trim() || /^\s*#/.test(l)) continue;
      if (indentOf(l) <= 6) break;
      if (indentOf(l) === 8 && !/^ {8}include:\s*$/.test(l)) errs.push(`the \`python\` matrix holds \`${l.trim()}\` beside \`include:\`: another axis multiplies or empties the legs`);
    }
  }
  const agg = jobBlock(text, "gates");
  if (!agg) return [...errs, "gates.yml has no `gates` job"];
  for (const l of agg) {
    if (/^\s+continue-on-error:/.test(l)) errs.push(`the \`gates\` job carries \`${l.trim()}\`: the required check would pass over a red leg`);
    else if (/^\s+if:/.test(l) && !/^ {4}if: always\(\)\s*$/.test(l)) errs.push(`the \`gates\` job carries \`${l.trim()}\`: its only condition may be the job's own \`if: always()\``);
  }
  const body = agg.join("\n");
  if (!/^ +R_PYTHON: \$\{\{ needs\.python\.result \}\}\s*$/m.test(body)) errs.push("the `gates` job does not set `R_PYTHON: ${{ needs.python.result }}`");
  if (!body.includes('[ "$R_PYTHON" = "success" ]')) errs.push("the `gates` job does not require needs.python.result == success (`[ \"$R_PYTHON\" = \"success\" ]`)");
  if (!/exit 1\b/.test(body)) errs.push("the `gates` job never exits 1");
  if (/\|\|\s*true\b|exit 0\b/.test(body)) errs.push("the `gates` job carries `|| true` or `exit 0`, which can swallow a red leg");
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
