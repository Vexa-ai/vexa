// merge-card-gate — choke point 1 (the merge card), enforced. A PR carries two artifacts judged
// on different axes; MAIN accepts it only when BOTH are accepted (delivery constitution, merge bar):
//
//   • VALUE accepted  — the observation bundle is real. Runtime PRs: `value-fsm` (pr-value L3)
//     GREEN on the head sha AND `state: value-signed` (the D9 human sign-off). Non-runtime PRs
//     (no pr-value leg): `state: value-signed` alone. Because `labeled` also re-triggers value-fsm,
//     its newest run on head is often still non-terminal when the card fires: the card WAITS for a
//     terminal verdict (success/failure) rather than reading an in-flight run as failure (#655). A
//     value-fsm that never settles within the wait budget stays not-mergeable — a label can never
//     waive value-fsm; success must be positively observed.
//   • DIFF accepted   — the code was reviewed. Either the PR author is a MAINTAINER (holds the
//     commit bit — a maintainer reviewing their own work is allowed; the mandatory-review rule is
//     the quality gate for CONTRIBUTOR PRs), OR a GitHub review APPROVAL from a NON-AUTHOR whose
//     commit_id == the PR head sha (a new push dismisses a stale approval — re-review required).
//   • ACCEPTANCE honest — `Closes` asserts the full acceptance table is delivered (#712). Every
//     issue the PR would auto-close (GraphQL closingIssuesReferences — the exact linkage GitHub
//     acts on at merge) must have NO undelivered legs in its Acceptance section; otherwise the
//     card grows a third ❌ row and blocks until the legs ship, are marked delivered with
//     evidence, or the link is re-filed as `Part of #N` (a plain reference closes nothing, so
//     the row disappears). Born of the #622/#623 incident: a merge keyword silently dropped a
//     live acceptance leg written as a plain bullet, not a checkbox — both shapes are parsed.
//   • ARCHITECTURE and SECURITY passes — required on EVERY PR (docs and CI included), one row each.
//     A row is ✅ only when a PR comment from an account with write or admin on the repo (the same
//     maintainer check DIFF uses) carries a marker for the PR's CURRENT head sha:
//
//       <!-- vexa-pass:architecture sha=<full head sha> verdict=pass -->
//       <!-- vexa-pass:security sha=<full head sha> verdict=pass -->
//
//     `verdict=waived` also clears the row, but only when the same comment carries a non-empty
//     `waived-by=<who>` field; the card shows the row as waived and names who waived it. Bound to
//     the head: a marker for any other sha does not count (a new push needs a new pass), and the row
//     says which sha the pass on record was for. When several maintainer markers name the head, the
//     newest wins, so a later `verdict=fail` supersedes an earlier pass. Only issue comments are
//     read (not review bodies), and the card's own sticky comment never counts.
//
//     SECURITY FINDINGS STAY PRIVATE. The marker and its comment carry only the verdict, a finding
//     count (`findings=<n>`, optional) and the sha — never a finding, a path, a payload or an
//     exploit. The findings themselves stay in the private channel SECURITY.md describes until
//     fixed and released (coordinated disclosure); this repo is public and so is every PR comment.
//
// This is a required status check on `main` (added to branch protection alongside `gates`). It
// runs on pull_request + pull_request_review (PR-entry) and on merge_group (the queue re-check,
// where the PR number is parsed from the queue ref). A red merge-card blocks the merge with a
// plain-language card of exactly what's missing. A pass comment re-runs the head's newest card
// through merge-card-pass.yml, because a comment event cannot refresh a head-bound check itself.
//
// The workflow checks out `main` for every PR, so this file judges PRs into every base branch.
// Whether a red card BLOCKS a merge is a property of the base branch's protection, not of this
// script: on an unprotected base the card renders and the merge is not gated.
//
// Inputs (env): GITHUB_REPOSITORY; and PR_NUMBERS (space-separated) OR MERGE_GROUP_REF to parse.
// Exit 0 = every named PR's card is satisfied; 1 = one or more not; 2 = usage/nothing to check.

import { execSync } from "node:child_process";
import { pathToFileURL } from "node:url";

const REPO = process.env.GITHUB_REPOSITORY;
const IS_MAIN = import.meta.url === pathToFileURL(process.argv[1] || "").href;
if (IS_MAIN && !REPO) { console.error("merge-card-gate: GITHUB_REPOSITORY required"); process.exit(2); }

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

