/**
 * session-store — persist & retrieve a browser session (cookies / Local Storage /
 * Login Data) so a login done once survives across browser launches.
 *
 * Two backends, one auth-essential manifest:
 *   - S3   (syncBrowserDataFromS3 / syncBrowserDataToS3) — the production path,
 *          shells out to the `aws` CLI. Carved verbatim from vexa-bot/s3-sync.ts.
 *   - local (loadSessionLocal / saveSessionLocal) — fs copy to/from a named dir,
 *          for desktop/dev with no S3 creds.
 *
 * The Chromium *persistent context* profile dir (BROWSER_DATA_DIR) IS the live
 * session; these helpers just copy the auth-essential subset of it in/out of a
 * durable store. Cache/GPU/IndexedDB junk is excluded — ~200KB, not the full profile.
 */
import { execSync } from 'child_process';
import { existsSync, unlinkSync, mkdirSync, cpSync, mkdtempSync, rmSync, readdirSync, readlinkSync, statSync } from 'fs';
import { join, dirname, basename } from 'path';

export const BROWSER_DATA_DIR = process.env.BROWSER_DATA_DIR || '/tmp/browser-data';

/**
 * A fresh, caller-owned Chromium profile dir: `${BROWSER_DATA_DIR}-XXXXXX`.
 *
 * Concurrent browsers MUST NOT share a profile dir: Chromium takes a SingletonLock on it,
 * and a second launch against a locked dir prints "Opening in existing browser session."
 * and exits — every bot after the first dies <1s (#478). Anything that may launch more
 * than one browser per filesystem (process-mode bots in vexa-lite) gets its dir from here
 * and removes it with removeProfileDir() on teardown.
 */
export function makeEphemeralProfileDir(): string {
  mkdirSync(dirname(BROWSER_DATA_DIR), { recursive: true });
  sweepStaleProfileDirs();
  return mkdtempSync(`${BROWSER_DATA_DIR}-`);
}

function pidAlive(pid: number): boolean {
  try { process.kill(pid, 0); return true; } catch { return false; }
}

/**
 * Remove sibling ephemeral profile dirs whose browser is gone. A workload killed hard
 * (runtime stop = SIGKILL) never runs its close() cleanup, so each launch sweeps instead:
 * Chromium's SingletonLock is a symlink to `<host>-<pid>` — dead pid ⇒ stale dir; no lock
 * at all ⇒ stale after 1h (browser never launched, or launched+closed cleanly elsewhere).
 * Best-effort by design: pid reuse just defers removal to a later sweep.
 */
export function sweepStaleProfileDirs(): void {
  const parent = dirname(BROWSER_DATA_DIR);
  const prefix = `${basename(BROWSER_DATA_DIR)}-`;
  let names: string[];
  try { names = readdirSync(parent).filter((n) => n.startsWith(prefix)); } catch { return; }
  for (const n of names) {
    const p = join(parent, n);
    try {
      let stale: boolean;
      try {
        const pid = Number(readlinkSync(join(p, 'SingletonLock')).split('-').pop());
        stale = !(pid > 0 && pidAlive(pid));
      } catch {
        stale = Date.now() - statSync(p).mtimeMs > 60 * 60 * 1000;
      }
      if (stale) rmSync(p, { recursive: true, force: true });
    } catch { /* best-effort */ }
  }
}

/** Best-effort removal of a profile dir created by makeEphemeralProfileDir(). */
export function removeProfileDir(dir: string): void {
  try { rmSync(dir, { recursive: true, force: true }); } catch { /* best-effort */ }
}

export const BROWSER_CACHE_EXCLUDES = [
  '*/Cache/*', '*/Code Cache/*', '*/GrShaderCache/*', '*/ShaderCache/*', '*/GraphiteDawnCache/*',
  '*/Service Worker/*', '*BrowserMetrics*',
  'SingletonLock', 'SingletonCookie', 'SingletonSocket',
  '*/GPUCache/*', '*/DawnGraphiteCache/*', '*/DawnWebGPUCache/*',
  '*/blob_storage/*', '*/File System/*', '*/IndexedDB/*',
];

export interface S3Config {
  userdataS3Path?: string;
  s3Endpoint?: string;
  s3Bucket?: string;
  s3AccessKey?: string;
  s3SecretKey?: string;
}

