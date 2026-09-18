// Offline CLI contract: unrelated Git histories, real format-patch/am, no network.
// Run from the worktree root: node --test scripts/vexum-backport.test.mjs
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';

const script = resolve('scripts/vexum-backport.mjs');
const scratch = resolve('.claude/scratch');
mkdirSync(scratch, { recursive: true });
const root = mkdtempSync(join(scratch, 'vexum-tests-'));
after(() => rmSync(root, { recursive: true, force: true }));
const env = { ...process.env, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null',
  GIT_TERMINAL_PROMPT: '0', TMPDIR: root };

function git(repo, args, extra = {}) {
  return execFileSync('git', ['-C', repo, ...args], { encoding: 'utf8',
    stdio: ['pipe', 'pipe', 'pipe'], env, ...extra });
}
function put(repo, file, contents) {
  mkdirSync(dirname(join(repo, file)), { recursive: true });
  writeFileSync(join(repo, file), contents);
}
function commit(repo, subject, changes, { signed = true, body = '', author = 'Original Author <author@example.test>' } = {}) {
  for (const [file, contents] of Object.entries(changes)) put(repo, file, contents);
  git(repo, ['add', '.']);
  const message = `${subject}\n\n${body ? `${body}\n\n` : ''}${signed ? `Signed-off-by: ${author}\n` : ''}`;
  git(repo, ['commit', '--cleanup=verbatim', '--author', author, '-F', '-'], {
    input: message,
    env: { ...env, GIT_AUTHOR_DATE: '2026-08-02T12:34:56+05:30', GIT_COMMITTER_DATE: '2026-08-03T01:02:03+00:00' },
  });
  return git(repo, ['rev-parse', 'HEAD']).trim();
}
function fixture() {
  const dir = mkdtempSync(join(root, 'pair-'));
  const f = { dir, vexum: join(dir, 'vexum'), vexa: join(dir, 'vexa'), paths: join(dir, 'paths.txt') };
  writeFileSync(f.paths, 'core\n');
  for (const repo of [f.vexum, f.vexa]) {
    mkdirSync(repo);
    git(repo, ['init', '-b', 'main']);
    git(repo, ['config', 'user.name', repo === f.vexum ? 'Vexum Committer' : 'Vexa Applier']);
    git(repo, ['config', 'user.email', repo === f.vexum ? 'source@example.test' : 'target@example.test']);
    git(repo, ['config', 'commit.gpgsign', 'false']);
    git(repo, ['config', 'core.hooksPath', '/dev/null']);
    commit(repo, 'Initial tree', { 'core/base.txt': 'base\n', 'private.txt': 'original\n' });
  }
  return f;
}
function run(f, command, ...extra) {
  const args = command ? [command, '--vexum', f.vexum, '--vexa', f.vexa, '--paths', f.paths, ...extra] : extra;
  try {
    return { status: 0, stdout: execFileSync(process.execPath, [script, ...args], {
      encoding: 'utf8', env, stdio: ['pipe', 'pipe', 'pipe'],
    }), stderr: '' };
  } catch (error) {
    return { status: error.status, stdout: error.stdout?.toString() || '', stderr: error.stderr?.toString() || '' };
  }
}
function ok(result) { assert.equal(result.status, 0, result.stderr); return result.stdout; }
function state(repo) {
  return {
    head: git(repo, ['rev-parse', 'HEAD']),
    branches: git(repo, ['for-each-ref', '--format=%(refname) %(objectname)', 'refs/heads']),
    status: git(repo, ['status', '--porcelain=v1', '--untracked-files=all']),
    worktrees: git(repo, ['worktree', 'list', '--porcelain']),
    base: readFileSync(join(repo, 'core/base.txt'), 'utf8'),
    private: readFileSync(join(repo, 'private.txt'), 'utf8'),
  };
}
const metadata = (repo, sha) => git(repo, ['log', '-1', '--format=%an <%ae>%n%ad%n%B', '--date=iso-strict', sha]);

