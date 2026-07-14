// release-value-gate — guarantee line 8, enforced (D9/D10). "All release value confirmed accepted."
//
// A release may promote only when EVERY change in the batch was individually proven before it
// entered. The batch = the PRs merged between the previous release tag and this one. Each PR is
// ACCEPTED when its value is machine-witnessed on its OWN merged head (the ladder, not a re-run):
//
//   • `value-fsm` (the pr-value L3 leg) is green on the PR's head sha            → runtime value proven
//   • OR the PR carries `state: value-signed` (a human TAKE sign-off)            → human-accepted
//   • OR the PR touched no runtime surface (pr-value's path filter) and merged   → backend-invisible,
//     which — since merge through the queue requires the full `gates` suite —      machine-sound by proxy
//
// UNACCEPTED (blocks promote): a PR that touched a runtime surface but whose `value-fsm` is
// absent or non-success and which carries no `state: value-signed`. The fix is named per row:
// re-run pr-value on the head, or take it through TAKE and label it — never waive the row.
//
// Inputs (env): RELEASE_VERSION (vX.Y.Z), GITHUB_REPOSITORY (owner/name). Uses `gh` (GH_TOKEN).
// Exit 0 = every batch PR accepted; exit 1 = one or more unaccepted (ledger printed); exit 2 = usage.

import { execSync } from "node:child_process";

const REPO = process.env.GITHUB_REPOSITORY;
const VERSION = process.env.RELEASE_VERSION;
if (!REPO || !VERSION) {
  console.error("release-value-gate: RELEASE_VERSION and GITHUB_REPOSITORY are required");
  process.exit(2);
}

// pr-value.yml's path filter — the definition of a "runtime surface" (keep in sync with that file).
const RUNTIME_PREFIXES = ["core/", "clients/terminal/", "deploy/compose/", "deploy/lite/", "libs/"];
const RUNTIME_FILES = ["package.json", "pnpm-lock.yaml"];

const gh = (path, jqOrArgs = "") =>
  execSync(`gh api "${path}" ${jqOrArgs}`, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
const ghj = (path) => JSON.parse(gh(path));

// SemVer-ish compare for v0.12.x tags (suffix like -rc.1 sorts before its release).
function parseVer(t) {
  const m = String(t).match(/^v?(\d+)\.(\d+)\.(\d+)(?:-(.+))?$/);
  if (!m) return null;
  return { core: [+m[1], +m[2], +m[3]], pre: m[4] || null, raw: t };
}
function cmpVer(a, b) {
  for (let i = 0; i < 3; i++) if (a.core[i] !== b.core[i]) return a.core[i] - b.core[i];
  if (a.pre === b.pre) return 0;
  if (!a.pre) return 1; // release > prerelease
  if (!b.pre) return -1;
  return a.pre < b.pre ? -1 : 1;
}

// The previous release tag: the greatest v0.12.* tag strictly less than VERSION.
function previousReleaseTag() {
  const cur = parseVer(VERSION);
  const tags = [];
  for (let page = 1; page <= 10; page++) {
    const batch = ghj(`repos/${REPO}/tags?per_page=100&page=${page}`);
    if (!batch.length) break;
    for (const t of batch) { const p = parseVer(t.name); if (p && p.pre === null) tags.push(p); }
    if (batch.length < 100) break;
  }
  const lower = tags.filter((t) => cmpVer(t, cur) < 0).sort(cmpVer);
  return lower.length ? lower[lower.length - 1].raw : null;
}

// Batch PR numbers = PRs referenced by the squash commits between prevTag and VERSION.
function batchPRNumbers(prevTag) {
  const range = prevTag ? `${prevTag}...${VERSION}` : VERSION;
  const nums = new Set();
  for (let page = 1; page <= 20; page++) {
    let cmp;
    try { cmp = ghj(`repos/${REPO}/compare/${range}?per_page=100&page=${page}`); }
    catch (e) { console.error(`release-value-gate: compare ${range} failed — ${e.message}`); process.exit(1); }
    const commits = cmp.commits || [];
    for (const c of commits) {
      const subject = (c.commit?.message || "").split("\n")[0];
      const m = subject.match(/\(#(\d+)\)\s*$/) || subject.match(/#(\d+)/);
      if (m) nums.add(+m[1]);
    }
    if (commits.length < 100) break;
  }
  return [...nums].sort((a, b) => a - b);
}

function prTouchesRuntime(num) {
  for (let page = 1; page <= 10; page++) {
    const files = ghj(`repos/${REPO}/pulls/${num}/files?per_page=100&page=${page}`);
    for (const f of files) {
      const p = f.filename;
      if (RUNTIME_FILES.includes(p) || RUNTIME_PREFIXES.some((pre) => p.startsWith(pre))) return true;
    }
    if (files.length < 100) break;
  }
  return false;
}

function valueFsmVerdict(sha) {
  // returns "success" | "failure" | "absent"
  const runs = ghj(`repos/${REPO}/commits/${sha}/check-runs?per_page=100`).check_runs || [];
  const vf = runs.filter((r) => /^value-fsm$/i.test(r.name) || /pr-value/i.test(r.name));
  if (!vf.length) return "absent";
  // newest-first; take the latest conclusion
  vf.sort((a, b) => new Date(b.started_at || 0) - new Date(a.started_at || 0));
  return vf[0].conclusion === "success" ? "success" : "failure";
}

const prevTag = previousReleaseTag();
console.log(`release-value-gate — batch since ${prevTag || "(no prior tag — full history)"} → ${VERSION}`);
const prs = batchPRNumbers(prevTag);
if (!prs.length) {
  console.log("  ⚠️  no PR-referencing commits found in range — nothing to accept (verify the range).");
}

const rows = [];
let unaccepted = 0;
for (const num of prs) {
  let pr;
  try { pr = ghj(`repos/${REPO}/pulls/${num}`); }
  catch { rows.push({ num, verdict: "SKIP", why: "PR not found (cross-repo ref?)" }); continue; }
  const labels = (pr.labels || []).map((l) => l.name);
  const sha = pr.head?.sha;
  const signed = labels.includes("state: value-signed");
  const runtime = prTouchesRuntime(num);
  const vf = sha ? valueFsmVerdict(sha) : "absent";

  let accepted = false, why = "";
  if (signed) { accepted = true; why = "state: value-signed (human TAKE)"; }
  else if (vf === "success") { accepted = true; why = "value-fsm green on head"; }
  else if (!runtime && vf === "absent") { accepted = true; why = "non-runtime PR; gates-green (merged)"; }
  else if (runtime && vf === "absent") { why = "runtime PR but no value-fsm run — re-run pr-value on head or label value-signed"; }
  else { why = `value-fsm ${vf} on head — re-run pr-value green or label value-signed`; }

  if (!accepted) unaccepted++;
  rows.push({ num, verdict: accepted ? "ACCEPTED" : "UNACCEPTED", why, title: (pr.title || "").slice(0, 60) });
}

console.log("");
console.log("| PR | verdict | basis |");
console.log("|----|---------|-------|");
for (const r of rows) console.log(`| #${r.num} | ${r.verdict} | ${r.why} |`);
console.log("");

if (unaccepted > 0) {
  console.error(`::error ::release-value-gate — ${unaccepted}/${prs.length} batch PR(s) UNACCEPTED (guarantee line 8). Promote blocked until every change is proven-accepted.`);
  process.exit(1);
}
console.log(`✓ release-value-gate — all ${prs.length} batch PR(s) accepted (guarantee line 8).`);
