#!/usr/bin/env node
// capture — RAW-SIGNAL HEALTH probe for a tape. Most "it's flaky / no transcript"
// reports are NOT pipeline bugs — they're a sick FEED: the audio never arrived, is
// near-silent, or stalls. The pipeline can only transcribe what it's fed, so check
// the FEED first. Operates on the tape alone (no desktop, no STT, no secrets).
//
// LANE-AWARE — the two capture topologies have DIFFERENT channel maps, so health
// must be judged per lane (judging gmeet on ch999 was the old bug — gmeet has no ch999):
//   • mixed lane (zoom/teams/youtube): one diarized tab-audio channel ch999 + mic ch1000.
//   • gmeet lane (google_meet): PER-PARTICIPANT channels 0..N (named) + mic ch1000;
//     ch999 is ALWAYS absent here, by design.
//
//   node capture.mjs <tape.jsonl>
//   DROP_RMS=0.006   silence floor (matches the pipeline's drop gate)
//   STALL_MS=3000    inter-frame gap above which capture is "stalled"
import fs from 'node:fs';
import readline from 'node:readline';

const TAPE = process.argv[2];
if (!TAPE) { console.error('usage: capture.mjs <tape.jsonl>'); process.exit(1); }
const DROP_RMS = Number(process.env.DROP_RMS || 0.006);
const STALL_MS = Number(process.env.STALL_MS || 3000);
const RATE = 16000;
const MIX = 999, MIC = 1000;   // reserved capture.v1 channels; idx in [0,998] is a gmeet participant, idx>1000 is a REC1 chunk

function decode(buf) {
  if (buf.length < 12) return null;
  const raw = buf.readInt32LE(0), named = raw < 0, idx = named ? (raw & 0x7fffffff) : raw;
  let p = named ? 16 + (((buf.readInt32LE(12)) + 3) & ~3) : 12;
  const n = Math.max(0, (buf.length - p) >> 2);
  let s = 0; for (let i = 0; i < n; i++) { const v = buf.readFloatLE(p + i * 4); s += v * v; }
  return { idx, n, rms: n ? Math.sqrt(s / n) : 0 };
}

// Aggregate a list of [idx, c] channels into one rollup (used for the multi-channel gmeet remote).
const agg = (chans) => {
  const r = { f: 0, smp: 0, rmsSum: 0, dropped: 0, maxGap: 0 };
  for (const [, c] of chans) { r.f += c.f; r.smp += c.smp; r.rmsSum += c.rmsSum; r.dropped += c.dropped; r.maxGap = Math.max(r.maxGap, c.maxGap); }
  return { ...r, avg: r.f ? r.rmsSum / r.f : 0, dur: r.smp / RATE };
};
const fmt = (c) => `${c.f}f · ${(c.smp / RATE).toFixed(1)}s · avgRMS=${(c.rmsSum / c.f).toFixed(4)} · <floor ${Math.round(100 * c.dropped / c.f)}% · maxGap=${(c.maxGap / 1000).toFixed(1)}s`;

