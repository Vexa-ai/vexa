#!/usr/bin/env node
// drive — the rotation + overlap engine. Drives every ADMITTED speaker bot,
// playing cached TTS clips into the live meeting via the production /speak API on a
// controlled timeline, and logs ground truth (who spoke when) to truth.jsonl for
// the scorer. Platform-agnostic (PLATFORM=teams|zoom|google_meet).
//
// ── TEST KNOBS (all env; defaults reproduce the benchmarked rig) ──────────────
//   WHO / HOW MANY : a speaker is in the run iff its TOK_<key> is set (A B V C D E F G H).
//   SPEECH LENGTH  : LEN_MED (s, 11) · LEN_SD (lognormal σ, 0.65) · LEN_MIN/LEN_MAX (2/30)
//                    → set at corpus-generation time (re-gen to change).
//   OVERLAP        : GAP_MEAN (s, 0.5 — lower/negative = MORE overlap) · GAP_SD (0.8)
//                    · GAP_MIN/GAP_MAX clamp (-1.5/+2.5).  gap<0 = two DIFFERENT
//                    speakers overlap; gap>0 = a pause.
//   RUN            : DURATION_S (900) · NATIVE_ID · PLATFORM · VEXA_BASE
//
//   source secrets.env first. Bots must already be admitted (run launch.mjs).
import fs from 'fs';
import path from 'path';
import { BASE, PLATFORM, NATIVE, SPEAKERS, sleep, hdr, activeKeys, requireConfig } from './speakers.mjs';
import { loadOrBuildCache, prepare } from './corpus.mjs';

requireConfig();
const HERE = path.dirname(new URL(import.meta.url).pathname);
const TRUTH = process.env.TRUTH_LOG || path.join(HERE, '..', 'truth.jsonl');
const DURATION_S = Number(process.env.DURATION_S || 900);
const RATE = 16000;

const GAP_MEAN = Number(process.env.GAP_MEAN ?? 0.5);
const GAP_SD = Number(process.env.GAP_SD ?? 0.8);
const GAP_MIN = Number(process.env.GAP_MIN ?? -1.5);
const GAP_MAX = Number(process.env.GAP_MAX ?? 2.5);

const rnd = (a) => a[Math.floor(Math.random() * a.length)];
const gauss = () => { let u = 0, v = 0; while (!u) u = Math.random(); while (!v) v = Math.random(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); };

async function post(p) {
  const r = await fetch(`${BASE}/bots/${PLATFORM}/${NATIVE}/speak`, {
    method: 'POST', headers: hdr(p.s.token),
    body: JSON.stringify({ audio_base64: p.b64, format: 'wav', sample_rate: RATE }),
  });
  if (!r.ok) console.log(`   ⚠ /speak ${p.s.en} ${r.status}: ${(await r.text()).slice(0, 80)}`);
}

// Next speaker — never the same as the last, so every overlap is two DIFFERENT people.
function pick(act, lastKey) {
  let pool = SPEAKERS.filter((s) => act.has(s.key));
  if (pool.length > 1 && lastKey) pool = pool.filter((s) => s.key !== lastKey);
  return pool.length ? rnd(pool) : null;
}
async function pickReady(lastKey) {
  for (;;) { const a = await activeKeys(); const s = pick(a, lastKey); if (s) return s; await sleep(600); }
}

async function main() {
  const t0 = Date.now();
  try { fs.writeFileSync(TRUTH, ''); } catch { /* */ }
  await loadOrBuildCache();
  const act0 = await activeKeys();
  console.log(`[drive] ${PLATFORM}/${NATIVE} · gap ${GAP_MIN}..${GAP_MAX}s(mean ${GAP_MEAN >= 0 ? '+' : ''}${GAP_MEAN}±${GAP_SD}) · ${DURATION_S}s · active=[${[...act0].join(',')}]`);
  if (!act0.size) console.log('[drive] no admitted bots yet — waiting (run launch.mjs, or admit in the meeting UI)…');

  let next = prepare(await pickReady(null));
  let lastKey = null;
  let scheduledMs = Date.now();                       // MASTER CLOCK — absolute start of each turn
  while ((Date.now() - t0) / 1000 < DURATION_S) {
    const w = scheduledMs - Date.now(); if (w > 0) await sleep(w);
    const startMs = Date.now();
    // FIRE-AND-FORGET: /speak has its own upload+playback latency; awaiting it would
    // land that latency between turns and eat the overlap. The master clock drives the
    // timeline; both turns carry the same latency, so scheduled overlap is preserved.
    post(next).catch((e) => console.log(`   ⚠ /speak ${next.s.en}: ${e.message}`));
    lastKey = next.s.key;
    try { fs.appendFileSync(TRUTH, JSON.stringify({ startMs, endMs: startMs + next.durSec * 1000, ru: next.s.label, en: next.s.en }) + '\n'); } catch { /* */ }
    const gap = Math.max(GAP_MIN, Math.min(GAP_MAX, GAP_MEAN + gauss() * GAP_SD));
    scheduledMs = startMs + next.durSec * 1000 + gap * 1000;   // absolute start of the NEXT turn
    console.log(`  t=${((Date.now() - t0) / 1000).toFixed(1)}s  ${next.s.en} (${next.s.label})  ${next.durSec.toFixed(1)}s  gap=${gap >= 0 ? '+' : ''}${gap.toFixed(1)}s${gap < 0 ? ' ⟂overlap' : ' ·pause'}`);
    next = prepare(await pickReady(lastKey));         // instant (cached) + refresh active set; absorbed by the wait
  }
  console.log('[drive] done');
}
main().catch((e) => { console.error(e); process.exit(1); });
