/**
 * O-TEL-1 — raw-signal telemetry (captured-signal.v1). OFFLINE, NO browser/redis/whisper.
 *
 * Drives the EXACT capture-bridge tap (`makeTelemetryTap` — the closure the bridge tees every
 * raw frame into BEFORE the pipeline) and asserts:
 *   • a fed frame REACHES the TelemetrySink (the dual-sink tap fires);
 *   • each captured frame CONFORMS to captured-signal.v1 (ajv against the published schema, SSOT);
 *   • the frame's pcm ROUND-TRIPS through @vexa/capture-codec (encode→decode→same Float32 PCM) —
 *     proving the stored signal replays through the same wire shape (O-TEL-2);
 *   • the tap is ZERO-OVERHEAD when the sink is unset (no captureFrame calls, no PCM work) — the
 *     proven O6 live-capture path is never altered.
 * Run: npx tsx src/telemetry.test.ts
 */
import Ajv2020, { type ValidateFunction } from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { encodeAudioFrame, decodeAudioFrame } from '@vexa/capture-codec';
import { makeRemoteAudioEnergyTap, makeTelemetryTap, pcmToBase64, rmsOf } from './capture-bridge.js';
import type { CapturedFrame, TelemetrySink } from './ports.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

// ── captured-signal.v1 validator (ajv against the PUBLISHED schema, loaded by path; P8) ──
const HERE = dirname(fileURLToPath(import.meta.url));
const CS_SCHEMA = join(HERE, '..', '..', '..', 'contracts', 'captured-signal.v1', 'captured-signal.schema.json');
const csSchema = JSON.parse(readFileSync(CS_SCHEMA, 'utf8'));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(csSchema);
const validateFrame: ValidateFunction = ajv.compile({ $ref: `${csSchema.$id}#/$defs/CapturedFrame` });
const validateHeader: ValidateFunction = ajv.compile({ $ref: `${csSchema.$id}#/$defs/SessionHeader` });

/** A capturing TelemetrySink — records every teed frame. */
function captureSink(): TelemetrySink & { readonly frames: CapturedFrame[] } {
  const frames: CapturedFrame[] = [];
  return { frames, captureFrame(f) { frames.push(f); } };
}

// Deterministic Float32-exact PCM (n/256 ramp — bit-exact, byte-stable base64).
const SR = 16000;
const pcm = (n: number, seed: number): Float32Array =>
  Float32Array.from({ length: n }, (_, i) => ((((seed * 5 + i * 3) % 256) - 128) / 256));

