/**
 * The bot's speak queue (speak-controller.ts) — L2, fake mic + fake TTS, no browser, no PulseAudio.
 *
 * Pins #1866 C3: one utterance at a time (FIFO), at most MAX_PENDING waiting and the next dropped,
 * speak_stop clears the queue and kills playback, the meeting-UI mic is read before it is clicked
 * (never a blind toggle), an unverifiable mic means no audio, Zoom's mic is left as its join set it,
 * and every line names the utterance's id — never its text.
 * Run: npx tsx src/speak-controller.test.ts
 */
import { createSpeakQueue, micStateFromControl, MAX_PENDING, type MicState, type TtsOutcome } from './speak-controller.js';

let failed = 0;
const check = (name: string, cond: boolean, detail?: string) => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond || !detail ? '' : ` — ${detail}`}`);
  if (!cond) failed++;
};
const tick = () => new Promise<void>((r) => setTimeout(r, 0));

/** A meeting-UI mic: `state` is what the page shows; a click flips it unless `stuck`. */
function fakeMic(initial: MicState = 'off', opts: { stuck?: boolean } = {}) {
  const m = {
    state: initial,
    clicks: 0,
    async read(): Promise<MicState> { return m.state; },
    async toggle(): Promise<void> {
      m.clicks++;
      if (opts.stuck || m.state === 'unknown') return;
      m.state = m.state === 'on' ? 'off' : 'on';
    },
  };
  return m;
}

/** A TTS whose plays are held open until `finish()`, recording overlap. */
function fakeTts(outcome: TtsOutcome = { ok: true, ms: 1200 }) {
  const t = {
    plays: [] as { text: string; voice?: string; micDuring: MicState | null }[],
    active: 0,
    maxActive: 0,
    stops: 0,
    pending: [] as ((o: TtsOutcome) => void)[],
    micRef: null as null | { state: MicState },
    async play(text: string, voice?: string): Promise<TtsOutcome> {
      t.plays.push({ text, voice, micDuring: t.micRef ? t.micRef.state : null });
      t.active++; t.maxActive = Math.max(t.maxActive, t.active);
      const o = await new Promise<TtsOutcome>((r) => t.pending.push(r));
      t.active--;
      return o;
    },
    stop(): void { t.stops++; for (const r of t.pending.splice(0)) r({ ok: false, reason: 'stopped' }); },
    finish(o: TtsOutcome = outcome) { const r = t.pending.shift(); r?.(o); },
  };
  return t;
}

function setup(o: { platform?: string; mic?: ReturnType<typeof fakeMic>; enabled?: boolean } = {}) {
  const lines: string[] = [];
  const mic = o.mic ?? fakeMic('off');
  const tts = fakeTts();
  tts.micRef = mic;
  const speak = createSpeakQueue({
    enabled: o.enabled ?? true, platform: o.platform ?? 'google_meet', mic, tts,
    log: (l) => lines.push(l), settleMs: 0,
  });
  return { speak, mic, tts, lines };
}

// ── serialisation: two acts → two sequential plays, never overlapping ──
{
  const { speak, tts, lines } = setup();
  const a = speak.speak('first sentence', undefined, 'id-a');
  const b = speak.speak('second sentence', undefined, 'id-b');
  await tick(); await tick();
  check('only the first utterance plays while the second waits', tts.plays.length === 1, JSON.stringify(tts.plays));
  tts.finish(); await a; await tick(); await tick();
  check('the second plays after the first finished', tts.plays.length === 2 && tts.plays[1].text === 'second sentence');
  tts.finish(); await b;
  check('no two playbacks ever overlapped', tts.maxActive === 1, `max ${tts.maxActive}`);
  check('started/done lines name each id', ['id-a', 'id-b'].every((id) =>
    lines.some((l) => l === `[bot] speak id=${id} started chars=${(id === 'id-a' ? 'first sentence' : 'second sentence').length}`)
    && lines.some((l) => l.startsWith(`[bot] speak id=${id} done ms=`))), lines.join(' | '));
}

// ── the queue cap: MAX_PENDING wait, the next is dropped (never played late) ──
{
  const { speak, tts, lines } = setup();
  const all = [speak.speak('p', undefined, 'q0')];
  await tick();
  for (let i = 1; i <= MAX_PENDING + 1; i++) all.push(speak.speak(`n${i}`, undefined, `q${i}`));
  await tick();
  check(`MAX_PENDING is 3`, MAX_PENDING === 3);
  check('the utterance past the cap is dropped as queue_full', lines.includes(`[bot] speak dropped id=q${MAX_PENDING + 1} reason=queue_full`), lines.join(' | '));
  for (let i = 0; i <= MAX_PENDING; i++) { tts.finish(); await tick(); await tick(); await tick(); }
  await Promise.all(all);
  check('the dropped utterance was never played', !tts.plays.some((p) => p.text === `n${MAX_PENDING + 1}`) && tts.plays.length === MAX_PENDING + 1, JSON.stringify(tts.plays.map((p) => p.text)));
}

// ── speak_stop clears the queue and kills playback ──
{
  const { speak, tts, mic, lines } = setup();
  const playing = speak.speak('a long paragraph', undefined, 's0');
  const waiting = [speak.speak('w1', undefined, 's1'), speak.speak('w2', undefined, 's2')];
  await tick(); await tick();
  await speak.stop();
  await playing; await Promise.all(waiting); await tick();
  check('stop killed the playback', tts.stops >= 1);
  check('stop reports how many it cleared', lines.includes('[bot] speak_stop cleared=2'), lines.join(' | '));
  check('the playing utterance ends as stopped', lines.includes('[bot] speak id=s0 failed reason=stopped'), lines.join(' | '));
  check('the waiting utterances never play', tts.plays.length === 1);
  check('the meeting-UI mic is off after stop', mic.state === 'off');
}

// ── the disabled gate: no play, a loud line ──
{
  const { speak, tts, mic, lines } = setup({ enabled: false });
  await speak.speak('hello', undefined, 'd1');
  check('a bot without voiceAgentEnabled does not play', tts.plays.length === 0 && mic.clicks === 0);
  check('it says why, with the id', lines.includes('[bot] speak id=d1 failed reason=disabled'), lines.join(' | '));
}

// ── mic driven by state ──
{
  const { speak, tts, mic, lines } = setup({ mic: fakeMic('off') });
  const p = speak.speak('hi', undefined, 'm1');
  await tick(); await tick();
  check('an off mic is switched on before audio plays', tts.plays[0]?.micDuring === 'on', JSON.stringify(tts.plays));
  tts.finish(); await p;
  check('and switched off again after', mic.state === 'off' && mic.clicks === 2, `state ${mic.state} clicks ${mic.clicks}`);
  check('the mic-off line names the id', lines.includes('[bot] speak id=m1 mic=off'), lines.join(' | '));
}
{
  const mic = fakeMic('on');
  const { speak, tts } = setup({ mic });
  const p = speak.speak('hi', undefined, 'm2');
  await tick(); await tick();
  check('a mic already on is not clicked off before speaking', tts.plays.length === 1 && tts.plays[0].micDuring === 'on' && mic.clicks === 0, `clicks ${mic.clicks}`);
  tts.finish(); await p;
}
{
  const { speak, tts, lines } = setup({ mic: fakeMic('unknown') });
  await speak.speak('hi', undefined, 'm3');
  check('an unverifiable mic means no play', tts.plays.length === 0);
  check('…failed reason=mic_unverified', lines.includes('[bot] speak id=m3 failed reason=mic_unverified'), lines.join(' | '));
}
{
  const { speak, tts, lines } = setup({ mic: fakeMic('off', { stuck: true }) });
  await speak.speak('hi', undefined, 'm4');
  check('a mic that will not switch on means no play', tts.plays.length === 0 && lines.includes('[bot] speak id=m4 failed reason=mic_unverified'), lines.join(' | '));
}
{
  const mic = fakeMic('on', { stuck: true });
  const { speak, tts, lines } = setup({ mic });
  const p = speak.speak('hi', undefined, 'm5');
  await tick(); await tick(); tts.finish(); await p;
  check('a mic that will not switch off is logged as unverified', lines.includes('[bot] speak id=m5 mic_off_unverified'), lines.join(' | '));
}
{
  const mic = fakeMic('on');
  const { speak, tts, lines } = setup({ platform: 'zoom', mic });
  const p = speak.speak('hi', undefined, 'z1');
  await tick(); await tick(); tts.finish(); await p;
  check('on Zoom the UI mic is left as the join set it', mic.clicks === 0 && tts.plays.length === 1, `clicks ${mic.clicks}`);
  check('…and the line says so', lines.some((l) => l.startsWith('[bot] speak id=z1 mic=zoom_ui_left_on')), lines.join(' | '));
}

// ── typed TTS failures reach the line ──
for (const reason of ['tts_http_401', 'tts_not_configured', 'tts_unreachable'] as const) {
  const { speak, tts, mic, lines } = setup();
  const p = speak.speak('hi', undefined, `f-${reason}`);
  await tick(); await tick(); tts.finish({ ok: false, reason }); await p;
  check(`a TTS ${reason} gives failed reason=${reason}`, lines.includes(`[bot] speak id=f-${reason} failed reason=${reason}`), lines.join(' | '));
  check(`…and the mic goes back off (${reason})`, mic.state === 'off');
}

// ── the id is on every line, the text on none ──
{
  const { speak, tts, lines } = setup();
  const secret = 'the merger closes on Friday';
  const p = speak.speak(secret, 'af_bella', 'sec1');
  await tick(); await tick(); tts.finish(); await p;
  check('the voice reaches the TTS', tts.plays[0]?.voice === 'af_bella');
  check('every speak line names the id', lines.filter((l) => l.startsWith('[bot] speak ')).every((l) => l.includes('id=sec1')), lines.join(' | '));
  check('no line carries the spoken text', !lines.some((l) => l.includes(secret)), lines.join(' | '));
}
{
  const { speak, tts, lines } = setup();
  const p = speak.speak('no id given');
  await tick(); await tick(); tts.finish(); await p;
  check('an act without an id still gets a traceable id', lines.some((l) => /^\[bot\] speak id=\S+ started chars=11$/.test(l)), lines.join(' | '));
}

// ── reading a meeting-UI mic control (label → state), per platform ──
const cases: [string, string | null, MicState][] = [
  ['Turn off microphone (ctrl + d)', null, 'on'],        // Meet, mic on
  ['Turn on microphone (ctrl + d)', null, 'off'],        // Meet, mic off
  ['Mute (Ctrl+Shift+M)', null, 'on'],                   // Teams, mic on
  ['Unmute (Ctrl+Shift+M)', null, 'off'],                // Teams, mic off
  ['Mute mic', null, 'on'],
  ['Unmute mic', null, 'off'],
  ['mute my microphone', null, 'on'],                    // Zoom web
  ['unmute my microphone', null, 'off'],
  ['Mute microphone', 'false', 'on'],                    // Jitsi
  ['Unmute microphone', 'true', 'off'],
  ['Camera', null, 'unknown'],
  ['', null, 'unknown'],
];
for (const [label, pressed, want] of cases) {
  check(`mic control "${label}" reads ${want}`, micStateFromControl(label, pressed) === want, String(micStateFromControl(label, pressed)));
}
check('no control at all reads unknown', micStateFromControl(null, null) === 'unknown');

if (failed) { console.error(`\n❌ speak-controller: ${failed} check(s) failed`); process.exit(1); }
console.log('\n✅ speak-controller (L2): one utterance at a time, a capped queue, stop clears it, the mic is read before it is clicked, and every line names the id.');
