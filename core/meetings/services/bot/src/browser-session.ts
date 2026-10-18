/**
 * Browser-session mode — the bot workload a person signs a bot account in through, or that checks one.
 *
 * When the env carries `VEXA_BROWSER_SESSION` (JSON), the entry runs this instead of a meeting (index.ts
 * main). meeting-api spawns the workload with the `meeting-bot` runtime profile and no BOT_CONFIG.
 *
 *   signin — Xvfb is up (entrypoint.sh). The VNC password is stored with `x11vnc -storepasswd` into a
 *            private file; x11vnc serves the display on 127.0.0.1:5900 only (`-localhost`), behind that
 *            password, and websockify (`--web /usr/share/novnc 6080 localhost:5900`) is the one
 *            listener the bots network reaches. The browser opens at the platform's sign-in page
 *            (@vexa/remote-browser `provisionLogin`, launched without the CDP debug port); on
 *            confirmed sign-in the profile's SESSION_PROFILE files (`readSessionProfile`) are PUT to
 *            `save_url` as a session-profile.v1 WritebackBody with `Authorization: Bearer <token>`,
 *            and the workload exits 0. Not confirmed within `timeout_ms` ⇒ exit 1, nothing sent.
 *   verify — the profile is restored from `restore_url` (session-restore.ts, Bearer token), the
 *            browser launches on it, `validateLoggedIn` decides, and `{logged_in, detail}` is POSTed
 *            to `result_url`; exit 0. A restore or launch that fails exits 1 with no result posted —
 *            a check that could not run is not evidence the account is signed out.
 *
 * The profile is a credential, and so are the token and the VNC password: none of them is ever
 * logged. Log lines are `[browser-session] …`.
 */
import { spawn as nodeSpawn, type ChildProcess, type SpawnOptions } from 'node:child_process';
import { connect } from 'node:net';
import { mkdtempSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  provisionLogin as rbProvisionLogin,
  validateLoggedIn as rbValidateLoggedIn,
  launchPersistentBrowser as rbLaunchPersistentBrowser,
  readSessionProfile,
  cleanStaleLocks,
  makeEphemeralProfileDir,
  removeProfileDir,
  getAuthenticatedBrowserArgs,
  getBrowserSessionArgs,
  CDP_DEBUG_ARGS,
  type AuthPlatform,
  type LoginStatus,
  type ProvisionLoginOptions,
  type Page,
  type BrowserContext,
} from '@vexa/remote-browser';
import { restoreSessionFromUrl } from './session-restore.js';

export type BrowserSessionPlatform = 'google' | 'microsoft' | 'zoom';

export interface BrowserSessionConfig {
  mode: 'signin' | 'verify';
  session_id: string;
  platform: BrowserSessionPlatform;
  token: string;
  timeout_ms: number;
  /** signin */
  vnc_password?: string;
  save_url?: string;
  /** verify */
  restore_url?: string;
  result_url?: string;
}

/** The session-profile platform behind each bot-account platform. */
export const AUTH_PLATFORM_OF: Record<BrowserSessionPlatform, AuthPlatform> = {
  google: 'google',
  microsoft: 'teams',
  zoom: 'zoom',
};

/** Thrown when VEXA_BROWSER_SESSION is missing, not JSON, or off its shape. Names the field, never its value. */
export class BrowserSessionConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'BrowserSessionConfigError';
  }
}

const HEX32 = /^[a-f0-9]{32}$/;
const CONTROL = /[\u0000-\u001f\u007f]/;
const MAX_TIMEOUT_MS = 2 * 60 * 60 * 1000;

function isHttpUrl(v: unknown): v is string {
  if (typeof v !== 'string' || !v) return false;
  try { const u = new URL(v); return u.protocol === 'http:' || u.protocol === 'https:'; } catch { return false; }
}

/** Parse + check VEXA_BROWSER_SESSION. Fields the mode needs are required; values are never echoed. */
export function parseBrowserSession(raw: string | undefined): BrowserSessionConfig {
  const bad = (m: string): never => { throw new BrowserSessionConfigError(`VEXA_BROWSER_SESSION: ${m}`); };
  if (!raw || !raw.trim()) bad('missing or empty');
  let d: any;
  try { d = JSON.parse(raw as string); } catch { bad('not valid JSON'); }
  if (!d || typeof d !== 'object' || Array.isArray(d)) bad('not an object');
  if (d.mode !== 'signin' && d.mode !== 'verify') bad('mode must be "signin" or "verify"');
  if (typeof d.session_id !== 'string' || !HEX32.test(d.session_id)) bad('session_id must be 32 lowercase hex');
  if (!Object.prototype.hasOwnProperty.call(AUTH_PLATFORM_OF, d.platform)) bad('platform must be google, microsoft or zoom');
  if (typeof d.token !== 'string' || !d.token || CONTROL.test(d.token)) bad('token is required');
  if (!Number.isInteger(d.timeout_ms) || d.timeout_ms <= 0 || d.timeout_ms > MAX_TIMEOUT_MS) {
    bad(`timeout_ms must be a positive integer of at most ${MAX_TIMEOUT_MS}`);
  }
  const cfg: BrowserSessionConfig = {
    mode: d.mode, session_id: d.session_id, platform: d.platform, token: d.token, timeout_ms: d.timeout_ms,
  };
  if (d.mode === 'signin') {
    if (typeof d.vnc_password !== 'string' || d.vnc_password.length < 6 || d.vnc_password.length > 128 || CONTROL.test(d.vnc_password)) {
      bad('vnc_password is required in signin mode (6..128 printable characters)');
    }
    if (!isHttpUrl(d.save_url)) bad('save_url is required in signin mode (an http(s) URL)');
    cfg.vnc_password = d.vnc_password;
    cfg.save_url = d.save_url;
  } else {
    if (!isHttpUrl(d.restore_url)) bad('restore_url is required in verify mode (an http(s) URL)');
    if (!isHttpUrl(d.result_url)) bad('result_url is required in verify mode (an http(s) URL)');
    cfg.restore_url = d.restore_url;
    cfg.result_url = d.result_url;
  }
  return cfg;
}

