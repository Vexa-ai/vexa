// Unit tests for pr-files.mjs and the gates that read a PR's file list through it.
// Run: node --test scripts/pr-files.test.mjs
//
// #2008 regression: the PR changes 5,143 files, 475 of them docs pages past the 2,000th. The REST
// list stops at 3,000 files and docs-current paged only 2,000, so it reported "changes no docs/**"
// on a PR that changes 475 docs files. A gate must judge every changed file or fail, never a part.

import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync, renameSync, unlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { prFiles, gitDiffFiles, API_FILE_CAP } from "./pr-files.mjs";
import { card as docsCard } from "./docs-current-gate.mjs";

const BASE = "b".repeat(40), HEAD = "h".repeat(40), MB = "m".repeat(40);
const names = (n, at) => Array.from({ length: n }, (_, i) => `${at}/f${i}.py`);

// A stand-in for the GitHub API that behaves like the real list: 100 per page, nothing past 3,000.
function fakeApi(pr, all, extra = () => undefined) {
  const api = (path) => {
    api.calls.push(path);
    let m;
    if (/\/pulls\/\d+$/.test(path)) return pr;
    if ((m = path.match(/\/pulls\/\d+\/files\?per_page=100&page=(\d+)$/))) {
      const from = (m[1] - 1) * 100;
      return from >= API_FILE_CAP ? [] : all.slice(from, Math.min(from + 100, API_FILE_CAP)).map((filename) => ({ filename, status: "modified" }));
    }
    if (path.includes("/compare/")) return { merge_base_commit: { sha: MB } };
    const r = extra(path);
    if (r !== undefined) return r;
    throw new Error(`unexpected API path ${path}`);
  };
  api.calls = [];
  return api;
}
const pr = (changed, o = {}) => ({ number: 2008, draft: false, changed_files: changed, base: { sha: BASE }, head: { sha: HEAD }, labels: [], user: { login: "maint" }, comments: 0, ...o });
const gitOf = (all) => { const g = (b, h) => { g.calls.push([b, h]); return all.map((filename) => ({ filename, status: "modified" })); }; g.calls = []; return g; };

test("a list the API returns whole is used as is; git is never asked", () => {
  const all = names(250, "core");
  const git = gitOf([]);
  const got = prFiles(pr(250), { repo: "o/r", api: fakeApi(pr(250), all), git });
  assert.equal(got.length, 250);
  assert.equal(git.calls.length, 0);
});

test("a PR past the API cap is read from git, merge base → head (#2008's shape)", () => {
  const all = [...names(3000, "core"), ...names(2143, "docs")];
  const git = gitOf(all);
  const api = fakeApi(pr(5143), all);
  const got = prFiles(pr(5143), { repo: "o/r", api, git });
  assert.equal(got.length, 5143);
  assert.deepEqual(git.calls, [[MB, HEAD]]);
  assert.equal(api.calls.filter((p) => p.includes("/files?")).length, 30, "the API is paged to its cap, not to 10 or 20 pages");
});

test("between 1,000 and 3,000 files the API list is enough (the old 10- and 20-page loops cut it)", () => {
  const all = names(2500, "core");
  const git = gitOf([]);
  assert.equal(prFiles(pr(2500), { repo: "o/r", api: fakeApi(pr(2500), all), git }).length, 2500);
  assert.equal(git.calls.length, 0);
});

test("when git also can't account for every changed file, it throws instead of judging part", () => {
  const all = names(4000, "core");
  assert.throws(() => prFiles(pr(4000), { repo: "o/r", api: fakeApi(pr(4000), all), git: gitOf(all.slice(0, 3500)) }), /refusing to judge part of the diff/);
});

test("docs-current: the #2008 shape passes on its docs pages past the 3,000th file", () => {
  const all = [...names(4668, "core"), ...names(475, "docs")];
  const c = docsCard(2008, { repo: "o/r", api: fakeApi(pr(5143), all), git: gitOf(all) });
  assert.equal(c.ok, true);
  assert.match(c.why, /docs\/\*\* updated/);
});

test("docs-current: the same surface change with no docs anywhere still fails (the gate is not loosened)", () => {
  const all = names(5143, "core");
  const c = docsCard(2008, { repo: "o/r", api: fakeApi(pr(5143), all), git: gitOf(all) });
  assert.equal(c.ok, false);
  assert.match(c.why, /changes no docs/);
});

test("docs-current: an evaluation error surfaces (git short of the count) rather than a verdict", () => {
  const all = [...names(4668, "core"), ...names(475, "docs")];
  assert.throws(() => docsCard(2008, { repo: "o/r", api: fakeApi(pr(5143), all), git: gitOf(all.slice(0, 3000)) }));
});

// The git path itself, against a real repository: shallow, blob-less, renames as remove + add.
test("gitDiffFiles reads names and statuses from a blob-less shallow fetch", () => {
  const dir = mkdtempSync(join(tmpdir(), "pr-files-"));
  try {
    const env = { ...process.env, GIT_AUTHOR_NAME: "t", GIT_AUTHOR_EMAIL: "t@t", GIT_COMMITTER_NAME: "t", GIT_COMMITTER_EMAIL: "t@t", GIT_CONFIG_NOSYSTEM: "1", HOME: dir };
    const git = (cwd, ...args) => execFileSync("git", args, { cwd, env, encoding: "utf8" }).trim();
    const origin = join(dir, "origin");
    mkdirSync(join(origin, "docs"), { recursive: true });
    mkdirSync(join(origin, "core"));
    git(origin, "init", "-q", "-b", "main");
    git(origin, "config", "uploadpack.allowFilter", "true");
    git(origin, "config", "uploadpack.allowAnySHA1InWant", "true");
    writeFileSync(join(origin, "keep.txt"), "1\n");
    writeFileSync(join(origin, "docs/a.md"), "a\n");
    writeFileSync(join(origin, "core/mv.py"), "print(1)\n");
    git(origin, "add", "-A"); git(origin, "commit", "-q", "-m", "base");
    const base = git(origin, "rev-parse", "HEAD");
    writeFileSync(join(origin, "keep.txt"), "2\n");
    writeFileSync(join(origin, "docs/new.mdx"), "n\n");
    unlinkSync(join(origin, "docs/a.md"));
    mkdirSync(join(origin, "other"));
    renameSync(join(origin, "core/mv.py"), join(origin, "other/mv.py"));
    git(origin, "add", "-A"); git(origin, "commit", "-q", "-m", "head");
    const head = git(origin, "rev-parse", "HEAD");

    const clone = join(dir, "clone");
    execFileSync("git", ["clone", "-q", "--depth=1", `file://${origin}`, clone], { env });
    const got = gitDiffFiles(base, head, { cwd: clone }).sort((a, b) => a.filename.localeCompare(b.filename));
    assert.deepEqual(got, [
      { filename: "core/mv.py", status: "removed" },
      { filename: "docs/a.md", status: "removed" },
      { filename: "docs/new.mdx", status: "added" },
      { filename: "keep.txt", status: "modified" },
      { filename: "other/mv.py", status: "added" },
    ]);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