// value-fsm is a live sibling check: `labeled` (which triggers merge-card) also re-triggers
// value-fsm, so the head sha's newest value-fsm run is frequently still `queued`/`in_progress`
// when merge-card evaluates. A non-terminal run has `conclusion === null` — it is NOT a failure,
// it simply has no verdict yet. Collapsing it into "failure" (the old bug) red-cards a PR whose
// value-fsm is on its way to green. The verdict is therefore FOUR-state:
//
//   "absent"   — no value-fsm run on this sha at all
//   "pending"  — newest run exists but is non-terminal (queued|in_progress; conclusion === null)
//   "success"  — newest run completed with conclusion "success"
//   "failure"  — newest run completed with any other conclusion (failure|cancelled|timed_out|…)
//
// Pure over a raw check-runs array so it is unit-testable against fixtures.
export function verdictFromRuns(runs) {
  const vf = (runs || []).filter((r) => r.name === "value-fsm");
  if (!vf.length) return "absent";
  vf.sort((a, b) => new Date(b.started_at || 0) - new Date(a.started_at || 0));
  const top = vf[0];
  if (top.status && top.status !== "completed") return "pending"; // queued | in_progress
  if (top.conclusion == null) return "pending";                   // completed-but-verdictless: treat as not-yet-terminal
  return top.conclusion === "success" ? "success" : "failure";
}

