/**
 * session-store — persist & retrieve a browser session (cookies / Local Storage /
 * Login Data) so a login done once survives across browser launches.
 *
 * One profile definition, three backends:
 *   - SESSION_PROFILE — the auth-essential subset of a Chromium profile, defined by the
 *          session-profile.v1 contract (core/meetings/contracts/session-profile.v1) and read from
 *          this package's verbatim copy of its schema. Nothing outside it is restored, uploaded,
 *          saved or loaded, and meeting-api's write-back route, which reads the same file,
 *          accepts nothing outside it.
 *   - S3   (syncBrowserDataFromS3 / syncBrowserDataToS3) — shells out to the `aws` CLI. A bot
 *          RESTORES with the deployment's read-only userdata key; the operator's `make login`
 *          UPLOADS with a key that can write the prefix. A bot never writes the store itself:
 *          its write-back goes through meeting-api (readSessionProfile builds the body).
 *   - local (loadSessionLocal / saveSessionLocal) — fs copy to/from a named dir,
 *          for desktop/dev with no S3 creds.
 *
 * The Chromium *persistent context* profile dir (BROWSER_DATA_DIR) IS the live
 * session; these helpers just copy the auth-essential subset of it in/out of a
 * durable store. Cache/GPU/IndexedDB/extensions are never part of it — ~200KB, not the full profile.
 */
import { execFileSync } from 'child_process';
import { existsSync, unlinkSync, mkdirSync, copyFileSync, mkdtempSync, rmSync, readdirSync, readlinkSync, statSync, lstatSync, readFileSync, writeFileSync } from 'fs';
import { join, dirname, basename } from 'path';
import SESSION_PROFILE_CONTRACT from './session-profile.v1.schema.json';

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

/** Cache and lock paths of a full Chromium profile. Kept for callers that sync a whole profile;
 *  the session store itself never needs it — it moves SESSION_PROFILE paths only. */
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

// ── The session profile ───────────────────────────────────────────────────

/** The shape of a session profile (session-profile.v1 `#/$defs/Profile`). */
export interface SessionProfileSpec {
  /** Exact profile-relative paths (cookies, login data, prefs, web data). */
  readonly files: readonly string[];
  /** LevelDB directories (Local Storage, Session Storage); their regular files whose names match
   *  `leveldbFile` are part of the profile, nothing below them is. */
  readonly leveldbDirs: readonly string[];
  /** Regex source for a LevelDB file name — the same source string meeting-api compiles. */
  readonly leveldbFile: string;
  readonly maxFileBytes: number;
  readonly maxTotalBytes: number;
  readonly maxFiles: number;
}

const SPEC: SessionProfileSpec = SESSION_PROFILE_CONTRACT.$defs.SessionProfile.const;

/** SESSION_PROFILE — the one definition of what a stored browser session may contain: the
 *  session-profile.v1 contract's `$defs.SessionProfile`, read from session-profile.v1.schema.json,
 *  a verbatim copy of the contract's schema (gate:fact-parity `session-profile-contract`). meeting-api
 *  reads the same file for its write-back route. */
export const SESSION_PROFILE: SessionProfileSpec = Object.freeze({
  files: Object.freeze([...SPEC.files]),
  leveldbDirs: Object.freeze([...SPEC.leveldbDirs]),
  leveldbFile: SPEC.leveldbFile,
  maxFileBytes: SPEC.maxFileBytes,
  maxTotalBytes: SPEC.maxTotalBytes,
  maxFiles: SPEC.maxFiles,
});

const LEVELDB_FILE = new RegExp(SESSION_PROFILE.leveldbFile);

const CONTROL_CHAR = /[\u0000-\u001f\u007f]/;

/** True when `rel` (profile-relative, '/'-separated) names a SESSION_PROFILE path. Anything with an
 *  empty, '.' or '..' segment, a backslash, a control character or a leading '/' is never one. The
 *  contract's PathVectors goldens hold this to meeting-api's matcher (session-store.test.ts). */
