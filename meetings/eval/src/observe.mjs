// observe.mjs — a LIVE TRANSCRIPT DYNAMICS harness.
//
// Taps the desktop gateway /ws (the same stream the extension sidepanel renders)
// and prints what a human watching the live transcript actually experiences:
//   ░ forming  — a pending (italic, not-yet-confirmed) segment first appears
//   ░ …churn   — that pending text changed again before confirming (flicker)
//   █ CONFIRM  — the segment locked in (with word count, churn count, latency)
// plus a periodic readout of the OVERSEGMENTATION signal (how many confirmed
// segments are tiny ≤3-word fragments — pyannote cutting mid-utterance) and the
// warm-up (time from first frame to first confirm). Raw stream is recorded to a
// .jsonl so a session can be replayed/analysed offline.
//
// Usage (from the repo root, while a session is live):
//   pnpm observe <platform> <native_meeting_id>     e.g. pnpm observe youtube 53yPfrqbpkE
//   pnpm observe                                    watch ALL sessions
// (or `./bin/eval.sh observe …` from meetings/eval). Uses Node's built-in global
// WebSocket (Node 21+) — no `ws` dependency, so it lives in @vexa/eval cleanly.
import { writeFileSync, appendFileSync } from 'node:fs';

const GATEWAY = process.env.GATEWAY || 'ws://localhost:8056/ws';
const PLATFORM = process.argv[2] || 'youtube';
const NATIVE = process.argv[3] || '';
const REC = `/tmp/transcript-rec-${PLATFORM}-${NATIVE || 'any'}.jsonl`;
writeFileSync(REC, '');

const t0 = Date.now();
const T = () => ((Date.now() - t0) / 1000).toFixed(1).padStart(6);
const seg = new Map();              // segment_id → { firstMs, confirmedMs, text, churn }
const lastPend = new Map();         // speaker → { text } — for the lost-transcript monitor
let firstFrameMs = null, firstConfirmMs = null, lastEnd = null;

const ws = new WebSocket(GATEWAY);
ws.onopen = () => {
  // Empty meetings list → the gateway sends ALL broadcasts (keys.size===0), so the
  // harness catches whatever session you start next, any video id.
  const meetings = NATIVE ? [{ platform: PLATFORM, native_id: NATIVE }] : [];
  ws.send(JSON.stringify({ action: 'subscribe', meetings }));
  console.log(`[observe] watching ${NATIVE ? PLATFORM + '/' + NATIVE : 'ALL meetings'} · ${GATEWAY} · rec → ${REC}\n`);
};
ws.onerror = (e) => console.log('[observe] ws error:', e?.message || 'connection failed — is the desktop up on :8056?');
ws.onclose = () => console.log('[observe] ws closed');
ws.onmessage = (ev) => {
  let m; try { m = JSON.parse(ev.data); } catch { return; }
  if (m.type !== 'transcript') return;
  appendFileSync(REC, JSON.stringify({ ms: Date.now() - t0, speaker: m.speaker, confirmed: m.confirmed, pending: m.pending }) + '\n');
  if (firstFrameMs === null) firstFrameMs = Date.now();
  const conf = m.confirmed || [], pend = m.pending || [];
  for (const s of pend) {
    const e = seg.get(s.segment_id);
    if (!e) { seg.set(s.segment_id, { firstMs: Date.now(), text: s.text, churn: 0 }); console.log(`${T()}  \x1b[2m░ forming [${m.speaker}] "${s.text}"\x1b[0m`); }
    else if (e.text !== s.text) { e.churn++; e.text = s.text; console.log(`${T()}  \x1b[2m░ …churn  [${m.speaker}] "${s.text}"\x1b[0m`); }
  }
  for (const s of conf) {
    const e = seg.get(s.segment_id) || { firstMs: Date.now(), churn: 0 };
    if (!e.confirmedMs) {
      e.confirmedMs = Date.now(); e.text = s.text; seg.set(s.segment_id, e);
      if (firstConfirmMs === null) { firstConfirmMs = Date.now(); console.log(`\n\x1b[33m>>> first CONFIRM at +${((firstConfirmMs - firstFrameMs) / 1000).toFixed(1)}s — that's the warm-up a human waits through\x1b[0m\n`); }
      const words = (s.text || '').trim().split(/\s+/).filter(Boolean).length;
      const st = +(s.start ?? 0), en = +(s.end ?? 0);
      const gap = lastEnd === null ? 0 : (st - lastEnd); lastEnd = en;
      const flag = (gap >= 0 && gap < 0.4 && m.speaker && /^seg_\d+$/.test(m.speaker)) ? ' \x1b[31m⟵split?\x1b[0m' : '';
      console.log(`${T()}  \x1b[32m█\x1b[0m [${m.speaker}] ${st.toFixed(1)}–${en.toFixed(1)}s (${(en - st).toFixed(1)}s ${words}w gap=${gap.toFixed(1)}s) "${s.text}"${flag}`);
    }
  }
  // Lost-transcript monitor: a viewer saw pending text, then the draft was CLEARED
  // without a confirm carrying it ({confirmed:[], pending:[]} after a non-empty pending).
  if (pend.length) lastPend.set(m.speaker, { text: pend.map((p) => p.text).join(' ') });
  else if (conf.length) lastPend.delete(m.speaker);              // promoted to confirmed → fine
  else if (lastPend.has(m.speaker)) {                            // clearPending with nothing confirmed → LOST
    console.log(`${T()}  \x1b[41m⚠ LOST\x1b[0m [${m.speaker}] pending cleared, never confirmed: "${lastPend.get(m.speaker).text}"`);
    lastPend.delete(m.speaker);
  }
};

setInterval(() => {
  const all = [...seg.values()].filter((s) => s.confirmedMs);
  if (!all.length) return;
  const w = all.map((s) => (s.text || '').trim().split(/\s+/).filter(Boolean).length);
  const avg = (w.reduce((a, b) => a + b, 0) / w.length).toFixed(1);
  const tiny = w.filter((x) => x <= 3).length;
  const churn = (all.reduce((a, s) => a + s.churn, 0) / all.length).toFixed(1);
  console.log(`\n\x1b[36m──[${T()}] confirmed=${all.length}  avg=${avg}w  tiny(≤3w)=${tiny} (${Math.round((100 * tiny) / all.length)}%) ← oversegmentation  avgChurn=${churn}──\x1b[0m\n`);
}, 12000);

process.on('SIGINT', () => { console.log(`\n[observe] ${seg.size} segments seen · raw → ${REC}`); process.exit(0); });
