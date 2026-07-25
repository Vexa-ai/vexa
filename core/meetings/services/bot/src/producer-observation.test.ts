/**
 * Producer identity failures cross page→Node on their own typed observation
 * boundary. They are counted and reported, never promoted into speaker hints.
 *
 *   tsx src/producer-observation.test.ts
 */
import { makeNameUnresolvedSink } from './capture-bridge.js';

let checks = 0;
function ok(condition: boolean, message: string): void {
  if (!condition) throw new Error(`assertion failed: ${message}`);
  console.log(`  ✅ ${message}`);
  checks++;
}

const logs: string[] = [];
const observations = makeNameUnresolvedSink((message) => logs.push(message));

observations.sink({
  type: 'name-unresolved',
  platform: 'teams',
  signal: 'dom-outline',
  reason: 'resolver-empty',
  edge: 'start',
  tMs: 1_700_000_000_000,
  participantId: 'unexpected-extra-field',
});

let counts = observations.counters();
ok(
  counts.total === 1 && counts.teams === 1 && counts.zoom === 0 && counts.invalid === 0,
  'one valid Teams observation is admitted and counted by platform',
);
ok(
  logs.some((message) => message.includes('name-unresolved') && message.includes('resolver-empty')),
  'the observation is reported on the Node-side telemetry channel',
);
ok(
  logs.every((message) => !message.includes('unexpected-extra-field')),
  'sanitized Node telemetry never logs arbitrary producer fields',
);

observations.sink({
  type: 'name-unresolved',
  platform: 'zoom',
  signal: 'dom-active',
  reason: 'footer-empty',
  tMs: 1_700_000_000_001,
});

counts = observations.counters();
ok(
  counts.total === 2 && counts.teams === 1 && counts.zoom === 1 && counts.invalid === 0,
  'one valid Zoom observation is admitted without inventing a Teams edge',
);
ok(
  logs.some((message) =>
    message.includes('platform=zoom')
    && message.includes('reason=footer-empty')
    && !message.includes('edge=')),
  'Zoom telemetry preserves the exclusive-poll contract instead of a per-participant edge',
);

observations.sink({
  type: 'name-unresolved',
  platform: 'teams',
  signal: 'dom-active',
  reason: 'resolver-empty',
  edge: 'start',
  tMs: 1_700_000_000_001,
});

counts = observations.counters();
ok(
  counts.total === 2 && counts.invalid === 1,
  'a platform/signal mismatch is rejected rather than counted as evidence',
);
ok(
  logs.some((message) => message.includes('producer-observation-invalid')),
  'a malformed producer observation fails loud',
);

console.log(`\n✅ producer-observation: ${checks} checks passed — unresolved identity is visible but never becomes a hint.`);
