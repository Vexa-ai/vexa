/**
 * Browser-session mode — sign a bot account in over a private live view, or check one. OFFLINE: spawn,
 * fetch, provisionLogin and the browser launch are fakes; the profile dirs are real temp dirs.
 *
 *   • VEXA_BROWSER_SESSION is parsed strictly: every field the mode needs is required, and the
 *     refusal names the field, never a value;
 *   • platform mapping: google→google, microsoft→teams, zoom→zoom;
 *   • signin: the VNC password is written with `x11vnc -storepasswd <pw> <file>`; x11vnc runs with
 *     -localhost behind -rfbauth <file> (never -nopw); websockify serves noVNC on 6080 → localhost:5900;
 *     the browser launches without the CDP debug port; on confirmed sign-in the profile is PUT to
 *     save_url with the Bearer token as a session-profile.v1 WritebackBody, profile paths only, exit 0;
 *     a timeout exits 1 with no PUT; a refused PUT exits 1; the password file and profile are removed;
 *   • verify: the restore is a GET with the Bearer token, the result is POSTed as {logged_in, detail},
 *     exit 0; a failed restore exits 1 and posts nothing;
 *   • no log line carries the token, the VNC password or a profile byte;
 *   • main() runs this mode when VEXA_BROWSER_SESSION is set, without VEXA_BOT_CONFIG.
 * Run: npx tsx src/browser-session.test.ts
 */
import { EventEmitter } from 'node:events';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { ChildProcess } from 'node:child_process';
import { Ajv2020 } from 'ajv/dist/2020.js';
import { isSessionProfilePath, type ProvisionLoginOptions } from '@vexa/remote-browser';
import {
  AUTH_PLATFORM_OF, BrowserSessionConfigError, parseBrowserSession, runBrowserSession, type BrowserSessionDeps,
} from './browser-session.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const CONTRACT = join(HERE, '..', '..', '..', 'contracts', 'session-profile.v1');
const schema = JSON.parse(readFileSync(join(CONTRACT, 'session-profile.schema.json'), 'utf8'));
const ajv = new Ajv2020({ strict: false, allErrors: true });
ajv.addSchema(schema);
const isWritebackBody = ajv.compile({ $ref: `${schema.$id}#/$defs/WritebackBody` });

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

const SID = 'a'.repeat(32);
const TOKEN = 'session-token-SECRET-123';
const PW = 'vnc-pw-SECRET-456';
const COOKIE = 'cookie-bytes-SECRET-789';

const signinCfg = (over: Record<string, unknown> = {}) => ({
  mode: 'signin', session_id: SID, platform: 'microsoft', token: TOKEN, vnc_password: PW,
  save_url: 'http://meeting-api:8080/internal/bot-accounts/sessions/' + SID + '/profile', timeout_ms: 60_000, ...over,
});
const verifyCfg = (over: Record<string, unknown> = {}) => ({
  mode: 'verify', session_id: SID, platform: 'google', token: TOKEN,
  restore_url: 'http://meeting-api:8080/internal/bot-accounts/restore/' + SID,
  result_url: 'http://meeting-api:8080/internal/bot-accounts/checks/' + SID, timeout_ms: 60_000, ...over,
});

interface Harness {
  deps: Partial<BrowserSessionDeps>;
  spawns: Array<{ cmd: string; args: string[]; child: FakeChild }>;
  requests: Array<{ url: string; method: string; auth: string; body: string }>;
  logs: string[];
  provisioned: ProvisionLoginOptions[];
  profileDirs: string[];
  passwdFileExistedDuringLogin: boolean[];
}

class FakeChild extends EventEmitter { killed = false; kill(): boolean { this.killed = true; this.emit('exit', 0); return true; } }

