/**
 * room-identity — the room-device classifier (Vexa Rooms rung 1).
 *
 * Asserts:
 *   • the embedded table has NOT drifted from the cross-language source of truth
 *     (contracts/room-identity/room-patterns.json) — this is what keeps the TS producer and the
 *     Python read path agreeing;
 *   • real room-kit names classify as `room`, naming WHICH pattern fired;
 *   • the names that must stay `person` stay `person` — including `Roomradar`, a real Vexa
 *     customer, which is why the numbered-room pattern demands digits;
 *   • the honest failure is honest: a room kit named `Steve Jobs` (a real one, met on a customer
 *     call) reads `person`, because nothing in the data distinguishes it;
 *   • a refused / provisional label is `unknown`, never `person`;
 *   • VEXA_ROOM_PATTERNS replaces the table, `"+"` extends it, and every malformed shape fails
 *     SAFE — the defaults stand and the bot keeps running.
 *
 * Offline, no browser, no network. Run: npx tsx src/room-identity.test.ts
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import {
  DEFAULT_ROOM_PATTERNS,
  ROOM_PATTERNS_ENV,
  createRoomClassifier,
  parseRoomPatternsEnv,
} from './room-identity.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

const here = dirname(fileURLToPath(import.meta.url));
// src/ → bot/ → services/ → meetings/ → contracts/room-identity/
const SSOT = join(here, '..', '..', '..', 'contracts', 'room-identity', 'room-patterns.json');

console.log('\n── A1/A6: the embedded table mirrors the cross-language source of truth ──');
{
  const ssot = JSON.parse(readFileSync(SSOT, 'utf8')) as {
    patterns: { id: string; regex: string; why: string }[];
  };
  const fromFile = ssot.patterns.map((p) => [p.id, p.regex] as const);
  const embedded = DEFAULT_ROOM_PATTERNS.map(([id, src]) => [id, src] as const);
  check('same number of patterns', fromFile.length === embedded.length,
    `json=${fromFile.length} embedded=${embedded.length}`);
  for (let i = 0; i < Math.max(fromFile.length, embedded.length); i++) {
    const a = fromFile[i], b = embedded[i];
    check(`pattern[${i}] ${a?.[0] ?? '(missing)'} identical`,
      !!a && !!b && a[0] === b[0] && a[1] === b[1],
      `json=${JSON.stringify(a)} embedded=${JSON.stringify(b)}`);
  }
  check('every pattern carries its evidence note',
    ssot.patterns.every((p) => typeof p.why === 'string' && p.why.trim().length > 20));
}

const classify = createRoomClassifier({ env: {}, onWarn: () => { /* quiet */ } });

console.log('\n── A7: room devices are detected, and the classifier says which pattern fired ──');
for (const [name, patternId] of [
  ['Amsterdam — Room 2', 'numbered-room'],
  ['Meeting Room', 'room-word'],
  ['HQ Conference Room', 'room-word'],
  ['Huddle Space 4', 'room-word'],
  ['Boardroom', 'room-word'],
  ['Rm-14', 'numbered-room'],
  ['Utrecht (Room)', 'room-suffix'],
  ['devices/AAkZ4Wc', 'meet-device-resource'],
  ['Microsoft Teams Rooms', 'teams-room'],
  ['Surface Hub 2S', 'teams-room'],
  ['Zoom Room', 'zoom-room'],
  ['Series One Board 65', 'google-meet-hardware'],
  ['Chromebox for Meetings', 'google-meet-hardware'],
  ['Logitech Rally Bar', 'vendor-room-kit'],
  ['Poly Studio X50', 'vendor-room-kit'],
  ['Neat Board', 'vendor-room-kit'],
  ['Yealink MeetingBar A20', 'vendor-room-kit'],
  ['Cisco Room Kit Pro', 'vendor-room-kit'],
  ['Crestron Flex', 'vendor-room-kit'],
  ['Vergaderruimte Noord', 'room-word-non-english'],
  ['Salle de réunion 1', 'room-word-non-english'],
] as const) {
  const kind = classify(name);
  const fired = classify.matched(name);
  check(`"${name}" → room (${patternId})`, kind === 'room' && fired === patternId,
    `kind=${kind} pattern=${fired}`);
}

