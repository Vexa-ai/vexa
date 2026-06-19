#!/usr/bin/env node
// capture — RAW-SIGNAL HEALTH probe for a tape. Most "it's flaky / no transcript"
// reports are NOT pipeline bugs — they're a sick capture: the remote tab-audio
// channel (ch999) never got minted (toolbar click lost / tab reload), or it's
// near-silent, or it stalls. The pipeline can only transcribe what it's fed, so
// check the FEED first. Operates on the tape alone (no desktop, no STT, no secrets).
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
const CH = { 999: 'remote-mix', 1000: 'local-mic' };   // the two capture.v1 audio channels

function decode(buf) {
  if (buf.length < 12) return null;
  const raw = buf.readInt32LE(0), named = raw < 0, idx = named ? (raw & 0x7fffffff) : raw;
  let p = named ? 16 + (((buf.readInt32LE(12)) + 3) & ~3) : 12;
  const n = Math.max(0, (buf.length - p) >> 2);
  let s = 0; for (let i = 0; i < n; i++) { const v = buf.readFloatLE(p + i * 4); s += v * v; }
  return { idx, n, rms: n ? Math.sqrt(s / n) : 0 };
}

async function main() {
  const rl = readline.createInterface({ input: fs.createReadStream(TAPE), crlfDelay: Infinity });
  let header = null, other = 0;
  const ch = new Map();           // idx → {f, smp, rmsSum, dropped, lastT, gaps[]}
  const hk = {}; const spk = new Set();
  for await (const line of rl) {
    if (!line) continue;
    let m; try { m = JSON.parse(line); } catch { continue; }
    if (!header) { header = m; continue; }
    if (m.bin) {
      const d = decode(Buffer.from(m.d, 'base64'));
      if (!d) continue;
      if (d.idx !== 999 && d.idx !== 1000) { other++; continue; }   // recording.v1 chunk / unknown — not capture audio
      let c = ch.get(d.idx);
      if (!c) { c = { f: 0, smp: 0, rmsSum: 0, dropped: 0, lastT: null, maxGap: 0 }; ch.set(d.idx, c); }
      c.f++; c.smp += d.n; c.rmsSum += d.rms; if (d.rms < DROP_RMS) c.dropped++;
      if (c.lastT !== null) c.maxGap = Math.max(c.maxGap, m.t - c.lastT);
      c.lastT = m.t;
    } else {
      try { const e = JSON.parse(m.d); hk[e.kind] = (hk[e.kind] || 0) + 1; if (e.speaker) spk.add(e.speaker); } catch { /* */ }
    }
  }

  console.log(`[capture] ${TAPE}`);
  console.log(`[capture] ${header?.platform}/${header?.native} · started ${header?.startedAt || '?'}`);
  const remote = ch.get(999), mic = ch.get(1000);
  for (const [idx, name] of Object.entries(CH)) {
    const c = ch.get(Number(idx));
    if (!c) { console.log(`  ch${idx} (${name}): — absent —`); continue; }
    const dur = c.smp / RATE, avg = c.rmsSum / c.f, dropPct = Math.round(100 * c.dropped / c.f);
    console.log(`  ch${idx} (${name}): ${c.f}f · ${dur.toFixed(1)}s · avgRMS=${avg.toFixed(4)} · <floor ${dropPct}% · maxGap=${(c.maxGap / 1000).toFixed(1)}s`);
  }
  console.log(`  hints: ${Object.entries(hk).map(([k, v]) => `${k}=${v}`).join(' ') || 'none'}${spk.size ? ` · speakers: ${[...spk].join(', ')}` : ''}`);
  if (other) console.log(`  (${other} non-capture binary frames — recording.v1 chunks, ignored)`);

  // ── verdict ──
  const issues = [];
  if (!remote || remote.f === 0) issues.push('NO REMOTE AUDIO (ch999) — tab-capture never minted (click the Vexa toolbar icon ON the meeting tab; lost on reload)');
  else {
    const avg = remote.rmsSum / remote.f;
    if (avg < DROP_RMS) issues.push(`remote audio near-silent (avgRMS ${avg.toFixed(4)} < ${DROP_RMS}) — captured the wrong stream or muted`);
    if (remote.maxGap >= STALL_MS) issues.push(`remote capture STALLS (gap up to ${(remote.maxGap / 1000).toFixed(1)}s) — tab-capture dropping mid-session`);
  }
  if ((!remote || remote.f === 0) && mic && mic.f > 0) issues.push('only the LOCAL MIC (ch1000) came through — the classic Zoom/Teams "captured 0 streams" symptom');
  const ok = issues.length === 0;
  console.log(`\n${ok ? '✓ CAPTURE HEALTHY' : '✗ CAPTURE UNHEALTHY'}`);
  for (const i of issues) console.log(`  ⚠ ${i}`);
  const r = remote || { f: 0, smp: 0, rmsSum: 0, maxGap: 0 };
  console.log(`\nCAPTURE ${header?.platform}/${header?.native} ch999=${r.f}f/${(r.smp / RATE).toFixed(0)}s/rms${r.f ? (r.rmsSum / r.f).toFixed(3) : '0'} ch1000=${mic ? mic.f : 0}f maxgap=${(r.maxGap / 1000).toFixed(1)}s verdict=${ok ? 'healthy' : 'unhealthy'}`);
  process.exit(ok ? 0 : 1);
}
main().catch((e) => { console.error('[capture]', e.message); process.exit(2); });
