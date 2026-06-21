/**
 * gmeet-channel-binder L2 — the PURE energy↔glow correlation, no DOM/audio. Pins:
 *  - a channel binds to the tile whose GLOW tracks its ENERGY (active-speaker corr),
 *  - 1 tile ↔ 1 channel (the stronger correlator wins a contested tile),
 *  - sub-confidence stays UNKNOWN (leak-free, never a guess), and
 *  - THE SELF/HOST TILE NEVER BINDS A REMOTE CHANNEL — even when it glows
 *    concurrently with a remote speaker (the live regression: a host talking on the
 *    mic during a remote turn leaked "Dmitriy Grankin" onto the remote's channel,
 *    found by the speaker-bots eval 2026-06-20).
 * Run: npx tsx src/gmeet-channel-binder.test.ts   (the package's `npm test` chains all)
 */
import { GmeetChannelBinder } from './gmeet-channel-binder.js';

let failed = 0;
const check = (name: string, cond: boolean) => { console.log(`  ${cond ? '✅' : '❌'} ${name}`); if (!cond) failed++; };

// Drive `frames` of channel energy, one per `stepMs`, with the given glowing set held
// across the whole burst. Returns the last name the channel resolves to.
function burst(b: GmeetChannelBinder, ch: number, glow: string[], frames: number, t0: number, stepMs = 100): string | undefined {
  for (const g of glow) b.recordGlow(g, false, t0);
  let last: string | undefined;
  for (let i = 0; i < frames; i++) last = b.nameForChannel(ch, t0 + i * stepMs, 0.5);
  for (const g of glow) b.recordGlow(g, true, t0 + frames * stepMs);
  return last;
}

// 1) Baseline: a lone glowing tile over a loud channel binds that channel.
{
  const b = new GmeetChannelBinder();
  const name = burst(b, 0, ['Anna'], 8, 1000);
  check('lone glow → channel binds that tile', name === 'Anna');
}

// 2) Sub-confidence stays UNKNOWN (one or two frames is not enough evidence).
{
  const b = new GmeetChannelBinder();
  const name = burst(b, 0, ['Anna'], 1, 1000);
  check('one loud frame → UNKNOWN (below minScore)', name === undefined);
}

// 3) Two channels, two tiles, disjoint glow → each binds its own (no cross-leak).
{
  const b = new GmeetChannelBinder();
  burst(b, 0, ['Anna'], 8, 1000);
  burst(b, 1, ['Zoya'], 8, 3000);
  check('ch0 → Anna', b.nameForChannel(0, 4000, 0.0) === 'Anna');
  check('ch1 → Zoya', b.nameForChannel(1, 4000, 0.0) === 'Zoya');
}

// 4) THE REGRESSION — self/host glows concurrently with a remote speaker over the
//    remote's channel. Without self-awareness the host can win the channel; with the
//    self excluded the channel binds the remote and NEVER the host.
{
  // 4a — document the hazard: both glow, host glow persists a touch longer.
  const leaky = new GmeetChannelBinder();
  for (const g of ['Anna', 'Dmitriy Grankin']) leaky.recordGlow(g, false, 1000);
  for (let i = 0; i < 8; i++) leaky.nameForChannel(0, 1000 + i * 100, 0.5);
  leaky.recordGlow('Anna', true, 1800);                 // Anna pauses; host keeps glowing
  for (let i = 8; i < 16; i++) leaky.nameForChannel(0, 1000 + i * 100, 0.5);
  const leakedTo = leaky.nameForChannel(0, 3000, 0.0);
  check('hazard exists: without self-exclusion a concurrent host CAN bind a remote channel',
        leakedTo === 'Dmitriy Grankin');

  // 4b — the fix: the same timeline, but the binder knows the self.
  const fixed = new GmeetChannelBinder({ selfName: 'Dmitriy Grankin' });
  for (const g of ['Anna', 'Dmitriy Grankin']) fixed.recordGlow(g, false, 1000);
  for (let i = 0; i < 8; i++) fixed.nameForChannel(0, 1000 + i * 100, 0.5);
  fixed.recordGlow('Anna', true, 1800);
  for (let i = 8; i < 16; i++) fixed.nameForChannel(0, 1000 + i * 100, 0.5);
  const bound = fixed.nameForChannel(0, 3000, 0.0);
  check('fix: self excluded → channel binds the REMOTE (Anna), never the host', bound === 'Anna');
  check('fix: channel is never the host name', bound !== 'Dmitriy Grankin');
}

// 5) Sticky self learned LATE (marker rendered late) still purges a prior self bind.
{
  const b = new GmeetChannelBinder();
  // host leaks first (self not yet known)…
  for (const g of ['Dmitriy Grankin']) b.recordGlow(g, false, 1000);
  for (let i = 0; i < 8; i++) b.nameForChannel(0, 1000 + i * 100, 0.5);
  check('pre-fix: host transiently bound', b.nameForChannel(0, 2000, 0.0) === 'Dmitriy Grankin');
  // …then the self name becomes known → its accrued agreement is purged everywhere.
  b.setSelfName('Dmitriy Grankin');
  check('setSelfName purges the self bind → UNKNOWN', b.nameForChannel(0, 2000, 0.0) === undefined);
  // and a real remote can now win the channel cleanly.
  burst(b, 0, ['Anna'], 8, 3000);
  check('after purge a remote binds normally', b.nameForChannel(0, 4000, 0.0) === 'Anna');
}

console.log(failed ? `\n❌ ${failed} failed` : '\n✅ all passed');
process.exit(failed ? 1 : 0);
