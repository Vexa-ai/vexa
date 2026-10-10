// dco-range: the whole-range DCO check. Run: node --test scripts/dco-range.test.mjs
//
// Every failing case is planted in a throwaway git repository and read back through git, so the
// assertions cover the same path CI runs: an unsigned commit, a sign-off by someone other than the
// author, a remediation by the wrong person, and a range longer than the DCO App's 250-commit read.
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { evaluateCommits, parseRemediations, parseSignoffs, readRange } from "./dco-range.mjs";

const SCRIPT = join(dirname(fileURLToPath(import.meta.url)), "dco-range.mjs");
const ADA = { name: "Ada Author", email: "ada@example.com" };
const BOB = { name: "Bob Other", email: "bob@example.com" };
const signoff = (who) => `Signed-off-by: ${who.name} <${who.email}>`;
const remediation = (who, sha) => `I, ${who.name} <${who.email}>, hereby add my Signed-off-by to this commit: ${sha}`;

function repo(t) {
  const dir = mkdtempSync(join(tmpdir(), "dco-range-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const run = (args, env = {}) =>
    execFileSync("git", args, {
      cwd: dir,
      encoding: "utf8",
      env: { ...process.env, GIT_CONFIG_GLOBAL: "/dev/null", GIT_CONFIG_NOSYSTEM: "1", ...env },
    }).trim();
  run(["init", "-q", "-b", "main"]);
  const commit = (author, ...lines) => {
    const env = { GIT_AUTHOR_NAME: author.name, GIT_AUTHOR_EMAIL: author.email, GIT_COMMITTER_NAME: author.name, GIT_COMMITTER_EMAIL: author.email };
    run(["commit", "-q", "--allow-empty", "--no-verify", "-m", lines.join("\n\n")], env);
    return run(["rev-parse", "HEAD"]);
  };
  return { dir, run, commit };
}

test("parses sign-off and remediation trailers", () => {
  const msg = `subject\n\nbody\n\n${signoff(ADA)}\nSigned-off-by: Bob Other<bob@example.com>`;
  assert.deepEqual(parseSignoffs(msg), [ADA, BOB]);
  assert.deepEqual(parseRemediations(`${remediation(ADA, "ABCDEF1")}\n${remediation(ADA, "a".repeat(40))}`), [
    { ...ADA, sha: "abcdef1" },
    { ...ADA, sha: "a".repeat(40) },
  ]);
  assert.deepEqual(parseSignoffs("Reviewed-by: Ada Author <ada@example.com>"), []);
});

test("passes a range where every commit is signed off by its author", (t) => {
  const r = repo(t);
  const base = r.commit(ADA, "base", signoff(ADA));
  r.commit(ADA, "one", signoff(ADA));
  r.commit({ name: "ada author", email: "ADA@example.com" }, "two, identity differs only in case", signoff(ADA));
  const result = evaluateCommits(readRange({ head: "HEAD", exclude: [base], cwd: r.dir }));
  assert.equal(result.ok, true);
  assert.equal(result.checked, 2);
});

test("planted: an unsigned commit fails", (t) => {
  const r = repo(t);
  const base = r.commit(ADA, "base", signoff(ADA));
  r.commit(ADA, "signed", signoff(ADA));
  const unsigned = r.commit(ADA, "planted unsigned commit");
  const result = evaluateCommits(readRange({ head: "HEAD", exclude: [base], cwd: r.dir }));
  assert.equal(result.ok, false);
  assert.deepEqual(result.failing.map((row) => [row.sha, row.verdict]), [[unsigned, "unsigned"]]);
});

test("planted: a sign-off by someone other than the author fails", (t) => {
  const r = repo(t);
  const base = r.commit(ADA, "base", signoff(ADA));
  const mismatched = r.commit(ADA, "planted mismatched signer", signoff(BOB));
  const result = evaluateCommits(readRange({ head: "HEAD", exclude: [base], cwd: r.dir }));
  assert.equal(result.ok, false);
  assert.deepEqual(result.failing.map((row) => [row.sha, row.verdict]), [[mismatched, "signer-mismatch"]]);
  assert.match(result.failing[0].detail, /Bob Other <bob@example.com>/);
});

test("the same name with another email is still a mismatch", (t) => {
  const r = repo(t);
  const base = r.commit(ADA, "base", signoff(ADA));
  r.commit(ADA, "signed with another address", signoff({ name: ADA.name, email: "ada@elsewhere.example" }));
  assert.equal(evaluateCommits(readRange({ head: "HEAD", exclude: [base], cwd: r.dir })).ok, false);
});

test("the author's own remediation commit certifies the commits it names", (t) => {
  const r = repo(t);
  const base = r.commit(ADA, "base", signoff(ADA));
  const unsigned = r.commit(ADA, "unsigned");
  const mismatched = r.commit(ADA, "mismatched", signoff(BOB));
  const fix = r.commit(ADA, `DCO Remediation Commit for ${ADA.name} <${ADA.email}>`, `${remediation(ADA, unsigned)}\n${remediation(ADA, mismatched.slice(0, 9))}`, signoff(ADA));
  const result = evaluateCommits(readRange({ head: "HEAD", exclude: [base], cwd: r.dir }));
  assert.equal(result.ok, true);
  const verdicts = Object.fromEntries(result.rows.map((row) => [row.sha, row.verdict]));
  assert.equal(verdicts[unsigned], "remediated");
  assert.equal(verdicts[mismatched], "remediated");
  assert.equal(verdicts[fix], "signed");
});

test("nobody remediates for another author, and an unsigned remediation counts for nothing", (t) => {
  const r = repo(t);
  const base = r.commit(ADA, "base", signoff(ADA));
  const unsigned = r.commit(ADA, "unsigned");
  r.commit(BOB, "Bob certifies for Ada", remediation(ADA, unsigned), signoff(BOB));
  r.commit(BOB, "Bob names himself", remediation(BOB, unsigned), signoff(BOB));
  r.commit(ADA, "Ada forgets to sign the remediation", remediation(ADA, unsigned));
  const result = evaluateCommits(readRange({ head: "HEAD", exclude: [base], cwd: r.dir }));
  assert.equal(result.ok, false);
  assert.deepEqual(result.failing.map((row) => row.sha).sort(), [unsigned, r.run(["rev-parse", "HEAD"])].sort());
});

test("merge commits are skipped and commits already on the default branch are excluded", (t) => {
  const r = repo(t);
  const base = r.commit(ADA, "base", signoff(ADA));
  r.run(["checkout", "-q", "-b", "feature"]);
  r.commit(ADA, "feature work", signoff(ADA));
  r.run(["checkout", "-q", "main"]);
  r.commit(BOB, "already on main, signed by someone else", signoff(ADA));
  r.run(["checkout", "-q", "feature"]);
  r.run(["merge", "-q", "--no-ff", "--no-edit", "main"], { GIT_AUTHOR_NAME: ADA.name, GIT_AUTHOR_EMAIL: ADA.email, GIT_COMMITTER_NAME: ADA.name, GIT_COMMITTER_EMAIL: ADA.email });
  const withMain = evaluateCommits(readRange({ head: "feature", exclude: [base], cwd: r.dir }));
  assert.equal(withMain.ok, false, "without the default-branch exclusion the commit from main is in range");
  const result = evaluateCommits(readRange({ head: "feature", exclude: [base, "main"], cwd: r.dir }));
  assert.equal(result.ok, true);
  assert.equal(result.merges, 1);
  assert.equal(result.checked, 1);
});

test("no ceiling: an unsigned commit beneath 300 signed ones is still found", (t) => {
  const r = repo(t);
  const base = r.commit(ADA, "base", signoff(ADA));
  const buried = r.commit(ADA, "buried unsigned commit");
  // fast-import writes the 300 commits in one process.
  const when = 1700000000;
  let stream = "";
  for (let i = 0; i < 300; i += 1) {
    const msg = `commit ${i}\n\n${signoff(ADA)}\n`;
    stream += `commit refs/heads/main\nauthor ${ADA.name} <${ADA.email}> ${when + i} +0000\ncommitter ${ADA.name} <${ADA.email}> ${when + i} +0000\n`;
    stream += `data ${Buffer.byteLength(msg)}\n${msg}${i === 0 ? `from ${buried}\n` : ""}\n`;
  }
  execFileSync("git", ["fast-import", "--quiet"], { cwd: r.dir, input: stream });
  const result = evaluateCommits(readRange({ head: "main", exclude: [base], cwd: r.dir }));
  assert.equal(result.checked, 301);
  assert.deepEqual(result.failing.map((row) => row.sha), [buried]);
});

test("the CLI exits 1 on a planted failure, lists every commit, and writes the job summary", (t) => {
  const r = repo(t);
  const base = r.commit(ADA, "base", signoff(ADA));
  r.commit(ADA, "good", signoff(ADA));
  const bad = r.commit(ADA, "bad", signoff(BOB));
  const summary = join(r.dir, ".git", "summary.md");
  writeFileSync(summary, "");
  const cli = (...args) => spawnSync(process.execPath, [SCRIPT, ...args], { cwd: r.dir, encoding: "utf8", env: { ...process.env, GITHUB_STEP_SUMMARY: summary } });
  const red = cli("--head", "HEAD", "--exclude", base);
  assert.equal(red.status, 1, red.stderr);
  assert.match(red.stdout, /## dco-range: FAIL/);
  assert.match(red.stdout, new RegExp(`::error title=DCO ${bad.slice(0, 12)}::`));
  assert.match(readFileSync(summary, "utf8"), /### Every commit in the range/);
  assert.equal(readFileSync(summary, "utf8").match(/^\| `[0-9a-f]{12}` \|/gm).length, 3, "failing row plus both commits in the full list");
  const green = cli("--head", "HEAD~1", "--exclude", base);
  assert.equal(green.status, 0, green.stdout);
  assert.match(green.stdout, /## dco-range: PASS/);
});

test("in CI it reads the pull_request event and excludes the base and the default branch", (t) => {
  const r = repo(t);
  const onMain = r.commit(BOB, "on main, signed by someone else", signoff(ADA));
  r.run(["update-ref", "refs/remotes/origin/main", onMain]);
  r.run(["checkout", "-q", "-b", "release-line"]);
  const base = r.commit(ADA, "base of the release line", signoff(ADA));
  const head = r.commit(ADA, "the pull request's commit", signoff(ADA));
  const eventPath = join(r.dir, ".git", "event.json");
  const event = (headSha) => ({ repository: { default_branch: "main" }, pull_request: { number: 1, head: { sha: headSha }, base: { sha: base } } });
  const cli = (headSha) => {
    writeFileSync(eventPath, JSON.stringify(event(headSha)));
    return spawnSync(process.execPath, [SCRIPT], { cwd: r.dir, encoding: "utf8", env: { ...process.env, GITHUB_EVENT_NAME: "pull_request", GITHUB_EVENT_PATH: eventPath, GITHUB_STEP_SUMMARY: "" } });
  };
  const green = cli(head);
  assert.equal(green.status, 0, green.stdout + green.stderr);
  assert.match(green.stdout, /1 commits checked/);
  const planted = r.commit(ADA, "planted unsigned commit on the pull request");
  const red = cli(planted);
  assert.equal(red.status, 1);
  assert.match(red.stdout, new RegExp(planted.slice(0, 12)));
});

test("a range that runs past a shallow clone's boundary is an error, not a pass", (t) => {
  const r = repo(t);
  const base = r.commit(ADA, "base", signoff(ADA));
  r.commit(ADA, "unsigned, beyond the shallow boundary");
  r.commit(ADA, "one", signoff(ADA));
  r.commit(ADA, "two", signoff(ADA));
  const shallow = mkdtempSync(join(tmpdir(), "dco-range-shallow-"));
  t.after(() => rmSync(shallow, { recursive: true, force: true }));
  execFileSync("git", ["clone", "-q", "--depth", "2", `file://${r.dir}`, shallow], { env: { ...process.env, GIT_CONFIG_GLOBAL: "/dev/null" } });
  const cli = (...args) => spawnSync(process.execPath, [SCRIPT, ...args], { cwd: shallow, encoding: "utf8", env: { ...process.env, GITHUB_STEP_SUMMARY: "" } });
  const cut = cli("--head", "HEAD");
  assert.equal(cut.status, 2, cut.stdout);
  assert.match(cut.stdout, /shallow clone boundary/);
  assert.equal(cli("--head", "HEAD", "--exclude", base).status, 2, "a base the clone does not have is an error too");
  assert.equal(cli("--head", "HEAD", "--exclude", "HEAD~1").status, 0, "a range inside the fetched depth is complete");
});
