/**
 * sandbox — the browser runs sandboxed wherever Chromium can sandbox itself, and is never handed the
 * bot's own environment. Pure logic plus one real launch wiring check with the launcher stubbed; no
 * Chromium. Same shape as auth.smoke.test.ts (tsx + exit code).
 *
 *  1. No launch-flag set turns the sandbox off.
 *  2. launchWithSandbox: sandbox on by default; off as root (said why); off after Chromium refuses to
 *     start for want of a sandbox (said why), and only then; any other launch failure is not retried;
 *     a caller's own --no-sandbox is honoured as given.
 *  3. browserEnv keeps the display, audio, locale, paths and proxies, and drops everything else —
 *     the bot's constructor and any credential.
 *  4. launchPersistentBrowser hands Chromium that environment and no sandbox-off flag.
 */
import Module from 'module';
import { getAuthenticatedBrowserArgs, getBrowserSessionArgs } from './args';
import { browserEnv, launchWithSandbox, NO_SANDBOX_ARGS, isSandboxStartFailure } from './sandbox';

const fails: string[] = [];
const check = (cond: boolean, msg: string) => { if (!cond) fails.push(msg); };
const OFF = ['--no-sandbox', '--disable-setuid-sandbox'];

// ── 1. flags ─────────────────────────────────────────────────────────────────
for (const [name, args] of [['authenticated', getAuthenticatedBrowserArgs()], ['session', getBrowserSessionArgs()]] as const) {
  for (const off of OFF) check(!args.includes(off), `${name} args turn the sandbox off (${off})`);
}

async function launches() {
  const quiet: string[] = [];
  const log = (line: string) => { quiet.push(line); };

  // on by default for a non-root browser
  let calls: boolean[] = [];
  let r = await launchWithSandbox(['--a'], async (s) => { calls.push(s); return 'ctx'; }, { uid: 1500000001, log });
  check(r.sandboxed === true && calls.join() === 'true', 'non-root: one launch, sandbox on');

  // as root: off, and said
  calls = []; quiet.length = 0;
  r = await launchWithSandbox(['--a'], async (s) => { calls.push(s); return 'ctx'; }, { uid: 0, log });
  check(r.sandboxed === false && calls.join() === 'false', 'root: launched with the sandbox off');
  check(quiet.some((l) => /without its sandbox: the browser runs as root/.test(l)), 'root: the launch says why');

  // the host refuses the sandbox: off on the second launch, and said
  calls = []; quiet.length = 0;
  r = await launchWithSandbox(['--a'], async (s) => {
    calls.push(s);
    if (s) throw new Error('browserType.launchPersistentContext: Target closed\n[FATAL:zygote_host_impl_linux.cc] No usable sandbox! ...');
    return 'ctx';
  }, { uid: 1500000001, log });
  check(r.sandboxed === false && calls.join() === 'true,false', 'no usable sandbox: tried with it, then without');
  check(quiet.some((l) => /without its sandbox: this host refuses/.test(l)), 'no usable sandbox: the launch says why');

  // any other failure is not turned into an unsandboxed retry
  calls = [];
  let threw = false;
  try {
    await launchWithSandbox([], async (s) => { calls.push(s); throw new Error('Executable doesn\'t exist'); }, { uid: 1500000001, log });
  } catch { threw = true; }
  check(threw && calls.join() === 'true', 'another launch failure is raised, never retried without the sandbox');

  // a harness's own --no-sandbox is honoured as given
  calls = [];
  r = await launchWithSandbox(['--no-sandbox', '--mute-audio'], async (s) => { calls.push(s); return 'ctx'; }, { uid: 1500000001, log });
  check(r.sandboxed === false && calls.join() === 'false', 'caller --no-sandbox: one launch, sandbox off as asked');

  check(isSandboxStartFailure(new Error('x No usable sandbox! y')) && !isSandboxStartFailure(new Error('Target closed')),
    'only Chromium\'s own no-sandbox refusal counts as a sandbox failure');
  check(NO_SANDBOX_ARGS.includes('--no-sandbox'), 'NO_SANDBOX_ARGS turns it off');
}

