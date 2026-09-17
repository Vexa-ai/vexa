// merge-card-gate — choke point 1 (the merge card), enforced. A PR carries two artifacts judged
// on different axes; MAIN accepts it only when BOTH are accepted (delivery constitution, merge bar).
// Blocking rows depend on the author's author_association. MEMBER/OWNER/COLLABORATOR PRs
// are evaluated exactly as before (VALUE · DIFF · ACCEPTANCE), as described below.
// External PRs require .github/ROSTER.json peer review: 2 sign-offs, ≥1 an approving review
// on head; the second may be a `Validated on …` comment by a roster member. They require
// value-fsm green on head for runtime changes and exactly one contribution-rights box ticked
// (not uncertain). `state: value-signed` is the maintainer's record applied at merge, not a
// precondition. Acceptance and the configurable Human test row are shared by both paths.
// The card does not, and cannot, re-check main's other required status checks (gates,
// contribution-rights, DCO and the security scanners): merge-card is itself one of them, so
// reading them would be circular. Branch protection already refuses a merge while any is red.
// The card's own contribution on that axis is the positively-observed value-fsm verdict.
//
// Member path:
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
//   • HUMAN TEST — who, OTHER than the author, ran the software on their own instance (#1687).
//     Read off the PR's comments, never off a label: a label says someone thinks this is
//     validated, a comment says who, on what, and what happened, and it cannot be applied by
//     accident from a mobile label picker. INFORMATIONAL — it renders ✅/❌ and changes no
//     verdict until `MERGE_CARD_REQUIRE_HUMAN_TEST` is turned on; because of that, and because
//     this is a required check, its read is fail-soft (see `humanTestSafely`).
//
// This is a required status check on `main` (added to branch protection alongside `gates`). It
// runs on pull_request + pull_request_review (PR-entry) and on merge_group (the queue re-check,
// where the PR number is parsed from the queue ref). A red merge-card blocks the merge with a
// plain-language card of exactly what's missing.
//
// Inputs (env): GITHUB_REPOSITORY; PR numbers resolve from PR_NUMBERS (space-separated), then
// MERGE_GROUP_REF, then GITHUB_EVENT_NAME / GITHUB_EVENT_PATH. The event-file read is fail-soft:
// an unreadable or malformed payload resolves to an empty list, never an exception.
// Optional: MERGE_CARD_REQUIRE_HUMAN_TEST (1|true|yes makes the human-test row BLOCKING; default
// off) and MERGE_CARD_AGENT_LOGINS (comma-separated logins that are agents, not humans — a login
// ending in `[bot]` and a `type: "Bot"` account are already excluded without listing them).
// Exit 0 = every named PR's card is satisfied; 1 = one or more not; 2 = usage/nothing to check.

import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { selectedRights } from "./contribution-rights-gate.mjs";

export const ROSTER_PATH = ".github/ROSTER.json";
export const MEMBER_ASSOCIATIONS = ["MEMBER", "OWNER", "COLLABORATOR"];

export function isExternalAuthor(pr) {
  const association = pr?.author_association;
  return typeof association === "string" && association.trim() !== ""
    && !MEMBER_ASSOCIATIONS.includes(association.trim().toUpperCase());
}

// Resolve against the trusted module checkout, never the PR's tree or an environment override.
const defaultRosterRead = () => readFileSync(new URL(`../${ROSTER_PATH}`, import.meta.url), "utf8");

export function readRoster(read = defaultRosterRead) {
  try {
    const roster = JSON.parse(read());
    if (!Array.isArray(roster?.reviewers)) throw new Error("reviewers must be an array");
    const reviewers = [...new Set(roster.reviewers
      .filter((login) => typeof login === "string" && login.trim())
      .map((login) => login.trim().toLowerCase()))];
    return { reviewers, error: null };
  } catch (e) { return { reviewers: [], error: String(e?.message || e) }; }
}

export function valueRow({ signed, runtime, valueFsm, external }) {
  if (!external && !signed) return { ok: false, why: "missing `state: value-signed` (the value sign-off)" };
  const signedNote = "`state: value-signed` is applied by the maintainer at merge — not required before merge on a PR from an external author";
  const signedSuffix = signed ? " (already applied)" : "";
  if (!runtime) return { ok: true, why: external
    ? `non-runtime — no value-fsm leg; ${signedNote}${signedSuffix}` : "non-runtime + value-signed" };
  if (valueFsm === "success") return { ok: true, why: external
    ? `value-fsm green on head — ${signedNote}${signedSuffix}` : "value-fsm green + value-signed" };
  const prefix = external ? "" : "value-signed but ";
  return { ok: false, why: valueFsm === "pending" || valueFsm === "absent"
    ? `${prefix}value-fsm did not reach a terminal verdict on head within the wait budget (still ${valueFsm}) — value-fsm must be green (a label cannot waive it)`
    : `${prefix}value-fsm is ${valueFsm} on head — value-fsm must be green (a label cannot waive it)` };
}

