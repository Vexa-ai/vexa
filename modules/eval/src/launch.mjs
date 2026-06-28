#!/usr/bin/env node
// launch — send the speaker bots into the target meeting via the PRODUCTION API,
// one at a time with a delay between them (so the egress IP isn't flagged for a
// burst of joins), then wait until they're admitted. Each bot joins as its own
// test account (its TOK_<key>), so it shows up as a distinct participant the
// /speak driver can later voice.
//
//   STAGGER_S  seconds between launches (default 10 — keeps IP from blocking)
//   BOT_NAME_PREFIX  display-name prefix (default 'spk') → "spk-Boris", …
//   LANG       meeting language (default 'en')
//   WAIT_S     how long to wait for all bots to reach 'active' (default 180; 0 = don't wait)
//
//   source secrets.env first (VEXA_BASE, NATIVE_ID, PLATFORM, TOK_*).
import { BASE, PLATFORM, NATIVE, SPEAKERS as ALL, sleep, hdr, activeKeys, requireConfig } from './speakers.mjs';

requireConfig();
// ONLY_KEYS=A,D,E → launch just those (retry failed / add new without re-requesting
// bots that are already joining or admitted). Default: every speaker with a token.
const ONLY = (process.env.ONLY_KEYS || '').split(',').map((s) => s.trim()).filter(Boolean);
const SPEAKERS = ONLY.length ? ALL.filter((s) => ONLY.includes(s.key)) : ALL;
const STAGGER_S = Number(process.env.STAGGER_S ?? 10);
const PREFIX = process.env.BOT_NAME_PREFIX || 'spk';
const LANG = process.env.LANG || 'en';
const WAIT_S = Number(process.env.WAIT_S ?? 180);

const MEETING_URL = process.env.MEETING_URL || '';   // full join link (Teams /meet/ etc.) — carries the passcode
const PASSCODE = process.env.PASSCODE || '';          // explicit passcode (Teams ?p=, Zoom pwd)

async function launchOne(s) {
  const body = {
    platform: PLATFORM,
    native_meeting_id: NATIVE,
    bot_name: `${PREFIX}-${s.en}`,
    language: LANG,
    task: 'transcribe',          // a normal bot; we drive its mic via /speak
    ...(MEETING_URL ? { meeting_url: MEETING_URL } : {}),
    ...(PASSCODE ? { passcode: PASSCODE } : {}),
  };
  const r = await fetch(`${BASE}/bots`, { method: 'POST', headers: hdr(s.token), body: JSON.stringify(body) });
  const txt = await r.text();
  if (!r.ok) { console.log(`  ✗ ${s.en} (${s.key}) launch ${r.status}: ${txt.slice(0, 120)}`); return false; }
  console.log(`  → ${s.en} (${s.key}) requested`);
  return true;
}

async function main() {
  console.log(`[launch] ${SPEAKERS.length} bot(s) → ${PLATFORM}/${NATIVE} via ${BASE}  ·  ${STAGGER_S}s apart`);
  for (let i = 0; i < SPEAKERS.length; i++) {
    await launchOne(SPEAKERS[i]);
    if (i < SPEAKERS.length - 1) await sleep(STAGGER_S * 1000);   // stagger to protect the egress IP
  }
  if (WAIT_S <= 0) { console.log('[launch] requested all; not waiting (WAIT_S=0)'); return; }

  console.log(`[launch] waiting up to ${WAIT_S}s for admission…`);
  const want = SPEAKERS.length;
  const t0 = Date.now();
  for (;;) {
    const act = await activeKeys();
    console.log(`  active ${act.size}/${want}: [${[...act].join(',')}]`);
    if (act.size >= want) { console.log('[launch] ✓ all admitted'); return; }
    if ((Date.now() - t0) / 1000 > WAIT_S) {
      console.log(`[launch] ⚠ timeout — ${act.size}/${want} admitted. Admit the rest in the meeting UI, then run drive.`);
      return;
    }
    await sleep(5000);
  }
}
main().catch((e) => { console.error(e); process.exit(1); });