// The auth-essential subset of a Chromium profile — cookies, localStorage, login
// data, prefs. Shared by both the S3 and local backends so they persist the same
// bits. ~200KB total (vs minutes for a full-profile sync).
const AUTH_ESSENTIAL_FILES = [
  'Local State',
  'Default/Cookies',
  'Default/Cookies-journal',
  'Default/Preferences',
  'Default/Secure Preferences',
  'Default/Login Data',
  'Default/Login Data-journal',
  'Default/Login Data For Account',
  'Default/Login Data For Account-journal',
  'Default/Network Persistent State',
  'Default/Web Data',
];

const AUTH_ESSENTIAL_DIRS = [
  'Default/Local Storage',
  'Default/Session Storage',
];

// ── S3 backend (production) ───────────────────────────────────────────────

/**
 * A typed, attributed S3-session-sync failure. The restore half THROWS this (an authenticated
 * bot whose session cannot be restored must fail loud with the step named — never die on an
 * unattributed execSync, never silently join signed-out); the save half only WARNS (teardown
 * must never hang or fail the exit on a flaky upload — the durable copy simply stays at the
 * last restore).
 */
export class SessionSyncError extends Error {
  constructor(
    public readonly step: 'session-restore' | 'session-save',
    detail: string,
    public readonly cause?: unknown,
  ) {
    super(`[session-store] ${step} failed: ${detail}`);
    this.name = 'SessionSyncError';
  }
}

/** Attribute an `aws` execSync failure: a missing CLI (shell 127 / ENOENT) is named as such —
 *  the deployment's image must ship the aws CLI — anything else carries the exit status. */
function describeAwsFailure(err: any): string {
  const status = err?.status ?? err?.code;
  if (status === 127 || err?.code === 'ENOENT') {
    return 'aws CLI not found on PATH — the bot image (or provisioning host) must ship it';
  }
  return `aws exited with ${String(status ?? 'unknown')}: ${String(err?.message ?? err)}`;
}

function getS3Env(config: S3Config): Record<string, string> {
  return {
    ...process.env as Record<string, string>,
    AWS_ACCESS_KEY_ID: config.s3AccessKey || '',
    AWS_SECRET_ACCESS_KEY: config.s3SecretKey || '',
  };
}

export function s3Sync(localDir: string, s3Path: string, config: S3Config, direction: 'up' | 'down', excludes: string[] = []): void {
  if (!config.userdataS3Path || !config.s3Endpoint || !config.s3Bucket) return;
  const s3Uri = `s3://${config.s3Bucket}/${s3Path}`;
  const excludeArgs = excludes.map(e => `--exclude "${e}"`).join(' ');
  const deleteArg = '';
  const [src, dst] = direction === 'down' ? [s3Uri, `${localDir}/`] : [`${localDir}/`, s3Uri];
  console.log(`[s3-sync] S3 sync ${direction}: ${src} → ${dst}`);
  try {
    execSync(
      `aws s3 sync "${src}" "${dst}" --endpoint-url "${config.s3Endpoint}" ${deleteArg} ${excludeArgs}`,
      { env: getS3Env(config), stdio: 'inherit', timeout: 300000 }
    );
  } catch (err: any) {
    // Attributed, typed failure naming the sync step + target (#724 C3). The pre-guard shape —
    // an unguarded execSync — killed the process before Chromium launched with nothing on the
    // log naming the step (the #461 signature).
    throw new SessionSyncError(
      direction === 'down' ? 'session-restore' : 'session-save',
      `${describeAwsFailure(err)} (endpoint ${config.s3Endpoint}, path ${s3Path})`,
      err,
    );
  }
}

export function syncBrowserDataFromS3(config: S3Config, dataDir: string = BROWSER_DATA_DIR): void {
  s3Sync(dataDir, `${config.userdataS3Path}/browser-data`, config, 'down', BROWSER_CACHE_EXCLUDES);
}

/**
 * Upload the auth-essential subset of a LIVE profile dir to the durable S3 copy — the write-back
 * half of restore→use→write-back (#725 C1). `dataDir` names the profile the browser actually ran
 * on (a per-bot ephemeral `${BROWSER_DATA_DIR}-XXXXXX` dir — symmetric with
 * `syncBrowserDataFromS3(config, dataDir)`). Per-item failures are attributed warnings, never a
 * throw and never unbounded (each call carries its own timeout) — a flaky upload on teardown
 * leaves the durable copy at the last restore, it never hangs the exit. Returns the number of
 * items uploaded (0 ⇒ nothing durable changed — provisioning treats that as failure).
 */
