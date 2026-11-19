/**
 * Session restore over HTTP — a saved bot account's profile comes from meeting-api, profile paths only.
 * OFFLINE: a loopback http server stands in for meeting-api.
 *
 *   • an authenticated invocation with sessionRestoreUrl is a valid invocation.v1;
 *   • the restore is a GET to exactly that URL with the bot's MeetingToken, and a good answer (a
 *     session-profile.v1 WritebackBody golden) writes exactly its profile paths into the dir;
 *   • an answer naming one path outside the profile, or a traversal path, is refused whole —
 *     nothing is written — as a typed SessionSyncError('session-restore');
 *   • an HTTP error, a non-JSON answer and an off-contract answer are the same typed failure, the one
 *     the composition root treats like a failed S3 restore (and none of them leaks the profile bytes);
 *   • launchBrowser's restore + write-back conditions name sessionRestoreUrl (source check: the launch
 *     itself is browser-resident, L4).
 * Run: npx tsx src/session-restore.test.ts
 */
import { createServer, type IncomingMessage } from 'node:http';
import { mkdtempSync, readFileSync, readdirSync, rmSync, existsSync } from 'node:fs';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { SessionSyncError, readSessionProfile } from '@vexa/remote-browser';
import { restoreSession } from './session-restore.js';
import { parseInvocation, type Invocation } from './config.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const GOLDEN = join(HERE, '..', '..', '..', 'contracts', 'session-profile.v1', 'golden');

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

const b64 = (s: string): string => Buffer.from(s).toString('base64');

