/**
 * L3 — a finished bot does not outlive its Redis credential. OFFLINE: the "redis" is an in-process
 * TCP server that answers every command with WRONGPASS, which is exactly what the bot sees once
 * meeting-api has removed its per-session Redis user (`ACL DELUSER` on the terminal callback).
 *
 * The defect: node-redis v4 reconnects forever by default, so after completion every bot client sat
 * in a WRONGPASS reconnect loop, a pending command (the voice note's XREAD, a last transcript
 * publish) never settled, `quit()` queued a QUIT that could never be sent, teardown never finished
 * and the process never exited.
 *
 * Run: tsx src/adapters/redis-terminal.test.ts
 */
import net from 'node:net';
import { botReconnectStrategy, createSessionEnd, isRedisAuthError } from './redis-terminal.js';
import { redisStreamClientFrom } from './voice-note-redis.js';
import { redisClientFrom } from './transcript-redis.js';
import { redisActsClientFrom } from './acts-redis.js';
import { makeLazyConnect } from './redis-lazy-connect.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
/** Resolve to 'settled' / 'rejected:<msg>' or 'pending' after `ms`. */
async function within<T>(p: Promise<T>, ms: number): Promise<string> {
  return Promise.race([
    p.then(() => 'settled', (e) => `rejected:${(e as Error)?.message ?? String(e)}`),
    sleep(ms).then(() => 'pending'),
  ]);
}

const WRONGPASS = 'WRONGPASS invalid username-password pair or user is disabled.';

/** A Redis that has forgotten this bot's user: every command is refused. Counts connections. */
async function revokedRedis(): Promise<{ url: string; connections: () => number; close: () => Promise<void> }> {
  let connections = 0;
  const sockets = new Set<net.Socket>();
  const server = net.createServer((s) => {
    connections++;
    sockets.add(s);
    s.on('close', () => sockets.delete(s));
    s.on('error', () => { /* client went away */ });
    s.on('data', () => { s.write(`-${WRONGPASS}\r\n`); });
  });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()));
  const port = (server.address() as net.AddressInfo).port;
  return {
    url: `redis://bot_user:secret@127.0.0.1:${port}`,
    connections: () => connections,
    close: () => new Promise<void>((r) => { for (const s of sockets) s.destroy(); server.close(() => r()); }),
  };
}

console.log('\n=== the reconnect rule ===');
{
  const session = createSessionEnd();
  const strategy = botReconnectStrategy(session);
  const auth = new Error(WRONGPASS);
  check('WRONGPASS / NOAUTH are recognised as auth failures',
    isRedisAuthError(auth) && isRedisAuthError(new Error('NOAUTH Authentication required.'))
    && !isRedisAuthError(new Error('Socket closed unexpectedly')));
  check('while the meeting runs, an auth failure is retried (redis restarted; meeting-api restores the user)',
    typeof strategy(0, auth) === 'number' && typeof strategy(50, auth) === 'number');
  check('while the meeting runs, a dropped socket is retried', typeof strategy(3, new Error('Socket closed unexpectedly')) === 'number');
  session.end();
  check('once the session is over, an auth failure is terminal', strategy(0, auth) instanceof Error);
  check('once the session is over, other failures stop after a few tries',
    typeof strategy(0, new Error('ECONNREFUSED')) === 'number' && strategy(10, new Error('ECONNREFUSED')) instanceof Error);
}

console.log('\n=== a finished voice note against a revoked credential ===');
{
  const redis = await revokedRedis();
  const session = createSessionEnd();
  const client = redisStreamClientFrom(redis.url, { session });
  // During the session the bot keeps trying: a restarted redis loses every ACL user until
  // meeting-api restores them, and a live bot must ride that out.
  const read = client.xReadBuffers('voice_note:meeting:7:audio', '0-0', 1000, 64);
  await sleep(400);
  check('during the session, an auth failure is retried', redis.connections() > 1, `connections=${redis.connections()}`);
  session.end();
  const outcome = await within(read, 2000);
  check('after completion the pending read settles instead of hanging', outcome.startsWith('rejected'), outcome);
  const seen = redis.connections();
  await sleep(600);
  check('and no further connection is attempted', redis.connections() === seen, `${seen} → ${redis.connections()}`);
  check('teardown (quit) finishes', (await within(client.quit(), 2000)) === 'settled');
  await redis.close();
}

console.log('\n=== the transcript writer and acts subscriber end the same way ===');
{
  const redis = await revokedRedis();
  const session = createSessionEnd();
  session.end();
  const tx = redisClientFrom(redis.url, { session });
  const acts = redisActsClientFrom(redis.url, { session });
  check('a publish after completion settles', (await within(tx.publish('tc:meeting:7', '{}'), 2000)).startsWith('rejected'));
  check('a subscribe after completion settles', (await within(acts.subscribe('bot_commands:meeting:7', () => {}), 2000)).startsWith('rejected'));
  const seen = redis.connections();
  await sleep(600);
  check('neither keeps reconnecting', redis.connections() === seen, `${seen} → ${redis.connections()}`);
  check('both quit', (await within(Promise.all([tx.quit(), acts.quit()]), 2000)) === 'settled');
  await redis.close();
}

console.log('\n=== quit never waits on a QUIT that cannot be sent ===');
{
  let disconnects = 0;
  const lazy = makeLazyConnect({
    connect: () => Promise.resolve(),
    quit: () => new Promise(() => { /* a QUIT queued on a socket that is not ready never answers */ }),
    disconnect: async () => { disconnects++; },
    isOpen: true,
    isReady: false,
  }, { quitSettleMs: 200 });
  await lazy.ensure();
  check('an open, not-ready client is disconnected, not quit', (await within(lazy.quit(), 1000)) === 'settled' && disconnects === 1);

  let disconnects2 = 0;
  const lazy2 = makeLazyConnect({
    connect: () => Promise.resolve(),
    quit: () => new Promise(() => { /* never answers */ }),
    disconnect: async () => { disconnects2++; },
    isOpen: true,
    isReady: true,
  }, { quitSettleMs: 200 });
  await lazy2.ensure();
  check('a QUIT that never answers is bounded, then the socket is dropped', (await within(lazy2.quit(), 1000)) === 'settled' && disconnects2 === 1);
}

console.log(`\nredis-terminal: ${failed ? `${failed} FAILED` : 'all passed'}`);
process.exit(failed ? 1 : 0);