// ── 3. environment ───────────────────────────────────────────────────────────
const env = browserEnv({
  PATH: '/usr/bin', HOME: '/h', DISPLAY: ':41', XAUTHORITY: '/h/x11/Xauthority', PULSE_SERVER: 'unix:/h/pulse/native',
  XDG_RUNTIME_DIR: '/h/pulse', LANG: 'C.UTF-8', HTTPS_PROXY: 'http://proxy:3128',
  VEXA_BOT_CONFIG: '{"token":"t","s3SecretKey":"k"}', REDIS_URL: 'redis://:pw@h/0', AWS_SECRET_ACCESS_KEY: 'k',
  ADMIN_API_TOKEN: 't', NODE_OPTIONS: '--require /tmp/x.js', LD_PRELOAD: '/tmp/x.so',
} as NodeJS.ProcessEnv);
for (const key of ['PATH', 'HOME', 'DISPLAY', 'XAUTHORITY', 'PULSE_SERVER', 'XDG_RUNTIME_DIR', 'LANG', 'HTTPS_PROXY']) {
  check(key in env, `browserEnv keeps ${key}`);
}
for (const key of ['VEXA_BOT_CONFIG', 'REDIS_URL', 'AWS_SECRET_ACCESS_KEY', 'ADMIN_API_TOKEN', 'NODE_OPTIONS', 'LD_PRELOAD']) {
  check(!(key in env), `browserEnv drops ${key}`);
}

// ── 4. the real launch, with Playwright's launcher stubbed ────────────────────
async function realLaunch() {
  const seen: any[] = [];
  const fakeContext = { addInitScript: async () => {}, pages: () => [{}], newPage: async () => ({}) };
  const fake = { chromium: { use() {}, launchPersistentContext: async (_dir: string, o: any) => { seen.push(o); return fakeContext; } } };
  const load = (Module as any)._load;
  (Module as any)._load = function (request: string, ...rest: any[]) {
    if (request === 'playwright-extra') return fake;
    if (request === 'puppeteer-extra-plugin-stealth') return () => ({});
    return load.call(this, request, ...rest);
  };
  process.env.VEXA_BOT_CONFIG = '{"token":"secret"}';
  process.env.DISPLAY = ':41';
  try {
    const { launchPersistentBrowser } = require('./browser');
    const out = await launchPersistentBrowser({ dataDir: '/tmp/none', args: getAuthenticatedBrowserArgs() });
    const o = seen[0];
    check(!!o && !!o.env, 'launchPersistentBrowser hands Chromium an environment of its own');
    check(!!o && !('VEXA_BOT_CONFIG' in (o.env || {})) && (o.env || {}).DISPLAY === ':41',
      'that environment has the display and not the bot\'s constructor');
    const root = process.getuid?.() === 0;
    // Playwright turns Chromium's sandbox off unless chromiumSandbox is true
    check(!!o && o.chromiumSandbox === !root, root ? 'as root the launch turns the sandbox off' : 'the launch asks Playwright for the sandbox');
    check(!!o && OFF.every((f) => o.args.includes(f) === root), root ? 'as root the flags say it is off' : 'no flag turns the sandbox off');
    check(out.sandboxed === !root, 'the launch reports whether it is sandboxed');
  } finally {
    (Module as any)._load = load;
    delete process.env.VEXA_BOT_CONFIG;
  }
}

async function main() {
  await launches();
  await realLaunch();
  if (fails.length) {
    console.log('❌ FAIL —\n  ' + fails.join('\n  '));
    process.exit(1);
  }
  console.log('✅ PASS — no flag set turns the sandbox off; it is on unless the browser is root or the host has no usable sandbox (said why); the browser gets display/audio/locale/paths/proxies and nothing of the bot\'s own environment.');
  process.exit(0);
}

main().catch((e) => { console.error('❌ FAIL —', e?.message || e); process.exit(1); });
