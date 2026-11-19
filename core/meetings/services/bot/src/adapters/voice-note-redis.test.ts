/**
 * The voice note's Redis reader (L3, offline): an XREAD reply maps onto stream entries, frames stay
 * binary, the end marker is recognised, and the reader asks for the entries after the last id.
 *
 * Run: tsx src/adapters/voice-note-redis.test.ts
 */
import { createRedisAudioStreamReader, entriesFrom, type XReadReply } from './voice-note-redis.js';

let passed = 0, failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  if (cond) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name}${detail ? ` — ${detail}` : ''}`); failed++; }
};

console.log('\n=== voice-note-redis: reply → entries ===');
const frame = Buffer.from([0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 255, 254, 253, 252]);
const reply: XReadReply = [{
  name: Buffer.from('voice_note:meeting:7:audio'),
  messages: [
    { id: Buffer.from('1-0'), message: { f: frame } },
    { id: Buffer.from('2-0'), message: { end: Buffer.from('1') } },
  ],
}];
const entries = entriesFrom(reply);
check('two entries', entries.length === 2);
check('the frame keeps every byte (binary-safe)', entries[0].id === '1-0' && entries[0].frame?.byteLength === 16 && entries[0].frame?.[12] === 255);
check('the end marker is recognised', entries[1].id === '2-0' && entries[1].end === true && !entries[1].frame);
check('a null reply (BLOCK timed out) is no entries', entriesFrom(null).length === 0);
const kept = entriesFrom([{ name: 'k', messages: [{ id: '3-0', message: { alive: Buffer.from('1') } }] }]);
check('a keepalive is recognised and carries no frame', kept[0].alive === true && !kept[0].frame && !kept[0].end);

console.log('\n=== voice-note-redis: the reader asks for what follows the last id ===');
const calls: Array<[string, string, number, number]> = [];
const reader = createRedisAudioStreamReader({
  key: 'voice_note:meeting:7:audio',
  client: { async xReadBuffers(key, after, block, count) { calls.push([key, after, block, count]); return reply; } },
});
const got = await reader.read('0-0', 1000);
check('one XREAD on the note key, after the given id, blocking', calls.length === 1 && calls[0][0] === 'voice_note:meeting:7:audio' && calls[0][1] === '0-0' && calls[0][2] === 1000 && calls[0][3] > 0);
check('entries come back', got.length === 2);

console.log(`\nvoice-note-redis: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
