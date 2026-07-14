// release-value-gate — guarantee line 8, enforced (D9/D10). "All release value confirmed accepted."
//
// A release may promote only when EVERY change in the batch was individually proven before it
// entered. The batch = the PRs merged between the previous release tag and this one. Each PR is
// ACCEPTED when its value is machine-witnessed on its OWN merged head (the ladder, not a re-run):
//
//   • runtime PR  → `value-fsm` (the pr-value L3 leg) is GREEN on the PR's head sha
//   • non-runtime → merged through the full `gates` suite (backend-invisible, machine-sound), or
//                   carries `state: value-signed` (a human TAKE sign-off)
//
// A RED value-fsm is never rescued by a label — the label signs the rows ABOVE the automation
// line, it does not waive the machine leg. A change we cannot positively verify fails CLOSED.
//
// Exit codes (so the published-guard can tell "unwitnessed" from "couldn't evaluate"):
//   0 — every batch change accepted
//   1 — at least one change is DEFINITIVELY unaccepted (retract-worthy)
//   3 — could not evaluate (transient API error, unresolvable ref, or a commit not mapped to a
//       gated PR) — blocks promote, but is NOT a "retract the release" signal
//   2 — usage
//
// Inputs (env): RELEASE_VERSION (vX.Y.Z), GITHUB_REPOSITORY (owner/name). Uses `gh` (GH_TOKEN).

import { execSync } from "node:child_process";

const REPO = process.env.GITHUB_REPOSITORY;
const VERSION = process.env.RELEASE_VERSION;
if (!REPO || !VERSION) { console.error("release-value-gate: RELEASE_VERSION and GITHUB_REPOSITORY are required"); process.exit(2); }

// pr-value.yml's path filter — the definition of a "runtime surface" (keep in sync with that file).
const RUNTIME_PREFIXES = ["core/", "clients/terminal/", "deploy/compose/", "deploy/lite/", "libs/"];
const RUNTIME_FILES = ["package.json", "pnpm-lock.yaml"];

// Retry gh reads a few times so a transient 5xx/secondary-limit doesn't masquerade as a verdict.
function ghRaw(path) {
  let last;
  for (let i = 0; i < 3; i++) {
    try { return execSync(`gh api "${path}"`, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }); }
    catch (e) { last = e; }
  }
  throw last;
}
const ghj = (path) => JSON.parse(ghRaw(path));

function parseVer(t) {
  const m = String(t).match(/^v?(\d+)\.(\d+)\.(\d+)(?:-(.+))?$/);
  return m ? { core: [+m[1], +m[2], +m[3]], pre: m[4] || null, raw: t } : null;
}
function cmpVer(a, b) {
  for (let i = 0; i < 3; i++) if (a.core[i] !== b.core[i]) return a.core[i] - b.core[i];
  if (a.pre === b.pre) return 0;
  if (!a.pre) return 1; if (!b.pre) return -1;
  return a.pre < b.pre ? -1 : 1;
}

function previousReleaseTag() {
  const cur = parseVer(VERSION);
  if (!cur) throw new Error(`RELEASE_VERSION "${VERSION}" is not vX.Y.Z`);
  const tags = [];
  for (let page = 1; page <= 10; page++) {
    const batch = ghj(`repos/${REPO}/tags?per_page=100&page=${page}`);
    for (const t of batch) { const p = parseVer(t.name); if (p && p.pre === null) tags.push(p); }
    if (batch.length < 100) break;
  }
  const lower = tags.filter((t) => cmpVer(t, cur) < 0).sort(cmpVer);
  return lower.length ? lower[lower.length - 1].raw : null;
}