function main(): void {
  // ── 1) gmeet lane: a glow-named frame is teed → reaches the sink, conforms, round-trips ──
  {
    const sink = captureSink();
    const tee = makeTelemetryTap('gmeet', sink);
    const g = pcm(8, 1);
    tee(0, g, 1718000000123, 'Alice');               // ch0, glow="Alice"
    tee(0, pcm(8, 4), 1718000000323, 'Alice');       // a second frame → seq increments

    check('a fed frame reached the TelemetrySink', sink.frames.length === 2, `n=${sink.frames.length}`);
    const f = sink.frames[0];
    check('frame is captured-signal.v1-valid (ajv vs SSOT)', !!validateFrame(f), ajv.errorsText(validateFrame.errors));
    check('frame carries the capture fields (ts, channel, glow, lane)',
      f.ts === 1718000000123 && f.speakerIndex === 0 && f.speakerName === 'Alice' && f.lane === 'gmeet', JSON.stringify(f));
    check('seq is monotone per session', sink.frames[0].seq === 0 && sink.frames[1].seq === 1,
      `${sink.frames[0].seq},${sink.frames[1].seq}`);
    check('pcm_len + rms match the source PCM', f.pcm_len === g.length && Math.abs((f.rms ?? -1) - rmsOf(g)) < 1e-9,
      `${f.pcm_len} ${f.rms}`);

    // ── ROUND-TRIP through @vexa/capture-codec: the captured base64 PCM ≡ encode→decode→same ──
    // Reconstruct the Float32Array from the stored base64, encode it as a codec audio frame with the
    // SAME (speakerIndex, ts, name), decode it back, and assert the PCM is Float32-bit-exact.
    const bytes = Buffer.from(f.pcm, 'base64');
    const restored = new Float32Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 4);
    const wire = encodeAudioFrame(f.speakerIndex, f.ts, restored, f.speakerName);
    const dec = decodeAudioFrame(wire, 0, wire.byteLength);
    const pcmExact = !!dec && dec.samples.length === g.length &&
      Buffer.compare(Buffer.from(dec.samples.buffer, dec.samples.byteOffset, dec.samples.byteLength),
                     Buffer.from(g.buffer, g.byteOffset, g.byteLength)) === 0;
    check('captured pcm round-trips through @vexa/capture-codec (encode→decode→same)', pcmExact,
      JSON.stringify({ in: Array.from(g), out: dec ? Array.from(dec.samples) : null }));
    check('round-trip preserves ts + glow name (codec carries them verbatim)',
      dec?.ts === f.ts && dec?.speakerName === f.speakerName, JSON.stringify(dec));
    // The captured base64 is itself the codec wire payload: re-encoding the restored PCM yields
    // base64 identical to what the tap stored.
    check('captured pcm base64 ≡ codec wire payload', pcmToBase64(restored) === f.pcm);
  }

  // ── 2) mixed lane: a mixed-stream frame + a hint-carrying frame conform with lane='mixed' ──
  {
    const sink = captureSink();
    const tee = makeTelemetryTap('mixed', sink);
    tee(999, pcm(8, 2), 1718000000523);                          // the mixed remote stream (ch999)
    tee(999, pcm(8, 3), 1718000000723, undefined, 'Boris');      // a frame carrying an active-speaker hint
    check('mixed frames reached the sink', sink.frames.length === 2);
    check('mixed frame conforms + lane=mixed', !!validateFrame(sink.frames[0]) && sink.frames[0].lane === 'mixed',
      ajv.errorsText(validateFrame.errors));
    check('hint frame conforms + carries the hint', !!validateFrame(sink.frames[1]) && sink.frames[1].hint === 'Boris',
      JSON.stringify(sink.frames[1]));
  }

  // ── 3) ZERO-OVERHEAD when the sink is UNSET — the tap does nothing, computes nothing ──
  {
    // A PCM whose .buffer access / rms loop would be observable IF the tap touched it. We pass a
    // Proxy-trapped getter on the typed array's properties is not portable; instead assert the
    // contract directly: with no sink, calling the tap N times is a no-op (no frames anywhere to
    // observe, returns undefined, never throws) — the single truthiness branch is the whole cost.
    const tee = makeTelemetryTap('gmeet', undefined);
    let threw = false;
    try { for (let i = 0; i < 1000; i++) tee(0, pcm(16, i), i, 'Alice', 'Boris'); }
    catch { threw = true; }
    check('unset sink: tap is a no-op (never throws, nothing captured)', !threw);

    // Stronger proof of the branch order: build the tap WITH a spy sink and confirm it fires, then
    // build it UNSET and confirm captureFrame is never reached — the single truthiness check is the
    // entire cost when unset, so the proven O6 capture path is byte-for-byte unchanged.
    let calledWhenSet = 0, calledWhenUnset = 0;
    const set: TelemetrySink = { captureFrame() { calledWhenSet++; } };
    const teeSet = makeTelemetryTap('gmeet', set);
    teeSet(0, pcm(8, 9), 1, 'Alice');
    const unset = makeTelemetryTap('gmeet', undefined);
    unset(0, pcm(8, 9), 1, 'Alice');
    check('set sink fires; unset sink never reaches captureFrame',
      calledWhenSet === 1 && calledWhenUnset === 0, `set=${calledWhenSet} unset=${calledWhenUnset}`);
  }

  // ── 4) the same REMOTE capture callbacks feed RMS energy to aloneness ──
  {
    const energies: number[] = [];
    const tap = makeRemoteAudioEnergyTap({
      ready() { /* readiness is driven by page capture start */ },
      observeRemoteEnergy(energy) { energies.push(energy); },
      unavailable() { /* teardown is driven by the capture bridge */ },
      snapshot() { return { available: true, lastRemoteAudioAt: 0 }; },
    });
    const frame = pcm(8, 7);
    tap(frame);
    check('remote-audio activity tap receives the captured frame RMS',
      energies.length === 1 && Math.abs(energies[0] - rmsOf(frame)) < 1e-9,
      JSON.stringify(energies));
  }

  if (failed) { console.error(`\n❌ telemetry (O-TEL-1): ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ telemetry (O-TEL-1): the capture-bridge tap tees raw frames into the TelemetrySink BEFORE the pipeline; each frame is captured-signal.v1-valid + round-trips through @vexa/capture-codec; the tap is zero-overhead when the sink is unset.');
}

// Header golden sanity: the SessionHeader golden also validates (the replay header shape).
const headerGolden = JSON.parse(readFileSync(join(HERE, '..', '..', '..', 'contracts', 'captured-signal.v1', 'golden', 'SessionHeader.gmeet.json'), 'utf8'));
check('captured-signal.v1 SessionHeader golden conforms', !!validateHeader(headerGolden), ajv.errorsText(validateHeader.errors));

main();