export function isSessionProfilePath(rel: string): boolean {
  if (typeof rel !== 'string' || !rel || rel.length > 512) return false;
  if (CONTROL_CHAR.test(rel) || rel.includes('\\') || rel.startsWith('/')) return false;
  const parts = rel.split('/');
  if (parts.some((p) => p === '' || p === '.' || p === '..')) return false;
  if (SESSION_PROFILE.files.includes(rel)) return true;
  const dir = parts.slice(0, -1).join('/');
  return SESSION_PROFILE.leveldbDirs.includes(dir) && LEVELDB_FILE.test(parts[parts.length - 1]);
}

// ── What a stored session carries ─────────────────────────────────────────

/** LevelDB dirs of the contract's profile that no copy of a session carries any more: Session
 *  Storage is per-tab state of the browser session that wrote it (the meeting page's included) and
 *  feeds tab/session restore; a sign-in never needs it. The contract still ALLOWS these paths (an
 *  older bot or an already-saved account may hold them), so they are dropped here, in every
 *  direction: never collected for a write-back or an upload, never written on a restore. */
export const SESSION_STATE_DIRS: readonly string[] = Object.freeze(['Default/Session Storage']);

/** Files of the contract's profile that hold CREDENTIALS, not a session: saved passwords (Login Data,
 *  the account-scoped Login Data For Account, their journals) and autofill / payment data (Web Data).
 *  A bot account carries a session, never a credential: these are never collected for a write-back
 *  or an upload and never written on a restore — an already-saved profile holding them is restored
 *  without them. The contract still ALLOWS the paths (fewer of them are sent; it is unchanged). */
export const CREDENTIAL_FILES: readonly string[] = Object.freeze([
  'Default/Login Data', 'Default/Login Data-journal',
  'Default/Login Data For Account', 'Default/Login Data For Account-journal',
  'Default/Web Data', 'Default/Web Data-journal',
]);

/** True when `rel` is a SESSION_PROFILE path a stored session still carries (not Session Storage,
 *  not a credential file). */
export function isCarriedProfilePath(rel: string): boolean {
  return isSessionProfilePath(rel) && !CREDENTIAL_FILES.includes(rel)
    && !SESSION_STATE_DIRS.includes(rel.split('/').slice(0, -1).join('/'));
}

const PREFERENCES = 'Default/Preferences';
/** Chromium's `session.restore_on_startup` value for "continue where you left off". */
const RESTORE_CONTINUE = 1;

/**
 * The profile's Preferences with the keys that make Chromium bring back an earlier session's PAGES
 * reset: `profile.exit_type` "Normal" and `profile.exited_cleanly` true (no crashed-session restore)
 * and no `session.startup_urls` (no pinned URLs reopened). `session.restore_on_startup` is set to
 * "continue" (1), because that is what keeps SESSION-ONLY cookies across a relaunch: with any other
 * value Chromium deletes them at start, and Zoom's sign-in cookies (`_zm_ssid`, `_zm_page_auth`,
 * `cred`) are session-only — measured 10 Nov on bbb: a session-only cookie set in run 1 was present in
 * run 2 with Normal/1 and gone with Normal/5 (c1481fa2's value, which signed a saved account out).
 * "Continue" reopens no earlier tab here: the tab state (`Sessions/`, Session Storage) is never
 * carried, and the launch keeps exactly one page on about:blank (browser.ts keepOneBlankPage).
 * Every other key (the sign-in's account and settings state) is kept as it is. Bytes that are not a
 * JSON object, or need no change, come back unchanged.
 */
export function normalizePreferences(data: Buffer): Buffer {
  let prefs: any;
  try { prefs = JSON.parse(data.toString('utf8')); } catch { return data; }
  if (!prefs || typeof prefs !== 'object' || Array.isArray(prefs)) return data;
  const obj = (v: unknown): Record<string, unknown> => (v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {});
  const profile = obj(prefs.profile);
  const session = obj(prefs.session);
  const autofill = obj(prefs.autofill);
  if (profile.exit_type === 'Normal' && profile.exited_cleanly === true
    && session.restore_on_startup === RESTORE_CONTINUE && !('startup_urls' in session)
    && hasNoCredentialPrefs(prefs)) return data;
  prefs.profile = { ...profile, exit_type: 'Normal', exited_cleanly: true, password_manager_leak_detection: false };
  const { startup_urls: _dropped, ...rest } = session;
  prefs.session = { ...rest, restore_on_startup: RESTORE_CONTINUE };
  prefs.credentials_enable_service = false;
  prefs.credentials_enable_autosignin = false;
  prefs.autofill = { ...autofill, profile_enabled: false, credit_card_enabled: false };
  return Buffer.from(JSON.stringify(prefs), 'utf8');
}