// Enumerate the range. Returns { prs:Set<number>, unaccounted:[subject], commitCount }.
// A commit is mapped to a PR ONLY by the strict trailing `(#N)` squash form — the loose "first
// #N in the subject" heuristic is dropped (it can grab an issue ref). A commit with no trailing
// (#N) is "unaccounted": we cannot tie it to a gated PR, so it fails closed (exit 3).
function enumerate(prevTag) {
  const range = `${prevTag}...${VERSION}`;
  const prs = new Set(); const unaccounted = []; let commitCount = 0;
  for (let page = 1; page <= 30; page++) {
    const cmp = ghj(`repos/${REPO}/compare/${range}?per_page=100&page=${page}`);
    const commits = cmp.commits || [];
    for (const c of commits) {
      commitCount++;
      const subject = (c.commit?.message || "").split("\n")[0];
      const m = subject.match(/\(#(\d+)\)\s*$/);
      if (m) prs.add(+m[1]);
      else if ((c.parents || []).length < 2) unaccounted.push(`${(c.sha || "").slice(0, 8)} ${subject.slice(0, 70)}`);
      // 2-parent merge commits in a squash-only repo are rare; ignore them, don't fail on them.
    }
    if (commits.length < 100) break;
  }
  return { prs: [...prs].sort((a, b) => a - b), unaccounted, commitCount };
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

// "success" | "failure" | "absent" — exact value-fsm name only; latest run wins (a re-run to
// green legitimately supersedes), so an auxiliary check whose name merely contains "value" cannot
// mask a failed value-fsm.
function valueFsmVerdict(sha) {
  const runs = (ghj(`repos/${REPO}/commits/${sha}/check-runs?per_page=100`).check_runs || [])
    .filter((r) => r.name === "value-fsm");
  if (!runs.length) return "absent";
  runs.sort((a, b) => new Date(b.started_at || 0) - new Date(a.started_at || 0));
  return runs[0].conclusion === "success" ? "success" : "failure";
}

// ── run ───────────────────────────────────────────────────────────────────────────────────────
let prevTag;
try { prevTag = previousReleaseTag(); }
catch (e) { console.error(`::error ::release-value-gate — could not resolve tags: ${e.message}`); process.exit(3); }
if (!prevTag) { console.error(`::error ::release-value-gate — no prior release tag < ${VERSION}; cannot bound the batch. Resolve manually.`); process.exit(3); }

let batch;
try { batch = enumerate(prevTag); }
catch (e) { console.error(`::error ::release-value-gate — compare ${prevTag}...${VERSION} failed: ${e.message}`); process.exit(3); }

console.log(`release-value-gate — batch ${prevTag} → ${VERSION}: ${batch.commitCount} commit(s), ${batch.prs.length} PR(s)`);

if (batch.commitCount === 0) {
  console.error(`::error ::release-value-gate — empty range ${prevTag}...${VERSION}: nothing to release (or a bad tag range). Fails closed.`);
  process.exit(1);
}

const rows = [];
let definitelyUnaccepted = 0;
let couldNotEvaluate = batch.unaccounted.length; // commits not mappable to a gated PR

for (const num of batch.prs) {
  let pr;
  try { pr = ghj(`repos/${REPO}/pulls/${num}`); }
  catch { rows.push({ num, verdict: "UNVERIFIABLE", why: "PR fetch failed after retries" }); couldNotEvaluate++; continue; }
  const labels = (pr.labels || []).map((l) => l.name);
  const signed = labels.includes("state: value-signed");
  const sha = pr.head?.sha;
  let runtime, vf;
  try { runtime = prTouchesRuntime(num); vf = sha ? valueFsmVerdict(sha) : "absent"; }
  catch { rows.push({ num, verdict: "UNVERIFIABLE", why: "check-runs/files fetch failed after retries" }); couldNotEvaluate++; continue; }

  let verdict, why;
  if (vf === "success") { verdict = "ACCEPTED"; why = "value-fsm green on head"; }
  else if (vf === "failure") { verdict = "UNACCEPTED"; why = "value-fsm RED on head — a label cannot waive it; re-run pr-value green"; }
  else if (!runtime && signed) { verdict = "ACCEPTED"; why = "non-runtime + state: value-signed"; }
  else if (!runtime) { verdict = "ACCEPTED"; why = "non-runtime PR; gates-green (merged)"; }
  else { verdict = "UNACCEPTED"; why = "runtime PR with no value-fsm run — re-run pr-value on head or (if non-runtime) reclassify"; }

  if (verdict === "UNACCEPTED") definitelyUnaccepted++;
  rows.push({ num, verdict, why });
}

console.log("\n| PR | verdict | basis |\n|----|---------|-------|");
for (const r of rows) console.log(`| #${r.num} | ${r.verdict} | ${r.why} |`);
if (batch.unaccounted.length) {
  console.log("\nUnaccounted commits (not tied to a gated PR — fail closed):");
  for (const u of batch.unaccounted) console.log(`  · ${u}`);
}
console.log("");

if (definitelyUnaccepted > 0) {
  console.error(`::error ::release-value-gate — ${definitelyUnaccepted} batch change(s) DEFINITIVELY unaccepted (guarantee line 8). Promote blocked.`);
  process.exit(1);
}
if (couldNotEvaluate > 0) {
  console.error(`::error ::release-value-gate — ${couldNotEvaluate} change(s) could not be verified (unaccounted commits / API errors). Promote blocked; resolve then re-run.`);
  process.exit(3);
}
console.log(`✓ release-value-gate — all ${batch.prs.length} batch PR(s) accepted (guarantee line 8).`);