export function rosterReviewRow({ reviews, comments, author, head, roster, agentLogins = [] }) {
  const members = new Set(roster.error ? [] : roster.reviewers.map((login) => login.toLowerCase()));
  const latest = new Map();
  // Reserve insertion order even when the first review is COMMENTED; only verdicts replace it.
  for (const review of reviews || []) {
    const login = review.user?.login?.toLowerCase();
    if (!login || login === author?.toLowerCase() || !members.has(login)) continue;
    if (!latest.has(login)) latest.set(login, null);
    if (["APPROVED", "CHANGES_REQUESTED", "DISMISSED"].includes(review.state)) latest.set(login, review);
  }
  const approvals = [...latest.values()]
    .filter((review) => review?.state === "APPROVED" && review.commit_id === head)
    .map((review) => review.user.login);
  const counted = new Set(approvals.map((login) => login.toLowerCase()));
  const validations = humanTestFromComments(comments, { author, agentLogins }).validations.filter((v) => {
    const login = v.by.toLowerCase();
    if (!members.has(login) || counted.has(login)) return false;
    counted.add(login);
    return true;
  });
  const ok = approvals.length >= 1 && approvals.length + validations.length >= 2;
  const signoffs = [...approvals.map((a) => `@${a} approved on head`),
    ...validations.map((v) => `@${v.by} validated (${v.shape}) — ${v.ran}, ${v.result}`)];
  const why = roster.error
    ? `could not read ${ROSTER_PATH} (${roster.error}) — no roster sign-off can be counted`
    : roster.reviewers.length === 0
      ? `${ROSTER_PATH} lists no reviewers — a maintainer must add roster members before a PR from an external author can clear this row`
      : ok
        ? `${signoffs.length} roster sign-offs: ${signoffs.join("; ")}`
        : `${signoffs.length} of 2 roster sign-offs, at least one an approving review on the current head sha: ${signoffs.length ? signoffs.join("; ") : "none yet"} — roster: ${roster.reviewers.map((r) => "@" + r).join(", ")}`;
  return { ok, why, approvals, validations };
}

export function rightsRow(prBody) {
  const selected = selectedRights(prBody || "");
  if (selected.length === 0) return { ok: false, why: "no contribution-rights box ticked — tick exactly one box in the PR description's Contribution rights section" };
  if (selected.length > 1) return { ok: false, why: `${selected.length} contribution-rights boxes ticked — tick exactly one` };
  if (selected[0] === "uncertain") return { ok: false, why: "contribution rights marked uncertain — Vexa will help determine the path; the `contribution-rights` check carries the resolution" };
  return { ok: true, why: `contribution rights declared: ${selected[0]} — the \`contribution-rights\` check is the authority on DCO and corporate authorisation` };
}

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

// Resolve only PR-bearing event shapes; plain issues share the same number space.
export function prNumbersFromEventPayload(eventName, event = {}) {
  try {
    let number;
    if (eventName === "issue_comment") {
      if (event?.issue?.pull_request == null) return [];
      number = Number(event.issue.number);
    } else if (["pull_request", "pull_request_target", "pull_request_review"].includes(eventName)) {
      number = Number(event?.pull_request?.number);
    } else if (eventName === "merge_group") {
      const ref = event?.merge_group?.head_ref;
      // gh-readonly-queue/main/pr-620-<sha> (a merge group can stack several)
      return typeof ref === "string"
        ? [...new Set([...ref.matchAll(/pr-(\d+)-/g)].map((m) => +m[1]))]
        : [];
    } else return [];
    return Number.isFinite(number) && number > 0 ? [number] : [];
  } catch { return []; }
}

const readEventFile = (path) => JSON.parse(readFileSync(path, "utf8"));

// Explicit inputs take precedence; event I/O cannot throw on the required-check path.
export function prNumbersFromEnv(env = {}, { readEvent = readEventFile } = {}) {
  try {
    const explicit = (env.PR_NUMBERS || "").trim();
    if (explicit) return [...new Set(explicit.split(/\s+/).map(Number).filter(Boolean))];
    const ref = env.MERGE_GROUP_REF || "";
    if (ref) {
      const nums = prNumbersFromEventPayload("merge_group", { merge_group: { head_ref: ref } });
      if (nums.length) return nums;
    }
    if (env.GITHUB_EVENT_NAME && env.GITHUB_EVENT_PATH)
      return prNumbersFromEventPayload(env.GITHUB_EVENT_NAME, readEvent(env.GITHUB_EVENT_PATH));
    return [];
  } catch { return []; }
}

