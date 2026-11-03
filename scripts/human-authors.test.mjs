import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { scanCommit, scanPr } from "./human-authors.mjs";
import { guardTree } from "./test-tree.mjs";

guardTree();

const HUMAN = {
  sha: "a".repeat(40), an: "DmitriyG228", ae: "2280905@gmail.com", cn: "DmitriyG228", ce: "2280905@gmail.com",
  body: "fix: a thing\n\nSigned-off-by: DmitriyG228 <2280905@gmail.com>\n",
};
const commit = (kw) => ({ ...HUMAN, ...kw });
const patterns = (c) => scanCommit(c).map((f) => f.pattern);

test("a clean commit passes", () => assert.deepEqual(scanCommit(HUMAN), []));
test("a human Co-authored-by trailer passes", () =>
  assert.deepEqual(scanCommit(commit({ body: HUMAN.body + "Co-authored-by: Jane Doe <jane@example.com>\n" })), []));
test("a Co-Authored-By trailer naming Claude fails", () =>
  assert.deepEqual(patterns(commit({ body: HUMAN.body + "Co-Authored-By: Claude <noreply@example.com>\n" })), ["co-authored-by-claude"]));
test("a co-authored-by trailer naming Anthropic fails, any case", () =>
  assert.deepEqual(patterns(commit({ body: "x\n\nco-authored-by: Bot <bot@ANTHROPIC.com>\n" })), ["co-authored-by-claude"]));
test("an author email at anthropic.com fails", () =>
  assert.deepEqual(patterns(commit({ ae: "noreply@anthropic.com" })), ["author-email-anthropic"]));
test("a committer email at a subdomain of anthropic.com fails", () =>
  assert.deepEqual(patterns(commit({ ce: "x@mail.Anthropic.com" })), ["committer-email-anthropic"]));
test("an author named Claude fails", () => assert.deepEqual(patterns(commit({ an: "Claude" })), ["author-name-claude"]));
test("a committer named claude fails", () => assert.deepEqual(patterns(commit({ cn: "claude" })), ["committer-name-claude"]));
test("the generated-with footer fails", () =>
  assert.deepEqual(patterns(commit({ body: "x\n\nGenerated with [Claude Code](https://claude.com/claude-code)\n" })), ["generated-with"]));
test("the robot generated-with marker fails", () =>
  assert.deepEqual(patterns(commit({ body: "x\n\n\u{1F916} Generated with some tool\n" })), ["generated-with"]));
test("a finding carries the sha and the line", () =>
  assert.deepEqual(scanCommit(commit({ body: "x\n\nCo-Authored-By: Claude Opus <noreply@anthropic.com>\n" })),
    [{ where: "a".repeat(40), pattern: "co-authored-by-claude", line: "Co-Authored-By: Claude Opus <noreply@anthropic.com>" }]));

test("a clean PR title and body pass", () => assert.deepEqual(scanPr({ pull_request: { title: "ci: add check", body: "Plain." } }), []));
test("the footer in a PR body fails", () =>
  assert.deepEqual(scanPr({ pull_request: { title: "t", body: "Text\n\nGenerated with Claude Code" } }).map((f) => f.pattern), ["generated-with"]));
test("a trailer in a PR title fails", () =>
  assert.deepEqual(scanPr({ pull_request: { title: "Co-Authored-By: Claude", body: null } }).map((f) => f.where), ["pr-title"]));

// End to end over a real git range, in a private repository under the OS temp dir.
const SCRIPT = fileURLToPath(new URL("./human-authors.mjs", import.meta.url));
function repo() {
  const dir = mkdtempSync(join(tmpdir(), "human-authors-"));
  const git = (args, env = {}) => execFileSync("git", args, { cwd: dir, encoding: "utf8", env: { ...process.env, ...env } }).trim();
  // plumbing, so no commit hook of the host judges the fixture
  let tip = null;
  const mk = (msg, name = "Jane", email = "jane@example.com") => {
    const tree = git(["hash-object", "-t", "tree", "-w", "/dev/null"]);
    tip = git(["commit-tree", tree, ...(tip ? ["-p", tip] : []), "-m", msg],
      { GIT_AUTHOR_NAME: name, GIT_AUTHOR_EMAIL: email, GIT_COMMITTER_NAME: name, GIT_COMMITTER_EMAIL: email });
    return tip;
  };
  git(["init", "-q"]);
  const run = (base, head, event) => {
    const args = [SCRIPT, base, head];
    if (event) { const p = join(dir, "event.json"); writeFileSync(p, JSON.stringify(event)); args.push("--event", p); }
    return spawnSync(process.execPath, args, { cwd: dir, encoding: "utf8" });
  };
  return { dir, mk, run, base: mk("base"), done: () => rmSync(dir, { recursive: true, force: true }) };
}

test("a clean range exits 0", () => {
  const r = repo();
  try {
    const head = r.mk("feat: x\n\nCo-authored-by: Bob <bob@example.com>");
    const out = r.run(r.base, head, { pull_request: { title: "x", body: "y" } });
    assert.equal(out.status, 0, out.stdout + out.stderr);
  } finally { r.done(); }
});

test("an offending commit is printed with its sha and patterns", () => {
  const r = repo();
  try {
    const bad = r.mk("feat: x", "Claude", "noreply@anthropic.com");
    const head = r.mk("feat: y");
    const out = r.run(r.base, head);
    assert.equal(out.status, 1);
    assert.match(out.stdout, new RegExp(bad));
    assert.match(out.stdout, /author-email-anthropic/);
    assert.match(out.stdout, /author-name-claude/);
  } finally { r.done(); }
});

test("a PR body alone fails the range", () => {
  const r = repo();
  try {
    const head = r.mk("feat: x");
    const out = r.run(r.base, head, { pull_request: { title: "x", body: "\u{1F916} Generated with X" } });
    assert.equal(out.status, 1);
    assert.match(out.stdout, /pr-body/);
  } finally { r.done(); }
});
