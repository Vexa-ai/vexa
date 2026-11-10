/**
 * Session write-back — an authenticated bot hands its session back to meeting-api, never to storage.
 * OFFLINE: a loopback http server stands in for meeting-api, and a fake `aws` on PATH records any
 * call so the test can prove there is none.
 *
 *   • the URL is the invocation's `sessionWritebackUrl`, used as given; an invocation without it gets
 *     no write-back, even when it names meeting-api's callback and upload URLs (nothing is derived);
 *   • the invocation that carries it passes the bot's invocation.v1 validation, and the body it sends
 *     is a session-profile.v1 `WritebackBody` that the contract accepts;
 *   • the request is a PUT with the bot's MeetingToken, and its body names SESSION_PROFILE files only
 *     — an extension, a cache file, a LevelDB LOCK and a symlink at a profile name are left out;
 *   • the write-back never runs the aws CLI (the bot's storage key is read-only);
 *   • a refusal from meeting-api rejects with its status (the caller logs it; teardown carries on);
 *   • only a bot that got past sign-in writes back: admitted or lobby reached ⇒ write; never past
 *     joining / needs_help from joining ⇒ no write; a guest ⇒ no write. The evidence comes from the
 *     lifecycle statuses the bot emits, and launchBrowser's teardown asks for it.
 * Run: npx tsx src/session-writeback.test.ts
 */
import { createServer, type IncomingMessage } from 'node:http';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Ajv2020 } from 'ajv/dist/2020.js';
import { isSessionProfilePath } from '@vexa/remote-browser';
import { writeBackSession, shouldWriteBack, createSignInEvidence, observeLifecycle } from './session-writeback.js';
import { parseInvocation, type Invocation } from './config.js';

// src/ → ../../../contracts/session-profile.v1 (meetings/services/bot/src → meetings/contracts/…)
const CONTRACT = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', 'contracts', 'session-profile.v1');
const sessionProfileSchema = JSON.parse(readFileSync(join(CONTRACT, 'session-profile.schema.json'), 'utf8'));
const contractAjv = new Ajv2020({ strict: false, allErrors: true });
contractAjv.addSchema(sessionProfileSchema);
const isWritebackBody = contractAjv.compile({ $ref: `${sessionProfileSchema.$id}#/$defs/WritebackBody` });

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

const inv = (over: Partial<Invocation> = {}): Invocation => ({
  platform: 'google_meet',
  meetingUrl: 'https://meet.google.com/abc-defg-hij',
  botName: 'VexaBot',
  redisUrl: 'redis://redis:6379/0',
  token: 'meeting-token-for-session',
  connectionId: 'conn-1234',
  meetingApiCallbackUrl: 'http://meeting-api:8080/bots/internal/callback/lifecycle',
  recordingUploadUrl: 'http://meeting-api:8080/internal/recordings/upload',
  sessionWritebackUrl: 'http://meeting-api:8080/internal/browser-session/conn-1234',
  authenticated: true,
  userdataS3Path: 'userdata/bot-identity-1',
  s3Endpoint: 'http://storage:9000',
  s3Bucket: 'vexa',
  s3AccessKey: 'read-only-key',
  s3SecretKey: 'read-only-secret',
  ...over,
});

