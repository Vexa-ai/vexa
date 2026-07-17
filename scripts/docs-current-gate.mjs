// docs-current-gate — D6c enforced. "Docs ride the change." A PR that touches a user-facing
// product surface must make a CONSCIOUS docs decision: either it updates docs/**, or it explicitly
// confirms none is needed via a `docs: none` label. No third option — docs cannot silently drift.
//
// This is a required status check on `main` (added to branch protection alongside `gates`). It runs
// on pull_request + pull_request_review (label events) + merge_group (the queue re-check, PR number
// from the queue ref). Docs-only / governance / test-only PRs (no product surface) are exempt.
//
// Inputs (env): GITHUB_REPOSITORY; PR_NUMBERS (space-sep) OR MERGE_GROUP_REF to parse.
// Exit 0 = every named PR resolved; 1 = one or more need a docs decision; 2 = usage.

import { execSync } from "node:child_process";

const REPO = process.env.GITHUB_REPOSITORY;
if (!REPO) { console.error("docs-current-gate: GITHUB_REPOSITORY required"); process.exit(2); }

// Product surfaces a user reads docs for (mirror pr-value's runtime filter — those are the
// surfaces users interact with). A change here needs a docs decision.
const SURFACE_PREFIXES = ["core/", "clients/", "deploy/", "libs/"];
const SURFACE_FILES = ["package.json"];
const isSurface = (p) => SURFACE_FILES.includes(p) || SURFACE_PREFIXES.some((pre) => p.startsWith(pre));
const isDocs = (p) => p.startsWith("docs/") || p.endsWith(".mdx");
const NONE_LABEL = "docs: none";

function ghRaw(path) { let e; for (let i = 0; i < 3; i++) { try { return execSync(`gh api "${path}"`, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }); } catch (x) { e = x; } } throw e; }
const ghj = (path) => JSON.parse(ghRaw(path));

function prNumbers() {
  const explicit = (process.env.PR_NUMBERS || "").trim();
  if (explicit) return [...new Set(explicit.split(/\s+/).map(Number).filter(Boolean))];
  const ref = process.env.MERGE_GROUP_REF || "";
  return [...new Set([...ref.matchAll(/pr-(\d+)-/g)].map((m) => +m[1]))];
}

function files(num) {
  const out = [];
  for (let pg = 1; pg <= 20; pg++) { const f = ghj(`repos/${REPO}/pulls/${num}/files?per_page=100&page=${pg}`); out.push(...f.map((x) => x.filename)); if (f.length < 100) break; }
  return out;
}

function card(num) {
  const pr = ghj(`repos/${REPO}/pulls/${num}`);
  if (pr.draft) return { num, ok: true, skip: "draft" };
  const labels = (pr.labels || []).map((l) => l.name);
  const changed = files(num);
  const touchesSurface = changed.some(isSurface);
  const touchesDocs = changed.some(isDocs);
  const declaredNone = labels.includes(NONE_LABEL);

  if (!touchesSurface) return { num, ok: true, why: "no product surface touched — docs decision N/A" };
  if (touchesDocs) return { num, ok: true, why: "docs/** updated with the change" };
  if (declaredNone) return { num, ok: true, why: `\`${NONE_LABEL}\` — author confirmed no docs change needed` };
  return { num, ok: false, why: `touches a product surface but changes no docs/** and lacks the \`${NONE_LABEL}\` label — update the docs, or add \`${NONE_LABEL}\` (with a reason) to confirm none is needed (D6c)` };
}

const nums = prNumbers();
if (!nums.length) { console.error("docs-current-gate: no PR number resolved"); process.exit(2); }

let failed = 0;
for (const num of nums) {
  let c;
  try { c = card(num); }
  catch (e) { console.error(`::error ::docs-current #${num} — could not evaluate: ${e.message}`); failed++; continue; }
  if (c.skip) { console.log(`#${num}: skipped (${c.skip})`); continue; }
  console.log(`#${num}: ${c.ok ? "✅" : "❌"} ${c.why}`);
  if (!c.ok) failed++;
}

if (failed) {
  console.error(`::error ::docs-current — ${failed} PR(s) need a docs decision: update docs/** or add the \`${NONE_LABEL}\` label (D6c).`);
  process.exit(1);
}
console.log(`✓ docs-current — docs decision made for: ${nums.map((n) => "#" + n).join(", ")}`);
