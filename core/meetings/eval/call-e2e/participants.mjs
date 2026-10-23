// Headless call participants (#1783): each joins the LiveKit room with its own token and publishes
// a speech WAV as its microphone track, in real time, then leaves. One JSON line per participant on
// stdout: identity, seconds published. Run from the bot package (it carries @livekit/rtc-node):
//
//   CALL_E2E_SPEC='<json: {url, people: [{token, identity, wav, startSec, holdSec}]}>' node participants.mjs
//
// The tokens arrive in the environment (never on a command line) and nothing prints them.
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

// The SDK is the bot package's own dependency: resolve it from the working directory (the bot
// package), not from this file's directory.
const require = createRequire(join(process.cwd(), 'package.json'));
const { AudioFrame, AudioSource, LocalAudioTrack, Room, TrackPublishOptions, TrackSource, dispose } =
  await import(pathToFileURL(require.resolve('@livekit/rtc-node')).href);

const spec = JSON.parse(process.env.CALL_E2E_SPEC);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function readWav16k(path) {
  const b = readFileSync(path);
  // Minimal RIFF reader: find the "data" chunk; the client converts to mono 16 kHz s16 first.
  let off = 12;
  while (off < b.length) {
    const id = b.toString('ascii', off, off + 4);
    const size = b.readUInt32LE(off + 4);
    if (id === 'data') return new Int16Array(b.buffer.slice(b.byteOffset + off + 8, b.byteOffset + off + 8 + size));
    off += 8 + size + (size % 2);
  }
  throw new Error(`no data chunk in ${path}`);
}

async function person(p) {
  const pcm = readWav16k(p.wav);
  const room = new Room();
  await room.connect(spec.url, p.token, { autoSubscribe: false, dynacast: false });
  const source = new AudioSource(16000, 1);
  const track = LocalAudioTrack.createAudioTrack('microphone', source);
  const opts = new TrackPublishOptions();
  opts.source = TrackSource.SOURCE_MICROPHONE;
  await room.localParticipant.publishTrack(track, opts);
  await sleep(p.startSec * 1000);
  const step = 160; // 10 ms
  const t0 = Date.now();
  let sent = 0;
  for (let i = 0; i + step <= pcm.length; i += step) {
    await source.captureFrame(new AudioFrame(pcm.slice(i, i + step), 16000, 1, step));
    sent += step;
  }
  await source.waitForPlayout?.();
  await sleep(p.holdSec * 1000);
  await room.disconnect();
  return { identity: p.identity, seconds_published: +(sent / 16000).toFixed(2), wall_sec: +((Date.now() - t0) / 1000).toFixed(1) };
}

const results = await Promise.all(spec.people.map(person));
for (const r of results) console.log(JSON.stringify(r));
await dispose();
