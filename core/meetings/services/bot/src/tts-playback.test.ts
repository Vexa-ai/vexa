/**
 * TTS playback (tts-playback.ts) — L2, a fake HTTP client and a fake player: no network, no PulseAudio.
 *
 * Pins #1866 C3: an unset URL is `tts_not_configured`, an HTTP error is `tts_http_<status>` and never
 * plays, an unreachable server is `tts_unreachable`; the token goes as both `Authorization: Bearer`
 * and `X-API-Key`; the voice falls back to TTS_VOICE, then `alloy`; the PulseAudio chain is unmuted
 * only while audio plays; `stop()` kills the player at once; one player at a time.
 * Run: npx tsx src/tts-playback.test.ts
 */
import { PassThrough, Writable } from 'node:stream';
import { EventEmitter } from 'node:events';
import { createTtsPlayback, pactlMuteCommand, type TtsHttp, type TtsPlayer } from './tts-playback.js';

let failed = 0;
const check = (name: string, cond: boolean, detail?: string) => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond || !detail ? '' : ` — ${detail}`}`);
  if (!cond) failed++;
};
const tick = () => new Promise<void>((r) => setTimeout(r, 0));

interface Sent { url: string; headers: Record<string, string>; body: any }

function fakeHttp(status: number, opts: { fail?: boolean } = {}) {
  const sent: Sent[] = [];
  let audio: PassThrough | null = null;
  const http: TtsHttp = async (url, init) => {
    sent.push({ url: url.toString(), headers: init.headers, body: JSON.parse(init.body) });
    if (opts.fail) throw new Error('ECONNREFUSED');
    audio = new PassThrough();
    return { status, body: audio };
  };
  return { http, sent, audio: () => audio };
}

function fakePlayers() {
  const made: { proc: TtsPlayer & EventEmitter; got: Buffer[]; killed: boolean }[] = [];
  const player = (): TtsPlayer => {
    const ee = new EventEmitter() as TtsPlayer & EventEmitter;
    const rec = { proc: ee, got: [] as Buffer[], killed: false };
    (ee as any).stdin = new Writable({ write(c, _e, cb) { rec.got.push(c); cb(); } });
    (ee as any).kill = () => { rec.killed = true; setTimeout(() => ee.emit('exit', null), 0); };
    made.push(rec);
    return ee;
  };
  return { player, made };
}

function harness(env: Record<string, string>, status = 200, opts: { fail?: boolean } = {}) {
  const h = fakeHttp(status, opts);
  const p = fakePlayers();
  const mutes: boolean[] = [];
  const lines: string[] = [];
  const tts = createTtsPlayback((l) => lines.push(l), {
    request: h.http, player: p.player, setMute: (m) => mutes.push(m), env, now: (() => { let t = 0; return () => (t += 500); })(),
  });
  return { tts, h, p, mutes, lines };
}

{
  // #1866 live: a muted virtual_mic shows in Meet as "Microphone problem" in place of the mic toggle,
  // so the bot can never read (or switch on) its mic. Silence comes from muting tts_sink alone.
  check('muting touches tts_sink only, never the virtual_mic source',
    pactlMuteCommand(true) === 'pactl set-sink-mute tts_sink 1' && pactlMuteCommand(false) === 'pactl set-sink-mute tts_sink 0',
    pactlMuteCommand(true));
}

const URL_ENV = { TTS_SERVICE_URL: 'http://tts.local:8880/', TTS_API_TOKEN: 'tok-123' };

{
  const { tts, h } = harness({});
  const o = await tts.play('hi');
  check('an unset TTS_SERVICE_URL is tts_not_configured', !o.ok && o.reason === 'tts_not_configured', JSON.stringify(o));
  check('…and nothing is requested', h.sent.length === 0);
}
{
  const { tts } = harness({ TTS_SERVICE_URL: 'not a url' });
  const o = await tts.play('hi');
  check('a malformed TTS_SERVICE_URL is tts_not_configured', !o.ok && o.reason === 'tts_not_configured', JSON.stringify(o));
}
{
  const { tts, p, mutes } = harness(URL_ENV, 401);
  const o = await tts.play('hi');
  check('a TTS 401 is tts_http_401', !o.ok && o.reason === 'tts_http_401', JSON.stringify(o));
  check('…and nothing plays, the chain stays muted', p.made.length === 0 && !mutes.includes(false), JSON.stringify(mutes));
}
{
  const { tts } = harness(URL_ENV, 200, { fail: true });
  const o = await tts.play('hi');
  check('an unreachable TTS server is tts_unreachable', !o.ok && o.reason === 'tts_unreachable', JSON.stringify(o));
}
{
  const { tts, h, p, mutes } = harness({ ...URL_ENV, TTS_VOICE: 'af_bella' });
  const playing = tts.play('hello there');
  await tick(); await tick();
  const sent = h.sent[0];
  check('it posts to {TTS_SERVICE_URL}/v1/audio/speech', sent?.url === 'http://tts.local:8880/v1/audio/speech', sent?.url);
  check('the token goes as Authorization: Bearer', sent?.headers['Authorization'] === 'Bearer tok-123');
  check('…and as X-API-Key', sent?.headers['X-API-Key'] === 'tok-123');
  check('it asks for 24 kHz PCM', sent?.body.response_format === 'pcm' && sent?.body.input === 'hello there');
  check('no voice named → TTS_VOICE', sent?.body.voice === 'af_bella', sent?.body.voice);
  check('the chain is unmuted while audio plays', mutes.includes(false));
  h.audio()!.end(Buffer.from([1, 2, 3, 4]));
  await tick(); await tick();
  p.made[0].proc.emit('exit', 0);
  const o = await playing;
  check('a played utterance is ok with its duration', o.ok && o.ms > 0, JSON.stringify(o));
  check('the PCM reached the player', Buffer.concat(p.made[0].got).length === 4);
  check('the chain is muted again after', mutes[mutes.length - 1] === true, JSON.stringify(mutes));
}
{
  const { tts, h } = harness(URL_ENV);
  void tts.play('x', 'auto'); await tick(); await tick();
  check('"auto" with no TTS_VOICE → alloy', h.sent[0]?.body.voice === 'alloy', h.sent[0]?.body.voice);
  tts.stop();
}
{
  const { tts, h } = harness({ ...URL_ENV, TTS_VOICE: 'af_bella' });
  void tts.play('x', 'am_puck'); await tick(); await tick();
  check('a named voice wins over TTS_VOICE', h.sent[0]?.body.voice === 'am_puck', h.sent[0]?.body.voice);
  tts.stop();
}
{
  const { tts, h } = harness({ TTS_SERVICE_URL: 'http://tts.local:8880' });
  void tts.play('x'); await tick(); await tick();
  check('no token → no auth headers', !('Authorization' in (h.sent[0]?.headers ?? {})) && !('X-API-Key' in (h.sent[0]?.headers ?? {})));
  tts.stop();
}
{
  const { tts, p, mutes } = harness(URL_ENV);
  const playing = tts.play('a long paragraph');
  await tick(); await tick();
  tts.stop();
  const o = await playing;
  check('stop kills the player', p.made[0]?.killed === true);
  check('…the utterance ends as stopped', !o.ok && o.reason === 'stopped', JSON.stringify(o));
  check('…and the chain is muted', mutes[mutes.length - 1] === true);
}
{
  const { tts, p } = harness(URL_ENV);
  void tts.play('one'); await tick(); await tick();
  void tts.play('two'); await tick(); await tick();
  check('a second play never runs beside the first: the first player is killed', p.made.length === 2 && p.made[0].killed && !p.made[1].killed, JSON.stringify(p.made.map((m) => m.killed)));
  tts.stop();
}
{
  const { tts, lines } = harness({ ...URL_ENV }, 500);
  await tts.play('the merger closes on Friday');
  check('no log line carries the spoken text or the token', !lines.some((l) => l.includes('merger') || l.includes('tok-123')), lines.join(' | '));
}

if (failed) { console.error(`\n❌ tts-playback: ${failed} check(s) failed`); process.exit(1); }
console.log('\n✅ tts-playback (L2): typed outcomes, both auth headers, the voice fallback, the chain open only while playing, stop kills at once, one player at a time.');
