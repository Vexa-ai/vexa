/**
 * capture.v1 fixture validator — "fixtures MUST meet the contract" (MANIFEST P4),
 * machine-enforced. A fixture that fails this is not a capture.v1 fixture.
 *
 *   node contracts/capture/v1/validate.mjs <fixture-dir>
 *
 * Two on-disk forms, auto-detected:
 *   • stream.capture  — the FAITHFUL wire log (capture.v1/stream): the canonical
 *     replayable form written by capture-recorder, the prod-dump tees, live-ingest
 *     and bench. Framing [u8 type 0=audio 1=event][u32LE len][payload]; audio
 *     payload [Int32LE speakerIndex][Float64LE ts][Float32LE pcm…]; events JSON.
 *   • audio/*.wav     — the legacy decoded corpus form (RawCaptureService).
 *
 * Conformance (schema.ts + §2): meta.topology ∈ {per-participant, mixed};
 * mixed ⇒ a single REMOTE channel (mic 1000 rides alongside); events well-formed.
 */
import { readFileSync, existsSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

const KINDS = new Set(['speaker-joined','speaker-left','active-speaker','caption','segment','lifecycle','track-lock','chat']);
const MIC_INDEX = 1000;   // local mic ("You") — a legit separate channel in BOTH topologies
const MIXED_CHANNEL = 999; // the single diarized remote channel under `mixed`
const dir = process.argv[2];
const errs = [];
const warns = [];
if (!dir || !existsSync(dir)) { console.error(`usage: validate.mjs <fixture-dir> (not found: ${dir})`); process.exit(2); }

// ── meta.json (shared) ──
let meta = {};
try { meta = JSON.parse(readFileSync(join(dir, 'meta.json'), 'utf8')); }
catch { errs.push('meta.json missing or unparseable'); }
if (!['per-participant', 'mixed'].includes(meta.topology)) errs.push(`meta.topology must be 'per-participant'|'mixed' (got ${JSON.stringify(meta.topology)})`);
if (!meta.platform) warns.push('meta.platform empty');
if (!meta.sample_rate) warns.push('meta.sample_rate empty');

const streamPath = join(dir, 'stream.capture');
const audioDir = join(dir, 'audio');

if (existsSync(streamPath)) {
  // ── stream.capture (the faithful wire log) ──
  if (meta.capture && meta.capture !== 'capture.v1/stream') warns.push(`meta.capture is ${JSON.stringify(meta.capture)} (expected 'capture.v1/stream')`);
  const buf = readFileSync(streamPath);
  const channels = new Set();
  let off = 0, audio = 0, events = 0, badRec = 0, lastTs = -Infinity, outOfOrder = 0, firstTs = null;
  const kinds = {};
  while (off + 5 <= buf.length) {
    const type = buf.readUInt8(off); const len = buf.readUInt32LE(off + 1); off += 5;
    if (off + len > buf.length) { errs.push(`stream.capture truncated at byte ${off} (record len ${len} > remaining)`); break; }
    const p = buf.subarray(off, off + len); off += len;
    if (type === 0) {
      if (len < 12) { badRec++; continue; }                 // [i32][f64] header minimum
      const spk = p.readInt32LE(0); const ts = p.readDoubleLE(4);
      channels.add(spk); audio++;
      if (firstTs === null) firstTs = ts;
      if (ts < lastTs) outOfOrder++; lastTs = ts;
    } else if (type === 1) {
      let e; try { e = JSON.parse(p.toString('utf8')); } catch { errs.push(`stream.capture event #${events + 1} not JSON`); continue; }
      if (!KINDS.has(e.kind)) errs.push(`stream.capture event #${events + 1} bad kind ${JSON.stringify(e.kind)}`);
      if (typeof e.ts !== 'number') errs.push(`stream.capture event #${events + 1} ts must be a number`);
      kinds[e.kind] = (kinds[e.kind] || 0) + 1; events++;
    } else { badRec++; errs.push(`stream.capture unknown record type ${type} at byte ${off - len - 5}`); }
  }
  if (audio === 0) errs.push('stream.capture has no audio frames');
  const remote = [...channels].filter((c) => c !== MIC_INDEX);
  if (meta.topology === 'mixed' && remote.length > 1) errs.push(`mixed topology but ${remote.length} remote channels [${remote}] — expected one (${MIXED_CHANNEL})`);
  if (meta.topology === 'mixed' && remote.length === 1 && remote[0] !== MIXED_CHANNEL) warns.push(`mixed remote channel is ${remote[0]} (expected ${MIXED_CHANNEL})`);
  if (badRec) warns.push(`${badRec} malformed record(s)`);
  if (outOfOrder) warns.push(`${outOfOrder} out-of-order ts (capture should be monotonic)`);
  const durS = firstTs !== null ? (lastTs - firstTs) / (lastTs > 1e6 ? 1000 : 1) : 0; // ts may be ms or s
  console.log(`  stream.capture: ${audio} audio frames, ${events} events ${JSON.stringify(kinds)}; channels=[${[...channels]}] (${remote.length} remote + ${channels.has(MIC_INDEX) ? 1 : 0} mic); topology=${meta.topology}; platform=${meta.platform}`);
} else if (existsSync(audioDir)) {
  // ── legacy audio/*.wav corpus form ──
  const ejPath = join(dir, 'events.jsonl');
  if (!existsSync(ejPath)) errs.push("events.jsonl missing — contract requires structured events");
  else {
    const lines = readFileSync(ejPath, 'utf8').split('\n').filter((l) => l.trim());
    let ok = 0;
    lines.forEach((l, i) => { let e; try { e = JSON.parse(l); } catch { errs.push(`events.jsonl:${i + 1} not JSON`); return; } if (!KINDS.has(e.kind)) errs.push(`events.jsonl:${i + 1} bad kind ${JSON.stringify(e.kind)}`); if (typeof e.ts !== 'number') errs.push(`events.jsonl:${i + 1} ts must be a number`); if (KINDS.has(e.kind) && typeof e.ts === 'number') ok++; });
    if (ok === 0) errs.push('events.jsonl has no valid events'); else console.log(`  events.jsonl: ${ok} valid events`);
  }
  const wavs = readdirSync(audioDir).filter((f) => f.endsWith('.wav'));
  if (wavs.length === 0) errs.push('audio/ has no .wav');
  const channels = Array.isArray(meta.channels) ? meta.channels : [];
  const remoteChannels = channels.filter((c) => c.channel !== MIC_INDEX);
  if (meta.topology === 'mixed' && remoteChannels.length > 1) errs.push(`mixed topology but ${remoteChannels.length} remote channels — stale/contaminated capture`);
  if (channels.length !== wavs.length) errs.push(`meta.channels (${channels.length}) ≠ audio tracks (${wavs.length})`);
  console.log(`  audio: ${wavs.length} wav(s) (${remoteChannels.length} remote + ${channels.length - remoteChannels.length} mic); topology=${meta.topology}; platform=${meta.platform}`);
} else {
  errs.push('no stream.capture and no audio/ — nothing to validate');
}

// ── verdict ──
warns.forEach((w) => console.log(`  ⚠ ${w}`));
if (errs.length) { console.error(`\n❌ NOT capture.v1-conformant (${errs.length}):`); errs.forEach((e) => console.error(`   - ${e}`)); process.exit(1); }
console.log(`\n✅ ${dir} meets capture.v1`);
