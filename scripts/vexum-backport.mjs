#!/usr/bin/env node
// Backport Vexum changes without squashing or manufacturing a DCO certification.
// list: print incoming commits, oldest first, with author and sign-off presence.
// check: report in sync, backport pending, forward sync overdue, or divergence.
// apply: refuse divergence and unsigned commits before creating a fresh branch.
// --dry-run prints the proposed commits without creating a branch or worktree.
// Exit codes: 0 = ok; 2 = invalid arguments; 3 = refused; 4 = forward overdue
// (4 belongs to check only). This tool never pushes or opens a pull request.
// Stable patch IDs over the allowlist compare changes across unrelated histories
// and across cherry-picks; SHA identity alone cannot recognize the same change.
// format-patch + am --3way retain the author, author date, and full message,
// including every Signed-off-by trailer. The applier becomes the committer.
// Every resulting commit's metadata is checked byte-for-byte before success.
// Comparison fetches into a disposable database; neither input repo is changed.
// Apply uses a temporary linked worktree, leaving the caller's checkout alone;
// on failure it aborts am, removes the worktree, and deletes the new branch.
// Only allowlisted hunks are applied; author, date, and message remain unchanged.
// Commits with no allowlisted patch are skipped. All commands omit merge commits;
// their ancestors carry the changes without duplicating a merge's first-parent diff.
// The default allowlist is beside this script, independent of the caller's cwd.
// The forward carve's squash is a known gap: commit-preserving forward sync is
// separate work. The forward carve still carries its own copy of this list;
// pinned by test until it reads this file.

import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const usage = 'Usage: node scripts/vexum-backport.mjs <list|check|apply> --vexum <repo-path-or-url> --vexa <repo-path> [--paths scripts/vexum-paths.txt] [--vexum-ref main] [--vexa-ref main] [--branch <name>] [--dry-run]';
class Failure extends Error {
  constructor(message, code = 3) { super(message); this.code = code; }
}

function git(repo, args, input) {
  return execFileSync('git', ['--no-optional-locks', '-C', repo,
    '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false',
    '-c', 'am.signoff=false', ...args], {
    input, encoding: 'utf8', maxBuffer: 256 * 1024 * 1024,
    stdio: ['pipe', 'pipe', 'pipe'],
  });
}

function options(argv) {
  const command = argv.shift();
  if (!['list', 'check', 'apply'].includes(command)) throw new Failure(usage, 2);
  const opts = { command, paths: new URL('./vexum-paths.txt', import.meta.url), 'vexum-ref': 'main',
    'vexa-ref': 'main', branch: `vexum-sync/${new Date().toISOString().slice(0, 10).replaceAll('-', '')}` };
  const seen = new Set();
  while (argv.length) {
    const flag = argv.shift();
    const key = flag.slice(2);
    if (!flag.startsWith('--') || seen.has(key)
      || !['vexum', 'vexa', 'paths', 'vexum-ref', 'vexa-ref', 'branch', 'dry-run'].includes(key)
      || (['branch', 'dry-run'].includes(key) && command !== 'apply')) throw new Failure(usage, 2);
    seen.add(key);
    if (key === 'dry-run') opts[key] = true;
    else {
      const value = argv.shift();
      if (!value || value.startsWith('-')) throw new Failure(usage, 2);
      opts[key] = value;
    }
  }
  if (!opts.vexum || !opts.vexa) throw new Failure(usage, 2);
  opts.vexa = resolve(opts.vexa);
  if (existsSync(opts.vexum)) opts.vexum = resolve(opts.vexum);
  try {
    git(opts.vexa, ['rev-parse', '--git-dir']);
    git(opts.vexa, ['rev-parse', '--verify', `${opts['vexa-ref']}^{commit}`]);
    // check-ref-format --branch accepts @{-1}; disallow its checkout expansion.
    if (opts.branch.startsWith('-') || opts.branch.includes('@{')) throw new Error('invalid branch');
    git(opts.vexa, ['check-ref-format', `refs/heads/${opts.branch}`]);
    const raw = readFileSync(opts.paths, 'utf8');
    opts.paths = raw.trimEnd().split('\n');
    if (!opts.paths.length || opts.paths.some(p => !p || p !== p.trim()
      || p.startsWith('/') || p.startsWith(':') || p.split('/').includes('..'))) {
      throw new Error('paths must contain one repository-relative literal path per line');
    }
  } catch (error) { throw new Failure(`${error.message}\n${usage}`, 2); }
  return opts;
}

function commits(repo, ref, paths) {
  const patches = git(repo, ['log', '--reverse', '--topo-order', '--full-history',
    '--format=commit %H', '--root', '--no-merges', '-p',
    '--binary', '--no-renames', '--no-ext-diff', '--no-textconv', ref, '--',
    ...paths.map(p => `:(top,literal)${p}`)]);
  const ids = git(repo, ['patch-id', '--stable'], patches).trim();
  if (!ids) return [];
  return ids.split('\n').map(line => {
    const [patch, sha] = line.split(' ');
    return { patch, sha };
  });
}

