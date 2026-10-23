#!/usr/bin/env node
// DCO over a pull request's whole commit range, with no commit ceiling.
//
// The DCO App reads at most 250 commits over REST, and its GraphQL fallback fails, so on a long
// pull request (a release line carries hundreds) it can neither pass nor say which commit is wrong.
// This check walks the range with git itself, from full history.
//
// The rule, per CONTRIBUTOR_RIGHTS.md: every non-merge commit carries
//   Signed-off-by: <author name> <author email>
// matching that commit's author, or the same author adds a DCO App individual remediation commit
// inside the range naming it:
//   I, <author name> <author email>, hereby add my Signed-off-by to this commit: <sha>
// A sign-off by anyone else does not count, and nobody may remediate for another author.
// Merge commits are skipped, as the DCO App skips them.
//
// The range is the commits the pull request adds: reachable from its head, not from its base, and
// not from the default branch, whose commits already passed the default branch's own gates.
//
// Usage:
//   node scripts/dco-range.mjs --head <rev> --exclude <rev> [--exclude <rev> ...]
//   node scripts/dco-range.mjs            (in CI: reads the pull_request or merge_group event)
// Exit 0 when every commit is certified, 1 when any is not, 2 when the range cannot be read.
// The full commit list goes to stdout and, in Actions, to the job summary.

import { execFileSync } from "node:child_process";
import { appendFileSync, readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const SIGNOFF = /^Signed-off-by:[ \t]*(.*?)[ \t]*<([^<>\n]+)>[ \t]*$/gim;
const REMEDIATION = /^I,[ \t]*(.*?)[ \t]*<([^<>\n]+)>,[ \t]*hereby add my Signed-off-by to this commit:[ \t]*([0-9a-f]{7,40})[ \t]*$/gim;
// The step summary is capped at 1 MiB by Actions; the log is not.
const SUMMARY_BUDGET = 900 * 1024;

const norm = (value) => String(value ?? "").trim().toLowerCase();
const sameIdentity = (a, b) => norm(a.name) === norm(b.name) && norm(a.email) === norm(b.email);
const ident = ({ name, email }) => `${name} <${email}>`;

export function parseSignoffs(message = "") {
  return [...message.matchAll(SIGNOFF)].map(([, name, email]) => ({ name, email }));
}

export function parseRemediations(message = "") {
  return [...message.matchAll(REMEDIATION)].map(([, name, email, sha]) => ({ name, email, sha: sha.toLowerCase() }));
}

/**
 * commits: [{ sha, parents: [sha], author: { name, email }, subject, message }]
 * Returns { ok, rows, checked, merges, failing } with one row per commit, in the given order.
 */
export function evaluateCommits(commits) {
  const nonMerge = commits.filter((c) => c.parents.length <= 1);
  // Remediation statements count only when the commit carrying them is itself signed off by its
  // author and names that same author as the one certifying.
  const remediations = [];
  for (const c of nonMerge) {
    if (!parseSignoffs(c.message).some((s) => sameIdentity(s, c.author))) continue;
    for (const r of parseRemediations(c.message)) {
      if (sameIdentity(r, c.author)) remediations.push({ ...r, by: c.sha });
    }
  }

  const rows = commits.map((c) => {
    if (c.parents.length > 1) return { ...c, verdict: "merge", ok: true, detail: "merge commit, skipped" };
    const signoffs = parseSignoffs(c.message);
    if (signoffs.some((s) => sameIdentity(s, c.author))) return { ...c, verdict: "signed", ok: true, detail: "signed off by the author" };
    const fix = remediations.find((r) => c.sha.toLowerCase().startsWith(r.sha) && sameIdentity(r, c.author));
    if (fix) return { ...c, verdict: "remediated", ok: true, detail: `remediated by the author in ${fix.by.slice(0, 12)}` };
    if (signoffs.length) {
      return { ...c, verdict: "signer-mismatch", ok: false, detail: `signed off by ${signoffs.map(ident).join(", ")}, not by the author` };
    }
    return { ...c, verdict: "unsigned", ok: false, detail: "no Signed-off-by" };
  });

  const failing = rows.filter((r) => !r.ok);
  return { ok: failing.length === 0, rows, checked: nonMerge.length, merges: commits.length - nonMerge.length, failing };
}

function git(args, cwd) {
  return execFileSync("git", args, { cwd, encoding: "utf8", maxBuffer: Infinity, stdio: ["ignore", "pipe", "pipe"] });
}

const hasCommit = (rev, cwd) => {
  try {
    git(["cat-file", "-e", `${rev}^{commit}`], cwd);
    return true;
  } catch {
    return false;
  }
};

function shallowBoundary(cwd) {
  try {
    return new Set(readFileSync(git(["rev-parse", "--git-path", "shallow"], cwd).trim().replace(/^(?!\/)/, `${cwd}/`), "utf8").split("\n").filter(Boolean));
  } catch {
    return new Set();
  }
}

/** Every commit reachable from head and from none of the excluded revisions, newest first. */
export function readRange({ head, exclude = [], cwd = process.cwd() }) {
  const out = git(["log", "--format=%x1e%H%x1f%P%x1f%an%x1f%ae%x1f%s%x1f%B", head, ...exclude.map((x) => `^${x}`), "--"], cwd);
  return out
    .split("\x1e")
    .filter((record) => record.trim())
    .map((record) => {
      const [sha, parents, name, email, subject, message] = record.split("\x1f");
      return { sha, parents: parents.split(" ").filter(Boolean), author: { name, email }, subject, message: message ?? "" };
    });
}

const cell = (text) => String(text).replace(/\|/g, "\\|").replace(/[\r\n]+/g, " ");
const subjectOf = (row) => (row.subject.length > 80 ? `${row.subject.slice(0, 77)}...` : row.subject);
const tableRow = (row) => `| \`${row.sha.slice(0, 12)}\` | ${cell(ident(row.author))} | ${row.ok ? "ok" : "**FAIL**"}: ${cell(row.detail)} | ${cell(subjectOf(row))} |`;
const HEADER = ["| Commit | Author | Verdict | Subject |", "|---|---|---|---|"];

export function renderReport(result, { head, exclude }) {
  const lines = [
    `## dco-range: ${result.ok ? "PASS" : "FAIL"}`,
    "",
    `Range \`${head}\` excluding ${exclude.map((x) => `\`${x}\``).join(", ") || "nothing"}: ` +
      `${result.checked} commits checked, ${result.merges} merge commits skipped, ${result.failing.length} failing. No commit ceiling.`,
    "",
  ];
  if (result.failing.length) {
    lines.push(
      "### Failing commits",
      "",
      ...HEADER,
      ...result.failing.map(tableRow),
      "",
      "Fix: sign off each commit as its author (`git commit --amend --signoff`, or `git rebase --signoff <base>` on a branch only you use),",
      "or, without rewriting history, have the author add an empty commit, signed off by them, whose message has one line per commit:",
      "",
      "```",
      "I, <author name> <author email>, hereby add my Signed-off-by to this commit: <sha>",
      "```",
      "",
      "See CONTRIBUTOR_RIGHTS.md, *Fixing a DCO failure*.",
      "",
    );
  }
  lines.push("### Every commit in the range", "", ...HEADER, ...result.rows.map(tableRow), "");
  return lines.join("\n");
}