/** What the mode touches outside itself — real by default, replaced in tests. */
export interface BrowserSessionDeps {
  spawn: (cmd: string, args: string[], opts: SpawnOptions) => ChildProcess;
  fetch: typeof fetch;
  provisionLogin: (opts: ProvisionLoginOptions) => Promise<LoginStatus>;
  launchPersistentBrowser: (opts: { dataDir: string; args: string[] }) => Promise<{ context: BrowserContext; page: Page }>;
  validateLoggedIn: (page: Page, platform: AuthPlatform) => Promise<LoginStatus>;
  /** Resolves once something listens on 127.0.0.1:port, rejects after timeoutMs. */
  waitForPort: (port: number, timeoutMs: number) => Promise<void>;
  log: (line: string) => void;
  /** A fresh, private profile dir (removed by the mode when it ends). */
  makeProfileDir: () => string;
  novncDir: string;
}

export function waitForLocalPort(port: number, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = (): void => {
      const sock = connect({ host: '127.0.0.1', port });
      sock.once('connect', () => { sock.destroy(); resolve(); });
      sock.once('error', () => {
        sock.destroy();
        if (Date.now() >= deadline) reject(new Error(`nothing listens on 127.0.0.1:${port} after ${timeoutMs} ms`));
        else setTimeout(attempt, 200);
      });
    };
    attempt();
  });
}

function defaultDeps(): BrowserSessionDeps {
  return {
    spawn: nodeSpawn,
    fetch: (...a) => fetch(...a),
    provisionLogin: rbProvisionLogin,
    launchPersistentBrowser: rbLaunchPersistentBrowser,
    validateLoggedIn: rbValidateLoggedIn,
    waitForPort: waitForLocalPort,
    log: (line) => console.log(line),
    makeProfileDir: makeEphemeralProfileDir,
    novncDir: process.env.VEXA_NOVNC_DIR || '/usr/share/novnc',
  };
}

/** The sign-in browser's flags: the interactive set, without the CDP debug port — websockify is the
 *  workload's only listener. */
export function signinBrowserArgs(): string[] {
  return getBrowserSessionArgs().filter((a) => !CDP_DEBUG_ARGS.includes(a));
}

/** Run a command to completion; resolves with its exit code. Its output is discarded (argv may hold a secret). */
function runToExit(deps: BrowserSessionDeps, cmd: string, args: string[]): Promise<number> {
  return new Promise((resolve) => {
    let child: ChildProcess;
    try { child = deps.spawn(cmd, args, { stdio: 'ignore' }); } catch { resolve(-1); return; }
    child.once('error', () => resolve(-1));
    child.once('exit', (code) => resolve(code ?? -1));
  });
}

function startDaemon(deps: BrowserSessionDeps, cmd: string, args: string[], children: ChildProcess[]): void {
  const child = deps.spawn(cmd, args, { stdio: 'ignore' });
  child.once('error', (e: NodeJS.ErrnoException) => deps.log(`[browser-session] ${cmd} failed to start (${e?.code ?? 'error'})`));
  children.push(child);
}

const VNC_PORT = 5900;
const WEB_PORT = 6080;

