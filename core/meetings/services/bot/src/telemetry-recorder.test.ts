/**
 * O-TEL-1b — the recorder adapter (telemetry.ts). OFFLINE, NO browser/redis/whisper.
 *
 * Drives the EXACT bridge tap (makeTelemetryTap) into the REAL recording sink
 * (createCaptureSignalRecorder) and asserts the persisted session is replay-grade:
 *   • the file opens with a captured-signal.v1 SessionHeader (ajv vs the SSOT schema);
 *   • every frame line conforms + seq is monotone + arrival order is preserved across
 *     buffered flushes;
 *   • each stored pcm decodes back to the EXACT Float32 PCM the tap saw (the replay loader's
 *     framePcm shape) — so replay.test.ts can consume a recorded session verbatim;
 *   • a zero-frame session still leaves an attributable header-only file;
 *   • a writer fault never throws into captureFrame, and close() is idempotent.
 * Run: npx tsx src/telemetry-recorder.test.ts
 */
import Ajv2020, { type ValidateFunction } from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import { readFileSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { makeTelemetryTap, makeSpeakerHintSink } from './capture-bridge.js';
import { createCaptureSignalRecorder, type SignalWriter } from './telemetry.js';
import type { Invocation } from './config.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

const HERE = dirname(fileURLToPath(import.meta.url));
const CS_SCHEMA = join(HERE, '..', '..', '..', 'contracts', 'captured-signal.v1', 'captured-signal.schema.json');
const csSchema = JSON.parse(readFileSync(CS_SCHEMA, 'utf8'));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(csSchema);
const validateFrame: ValidateFunction = ajv.compile({ $ref: `${csSchema.$id}#/$defs/CapturedFrame` });
const validateHeader: ValidateFunction = ajv.compile({ $ref: `${csSchema.$id}#/$defs/SessionHeader` });
const validateHint: ValidateFunction = ajv.compile({ $ref: `${csSchema.$id}#/$defs/HintEvent` });

const inv = {
  platform: 'google_meet', meetingUrl: 'https://meet.google.com/abc-defg-hij', botName: 'RecBot',
  nativeMeetingId: 'abc-defg-hij', connectionId: 'conn-rec-1', redisUrl: 'redis://unused:6379',
  language: 'en',
} as Invocation;

const pcm = (n: number, seed: number): Float32Array =>
  Float32Array.from({ length: n }, (_, i) => ((((seed * 5 + i * 3) % 256) - 128) / 256));

async function main(): Promise<void> {
  const dir = mkdtempSync(join(tmpdir(), 'cs-rec-'));
  try {
    // ── 1) tap → recorder → file: header + frames conform, order + pcm exact ──
    {
      const rec = createCaptureSignalRecorder(inv, { dir, flushMs: 10 });
      const tee = makeTelemetryTap('gmeet', rec.sink);
      const sent: Float32Array[] = [];
      for (let i = 0; i < 50; i++) {
        const p = pcm(160, i);
        sent.push(p);
        tee(i % 2, p, 1718000000000 + i * 100, i % 2 ? 'Bob' : 'Alice');
      }
      await rec.close();
      await rec.close(); // idempotent

      const lines = readFileSync(rec.path, 'utf8').split('\n').filter(Boolean);
      const header = JSON.parse(lines[0]);
      check('SessionHeader conforms (ajv vs SSOT)', !!validateHeader(header), ajv.errorsText(validateHeader.errors));
      check('header carries platform/native/lane', header.platform === 'google_meet' && header.native_meeting_id === 'abc-defg-hij' && header.lane === 'gmeet', JSON.stringify(header));
      const frames = lines.slice(1).map((l) => JSON.parse(l));
      check('all 50 frames persisted', frames.length === 50, `n=${frames.length}`);
      check('every frame conforms', frames.every((f) => validateFrame(f)), ajv.errorsText(validateFrame.errors));
      check('seq monotone, arrival order preserved', frames.every((f, i) => f.seq === i), JSON.stringify(frames.map((f) => f.seq).slice(0, 5)));
      const exact = frames.every((f, i) => {
        const b = Buffer.from(f.pcm, 'base64');
        const restored = new Float32Array(b.buffer, b.byteOffset, b.byteLength / 4);
        return f.pcm_len === sent[i].length &&
          Buffer.compare(Buffer.from(restored.buffer, restored.byteOffset, restored.byteLength),
                         Buffer.from(sent[i].buffer, sent[i].byteOffset, sent[i].byteLength)) === 0;
      });
      check('stored pcm ≡ tapped pcm (Float32-bit-exact, replay-loadable)', exact);
      check('names alternate Alice/Bob as tapped', frames[0].speakerName === 'Alice' && frames[1].speakerName === 'Bob');
    }

    // ── 1b) MIXED lane: the out-of-band hints are recorded too ────────────────────────
    // Live-witnessed gap: a mixed-lane session (jitsi/teams/zoom) carries ONE audio stream and
    // names it from hints that arrive on their own channel — never on the audio frames. A
    // recorder that taps only audio stores a session that replays sound with NO speakers.
    // Drive the REAL hint sink (the closure the bridge exposes as __vexaSpeakerHint).
    {
      const rec = createCaptureSignalRecorder({ ...inv, platform: 'jitsi' } as Invocation, { dir: join(dir, 'mixed'), flushMs: 10 });
      const tee = makeTelemetryTap('mixed', rec.sink);
      const seen: Array<[string, number, boolean | undefined]> = [];
      const { sink: hintSink } = makeSpeakerHintSink(
        { recordHint: (n, t, e) => { seen.push([n, t, e]); } }, () => { /* quiet */ }, rec.sink,
      );
      // Hints ride the LIVE epoch clock: the bridge's skew guard re-stamps anything far from
      // now (a page emitting performance.now() instead of epoch), so the fixture must be current.
      const base = Date.now();
      hintSink('Anna', base + 100);
      for (let i = 0; i < 10; i++) tee(0, pcm(160, i), base + 200 + i * 100);
      hintSink('Anna', base + 1300, true);
      hintSink('Boris', base + 1400);
      for (let i = 0; i < 10; i++) tee(0, pcm(160, i + 50), base + 1500 + i * 100);
      await rec.close();

      const recs = readFileSync(rec.path, 'utf8').split('\n').filter(Boolean).slice(1).map((l) => JSON.parse(l));
      const hintRecs = recs.filter((r) => r.type === 'hint');
      const frameRecs = recs.filter((r) => r.type !== 'hint');
      check('mixed lane: hints reach the session file', hintRecs.length === 3, `n=${hintRecs.length}`);
      check('every hint is captured-signal.v1-valid (ajv vs SSOT)',
        hintRecs.every((h) => validateHint(h)), ajv.errorsText(validateHint.errors));
      check('hints carry name + epoch t + isEnd',
        hintRecs[0].name === 'Anna' && hintRecs[0].t === base + 100 &&
        hintRecs[1].isEnd === true && hintRecs[2].name === 'Boris', JSON.stringify(hintRecs));
      check('audio frames still recorded alongside hints', frameRecs.length === 20, `n=${frameRecs.length}`);
      check('hints interleave with audio in arrival order',
        recs[0].type === 'hint' && recs[1].type !== 'hint', recs.slice(0, 3).map((r) => r.type ?? 'frame').join(','));
      check('the pipeline still receives every hint (tee never swallows)', seen.length === 3, `n=${seen.length}`);
      // The stored hints must be enough to reconstruct who spoke when, offline.
      const names = [...new Set(hintRecs.map((h) => h.name))].sort();
      check('stored hints name both speakers (attribution is reproducible offline)',
        names.join(',') === 'Anna,Boris', names.join(','));
    }

    // ── 2) zero-frame session: header-only file still written (attributable) ──
    {
      const rec = createCaptureSignalRecorder(inv, { dir: join(dir, 'empty') });
      await rec.close();
      const lines = readFileSync(rec.path, 'utf8').split('\n').filter(Boolean);
      check('zero-frame session leaves a header-only file', lines.length === 1 && !!validateHeader(JSON.parse(lines[0])));
    }

    // ── 3) writer faults are swallowed — captureFrame never throws into capture ──
    {
      const bad: SignalWriter = { append: async () => { throw new Error('disk gone'); }, end: async () => { /* */ } };
      const rec = createCaptureSignalRecorder(inv, { writer: bad, flushMs: 5, log: () => { /* quiet */ } });
      const tee = makeTelemetryTap('gmeet', rec.sink);
      let threw = false;
      try { for (let i = 0; i < 100; i++) tee(0, pcm(16, i), i, 'Alice'); await rec.close(); }
      catch { threw = true; }
      check('a faulting writer never throws into the capture path', !threw);
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }

  if (failed) { console.error(`\n❌ telemetry-recorder (O-TEL-1b): ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ telemetry-recorder (O-TEL-1b): the recorder persists a replay-grade captured-signal.v1 session (header + ordered conformant frames, bit-exact pcm); zero-frame sessions stay attributable; writer faults never reach capture.');
}

main().catch((e) => { console.error(e); process.exit(1); });
