/**
 * #956 M1 causal-contract RED.
 *
 * The authored captured-signal tape models Jitsi's exclusive, trailing
 * dominant-speaker signal. Boris's acoustic turn starts while Redux still
 * reports Anna; the repeated Anna event is therefore a heartbeat ("no
 * transition detected"), not fresh testimony that Anna started this turn.
 * Jitsi reports the Anna→Boris transition 2,803 ms after acoustic onset.
 *
 * Causal contract:
 *   - before that exclusive signal advances, the turn is UNKNOWN;
 *   - after it advances, the stale outgoing name cannot remain authoritative.
 *
 * Current expected RED: the shared dom-active model treats the heartbeat as a
 * new open Anna turn, window-matches it uncontested, and fabricates confidence
 * 1.0. The later correct transition cannot repair this closed turn.
 *
 * Run explicitly (it is intentionally excluded from the green package suite):
 *   pnpm --filter @vexa/mixed-pipeline exec tsx src/causal-stale-heartbeat.red.test.ts
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { ClusterNameBinder, type HintEvent } from './index.js';

interface TapeHint {
  type: 'hint';
  t: number;
  name: string;
  isEnd?: boolean;
}

interface TapeBoundary {
  type: 'boundary';
  tMs: number;
  kind: string;
}

const here = dirname(fileURLToPath(import.meta.url));
const tapePath = join(
  here,
  '..',
  '..',
  '..',
  'eval',
  'replay-fixture',
  'm1-jitsi-stale-heartbeat.captured-signal.jsonl',
);
const records = readFileSync(tapePath, 'utf8')
  .trim()
  .split('\n')
  .map((line) => JSON.parse(line) as Record<string, unknown>);
const header = records[0];
assert.equal(header.type, 'captured_signal_header');
assert.equal(header.platform, 'jitsi');
assert.equal(header.lane, 'mixed');

const hints = records.filter((record) => record.type === 'hint') as unknown as TapeHint[];
const boundaries = records.filter(
  (record) => record.type === 'boundary',
) as unknown as TapeBoundary[];
const onset = boundaries.find((record) => record.kind === 'speaker→speaker')?.tMs;
const end = boundaries.find((record) => record.kind === 'speaker→silence')?.tMs;
const correctStart = hints.find((hint) => hint.name === 'Boris' && !hint.isEnd)?.t;
assert(onset !== undefined && end !== undefined && correctStart !== undefined);
assert.equal(correctStart - onset, 2803, 'authored onset→identity-start envelope');

const commit = { clusterId: 'seg_boris_turn', tStartMs: onset, tEndMs: end };
const asCurrentHint = (hint: TapeHint): HintEvent => ({
  name: hint.name,
  tMs: hint.t,
  kind: 'dom-active',
  isEnd: hint.isEnd,
});

// Negative control: absence of testimony cannot invent a display name.
const nullControl = new ClusterNameBinder({}).resolve(commit, { recordVote: false });
assert.equal(nullControl.speakerName, commit.clusterId);
assert.equal(nullControl.source, 'provisional-cluster-id');

// One-dial falsifier for M1: keep the prior transition but remove only its
// repeated heartbeat. The old turn has decayed and cannot name the new one.
const withoutHeartbeatBinder = new ClusterNameBinder({});
const initialOutgoingHint = hints.find((hint) => hint.name === 'Anna' && !hint.isEnd);
assert(initialOutgoingHint);
withoutHeartbeatBinder.recordHint(asCurrentHint(initialOutgoingHint));
const withoutHeartbeat = withoutHeartbeatBinder.resolve(commit, { recordVote: false });
assert.equal(withoutHeartbeat.speakerName, commit.clusterId);
assert.equal(withoutHeartbeat.source, 'provisional-cluster-id');

const binder = new ClusterNameBinder({});
for (const hint of hints.filter((event) => event.t < correctStart)) {
  binder.recordHint(asCurrentHint(hint));
}
const beforeAdvance = binder.resolve(commit, { recordVote: false });

for (const hint of hints.filter((event) => event.t >= correctStart)) {
  binder.recordHint(asCurrentHint(hint));
}
const afterAdvance = binder.resolve(commit, { recordVote: false });

const beforeIsUnknown =
  beforeAdvance.speakerName === commit.clusterId
  && beforeAdvance.source === 'provisional-cluster-id';
const staleNameWasRepairable = afterAdvance.speakerName !== 'Anna';
const causalGreen = beforeIsUnknown && staleNameWasRepairable;

console.log(
  `M1_CAUSAL_${causalGreen ? 'GREEN' : 'RED'} `
  + `platform=jitsi signal=exclusive-trailing onset_to_identity_start_ms=${correctStart - onset} `
  + `before=${beforeAdvance.speakerName}/${beforeAdvance.source}/${beforeAdvance.confidence.toFixed(3)} `
  + `after=${afterAdvance.speakerName}/${afterAdvance.source}/${afterAdvance.confidence.toFixed(3)} `
  + `expected_before=unknown expected_after_not=Anna `
  + `null_control=unknown without_heartbeat=${withoutHeartbeat.speakerName}`,
);

if (!causalGreen) {
  console.error(
    'Causal violation: a repeated outgoing dominant-speaker heartbeat named a later acoustic '
    + 'turn before the exclusive signal advanced, and the stale name remained authoritative.',
  );
}
process.exit(causalGreen ? 0 : 1);
