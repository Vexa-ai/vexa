/**
 * Golden: per-channel ENERGY ↔ GLOW correlation. A channel binds to the tile whose
 * glow tracks its audio energy over a short window — so overlapping speakers on
 * separate channels get their OWN names. Run: npx tsx modules/capture/test/channel-binder.test.ts
 */
import assert from 'node:assert';
import { GmeetChannelBinder } from '../src/gmeet-channel-binder';

let pass = 0;
const ok = (name: string, fn: () => void) => { fn(); pass++; console.log(`  ✅ ${name}`); };
const mk = () => new GmeetChannelBinder({ tauMs: 2500, loudThreshold: 0.02, minScore: 2.5 });

type Frame = { ch: number; t: number; e: number };
type Glow = { n: string; t: number; end?: boolean };
function run(b: GmeetChannelBinder, frames: Frame[], glows: Glow[]): Map<number, string | undefined> {
  const evs = [...frames.map((f) => ({ t: f.t, f })), ...glows.map((g) => ({ t: g.t, g }))]
    .sort((a, b2) => a.t - b2.t);
  const last = new Map<number, string | undefined>();
  for (const e of evs as any[]) {
    if (e.g) b.recordGlow(e.g.n, !!e.g.end, e.g.t);
    else last.set(e.f.ch, b.nameForChannel(e.f.ch, e.f.t, e.f.e));
  }
  return last;
}
const loud = (ch: number, from: number, to: number): Frame[] => {
  const o: Frame[] = []; for (let t = from; t <= to; t += 256) o.push({ ch, t, e: 0.1 }); return o;
};
const quiet = (ch: number, from: number, to: number): Frame[] => {
  const o: Frame[] = []; for (let t = from; t <= to; t += 256) o.push({ ch, t, e: 0.001 }); return o;
};

// 1. one speaker — energy tracks the glow → bind
ok('one channel binds to the tile whose glow tracks its energy', () => {
  const last = run(mk(), loud(0, 1000, 3000), [{ n: 'Анна', t: 1000 }]);
  assert.equal(last.get(0), 'Анна');
});

// 2. THE headline — overlap, but each channel's energy tracks its OWN glow (solo moments break the tie)
ok('OVERLAP: energy correlation gives each channel its own speaker (no leak)', () => {
  const last = run(mk(),
    [...loud(0, 1000, 4000), ...loud(1, 2500, 5500), ...quiet(0, 4100, 5500)],  // A solo, overlap, B solo
    [{ n: 'Анна', t: 1000 }, { n: 'Анна', t: 4000, end: true },
     { n: 'Борис', t: 2500 }]);
  assert.equal(last.get(0), 'Анна', 'ch0 = Анна (its energy tracked Анна)');
  assert.equal(last.get(1), 'Борис', 'ch1 = Борис (not the overlapping Анна)');
});

// 3. confidence floor — a sliver of agreement stays UNKNOWN (never a guess)
ok('below the confidence floor ⇒ UNKNOWN', () => {
  const last = run(mk(), [{ ch: 0, t: 1000, e: 0.1 }], [{ n: 'Анна', t: 1000 }]);  // one loud frame only
  assert.equal(last.get(0), undefined);
});

// 4. a quiet channel never accrues agreement
ok('a quiet channel (below loud threshold) stays UNKNOWN even while a tile glows', () => {
  const last = run(mk(), quiet(0, 1000, 3000), [{ n: 'Анна', t: 1000 }]);
  assert.equal(last.get(0), undefined);
});

// 5. one tile ↔ one channel — the tile goes to the channel that correlates with it more
ok('a tile is claimed by the channel that correlates with it most', () => {
  // both channels loud while only Анна glows, but ch0 has a longer head start
  const last = run(mk(),
    [...loud(0, 1000, 4000), ...loud(1, 3000, 4000)],
    [{ n: 'Анна', t: 1000 }]);
  assert.equal(last.get(0), 'Анна', 'ch0 wins Анна (stronger correlation)');
  assert.equal(last.get(1), undefined, 'ch1 cannot also claim Анна');
});

// 6. rotation — old correlation decays, channel re-binds to the next speaker
ok('after a speaker change the channel re-binds (old correlation decays)', () => {
  const last = run(mk(),
    [...loud(0, 1000, 3000), ...loud(0, 5500, 7500)],
    [{ n: 'Анна', t: 1000 }, { n: 'Анна', t: 3000, end: true }, { n: 'Борис', t: 5500 }]);
  assert.equal(last.get(0), 'Борис', 'rebound to Борис, not stuck on the decayed Анна');
});

console.log(`\n✅ channel-binder golden: ${pass} checks passed`);