function describe(repo, commit) {
  const message = git(repo, ['log', '-1', '--format=%B', commit.sha]);
  const trailers = git(repo, ['interpret-trailers', '--parse'], message);
  return { ...commit, signed: /^Signed-off-by:[ \t]*\S.+$/m.test(trailers),
    author: git(repo, ['log', '-1', '--format=%an <%ae>', commit.sha]).trimEnd(),
    subject: git(repo, ['log', '-1', '--format=%s', commit.sha]).trimEnd() };
}

const line = c => `${c.sha.slice(0, 7)}  ${c.author}  signed-off: ${c.signed ? 'yes' : 'NO'}  ${c.subject}`;
const metadata = (repo, ref) => git(repo, ['log', '-1', '--format=%an <%ae>%n%ad%n%B', '--date=iso-strict', ref]);

function apply(opts, db, pending, base, temporary) {
  for (const c of pending) {
    if (!c.signed) throw new Failure(`refused ${c.sha}: missing Signed-off-by trailer`);
  }
  if (opts['dry-run']) {
    for (const c of pending) console.log(line(c));
    console.log(`dry-run: ${pending.length} commit(s) would be applied on ${opts.branch}`);
    return;
  }
  if (!pending.length) { console.log(`in sync: 0 commit(s); no branch created (${opts.branch})`); return; }
  if (git(opts.vexa, ['for-each-ref', '--format=%(refname)', `refs/heads/${opts.branch}`]).trim()) {
    throw new Failure(`refused: branch ${opts.branch} already exists`);
  }
  const worktree = join(temporary, 'apply');
  let attached = false;
  let created = false;
  let success = false;
  let applied = 0;
  let current;
  try {
    git(opts.vexa, ['worktree', 'add', '--detach', worktree, base]);
    attached = true;
    git(worktree, ['checkout', '-b', opts.branch]);
    created = true;
    for (const c of pending) {
      current = c;
      const patch = git(db, ['format-patch', '-1', '--stdout', '--keep-subject',
        '--no-signature', '--binary', '--full-index', '--no-renames', '--no-ext-diff',
        '--no-textconv', c.sha, '--', ...opts.paths.map(p => `:(top,literal)${p}`)]);
      if (!patch) continue;
      git(worktree, ['-c', 'am.threeWay=true', '-c', 'rerere.enabled=false',
        'am', '--3way', '--keep', '--keep-cr', '--no-scissors', '--whitespace=nowarn'], patch);
      if (metadata(db, c.sha) !== metadata(worktree, 'HEAD')) {
        throw new Error('author, author date, or full message changed');
      }
      applied++;
    }
    success = true;
  } catch (error) {
    if (attached && existsSync(git(worktree, ['rev-parse', '--git-path', 'rebase-apply']).trim())) {
      git(worktree, ['am', '--abort']);
    }
    throw new Failure(`refused${current ? ` ${current.sha}` : ''}: ${error.stderr?.toString().trim() || error.message}`);
  } finally {
    if (attached) git(opts.vexa, ['worktree', 'remove', '--force', worktree]);
    if (created && !success) git(opts.vexa, ['branch', '-D', opts.branch]);
  }
  console.log(`${opts.branch}: ${applied} commit(s) applied`);
}

function main() {
  const opts = options(process.argv.slice(2));
  const temporary = mkdtempSync(join(tmpdir(), 'vexum-backport-'));
  try {
    const db = join(temporary, 'compare.git');
    git(temporary, ['init', '--bare', db]);
    const base = git(opts.vexa, ['rev-parse', '--verify', `${opts['vexa-ref']}^{commit}`]).trim();
    git(db, ['fetch', '--quiet', '--no-tags', '--', opts.vexa, `${base}:refs/heads/vexa`]);
    git(db, ['fetch', '--quiet', '--no-tags', '--', opts.vexum, `${opts['vexum-ref']}:refs/heads/vexum`]);
    const source = commits(db, 'vexum', opts.paths);
    const target = commits(db, 'vexa', opts.paths);
    const sourceIds = new Set(source.map(c => c.patch));
    const targetIds = new Set(target.map(c => c.patch));
    const incoming = source.filter(c => !targetIds.has(c.patch));
    const outgoing = target.filter(c => !sourceIds.has(c.patch));
    const n = incoming.length, m = outgoing.length;
    const summary = n && m ? `diverged: ${n} pending backport, ${m} overdue forward`
      : n ? `backport pending: ${n} commit(s) on Vexum not in vexa`
        : m ? `forward sync overdue: ${m} commit(s) in vexa not on Vexum` : 'in sync';
    if (opts.command === 'check') {
      console.log(summary);
      process.exitCode = n && m ? 3 : m ? 4 : 0;
    } else if (opts.command === 'list') {
      for (const c of incoming) console.log(line(describe(db, c)));
    } else {
      if (n && m) throw new Failure(summary);
      apply(opts, db, incoming.map(c => describe(db, c)), base, temporary);
    }
  } finally { rmSync(temporary, { recursive: true, force: true }); }
}

try { main(); }
catch (error) {
  console.error(error instanceof Failure ? error.message : `refused: ${error.stderr?.toString().trim() || error.message}`);
  process.exitCode = error instanceof Failure ? error.code : 3;
}
