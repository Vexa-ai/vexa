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
const ARGV = process.argv.slice(2);
// --flag-issues (O-TEL-3): also EMIT flagged-issue.v1 records for mis-attr / overseg over a
// threshold, so the auto-flagger feeds the flag store → the O-TEL-2 replay. Off by default
// (pure scorer). --flag-out <file> writes the JSON array; else it prints to stdout (FLAG_… section).
const FLAG_ISSUES = ARGV.includes('--flag-issues');
const flagOutIx = ARGV.indexOf('--flag-out');
const FLAG_OUT = flagOutIx >= 0 ? ARGV[flagOutIx + 1] : null;
// SIGNAL link the emitted issues route to (the captured-signal.v1 / tape the live signal is in).
const FLAG_SIGNAL = process.env.FLAG_SIGNAL || null;
// TRACE link (O-OBS-1 ↔ O-TEL-3): the meeting's distributed trace_id, stamped on each emitted
// issue so a flagged bug pulls its full cross-system trace AND ties to the captured-signal.v1
// header carrying the SAME trace_id. Sourced from the bot's X-Trace-Id at capture time.
const FLAG_TRACE = process.env.FLAG_TRACE || null;
const positional = ARGV.filter((a) => !a.startsWith('--') && a !== FLAG_OUT);
const PLATFORM = positional[0], NATIVE = positional[1];
const GAP = Number(process.env.GAP || 0.5);
// Auto-flag threshold: emit issues only once the count crosses it (a single stray cut isn't a bug).
const FLAG_MIN = Number(process.env.FLAG_MIN || 1);
if (!PLATFORM || !NATIVE) { console.error('usage: analyze.mjs <platform> <native_meeting_id> [--flag-issues] [--flag-out <file>]'); process.exit(1); }