async function main() {
  const rl = readline.createInterface({ input: fs.createReadStream(TAPE), crlfDelay: Infinity });
  let header = null, other = 0;
  const ch = new Map();           // idx → {f, smp, rmsSum, dropped, lastT, maxGap}
  const hk = {}; const spk = new Set();
  for await (const line of rl) {
    if (!line) continue;
    let m; try { m = JSON.parse(line); } catch { continue; }
    if (!header) { header = m; continue; }
    if (m.bin) {
      const d = decode(Buffer.from(m.d, 'base64'));
      if (!d) continue;
      if (d.idx > MIC) { other++; continue; }   // REC1 chunk / unknown (REC_MAGIC ≫ 1000) — not capture audio
      let c = ch.get(d.idx);
      if (!c) { c = { f: 0, smp: 0, rmsSum: 0, dropped: 0, lastT: null, maxGap: 0 }; ch.set(d.idx, c); }
      c.f++; c.smp += d.n; c.rmsSum += d.rms; if (d.rms < DROP_RMS) c.dropped++;
      if (c.lastT !== null) c.maxGap = Math.max(c.maxGap, m.t - c.lastT);
      c.lastT = m.t;
    } else {
      try { const e = JSON.parse(m.d); hk[e.kind] = (hk[e.kind] || 0) + 1; if (e.speaker) spk.add(e.speaker); } catch { /* */ }
    }
  }

  const platform = header?.platform || '?';
  const isGmeet = platform === 'google_meet';
  const mic = ch.get(MIC);
  const participants = [...ch.entries()].filter(([i]) => i >= 0 && i <= 998).sort((a, b) => a[0] - b[0]);
  const remoteChans = isGmeet ? participants : (ch.get(MIX) ? [[MIX, ch.get(MIX)]] : []);

  console.log(`[capture] ${TAPE}`);
  console.log(`[capture] ${platform}/${header?.native} · ${isGmeet ? 'gmeet lane — per-participant ch0..N' : 'mixed lane — ch999'} · started ${header?.startedAt || '?'}`);
  if (isGmeet) {
    if (participants.length === 0) console.log('  participants (ch0..N): — none —');
    else for (const [idx, c] of participants) console.log(`  participant ch${idx}: ${fmt(c)}`);
  } else {
    const r = ch.get(MIX);
    console.log(`  ch999 (remote-mix): ${r ? fmt(r) : '— absent —'}`);
  }
  console.log(`  ch1000 (local-mic): ${mic ? fmt(mic) : '— absent —'}`);
  console.log(`  hints: ${Object.entries(hk).map(([k, v]) => `${k}=${v}`).join(' ') || 'none'}${spk.size ? ` · speakers: ${[...spk].join(', ')}` : ''}`);
  if (other) console.log(`  (${other} non-capture binary frames — recording.v1 chunks, ignored)`);

  // ── verdict (lane-aware): healthy · inconclusive · unhealthy ──
  const issues = [];
  const remote = agg(remoteChans);
  const joined = hk['speaker-joined'] || 0;   // distinct joins the page saw (self counts as ≤1; ≥2 ⇒ someone else was there)
  let verdict = 'healthy';
  if (remoteChans.length === 0 || remote.f === 0) {
    if (isGmeet) {
      // A tape alone can't tell SOLO (gmeet not exercised) from CAPTURE-FAILED with certainty —
      // "You" + your tile name both look like speakers. Report the evidence; don't assert. The
      // join count is the best signal: ≤1 ⇒ likely solo; ≥2 ⇒ someone else was present but silent.
      const ev = `hints: speaker-joined=${joined}, active-speaker=${hk['active-speaker'] || 0}, speakers=[${[...spk].join(', ') || '—'}]`;
      issues.push(joined >= 2
        ? `no per-participant remote audio, but ≥2 joins seen — gmeet per-participant capture likely FAILED. ${ev}. Re-census after a re-test.`
        : `no per-participant remote audio — likely a SOLO meeting (only your mic); gmeet was NOT exercised. ${ev}. Re-test with another participant SPEAKING to validate gmeet.`);
      verdict = 'inconclusive';   // capture machinery isn't proven broken OR working — the tape didn't exercise it
    } else {
      issues.push('NO REMOTE AUDIO (ch999) — tab-capture never minted (click the Vexa toolbar icon ON the meeting tab; lost on reload)'); verdict = 'unhealthy';
    }
  } else {
    if (remote.avg < DROP_RMS) { issues.push(`remote audio near-silent (avgRMS ${remote.avg.toFixed(4)} < ${DROP_RMS}) — muted / wrong stream / video not playing`); verdict = 'unhealthy'; }
    if (remote.maxGap >= STALL_MS) { issues.push(`remote capture STALLS (gap up to ${(remote.maxGap / 1000).toFixed(1)}s) — ${isGmeet ? 'a participant element dropped' : 'tab-capture dropping mid-session'}`); verdict = 'unhealthy'; }
  }
  const LABEL = { healthy: '✓ CAPTURE HEALTHY', inconclusive: '⚠ CAPTURE INCONCLUSIVE', unhealthy: '✗ CAPTURE UNHEALTHY' };
  console.log(`\n${LABEL[verdict]}`);
  for (const i of issues) console.log(`  ⚠ ${i}`);
  const remoteTag = isGmeet
    ? `participants=${participants.length} remote=${remote.f}f/${remote.dur.toFixed(0)}s/rms${remote.f ? remote.avg.toFixed(3) : '0'}`
    : `ch999=${remote.f}f/${remote.dur.toFixed(0)}s/rms${remote.f ? remote.avg.toFixed(3) : '0'}`;
  console.log(`\nCAPTURE ${platform}/${header?.native} lane=${isGmeet ? 'gmeet' : 'mixed'} ${remoteTag} ch1000=${mic ? mic.f : 0}f maxgap=${(remote.maxGap / 1000).toFixed(1)}s verdict=${verdict}`);
  process.exit(verdict === 'healthy' ? 0 : 1);
}
main().catch((e) => { console.error('[capture]', e.message); process.exit(2); });