/**
 * A bot browser never saves or offers a password, an address or a card: Chromium's own prefs
 * (names checked in the Chromium binary the bots run, 10 Nov): `credentials_enable_service` (offer to
 * save passwords), `credentials_enable_autosignin`, `profile.password_manager_leak_detection`,
 * `autofill.profile_enabled`, `autofill.credit_card_enabled` — all false. Prefs only: nothing about
 * how the browser presents itself to a site changes.
 */
function hasNoCredentialPrefs(prefs: any): boolean {
  return prefs.credentials_enable_service === false && prefs.credentials_enable_autosignin === false
    && prefs.profile?.password_manager_leak_detection === false
    && prefs.autofill?.profile_enabled === false && prefs.autofill?.credit_card_enabled === false;
}

/**
 * A fresh sign-in profile (the person types the password there) gets the same Preferences a restore
 * gets — no password manager, no autofill — before the browser opens on it. An existing Preferences
 * keeps every other key; a missing one is created. Never throws: a profile it cannot write is logged.
 */
export function prepareSignInProfile(dataDir: string): void {
  try {
    mkdirSync(join(dataDir, 'Default'), { recursive: true });
    if (!realDirChain(dataDir, 'Default')) { console.log('[session-store] sign-in profile: Default is not a real directory; prefs not set'); return; }
    const abs = join(dataDir, 'Default', 'Preferences');
    let before = Buffer.from('{}');
    if (existsSync(abs)) {
      if (!lstatSync(abs).isFile()) { console.log('[session-store] sign-in profile: Preferences is not a regular file; prefs not set'); return; }
      before = readFileSync(abs);
    }
    const after = normalizePreferences(before);
    if (after !== before || !existsSync(abs)) writeFileSync(abs, after, { mode: 0o600 });
  } catch (e: any) {
    console.log(`[session-store] sign-in profile: prefs not set (${String(e?.code || e?.name || 'error')})`);
  }
}

/** normalizePreferences on the Preferences file of a profile dir, in place (a regular file only). */
export function normalizeProfileDir(dataDir: string): void {
  const abs = join(dataDir, ...PREFERENCES.split('/'));
  try {
    if (!realDirChain(dataDir, 'Default') || !lstatSync(abs).isFile()) return;
    const before = readFileSync(abs);
    const after = normalizePreferences(before);
    if (after !== before) writeFileSync(abs, after, { mode: 0o600 });
  } catch { /* no Preferences: nothing to reset */ }
}

/** One SESSION_PROFILE file found in a profile dir. */
export interface ProfileFile {
  /** profile-relative, '/'-separated */
  path: string;
  /** absolute path on disk */
  abs: string;
  size: number;
}

/** Every directory from `root` (exclusive) down to `rel` must be a real directory — a symlinked
 *  `Default/` would otherwise route reads of the profile somewhere else. */
function realDirChain(root: string, rel: string): boolean {
  let cur = root;
  for (const part of rel.split('/').filter(Boolean)) {
    cur = join(cur, part);
    try {
      const st = lstatSync(cur);
      if (!st.isDirectory() || st.isSymbolicLink()) return false;
    } catch {
      return false;
    }
  }
  return true;
}

/**
 * The SESSION_PROFILE files present in `dataDir`: regular files only (a symlink, a directory or a
 * device is never part of a session), each within `maxFileBytes`, at most `maxFiles` and
 * `maxTotalBytes` together. Deterministic order: `files` first, then each LevelDB dir by name.
 */
