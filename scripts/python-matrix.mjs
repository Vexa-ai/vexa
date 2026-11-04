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
