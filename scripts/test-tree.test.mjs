// The rules in scripts/test-tree.mjs, held: every gate-script test file is guarded, the guard fails
// a test that leaves its tree changed (and only that), and a private copy is the checkout as a gate
// sees it, writable without the checkout noticing.
// Run: node --test scripts/test-tree.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { guardTree, REPO, sandboxTree } from "./test-tree.mjs";

guardTree();

const TEST_DIRS = ["scripts", "release"];

test("every *.test.mjs under scripts/ and release/ calls guardTree()", () => {
  const unguarded = [];
  for (const dir of TEST_DIRS) {
    for (const f of readdirSync(join(REPO, dir)).filter((n) => n.endsWith(".test.mjs"))) {
      const text = readFileSync(join(REPO, dir, f), "utf8");
      if (!/^guardTree\(\);?$/m.test(text) || !/from "(\.\/|\.\.\/scripts\/)test-tree\.mjs"/.test(text)) unguarded.push(`${dir}/${f}`);
    }
  }
  assert.deepEqual(unguarded, [], "a test file that does not guard the tree can write into it unseen");
});

// A scratch repository holding one committed file, and a test file that guards THAT repository.
function runGuarded(body) {
  const repo = mkdtempSync(join(tmpdir(), "vexa-guard-"));
  try {
    const git = (...args) => execFileSync("git", ["-c", "core.hooksPath=/dev/null", ...args], { cwd: repo, stdio: "pipe" });
    git("init", "-q");
    writeFileSync(join(repo, "tracked.txt"), "one\n");
    git("add", "-A");
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "fixture");
    const spec = join(repo, "fixture.test.mjs");
    writeFileSync(spec, [
      `import test from "node:test";`,
      `import { writeFileSync, rmSync } from "node:fs";`,
      `import { guardTree } from ${JSON.stringify(join(REPO, "scripts", "test-tree.mjs"))};`,
      `const REPO = ${JSON.stringify(repo)};`,
      `guardTree(REPO);`,
      body,
    ].join("\n"));
    // the fixture spec itself is untracked: commit it, so only what a TEST does is a change
    git("add", "-A");
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "spec");
    // A `node --test` started from inside a test inherits NODE_TEST_CONTEXT and reports to a parent
    // instead of exiting non-zero on a failure; this one must stand alone.
    const env = { ...process.env };
    delete env.NODE_TEST_CONTEXT;
    try {
      return { ok: true, out: execFileSync("node", ["--test", spec], { cwd: repo, encoding: "utf8", stdio: "pipe", env }) };
    } catch (e) {
      return { ok: false, out: `${e.stdout || ""}${e.stderr || ""}` };
    }
  } finally {
    rmSync(repo, { recursive: true, force: true });
  }
}

test("vacuity: a test that leaves the tree as it found it passes the guard", () => {
  const r = runGuarded(`test("reads only", () => {});`);
  assert.equal(r.ok, true, r.out);
});

test("a test that leaves a planted file behind fails, named, with the path", () => {
  const r = runGuarded(`test("plants and forgets", () => { writeFileSync(REPO + "/planted.txt", "x"); });`);
  assert.equal(r.ok, false, r.out);
  assert.match(r.out, /plants and forgets/);
  assert.match(r.out, /planted\.txt/);
});

test("a test that edits a tracked file and leaves it edited fails", () => {
  const r = runGuarded(`test("edits", () => { writeFileSync(REPO + "/tracked.txt", "two\\n"); });`);
  assert.equal(r.ok, false, r.out);
  assert.match(r.out, /tracked\.txt/);
});

test("a test that plants and restores before it ends passes (the window is the sandbox's job)", () => {
  const r = runGuarded(`test("plants and cleans", () => { writeFileSync(REPO + "/p.txt", "x"); rmSync(REPO + "/p.txt"); });`);
  assert.equal(r.ok, true, r.out);
});

test("the private copy is the checkout as a gate reads it, and writing to it leaves the checkout alone", () => {
  const box = sandboxTree();
  assert.notEqual(box, REPO);
  for (const f of ["scripts/gates.mjs", "package.json", "architecture.calm.json"])
    assert.equal(readFileSync(join(box, f), "utf8"), readFileSync(join(REPO, f), "utf8"), f);
  assert.ok(existsSync(join(box, "node_modules")), "the root node_modules is linked in");
  const listed = execFileSync("git", ["ls-files", "scripts/gates.mjs"], { cwd: box, encoding: "utf8" }).trim();
  assert.equal(listed, "scripts/gates.mjs", "the copy is a repository whose index holds the tree");
  writeFileSync(join(box, "zz-sandbox-only.txt"), "x");
  assert.equal(existsSync(join(REPO, "zz-sandbox-only.txt")), false);
});