async function main(): Promise<void> {
  console.log('session restore');

  const base: Invocation = {
    platform: 'teams',
    meetingUrl: 'https://teams.microsoft.com/l/meetup-join/abc',
    botName: 'VexaBot',
    redisUrl: 'redis://redis:6379/0',
    token: 'meeting-token-for-session',
    connectionId: 'conn-1234',
    authenticated: true,
    sessionRestoreUrl: 'http://meeting-api:8080/internal/browser-session/conn-1234',
    sessionWritebackUrl: 'http://meeting-api:8080/internal/browser-session/conn-1234',
  };
  let parseError = '';
  let parsed: Invocation | undefined;
  try { parsed = parseInvocation(JSON.stringify(base)); } catch (e) { parseError = String(e); }
  check('an authenticated invocation with sessionRestoreUrl is a valid invocation.v1',
    parsed?.sessionRestoreUrl === base.sessionRestoreUrl, parseError);

  // ── the loopback meeting-api ──
  const seen: Array<{ method?: string; url?: string; auth?: string }> = [];
  let status = 200;
  let answer = '';
  const server = createServer((req: IncomingMessage, res) => {
    seen.push({ method: req.method, url: req.url, auth: req.headers.authorization });
    req.resume();
    req.on('end', () => { res.statusCode = status; res.setHeader('content-type', 'application/json'); res.end(answer); });
  });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()));
  const port = (server.address() as AddressInfo).port;
  const live: Invocation = { ...base, sessionRestoreUrl: `http://127.0.0.1:${port}/mapi/internal/browser-session/conn-1234` };
  const dirs: string[] = [];
  const freshDir = (): string => { const d = mkdtempSync(join(tmpdir(), 'session-restore-')); dirs.push(d); return d; };

  const expectRefused = async (why: string, body: string, code = 200): Promise<void> => {
    status = code; answer = body;
    const dir = freshDir();
    let err: unknown;
    try { await restoreSession(live, dir); } catch (e) { err = e; }
    check(`${why}: a typed session-restore failure`,
      err instanceof SessionSyncError && (err as SessionSyncError).step === 'session-restore', String(err));
    check(`${why}: nothing is written`, readdirSync(dir).length === 0, JSON.stringify(readdirSync(dir)));
    check(`${why}: the failure names no profile bytes`, !String((err as Error)?.message).includes('secret-cookie'), String(err));
  };

  try {
    // ── a good answer: the contract's own golden ──
    const golden = readFileSync(join(GOLDEN, 'WritebackBody.session.json'), 'utf8');
    const goldenPaths = (JSON.parse(golden).files as Array<{ path: string }>).map((f) => f.path);
    status = 200; answer = golden;
    const dir = freshDir();
    const n = await restoreSession(live, dir);
    check('the restore wrote every file of the answer', n === goldenPaths.length, `${n} vs ${goldenPaths.length}`);
    check('one request reached meeting-api', seen.length === 1, String(seen.length));
    check("a GET to exactly the invocation's sessionRestoreUrl",
      seen[0]?.method === 'GET' && seen[0]?.url === '/mapi/internal/browser-session/conn-1234', `${seen[0]?.method} ${seen[0]?.url}`);
    check("authenticated with the bot's MeetingToken", seen[0]?.auth === 'Bearer meeting-token-for-session', String(seen[0]?.auth));
    const back = readSessionProfile(dir).map((f) => f.path).sort();
    check('the dir now holds exactly the profile paths of the answer', JSON.stringify(back) === JSON.stringify([...goldenPaths].sort()),
      JSON.stringify(back));

    // ── refused whole ──
    const cookie = { path: 'Default/Cookies', data: b64('secret-cookie') };
    await expectRefused('a path outside the profile', JSON.stringify({ files: [cookie, { path: 'Default/Extensions/x/manifest.json', data: b64('{}') }] }));
    await expectRefused('a traversal path', JSON.stringify({ files: [cookie, { path: 'Default/../../escape', data: b64('x') }] }));
    check('the traversal wrote nothing outside the dir', !existsSync(join(tmpdir(), 'escape')));
    await expectRefused('an HTTP error', '{"detail":"no bot account for this session"}', 404);
    await expectRefused('a server error', 'upstream down', 502);
    await expectRefused('a non-JSON answer', 'not json');
    await expectRefused('an answer with an extra field', JSON.stringify({ files: [cookie], owner: 'x' }));
    await expectRefused('an empty profile', JSON.stringify({ files: [] }));

    // ── no token ⇒ no request, typed failure ──
    const before = seen.length;
    let noToken: unknown;
    try { await restoreSession({ ...live, token: undefined }, freshDir()); } catch (e) { noToken = e; }
    check('no MeetingToken ⇒ a typed failure and no request', noToken instanceof SessionSyncError && seen.length === before);

    // ── unreachable meeting-api ⇒ typed failure ──
    let unreachable: unknown;
    try {
      await restoreSession(live, freshDir(), { fetchImpl: (async () => { throw new TypeError('fetch failed'); }) as typeof fetch });
    } catch (e) { unreachable = e; }
    check('an unreachable meeting-api is a typed session-restore failure',
      unreachable instanceof SessionSyncError && (unreachable as SessionSyncError).step === 'session-restore', String(unreachable));
  } finally {
    await new Promise<void>((r) => server.close(() => r()));
    for (const d of dirs) rmSync(d, { recursive: true, force: true });
  }

  // ── launchBrowser restores from the URL and writes back for it (source check; the launch is L4) ──
  const bridge = readFileSync(join(HERE, 'capture-bridge.ts'), 'utf8');
  check('launchBrowser restores over HTTP when sessionRestoreUrl is set',
    /inv\.authenticated && \(inv\.sessionRestoreUrl \|\| inv\.userdataS3Path\)/.test(bridge) && /await restoreSession\(inv, dataDir\)/.test(bridge));
  check('write-back runs for an authenticated bot with either userdataS3Path or sessionRestoreUrl',
    /inv\.authenticated && \(inv\.userdataS3Path \|\| inv\.sessionRestoreUrl\)/.test(bridge));

  if (failed) {
    console.error(`\n❌ session-restore.test — ${failed} check(s) failed`);
    process.exit(1);
  }
  console.log('\n✅ session-restore.test — the bot account profile comes from meeting-api, profile paths only, fail-loud');
}

main().catch((e) => { console.error(e); process.exit(1); });
