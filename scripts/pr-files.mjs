// pr-files — every file a PR changes, however many. For the gates that judge a PR by its file
// list (docs-current, release-witness; merge-card and release-value once #2008 is on main).
//
// GitHub's REST list (pulls/{n}/files) stops at 3,000 files, and the gates paged it only to 1,000
// or 2,000. A gate that decides from a cut list judges a large PR on part of its diff and never
// says so: Vexa-ai/vexa#2008 changes 5,143 files, 475 of them docs pages past the 2,000th, and
// docs-current reported "changes no docs/**". So the list is checked against the PR's own
// `changed_files` count; when the API list is shorter, the names come from git instead: the same
// three-dot diff GitHub shows (merge base → head), fetched as commits and trees only (no blobs).
// When even that cannot account for every changed file, this throws: a gate must fail, never
// judge part of a PR.
//
// A rename counts under both names on either path: the old one as removed, the new one as added
// (git: --no-renames, since exact rename detection would need the blobs). So a file moved out of
// a surface counts as touching it, whether the PR has 2 files or 5,000.

import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export const API_FILE_CAP = 3000;   // GitHub's documented maximum for pulls/{n}/files
const PER_PAGE = 100;

// Names and statuses of every file changed between two commits. The fetch goes into a throwaway
// bare repository, never into the caller's clone (a shallow fetch there would cut its history and
// turn it into a partial clone). Only the remote's URL and any http extraheader (the Actions
// checkout token) are copied from `cwd`, into the throwaway's own config, never onto argv.
export function gitDiffFiles(base, head, { cwd = process.cwd(), remote = "origin", run = execFileSync } = {}) {
  const opts = { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], maxBuffer: 256 * 1024 * 1024 };
  const url = run("git", ["remote", "get-url", remote], { cwd, ...opts }).trim();
  let headers = "";
  try { headers = run("git", ["config", "--get-regexp", "^http\\..*extraheader$"], { cwd, ...opts }); } catch { /* none set */ }
  const dir = mkdtempSync(join(tmpdir(), "pr-files-"));
  try {
    const git = (args) => run("git", ["--git-dir", dir, ...args], opts);
    git(["init", "--quiet", "--bare"]);
    for (const line of headers.split("\n").filter(Boolean)) {
      const sp = line.indexOf(" ");
      git(["config", "--add", line.slice(0, sp), line.slice(sp + 1)]);
    }
    git(["fetch", "--no-tags", "--quiet", "--depth=1", "--filter=blob:none", url, base, head]);
    const out = git(["diff", "--no-renames", "--name-status", "-z", base, head]);
    const parts = out.split("\0").filter(Boolean);
    const files = [];
    for (let i = 0; i + 1 < parts.length; i += 2) files.push({ filename: parts[i + 1], status: STATUS[parts[i][0]] || parts[i] });
    return files;
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}
const STATUS = { A: "added", D: "removed", M: "modified", T: "changed" };

// Every changed file of `pr` (the pulls/{n} object), as [{ filename, status }].
//   api(path) → parsed JSON of a GitHub REST GET (the gate's own reader, injectable for tests)
//   git(base, head) → the file list between two commits (gitDiffFiles by default)
export function prFiles(pr, { repo, api, git = gitDiffFiles } = {}) {
  const num = pr.number;
  const listed = [];
  let rows = 0;   // API rows; GitHub's changed_files counts a rename once
  for (let page = 1; page <= API_FILE_CAP / PER_PAGE; page++) {
    const batch = api(`repos/${repo}/pulls/${num}/files?per_page=${PER_PAGE}&page=${page}`);
    rows += batch.length;
    for (const f of batch) {
      listed.push({ filename: f.filename, status: f.status });
      if (f.previous_filename) listed.push({ filename: f.previous_filename, status: "removed" });
    }
    if (batch.length < PER_PAGE) break;
  }
  const expected = pr.changed_files;
  if (typeof expected !== "number") {
    if (rows >= API_FILE_CAP) throw new Error(`#${num}: the API list stopped at its ${API_FILE_CAP}-file cap and the PR gives no changed_files count to check it against`);
    return listed;
  }
  if (rows >= expected) return listed;

  const base = pr.base?.sha, head = pr.head?.sha;
  if (!base || !head) throw new Error(`#${num}: the API listed ${rows} of ${expected} changed files and the PR names no base/head sha to read the rest from`);
  const mergeBase = api(`repos/${repo}/compare/${base}...${head}?per_page=1`).merge_base_commit?.sha;
  if (!mergeBase) throw new Error(`#${num}: the API listed ${rows} of ${expected} changed files and gave no merge base for ${base}...${head}`);
  const all = git(mergeBase, head);
  // --no-renames counts a rename twice, so git may list more than GitHub counts, never fewer.
  if (all.length < expected) throw new Error(`#${num}: git lists ${all.length} files between ${mergeBase} and ${head}, the PR says ${expected} — refusing to judge part of the diff`);
  return all;
}
