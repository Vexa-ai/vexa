/**
 * session-store.test — the session profile and the S3 sync surface, offline (no real S3, no network).
 *
 * A fake `aws` executable on PATH records every invocation (one argument per line) and answers a
 * listing from a file, so the REAL syncBrowserDataFromS3 / syncBrowserDataToS3 run end-to-end and
 * the assertions read what actually crossed the process boundary. Covers:
 *
 *  1. RESTORE PULLS ONLY THE PROFILE — every stored key is checked against SESSION_PROFILE before
 *     anything is fetched: a key outside it (an extension, a cache, a traversal name, a nested
 *     LevelDB path, an oversized object) is never named in the download, even though it is stored.
 *  2. UPLOAD SENDS ONLY THE PROFILE — regular files only (a symlink at a profile name is skipped,
 *     as is a symlinked directory), from the LIVE dataDir handed in (#725 C1).
 *  3. FAIL-LOUD RESTORE (#724 C3) — a failing listing surfaces as a typed SessionSyncError naming
 *     the session-restore step (and names a missing aws CLI); the upload half stays warn-only.
 *  4. THE PATH RULE — isSessionProfilePath refuses traversal, absolute, backslash and NUL names.
 *
 * Same shape as auth.smoke.test.ts (tsx + exit code, no assert lib).
 */
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync, existsSync, chmodSync, symlinkSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';
import {
  syncBrowserDataToS3, syncBrowserDataFromS3, SessionSyncError, SESSION_PROFILE, isSessionProfilePath,
  collectSessionProfile, readSessionProfile, saveSessionLocal, type S3Config,
} from './session-store';

const fails: string[] = [];
const check = (cond: boolean, msg: string) => { if (!cond) fails.push(msg); };

const work = mkdtempSync(join(tmpdir(), 'session-store-test-'));
const binDir = join(work, 'bin');
const logFile = join(work, 'aws-calls.log');
const listingFile = join(work, 'listing.json');
mkdirSync(binDir, { recursive: true });

/** Install the fake `aws` on PATH. 'ok' records + answers the listing; 'fail' records + exits 1. */
function installFakeAws(mode: 'ok' | 'fail'): void {
  const script = [
    '#!/bin/sh',
    `for a in "$@"; do printf '%s\\n' "$a" >> "${logFile}"; done`,
    `echo '--END--' >> "${logFile}"`,
    mode === 'fail' ? 'exit 1' : '',
    `if [ "$1" = "s3api" ] && [ -f "${listingFile}" ]; then cat "${listingFile}"; fi`,
    'exit 0',
  ].join('\n') + '\n';
  writeFileSync(join(binDir, 'aws'), script);
  chmodSync(join(binDir, 'aws'), 0o755);
}
const realPath = process.env.PATH ?? '';
const withFakePath = `${binDir}:${realPath}`;
/** Every recorded aws call, as its argv. */
const calls = (): string[][] => {
  if (!existsSync(logFile)) return [];
  return readFileSync(logFile, 'utf8').split('--END--\n').filter((c) => c.length).map((c) => c.split('\n').filter((l) => l !== ''));
};
const resetLog = (): void => { rmSync(logFile, { force: true }); };
/** The values that follow each `flag` in an argv. */
const flagValues = (argv: string[], flag: string): string[] => argv.flatMap((a, i) => (a === flag ? [argv[i + 1]] : []));

const config: S3Config = {
  userdataS3Path: 'userdata/test-identity',
  s3Endpoint: 'http://storage.test:9000',
  s3Bucket: 'vexa-test',
  s3AccessKey: 'k',
  s3SecretKey: 's',
};
const base = 'userdata/test-identity/browser-data';

// ── 4. the path rule ────────────────────────────────────────────────────────
for (const ok of ['Local State', 'Default/Cookies', 'Default/Web Data', 'Default/Local Storage/leveldb/000003.log',
  'Default/Local Storage/leveldb/CURRENT', 'Default/Session Storage/MANIFEST-000001', 'Default/Session Storage/000005.ldb']) {
  check(isSessionProfilePath(ok), `'${ok}' is a session profile path`);
}
for (const bad of ['', '/Default/Cookies', 'Default/../Default/Cookies', 'Default/Local Storage/../../x',
  './Local State', 'Default//Cookies', 'Default\\Cookies', 'Default/Cookies\0', 'Default/Extensions/abc/manifest.json',
  'Default/Cache/data_0', 'Default/Local Storage/leveldb/LOCK', 'Default/Local Storage/leveldb/sub/000003.log',
  'Default/Local Storage/000003.log', 'Default/Session Storage/x.ldb', 'Default/Preferences.bak', 'Default/Bookmarks']) {
  check(!isSessionProfilePath(bad), `'${JSON.stringify(bad)}' must not be a session profile path`);
}

