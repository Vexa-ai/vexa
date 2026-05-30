/**
 * TeamsAttributor unit tests. Runs under tsx — no test framework needed.
 *   npx tsx src/services/teams-attributor.test.ts
 *
 * Tests the four cases the pack epic synthetic-gate calls out:
 *  1. clean window-match
 *  2. cluster-vote fallback when no caption overlaps
 *  3. late-caption rename trigger
 *  4. multi-speaker overlap: dominant-time-coverage wins
 */
import { TeamsAttributor } from './teams-attributor';

let passed = 0;
let failed = 0;

function assert(cond: boolean, msg: string): void {
  if (cond) {
    console.log(`  ✓ ${msg}`);
    passed++;
  } else {
    console.error(`  ✗ ${msg}`);
    failed++;
  }
}

function eq<T>(actual: T, expected: T, msg: string): void {
  if (actual === expected) {
    console.log(`  ✓ ${msg} (= ${JSON.stringify(actual)})`);
    passed++;
  } else {
    console.error(`  ✗ ${msg} — expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
    failed++;
  }
}

function close(actual: number, expected: number, tolerance: number, msg: string): void {
  if (Math.abs(actual - expected) <= tolerance) {
    console.log(`  ✓ ${msg} (= ${actual.toFixed(3)})`);
    passed++;
  } else {
    console.error(`  ✗ ${msg} — expected ~${expected} (±${tolerance}), got ${actual}`);
    failed++;
  }
}

// ─────────────────────────────────────────────────────────────────────
console.log('test 1: clean window-match — caption inside commit window');
{
  // Caption lag default 1000ms, so caption tMs=11000 represents speech at audio ts ~10000.
  const attr = new TeamsAttributor();
  attr.recordCaption('Alice', 11000);
  attr.recordCaption('Bob',   18000);
  const result = attr.resolve({ clusterId: 'speaker_0', tStartMs: 9000, tEndMs: 13000 });
  eq(result.speakerName, 'Alice', 'commit at [9,13] resolves to Alice');
  eq(result.source, 'window-match', 'source=window-match');
  close(result.confidence, 1.0, 0.5, 'high confidence');
}

// ─────────────────────────────────────────────────────────────────────
console.log('\ntest 2: cluster-vote fallback — second commit on same cluster, no nearby caption');
{
  const attr = new TeamsAttributor();
  // First commit: Alice resolves via window-match.
  attr.recordCaption('Alice', 11000);
  const r1 = attr.resolve({ clusterId: 'speaker_0', tStartMs: 9000, tEndMs: 13000 });
  eq(r1.speakerName, 'Alice', 'first commit on speaker_0 resolves to Alice');
  eq(r1.source, 'window-match', 'first commit source=window-match');
  // Second commit on same cluster, but NO caption nearby (~50s later).
  const r2 = attr.resolve({ clusterId: 'speaker_0', tStartMs: 60000, tEndMs: 64000 });
  eq(r2.speakerName, 'Alice', 'second commit on speaker_0 still resolves to Alice (cluster vote)');
  eq(r2.source, 'cluster-vote', 'second commit source=cluster-vote');
}

// ─────────────────────────────────────────────────────────────────────
console.log('\ntest 3: late-caption rename — provisional cluster ID, then caption arrives');
{
  const renames: Array<{ cluster: string; name: string }> = [];
  const attr = new TeamsAttributor({
    onLateResolve: (cluster, name) => { renames.push({ cluster, name }); },
  });
  // Commit fires BEFORE any caption.
  const r1 = attr.resolve({ clusterId: 'speaker_3', tStartMs: 5000, tEndMs: 9000 });
  eq(r1.speakerName, 'speaker_3', 'provisional commit publishes with cluster_id');
  eq(r1.source, 'provisional-cluster-id', 'source=provisional-cluster-id');
  assert(renames.length === 0, 'no rename fired yet');
  // Caption arrives later, then a SECOND commit on speaker_3 fires.
  // The second commit's resolve() will window-match the caption AND
  // detect via clusterLastResolvedName that the cluster was previously
  // emitted as `speaker_3`, firing onLateResolve.
  attr.recordCaption('Bob', 6200); // shifted +lag(1000) puts it in the window of [5000,9000]
  const r2 = attr.resolve({ clusterId: 'speaker_3', tStartMs: 5000, tEndMs: 9000 });
  eq(r2.speakerName, 'Bob', 'after caption arrives, second commit resolves to Bob');
  eq(r2.source, 'window-match', 'source=window-match');
  assert(renames.length === 1, 'onLateResolve fired once');
  eq(renames[0]?.cluster, 'speaker_3', 'rename cluster=speaker_3');
  eq(renames[0]?.name, 'Bob', 'rename name=Bob');
}

// ─────────────────────────────────────────────────────────────────────
console.log('\ntest 4: multi-speaker overlap — dominant time-coverage wins');
{
  // Tighter tolerance so the test directly measures algorithm intent
  // without the search-window slack pulling in too much neighbor audio.
  const attr = new TeamsAttributor({ matchToleranceMs: 200 });
  // Alice speaks for 8s, Bob takes over for the last 1s within the commit.
  attr.recordCaption('Alice', 11000); // active 11000 → 19000 (next caption)
  attr.recordCaption('Bob',   19000); // active 19000+
  // Commit [10000, 19000]; lag-shifted [11000, 20000]; tolerance ±200ms
  //   → search window [10800, 20200]
  // Alice overlap: [11000, 19000] ∩ search = 8000ms
  // Bob overlap:   [19000, 20200] ∩ search = 1200ms
  const result = attr.resolve({ clusterId: 'speaker_0', tStartMs: 10000, tEndMs: 19000 });
  eq(result.speakerName, 'Alice', 'Alice wins (more overlap time)');
  eq(result.source, 'window-match', 'source=window-match');
  // ratio: alice 8000 / (8000+1200) ≈ 0.87
  assert(result.confidence >= 0.8, `confidence ≥ 0.8 (got ${result.confidence.toFixed(3)})`);
}

// ─────────────────────────────────────────────────────────────────────
console.log('\ntest 5: reset clears state');
{
  const attr = new TeamsAttributor();
  attr.recordCaption('Alice', 1000);
  attr.resolve({ clusterId: 'speaker_0', tStartMs: 0, tEndMs: 2000 });
  assert(attr.captionCount() === 1, 'caption logged');
  assert(attr.clusterCount() === 1, 'cluster tallied');
  attr.reset();
  assert(attr.captionCount() === 0, 'caption log cleared');
  assert(attr.clusterCount() === 0, 'cluster vote cleared');
}

// ─────────────────────────────────────────────────────────────────────
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