export function collectSessionProfile(dataDir: string): ProfileFile[] {
  const out: ProfileFile[] = [];
  let total = 0;
  const consider = (rel: string): void => {
    if (!isCarriedProfilePath(rel)) return;   // no Session Storage, no credential file
    if (!realDirChain(dataDir, rel.split('/').slice(0, -1).join('/'))) return;
    const abs = join(dataDir, ...rel.split('/'));
    let st;
    try { st = lstatSync(abs); } catch { return; }
    if (!st.isFile()) {
      console.log(`[session-store] skip ${rel}: not a regular file`);
      return;
    }
    if (st.size > SESSION_PROFILE.maxFileBytes) {
      console.log(`[session-store] skip ${rel}: ${st.size} bytes exceeds ${SESSION_PROFILE.maxFileBytes}`);
      return;
    }
    if (out.length >= SESSION_PROFILE.maxFiles || total + st.size > SESSION_PROFILE.maxTotalBytes) {
      console.log(`[session-store] skip ${rel}: the session profile is already at its size or file limit`);
      return;
    }
    out.push({ path: rel, abs, size: st.size });
    total += st.size;
  };
  for (const f of SESSION_PROFILE.files) consider(f);
  for (const d of SESSION_PROFILE.leveldbDirs) {
    if (SESSION_STATE_DIRS.includes(d)) continue;   // carried by no copy (isCarriedProfilePath)
    if (!realDirChain(dataDir, d)) continue;
    let names: string[];
    try { names = readdirSync(join(dataDir, ...d.split('/'))).sort(); } catch { continue; }
    for (const n of names) consider(`${d}/${n}`);
  }
  return out;
}

/** The SESSION_PROFILE files of `dataDir` with their bytes — the body of a write-back. A file that
 *  changed size past the limit between the scan and the read is left out. */
export function readSessionProfile(dataDir: string): Array<{ path: string; data: Buffer }> {
  const out: Array<{ path: string; data: Buffer }> = [];
  for (const f of collectSessionProfile(dataDir)) {
    let data: Buffer;
    try { data = readFileSync(f.abs); } catch { continue; }
    if (data.length > SESSION_PROFILE.maxFileBytes) continue;
    if (f.path === PREFERENCES) data = normalizePreferences(data);
    out.push({ path: f.path, data });
  }
  return out;
}

/**
 * Write a session profile received in memory (a restore answered over HTTP — session-profile.v1
 * `WritebackBody`, decoded) into `dataDir`. The whole set is checked before anything is written, and
 * one bad entry refuses all of it: every path must be a SESSION_PROFILE path named once (no
 * traversal, no absolute or backslash names), every file within `maxFileBytes`, at most `maxFiles`
 * and `maxTotalBytes` together. Directories are created as real directories; an existing symlink,
 * directory or device in the way refuses the write. Throws SessionSyncError('session-restore');
 * returns the number of files written. Of an accepted set, Session Storage (SESSION_STATE_DIRS) is
 * not written, and Preferences is written normalised (normalizePreferences): an account saved before
 * either rule opens clean on its next use, with no new sign-in.
 */
export function writeSessionProfile(dataDir: string, files: ReadonlyArray<{ path: string; data: Buffer }>): number {
  const refuse = (detail: string): never => { throw new SessionSyncError('session-restore', detail); };
  if (!Array.isArray(files)) refuse('the restored profile is not a list of files');
  if (files.length > SESSION_PROFILE.maxFiles) refuse(`the restored profile names ${files.length} files, over ${SESSION_PROFILE.maxFiles}`);
  const seen = new Set<string>();
  let total = 0;
  files.forEach((f, i) => {
    if (!f || !isSessionProfilePath(f.path)) refuse(`entry ${i} is not a session profile path`);
    if (seen.has(f.path)) refuse(`entry ${i} names a path twice`);
    seen.add(f.path);
    if (!Buffer.isBuffer(f.data)) refuse(`entry ${i} carries no bytes`);
    if (f.data.length > SESSION_PROFILE.maxFileBytes) refuse(`entry ${i} is over ${SESSION_PROFILE.maxFileBytes} bytes`);
    total += f.data.length;
  });
  if (total > SESSION_PROFILE.maxTotalBytes) refuse(`the restored profile is over ${SESSION_PROFILE.maxTotalBytes} bytes`);
  mkdirSync(dataDir, { recursive: true });
  let written = 0;
  for (const f of files) {
    if (!isCarriedProfilePath(f.path)) continue;
    const parts = f.path.split('/');
    let cur = dataDir;
    for (const part of parts.slice(0, -1)) {
      cur = join(cur, part);
      let st;
      try { st = lstatSync(cur); } catch { st = undefined; }
      if (!st) mkdirSync(cur);
      else if (!st.isDirectory() || st.isSymbolicLink()) refuse(`a parent of ${f.path} is not a real directory`);
    }
    const abs = join(dataDir, ...parts);
    let st;
    try { st = lstatSync(abs); } catch { st = undefined; }
    if (st && !st.isFile()) refuse(`${f.path} exists and is not a regular file`);
    writeFileSync(abs, f.path === PREFERENCES ? normalizePreferences(f.data) : f.data, { mode: 0o600 });
    written++;
  }
  return written;
}