console.log('\n── A7: people stay people — the near misses that would break real customers ──');
for (const name of [
  'Robin Dirksen',
  'Dmitry Grankin',
  // A live Vexa customer. `\broom\b` alone would have classified this account as a room device —
  // which is why the numbered-room pattern requires digits.
  'Roomradar',
  'Zoom Video Communications',
  'Neatly Ltd',
  'Roommate Ventures',
  'Broom Hilda',
  // The vendor-model patterns end on a word boundary, so the plural of a model word is not a hit.
  'Poly Studios',
  'Neat Bars Inc',
]) {
  const kind = classify(name);
  // `Roommate Ventures` is the known cost of matching vendor model names; it is asserted in the
  // honest-limits block below, not here.
  if (name === 'Roommate Ventures') continue;
  check(`"${name}" → person`, kind === 'person', `kind=${kind} pattern=${classify.matched(name)}`);
}

console.log('\n── A7: the honest limits, asserted so nobody believes the detector is complete ──');
{
  // A REAL room kit, met on a customer call on 2026-09-15: a Lenovo Meet kit whose device-name
  // field somebody had filled in with `Steve Jobs`. Eighteen transcript segments of a Dutch
  // engineer speaking were attributed to it. This reads `person` and always will — nothing in
  // the data distinguishes a room named after a human from a human. The remedy is one
  // admin-console edit on the customer's side, not a cleverer regex.
  check('a room kit named "Steve Jobs" reads person — the documented blind spot',
    classify('Steve Jobs') === 'person');
  // The opposite cost: a company whose name contains a vendor model word is called a room.
  // Stated, not hidden — an operator hits it with VEXA_ROOM_PATTERNS.
  check('"Roommate Ventures" is a false positive — stated, not hidden',
    classify('Roommate Ventures') === 'room');
}

console.log('\n── unknown is not person: a refusal must never be dressed as a human ──');
for (const name of ['', '   ', 'Speaker', 'Speaker A', 'Speaker AB', 'seg_17', 'ch-3', 'ch-3:2', null, undefined]) {
  check(`${JSON.stringify(name)} → unknown`, classify(name) === 'unknown', `kind=${classify(name)}`);
}

console.log('\n── A2: VEXA_ROOM_PATTERNS replaces, "+" extends, malformed fails SAFE ──');
{
  const replaced = createRoomClassifier({
    env: { [ROOM_PATTERNS_ENV]: JSON.stringify(['^HQ-']) },
    onWarn: () => { /* quiet */ },
  });
  check('replace mode: the override matches', replaced('HQ-Oslo') === 'room');
  check('replace mode: the defaults are GONE', replaced('Meeting Room') === 'person');
  check('replace mode: pattern ids are labelled env:*', replaced.patternIds.join(',') === 'env:0');

  const appended = createRoomClassifier({
    env: { [ROOM_PATTERNS_ENV]: JSON.stringify(['+', '^HQ-']) },
    onWarn: () => { /* quiet */ },
  });
  check('append mode: the override matches', appended('HQ-Oslo') === 'room');
  check('append mode: the defaults SURVIVE', appended('Meeting Room') === 'room');
  check('append mode: the table is defaults + 1',
    appended.patternIds.length === DEFAULT_ROOM_PATTERNS.length + 1);

  for (const [label, raw] of [
    ['not JSON', 'meeting room, board room'],
    ['not an array', '{"patterns":["x"]}'],
    ['not strings', '[1,2,3]'],
    ['empty array', '[]'],
    ['only the + marker', '["+"]'],
  ] as const) {
    let warned = 0;
    const safe = createRoomClassifier({ env: { [ROOM_PATTERNS_ENV]: raw }, onWarn: () => { warned++; } });
    check(`malformed (${label}): defaults stand`,
      safe('Meeting Room') === 'room' && safe.patternIds.length === DEFAULT_ROOM_PATTERNS.length);
    check(`malformed (${label}): the refusal is surfaced, not swallowed`, warned === 1);
    check(`malformed (${label}): parse returns null`, parseRoomPatternsEnv(raw, () => {}) === null);
  }

  let dropped = 0;
  const partial = createRoomClassifier({
    env: { [ROOM_PATTERNS_ENV]: JSON.stringify(['^HQ-', '([unclosed']) },
    onWarn: () => { dropped++; },
  });
  check('one uncompilable pattern is dropped, the rest survive',
    partial('HQ-Oslo') === 'room' && partial.patternIds.join(',') === 'env:0' && dropped === 1);

  check('unset env → defaults', parseRoomPatternsEnv(undefined) === null && classify('Meeting Room') === 'room');
}

if (failed) {
  console.error(`\n❌ room-identity: ${failed} check(s) failed`);
  process.exit(1);
}
console.log('\n✅ room-identity — the table matches the cross-language SSOT; room kits are detected with the pattern named; people (Roomradar included) stay people; a refusal stays `unknown`; the "Steve Jobs" blind spot and the `Roommate` false positive are asserted rather than hidden; VEXA_ROOM_PATTERNS replaces/extends and every malformed shape leaves the defaults running.');
