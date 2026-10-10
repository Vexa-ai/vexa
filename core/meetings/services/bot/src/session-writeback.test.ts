/**
 * Session write-back — an authenticated bot hands its session back to meeting-api, never to storage.
 * OFFLINE: a loopback http server stands in for meeting-api, and a fake `aws` on PATH records any
 * call so the test can prove there is none.
 *
 *   • the URL is meeting-api's own (derived from the callback URL it built, path prefix kept), with
 *     this bot's session uid as the last segment; no meeting-api in the invocation ⇒ no write-back;
 *   • the request is a PUT with the bot's MeetingToken, and its body names SESSION_PROFILE files only
 *     — an extension, a cache file, a LevelDB LOCK and a symlink at a profile name are left out;
 *   • the write-back never runs the aws CLI (the bot's storage key is read-only);
 *   • a refusal from meeting-api rejects with its status (the caller logs it; teardown carries on).
 * Run: npx tsx src/session-writeback.test.ts
 */
import { createServer, type IncomingMessage } from 'node:http';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { isSessionProfilePath } from '@vexa/remote-browser';
import { sessionWritebackUrl, writeBackSession, SESSION_WRITEBACK_PATH } from './session-writeback.js';
import type { Invocation } from './config.js';

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

  // ── the URL is meeting-api's, derived from what meeting-api itself put in the invocation ──
  check('derived from the lifecycle callback URL',
    sessionWritebackUrl(inv()) === `http://meeting-api:8080${SESSION_WRITEBACK_PATH}/conn-1234`, String(sessionWritebackUrl(inv())));
  check('a path prefix on meeting-api is kept',
    sessionWritebackUrl(inv({ meetingApiCallbackUrl: 'https://h/api/bots/internal/callback/lifecycle' }))
      === `https://h/api${SESSION_WRITEBACK_PATH}/conn-1234`);
  check('falls back to the recording upload URL',
    sessionWritebackUrl(inv({ meetingApiCallbackUrl: undefined }))
      === `http://meeting-api:8080${SESSION_WRITEBACK_PATH}/conn-1234`);
  check('the session uid is one encoded path segment',
    sessionWritebackUrl(inv({ connectionId: '../x/y' }))!.endsWith(`${SESSION_WRITEBACK_PATH}/..%2Fx%2Fy`));
  check('no meeting-api in the invocation ⇒ no URL',
    sessionWritebackUrl(inv({ meetingApiCallbackUrl: undefined, recordingUploadUrl: undefined })) === null);
  check('no session uid ⇒ no URL', sessionWritebackUrl(inv({ connectionId: undefined })) === null);

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
  const live = inv({ meetingApiCallbackUrl: `http://127.0.0.1:${port}/bots/internal/callback/lifecycle` });

  try {
    const result = await writeBackSession(live, dataDir);
    check('meeting-api accepted the session files', result.sent === 3 && !result.skipped, JSON.stringify(result));
    const req = seen[0];
    check('one request reached meeting-api', seen.length === 1, String(seen.length));
    check('a PUT to the session route', req?.method === 'PUT' && req?.url === `${SESSION_WRITEBACK_PATH}/conn-1234`, `${req?.method} ${req?.url}`);
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
    check('the write-back never runs the aws CLI', !existsSync(awsLog));

    // ── a refusal rejects with the status; nothing is retried ──
    answer = 403;
    let refused = '';
    try { await writeBackSession(live, dataDir); } catch (e) { refused = String(e); }
    check('a refusal rejects naming the status', refused.includes('HTTP 403'), refused);
    check('a refusal is one attempt', seen.length === 2, String(seen.length));

    // ── nothing to send ⇒ nothing sent ──
    const none = await writeBackSession(inv({ meetingApiCallbackUrl: undefined, recordingUploadUrl: undefined }), dataDir);
    check('no meeting-api route ⇒ skipped, no request', none.sent === 0 && !!none.skipped && seen.length === 2);
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

  if (failed) {
    console.error(`\n❌ session-writeback.test — ${failed} check(s) failed`);
    process.exit(1);
  }
  console.log('\n✅ session-writeback.test — the session goes to meeting-api, profile files only, with the MeetingToken');
}

main().catch((e) => { console.error(e); process.exit(1); });