export function syncBrowserDataToS3(config: S3Config, dataDir: string = BROWSER_DATA_DIR): number {
  if (!config.userdataS3Path || !config.s3Endpoint || !config.s3Bucket) return 0;
  const s3Base = `s3://${config.s3Bucket}/${config.userdataS3Path}/browser-data`;
  const env = getS3Env(config);
  const endpoint = `--endpoint-url "${config.s3Endpoint}"`;
  let uploaded = 0;

  console.log(`[s3-sync] S3 save (auth-essential files only) from ${dataDir}...`);

  for (const file of AUTH_ESSENTIAL_FILES) {
    const local = join(dataDir, file);
    if (!existsSync(local)) continue;
    try {
      execSync(`aws s3 cp "${local}" "${s3Base}/${file}" ${endpoint}`, { env, stdio: 'pipe', timeout: 10000 });
      uploaded++;
    } catch (err: any) {
      console.log(`[s3-sync] Warning: failed to upload ${file}: ${describeAwsFailure(err)}`);
    }
  }

  for (const dir of AUTH_ESSENTIAL_DIRS) {
    const local = join(dataDir, dir);
    if (!existsSync(local)) continue;
    try {
      execSync(`aws s3 sync "${local}/" "${s3Base}/${dir}/" ${endpoint}`, { env, stdio: 'pipe', timeout: 10000 });
      uploaded++;
    } catch (err: any) {
      console.log(`[s3-sync] Warning: failed to sync ${dir}: ${describeAwsFailure(err)}`);
    }
  }

  console.log(`[s3-sync] Uploaded ${uploaded} auth-essential items`);
  return uploaded;
}

// ── Local backend (desktop/dev, no S3 creds) ─────────────────────────────

/** Copy the auth-essential profile subset OUT of a live profile dir into a durable dir. */
export function saveSessionLocal(destDir: string, srcDataDir: string = BROWSER_DATA_DIR): number {
  mkdirSync(destDir, { recursive: true });
  let n = 0;
  for (const file of AUTH_ESSENTIAL_FILES) {
    const src = join(srcDataDir, file);
    if (!existsSync(src)) continue;
    const dst = join(destDir, file);
    mkdirSync(dirname(dst), { recursive: true });
    try { cpSync(src, dst); n++; } catch (err: any) { console.log(`[session-store] save skip ${file}: ${err.message}`); }
  }
  for (const dir of AUTH_ESSENTIAL_DIRS) {
    const src = join(srcDataDir, dir);
    if (!existsSync(src)) continue;
    try { cpSync(src, join(destDir, dir), { recursive: true }); n++; } catch (err: any) { console.log(`[session-store] save skip ${dir}: ${err.message}`); }
  }
  console.log(`[session-store] Saved ${n} auth-essential items → ${destDir}`);
  return n;
}

/** Copy the auth-essential profile subset back INTO a profile dir before launch. */
export function loadSessionLocal(srcDir: string, destDataDir: string = BROWSER_DATA_DIR): number {
  if (!existsSync(srcDir)) { console.log(`[session-store] no saved session at ${srcDir}`); return 0; }
  mkdirSync(destDataDir, { recursive: true });
  let n = 0;
  for (const file of AUTH_ESSENTIAL_FILES) {
    const src = join(srcDir, file);
    if (!existsSync(src)) continue;
    const dst = join(destDataDir, file);
    mkdirSync(dirname(dst), { recursive: true });
    try { cpSync(src, dst); n++; } catch (err: any) { console.log(`[session-store] load skip ${file}: ${err.message}`); }
  }
  for (const dir of AUTH_ESSENTIAL_DIRS) {
    const src = join(srcDir, dir);
    if (!existsSync(src)) continue;
    try { cpSync(src, join(destDataDir, dir), { recursive: true }); n++; } catch (err: any) { console.log(`[session-store] load skip ${dir}: ${err.message}`); }
  }
  console.log(`[session-store] Loaded ${n} auth-essential items ← ${srcDir}`);
  return n;
}

// ── Profile hygiene ───────────────────────────────────────────────────────

export function cleanStaleLocks(dir: string = BROWSER_DATA_DIR): void {
  const lockFiles = ['SingletonLock', 'SingletonCookie', 'SingletonSocket'];
  for (const f of lockFiles) {
    const p = join(dir, f);
    if (existsSync(p)) {
      try { unlinkSync(p); } catch {}
      console.log(`[session-store] Removed stale lock: ${f}`);
    }
  }
}

export function ensureBrowserDataDir(dir: string = BROWSER_DATA_DIR): void {
  mkdirSync(dir, { recursive: true });
}