function harness(opts: { loggedIn?: boolean; saveStatus?: number; restoreStatus?: number; validate?: boolean } = {}): Harness {
  const h: Harness = { deps: {}, spawns: [], requests: [], logs: [], provisioned: [], profileDirs: [], passwdFileExistedDuringLogin: [] };
  h.deps = {
    spawn: ((cmd: string, args: string[]) => {
      const child = new FakeChild();
      h.spawns.push({ cmd, args, child });
      if (args[0] === '-storepasswd') {
        writeFileSync(args[2], 'obfuscated', { mode: 0o600 });
        setImmediate(() => child.emit('exit', 0));
      }
      return child as unknown as ChildProcess;
    }) as BrowserSessionDeps['spawn'],
    fetch: (async (url: string, init: RequestInit = {}) => {
      const method = init.method ?? 'GET';
      const auth = String((init.headers as Record<string, string>)?.Authorization ?? '');
      h.requests.push({ url, method, auth, body: String(init.body ?? '') });
      if (method === 'GET') {
        const golden = readFileSync(join(CONTRACT, 'golden', 'WritebackBody.session.json'), 'utf8');
        return new Response(opts.restoreStatus && opts.restoreStatus !== 200 ? '{"detail":"no"}' : golden, { status: opts.restoreStatus ?? 200 });
      }
      if (method === 'PUT') return new Response('{"account_id":"x","files":3}', { status: opts.saveStatus ?? 200 });
      return new Response('{}', { status: 200 });
    }) as typeof fetch,
    provisionLogin: async (o: ProvisionLoginOptions) => {
      h.provisioned.push(o);
      const pwFile = h.spawns.find((s) => s.args[0] === '-storepasswd')?.args[2];
      h.passwdFileExistedDuringLogin.push(!!pwFile && existsSync(pwFile));
      if (opts.loggedIn === false) return { loggedIn: false, detail: 'timed out waiting for sign-in' };
      mkdirSync(join(o.profileDir, 'Default', 'Extensions', 'x'), { recursive: true });
      writeFileSync(join(o.profileDir, 'Default', 'Cookies'), COOKIE);
      writeFileSync(join(o.profileDir, 'Local State'), '{}');
      writeFileSync(join(o.profileDir, 'Default', 'Extensions', 'x', 'manifest.json'), '{}');
      return { loggedIn: true, detail: 'ok' };
    },
    launchPersistentBrowser: (async () => ({ context: { close: async () => {} }, page: {} })) as unknown as BrowserSessionDeps['launchPersistentBrowser'],
    validateLoggedIn: async () => ({ loggedIn: opts.validate ?? true, detail: 'platform=google signedOut=false authCookie=true' }),
    waitForPort: async () => {},
    log: (line: string) => { h.logs.push(line); },
    makeProfileDir: () => { const d = mkdtempSync(join(tmpdir(), 'bs-profile-')); h.profileDirs.push(d); return d; },
    novncDir: '/usr/share/novnc',
  };
  return h;
}

const noSecretsLogged = (h: Harness): boolean => h.logs.every((l) => !l.includes(TOKEN) && !l.includes(PW) && !l.includes(COOKIE));

