/** SOUNDS — the three notification sounds (0.13.4 S6, ported from the cards mock), synthesised with
 *  WebAudio: no files, no network, no dependency.
 *    chime — a card or a mention for you (two soft sine partials, ~350 ms)
 *    tick  — an in-app toast (a tiny filtered click, ~60 ms)
 *    ring  — a call ringing you (a gentle two-tone ring, every 1.6 s until stopRing() or 30 s)
 *
 *  Browsers refuse audio until a user gesture: `unlocked()` says whether one happened, `unlock()` is
 *  called from that gesture (the one-time "Enable sounds" prompt, or any click). Everything here is
 *  safe without an AudioContext (jsdom, an old browser, a locked-down kiosk): it just stays silent.
 *
 *  `soundFor()` is the pure decision: which sound, if any, a new notice may make. Sound is for
 *  interruptions only, never for the room the person is looking at, never in quiet hours (the
 *  person's policy, their local time), never when sounds or that sound are off. Plain messages
 *  never sound. */
export type SoundKind = "chime" | "tick" | "ring";

type Ctx = AudioContext;
let ctx: Ctx | null = null;
let master: GainNode | null = null;
let ringTimer: ReturnType<typeof setInterval> | null = null;
let ringStop: ReturnType<typeof setTimeout> | null = null;
let volume = 0.6;
const unlockSubs = new Set<() => void>();

function AudioCtor(): (new () => Ctx) | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { AudioContext?: new () => Ctx; webkitAudioContext?: new () => Ctx };
  return w.AudioContext ?? w.webkitAudioContext ?? null;
}

export const audioSupported = (): boolean => AudioCtor() !== null;
export const unlocked = (): boolean => !!ctx && ctx.state === "running";

/** Call from a user gesture. Idempotent; silent where audio does not exist. */
export function unlock(): void {
  try {
    if (!ctx) {
      const C = AudioCtor();
      if (!C) return;
      ctx = new C();
      master = ctx.createGain();
      master.gain.value = volume;
      master.connect(ctx.destination);
    }
    const p = ctx.resume?.();
    const done = () => unlockSubs.forEach((f) => f());
    if (p && typeof (p as Promise<void>).then === "function") void (p as Promise<void>).then(done, () => undefined);
    else done();
  } catch { /* no audio is fine */ }
}
/** Told when audio becomes unlocked (to hide the "Enable sounds" prompt). */
export function onUnlocked(fn: () => void): () => void { unlockSubs.add(fn); return () => { unlockSubs.delete(fn); }; }

export function setVolume(v: number): void {
  volume = Math.max(0, Math.min(1, Number.isFinite(v) ? v : 0.6));
  try { if (master) master.gain.value = volume; } catch { /* */ }
}
export const getVolume = (): number => volume;

function tone(freq: number, start: number, dur: number, peak: number, type: OscillatorType = "sine") {
  if (!ctx || !master) return;
  const o = ctx.createOscillator(); const g = ctx.createGain();
  o.type = type; o.frequency.value = freq;
  g.gain.setValueAtTime(0, start); g.gain.linearRampToValueAtTime(peak, start + 0.012); g.gain.exponentialRampToValueAtTime(0.0001, start + dur);
  o.connect(g); g.connect(master); o.start(start); o.stop(start + dur + 0.02);
}
function ringOnce(t: number) { for (let i = 0; i < 2; i++) { tone(660, t + i * 0.22, 0.18, 0.16); tone(830, t + i * 0.22 + 0.02, 0.18, 0.1); } }

/** One sound, now. `ring` here is a single beat; use startRing() for the repeating call ring. */
export function play(kind: SoundKind): void {
  try {
    if (!unlocked() || !ctx) return;
    const t = ctx.currentTime + 0.01;
    if (kind === "chime") { tone(880, t, 0.32, 0.22); tone(1318.5, t + 0.07, 0.28, 0.14); }
    else if (kind === "tick") tone(2400, t, 0.05, 0.08, "triangle");
    else ringOnce(t);
  } catch { /* */ }
}

export const RING_EVERY_MS = 1600;
export const RING_FOR_MS = 30000;
/** The call ring: repeats every 1.6 s until stopRing() or 30 s (then `onEnd`). */
export function startRing(onEnd?: () => void): void {
  stopRing();
  if (!unlocked()) return;
  const beat = () => { try { if (ctx) ringOnce(ctx.currentTime + 0.01); } catch { /* */ } };
  beat();
  ringTimer = setInterval(beat, RING_EVERY_MS);
  ringStop = setTimeout(() => { stopRing(); onEnd?.(); }, RING_FOR_MS);
}
export function stopRing(): void {
  if (ringTimer) clearInterval(ringTimer);
  if (ringStop) clearTimeout(ringStop);
  ringTimer = ringStop = null;
}
export const ringing = (): boolean => ringTimer !== null;

/** Settings' Play button: a ring previews for ~3 s, the others play once. Unlocks on the way (it is a click). */
export function preview(kind: SoundKind): void {
  if (!unlocked()) unlock();
  if (kind === "ring") { startRing(); setTimeout(stopRing, 3300); } else play(kind);
}

/** For tests: forget the context. */
export function __resetSounds(): void { stopRing(); ctx = null; master = null; volume = 0.6; unlockSubs.clear(); }

// ── the decision ──────────────────────────────────────────────────────────────────────────────
export type SoundPolicy = { on: boolean; chime: boolean; tick: boolean; ring: boolean; volume: number };
export type QuietPolicy = { on: boolean; from: string; to: string };
export type SoundNotice = { kind: string; object?: { kind?: string; id?: string } };
export type SoundContext = {
  sounds: SoundPolicy;
  quiet: QuietPolicy;
  /** the room (chat session id) the person is looking at right now, if any */
  roomInView?: string | null;
  /** minutes since local midnight; default: the browser's clock */
  nowMinutes?: number;
};

/** The kinds that interrupt. Everything else — a plain message, a routine run, decided-by-rules — is silent. */
const INTERRUPTS: Record<string, SoundKind> = {
  call_started: "ring",
  call_ringing: "ring",
  card_waiting: "chime",
  card_slipped: "chime",
  chat_mentioned: "chime",
  workspace_invited: "chime",
  join_requested: "chime",
  workspace_join_requested: "chime",
};

const hhmm = (s: string): number | null => {
  const m = /^(\d{1,2}):(\d{2})$/.exec(s ?? "");
  if (!m) return null;
  const h = Number(m[1]), mm = Number(m[2]);
  return h < 24 && mm < 60 ? h * 60 + mm : null;
};
/** Is `now` (minutes since midnight) inside quiet hours? The window may wrap midnight (22:00–08:00). */
export function inQuietHours(q: QuietPolicy, now: number): boolean {
  if (!q?.on) return false;
  const a = hhmm(q.from), b = hhmm(q.to);
  if (a === null || b === null || a === b) return false;
  return a < b ? now >= a && now < b : now >= a || now < b;
}
const localMinutes = () => { const d = new Date(); return d.getHours() * 60 + d.getMinutes(); };

export function soundFor(n: SoundNotice, c: SoundContext): SoundKind | null {
  const kind = INTERRUPTS[n.kind];
  if (!kind) return null;
  if (!c.sounds?.on || !c.sounds[kind]) return null;
  if (c.roomInView && n.object?.id === c.roomInView) return null;
  if (inQuietHours(c.quiet, c.nowMinutes ?? localMinutes())) return null;
  return kind;
}
