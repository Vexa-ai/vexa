// merge-card-gate — choke point 1 (the merge card), enforced. A PR carries two artifacts judged
// on different axes; MAIN accepts it only when BOTH are accepted (delivery constitution, merge bar):
//
//   • VALUE accepted  — the observation bundle is real. Runtime PRs: `value-fsm` (pr-value L3)
//     GREEN on the head sha AND `state: value-signed` (the D9 human sign-off). Non-runtime PRs
//     (no pr-value leg): `state: value-signed` alone.
//   • DIFF accepted   — the code was reviewed. A GitHub review APPROVAL from a NON-AUTHOR whose
//     commit_id == the PR head sha (a new push dismisses a stale approval — re-review required).
//
// This is a required status check on `main` (added to branch protection alongside `gates`). It
// runs on pull_request + pull_request_review (PR-entry) and on merge_group (the queue re-check,
// where the PR number is parsed from the queue ref). A red merge-card blocks the merge with a
// plain-language card of exactly what's missing.
//
// Inputs (env): GITHUB_REPOSITORY; and PR_NUMBERS (space-separated) OR MERGE_GROUP_REF to parse.
// Exit 0 = every named PR's card is satisfied; 1 = one or more not; 2 = usage/nothing to check.

import { execSync } from "node:child_process";

const REPO = process.env.GITHUB_REPOSITORY;
if (!REPO) { console.error("merge-card-gate: GITHUB_REPOSITORY required"); process.exit(2); }

const RUNTIME_PREFIXES = ["core/", "clients/terminal/", "deploy/compose/", "deploy/lite/", "libs/"];
const RUNTIME_FILES = ["package.json", "pnpm-lock.yaml"];

function ghRaw(path) {
  let last;
  for (let i = 0; i < 3; i++) {
    try { return execSync(`gh api "${path}"`, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }); }
    catch (e) { last = e; }
  }
  throw last;
}
const ghj = (path) => JSON.parse(ghRaw(path));

// Resolve the PR number(s) to check.
function prNumbers() {
  const explicit = (process.env.PR_NUMBERS || "").trim();
  if (explicit) return [...new Set(explicit.split(/\s+/).map(Number).filter(Boolean))];
  const ref = process.env.MERGE_GROUP_REF || "";
  // gh-readonly-queue/main/pr-620-<sha>  (a merge group can stack several)
  return [...new Set([...ref.matchAll(/pr-(\d+)-/g)].map((m) => +m[1]))];
}

function touchesRuntime(num) {
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
  const runs = (ghj(`repos/${REPO}/commits/${sha}/check-runs?per_page=100`).check_runs || [])
    .filter((r) => r.name === "value-fsm");
  if (!runs.length) return "absent";
  runs.sort((a, b) => new Date(b.started_at || 0) - new Date(a.started_at || 0));
  return runs[0].conclusion === "success" ? "success" : "failure";
}

// A fresh, non-author APPROVED review: the reviewer's latest review is APPROVED and was submitted
// against the current head sha (a later push moves the head and invalidates the approval).
function diffAccepted(pr) {
  const author = pr.user?.login;
  const head = pr.head?.sha;
  const reviews = ghj(`repos/${REPO}/pulls/${pr.number}/reviews?per_page=100`);
  const latestByUser = new Map();
  for (const r of reviews) {
    if (!["APPROVED", "CHANGES_REQUESTED", "DISMISSED"].includes(r.state)) continue; // ignore COMMENTED
    latestByUser.set(r.user?.login, r);
  }
  for (const [login, r] of latestByUser) {
    if (login && login !== author && r.state === "APPROVED" && r.commit_id === head) return { ok: true, by: login };
  }
  return { ok: false };
}

function card(num) {
  const pr = ghj(`repos/${REPO}/pulls/${num}`);
  if (pr.draft) return { num, ok: true, skip: "draft" };
  const labels = (pr.labels || []).map((l) => l.name);
  const signed = labels.includes("state: value-signed");
  const head = pr.head?.sha;
  const runtime = touchesRuntime(num);
  const vf = head ? valueFsmVerdict(head) : "absent";

  // VALUE
  let valueOk = false, valueWhy;
  if (!signed) valueWhy = "missing `state: value-signed` (the value sign-off)";
  else if (runtime && vf !== "success") valueWhy = `value-signed but value-fsm is ${vf} on head — value-fsm must be green (a label cannot waive it)`;
  else { valueOk = true; valueWhy = runtime ? "value-fsm green + value-signed" : "non-runtime + value-signed"; }

  // DIFF
  const d = diffAccepted(pr);
  const diffWhy = d.ok ? `approved by @${d.by} on head` : "no non-author approval on the current head sha (a new push dismisses a stale approval)";

  return { num, ok: valueOk && d.ok, valueOk, valueWhy, diffOk: d.ok, diffWhy };
}

const nums = prNumbers();
if (!nums.length) { console.error("merge-card-gate: no PR number resolved from PR_NUMBERS / MERGE_GROUP_REF"); process.exit(2); }

let failed = 0;
for (const num of nums) {
  let c;
  try { c = card(num); }
  catch (e) { console.error(`::error ::merge-card #${num} — could not evaluate: ${e.message}`); failed++; continue; }
  if (c.skip) { console.log(`#${num}: skipped (${c.skip})`); continue; }
  console.log(`\n── merge card #${num} ──`);
  console.log(`  value: ${c.valueOk ? "✅" : "❌"} ${c.valueWhy}`);
  console.log(`  diff:  ${c.diffOk ? "✅" : "❌"} ${c.diffWhy}`);
  if (!c.ok) { failed++; console.log(`  → NOT mergeable: the card is not satisfied.`); }
  else console.log(`  → card satisfied.`);
}

if (failed) {
  console.error(`::error ::merge-card — ${failed} PR(s) not mergeable: value AND diff must both be accepted (choke point 1).`);
  process.exit(1);
}
console.log(`\n✓ merge-card — value + diff accepted for: ${nums.map((n) => "#" + n).join(", ")}`);
