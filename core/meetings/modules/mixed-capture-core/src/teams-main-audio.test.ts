/**
 * teams-main-audio — the Teams mix selector, including the failure the fix itself can cause.
 *
 * Preferring the `mainAudio` track fixes double-fed audio (repeated words). But the selector is a
 * STRING MATCH on a vendor-generated track id, so the interesting cases are not "does it pick the
 * mix" — they are what happens when the prefix is not there: silence forever, or fall back and say
 * so. These pin the second.
 *
 * Run: npx tsx src/teams-main-audio.test.ts
 */
import { selectTeamsMixStreams, mainAudioProvedSilent, type StreamLike } from './teams-main-audio.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};
const stream = (...ids: string[]): StreamLike => ({ getAudioTracks: () => ids.map((id) => ({ id })) });

// ── 1. The fix itself: the mix is present alongside the redundant track → mix ALONE. ──
{
  const mix = stream('mainAudio-0');
  const redundant = stream('dominantSpeaker-7');
  const r = selectTeamsMixStreams([redundant, mix], { firstMissMs: null, nowMs: 1_000_000 });
  check('the server mix alone is captured when it exists (no double-feed)',
    r.outcome === 'main-audio' && r.streams.length === 1 && r.streams[0] === mix,
    `${r.outcome} n=${r.streams.length}`);
  check('no observation is emitted on the healthy path', !r.observation, JSON.stringify(r.observation));
}

// ── 2. Case matters not: Teams' casing is not a contract. ──
{
  const r = selectTeamsMixStreams([stream('MainAudio-abc')], { firstMissMs: null, nowMs: 1_000_000 });
  check('the prefix match is case-insensitive', r.outcome === 'main-audio', r.outcome);
}

// ── 3. Inside the grace window the mix may simply not have arrived — wait, quietly. ──
{
  const r = selectTeamsMixStreams([stream('abc-1'), stream('abc-2')],
    { firstMissMs: 1_000_000, nowMs: 1_005_000, graceMs: 15_000 });
  check('within grace: capture nothing yet, stay silent', r.outcome === 'waiting' && r.streams.length === 0 && !r.observation,
    `${r.outcome} n=${r.streams.length}`);
}

// ── 4. THE BLOCKER. Past grace with no mix, the bot must NOT sit recording silence for the whole
// meeting. Fall back to every track — the pre-fix behaviour — and report it. ──
{
  const a = stream('abc-1'), b = stream('abc-2');
  const r = selectTeamsMixStreams([a, b], { firstMissMs: 1_000_000, nowMs: 1_020_000, graceMs: 15_000 });
  check('past grace: FAILS OPEN to every track rather than capturing silence',
    r.outcome === 'fallback-all' && r.streams.length === 2, `${r.outcome} n=${r.streams.length}`);
  check('the fallback is reported, naming the condition', r.observation?.kind === 'main-audio-absent', JSON.stringify(r.observation));
  check('the report carries the ids actually seen (a rotted prefix is diagnosable from the log)',
    JSON.stringify(r.observation?.trackIds) === JSON.stringify(['abc-1', 'abc-2']), JSON.stringify(r.observation?.trackIds));
  check('the report says what it did', /fail-open/.test(r.observation?.action || ''), r.observation?.action);
}

// ── 5. NO LATCH. The old warning fired once, so a permanently-degraded capture looked healthy
// after its first second. Every falling-back rescan must re-report. ──
{
  const s = [stream('abc-1')];
  const rescans = [1_020_000, 1_022_000, 1_024_000, 1_026_000]
    .map((now) => selectTeamsMixStreams(s, { firstMissMs: 1_000_000, nowMs: now, graceMs: 15_000 }));
  check('every falling-back rescan re-emits (no one-shot latch)',
    rescans.every((r) => !!r.observation), `${rescans.filter((r) => !!r.observation).length}/4`);
  check('the report shows the wait growing, so staleness is visible',
    (rescans[3].observation?.waitedMs ?? 0) > (rescans[0].observation?.waitedMs ?? 0),
    `${rescans[0].observation?.waitedMs} → ${rescans[3].observation?.waitedMs}`);
}

