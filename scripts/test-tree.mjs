/**
 * test-tree.mjs — the two rules every gate-script test file lives by.
 *
 * `node --test scripts/*.test.mjs release/*.test.mjs` runs each FILE in its own process, in
 * parallel, over ONE checkout. A test that plants a fixture into that checkout, or edits a tracked
 * file and restores it in a `finally`, has opened a write window every other file can read through:
 * on 2026-10-10 `publish-edge.test.mjs` ran gate:config-contract over the real tree while
 * `gates.test.mjs` had `VEXA_PHANTOM_ENTRY` planted in deploy/lite/entrypoint.sh and a phantom env
 * read in the terminal's mailer, and failed on a sabotage it did not make. A restore in `finally`
 * makes the END state right; it does nothing about the window.
 *
 *   1. `sandboxTree()` — a test that has to change the tree it asserts about changes a PRIVATE COPY.
 *      The copy is the checkout as a gate sees it: every tracked and untracked-but-not-ignored file,
 *      as it is on disk now (uncommitted edits included), in a fresh git repository whose index
 *      holds them (`git grep --untracked` and `git ls-files` work), with each package's
 *      `node_modules` linked back to the real one. gates.mjs takes its root from `process.cwd()`,
 *      so a gate run with the copy as its cwd reads the copy and nothing else. One copy per test
 *      file (per process), removed when the process exits.
 *
 *   2. `guardTree()` — no test may leave the checkout different from how it found it. Called once at
 *      the top of a test file, it snapshots `git status` (plus the content of every path already
 *      dirty, so an edit to an uncommitted file is seen too) before the first test, and fails the
 *      test after which the snapshot no longer matches, naming the paths. `test-tree.test.mjs`
 *      fails any *.test.mjs under scripts/ or release/ that does not call it.
 */
import { after, afterEach, before } from "node:test";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  copyFileSync, existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, readlinkSync, rmSync, symlinkSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const REPO = join(dirname(fileURLToPath(import.meta.url)), "..");

// git with nothing of the caller's configuration that could run a program or take a lock: no hooks,
// no optional index refresh (a `git status` racing a sibling process must not write .git/index).
function git(cwd, args, opts = {}) {
  return execFileSync("git", ["-c", "core.hooksPath=/dev/null", ...args], {
    cwd, stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, GIT_OPTIONAL_LOCKS: "0" }, ...opts,
  });
}

function treeFiles(root) {
  return git(root, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
    .toString().split("\0").filter(Boolean);
}

let sandbox = null;

/** The path of this process's private copy of the checkout (created on first call). */
export function sandboxTree() {
  if (sandbox) return sandbox;
  const root = mkdtempSync(join(tmpdir(), "vexa-gate-tree-"));
  process.on("exit", () => { try { rmSync(root, { recursive: true, force: true }); } catch { /* best effort */ } });
  const packageDirs = new Set([""]);
  for (const rel of treeFiles(REPO)) {
    const src = join(REPO, rel);
    let st;
    try { st = lstatSync(src); } catch { continue; }          // tracked but deleted on disk: absent here too
    const dst = join(root, rel);
    mkdirSync(dirname(dst), { recursive: true });
    if (st.isSymbolicLink()) symlinkSync(readlinkSync(src), dst);
    else if (st.isFile()) copyFileSync(src, dst);             // libuv carries the mode bits across
    if (rel === "package.json" || rel.endsWith("/package.json")) packageDirs.add(dirname(rel) === "." ? "" : dirname(rel));
  }
  // The dependencies a gate's own tooling resolves (ajv, dependency-cruiser, …) stay where pnpm put
  // them: each package's node_modules is linked, never copied.
  for (const dir of packageDirs) {
    const real = join(REPO, dir, "node_modules");
    const here = join(root, dir, "node_modules");
    if (existsSync(real) && !existsSync(here)) symlinkSync(real, here);
  }
  git(root, ["init", "-q"]);
  git(root, ["add", "-A"]);
  sandbox = root;
  return root;
}

// One entry per path git reports as changed or untracked: its status code and its content hash.
function snapshot(root) {
  const out = git(root, ["status", "--porcelain=v1", "-z", "--untracked-files=all"]).toString();
  const rows = new Map();
  const fields = out.split("\0");
  for (let i = 0; i < fields.length; i++) {
    const entry = fields[i];
    if (!entry) continue;
    const code = entry.slice(0, 2), path = entry.slice(3);
    if (code[0] === "R" || code[0] === "C") i++;               // -z: a rename's SOURCE path is the next field
    let digest = "absent";
    try {
      const st = lstatSync(join(root, path));
      digest = st.isSymbolicLink() ? `link:${readlinkSync(join(root, path))}`
        : st.isFile() ? createHash("sha256").update(readFileSync(join(root, path))).digest("hex") : "dir";
    } catch { /* absent */ }
    rows.set(path, `${code} ${digest}`);
  }
  return rows;
}

function differences(before, now) {
  const paths = new Set([...before.keys(), ...now.keys()]);
  return [...paths].filter((p) => before.get(p) !== now.get(p)).sort()
    .map((p) => `${p}: ${before.get(p) ?? "clean"} → ${now.get(p) ?? "clean"}`);
}

/** Fail the test after which the checkout differs from how this file found it. (`root` exists for
 *  test-tree.test.mjs, which proves the guard on a scratch repository; every test file passes none.) */
export function guardTree(root = REPO) {
  let start = null;
  const check = (where) => {
    const now = snapshot(root);
    const diff = differences(start, now);
    if (diff.length) {
      start = now;                                               // report each change once, at the test that made it
      throw new Error(`${where} left the checkout changed — a test writes into a private copy ` +
        `(scripts/test-tree.mjs sandboxTree), never into the tree other test files are reading:\n  ` +
        diff.join("\n  "));
    }
  };
  before(() => { start = snapshot(root); });
  afterEach((t) => check(`"${t.name}"`));
  after(() => check("this file's hooks"));
}
