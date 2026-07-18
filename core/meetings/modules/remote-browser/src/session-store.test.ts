/**
 * session-store.test — the S3 sync surface, offline (no real S3, no network).
 *
 * A fake `aws` executable on PATH records every invocation to a log file, so the REAL
 * syncBrowserDataToS3 / syncBrowserDataFromS3 run end-to-end and the assertions read
 * what actually crossed the shell boundary. Covers the two contract-level behaviours:
 *
 *  1. WRITE-BACK SOURCE DIR (#725 C1) — syncBrowserDataToS3(config, dataDir) uploads
 *     from the LIVE per-bot ephemeral dir it is handed, symmetric with the restore half.
 *  2. FAIL-LOUD RESTORE (#724 C3) — a failing download surfaces as a typed
 *     SessionSyncError naming the session-restore step (and names a missing aws CLI),
 *     never an unattributed process death; the upload half stays warn-only and reports 0.
 *
 * Same shape as auth.smoke.test.ts (tsx + exit code, no assert lib).
 */
import { execSync } from 'child_process';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync, existsSync, chmodSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';
import { syncBrowserDataToS3, syncBrowserDataFromS3, SessionSyncError, type S3Config } from './session-store';

const fails: string[] = [];
const check = (cond: boolean, msg: string) => { if (!cond) fails.push(msg); };

const work = mkdtempSync(join(tmpdir(), 'session-store-test-'));
const binDir = join(work, 'bin');
const logFile = join(work, 'aws-calls.log');
mkdirSync(binDir, { recursive: true });

/** Install the fake `aws` on PATH. mode: 'ok' records + exits 0; 'fail' records + exits 1. */
function installFakeAws(mode: 'ok' | 'fail'): void {
  const script = `#!/bin/sh\necho "$@" >> "${logFile}"\n${mode === 'fail' ? 'exit 1' : 'exit 0'}\n`;
  writeFileSync(join(binDir, 'aws'), script);
  chmodSync(join(binDir, 'aws'), 0o755);
}
const realPath = process.env.PATH ?? '';
const withFakePath = `${binDir}:${realPath}`;
const calls = (): string[] => (existsSync(logFile) ? readFileSync(logFile, 'utf8').trim().split('\n').filter(Boolean) : []);
const resetLog = (): void => { rmSync(logFile, { force: true }); };

const config: S3Config = {
  userdataS3Path: 'userdata/test-identity',
  s3Endpoint: 'http://minio.test:9000',
  s3Bucket: 'vexa-test',
  s3AccessKey: 'k',
  s3SecretKey: 's',
};

// A live-looking profile dir with an auth-essential subset present.
const liveDir = mkdtempSync(join(tmpdir(), 'browser-data-'));
mkdirSync(join(liveDir, 'Default', 'Local Storage'), { recursive: true });
writeFileSync(join(liveDir, 'Default', 'Cookies'), 'rotated-cookie-bytes');
writeFileSync(join(liveDir, 'Local State'), '{}');
writeFileSync(join(liveDir, 'Default', 'Local Storage', 'x.ldb'), 'ls');

// ── 1. write-back uploads from the dataDir it is handed (#725 C1) ───────────
installFakeAws('ok');
process.env.PATH = withFakePath;
resetLog();
const uploaded = syncBrowserDataToS3(config, liveDir);
check(uploaded === 3, `write-back should upload the 3 present auth-essential items, got ${uploaded}`);
const upCalls = calls();
check(upCalls.length === 3, `expected 3 aws invocations, got ${upCalls.length}`);
check(upCalls.every((c) => c.includes(liveDir)), 'every upload must read from the LIVE dataDir handed in (not BROWSER_DATA_DIR)');
check(upCalls.some((c) => c.includes(`${liveDir}/Default/Cookies`)), 'the rotated Cookies file must be among the uploads');
check(upCalls.every((c) => c.includes('s3://vexa-test/userdata/test-identity/browser-data')), 'uploads must target the userdata prefix');

// ── 1b. incomplete config is a no-op (guest/anonymous path untouched) ───────
resetLog();
const noop = syncBrowserDataToS3({ userdataS3Path: 'x' }, liveDir);   // no endpoint/bucket
check(noop === 0 && calls().length === 0, 'incomplete S3 config must be a no-op (0 uploads, no aws calls)');

// ── 2. upload failures are warnings, never throws (teardown safety) ─────────
installFakeAws('fail');
resetLog();
let threw = false;
let count = -1;
try { count = syncBrowserDataToS3(config, liveDir); } catch { threw = true; }
check(!threw, 'write-back must NEVER throw on upload failure (teardown path)');
check(count === 0, `all-failing uploads must report 0 items, got ${count}`);

// ── 3. restore failure is a typed, attributed SessionSyncError (#724 C3) ────
let restoreErr: unknown;
try { syncBrowserDataFromS3(config, liveDir); } catch (e) { restoreErr = e; }
check(restoreErr instanceof SessionSyncError, 'failing restore must throw SessionSyncError (typed, not a bare execSync death)');
if (restoreErr instanceof SessionSyncError) {
  check(restoreErr.step === 'session-restore', `restore error must name the session-restore step, got '${restoreErr.step}'`);
  check(restoreErr.message.includes('http://minio.test:9000'), 'restore error must name the endpoint');
}

// ── 3b. a missing aws CLI is named as such ──────────────────────────────────
process.env.PATH = join(work, 'empty-bin');   // no aws anywhere on PATH
mkdirSync(process.env.PATH, { recursive: true });
let missingErr: unknown;
try { syncBrowserDataFromS3(config, liveDir); } catch (e) { missingErr = e; }
check(missingErr instanceof SessionSyncError, 'missing aws CLI must still surface as SessionSyncError');
check(String((missingErr as Error)?.message ?? '').includes('aws CLI not found'), 'missing aws CLI must be named in the error');
process.env.PATH = realPath;

// ── 3c. restore with incomplete config is a no-op (anonymous bots unaffected) ──
let noopThrew = false;
try { syncBrowserDataFromS3({}, liveDir); } catch { noopThrew = true; }
check(!noopThrew, 'restore with no S3 config must be a no-op');

// ── verdict ─────────────────────────────────────────────────────────────────
rmSync(work, { recursive: true, force: true });
rmSync(liveDir, { recursive: true, force: true });
if (fails.length) {
  console.error(`session-store.test FAILED (${fails.length}):`);
  for (const f of fails) console.error(`  ✗ ${f}`);
  process.exit(1);
}
console.log('session-store.test OK — write-back dataDir honored; restore fail-loud + typed; teardown warn-only.');
// keep execSync imported reference honest (silences unused-import under strict configs)
void execSync;
