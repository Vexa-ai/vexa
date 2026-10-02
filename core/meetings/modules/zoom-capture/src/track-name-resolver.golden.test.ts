/**
 * track-name-resolver L2 goldens — the PURE channel↔name correlation, no DOM and no audio.
 *
 * This algorithm shipped as an object literal inside a `page.evaluate` string, where it could not be
 * imported, tested, diffed against its two siblings, or replayed. These are the properties its author
 * says he validated offline, now pinned in the repo, plus the two capabilities the existing namers
 * earned from live incidents and this one was missing:
 *
 *   1. VOTE + MARGIN     — a stray sticky-DOM sample never binds, and never churns a settled name.
 *   2. 1:1 BY IDENTITY   — a name that an ACTIVE channel holds cannot be pasted onto another channel.
 *   3. PURITY THRESHOLD  — …unless this channel's votes for it are overwhelmingly its own, which is
 *                          how two genuinely same-named people differ from contamination.
 *   4. IDLE RELEASE      — a name whose owner went long-idle is available again (leave → rejoin).
 *   5. SELF-EXCLUSION    — the local participant never binds a remote channel, and a self bind that
 *                          slipped in before the name was known is PURGED when it becomes known.
 *                          (GmeetChannelBinder earned this from the speaker-bots eval, 2026-06-20.)
 *   6. SPEAKER A/B/C     — an unbound channel is separated-but-unnamed, not anonymous: it gets a
 *                          stable letter over the UNNAMED pool, and the letters close up when a
 *                          channel earns a real name. (TrackNamer.labelFor, same reason.)
 *
 * Run: npx tsx src/track-name-resolver.golden.test.ts   (the package's `npm test` chains all)
 */
import { createTrackNameResolver, speakerLabel, TRACK_NAME_DEFAULTS } from './track-name-resolver.js';
import type { TrackNameResolver } from './track-name-resolver.js';