async function runSignin(cfg: BrowserSessionConfig, deps: BrowserSessionDeps): Promise<number> {
  const display = process.env.DISPLAY || ':99';
  const children: ChildProcess[] = [];
  // The password file lives in a private dir (0700, the file 0600 as x11vnc writes it). x11vnc's
  // `-rfbauth` reads it at each authentication, so it stays until the session ends, then goes.
  const secretDir = mkdtempSync(join(tmpdir(), 'vnc-auth-'));
  const passwdFile = join(secretDir, 'passwd');
  const profileDir = deps.makeProfileDir();
  try {
    const stored = await runToExit(deps, 'x11vnc', ['-storepasswd', cfg.vnc_password as string, passwdFile]);
    if (stored !== 0 || !existsSync(passwdFile)) {
      deps.log(`[browser-session] could not store the VNC password (x11vnc exited ${stored})`);
      return 1;
    }
    startDaemon(deps, 'x11vnc', ['-display', display, '-localhost', '-rfbport', String(VNC_PORT), '-forever', '-shared', '-rfbauth', passwdFile], children);
    startDaemon(deps, 'websockify', ['--web', deps.novncDir, String(WEB_PORT), `localhost:${VNC_PORT}`], children);
    try {
      await deps.waitForPort(VNC_PORT, 10_000);
      await deps.waitForPort(WEB_PORT, 10_000);
    } catch (e) {
      deps.log(`[browser-session] the live view did not come up: ${(e as Error).message}`);
      return 1;
    }
    deps.log(`[browser-session] live view ready on :${WEB_PORT}; waiting up to ${Math.round(cfg.timeout_ms / 1000)} s for sign-in`);

    const status = await deps.provisionLogin({
      platform: AUTH_PLATFORM_OF[cfg.platform],
      profileDir,
      timeoutMs: cfg.timeout_ms,
      args: signinBrowserArgs(),
    });
    if (!status.loggedIn) {
      deps.log('[browser-session] sign-in not confirmed before the timeout; nothing saved');
      return 1;
    }
    const files = readSessionProfile(profileDir);
    if (!files.length) {
      deps.log('[browser-session] sign-in confirmed but the profile holds no session files; nothing saved');
      return 1;
    }
    const res = await deps.fetch(cfg.save_url as string, {
      method: 'PUT',
      headers: { Authorization: `Bearer ${cfg.token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ files: files.map((f) => ({ path: f.path, data: f.data.toString('base64') })) }),
      signal: AbortSignal.timeout(30_000),
    });
    if (!res.ok) {
      const detail = await res.text().catch(() => '');
      deps.log(`[browser-session] meeting-api refused the profile: HTTP ${res.status} ${detail.slice(0, 200)}`.trim());
      return 1;
    }
    deps.log(`[browser-session] sign-in confirmed; ${files.length} session file(s) saved`);
    return 0;
  } catch (e) {
    deps.log(`[browser-session] sign-in failed: ${(e as Error)?.name ?? 'Error'}: ${(e as Error)?.message ?? ''}`);
    return 1;
  } finally {
    for (const c of children) { try { c.kill('SIGTERM'); } catch { /* gone */ } }
    rmSync(secretDir, { recursive: true, force: true });
    removeProfileDir(profileDir);
  }
}

async function runVerify(cfg: BrowserSessionConfig, deps: BrowserSessionDeps): Promise<number> {
  const profileDir = deps.makeProfileDir();
  let context: BrowserContext | undefined;
  try {
    let status: LoginStatus;
    try {
      const n = await restoreSessionFromUrl(cfg.restore_url as string, cfg.token, profileDir, { fetchImpl: deps.fetch });
      cleanStaleLocks(profileDir);
      deps.log(`[browser-session] restored ${n} session file(s); checking the sign-in`);
      const launched = await deps.launchPersistentBrowser({ dataDir: profileDir, args: getAuthenticatedBrowserArgs() });
      context = launched.context;
      status = await deps.validateLoggedIn(launched.page, AUTH_PLATFORM_OF[cfg.platform]);
    } catch (e) {
      deps.log(`[browser-session] the check could not run: ${(e as Error)?.name ?? 'Error'}: ${(e as Error)?.message ?? ''}`);
      return 1;
    }
    const res = await deps.fetch(cfg.result_url as string, {
      method: 'POST',
      headers: { Authorization: `Bearer ${cfg.token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ logged_in: !!status.loggedIn, detail: String(status.detail ?? '') }),
      signal: AbortSignal.timeout(30_000),
    });
    if (!res.ok) {
      deps.log(`[browser-session] meeting-api refused the check result: HTTP ${res.status}`);
      return 1;
    }
    deps.log(`[browser-session] check done: logged_in=${!!status.loggedIn}`);
    return 0;
  } catch (e) {
    deps.log(`[browser-session] verify failed: ${(e as Error)?.name ?? 'Error'}: ${(e as Error)?.message ?? ''}`);
    return 1;
  } finally {
    if (context) await context.close().catch(() => { /* best-effort */ });
    removeProfileDir(profileDir);
  }
}

/** The browser-session entry: parse VEXA_BROWSER_SESSION and run its mode. Resolves with the exit code. */
export async function runBrowserSession(env: NodeJS.ProcessEnv = process.env, overrides: Partial<BrowserSessionDeps> = {}): Promise<number> {
  const deps: BrowserSessionDeps = { ...defaultDeps(), ...overrides };
  let cfg: BrowserSessionConfig;
  try {
    cfg = parseBrowserSession(env.VEXA_BROWSER_SESSION);
  } catch (e) {
    deps.log(`[browser-session] FATAL ${(e as Error).message}`);
    return 1;
  }
  deps.log(`[browser-session] mode=${cfg.mode} platform=${cfg.platform} session=${cfg.session_id}`);
  return cfg.mode === 'signin' ? runSignin(cfg, deps) : runVerify(cfg, deps);
}
