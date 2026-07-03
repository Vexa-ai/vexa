/**
 * L3 — acts-redis adapter (redis pub/sub command ingress). OFFLINE, NO real redis.
 *
 * Injects a fake client that delivers raw JSON messages to the subscribed callback and asserts:
 *   • it SUBSCRIBEs the documented channel `bot_commands:meeting:{meetingId}`;
 *   • a real acts.v1 GOLDEN message (Act.leave.json) parses to an Act and reaches the handler;
 *   • a non-leave golden (Act.speak.json) also reaches the handler intact;
 *   • a malformed (non-JSON) message and an unknown-action message are IGNORED, not thrown,
 *     and never reach the handler;
 *   • the returned unsubscribe fn calls the client's unsubscribe.
 * Run: npx tsx src/adapters/acts-redis.test.ts
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRedisActsSource, type RedisActsClient } from './acts-redis.js';
import { actsChannel, type Act } from '../contracts.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

const HERE = dirname(fileURLToPath(import.meta.url));
const ACTS_GOLDEN = join(HERE, '..', '..', '..', '..', 'contracts', 'acts.v1', 'golden');
const golden = (n: string): string => readFileSync(join(ACTS_GOLDEN, n), 'utf8');

/** A fake redis subscriber: captures the channel + listener so the test can DELIVER messages. */
function fakeClient() {
  let subscribedChannel: string | undefined;
  let listener: ((m: string) => void) | undefined;
  let unsubscribed = 0;
  const client: RedisActsClient = {
    subscribe(channel, cb) { subscribedChannel = channel; listener = cb; },
    unsubscribe() { unsubscribed++; },
  };
  return {
    client,
    get channel() { return subscribedChannel; },
    deliver: (m: string) => listener?.(m),
    get unsubscribed() { return unsubscribed; },
  };
}

async function main(): Promise<void> {
  // ── subscribes the documented channel ──
  {
    const fake = fakeClient();
    const src = createRedisActsSource({ client: fake.client, meetingId: 42 });
    src.subscribe(() => {});
    check('subscribe: channel = bot_commands:meeting:42', fake.channel === actsChannel(42), fake.channel);
    check('subscribe: channel matches documented format', fake.channel === 'bot_commands:meeting:42', fake.channel);
  }

  // ── a real acts.v1 golden (leave) reaches the handler as a parsed Act ──
  {
    const fake = fakeClient();
    const received: Act[] = [];
    const src = createRedisActsSource({ client: fake.client, meetingId: 42 });
    src.subscribe((a) => { received.push(a); });
    fake.deliver(golden('Act.leave.json'));
    await new Promise((r) => setImmediate(r)); // let the async handler dispatch settle
    check('leave: one act reached the handler', received.length === 1, String(received.length));
    check('leave: parsed to { action: leave }', received[0]?.action === 'leave', JSON.stringify(received[0]));
  }

  // ── a non-leave golden (speak) reaches the handler intact ──
  {
    const fake = fakeClient();
    const received: Act[] = [];
    const src = createRedisActsSource({ client: fake.client, meetingId: 42 });
    src.subscribe((a) => { received.push(a); });
    fake.deliver(golden('Act.speak.json'));
    await new Promise((r) => setImmediate(r));
    check('speak: reached the handler', received.length === 1 && received[0]?.action === 'speak', JSON.stringify(received[0]));
    check('speak: text carried through', (received[0] as { text?: string })?.text === 'Joining now — one moment.');
  }

  // ── malformed + unknown messages are IGNORED, never thrown, never delivered ──
  {
    const fake = fakeClient();
    const received: Act[] = [];
    const src = createRedisActsSource({ client: fake.client, meetingId: 42 });
    src.subscribe((a) => { received.push(a); });
    let threw = false;
    try {
      fake.deliver('not json {');                                  // malformed
      fake.deliver(JSON.stringify({ action: 'frobnicate' }));      // unknown action
      fake.deliver(JSON.stringify({ no: 'action' }));              // missing discriminator
      fake.deliver('null');                                        // valid JSON, not an object
    } catch { threw = true; }
    await new Promise((r) => setImmediate(r));
    check('garbage: nothing thrown out of the message path', threw === false);
    check('garbage: nothing reached the handler', received.length === 0, String(received.length));
  }

  // ── the unsubscribe fn tears the subscription down ──
  {
    const fake = fakeClient();
    const src = createRedisActsSource({ client: fake.client, meetingId: 42 });
    const unsub = src.subscribe(() => {});
    unsub();
    await new Promise((r) => setImmediate(r));
    check('unsubscribe: client.unsubscribe called', fake.unsubscribed === 1, String(fake.unsubscribed));
  }

  if (failed) { console.error(`\n❌ acts-redis (L3): ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ acts-redis (L3): subscribes bot_commands:meeting:{id}, routes acts.v1 goldens through parseAct to the handler, ignores malformed/unknown messages.');
}

void main();