const terminal = (t) => /[.?!]$/.test((t || '').trim()) && !/(\.\.\.|…)$/.test((t || '').trim());
const words = (t) => (t || '').trim().toLowerCase().replace(/[^\p{L}\p{N}\s']/gu, ' ').split(/\s+/).filter(Boolean);
const trunc = (t, e) => { t = (t || '').trim(); return t.length <= 44 ? t : (e === -1 ? '…' + t.slice(-42) : t.slice(0, 42) + '…'); };

// MIS-ATTRIBUTION oracle (offline, no ground truth): the eval clips self-ID ("This is
// Boris", "Boris here") — so a segment whose CONTENT names speaker X while its LABEL is a
// DIFFERENT known speaker is a provable wrong attribution. (Same content check judge.py
// runs against truth, but here from the transcript alone.) ALIAS folds common STT mishears.
const NAMES = ['anna', 'boris', 'vera', 'galina', 'egor', 'zhanna', 'zoya', 'igor', 'dmitry'];
const ALIAS = { zira: 'vera', vela: 'vera', galena: 'galina', dimitri: 'dmitry', dimitry: 'dmitry', yegor: 'egor', zoia: 'zoya', ana: 'anna', jana: 'zhanna', jeanne: 'zhanna', soya: 'zoya', dtree: 'dmitry', etree: 'dmitry' };
const canon = (w) => { w = (w || '').toLowerCase(); w = ALIAS[w] || w; return NAMES.includes(w) ? w : null; };
// A self-ID is an explicit self-INTRODUCTION only ("This is Boris", "Boris here",
// "I'm Boris", "Boris speaking"). A mere MENTION of someone else ("Boris thinks…",
// "Boris would add…", "one from Anna") is NOT a self-ID — counting it would flag a
// correct label as mis-attributed (a false alarm the gate can't afford).
const selfId = (t) => {
  const s = (t || '').toLowerCase().slice(0, 40);
  const m = s.match(/\b(?:this is|i'?m|i am)\s+([a-z]+)/) || s.match(/\b([a-z]+)\s+(?:here|speaking)\b/);
  return m ? canon(m[1]) : null;
};
const labelName = (sp) => { const m = /^spk[-_ ](.+)$/i.exec(sp || ''); return m ? canon(m[1]) : null; };
const NOISE = process.env.VEXA_NOISE_NAME || '';   // a known noise/silent bot — any segment under its label is a hijack

let d;
// TRANSCRIPT_FILE → score a JSON `{segments:[…]}` dumped from another source (e.g. a STANDALONE
// bot's transcript.v1 redis stream, via read-redis-transcript.mjs) instead of the gateway. Same
// scorer either way. Used by the O6 Meet-leg harness, where the bot publishes to redis (no gateway
// meeting record). Else fetch the gateway as before.
if (process.env.TRANSCRIPT_FILE) {
  const { readFileSync } = await import('node:fs');
  try { d = JSON.parse(readFileSync(process.env.TRANSCRIPT_FILE, 'utf8')); }
  catch (e) { console.error(`[analyze] TRANSCRIPT_FILE ${process.env.TRANSCRIPT_FILE} unreadable (${e.message})`); process.exit(1); }
} else {
  let res;
  // X-API-Key when set → the cloud gateway (api.cloud.vexa.ai) needs it; the open desktop ignores it.
  const _h = process.env.VEXA_API_KEY ? { 'X-API-Key': process.env.VEXA_API_KEY } : {};
  try { res = await fetch(`${GATEWAY}/transcripts/${PLATFORM}/${encodeURIComponent(NATIVE)}`, { headers: _h }); }
  catch (e) { console.error(`[analyze] gateway ${GATEWAY} unreachable (${e.message})`); process.exit(1); }
  d = await res.json();
}
const segs = (d.segments || []).slice().sort((a, b) => (a.start || 0) - (b.start || 0));
if (!segs.length) { console.log(`[analyze] no confirmed segments for ${PLATFORM}/${NATIVE}`); process.exit(0); }

const by = {}; let short = 0, segN = 0;
for (const s of segs) { const sp = s.speaker || '?'; by[sp] = (by[sp] || 0) + 1; if (words(s.text).length <= 3) short++; if (/^seg_\d+$/.test(sp)) segN++; }
let midcut = 0, dup = 0; const ex = [];
// O-TEL-3: collect the OFFENDING segments (not just counts) so --flag-issues can emit
// flagged-issue.v1 records that route to the O-TEL-2 replay. seg = the offending segment.
const overseg = [];   // {seg, why}
for (let i = 1; i < segs.length; i++) {
  const p = segs[i - 1], c = segs[i], same = p.speaker === c.speaker, g = (c.start || 0) - (p.end || 0);
  if (same && g < GAP && !terminal(p.text)) { midcut++; overseg.push({ seg: c, why: `mid-utterance cut after "${trunc(p.text)}" (gap ${g.toFixed(1)}s, no terminal punctuation)` }); if (ex.length < 10) ex.push(`  ✂ [${c.speaker}] "${trunc(p.text)}" ⟶ "${trunc(c.text)}"  gap=${g.toFixed(1)}s`); }
  const pw = words(p.text), cw = words(c.text);
  if (same && pw.length && cw.length && pw[pw.length - 1] === cw[0]) { dup++; overseg.push({ seg: c, why: `boundary-word dup ("${cw[0]}") across a same-speaker cut` }); if (ex.length < 16) ex.push(`  ⊕ dup("${cw[0]}") [${c.speaker}] "…${trunc(p.text, -1)}" ⟶ "${trunc(c.text)}"`); }
}
// Mis-attribution: content self-IDs one speaker, label says another. Hijack: a known
// silent/noise bot's label reaching the transcript at all. Both are intolerable and were
// invisible to the old scorer (it only tallied labels). Loss is the benchmark's job.
let misattr = 0, idd = 0; const maEx = [];
const misattrSegs = [];   // {seg, said} — the offending segments for --flag-issues
for (const s of segs) {
  const said = selfId(s.text); if (!said) continue;
  const lab = labelName(s.speaker); if (lab === null) continue;   // label isn't a known speaker → can't judge
  idd++;
  if (lab !== said) { misattr++; misattrSegs.push({ seg: s, said }); if (maEx.length < 8) maEx.push(`  ✗ [${s.speaker}] but content self-IDs "${said}": "${trunc(s.text)}"`); }
}
const hijack = NOISE ? segs.filter((s) => (s.speaker || '') === NOISE).length : 0;

const dur = ((segs.at(-1).end || 0) - (segs[0].start || 0)).toFixed(0);
console.log(`[analyze] ${PLATFORM}/${NATIVE} · ${segs.length} confirmed segments · ${dur}s`);
console.log(`speakers: ${Object.entries(by).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k}=${v}`).join('  ')}`);
console.log(`unnamed seg_N=${segN}    short≤3w=${short} (${Math.round(100 * short / segs.length)}%, legit — not penalized)`);
console.log(`REAL oversegmentation →  ✂ mid-utterance cuts=${midcut}   ⊕ boundary dups=${dup}   (${Math.round(100 * (midcut + dup) / segs.length)}% of segments)`);
console.log(`ATTRIBUTION →  ✗ mis-attributed (content self-ID ≠ label)=${misattr}/${idd} self-IDing${NOISE ? `   ⚠ hijack[${NOISE}]=${hijack}` : ''}`);
if (ex.length) { console.log('\nexamples:'); ex.forEach((e) => console.log(e)); }
if (maEx.length) { console.log('\nmis-attribution:'); maEx.forEach((e) => console.log(e)); }
console.log(`\nSCORE ${PLATFORM}/${NATIVE} segments=${segs.length} segN=${segN} midcut=${midcut} dup=${dup} short=${short} misattr=${misattr}${NOISE ? ` hijack=${hijack}` : ''}`);
console.log(`(loss is not visible here — run \`benchmark <tape>\` for the full-audio recall/lost-span oracle)`);

// ── O-TEL-3 auto-flagger: emit flagged-issue.v1 records for the offending segments ──
// Derives issue_type from analyze.mjs's OWN oracles (mis-attribution, oversegmentation). Each
// record is system-flagged + carries the SIGNAL link (FLAG_SIGNAL → captured-signal.v1 / tape)
// so it routes to the O-TEL-2 replay. Emitted only once a count crosses FLAG_MIN.
if (FLAG_ISSUES) {
  const sessionStart = d.start_time || new Date().toISOString();
  const signal = FLAG_SIGNAL
    ? (/\.captured-signal\.|captured-signal\.v1/.test(FLAG_SIGNAL) ? { captured_signal: FLAG_SIGNAL } : { tape: FLAG_SIGNAL })
    : undefined;
  const mk = (seg, issue_type, severity, description) => ({
    issue_id: `${PLATFORM}-${NATIVE}-${seg.segment_id || Math.round((seg.start || 0) * 1000)}-${issue_type}`,
    platform: PLATFORM, native_meeting_id: String(NATIVE), session_start_time: sessionStart,
    segment_id: String(seg.segment_id ?? `${seg.speaker}:${Math.round((seg.start || 0) * 1000)}`),
    speaker: seg.speaker || '', text: seg.text || '', start: seg.start || 0, end: seg.end || 0,
    issue_type, severity, description,
    ...(signal ? { signal } : {}),
    ...(FLAG_TRACE ? { trace_id: FLAG_TRACE } : {}),
    flagged_by: 'system', status: 'open', created_at: new Date().toISOString(),
  });
  const out = [];
  if (misattr >= FLAG_MIN) for (const { seg, said } of misattrSegs)
    out.push({ ...mk(seg, 'mis-attribution', 'high', `content self-IDs "${said}" but label is "${seg.speaker}"`), ground_truth: said });
  if (midcut + dup >= FLAG_MIN) for (const { seg, why } of overseg)
    out.push(mk(seg, 'oversegment', 'medium', why));

  const json = JSON.stringify(out, null, 2);
  if (FLAG_OUT) { const { writeFileSync } = await import('node:fs'); writeFileSync(FLAG_OUT, json + '\n'); console.log(`\nFLAG ${out.length} flagged-issue.v1 record(s) → ${FLAG_OUT}`); }
  else { console.log(`\nFLAG ${out.length} flagged-issue.v1 record(s):`); console.log(json); }
}
