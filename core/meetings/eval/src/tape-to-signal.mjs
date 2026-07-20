#!/usr/bin/env node
// tape-to-signal — the bridge between the two loops.
//
// The EXTERNAL loop (human in it) runs the extension into the desktop and records a raw tape:
// the verbatim capture.v1 ingest stream (`VEXA_RECORD_TAPE=<dir>` in services/desktop). That tape
// can only be replayed back INTO a running desktop, so a session a human just judged could not be
// handed to the offline scorers — which is why fixtures had to be scraped out of bot containers.
//
// The INTERNAL loop (no human) drives the REAL lanes in-process off a `captured-signal.v1`
// session: decoded audio frames + speaker hints, the shape services/bot/src/replay*.test.ts and
// quality*.test.ts already consume.
//
// This converts the first into the second, so every human-witnessed session becomes a replayable,
// scoreable fixture for free.
//
//   node src/tape-to-signal.mjs <tape.jsonl> [out.captured-signal.jsonl]
//
// Note on segmenter cuts: the bot's recorder stores production's own boundary events; a desktop
// tape is recorded BEFORE the pipeline, so it has none. That is not a loss — the offline harness
// can run the REAL PyannoteSegmenter (the model loads locally in ~2s), which is strictly more
// faithful than replaying someone else's cuts.
import { readFileSync, writeFileSync } from 'node:fs';
// eval is deliberately NOT a workspace member (its scripts run against arbitrary checkouts), so
// the codec brick is imported by path, same as the other eval scripts import bricks.
import { decodeAudioFrame, decodeEvent } from '../../modules/capture-codec/dist/index.js';

/**
 * Convert a desktop tape into a `captured-signal.v1` session.
 *
 * `headSec` cuts the result to the first N seconds of capture. A tape grows for as long as the
 * desktop runs, so a session someone measured — and built a costly single-pass reference for — is
 * a PREFIX of the tape that outlived it. The cut is what lets that exact window be re-derived from
 * the tape instead of kept as a loose side-artifact.
 */
export function tapeToSignal(tapePath, { headSec = Infinity } = {}) {
  const lines = readFileSync(tapePath, 'utf8').split('\n').filter(Boolean);
  const head = JSON.parse(lines[0]);

  // The tape stamps a RELATIVE ms offset (`t`); captured-signal.v1 frames carry absolute capture
  // time, and the hint↔audio clock must share one epoch or the binder matches nothing. The frames'
  // own decoded `ts` IS that epoch, so it is preferred; `t` only anchors events that lack one.
  const startedAtMs = Date.parse(head.startedAt ?? '') || 0;

  const records = [];
  let frames = 0, hints = 0, skipped = 0, firstTs = 0;

  for (const line of lines.slice(1)) {
    let rec;
    try { rec = JSON.parse(line); } catch { skipped++; continue; }
    if (rec.bin) {
      const buf = Buffer.from(rec.d, 'base64');
      // The tape is the VERBATIM ingest stream, so binary messages are audio frames AND recording
      // chunks. A recording chunk fed to decodeAudioFrame yields plausible-looking garbage (its
      // 'REC1' magic reads as a speakerIndex, media bytes as denormal timestamps), so discriminate
      // on the magic first.
      if (buf.byteLength >= 4 && buf.readInt32LE(0) === 0x52454331) { skipped++; continue; }
      const f = decodeAudioFrame(buf.buffer, buf.byteOffset, buf.byteLength);
      if (!f || !Number.isFinite(f.ts) || f.ts <= 0) { skipped++; continue; }
      if (!firstTs) firstTs = f.ts;
      let rms = 0;
      for (let i = 0; i < f.samples.length; i++) rms += f.samples[i] * f.samples[i];
      rms = Math.sqrt(rms / Math.max(1, f.samples.length));
      records.push({
        seq: frames++,
        ts: f.ts,
        speakerIndex: f.speakerIndex,
        ...(f.speakerName ? { speakerName: f.speakerName } : {}),
        pcm: Buffer.from(f.samples.buffer, f.samples.byteOffset, f.samples.byteLength).toString('base64'),
        pcm_len: f.samples.length,
        rms: Number(rms.toFixed(6)),
        lane: head.platform === 'google_meet' ? 'gmeet' : 'mixed',
      });
    } else {
      const ev = decodeEvent(rec.d);
      if (!ev || ev.kind !== 'active-speaker' || !ev.speaker) { skipped++; continue; }
      records.push({
        type: 'hint',
        t: ev.ts || (startedAtMs + rec.t),
        name: ev.speaker,
        isEnd: ev.text === 'end' || ev.isEnd === true,
        lane: 'mixed',
      });
      hints++;
    }
  }

  records.sort((a, b) => (a.ts ?? a.t) - (b.ts ?? b.t));

  const cutAt = firstTs + headSec * 1000;
  const kept = records.filter((r) => (r.ts ?? r.t) <= cutAt);
  const dropped = records.length - kept.length;
  // seq is a position in the session, so it renumbers with the cut rather than carrying the tape's.
  let seq = 0;
  for (const r of kept) if (r.pcm) r.seq = seq++;

  const header = {
    type: 'captured_signal_header',
    v: 1,
    platform: head.platform,
    native_meeting_id: head.native,
    language: head.language ?? null,
    lane: head.platform === 'google_meet' ? 'gmeet' : 'mixed',
    sample_rate: 16000,
    started_at: head.startedAt ?? new Date(firstTs || Date.now()).toISOString(),
    source: 'desktop-tape',
    ...(Number.isFinite(headSec) ? { head_sec: headSec } : {}),
  };

  return { header, records: kept, stats: { frames: seq, hints: kept.filter((r) => r.type === 'hint').length, skipped, dropped } };
}

export function writeSession(out, header, records) {
  writeFileSync(out, [header, ...records].map((r) => JSON.stringify(r)).join('\n') + '\n');
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const [, , tapePath, outArg] = process.argv;
  if (!tapePath) {
    console.error('usage: tape-to-signal.mjs <tape.jsonl> [out.captured-signal.jsonl]   (env HEAD_SEC cuts the window)');
    process.exit(1);
  }
  const out = outArg ?? tapePath.replace(/\.jsonl$/, '') + '.captured-signal.jsonl';
  const { header, records, stats } = tapeToSignal(tapePath, { headSec: Number(process.env.HEAD_SEC ?? Infinity) });
  writeSession(out, header, records);
  const audioSec = records.filter((r) => r.pcm).reduce((n, r) => n + r.pcm_len / 16000, 0);
  const ts = records.filter((r) => r.pcm).map((r) => r.ts);
  const wallSec = ts.length ? (Math.max(...ts) - Math.min(...ts)) / 1000 : 0;
  console.log(out);
  console.log(`  ${stats.frames} frames · ${stats.hints} hints · ${stats.skipped} skipped` +
    (stats.dropped ? ` · ${stats.dropped} after the ${process.env.HEAD_SEC}s cut` : ''));
  console.log(`  ${audioSec.toFixed(1)}s audio over ${wallSec.toFixed(1)}s wall — capture duty cycle ${wallSec ? (audioSec / wallSec * 100).toFixed(1) : '—'}%`);
}