let failed = 0;
const check = (name: string, cond: boolean, detail?: string) => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond || !detail ? '' : ` — ${detail}`}`);
  if (!cond) failed++;
};

/**
 * Cast `n` clean votes for (channel ← name) starting at `t0`: the DOM lights exactly `name` while
 * `channel` carries the loudest energetic frames. Returns a time past the hot window, so the NEXT
 * burst is unambiguously the loudest one (two channels hot at equal energy is a tie, and a tie is
 * deliberately not a clean moment — nobody votes).
 */
function votes(r: TrackNameResolver, ch: number, name: string, n: number, t0: number, energy = 0.5): number {
  let t = t0;
  for (let i = 0; i < n; i++) {
    t = t0 + i * 10;
    r.onSpeak(name, t, false);
    r.markHot(ch, t, energy);
    r.resolve(ch, t);
  }
  return t + TRACK_NAME_DEFAULTS.hotMs + 100;
}

// ── 1. VOTE + MARGIN: self-correcting, and stable once settled ────────────────────────────────
{
  const r = createTrackNameResolver();
  // The sticky spotlight names Justin for one clean moment at the top of Scott's turn.
  let t = votes(r, 0, 'Justin', 1, 1_000);
  check('one stray vote does not bind (below the margin)', r.nameFor(0) === undefined, String(r.nameFor(0)));
  // …then the true speaker accumulates, and outweighs it. No permanent early lock-in.
  t = votes(r, 0, 'Scott', 5, t);
  check('the true speaker outweighs a wrong early sample → binds Scott', r.nameFor(0) === 'Scott', String(r.nameFor(0)));
  // A couple of stray Justin votes must not churn the settled binding (needs to LEAD by margin).
  votes(r, 0, 'Justin', 2, t);
  check('stray votes below margin do not churn a settled name', r.nameFor(0) === 'Scott', String(r.nameFor(0)));
}

// ── 2. 1:1 BY IDENTITY: an active channel's name cannot be stolen ──────────────────────────────
{
  const r = createTrackNameResolver();
  let t = votes(r, 0, 'Justin', 6, 1_000);           // ch0 IS Justin, and stays active
  // ch1 (Scott) is contaminated: the sticky DOM keeps holding "Justin" through part of Scott's turn.
  // 7 own votes + 3 contaminated = 30% Justin — a mixed minority, below the purity floor.
  t = votes(r, 1, 'Scott', 7, t);
  votes(r, 1, 'Justin', 3, t);
  check("a contaminated minority never steals an active channel's name", r.nameFor(1) === 'Scott', String(r.nameFor(1)));
  check('the rightful owner keeps its name', r.nameFor(0) === 'Justin', String(r.nameFor(0)));
}

// ── 3. PURITY THRESHOLD: two genuinely same-named people co-hold the name ──────────────────────
{
  const r = createTrackNameResolver();
  const t = votes(r, 0, 'John Smith', 6, 1_000);     // the first John Smith, still active
  // The second John Smith's channel votes ONLY for that name — purity 1.0, well over the 0.7 floor.
  votes(r, 1, 'John Smith', 5, t);
  check('purity ≥ threshold → a second same-named speaker also earns the name',
    r.nameFor(1) === 'John Smith', String(r.nameFor(1)));
  check('…and the first keeps it (co-held, not transferred)', r.nameFor(0) === 'John Smith', String(r.nameFor(0)));
  check('the purity floor under test is the documented default', TRACK_NAME_DEFAULTS.purity === 0.7);
}

// ── 4. IDLE RELEASE: leave, then rejoin under a new stream ─────────────────────────────────────
{
  const r = createTrackNameResolver();
  const t = votes(r, 0, 'Anna', 6, 1_000);
  check('ch0 binds Anna', r.nameFor(0) === 'Anna');
  // Anna drops and rejoins; the new stream is a new channel. ch0 has been idle far past the release.
  votes(r, 1, 'Anna', 5, t + TRACK_NAME_DEFAULTS.idleReleaseMs + 1_000);
  check('an idle owner releases its name to the rejoined stream', r.nameFor(1) === 'Anna', String(r.nameFor(1)));
  check('…and the dead channel no longer claims it', r.nameFor(0) === undefined, String(r.nameFor(0)));
}

// ── 5. SELF-EXCLUSION: the backstop, in both directions ────────────────────────────────────────
{
  // 5a — the hazard, documented: with no self name known, our own tile can win a remote channel.
  const leaky = createTrackNameResolver();
  votes(leaky, 0, 'Vexa Bot', 6, 1_000);
  check('pre-fix: the self transiently bound a remote channel', leaky.nameFor(0) === 'Vexa Bot');
  // …then the self name becomes known (the watcher's marker renders late) → the bind is PURGED.
  leaky.setSelfName('Vexa Bot');
  check('setSelfName purges the self bind → unbound', leaky.nameFor(0) === undefined, String(leaky.nameFor(0)));
  const t = votes(leaky, 0, 'Anna', 6, 5_000);
  check('after the purge a real remote binds normally', leaky.nameFor(0) === 'Anna', String(leaky.nameFor(0)));

  // 5b — with the self known up front it never enters the vote at all, even lit alongside a remote.
  const guarded = createTrackNameResolver({ selfName: 'Vexa Bot' });
  votes(guarded, 0, 'Vexa Bot', 8, t);
  check('a known self never binds a remote channel', guarded.nameFor(0) === undefined, String(guarded.nameFor(0)));
  votes(guarded, 0, 'Anna', 5, t + 2_000);
  check('…and the channel is still free for its real owner', guarded.nameFor(0) === 'Anna', String(guarded.nameFor(0)));
}

// ── 6. SPEAKER A/B/C: unbound is separated-but-unnamed, never anonymous ────────────────────────
{
  const r = createTrackNameResolver();
  // Three channels heard, nobody lit → no votes cast, so all three stay unbound.
  r.markHot(0, 1_000, 0.5);
  r.markHot(1, 1_100, 0.5);
  r.markHot(2, 1_200, 0.5);
  check('unbound channels get stable letters in first-heard order',
    r.labelFor(0) === 'Speaker A' && r.labelFor(1) === 'Speaker B' && r.labelFor(2) === 'Speaker C',
    [r.labelFor(0), r.labelFor(1), r.labelFor(2)].join(' · '));
  check('the same channel keeps its letter across calls', r.labelFor(1) === 'Speaker B');

  // ch0 earns a real name → it leaves the unnamed pool and the letters CLOSE UP behind it, so the
  // transcript never shows a "Speaker B" with no Speaker A anywhere in it.
  votes(r, 0, 'Scott', 6, 2_000);
  check('a named channel publishes its name, not a letter', r.labelFor(0) === 'Scott', r.labelFor(0));
  check('the letters close up over the remaining unnamed channels',
    r.labelFor(1) === 'Speaker A' && r.labelFor(2) === 'Speaker B',
    [r.labelFor(1), r.labelFor(2)].join(' · '));

  // A channel first seen at labelFor time is registered there (a late joiner asked about directly).
  check('a newly seen channel takes the next free letter', r.labelFor(7) === 'Speaker C', r.labelFor(7));
  check('first-heard order is what the letters run over', JSON.stringify(r.channels()) === '[0,1,2,7]',
    JSON.stringify(r.channels()));
  check('letters carry past Z', speakerLabel(0) === 'Speaker A' && speakerLabel(25) === 'Speaker Z'
    && speakerLabel(26) === 'Speaker AA');
}

// ── 7. Purity: the resolver holds no state outside itself ──────────────────────────────────────
{
  const a = createTrackNameResolver();
  const b = createTrackNameResolver();
  votes(a, 0, 'Anna', 6, 1_000);
  check('two resolvers do not share state', a.nameFor(0) === 'Anna' && b.nameFor(0) === undefined);
  check('bindings() reports what the host observes', JSON.stringify(a.bindings()) === '[{"channel":0,"name":"Anna"}]',
    JSON.stringify(a.bindings()));
}

console.log(failed ? `\n❌ track-name-resolver: ${failed} failed` : '\n✅ track-name-resolver: all goldens passed');
process.exit(failed ? 1 : 0);