// ── 1. restore pulls only the profile, even when other keys are stored ──────
const stored = [
  ['Local State', 10], ['Default/Cookies', 100], ['Default/Preferences', 50],
  ['Default/Local Storage/leveldb/CURRENT', 16], ['Default/Local Storage/leveldb/000003.log', 200],
  ['Default/Session Storage/MANIFEST-000001', 30],
  // stored under the prefix, never part of a session:
  ['Default/Extensions/evil/1.0/manifest.json', 10], ['Default/Cache/data_0', 10],
  ['Default/Local Storage/leveldb/LOCK', 0], ['Default/Local Storage/../../planted', 10],
  ['Default/Session Storage/nested/000001.log', 10], ['Default/Web Data', SESSION_PROFILE.maxFileBytes + 1],
  ['Default/Bookmarks', 10],
] as const;
const allowedStored = ['Default/Cookies', 'Default/Local Storage/leveldb/000003.log', 'Default/Local Storage/leveldb/CURRENT',
  'Default/Preferences', 'Default/Session Storage/MANIFEST-000001', 'Local State'];
writeFileSync(listingFile, JSON.stringify({
  Contents: [
    ...stored.map(([k, size]) => ({ Key: `${base}/${k}`, Size: size })),
    { Key: 'userdata/test-identity/browser-data-other/Cookies', Size: 1 },   // a sibling prefix
  ],
}));
installFakeAws('ok');
process.env.PATH = withFakePath;
resetLog();
const restoreDir = mkdtempSync(join(tmpdir(), 'browser-data-restore-'));
syncBrowserDataFromS3(config, restoreDir);
const restoreCalls = calls();
check(restoreCalls.length === 2, `restore: expected a listing + one download, got ${restoreCalls.length} aws call(s)`);
const [listCall, syncCall] = restoreCalls;
check(listCall?.[0] === 's3api' && listCall?.[1] === 'list-objects-v2', 'restore lists the stored prefix first');
check(flagValues(listCall ?? [], '--prefix')[0] === `${base}/`, 'the listing is limited to the session prefix');
check(syncCall?.[0] === 's3' && syncCall?.[1] === 'sync', 'restore downloads with one sync');
check(syncCall?.[2] === `s3://vexa-test/${base}/` && syncCall?.[3] === `${restoreDir}/`, 'restore downloads the prefix into the bot dir');
const excludes = flagValues(syncCall ?? [], '--exclude');
const includes = flagValues(syncCall ?? [], '--include');
check(excludes.length === 1 && excludes[0] === '*', 'restore excludes everything, then includes profile paths');
check((syncCall ?? []).indexOf('--exclude') < (syncCall ?? []).indexOf('--include'), 'the exclude-all filter precedes the includes');
check(JSON.stringify([...includes].sort()) === JSON.stringify([...allowedStored].sort()),
  `restore includes exactly the stored profile paths, got ${JSON.stringify(includes)}`);
for (const planted of ['Extensions', 'Cache', 'LOCK', 'planted', 'nested', 'Web Data', 'Bookmarks', 'browser-data-other']) {
  check(!(syncCall ?? []).some((a) => a.includes(planted)), `restore must never name '${planted}'`);
}

// ── 1b. nothing stored that belongs to a session → no download at all ──────
writeFileSync(listingFile, JSON.stringify({ Contents: [{ Key: `${base}/Default/Extensions/x/manifest.json`, Size: 1 }] }));
resetLog();
syncBrowserDataFromS3(config, restoreDir);
check(calls().length === 1, 'restore with no profile path stored makes no download call');
writeFileSync(listingFile, '');
resetLog();
let emptyThrew = false;
try { syncBrowserDataFromS3(config, restoreDir); } catch { emptyThrew = true; }
check(!emptyThrew && calls().length === 1, 'an empty listing is an empty session, not a failure');
rmSync(listingFile, { force: true });

// ── 2. upload sends only the profile, regular files only ────────────────────
const liveDir = mkdtempSync(join(tmpdir(), 'browser-data-'));
const outside = join(work, 'outside.txt');
writeFileSync(outside, 'not a profile file');
mkdirSync(join(liveDir, 'Default', 'Local Storage', 'leveldb'), { recursive: true });
mkdirSync(join(liveDir, 'Default', 'Extensions', 'evil'), { recursive: true });
mkdirSync(join(liveDir, 'Default', 'Cache'), { recursive: true });
writeFileSync(join(liveDir, 'Default', 'Cookies'), 'rotated-cookie-bytes');
writeFileSync(join(liveDir, 'Local State'), '{}');
writeFileSync(join(liveDir, 'Default', 'Local Storage', 'leveldb', '000003.log'), 'ls');
writeFileSync(join(liveDir, 'Default', 'Local Storage', 'leveldb', 'LOCK'), '');
writeFileSync(join(liveDir, 'Default', 'Extensions', 'evil', 'manifest.json'), '{}');
writeFileSync(join(liveDir, 'Default', 'Cache', 'data_0'), 'cache');
symlinkSync(outside, join(liveDir, 'Default', 'Web Data'));                 // a link at a profile name
mkdirSync(join(work, 'elsewhere'), { recursive: true });
writeFileSync(join(work, 'elsewhere', 'MANIFEST-000001'), 'm');
symlinkSync(join(work, 'elsewhere'), join(liveDir, 'Default', 'Session Storage'));  // a linked LevelDB dir