async function main(): Promise<void> {
  console.log('browser-session mode');

  // ── config ──
  const refuses = (why: string, raw: string | undefined, field: string): void => {
    let err: unknown;
    try { parseBrowserSession(raw); } catch (e) { err = e; }
    check(`refuses ${why}`, err instanceof BrowserSessionConfigError && String((err as Error).message).includes(field), String(err));
    check(`the refusal of ${why} echoes no secret`, !String((err as Error)?.message).includes(TOKEN) && !String((err as Error)?.message).includes(PW));
  };
  refuses('a missing env', undefined, 'missing');
  refuses('non-JSON', '{', 'JSON');
  for (const field of ['mode', 'session_id', 'platform', 'token', 'timeout_ms', 'vnc_password', 'save_url']) {
    const c: Record<string, unknown> = signinCfg();
    delete c[field];
    refuses(`a signin without ${field}`, JSON.stringify(c), field);
  }
  for (const field of ['restore_url', 'result_url']) {
    const c: Record<string, unknown> = verifyCfg();
    delete c[field];
    refuses(`a verify without ${field}`, JSON.stringify(c), field);
  }
  refuses('an unknown mode', JSON.stringify(signinCfg({ mode: 'meeting' })), 'mode');
  refuses('a session_id that is not 32 hex', JSON.stringify(signinCfg({ session_id: '../x' })), 'session_id');
  refuses('an unknown platform', JSON.stringify(signinCfg({ platform: 'teams' })), 'platform');
  refuses('a short VNC password', JSON.stringify(signinCfg({ vnc_password: 'abc' })), 'vnc_password');
  refuses('a save_url that is not http(s)', JSON.stringify(signinCfg({ save_url: 'file:///etc/passwd' })), 'save_url');
  refuses('a zero timeout', JSON.stringify(signinCfg({ timeout_ms: 0 })), 'timeout_ms');
  const ok = parseBrowserSession(JSON.stringify(signinCfg()));
  check('a complete signin config parses', ok.mode === 'signin' && ok.vnc_password === PW && ok.save_url?.startsWith('http://') === true);
  check('platform mapping google→google, microsoft→teams, zoom→zoom',
    AUTH_PLATFORM_OF.google === 'google' && AUTH_PLATFORM_OF.microsoft === 'teams' && AUTH_PLATFORM_OF.zoom === 'zoom');

  // ── signin: confirmed ──
  {
    const h = harness();
    const code = await runBrowserSession({ VEXA_BROWSER_SESSION: JSON.stringify(signinCfg()), DISPLAY: ':99' }, h.deps);
    check('signin confirmed ⇒ exit 0', code === 0, `${code} :: ${h.logs.join(' | ')}`);
    const store = h.spawns.find((s) => s.cmd === 'x11vnc' && s.args[0] === '-storepasswd');
    check('the VNC password is written with `x11vnc -storepasswd <pw> <file>`',
      !!store && store.args.length === 3 && store.args[1] === PW && !!store.args[2]);
    const pwFile = store?.args[2] ?? '';
    const vnc = h.spawns.find((s) => s.cmd === 'x11vnc' && s.args[0] !== '-storepasswd');
    const a = vnc?.args ?? [];
    check('x11vnc listens on localhost only', a.includes('-localhost'), a.join(' '));
    check('x11vnc serves the display on 5900 behind -rfbauth <the password file>',
      a[a.indexOf('-rfbport') + 1] === '5900' && a[a.indexOf('-rfbauth') + 1] === pwFile && a[a.indexOf('-display') + 1] === ':99', a.join(' '));
    check('x11vnc never runs without a password', !a.includes('-nopw'));
    check('x11vnc argv never carries the password itself', !a.includes(PW));
    const ws = h.spawns.find((s) => s.cmd === 'websockify');
    check('websockify serves noVNC on 6080 → localhost:5900',
      JSON.stringify(ws?.args) === JSON.stringify(['--web', '/usr/share/novnc', '6080', 'localhost:5900']), JSON.stringify(ws?.args));
    check('the password file exists while the person signs in', h.passwdFileExistedDuringLogin[0] === true);
    check('the password file is removed when the session ends', !!pwFile && !existsSync(pwFile) && !existsSync(dirname(pwFile)));
    const p = h.provisioned[0];
    check('provisionLogin runs for the mapped platform (microsoft → teams) with the session timeout',
      p?.platform === 'teams' && p?.timeoutMs === 60_000, JSON.stringify({ platform: p?.platform, timeoutMs: p?.timeoutMs }));
    check('the sign-in browser opens no CDP debug port',
      Array.isArray(p?.args) && !(p?.args ?? []).some((x) => x.startsWith('--remote-debugging')), JSON.stringify(p?.args));
    const put = h.requests.find((r) => r.method === 'PUT');
    check('the profile is PUT to save_url', put?.url === signinCfg().save_url && h.requests.length === 1, JSON.stringify(h.requests.map((r) => `${r.method} ${r.url}`)));
    check('with the Bearer token', put?.auth === `Bearer ${TOKEN}`);
    const body = JSON.parse(put?.body ?? '{}');
    check('the body is a session-profile.v1 WritebackBody', isWritebackBody(body) === true, ajv.errorsText(isWritebackBody.errors));
    const paths = (body.files ?? []).map((f: { path: string }) => f.path);
    check('the body names profile paths only (the extension is left out)',
      paths.length === 2 && paths.every(isSessionProfilePath) && !paths.some((x: string) => x.includes('Extensions')), JSON.stringify(paths));
    check('the children are stopped at the end', h.spawns.filter((s) => s.args[0] !== '-storepasswd').every((s) => s.child.killed));
    check('the profile dir is removed', h.profileDirs.every((d) => !existsSync(d)));
    check('no log line carries the token, the VNC password or a profile byte', noSecretsLogged(h), h.logs.join(' | '));
    check('log lines are [browser-session] …', h.logs.every((l) => l.startsWith('[browser-session] ')), h.logs.join(' | '));
  }

  // ── signin: timeout ──
  {
    const h = harness({ loggedIn: false });
    const code = await runBrowserSession({ VEXA_BROWSER_SESSION: JSON.stringify(signinCfg()) }, h.deps);
    check('signin not confirmed in time ⇒ exit 1', code === 1, String(code));
    check('…and nothing is PUT', h.requests.length === 0, String(h.requests.length));
    check('…and the password file is removed', !existsSync(h.spawns[0]?.args[2] ?? '/nonexistent-but-checked'));
  }

  // ── signin: meeting-api refuses the profile ──
  {
    const h = harness({ saveStatus: 409 });
    const code = await runBrowserSession({ VEXA_BROWSER_SESSION: JSON.stringify(signinCfg()) }, h.deps);
    check('a refused PUT ⇒ exit 1', code === 1, String(code));
    check('a refused PUT logs no secret', noSecretsLogged(h));
  }

  // ── verify ──
  {
    const h = harness({ validate: false });
    const code = await runBrowserSession({ VEXA_BROWSER_SESSION: JSON.stringify(verifyCfg()) }, h.deps);
    check('verify ⇒ exit 0', code === 0, `${code} :: ${h.logs.join(' | ')}`);
    const get = h.requests[0];
    check('the restore is a GET to restore_url with the Bearer token',
      get?.method === 'GET' && get?.url === verifyCfg().restore_url && get?.auth === `Bearer ${TOKEN}`);
    const post = h.requests[1];
    check('the result is POSTed to result_url with the Bearer token',
      post?.method === 'POST' && post?.url === verifyCfg().result_url && post?.auth === `Bearer ${TOKEN}`);
    const r = JSON.parse(post?.body ?? '{}');
    check('the result is {logged_in, detail}',
      Object.keys(r).sort().join() === 'detail,logged_in' && r.logged_in === false && typeof r.detail === 'string', post?.body);
    check('verify removes its profile dir', h.profileDirs.every((d) => !existsSync(d)));
    check('verify logs no secret', noSecretsLogged(h));
  }
  {
    const h = harness({ restoreStatus: 404 });
    const code = await runBrowserSession({ VEXA_BROWSER_SESSION: JSON.stringify(verifyCfg()) }, h.deps);
    check('a failed restore ⇒ exit 1, and no result is posted', code === 1 && h.requests.every((q) => q.method === 'GET'), String(code));
  }

  // ── main() dispatches the mode before any invocation.v1 is read ──
  {
    const { main: botMain } = await import('./index.js');
    const lines: string[] = [];
    const orig = console.log;
    console.log = (...a: unknown[]) => { lines.push(a.join(' ')); };
    let code: number;
    try { code = await botMain({ VEXA_BROWSER_SESSION: JSON.stringify({ mode: 'signin' }) }); } finally { console.log = orig; }
    check('main() runs browser-session mode when VEXA_BROWSER_SESSION is set (no VEXA_BOT_CONFIG needed)',
      code === 1 && lines.some((l) => l.startsWith('[browser-session] FATAL')), lines.join(' | '));
  }

  if (failed) {
    console.error(`\n❌ browser-session.test — ${failed} check(s) failed`);
    process.exit(1);
  }
  console.log('\n✅ browser-session.test — a private live view, the profile to meeting-api only on confirmed sign-in, nothing secret logged');
}

main().catch((e) => { console.error(e); process.exit(1); });
