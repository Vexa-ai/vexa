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
import { existsSync, unlinkSync, mkdirSync, cpSync, mkdtempSync, rmSync } from 'fs';
import { join, dirname } from 'path';

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
  return mkdtempSync(`${BROWSER_DATA_DIR}-`);
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
  execSync(
    `aws s3 sync "${src}" "${dst}" --endpoint-url "${config.s3Endpoint}" ${deleteArg} ${excludeArgs}`,
    { env: getS3Env(config), stdio: 'inherit', timeout: 300000 }
  );
}

export function syncBrowserDataFromS3(config: S3Config, dataDir: string = BROWSER_DATA_DIR): void {
  s3Sync(dataDir, `${config.userdataS3Path}/browser-data`, config, 'down', BROWSER_CACHE_EXCLUDES);
}

export function syncBrowserDataToS3(config: S3Config): void {
  if (!config.userdataS3Path || !config.s3Endpoint || !config.s3Bucket) return;
  const s3Base = `s3://${config.s3Bucket}/${config.userdataS3Path}/browser-data`;
  const env = getS3Env(config);
  const endpoint = `--endpoint-url "${config.s3Endpoint}"`;
  let uploaded = 0;

  console.log(`[s3-sync] S3 save (auth-essential files only)...`);

  for (const file of AUTH_ESSENTIAL_FILES) {
    const local = join(BROWSER_DATA_DIR, file);
    if (!existsSync(local)) continue;
    try {
      execSync(`aws s3 cp "${local}" "${s3Base}/${file}" ${endpoint}`, { env, stdio: 'pipe', timeout: 10000 });
      uploaded++;
    } catch (err: any) {
      console.log(`[s3-sync] Warning: failed to upload ${file}: ${err.message}`);
    }
  }

  for (const dir of AUTH_ESSENTIAL_DIRS) {
    const local = join(BROWSER_DATA_DIR, dir);
    if (!existsSync(local)) continue;
    try {
      execSync(`aws s3 sync "${local}/" "${s3Base}/${dir}/" ${endpoint}`, { env, stdio: 'pipe', timeout: 10000 });
      uploaded++;
    } catch (err: any) {
      console.log(`[s3-sync] Warning: failed to sync ${dir}: ${err.message}`);
    }
  }

  console.log(`[s3-sync] Uploaded ${uploaded} auth-essential items`);
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
