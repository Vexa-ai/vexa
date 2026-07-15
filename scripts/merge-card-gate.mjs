// merge-card-gate — choke point 1 (the merge card), enforced. A PR carries two artifacts judged
// on different axes; MAIN accepts it only when BOTH are accepted (delivery constitution, merge bar):
//
//   • VALUE accepted  — the observation bundle is real. Runtime PRs: `value-fsm` (pr-value L3)
//     GREEN on the head sha AND `state: value-signed` (the D9 human sign-off). Non-runtime PRs
//     (no pr-value leg): `state: value-signed` alone.
//   • DIFF accepted   — the code was reviewed. Either the PR author is a MAINTAINER (holds the
//     commit bit — a maintainer reviewing their own work is allowed; the mandatory-review rule is
//     the quality gate for CONTRIBUTOR PRs), OR a GitHub review APPROVAL from a NON-AUTHOR whose
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

// Is the PR author a MAINTAINER — i.e. holds the commit bit (push access to this repo)? A
// maintainer's own PR does not require a separate non-author review: the mandatory-review rule is
// the quality gate for CONTRIBUTOR PRs, not for a maintainer reviewing their own work (D-R0 — a
// maintainer's exclusive authorities are the ready-stamp and the merge).
function authorIsMaintainer(login) {
  if (!login) return false;
  try {
    const p = ghj(`repos/${REPO}/collaborators/${login}/permission`);
    return p.permission === "admin" || p.permission === "write"; // admin/maintain/write = has the commit bit
  } catch { return false; }
}

// DIFF accepted when EITHER the author is a maintainer (self-review, above) OR a fresh, non-author
// APPROVED review exists: the reviewer's latest review is APPROVED and was submitted against the
// current head sha (a later push moves the head and invalidates the approval).
function diffAccepted(pr) {
  const author = pr.user?.login;
  if (authorIsMaintainer(author)) return { ok: true, maintainer: true };
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
  const diffWhy = d.ok
    ? (d.maintainer
        ? `maintainer self-review — @${pr.user?.login} holds the commit bit (no separate non-author review required)`
        : `approved by @${d.by} on head`)
    : "no non-author approval on the current head sha (a new push dismisses a stale approval)";

  return { num, ok: valueOk && d.ok, valueOk, valueWhy, diffOk: d.ok, diffWhy };
}

// Render one PR's card as GitHub-flavoured markdown. The leading marker lets the sticky-comment
// workflow find and update its own comment in place. This same markdown feeds the check summary.
function renderCard(c) {
  if (c.skip) return `<!-- merge-card -->\n### 🃏 Merge card — #${c.num}\n\n_Skipped (${c.skip})._`;
  const row = (label, ok, why) => `| **${label}** | ${ok ? "✅" : "❌"} | ${why} |`;
  const verdict = c.ok
    ? `**Ready to merge** — value and diff both accepted.`
    : `**Not mergeable yet** — value **and** diff must both be accepted before merge (choke point 1). Fill in what's ❌ above, then this clears automatically.`;
  return [
    `<!-- merge-card -->`,
    `### 🃏 Merge card — #${c.num}`,
    ``,
    `| check | | what it needs |`,
    `|---|---|---|`,
    row("Value", c.valueOk, c.valueWhy),
    row("Diff", c.diffOk, c.diffWhy),
    ``,
    verdict,
    ``,
    `<sub>How a PR reaches merge: [the merge bar](https://docs.vexa.ai/governance/delivery#integration-—-the-merge-bar).</sub>`,
  ].join("\n");
}

const nums = prNumbers();
if (!nums.length) { console.error("merge-card-gate: no PR number resolved from PR_NUMBERS / MERGE_GROUP_REF"); process.exit(2); }

let failed = 0;
const cards = [];
for (const num of nums) {
  let c;
  try { c = card(num); }
  catch (e) { console.error(`::error ::merge-card #${num} — could not evaluate: ${e.message}`); failed++; continue; }
  cards.push(c);
  console.log(renderCard(c));
  console.log("");
  if (!c.skip && !c.ok) failed++;
}

if (failed) {
  console.error(`::error ::merge-card — ${failed} PR(s) not mergeable: value AND diff must both be accepted (choke point 1).`);
  process.exit(1);
}
console.log(`✓ merge-card — value + diff accepted for: ${nums.map((n) => "#" + n).join(", ")}`);