function fitSummary(report) {
  if (Buffer.byteLength(report) <= SUMMARY_BUDGET) return report;
  const cut = report.slice(0, SUMMARY_BUDGET);
  return `${cut.slice(0, cut.lastIndexOf("\n"))}\n\n_The list continues in the job log; the step summary is capped at 1 MiB._\n`;
}

function fromEvent(env, cwd) {
  const event = JSON.parse(readFileSync(env.GITHUB_EVENT_PATH, "utf8"));
  const defaultBranch = event.repository?.default_branch;
  const exclude = [];
  let head;
  if (env.GITHUB_EVENT_NAME === "pull_request" || env.GITHUB_EVENT_NAME === "pull_request_target") {
    head = event.pull_request.head.sha;
    exclude.push(event.pull_request.base.sha);
    // A fork's head is reachable only through the pull request ref.
    if (!hasCommit(head, cwd)) git(["fetch", "--no-tags", "--quiet", "origin", `+refs/pull/${event.pull_request.number}/head`], cwd);
  } else if (env.GITHUB_EVENT_NAME === "merge_group") {
    // The queue's own commit is a squash GitHub writes; certify the pull request's commits instead.
    const number = /\/pr-(\d+)-/.exec(event.merge_group.head_ref)?.[1];
    if (!number) throw new Error(`cannot read a pull request number from ${event.merge_group.head_ref}`);
    git(["fetch", "--no-tags", "--quiet", "origin", `+refs/pull/${number}/head:refs/dco-range/pr-${number}`], cwd);
    head = `refs/dco-range/pr-${number}`;
    exclude.push(event.merge_group.base_sha);
  } else {
    throw new Error(`dco-range runs on pull_request or merge_group, not ${env.GITHUB_EVENT_NAME}`);
  }
  if (defaultBranch && hasCommit(`refs/remotes/origin/${defaultBranch}`, cwd)) exclude.push(`origin/${defaultBranch}`);
  for (const rev of exclude) {
    if (!hasCommit(rev, cwd)) git(["fetch", "--no-tags", "--quiet", "origin", rev], cwd);
  }
  return { head, exclude };
}

function parseArgs(argv) {
  const out = { exclude: [] };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--head") out.head = argv[++i];
    else if (argv[i] === "--exclude") out.exclude.push(argv[++i]);
    else throw new Error(`unknown argument ${argv[i]}`);
  }
  return out;
}

function main() {
  const cwd = process.cwd();
  const args = parseArgs(process.argv.slice(2));
  const range = args.head ? args : fromEvent(process.env, cwd);
  const commits = readRange({ ...range, cwd });
  // In a shallow clone the walk stops at the boundary and silently drops the rest of the range.
  const boundary = shallowBoundary(cwd);
  const cut = commits.find((c) => boundary.has(c.sha));
  if (cut) throw new Error(`the range runs past the shallow clone boundary at ${cut.sha.slice(0, 12)}; check out with fetch-depth: 0`);
  const result = evaluateCommits(commits);
  const report = renderReport(result, range);
  console.log(report);
  for (const row of result.failing) {
    console.log(`::error title=DCO ${row.sha.slice(0, 12)}::${ident(row.author)}: ${row.detail}`);
  }
  if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY, fitSummary(report));
  process.exitCode = result.ok ? 0 : 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    main();
  } catch (error) {
    console.error(error);
    console.log(`::error title=dco-range could not read the range::${String(error.message).split("\n")[0]}`);
    process.exitCode = 2;
  }
}
