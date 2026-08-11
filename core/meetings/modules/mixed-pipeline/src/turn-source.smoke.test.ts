/**
 * turn-source — the transport spine's state machine, driven directly.
 *
 * Everything here is about the three shapes the audible set can take and the cuts between them.
 * The one that matters most is the middle one: two sources audible at once is a span NOBODY can be
 * named for, and a spine that quietly handed it to one of them would be inventing attribution out
 * of a genuine ambiguity. The test therefore asserts the contested turn EXISTS as its own span,
 * not merely that nothing crashed.
 *
 * Run: npx tsx src/turn-source.smoke.test.ts
 */
import { CsrcTurnSource, PyannoteTurnSource, type TurnClosedEvent, type TurnOpenedEvent } from './turn-source.js';

let failed = 0;
const check = (name: string, cond: boolean, detail?: string): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond || !detail ? '' : ` — ${detail}`}`);
  if (!cond) failed++;
};

const silence = (ms: number): Float32Array => new Float32Array(Math.round(16 * ms));
const speech = (ms: number): Float32Array => {
  const a = new Float32Array(Math.round(16 * ms));
  for (let i = 0; i < a.length; i++) a[i] = Math.sin(i / 8) * 0.3;
  return a;
};

// ── 1) One source: a turn opens on the activation and closes on the deactivation + hysteresis ────
{
  const opened: TurnOpenedEvent[] = [];
  const closed: TurnClosedEvent[] = [];
  const src = new CsrcTurnSource({ turnOpened: (e) => opened.push(e), turnClosed: (e) => closed.push(e) }, { hysteresisMs: 600 });
  src.onTransportEvent({ csrc: 7, active: true, tMs: 1000 });
  check('a turn opens on the activation, carrying the transport id',
    opened.length === 1 && opened[0].t0 === 1000 && opened[0].trackId === '7', JSON.stringify(opened));
  src.onTransportEvent({ csrc: 7, active: false, tMs: 5000 });
  check('the deactivation alone does not close it — the hysteresis is still running',
    closed.length === 0, JSON.stringify(closed));
  // A reactivation inside the window is the SAME turn continuing: a speaker's packet train has
  // gaps (DTX, jitter), and cutting on each one would shatter a sentence into unusable slivers.
  src.onTransportEvent({ csrc: 7, active: true, tMs: 5300 });
  src.onTransportEvent({ csrc: 7, active: false, tMs: 8000 });
  check('a reactivation inside the hysteresis continues the same turn (no cut)',
    closed.length === 0 && opened.length === 1, `${JSON.stringify(opened)} / ${JSON.stringify(closed)}`);
  // Audio carries the clock forward: the close does not need another transition to happen.
  src.onAudio(silence(1000), 8700);
  check('the turn closes once the hysteresis elapses, AT the instant the source went quiet',
    closed.length === 1 && closed[0].t1 === 8000 && closed[0].trackId === '7' && closed[0].reason === 'transport-inactive',
    JSON.stringify(closed));
}

// ── 2) Two sources: the overlap is its own CONTESTED span, and it is nobody's ────────────────────
{
  const opened: TurnOpenedEvent[] = [];
  const closed: TurnClosedEvent[] = [];
  const src = new CsrcTurnSource({ turnOpened: (e) => opened.push(e), turnClosed: (e) => closed.push(e) }, { hysteresisMs: 400 });
  src.onTransportEvent({ csrc: 1, active: true, tMs: 0 });
  src.onTransportEvent({ csrc: 2, active: true, tMs: 2000 });   // crosstalk begins
  check('the mono turn is CUT where the second voice arrives, keeping its own name',
    closed.length === 1 && closed[0].trackId === '1' && closed[0].t1 === 2000, JSON.stringify(closed));
  check('the overlap opens a CONTESTED turn carrying NO track (the mix cannot be split)',
    opened.length === 2 && opened[1].contested === true && opened[1].trackId === undefined && opened[1].t0 === 2000,
    JSON.stringify(opened));
  src.onTransportEvent({ csrc: 2, active: false, tMs: 4000 });
  src.onAudio(silence(500), 4500);
  check('when one voice drops out, the contested span ends and the survivor gets a clean turn',
    closed.length === 2 && closed[1].contested === true && opened.length === 3 && opened[2].trackId === '1',
    `${JSON.stringify(closed)} / ${JSON.stringify(opened)}`);
  src.flush(9000);
  check('flush closes what is still open (a meeting that ends mid-turn keeps its last words)',
    closed.length === 3 && closed[2].reason === 'dispose', JSON.stringify(closed));
  const h = src.health();
  check('health counts the transitions, the tracks and the contested spans',
    h.transitions === 3 && h.tracks === 2 && h.contested === 1, JSON.stringify(h));
}

// ── 3) The open turn's live edge stops where the transport stopped ───────────────────────────────
{
  const grown: number[] = [];
  const src = new CsrcTurnSource(
    { turnOpened: () => { /* n/a */ }, turnGrown: (e) => grown.push(e.tMs), turnClosed: () => { /* n/a */ } },
    { hysteresisMs: 600 },
  );
  src.onTransportEvent({ csrc: 3, active: true, tMs: 0 });
  src.onAudio(speech(100), 0);
  check('while the source is audible the edge follows the audio', grown[grown.length - 1] === 100, JSON.stringify(grown));
  src.onTransportEvent({ csrc: 3, active: false, tMs: 150 });
  src.onAudio(speech(100), 100);
  check('once it goes quiet the edge STOPS at that instant — the next audio may be someone else',
    grown[grown.length - 1] === 150, JSON.stringify(grown));
}

// ── 4) Liveness: energetic audio with no transitions at all is a DEAD transport ──────────────────
{
  const dead: string[] = [];
  const src = new CsrcTurnSource(
    { turnOpened: () => { /* n/a */ }, turnClosed: () => { /* n/a */ } },
    { hysteresisMs: 600, onDead: (i) => dead.push(i.reason) },
  );
  src.onTransportEvent({ csrc: 9, active: true, tMs: 0 });
  for (let i = 0; i < 500; i++) src.onAudio(silence(100), i * 100);   // 50s of SILENCE
  check('a quiet room is not a dead transport (no false fallback in a pause)', dead.length === 0, JSON.stringify(dead));
  for (let i = 0; i < 500; i++) src.onAudio(speech(100), 50_000 + i * 100);   // 50s of speech, still nothing
  check('50s of energetic audio with zero transitions fires the fallback, once',
    dead.length === 1 && dead[0] === 'transport-silent', JSON.stringify(dead));
}

// ── 5) The pyannote wrapper maps boundaries exactly as the transcriber always did ────────────────
{
  const seq: string[] = [];
  let fire!: (ev: { tMs: number; kind: string; confidence: number }) => void;
  await PyannoteTurnSource.create(
    { turnOpened: (e) => seq.push(`open@${e.t0}${e.trackId ? `:${e.trackId}` : ''}`), turnClosed: (e) => seq.push(`close@${e.t1}:${e.reason}`) },
    async (onBoundary) => { fire = onBoundary as never; return { appendFrame: async () => { /* injected */ }, reset: () => { /* n/a */ } }; },
  );
  fire({ tMs: 100, kind: 'silence→speaker', confidence: 1 });
  fire({ tMs: 200, kind: 'speaker→speaker', confidence: 1 });
  fire({ tMs: 300, kind: 'speaker→silence', confidence: 1 });
  check('silence→speaker opens; speaker→speaker closes AND reopens; speaker→silence closes',
    seq.join(' ') === 'open@100 close@200:speaker-change open@200 close@300:silence', seq.join(' '));
  check('a pyannote turn NEVER carries a track id — it has no identity to carry',
    seq.every((s) => !s.includes(':') || s.startsWith('close')), seq.join(' '));
}

if (failed) { console.error(`\n❌ turn-source: ${failed} check(s) FAILED.`); process.exit(1); }
console.log('\n✅ turn-source: the transport spine cuts on observed edges, holds a speaker through their own gaps, and gives crosstalk its own unattributable span.');