test('list orders two signed commits; apply preserves each author, date, and full message byte-for-byte', () => {
  const f = fixture();
  const first = commit(f.vexum, '[PATCH] Première modification', { 'core/base.txt': 'first\n' }, {
    author: 'Élodie Example <elodie@example.test>',
    body: 'Body with Unicode: café.\n\nReviewed-by: Reviewer <review@example.test>',
  });
  const second = commit(f.vexum, 'Second change', { 'core/second.txt': 'second\n' }, {
    author: 'Another Author <another@example.test>',
    body: 'Two paragraphs.\n\nSigned-off-by: Reviewer <review@example.test>',
  });
  const list = ok(run(f, 'list')).trimEnd().split('\n');
  assert.equal(list.length, 2);
  assert.equal(list[0], `${first.slice(0, 7)}  Élodie Example <elodie@example.test>  signed-off: yes  [PATCH] Première modification`);
  assert.equal(list[1], `${second.slice(0, 7)}  Another Author <another@example.test>  signed-off: yes  Second change`);
  assert.match(ok(run(f, 'apply', '--branch', 'sync/two')), /sync\/two: 2 commit\(s\) applied/);
  const applied = git(f.vexa, ['rev-list', '--reverse', 'main..sync/two']).trim().split('\n');
  assert.equal(applied.length, 2);
  for (let i = 0; i < 2; i++) {
    const source = [first, second][i];
    assert.notEqual(source, applied[i]);
    assert.equal(metadata(f.vexum, source), metadata(f.vexa, applied[i]));
  }
  assert.equal(git(f.vexa, ['show', 'sync/two:core/base.txt']), 'first\n');
  assert.equal(git(f.vexa, ['show', 'sync/two:core/second.txt']), 'second\n');
  assert.equal(git(f.vexa, ['branch', '--show-current']).trim(), 'main');
  assert.equal(ok(run(f, 'list', '--vexa-ref', 'sync/two')), '');
});

test('non-allowlist changes are invisible; list and check leave both repositories unchanged', () => {
  const f = fixture();
  commit(f.vexum, 'Private change', { 'private.txt': 'source-only\n' });
  const before = [state(f.vexum), state(f.vexa)];
  assert.equal(ok(run(f, 'list')), '');
  assert.equal(ok(run(f, 'check')), 'in sync\n');
  assert.deepEqual([state(f.vexum), state(f.vexa)], before);
});

test('a cherry-picked change with a different SHA is recognized by stable patch-id', () => {
  const f = fixture();
  const source = commit(f.vexum, 'Shared change', { 'core/base.txt': 'shared\n' });
  git(f.vexa, ['fetch', f.vexum, 'main']);
  git(f.vexa, ['cherry-pick', source]);
  assert.notEqual(git(f.vexa, ['rev-parse', 'HEAD']).trim(), source);
  assert.equal(ok(run(f, 'list')), '');
  assert.equal(ok(run(f, 'check')), 'in sync\n');
});

test('patch identity is restricted to allowlist paths even for mixed commits', () => {
  const f = fixture();
  commit(f.vexum, 'Source patch', { 'core/base.txt': 'same\n', 'private.txt': 'source\n' });
  commit(f.vexa, 'Target patch', { 'core/base.txt': 'same\n', 'private.txt': 'target\n' });
  assert.equal(ok(run(f, 'list')), '');
  assert.equal(ok(run(f, 'check')), 'in sync\n');
});

test('a no-ff merge lists and backports only its side commits, then reports in sync', () => {
  const f = fixture();
  git(f.vexum, ['checkout', '-b', 'feature']);
  const first = commit(f.vexum, 'Feature first', { 'core/first.txt': 'first\n' });
  const second = commit(f.vexum, 'Feature second', { 'core/second.txt': 'second\n' });
  git(f.vexum, ['checkout', 'main']);
  git(f.vexum, ['merge', '--no-ff', 'feature', '-m', 'Merge feature']);
  const merge = git(f.vexum, ['rev-parse', 'HEAD']).trim();
  const listed = ok(run(f, 'list')).trimEnd().split('\n');
  assert.deepEqual(listed.map(line => line.slice(0, 7)), [first, second].map(sha => sha.slice(0, 7)));
  assert.ok(!listed.some(line => line.startsWith(merge.slice(0, 7))));
  assert.equal(ok(run(f, 'check')), 'backport pending: 2 commit(s) on Vexum not in vexa\n');
  assert.match(ok(run(f, 'apply', '--branch', 'sync/merged')), /2 commit\(s\) applied/);
  const applied = git(f.vexa, ['rev-list', '--reverse', 'main..sync/merged']).trim().split('\n');
  assert.equal(applied.length, 2);
  for (let i = 0; i < applied.length; i++) {
    assert.equal(metadata(f.vexum, [first, second][i]), metadata(f.vexa, applied[i]));
  }
  assert.equal(ok(run(f, 'check', '--vexa-ref', 'sync/merged')), 'in sync\n');
});

