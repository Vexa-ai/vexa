/**
 * The ingest WebSocket never carries the API key in its URL (card ca078278e83d).
 *  • ingestSocket() puts the key in the subprotocol list (ws.v1 § Connect), not the URL.
 *  The source-wide grep (no `api_key` anywhere in src/) is scripts/check-no-url-key.mjs.
 * Pure: no chrome. Run: npx tsx src/ingest-socket.test.ts
 */
import { ingestSocket } from './ingest-socket.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

const KEY = 'vxa_test_KEY_1234567890';
const meeting = { platform: 'teams', nativeMeetingId: '1234567890123' };
{
  const s = ingestSocket('ws://localhost:9099/ingest', meeting, 'auto', KEY);
  check('key is not in the URL', !s.url.includes(KEY), s.url);
  check('no api_key parameter in the URL', !/[?&]api_key=/.test(s.url), s.url);
  check('subprotocols = [vexa.v1, vexa.key.<key>]',
    JSON.stringify(s.protocols) === JSON.stringify(['vexa.v1', `vexa.key.${KEY}`]), JSON.stringify(s.protocols));
  const q = new URL(s.url).searchParams;
  check('meeting identity + language stay in the URL',
    q.get('platform') === 'teams' && q.get('native_meeting_id') === '1234567890123' && q.get('language') === 'auto', s.url);
}
{
  const s = ingestSocket('ws://localhost:9099/ingest', meeting, 'auto', '');
  check('no key → no subprotocols', s.protocols.length === 0, JSON.stringify(s.protocols));
}

if (failed) { console.log(`\n${failed} check(s) failed`); (globalThis as any).process.exit(1); }
console.log('\nall ingest-socket checks passed');