// ── 6. Recovery: the mix appearing later takes over, and the noise stops. ──
{
  const r = selectTeamsMixStreams([stream('abc-1'), stream('mainAudio-9')],
    { firstMissMs: 1_000_000, nowMs: 1_030_000, graceMs: 15_000 });
  check('a late-arriving mix takes over and silences the report',
    r.outcome === 'main-audio' && r.streams.length === 1 && !r.observation, r.outcome);
}

// ── 7. Non-Teams platforms never reach this selector, but an empty list must not throw. ──
{
  const r = selectTeamsMixStreams([], { firstMissMs: null, nowMs: 1_000_000 });
  check('no streams at all is handled without throwing', r.streams.length === 0, r.outcome);
}

// ── 8. PRESENCE IS NOT LIVENESS. The mix appeared, was captured, and carried pure silence.
// Observed live on staging 2026-08-11: three remote tracks mirrored, the mainAudio pick connected,
// and not one word ever transcribed — no boundaries, no turns, every speaker hint missed. ──
{
  check('a mix that has never been captured is NOT yet silent (no premature abandon)',
    mainAudioProvedSilent({ captureStartedMs: null, energeticMs: 0, nowMs: 1_000_000 }) === false);
  check('inside the silence window it is given the benefit of the doubt',
    mainAudioProvedSilent({ captureStartedMs: 1_000_000, energeticMs: 0, nowMs: 1_010_000, silenceMs: 20_000 }) === false);
  check('a mix that carried ANY energy is never abandoned, however quiet the room since',
    mainAudioProvedSilent({ captureStartedMs: 1_000_000, energeticMs: 40, nowMs: 9_000_000, silenceMs: 20_000 }) === false);
  check('captured past the window with zero energy is proved dead',
    mainAudioProvedSilent({ captureStartedMs: 1_000_000, energeticMs: 0, nowMs: 1_020_000, silenceMs: 20_000 }) === true);
}

// ── 9. The verdict changes the pick: a PRESENT but silent mix falls back to every track. ──
{
  const all = [stream('abc-1'), stream('mainAudio-0'), stream('def-2')];
  const healthy = selectTeamsMixStreams(all, { firstMissMs: null, nowMs: 1_000_000 });
  check('while the mix looks alive it is still preferred alone',
    healthy.outcome === 'main-audio' && healthy.streams.length === 1, healthy.outcome);

  const dead = selectTeamsMixStreams(all,
    { firstMissMs: null, nowMs: 1_000_000, mainAudioSilent: true, mainAudioCapturedMs: 21_000 });
  check('a mix proved silent is abandoned for ALL tracks',
    dead.outcome === 'fallback-all' && dead.streams.length === 3, `${dead.outcome}/${dead.streams.length}`);
  check('and it says why, naming the tracks so the pick is diagnosable',
    dead.observation?.kind === 'main-audio-silent' && (dead.observation as any).capturedMs === 21_000
      && (dead.observation?.trackIds || []).includes('mainAudio-0'),
    JSON.stringify(dead.observation));

  const rescans = [1_000_000, 1_002_000, 1_004_000].map((now) =>
    selectTeamsMixStreams(all, { firstMissMs: null, nowMs: now, mainAudioSilent: true, mainAudioCapturedMs: 21_000 }));
  check('the silent fallback re-reports on every rescan too (no one-shot latch)',
    rescans.every((r) => r.observation?.kind === 'main-audio-silent'));
}

if (failed) { console.error(`\n❌ teams-main-audio: ${failed} check(s) FAILED.`); process.exit(1); }
console.log('\n✅ teams-main-audio: the mix wins when present; when the selector finds nothing it fails OPEN and keeps saying so.');
