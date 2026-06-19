#!/usr/bin/env node
// noise — the FAILURE-MODE injector. Drives ONE bot (NOISE_KEY) to emit brief
// noise bursts into the live meeting on a controlled cadence, WITHOUT ever taking
// a conversational turn. Each burst briefly lights that bot's tile, so the
// platform's active-speaker DOM flickers — exactly the transient that hijacks
// speaker attribution. This makes the flicker REPRODUCIBLE: run it alongside
// drive.mjs (the real conversation) and watch (observe.mjs) whether a brief burst
// steals a turn's name.
//
// The contract: the noise bot is NOT a conversational speaker (drive.mjs excludes
// the same NOISE_KEY), so its display name "<PREFIX>-<en>" can ONLY reach the
// transcript via a flicker → every such segment is a provable hijack. Sweep
// NOISE_DUR_MS across the platform's debounce window to find the edge:
//   zoom-speakers CONFIRM_POLLS≈500ms · msteams-speakers 300ms+200ms hysteresis.
// Bursts shorter than that window MUST be dropped (no hijack); longer ones are a
// legitimate speaker change.
//
// ── KNOBS (env; source secrets.env first for VEXA_BASE/NATIVE_ID/PLATFORM/TOK_*) ─
//   NOISE_KEY     which speaker bot emits noise (default 'D' → its TOK_D)
//   NOISE_DUR_MS  burst length (default 180). Below platform debounce ⇒ should DROP.
//   NOISE_EVERY_S cadence (default 3.5) · NOISE_JITTER_S randomization (default 1.5)
//   NOISE_GAIN    amplitude 0..1 (default 0.6). Bump if the platform's noise
//                 suppression swallows it (the bot's tile never lights at all).
//   DURATION_S    total run seconds (default 900)
import fs from 'fs';
import path from 'path';
import { BASE, PLATFORM, NATIVE, SPEAKERS, sleep, hdr, activeKeys, requireConfig } from './speakers.mjs';

requireConfig();
const HERE = path.dirname(new URL(import.meta.url).pathname);
const NOISE_LOG = process.env.NOISE_LOG || path.join(HERE, '..', 'noise.jsonl');
const RATE = 16000;
const NOISE_KEY = process.env.NOISE_KEY || 'D';
const PREFIX = process.env.BOT_NAME_PREFIX || 'spk';   // must match launch.mjs's prefix
const DUR_MS = Number(process.env.NOISE_DUR_MS || 180);
const EVERY_S = Number(process.env.NOISE_EVERY_S || 3.5);
const JITTER_S = Number(process.env.NOISE_JITTER_S || 1.5);
const GAIN = Number(process.env.NOISE_GAIN || 0.6);
const DURATION_S = Number(process.env.DURATION_S || 900);

// A short PCM16 mono 16 kHz white-noise burst with ~8 ms raised edges (no clicks).
function makeNoiseWav(durMs, gain, rate = RATE) {
  const n = Math.floor((rate * durMs) / 1000);
  const fade = Math.min(Math.floor(rate * 0.008), Math.floor(n / 4));
  const buf = Buffer.alloc(44 + n * 2);
  buf.write('RIFF', 0); buf.writeUInt32LE(36 + n * 2, 4); buf.write('WAVE', 8);
  buf.write('fmt ', 12); buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20);   // PCM
  buf.writeUInt16LE(1, 22); buf.writeUInt32LE(rate, 24); buf.writeUInt32LE(rate * 2, 28);
  buf.writeUInt16LE(2, 32); buf.writeUInt16LE(16, 34);                          // mono, 16-bit
  buf.write('data', 36); buf.writeUInt32LE(n * 2, 40);
  for (let i = 0; i < n; i++) {
    const env = i < fade ? i / fade : i > n - fade ? (n - i) / fade : 1;
    const s = (Math.random() * 2 - 1) * gain * env * 32767;
    buf.writeInt16LE(Math.max(-32768, Math.min(32767, Math.round(s))), 44 + i * 2);
  }
  return buf.toString('base64');
}

async function speak(token, b64) {
  const r = await fetch(`${BASE}/bots/${PLATFORM}/${NATIVE}/speak`, {
    method: 'POST', headers: hdr(token),
    body: JSON.stringify({ audio_base64: b64, format: 'wav', sample_rate: RATE }),
  });
  if (!r.ok) console.log(`  ⚠ noise /speak ${r.status}: ${(await r.text()).slice(0, 80)}`);
}

async function main() {
  const bot = SPEAKERS.find((s) => s.key === NOISE_KEY);
  if (!bot) { console.error(`NOISE_KEY=${NOISE_KEY}: no TOK_${NOISE_KEY} set — the noise bot needs its own token`); process.exit(1); }
  const displayName = `${PREFIX}-${bot.en}`;
  const b64 = makeNoiseWav(DUR_MS, GAIN);
  try { fs.writeFileSync(NOISE_LOG, ''); } catch { /* */ }
  console.log(`[noise] ${PLATFORM}/${NATIVE} · "${displayName}" · ${DUR_MS}ms bursts gain=${GAIN} every ${EVERY_S}±${JITTER_S}s · ${DURATION_S}s`);
  console.log(`[noise] this bot must NOT be a drive speaker (set NOISE_KEY=${NOISE_KEY} on drive too).`);
  console.log(`[noise] ⇒ ANY transcript segment attributed to "${displayName}" is a flicker hijack (VEXA_NOISE_NAME="${displayName}" flags them in observe).`);

  for (;;) { const a = await activeKeys(); if (a.has(NOISE_KEY)) break; console.log(`[noise] waiting for "${displayName}" to be admitted…`); await sleep(2500); }

  const t0 = Date.now();
  while ((Date.now() - t0) / 1000 < DURATION_S) {
    const at = Date.now();
    speak(bot.token, b64).catch((e) => console.log(`  ⚠ noise: ${e.message}`));
    try { fs.appendFileSync(NOISE_LOG, JSON.stringify({ at, displayName, key: NOISE_KEY, durMs: DUR_MS, gain: GAIN }) + '\n'); } catch { /* */ }
    console.log(`  t=${((Date.now() - t0) / 1000).toFixed(1)}s  💥 "${displayName}" noise ${DUR_MS}ms`);
    const wait = Math.max(0.5, EVERY_S + (Math.random() * 2 - 1) * JITTER_S) * 1000;
    await sleep(wait);
  }
  console.log('[noise] done');
}
main().catch((e) => { console.error(e); process.exit(1); });