// ── S3 backend ────────────────────────────────────────────────────────────

/**
 * A typed, attributed S3-session-sync failure. The restore half THROWS this (an authenticated
 * bot whose session cannot be restored must fail loud with the step named — never die on an
 * unattributed exec, never silently join signed-out); the save half only WARNS (teardown
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

/** Attribute an `aws` exec failure: a missing CLI (ENOENT / 127) is named as such —
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

/** `aws s3 sync` between a local dir and `s3://<bucket>/<s3Path>`. Filters are applied in order
 *  (excludes, then includes), so `excludes=['*']` + `includes=[...]` moves exactly those paths. */
export function s3Sync(
  localDir: string, s3Path: string, config: S3Config, direction: 'up' | 'down',
  excludes: string[] = [], includes: string[] = [],
): void {
  if (!config.userdataS3Path || !config.s3Endpoint || !config.s3Bucket) return;
  const s3Uri = `s3://${config.s3Bucket}/${s3Path}`;
  const [src, dst] = direction === 'down' ? [`${s3Uri}/`, `${localDir}/`] : [`${localDir}/`, `${s3Uri}/`];
  console.log(`[s3-sync] S3 sync ${direction}: ${src} → ${dst}`);
  try {
    // argv-exec, never a shell — config values are arguments, they cannot inject.
    execFileSync(
      'aws',
      ['s3', 'sync', src, dst, '--endpoint-url', config.s3Endpoint, '--no-follow-symlinks',
        ...excludes.flatMap(e => ['--exclude', e]), ...includes.flatMap(i => ['--include', i])],
      { env: getS3Env(config), stdio: 'inherit', timeout: 300000 }
    );
  } catch (err: any) {
    // Attributed, typed failure naming the sync step + target (#724 C3). The pre-guard shape —
    // an unguarded exec — killed the process before Chromium launched with nothing on the
    // log naming the step (the #461 signature).
    throw new SessionSyncError(
      direction === 'down' ? 'session-restore' : 'session-save',
      `${describeAwsFailure(err)} (endpoint ${config.s3Endpoint}, path ${s3Path})`,
      err,
    );
  }
}

/** The stored objects under `s3://<bucket>/<prefix>` as {key, size}. Throws SessionSyncError. */
function listStoredSession(config: S3Config, prefix: string): Array<{ key: string; size: number }> {
  let text: string;
  try {
    text = execFileSync(
      'aws',
      ['s3api', 'list-objects-v2', '--bucket', config.s3Bucket!, '--prefix', prefix,
        '--endpoint-url', config.s3Endpoint!, '--output', 'json'],
      { env: getS3Env(config), encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit'],
        timeout: 60000, maxBuffer: 32 * 1024 * 1024 },
    );
  } catch (err: any) {
    throw new SessionSyncError('session-restore',
      `${describeAwsFailure(err)} (endpoint ${config.s3Endpoint}, path ${prefix})`, err);
  }
  if (!text || !text.trim()) return [];
  let listing: any;
  try { listing = JSON.parse(text); } catch (err) {
    throw new SessionSyncError('session-restore',
      `the listing of ${prefix} is not JSON (endpoint ${config.s3Endpoint}, path ${prefix})`, err);
  }
  const contents = Array.isArray(listing?.Contents) ? listing.Contents : [];
  return contents
    .filter((o: any) => typeof o?.Key === 'string')
    .map((o: any) => ({ key: o.Key as string, size: Number(o.Size ?? 0) }));
}

