// Shared config for the synthetic-meeting eval harness — the speaker roster (one
// production test account per "speaker", driven by its own API token) + the target
// meeting + small API helpers. Imported by launch.mjs (send bots in) and
// drive.mjs (make them speak). Platform-agnostic: set PLATFORM=teams|zoom|google_meet.
//
// Secrets come from the environment (source secrets.env first) — NEVER hard-coded,
// NEVER committed. See secrets.env.example.

export const BASE = (process.env.VEXA_BASE || '').replace(/\/+$/, '');
export const PLATFORM = process.env.PLATFORM || 'google_meet';
export const NATIVE = process.env.NATIVE_ID || '';

/** The 9 cached voices (Deepgram Aura). A speaker is ACTIVE in a run iff its
 *  TOK_<key> is set — N tokens set ⇒ an N-speaker meeting. `en` is the self-ID the
 *  clip leads with ("Boris here, …"), which the leakage/attribution scorer keys on. */
export const ALL_SPEAKERS = [
  { key: 'A', label: 'Анна',    en: 'Anna',   voice: 'aura-asteria-en' },
  { key: 'B', label: 'Борис',   en: 'Boris',  voice: 'aura-orion-en' },
  { key: 'V', label: 'Вера',    en: 'Vera',   voice: 'aura-luna-en' },
  { key: 'C', label: 'Галина',  en: 'Galina', voice: 'aura-stella-en' },
  { key: 'D', label: 'Дмитрий', en: 'Dmitry', voice: 'aura-arcas-en' },
  { key: 'E', label: 'Егор',    en: 'Egor',   voice: 'aura-perseus-en' },
  { key: 'F', label: 'Жанна',   en: 'Zhanna', voice: 'aura-athena-en' },
  { key: 'G', label: 'Зоя',     en: 'Zoya',   voice: 'aura-hera-en' },
  { key: 'H', label: 'Игорь',   en: 'Igor',   voice: 'aura-zeus-en' },
];

/** The speakers selected for THIS run = those with a token set. */
export const SPEAKERS = ALL_SPEAKERS
  .map((s) => ({ ...s, token: process.env[`TOK_${s.key}`] }))
  .filter((s) => s.token);

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export const hdr = (token) => ({ 'X-API-Key': token, 'Content-Type': 'application/json' });

export function requireConfig() {
  if (!BASE) { console.error('VEXA_BASE not set — source secrets.env'); process.exit(1); }
  if (!NATIVE) { console.error('NATIVE_ID not set — the target meeting id'); process.exit(1); }
  if (!SPEAKERS.length) { console.error('no TOK_<key> set — at least one speaker token required'); process.exit(1); }
}

/** Which of our speaker bots are ADMITTED (status 'active') in the target meeting. */
export async function activeKeys() {
  const out = new Set();
  await Promise.all(SPEAKERS.map(async (s) => {
    try {
      const r = await fetch(`${BASE}/bots`, { headers: hdr(s.token) });
      const d = await r.json();
      const ms = Array.isArray(d) ? d : (d.meetings || []);
      if (ms.some((m) => m.native_meeting_id === NATIVE && m.status === 'active')) out.add(s.key);
    } catch { /* transient — caller re-polls */ }
  }));
  return out;
}