const prNumbers = () => prNumbersFromEnv(process.env);

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
function diffAccepted(pr, reviews) {
  const author = pr.user?.login;
  if (authorIsMaintainer(author)) return { ok: true, maintainer: true };
  const head = pr.head?.sha;
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

// The one shape a validation may take. Three deployment shapes, and `hosted` is deliberately not
// among them: the claim this row makes is that a human stood the software up themselves, and using
// the hosted service proves nothing about THIS diff. The comma split between `what I ran` and
// `result` is lazy, so a `what I ran` containing a comma splits at the first one — both captures
// are rendered, joined back with the comma, so where the split landed never changes what a reader
// sees. Deliberately anchored to ONE line: a shape buried in prose is not a claim.
const VALIDATION_RE = /^\s*Validated on (lite|compose|helm),\s*(.+?),\s*(.+?)\s*$/im;

export const NO_HUMAN_TEST =
  "no human test — a non-author human must post a comment whose FIRST non-empty line is " +
  "`Validated on <lite|compose|helm>, <what I ran>, <result>`";

// Pure over a comments array (the `repos/:repo/issues/:n/comments` shape) so the row has fixture
// proof and no network in its unit path — the verdictFromRuns/openAcceptanceLegs pattern. An agent
// posting under a human-shaped account is the one exclusion a suffix test cannot make, hence
// `agentLogins`; this repo names none today, so the list is empty and the mechanism is the point.
export function humanTestFromComments(comments, { author, agentLogins = [], blocking = false } = {}) {
  const agents = new Set(agentLogins.map((login) => login.toLowerCase()));
  const validations = [];
  for (const comment of comments || []) {
    const by = comment.user?.login;
    if (!by || by.toLowerCase() === author?.toLowerCase()) continue;
    if (comment.user?.type === "Bot" || /\[bot\]$/i.test(by) || agents.has(by.toLowerCase())) continue;
    const firstLine = (comment.body || "").split(/\r?\n/).find((line) => line.trim());
    const match = firstLine?.match(VALIDATION_RE);
    if (!match) continue;
    validations.push({ by, shape: match[1].toLowerCase(), ran: match[2].trim(), result: match[3].trim(), url: comment.html_url });
  }
  const ok = validations.length > 0;
  const why = ok
    ? validations.map((v) => `@${v.by} (${v.shape}) — ${v.ran}, ${v.result}${v.url ? ` ([comment](${v.url}))` : ""}`).join("; ")
    // Say what the row does to the merge, and say it from the flag — not from a hard-coded clause
    // that becomes a lie the day someone follows the workflows' own instruction to flip it.
    : `${NO_HUMAN_TEST} (${blocking ? "required — this row blocks merge" : "informational — this row does not block merge"})`;
  return { ok, why, validations };
}

// Paginated like touchesRuntime: a long-running PR here routinely passes 100 comments, and a
// validation posted past the first page would render a false ❌ — and, once the flag is on, a
// false block.
function readComments(num) {
  const all = [];
  for (let page = 1; page <= 10; page++) {
    const batch = ghj(`repos/${REPO}/issues/${num}/comments?per_page=100&page=${page}`);
    all.push(...batch);
    if (batch.length < 100) break;
  }
  return all;
}

// Reviews have the same pagination budget as comments; a sign-off on page two counts.
function readReviews(num) {
  const all = [];
  for (let page = 1; page <= 10; page++) {
    const batch = ghj(`repos/${REPO}/pulls/${num}/reviews?per_page=100&page=${page}`);
    all.push(...batch);
    if (batch.length < 100) break;
  }
  return all;
}

// Informational, so the read is fail-SOFT: every other read in card() is gating data and a throw
// rightly fails the card, but this row must never red a required check it does not gate. An
// unreadable comments list becomes an unavailable row, not a failed evaluation.
export function humanTestSafely(read, num, opts) {
  try { return humanTestFromComments(read(num), opts); }
  catch (e) { return { ok: false, why: `could not read this PR's comments (${e.message}) — the human-test row is unavailable`, validations: [] }; }
}

// The two knobs, read from an env object rather than `process.env` directly so both settings are
// reachable from a test. `blocking` is the whole safety claim of this change: while it is off, the
// human-test row cannot alter the verdict, and `cardOk` below is the only place that could.
export function humanTestConfig(env = {}) {
  return {
    blocking: ["1", "true", "yes"].includes((env.MERGE_CARD_REQUIRE_HUMAN_TEST || "").toLowerCase()),
    agentLogins: (env.MERGE_CARD_AGENT_LOGINS || "").split(",").map((login) => login.trim()).filter(Boolean),
  };
}

// VALUE and DIFF/PEER REVIEW always gate; ACCEPTANCE and RIGHTS gate when present.
// HUMAN TEST gates only when `blocking`.
export function cardOk({ valueOk, diffOk, acceptance, humanTest, rights, blocking = false }) {
  return Boolean(valueOk) && Boolean(diffOk) && (!acceptance || acceptance.ok) && (!rights || rights.ok) && (!blocking || humanTest.ok);
}

async function card(num, { readClosing = readClosingIssues, readComments: readCmts = readComments } = {}) {
  const pr = ghj(`repos/${REPO}/pulls/${num}`);
  const external = isExternalAuthor(pr);
  if (pr.draft) return { num, external, ok: true, skip: "draft" };
  const labels = (pr.labels || []).map((l) => l.name);
  const signed = labels.includes("state: value-signed");
  const head = pr.head?.sha;
  const runtime = touchesRuntime(num);
  // Only a runtime PR has a value-fsm leg, and only then do we pay the wait. A non-terminal
  // value-fsm is waited out to its real verdict rather than sampled once mid-run (the #655 race).
  const vf = runtime && head ? await waitForTerminalValueFsm(head) : "absent";

  const { ok: valueOk, why: valueWhy } = valueRow({ signed, runtime, valueFsm: vf, external });

  // Share one fail-soft comments read: the informational row still reports all human tests.
  const { blocking, agentLogins } = humanTestConfig(process.env);
  let comments = [];
  const humanTest = humanTestSafely((n) => {
    const result = readCmts(n);
    comments = result;
    return result;
  }, num, { author: pr.user?.login, agentLogins, blocking });

  // Discard a partial read on failure: unreadable evidence cannot count as a sign-off.
  let reviews = [], reviewError;
  try { reviews = readReviews(num); }
  catch (e) { reviewError = e; }
  const d = external
    ? rosterReviewRow({ reviews, comments, author: pr.user?.login, head, roster: readRoster(), agentLogins })
    : diffAccepted(pr, reviews);
  if (reviewError && !d.maintainer) {
    d.ok = false;
    d.why = `could not read this PR's reviews (${reviewError.message}) — no review approval can be counted`;
  }
  const diffWhy = external || reviewError && !d.maintainer ? d.why : d.ok
    ? (d.maintainer
        ? `maintainer self-review — @${pr.user?.login} holds the commit bit (no separate non-author review required)`
        : `approved by @${d.by} on head`)
    : "no non-author approval on the current head sha (a new push dismisses a stale approval)";
  const rights = external ? rightsRow(pr.body) : null;

  // ACCEPTANCE — what would this merge auto-close, and is every closed issue fully delivered?
  const acceptance = acceptanceFromIssues(readClosing(num));

  return {
    num, external, ok: cardOk({ valueOk, diffOk: d.ok, acceptance, humanTest, rights, blocking }),
    valueOk, valueWhy, diffOk: d.ok, diffWhy, acceptance, rights, humanTest,
  };
}

// Render one PR's card as GitHub-flavoured markdown. The leading marker lets the sticky-comment
// workflow find and update its own comment in place. This same markdown feeds the check summary.
export function renderCard(c) {
  if (c.skip) return `<!-- merge-card -->\n### 🃏 Merge card — #${c.num}\n\n_Skipped (${c.skip})._`;
  const row = (label, ok, why) => `| **${label}** | ${ok ? "✅" : "❌"} | ${why} |`;
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
    row(c.external ? "Peer review" : "Diff", c.diffOk, c.diffWhy),
    // The row exists only when the PR carries a closing reference — `Part of #N` closes nothing.
    ...(c.acceptance ? [row("Acceptance", c.acceptance.ok, c.acceptance.why)] : []),
    ...(c.rights ? [row("Rights", c.rights.ok, c.rights.why)] : []),
    // Guarded like the Acceptance row above it: this renderer is exported, so a caller that
    // builds a card without the row must get a three-row card, never a throw.
    ...(c.humanTest ? [row("Human test", c.humanTest.ok, c.humanTest.why)] : []),
    ``,
    verdict,
    ``,
    `<sub>How a PR reaches merge: [the merge bar](https://docs.vexa.ai/governance/delivery#integration-—-the-merge-bar).</sub>`,
  ].join("\n");
}

async function main() {
  const nums = prNumbers();
  if (!nums.length) { console.error("merge-card-gate: no PR number resolved from PR_NUMBERS / MERGE_GROUP_REF / GITHUB_EVENT_NAME + GITHUB_EVENT_PATH"); process.exit(2); }

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