function readValueFsmRuns(sha) {
  return ghj(`repos/${REPO}/commits/${sha}/check-runs?per_page=100`).check_runs || [];
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Wait for value-fsm to reach a TERMINAL verdict on `sha` instead of sampling it once mid-run.
// Polls the newest run's verdict; on "pending" it backs off and re-reads, up to `attempts` reads
// within the job budget. Removes the race entirely: a value-fsm still running when the card fires
// is waited out to its real success/failure. If it never settles within the budget the verdict
// stays "pending" (or "absent") and the caller red-cards it LOUDLY — a non-terminal check is never
// silently accepted, so the invariant holds: success must be positively observed, a label cannot
// waive it. Injected read/sleep make it unit-testable without the network or real clock.
export async function waitForTerminalValueFsm(
  sha,
  { read = readValueFsmRuns, wait = sleep, attempts = 20, delayMs = 15000 } = {},
) {
  let verdict = "absent";
  for (let i = 0; i < attempts; i++) {
    verdict = verdictFromRuns(read(sha));
    // "absent" and "pending" are both non-terminal: `labeled` also re-triggers value-fsm, so on a
    // runtime PR the run may not have registered (absent) or may still be running (pending) when
    // the card fires. Only success/failure are settled reads.
    if (verdict === "success" || verdict === "failure") return verdict;
    if (i < attempts - 1) await wait(delayMs);
  }
  return verdict; // still absent/pending after the budget — caller treats non-success as not-mergeable
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

// The Acceptance SECTION of an issue body: from the first heading matching /acceptance/i to the
// next heading of the same or higher level (the D10 house shape). Everything outside it — plan
// checklists, design bullets — is ignored, so an unchecked box elsewhere never trips the row.
function acceptanceSection(body) {
  const lines = (body || "").split(/\r?\n/);
  let start = -1, level = 0;
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(/^(#{1,6})\s+(.*)/);
    if (m && /acceptance/i.test(m[2])) { start = i + 1; level = m[1].length; break; }
  }
  if (start < 0) return "";
  let end = lines.length;
  for (let i = start; i < lines.length; i++) {
    const m = lines[i].match(/^(#{1,6})\s/);
    if (m && m[1].length <= level) { end = i; break; }
  }
  return lines.slice(start, end).join("\n");
}

// Count the UNDELIVERED legs in an issue body's Acceptance section — both house shapes (#712):
//
//   • checkbox shape — the section contains task-list items: every `- [ ]` is an open leg.
//   • legacy bullet shape (the real #622) — NO task-list items: every top-level list item
//     WITHOUT a delivered marker (trailing ✅ or `[x]`) is an open leg. A naive `- [ ]` count
//     scores this shape "0 unchecked" and lets the close through — exactly the #623 incident.
//
// Pure over the raw body string so it is unit-testable against fixtures.
export function openAcceptanceLegs(body) {
  const section = acceptanceSection(body);
  if (!section) return 0;
  if (/^\s*[-*+] \[[ xX]\]/m.test(section)) return (section.match(/^\s*[-*+] \[ \]/gm) || []).length;
  // Bullet shape: fold indented continuation lines into their item so a wrapped bullet's
  // trailing marker still counts; prose paragraphs between/after items are not legs.
  let open = 0, item = null;
  const close = () => { if (item != null && !/(?:✅|\[x\])\s*$/i.test(item.trim())) open++; item = null; };
  for (const line of section.split("\n")) {
    if (/^[-*+]\s+/.test(line)) { close(); item = line; }
    else if (item != null && /^\s+\S/.test(line)) item += " " + line.trim();
    else close();
  }
  close();
  return open;
}

// The ACCEPTANCE row, pure over the PR's closing issues [{number, body}]. Returns null when the
// PR closes nothing (a `Part of #N` reference is not a closing keyword — the row is absent), else
// the row: ✅ only when EVERY closing issue's acceptance legs are all delivered.
export function acceptanceFromIssues(issues) {
  if (!issues || !issues.length) return null;
  const red = [];
  for (const it of issues) {
    const open = openAcceptanceLegs(it.body);
    if (open > 0)
      red.push(`issue #${it.number} has ${open} undelivered acceptance leg(s) — deliver them, mark them delivered with evidence on the issue, or re-link as Part of #${it.number}`);
  }
  if (red.length) return { ok: false, why: red.join("; ") };
  return { ok: true, why: `every acceptance leg delivered on ${issues.map((i) => "#" + i.number).join(", ")}` };
}

// The PR's closing issues via GraphQL closingIssuesReferences — the same linkage GitHub acts on
// at merge, so the row inspects exactly what the keyword will do. Injected into card() so the
// verdict path stays offline-testable (the verdictFromRuns pattern).
function readClosingIssues(num) {
  const [owner, name] = REPO.split("/");
  const query =
    "query($owner:String!,$name:String!,$num:Int!){repository(owner:$owner,name:$name){pullRequest(number:$num){closingIssuesReferences(first:50){nodes{number body}}}}}";
  let last;
  for (let i = 0; i < 3; i++) {
    try {
      const out = execSync(
        `gh api graphql -f query='${query}' -F owner="${owner}" -F name="${name}" -F num=${num}`,
        { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
      );
      return JSON.parse(out).data.repository.pullRequest.closingIssuesReferences.nodes || [];
    } catch (e) { last = e; }
  }
  throw last;
}

// ── ARCHITECTURE and SECURITY passes ────────────────────────────────────────────────────────────

const CARD_MARKER = "<!-- merge-card -->";
const FULL_SHA = /^[0-9a-f]{40}$/i;
const short = (sha) => String(sha || "").slice(0, 7);

// Every `<!-- vexa-pass:<kind> key=value … -->` marker in a comment body, in order. Fields are
// whitespace-separated key=value tokens; a marker without a sha or a verdict is not a marker.
// Pure over the raw body string so it is unit-testable against fixtures.
export function passMarkers(body) {
  const out = [];
  for (const m of String(body || "").matchAll(/<!--\s*vexa-pass:([a-z][a-z-]*)\s([\s\S]*?)-->/gi)) {
    const fields = {};
    for (const f of m[2].matchAll(/([a-z][a-z-]*)=([^\s<>]+)/gi)) fields[f[1].toLowerCase()] = f[2];
    if (!fields.sha || !fields.verdict) continue;
    out.push({ kind: m[1].toLowerCase(), sha: fields.sha, verdict: fields.verdict.toLowerCase(), fields });
  }
  return out;
}

// The `waived-by=<who>` field anywhere in the same comment (inside the marker or beside it). An
// empty value, or one with no letter or digit in it, is no waiver.
function waivedBy(body) {
  const m = String(body || "").match(/(?:^|[\s(|])waived-by=([^\s<>]+)/i);
  const who = m ? m[1].replace(/-+$/, "") : "";
  return /[a-z0-9]/i.test(who) ? who : null;
}

const howToPass = (kind) =>
  `a maintainer (write or admin on the repo) posts a PR comment carrying \`<!-- vexa-pass:${kind} sha=<full head sha> verdict=pass -->\``;

// One pass row, pure over the PR's issue comments (the `repos/:repo/issues/:n/comments` shape, in
// API order = oldest first) and an injected maintainer predicate, so it has no network in its unit
// path. Returns { ok, state, why } where state is pass | waived | stale | invalid | missing.
export function passRow(kind, comments, { head, isMaintainer }) {
  const headSha = String(head || "").toLowerCase();
  const seen = []; // { by, sha, verdict, fields, waiver }
  for (const c of comments || []) {
    const body = c?.body || "";
    if (body.includes(CARD_MARKER)) continue; // the card quoting itself is never a pass
    const by = c?.user?.login;
    if (!by) continue;
    for (const mk of passMarkers(body)) if (mk.kind === kind) seen.push({ ...mk, by, waiver: waivedBy(body) });
  }
  const counted = seen.filter((s) => isMaintainer(s.by));
  const onHead = counted.filter((s) => s.sha.toLowerCase() === headSha);

  if (onHead.length) {
    const s = onHead[onHead.length - 1]; // newest maintainer verdict on head wins
    const count = /^\d+$/.test(s.fields.findings || "") ? ` (${s.fields.findings} finding${s.fields.findings === "1" ? "" : "s"})` : "";
    if (s.verdict === "pass")
      return { ok: true, state: "pass", why: `pass on head ${short(head)} by @${s.by}${count}` };
    if (s.verdict === "waived" && s.waiver)
      return { ok: true, state: "waived", why: `waived on head ${short(head)} by ${s.waiver} (recorded by @${s.by})${count}` };
    if (s.verdict === "waived")
      return { ok: false, state: "invalid", why: `@${s.by} recorded \`verdict=waived\` on head ${short(head)} without a \`waived-by=\` field — a waiver counts only when the same comment names who waived it` };
    return { ok: false, state: "invalid", why: `the newest ${kind} pass on head ${short(head)} (by @${s.by}) is \`verdict=${s.verdict}\`${count} — only \`verdict=pass\`, or \`verdict=waived\` with \`waived-by=\`, clears this row` };
  }

  if (counted.length) {
    const s = counted[counted.length - 1];
    if (!FULL_SHA.test(s.sha) && headSha.startsWith(s.sha.toLowerCase()))
      return { ok: false, state: "invalid", why: `@${s.by}'s ${kind} marker carries \`sha=${s.sha}\` — it must carry the full 40-character head sha (${head})` };
    return { ok: false, state: "stale", why: `${kind} pass on record is for ${short(s.sha)}, head is ${short(head)} — re-run the ${kind} pass on the current head; ${howToPass(kind)}` };
  }

  const strangers = [...new Set(seen.map((s) => "@" + s.by))];
  const ignored = strangers.length ? ` (${strangers.join(", ")} posted a marker, which does not count — no write or admin on the repo)` : "";
  return { ok: false, state: "missing", why: `no ${kind} pass on head ${short(head)}${ignored} — ${howToPass(kind)}` };
}

// Paginated like touchesRuntime: a long-running PR here routinely passes 100 comments, and a pass
// posted past the first page would render a false ❌. These rows GATE, so a failed read throws and
// fails the card, like every other gating read in card().
function readComments(num) {
  const all = [];
  for (let page = 1; page <= 10; page++) {
    const batch = ghj(`repos/${REPO}/issues/${num}/comments?per_page=100&page=${page}`);
    all.push(...batch);
    if (batch.length < 100) break;
  }
  return all;
}

// The verdict, pure over the rows. Every row gates; ACCEPTANCE exists only when the PR carries a
// closing reference. The passes never buy another row, and no other row buys a pass.
export function cardOk({ valueOk, diffOk, acceptance, architecture, security }) {
  return Boolean(valueOk) && Boolean(diffOk) && (!acceptance || acceptance.ok) && Boolean(architecture?.ok) && Boolean(security?.ok);
}

async function card(num, { readClosing = readClosingIssues, readCmts = readComments } = {}) {
  const pr = ghj(`repos/${REPO}/pulls/${num}`);
  if (pr.draft) return { num, ok: true, skip: "draft" };
  const labels = (pr.labels || []).map((l) => l.name);
  const signed = labels.includes("state: value-signed");
  const head = pr.head?.sha;
  const runtime = touchesRuntime(num);
  // Only a runtime PR has a value-fsm leg, and only then do we pay the wait. A non-terminal
  // value-fsm is waited out to its real verdict rather than sampled once mid-run (the #655 race).
  const vf = runtime && head ? await waitForTerminalValueFsm(head) : "absent";

  // VALUE
  let valueOk = false, valueWhy;
  if (!signed) valueWhy = "missing `state: value-signed` (the value sign-off)";
  else if (runtime && vf === "success") { valueOk = true; valueWhy = "value-fsm green + value-signed"; }
  else if (runtime && (vf === "pending" || vf === "absent"))
    valueWhy = `value-signed but value-fsm did not reach a terminal verdict on head within the wait budget (still ${vf}) — value-fsm must be green (a label cannot waive it)`;
  else if (runtime) valueWhy = `value-signed but value-fsm is ${vf} on head — value-fsm must be green (a label cannot waive it)`;
  else { valueOk = true; valueWhy = "non-runtime + value-signed"; }

  // DIFF
  const d = diffAccepted(pr);
  const diffWhy = d.ok
    ? (d.maintainer
        ? `maintainer self-review — @${pr.user?.login} holds the commit bit (no separate non-author review required)`
        : `approved by @${d.by} on head`)
    : "no non-author approval on the current head sha (a new push dismisses a stale approval)";

  // ACCEPTANCE — what would this merge auto-close, and is every closed issue fully delivered?
  const acceptance = acceptanceFromIssues(readClosing(num));

  // ARCHITECTURE + SECURITY — a maintainer's pass marker for THIS head sha, every PR. One comments
  // read and one permission lookup per distinct marker author serve both rows.
  const comments = readCmts(num);
  const perms = new Map();
  const isMaintainer = (login) => {
    if (!perms.has(login)) perms.set(login, authorIsMaintainer(login));
    return perms.get(login);
  };
  const architecture = passRow("architecture", comments, { head, isMaintainer });
  const security = passRow("security", comments, { head, isMaintainer });

  const rows = { valueOk, diffOk: d.ok, acceptance, architecture, security };
  return { num, ok: cardOk(rows), valueOk, valueWhy, diffOk: d.ok, diffWhy, acceptance, architecture, security };
}

// Render one PR's card as GitHub-flavoured markdown. The leading marker lets the sticky-comment
// workflow find and update its own comment in place. This same markdown feeds the check summary.
export function renderCard(c) {
  if (c.skip) return `<!-- merge-card -->\n### 🃏 Merge card — #${c.num}\n\n_Skipped (${c.skip})._`;
  const row = (label, ok, why, mark = ok ? "✅" : "❌") => `| **${label}** | ${mark} | ${why} |`;
  const pass = (label, r) => row(label, r.ok, r.why, r.ok && r.state === "waived" ? "✅ waived" : undefined);
  const verdict = c.ok
    ? `**Ready to merge** — every row above is accepted.`
    : `**Not mergeable yet** — every row above must be accepted before merge (choke point 1). Fill in what's ❌ above, then this clears automatically.`;
  return [
    `<!-- merge-card -->`,
    `### 🃏 Merge card — #${c.num}`,
    ``,
    `| check | | what it needs |`,
    `|---|---|---|`,
    row("Value", c.valueOk, c.valueWhy),
    row("Diff", c.diffOk, c.diffWhy),
    // The row exists only when the PR carries a closing reference — `Part of #N` closes nothing.
    ...(c.acceptance ? [row("Acceptance", c.acceptance.ok, c.acceptance.why)] : []),
    // Every PR carries both pass rows; a card built without them renders them as missing (red),
    // never silently drops them.
    pass("Architecture", c.architecture || { ok: false, why: "architecture pass not evaluated" }),
    pass("Security", c.security || { ok: false, why: "security pass not evaluated" }),
    ``,
    verdict,
    ``,
    `<sub>How a PR reaches merge: [the merge bar](https://docs.vexa.ai/governance/delivery#integration-—-the-merge-bar).</sub>`,
  ].join("\n");
}

async function main() {
  const nums = prNumbers();
  if (!nums.length) { console.error("merge-card-gate: no PR number resolved from PR_NUMBERS / MERGE_GROUP_REF"); process.exit(2); }

  let failed = 0;
  for (const num of nums) {
    let c;
    try { c = await card(num); }
    catch (e) { console.error(`::error ::merge-card #${num} — could not evaluate: ${e.message}`); failed++; continue; }
    console.log(renderCard(c));
    console.log("");
    if (!c.skip && !c.ok) failed++;
  }

  if (failed) {
    console.error(`::error ::merge-card — ${failed} PR(s) not mergeable: every card row must be accepted (choke point 1).`);
    process.exit(1);
  }
  console.log(`✓ merge-card — card accepted for: ${nums.map((n) => "#" + n).join(", ")}`);
}

if (IS_MAIN) main();