/**
 * Restore the stored session into `dataDir`, SESSION_PROFILE paths only. The stored prefix is
 * listed first and every key is checked against the profile before anything is downloaded: an
 * object under the prefix that is not a profile path (or is larger than `maxFileBytes`) is never
 * fetched. Throws SessionSyncError('session-restore') when the store cannot be read.
 */
export function syncBrowserDataFromS3(config: S3Config, dataDir: string = BROWSER_DATA_DIR): void {
  if (!config.userdataS3Path || !config.s3Endpoint || !config.s3Bucket) return;
  const base = `${config.userdataS3Path}/browser-data`;
  const wanted: string[] = [];
  let ignored = 0;
  for (const { key, size } of listStoredSession(config, `${base}/`)) {
    const rel = key.startsWith(`${base}/`) ? key.slice(base.length + 1) : '';
    if (isCarriedProfilePath(rel) && size <= SESSION_PROFILE.maxFileBytes) wanted.push(rel);
    else ignored++;
  }
  if (ignored) console.log(`[s3-sync] ${ignored} stored object(s) a session does not carry (outside the profile, or Session Storage) left in place, not restored`);
  if (!wanted.length) {
    console.log(`[s3-sync] no session profile stored at s3://${config.s3Bucket}/${base}/`);
    return;
  }
  wanted.sort();
  s3Sync(dataDir, base, config, 'down', ['*'], wanted);
  normalizeProfileDir(dataDir);
}

/**
 * Upload the SESSION_PROFILE files of a LIVE profile dir to the durable S3 copy — the operator's
 * provisioning upload (`make login`), run with a key that can write the userdata prefix. Bots do
 * not call this: their write-back goes through meeting-api. Failures are attributed warnings,
 * never a throw; returns the number of profile files sent (0 ⇒ nothing durable changed —
 * provisioning treats that as failure).
 */
export function syncBrowserDataToS3(config: S3Config, dataDir: string = BROWSER_DATA_DIR): number {
  if (!config.userdataS3Path || !config.s3Endpoint || !config.s3Bucket) return 0;
  const items = collectSessionProfile(dataDir);
  console.log(`[s3-sync] S3 save (session profile only, ${items.length} file(s)) from ${dataDir}...`);
  if (!items.length) return 0;
  try {
    s3Sync(dataDir, `${config.userdataS3Path}/browser-data`, config, 'up', ['*'], items.map((i) => i.path));
  } catch (err: any) {
    console.log(`[s3-sync] Warning: session upload failed: ${String(err?.message ?? err)}`);
    return 0;
  }
  console.log(`[s3-sync] Uploaded ${items.length} session profile file(s)`);
  return items.length;
}

// ── Local backend (desktop/dev, no S3 creds) ─────────────────────────────

function copyProfile(srcDir: string, destDir: string, verb: 'save' | 'load'): number {
  let n = 0;
  for (const f of collectSessionProfile(srcDir)) {
    const dst = join(destDir, ...f.path.split('/'));
    try {
      mkdirSync(dirname(dst), { recursive: true });
      copyFileSync(f.abs, dst);
      n++;
    } catch (err: any) {
      console.log(`[session-store] ${verb} skip ${f.path}: ${err.message}`);
    }
  }
  return n;
}

/** Copy the SESSION_PROFILE subset OUT of a live profile dir into a durable dir. */
export function saveSessionLocal(destDir: string, srcDataDir: string = BROWSER_DATA_DIR): number {
  mkdirSync(destDir, { recursive: true });
  const n = copyProfile(srcDataDir, destDir, 'save');
  console.log(`[session-store] Saved ${n} session profile file(s) → ${destDir}`);
  return n;
}

/** Copy the SESSION_PROFILE subset back INTO a profile dir before launch. */
export function loadSessionLocal(srcDir: string, destDataDir: string = BROWSER_DATA_DIR): number {
  if (!existsSync(srcDir)) { console.log(`[session-store] no saved session at ${srcDir}`); return 0; }
  mkdirSync(destDataDir, { recursive: true });
  const n = copyProfile(srcDir, destDataDir, 'load');
  normalizeProfileDir(destDataDir);
  console.log(`[session-store] Loaded ${n} session profile file(s) ← ${srcDir}`);
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
