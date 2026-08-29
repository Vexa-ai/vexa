/**
 * Which remote streams the Teams mixed lane should transcribe.
 *
 * Teams delivers the whole meeting as ONE server-side mix whose track id is prefixed
 * `mainAudio`, and ALSO hands the bot a redundant track whose audio is already inside that mix.
 * Combining both feeds every word to the transcriber twice. So when the mix is present, it is
 * the only thing worth capturing.
 *
 * The reason this is a function rather than four lines inside the page callback: the selector is
 * a STRING MATCH on a vendor-generated track id, and preferring it unconditionally has a failure
 * mode worse than the bug it fixes. If Teams renames or drops that prefix — the same class of rot
 * that already killed this platform's class-based name selectors — the filter matches nothing, the
 * mixer receives an empty stream list, and the bot sits in the meeting recording SILENCE for its
 * whole duration. A doubled transcript is bad; an absent one is worse, and the second failure is
 * the one nobody notices until the meeting is over.
 *
 * So: prefer `mainAudio` whenever at least one such track exists; if none has appeared within a
 * grace window, fall back to capturing every track exactly as the pre-fix code did, and report it.
 * The report is returned on EVERY call that falls back, never latched — a permanently degraded
 * capture that announces itself once and then looks healthy forever is how a silent failure
 * survives a whole meeting.
 *
 * Pure and side-effect free so the page callback and the unit test run the SAME code — a
 * hand-copied twin inside `page.evaluate` would drift from its test on the first edit.
 */

/** The shape this needs from a MediaStream — anything exposing audio tracks with ids. */
export interface AudioTrackLike { id?: string }
export interface StreamLike { getAudioTracks?: () => AudioTrackLike[] }

/** Emitted whenever the mix is absent past the grace window and capture falls back to all tracks. */
export interface MainAudioAbsentObservation {
  kind: 'main-audio-absent';
  platform: 'teams';
  waitedMs: number;
  streamCount: number;
  /** The ids actually seen, so a rotted prefix is diagnosable from the log alone. */
  trackIds: string[];
  action: string;
}

/** Emitted whenever the mix was PRESENT and picked, but carried no energetic audio at all. */
export interface MainAudioSilentObservation {
  kind: 'main-audio-silent';
  platform: 'teams';
  /** How long the picked mix was captured before it was abandoned. */
  capturedMs: number;
  streamCount: number;
  trackIds: string[];
  action: string;
}

export interface TeamsMixSelection {
  /** The streams to feed the mixer. */
  streams: StreamLike[];
  /** 'main-audio' — the mix alone · 'waiting' — inside grace, capture nothing yet ·
   *  'fallback-all' — capturing everything, because the mix never appeared OR never carried sound. */
  outcome: 'main-audio' | 'waiting' | 'fallback-all';
  observation?: MainAudioAbsentObservation | MainAudioSilentObservation;
}

export const TEAMS_MAIN_AUDIO_GRACE_MS = 15000;

/** How long a PICKED mix may stay wholly silent before it is abandoned for every track. */
export const TEAMS_MAIN_AUDIO_SILENCE_MS = 20000;

/** Matches the lane's own silence floor (chunked-transcriber DROP_RMS / turn-source DEATH_RMS). */
export const TEAMS_MAIN_AUDIO_ENERGY_RMS = 0.006;

/**
 * Has the picked mix proved itself DEAD — captured for long enough, with no energetic audio ever?
 *
 * The absence fallback above only ever asked "did a mainAudio track appear?". A track that appears
 * and then carries pure silence answers yes and passes every check, so the bot captures a silent
 * stream for the whole meeting while three real ones sit mirrored and unused. That produces no
 * pyannote boundaries, so no turn ever opens, so every speaker hint arrives with nothing to overlap
 * and no audio is ever submitted for transcription — an empty transcript from a completely healthy
 * looking bot. Worse, the lane's own rescue path cannot fire either: the transport watchdog demotes
 * csrc back to pyannote only after CSRC_DEATH_MS of ENERGETIC audio, which silence never supplies.
 * Presence is not liveness, and this is the check that says so.
 *
 * @param captureStartedMs  when the picked mix first produced a capture callback (null = not yet)
 * @param energeticMs       accumulated ms of audio at or above the energy floor
 */
export function mainAudioProvedSilent(
  { captureStartedMs, energeticMs, nowMs, silenceMs = TEAMS_MAIN_AUDIO_SILENCE_MS }:
    { captureStartedMs: number | null; energeticMs: number; nowMs: number; silenceMs?: number },
): boolean {
  if (captureStartedMs === null) return false;   // nothing captured yet — not evidence of silence
  if (energeticMs > 0) return false;             // it spoke at least once; it is a real mix
  return nowMs - captureStartedMs >= silenceMs;
}

const hasMainAudio = (s: StreamLike): boolean =>
  (s.getAudioTracks?.() || []).some((t) => String(t?.id || '').toLowerCase().startsWith('mainaudio'));

const trackIdsOf = (streams: StreamLike[]): string[] =>
  (streams || [])
    .flatMap((s) => (s.getAudioTracks?.() || []).map((t) => String(t?.id || '')))
    .slice(0, 8);

/**
 * @param streams      every mirrored remote stream
 * @param firstMissMs  when the mix was FIRST observed missing (the caller persists this across rescans)
 * @param nowMs        current time
 * @param graceMs      how long to wait for the mix before falling back
 * @param mainAudioSilent  the caller's latched verdict from `mainAudioProvedSilent` — the picked mix
 *                     was captured and never carried sound, so it must not be preferred again
 * @param mainAudioCapturedMs  how long that silent mix was captured, for the report
 */
export function selectTeamsMixStreams(
  streams: StreamLike[],
  { firstMissMs, nowMs, graceMs = TEAMS_MAIN_AUDIO_GRACE_MS, mainAudioSilent = false, mainAudioCapturedMs = 0 }:
    { firstMissMs: number | null; nowMs: number; graceMs?: number; mainAudioSilent?: boolean; mainAudioCapturedMs?: number },
): TeamsMixSelection {
  const main = (streams || []).filter(hasMainAudio);
  if (main.length && !mainAudioSilent) return { streams: main, outcome: 'main-audio' };

  // The mix is THERE but proved silent: take everything instead. Doubled words are recoverable;
  // a silent meeting is not. Re-emitted on every rescan, like the absent case, so a bot running on
  // the fallback never looks healthy while capturing the wrong thing.
  if (main.length && mainAudioSilent) {
    return {
      streams: streams || [],
      outcome: 'fallback-all',
      observation: {
        kind: 'main-audio-silent',
        platform: 'teams',
        capturedMs: Math.max(0, Math.round(mainAudioCapturedMs)),
        streamCount: (streams || []).length,
        trackIds: trackIdsOf(streams),
        action: 'mainAudio mix was picked but carried NO energetic audio — capturing ALL tracks instead',
      },
    };
  }

  const waitedMs = firstMissMs === null ? 0 : Math.max(0, nowMs - firstMissMs);
  if (waitedMs < graceMs) return { streams: [], outcome: 'waiting' };

  return {
    streams: streams || [],
    outcome: 'fallback-all',
    observation: {
      kind: 'main-audio-absent',
      platform: 'teams',
      waitedMs,
      streamCount: (streams || []).length,
      trackIds: (streams || [])
        .flatMap((s) => (s.getAudioTracks?.() || []).map((t) => String(t?.id || '')))
        .slice(0, 8),
      action: 'capturing ALL tracks (fail-open) — audio may be double-fed; the mainAudio selector may have rotted',
    },
  };
}