test('mixed commits apply only allowlisted hunks while retaining the full original metadata', () => {
  const f = fixture();
  const source = commit(f.vexum, 'Core and governance', {
    'core/base.txt': 'new core\n', 'GOVERNANCE.md': 'Vexum-only governance\n',
    'security-insights.yml': 'Vexum-only metadata\n', 'security/policy.md': 'Vexum-only policy\n',
  });
  assert.match(ok(run(f, 'apply', '--branch', 'sync/mixed')), /1 commit\(s\) applied/);
  assert.equal(git(f.vexa, ['show', 'sync/mixed:core/base.txt']), 'new core\n');
  assert.equal(git(f.vexa, ['ls-tree', '-r', '--name-only', 'sync/mixed', '--',
    'GOVERNANCE.md', 'security-insights.yml', 'security']), '');
  assert.equal(git(f.vexa, ['diff', '--name-only', 'main', 'sync/mixed']), 'core/base.txt\n');
  assert.equal(metadata(f.vexum, source), metadata(f.vexa, 'sync/mixed'));
  assert.equal(ok(run(f, 'check', '--vexa-ref', 'sync/mixed')), 'in sync\n');
});

test('default paths work when the CLI is invoked from a temporary directory', () => {
  const f = fixture();
  const source = commit(f.vexum, 'From any cwd', { 'core/base.txt': 'updated\n' });
  const invoke = (...args) => execFileSync(process.execPath, [script, ...args,
    '--vexum', f.vexum, '--vexa', f.vexa], {
    cwd: f.dir, env, encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'],
  });
  assert.match(invoke('list'), new RegExp(source.slice(0, 7)));
  assert.match(invoke('apply', '--branch', 'sync/cwd'), /1 commit\(s\) applied/);
  assert.equal(invoke('check', '--vexa-ref', 'sync/cwd'), 'in sync\n');
});

test('non-allowlisted-only commits are skipped by apply without a branch', () => {
  const f = fixture();
  commit(f.vexum, 'Governance only', { 'GOVERNANCE.md': 'governance\n' }, { signed: false });
  const before = state(f.vexa);
  assert.match(ok(run(f, 'apply')), /0 commit\(s\); no branch created/);
  assert.deepEqual(state(f.vexa), before);
});

test('the DCO trailer must use the exact Signed-off-by capitalization', () => {
  const f = fixture();
  const source = commit(f.vexum, 'Lowercase trailer', { 'core/base.txt': 'updated\n' }, {
    signed: false, body: 'signed-off-by: Original Author <author@example.test>',
  });
  assert.match(ok(run(f, 'list')), /signed-off: NO/);
  const before = state(f.vexa);
  const result = run(f, 'apply');
  assert.equal(result.status, 3);
  assert.match(result.stderr, new RegExp(source));
  assert.deepEqual(state(f.vexa), before);
});

test('an unsigned later commit refuses the entire batch before any branch is created', () => {
  const f = fixture();
  commit(f.vexum, 'Signed first', { 'core/first.txt': 'first\n' });
  const unsigned = commit(f.vexum, 'Unsigned', { 'core/second.txt': 'second\n' }, { signed: false });
  const before = state(f.vexa);
  const result = run(f, 'apply', '--branch', 'sync/unsigned');
  assert.equal(result.status, 3);
  assert.match(result.stderr, new RegExp(unsigned));
  assert.match(result.stderr, /missing Signed-off-by/);
  assert.deepEqual(state(f.vexa), before);
  assert.match(ok(run(f, 'list')), /signed-off: NO/);
});

test('a Signed-off-by mention in prose is not a trailer', () => {
  const f = fixture();
  commit(f.vexum, 'No certification', { 'core/base.txt': 'unsigned\n' }, {
    signed: false, body: 'Signed-off-by: Someone <someone@example.test>\n\nThis paragraph is still the message body.',
  });
  assert.equal(run(f, 'apply').status, 3);
});

test('conflict after an applied commit aborts and restores branches, files, and worktree registration', () => {
  const f = fixture();
  const edit = commit(f.vexum, 'Shared edit', { 'core/base.txt': 'intermediate\n' });
  const revert = commit(f.vexum, 'Shared revert', { 'core/base.txt': 'base\n' });
  git(f.vexa, ['fetch', f.vexum, 'main']);
  git(f.vexa, ['cherry-pick', edit, revert]);
  // This repeated patch ID is already known to vexa, whose tree retains the revert.
  commit(f.vexum, 'Reapply shared edit', { 'core/base.txt': 'intermediate\n' });
  commit(f.vexum, 'Applicable first', { 'core/first.txt': 'first\n' });
  const bad = commit(f.vexum, 'Conflict second', { 'core/base.txt': 'source edit\n' });
  assert.equal(ok(run(f, 'check')), 'backport pending: 2 commit(s) on Vexum not in vexa\n');
  const before = state(f.vexa);
  const result = run(f, 'apply', '--branch', 'sync/conflict');
  assert.equal(result.status, 3);
  assert.match(result.stderr, new RegExp(bad));
  assert.deepEqual(state(f.vexa), before);
});