async function main(): Promise<void> {
  console.log('session write-back');

  // ── the field is invocation.v1's: an invocation carrying it passes the bot's own boot validation ──
  let parsed: Invocation | undefined;
  let parseError = '';
  try { parsed = parseInvocation(JSON.stringify(inv())); } catch (e) { parseError = String(e); }
  check('an authenticated invocation with sessionWritebackUrl is a valid invocation.v1',
    parsed?.sessionWritebackUrl === 'http://meeting-api:8080/internal/browser-session/conn-1234', parseError);

  // ── a live profile with session files and things that are not ──
  const work = mkdtempSync(join(tmpdir(), 'session-writeback-'));
  const dataDir = join(work, 'profile');
  mkdirSync(join(dataDir, 'Default', 'Local Storage', 'leveldb'), { recursive: true });
  mkdirSync(join(dataDir, 'Default', 'Extensions', 'planted'), { recursive: true });
  mkdirSync(join(dataDir, 'Default', 'Cache'), { recursive: true });
  writeFileSync(join(dataDir, 'Local State'), '{"os_crypt":{}}');
  writeFileSync(join(dataDir, 'Default', 'Cookies'), 'rotated-cookies');
  writeFileSync(join(dataDir, 'Default', 'Local Storage', 'leveldb', 'CURRENT'), 'MANIFEST-000001\n');
  writeFileSync(join(dataDir, 'Default', 'Local Storage', 'leveldb', 'LOCK'), '');
  writeFileSync(join(dataDir, 'Default', 'Extensions', 'planted', 'manifest.json'), '{}');
  writeFileSync(join(dataDir, 'Default', 'Cache', 'data_0'), 'cache');
  writeFileSync(join(work, 'elsewhere'), 'outside the profile');
  symlinkSync(join(work, 'elsewhere'), join(dataDir, 'Default', 'Preferences'));

  // a fake aws that records any call — the write-back must make none
  const bin = join(work, 'bin');
  const awsLog = join(work, 'aws.log');
  mkdirSync(bin);
  writeFileSync(join(bin, 'aws'), `#!/bin/sh\necho "$@" >> "${awsLog}"\nexit 0\n`);
  chmodSync(join(bin, 'aws'), 0o755);
  const realPath = process.env.PATH;
  process.env.PATH = `${bin}:${realPath ?? ''}`;

  // ── the loopback meeting-api ──
  const seen: Array<{ method?: string; url?: string; auth?: string; type?: string; body: string }> = [];
  let answer = 200;
  const server = createServer((req: IncomingMessage, res) => {
    const chunks: Buffer[] = [];
    req.on('data', (c: Buffer) => chunks.push(c));
    req.on('end', () => {
      seen.push({ method: req.method, url: req.url, auth: req.headers.authorization,
                  type: req.headers['content-type'], body: Buffer.concat(chunks).toString('utf8') });
      res.statusCode = answer;
      res.end(answer === 200 ? '{"written":3}' : '{"detail":"not the live authenticated session"}');
    });
  });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()));
  const port = (server.address() as AddressInfo).port;
  // The callback and upload URLs point at the same loopback server, at paths a derivation would turn
  // into a different write-back URL: only the invocation's own field may be used.
  const live = inv({
    meetingApiCallbackUrl: `http://127.0.0.1:${port}/bots/internal/callback/lifecycle`,
    recordingUploadUrl: `http://127.0.0.1:${port}/internal/recordings/upload`,
    sessionWritebackUrl: `http://127.0.0.1:${port}/mapi/internal/browser-session/conn-1234`,
  });

  try {
    const result = await writeBackSession(live, dataDir);
    check('meeting-api accepted the session files', result.sent === 3 && !result.skipped, JSON.stringify(result));
    const req = seen[0];
    check('one request reached meeting-api', seen.length === 1, String(seen.length));
    check("a PUT to exactly the invocation's sessionWritebackUrl",
      req?.method === 'PUT' && req?.url === '/mapi/internal/browser-session/conn-1234', `${req?.method} ${req?.url}`);
    check("authenticated with the bot's MeetingToken", req?.auth === 'Bearer meeting-token-for-session', String(req?.auth));
    check('a JSON body', req?.type === 'application/json', String(req?.type));
    const files = (JSON.parse(req?.body ?? '{}').files ?? []) as Array<{ path: string; data: string }>;
    const paths = files.map((f) => f.path);
    check('the body names exactly the session files present',
      JSON.stringify(paths) === JSON.stringify(['Local State', 'Default/Cookies', 'Default/Local Storage/leveldb/CURRENT']),
      JSON.stringify(paths));
    check('every path in the body is a session profile path', paths.every(isSessionProfilePath));
    check('the bytes ride base64', Buffer.from(files.find((f) => f.path === 'Default/Cookies')?.data ?? '', 'base64').toString() === 'rotated-cookies');
    for (const planted of ['Extensions', 'Cache', 'LOCK', 'Preferences']) {
      check(`'${planted}' is not sent`, !(req?.body ?? '').includes(planted));
    }
    check('no entry carries anything but a path and its bytes', files.every((f) => Object.keys(f).sort().join() === 'data,path'));
    check('the body is a session-profile.v1 WritebackBody', isWritebackBody(JSON.parse(req?.body ?? '{}')) === true,
      contractAjv.errorsText(isWritebackBody.errors));
    check('the write-back never runs the aws CLI', !existsSync(awsLog));

    // ── a refusal rejects with the status; nothing is retried ──
    answer = 403;
    let refused = '';
    try { await writeBackSession(live, dataDir); } catch (e) { refused = String(e); }
    check('a refusal rejects naming the status', refused.includes('HTTP 403'), refused);
    check('a refusal is one attempt', seen.length === 2, String(seen.length));

    // ── nothing to send ⇒ nothing sent ──
    const none = await writeBackSession({ ...live, sessionWritebackUrl: undefined }, dataDir);
    check('no sessionWritebackUrl ⇒ skipped, no request, nothing derived from the callback or upload URL',
      none.sent === 0 && !!none.skipped && seen.length === 2, `${JSON.stringify(none)} after ${seen.length} request(s)`);
    const noToken = await writeBackSession({ ...live, token: undefined }, dataDir);
    check('no MeetingToken ⇒ skipped, no request', noToken.sent === 0 && !!noToken.skipped && seen.length === 2);
    const empty = join(work, 'empty-profile');
    mkdirSync(empty);
    const nothing = await writeBackSession(live, empty);
    check('an empty profile ⇒ skipped, no request', nothing.sent === 0 && !!nothing.skipped && seen.length === 2);
  } finally {
    process.env.PATH = realPath;
    await new Promise<void>((r) => server.close(() => r()));
    rmSync(work, { recursive: true, force: true });
  }

  // ── the write-back decision: only a bot that got past sign-in ──
  const lane = (statuses: string[]) => { const e = createSignInEvidence(); statuses.forEach((x) => e.observe(x)); return e.evidence(); };
  const table: Array<[string, boolean, string[], boolean]> = [
    ['admitted ⇒ write', true, ['joining', 'active'], true],
    ['lobby reached ⇒ write', true, ['joining', 'awaiting_admission', 'failed'], true],
    ['helped in through the live view, then admitted ⇒ write', true, ['joining', 'needs_help', 'active', 'completed'], true],
    ['never past joining ⇒ no write', true, ['joining', 'failed'], false],
    ['needs_help entered from joining, then ended ⇒ no write', true, ['joining', 'needs_help', 'failed'], false],
    ['nothing reported ⇒ no write', true, [], false],
    ['guest invocation ⇒ no write, even admitted', false, ['joining', 'active'], false],
  ];
  for (const [name, authenticated, statuses, want] of table) {
    check(`decision: ${name}`, shouldWriteBack(authenticated, lane(statuses)) === want, JSON.stringify(lane(statuses)));
  }
  // wiring: the observed sink sees every status and still delivers each event unchanged
  const delivered: unknown[] = [];
  const ev = createSignInEvidence();
  const observed = observeLifecycle({
    async emit(e) { delivered.push(e); },
    async emitReachable(e) { delivered.push(e); return 'reachable'; },
  }, (st) => ev.observe(st));
  await observed.emitReachable!({ connection_id: 'c', status: 'joining' } as any);
  await observed.emit({ connection_id: 'c', status: 'awaiting_admission' } as any);
  check('the observed sink delivers every event', delivered.length === 2);
  check('the observed sink records the lobby', ev.evidence().lobbyReached && !ev.evidence().admitted, JSON.stringify(ev.evidence()));
  const noGate = observeLifecycle({ async emit() {} }, () => { throw new Error('observer fault'); });
  check('a sink without the reachability gate stays without it', noGate.emitReachable === undefined);
  let threw = false;
  try { await noGate.emit({ connection_id: 'c', status: 'active' } as any); } catch { threw = true; }
  check('an observer fault never stops a report', !threw);
  // launchBrowser's teardown asks for the decision before writing back (source check; the launch is L4)
  const bridge = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'capture-bridge.ts'), 'utf8');
  check('teardown withholds the write-back without sign-in evidence',
    /!shouldWriteBack\(inv\.authenticated, opts\.signInEvidence\?\.\(\) \?\? \{ lobbyReached: false, admitted: false \}\)/.test(bridge));
  const index = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'index.ts'), 'utf8');
  check('the composition root feeds the lifecycle statuses to the evidence and hands it to the launch',
    /observeLifecycle\(lifecycleSink, \(status\) => signIn\.observe\(status\)\)/.test(index)
    && /launchBrowser\(inv, \{ signInEvidence: signIn\.evidence \}\)/.test(index));

  if (failed) {
    console.error(`\n❌ session-writeback.test — ${failed} check(s) failed`);
    process.exit(1);
  }
  console.log('\n✅ session-writeback.test — the session goes to meeting-api, profile files only, with the MeetingToken');
}

main().catch((e) => { console.error(e); process.exit(1); });