const collected = collectSessionProfile(liveDir).map((f) => f.path);
check(JSON.stringify(collected) === JSON.stringify(['Local State', 'Default/Cookies', 'Default/Local Storage/leveldb/000003.log']),
  `collect: only regular profile files, got ${JSON.stringify(collected)}`);
const body = readSessionProfile(liveDir);
check(body.length === 3 && body.find((f) => f.path === 'Default/Cookies')?.data.toString() === 'rotated-cookie-bytes',
  'readSessionProfile carries the bytes of exactly the collected files');

resetLog();
const uploaded = syncBrowserDataToS3(config, liveDir);
check(uploaded === 3, `upload should send the 3 profile files present, got ${uploaded}`);
const upCalls = calls();
check(upCalls.length === 1, `upload: expected one aws sync, got ${upCalls.length}`);
const up = upCalls[0] ?? [];
check(up[2] === `${liveDir}/` && up[3] === `s3://vexa-test/${base}/`, 'upload reads the LIVE dataDir handed in and targets the prefix');
check(up.includes('--no-follow-symlinks'), 'upload never follows a symlink');
check(JSON.stringify(flagValues(up, '--include')) === JSON.stringify(collected), 'upload includes exactly the collected files');
for (const planted of ['Extensions', 'Cache', 'LOCK', 'Web Data', 'Session Storage']) {
  check(!up.some((a) => a.includes(planted)), `upload must never name '${planted}'`);
}

// ── 2b. incomplete config is a no-op (guest/anonymous path untouched) ───────
resetLog();
const noop = syncBrowserDataToS3({ userdataS3Path: 'x' }, liveDir);   // no endpoint/bucket
check(noop === 0 && calls().length === 0, 'incomplete S3 config must be a no-op (0 uploads, no aws calls)');

// ── 2c. local save copies the profile only ──────────────────────────────────
const saved = mkdtempSync(join(tmpdir(), 'saved-session-'));
check(saveSessionLocal(saved, liveDir) === 3, 'local save copies the 3 profile files');
check(!existsSync(join(saved, 'Default', 'Extensions')) && !existsSync(join(saved, 'Default', 'Web Data')),
  'local save leaves non-profile files and links behind');

// ── 3. upload failures are warnings, never throws (teardown safety) ─────────
installFakeAws('fail');
resetLog();
let threw = false;
let count = -1;
try { count = syncBrowserDataToS3(config, liveDir); } catch { threw = true; }
check(!threw, 'upload must NEVER throw on failure');
check(count === 0, `a failing upload must report 0 files, got ${count}`);

// ── 3b. restore failure is a typed, attributed SessionSyncError (#724 C3) ───
let restoreErr: unknown;
try { syncBrowserDataFromS3(config, liveDir); } catch (e) { restoreErr = e; }
check(restoreErr instanceof SessionSyncError, 'failing restore must throw SessionSyncError (typed, not a bare exec death)');
if (restoreErr instanceof SessionSyncError) {
  check(restoreErr.step === 'session-restore', `restore error must name the session-restore step, got '${restoreErr.step}'`);
  const namedEndpoint = /\(endpoint (.+?), path /.exec(restoreErr.message)?.[1];
  check(namedEndpoint === config.s3Endpoint, `restore error must name the exact endpoint, got '${namedEndpoint}'`);
}

// ── 3c. a missing aws CLI is named as such ──────────────────────────────────
process.env.PATH = join(work, 'empty-bin');   // no aws anywhere on PATH
mkdirSync(process.env.PATH, { recursive: true });
let missingErr: unknown;
try { syncBrowserDataFromS3(config, liveDir); } catch (e) { missingErr = e; }
check(missingErr instanceof SessionSyncError, 'missing aws CLI must still surface as SessionSyncError');
check(String((missingErr as Error)?.message ?? '').includes('aws CLI not found'), 'missing aws CLI must be named in the error');
process.env.PATH = realPath;

// ── 3d. restore with incomplete config is a no-op (anonymous bots unaffected) ──
let noopThrew = false;
try { syncBrowserDataFromS3({}, liveDir); } catch { noopThrew = true; }
check(!noopThrew, 'restore with no S3 config must be a no-op');

// ── verdict ─────────────────────────────────────────────────────────────────
for (const d of [work, liveDir, restoreDir, saved]) rmSync(d, { recursive: true, force: true });
if (fails.length) {
  console.error(`session-store.test FAILED (${fails.length}):`);
  for (const f of fails) console.error(`  ✗ ${f}`);
  process.exit(1);
}
console.log('session-store.test OK — restore and upload move the session profile only; restore fail-loud + typed; upload warn-only.');
