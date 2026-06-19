#!/usr/bin/env node
// analyze — score a session's CONFIRMED transcript for segmentation + attribution
// health. Pulls from the desktop gateway (works on a LIVE session OR a REPLAYED
// tape — same scorer either way), and reports:
//   • per-speaker turn counts + unnamed seg_N (attribution gaps)
//   • short turns ≤3w — LEGIT in a dynamic call, reported but NOT penalized
//   • the REAL oversegmentation signals:
//       ✂ mid-utterance cuts — consecutive SAME-speaker turns, sub-GAP gap, where the
//         first didn't end on terminal punctuation (pyannote split one utterance)
//       ⊕ boundary-word dups  — last word of a turn == first word of the next, same
//         speaker (Whisper re-transcribed the boundary across a cut)
// Honors "false-positives-ok, false-negatives-not": it flags OVER-cutting and never
// penalizes a legit short turn. Final SCORE line is grep-friendly for before/after.
//
//   node analyze.mjs <platform> <native_meeting_id>      e.g. analyze zoom 89237402037
//   GATEWAY=http://localhost:8056   GAP=0.5   (max same-speaker gap counted as a cut)
const GATEWAY = (process.env.GATEWAY || 'http://localhost:8056').replace(/\/+$/, '');
const PLATFORM = process.argv[2], NATIVE = process.argv[3];
const GAP = Number(process.env.GAP || 0.5);
if (!PLATFORM || !NATIVE) { console.error('usage: analyze.mjs <platform> <native_meeting_id>'); process.exit(1); }

const terminal = (t) => /[.?!]$/.test((t || '').trim()) && !/(\.\.\.|…)$/.test((t || '').trim());
const words = (t) => (t || '').trim().toLowerCase().replace(/[^\p{L}\p{N}\s']/gu, ' ').split(/\s+/).filter(Boolean);
const trunc = (t, e) => { t = (t || '').trim(); return t.length <= 44 ? t : (e === -1 ? '…' + t.slice(-42) : t.slice(0, 42) + '…'); };

let res;
try { res = await fetch(`${GATEWAY}/transcripts/${PLATFORM}/${encodeURIComponent(NATIVE)}`); }
catch (e) { console.error(`[analyze] gateway ${GATEWAY} unreachable — is the desktop up? (${e.message})`); process.exit(1); }
const d = await res.json();
const segs = (d.segments || []).slice().sort((a, b) => (a.start || 0) - (b.start || 0));
if (!segs.length) { console.log(`[analyze] no confirmed segments for ${PLATFORM}/${NATIVE}`); process.exit(0); }

const by = {}; let short = 0, segN = 0;
for (const s of segs) { const sp = s.speaker || '?'; by[sp] = (by[sp] || 0) + 1; if (words(s.text).length <= 3) short++; if (/^seg_\d+$/.test(sp)) segN++; }
let midcut = 0, dup = 0; const ex = [];
for (let i = 1; i < segs.length; i++) {
  const p = segs[i - 1], c = segs[i], same = p.speaker === c.speaker, g = (c.start || 0) - (p.end || 0);
  if (same && g < GAP && !terminal(p.text)) { midcut++; if (ex.length < 10) ex.push(`  ✂ [${c.speaker}] "${trunc(p.text)}" ⟶ "${trunc(c.text)}"  gap=${g.toFixed(1)}s`); }
  const pw = words(p.text), cw = words(c.text);
  if (same && pw.length && cw.length && pw[pw.length - 1] === cw[0]) { dup++; if (ex.length < 16) ex.push(`  ⊕ dup("${cw[0]}") [${c.speaker}] "…${trunc(p.text, -1)}" ⟶ "${trunc(c.text)}"`); }
}
const dur = ((segs.at(-1).end || 0) - (segs[0].start || 0)).toFixed(0);
console.log(`[analyze] ${PLATFORM}/${NATIVE} · ${segs.length} confirmed segments · ${dur}s`);
console.log(`speakers: ${Object.entries(by).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k}=${v}`).join('  ')}`);
console.log(`unnamed seg_N=${segN}    short≤3w=${short} (${Math.round(100 * short / segs.length)}%, legit — not penalized)`);
console.log(`REAL oversegmentation →  ✂ mid-utterance cuts=${midcut}   ⊕ boundary dups=${dup}   (${Math.round(100 * (midcut + dup) / segs.length)}% of segments)`);
if (ex.length) { console.log('\nexamples:'); ex.forEach((e) => console.log(e)); }
console.log(`\nSCORE ${PLATFORM}/${NATIVE} segments=${segs.length} segN=${segN} midcut=${midcut} dup=${dup} short=${short}`);