test('dry-run prints incoming changes without creating a branch', () => {
  const f = fixture();
  const sha = commit(f.vexum, 'Pending', { 'core/base.txt': 'pending\n' });
  const before = state(f.vexa);
  const result = run(f, 'apply', '--dry-run');
  assert.match(ok(result), new RegExp(sha.slice(0, 7)));
  assert.match(result.stdout, /dry-run: 1 commit\(s\) would be applied on vexum-sync\/\d{8}/);
  assert.deepEqual(state(f.vexa), before);
});

test('check distinguishes all four states and apply refuses divergence without mutation', () => {
  const f = fixture();
  assert.equal(ok(run(f, 'check')), 'in sync\n');
  commit(f.vexum, 'Incoming', { 'core/incoming.txt': 'incoming\n' });
  assert.equal(ok(run(f, 'check')), 'backport pending: 1 commit(s) on Vexum not in vexa\n');
  commit(f.vexa, 'Outgoing', { 'core/outgoing.txt': 'outgoing\n' });
  const diverged = run(f, 'check');
  assert.equal(diverged.status, 3);
  assert.equal(diverged.stdout, 'diverged: 1 pending backport, 1 overdue forward\n');
  const before = state(f.vexa);
  assert.equal(run(f, 'apply').status, 3);
  assert.deepEqual(state(f.vexa), before);
  const ahead = fixture();
  commit(ahead.vexa, 'Outgoing only', { 'core/outgoing.txt': 'outgoing\n' });
  const overdue = run(ahead, 'check');
  assert.equal(overdue.status, 4);
  assert.equal(overdue.stdout, 'forward sync overdue: 1 commit(s) in vexa not on Vexum\n');
});

test('apply never overwrites an existing branch', () => {
  const f = fixture();
  commit(f.vexum, 'Pending', { 'core/base.txt': 'pending\n' });
  git(f.vexa, ['branch', 'sync/existing']);
  const before = state(f.vexa);
  assert.equal(run(f, 'apply', '--branch', 'sync/existing').status, 3);
  assert.deepEqual(state(f.vexa), before);
});

test('apply leaves a detached, dirty caller checkout untouched', () => {
  const f = fixture();
  commit(f.vexum, 'Pending', { 'core/base.txt': 'pending\n' });
  git(f.vexa, ['checkout', '--detach']);
  put(f.vexa, 'core/base.txt', 'uncommitted\n');
  put(f.vexa, 'untracked.txt', 'keep me\n');
  const before = state(f.vexa);
  ok(run(f, 'apply', '--branch', 'sync/isolated'));
  const current = state(f.vexa);
  assert.deepEqual({ ...current, branches: before.branches }, before);
  assert.equal(readFileSync(join(f.vexa, 'untracked.txt'), 'utf8'), 'keep me\n');
});

test('binary commits survive full patches', () => {
  const f = fixture();
  const blob = Buffer.from([0, 255, 1, 200, 0, 50]);
  const source = commit(f.vexum, 'Binary', { 'core/blob': blob });
  ok(run(f, 'apply', '--branch', 'sync/binary'));
  assert.deepEqual(git(f.vexa, ['show', 'sync/binary:core/blob'], { encoding: null }), blob);
  assert.equal(metadata(f.vexum, source), metadata(f.vexa, 'sync/binary'));
});

test('unknown command, missing source, and invalid options return usage on stderr with exit 2', () => {
  const f = fixture();
  for (const args of [[], ['unknown'], ['list', '--vexa', f.vexa],
    ['list', '--vexum', f.vexum, '--vexa', f.vexa, '--dry-run'],
    ['apply', '--vexum', f.vexum, '--vexa', f.vexa, '--branch', 'bad..branch']]) {
    const result = run(f, null, ...args);
    assert.equal(result.status, 2, JSON.stringify(result));
    assert.match(result.stderr, /Usage:/);
  }
});

test('the shared path file pins every INCLUDE entry in the carve, in order', () => {
  const source = readFileSync('scripts/sync-carve.sh', 'utf8');
  const array = source.match(/^INCLUDE=\(\n([\s\S]*?)^\)/m);
  assert.ok(array, 'carve INCLUDE array exists');
  const paths = array[1].split('\n').map(line => line.split('#')[0].trim()).filter(Boolean);
  const raw = readFileSync('scripts/vexum-paths.txt', 'utf8');
  assert.equal(raw, `${paths.join('\n')}\n`);
  assert.ok(paths.length >= 20);
});
