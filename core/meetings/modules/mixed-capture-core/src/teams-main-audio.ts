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

export interface TeamsMixSelection {
  /** The streams to feed the mixer. */
  streams: StreamLike[];
  /** 'main-audio' — the mix alone · 'waiting' — inside grace, capture nothing yet ·
   *  'fallback-all' — grace expired, capturing everything (possibly double-fed). */
  outcome: 'main-audio' | 'waiting' | 'fallback-all';
  observation?: MainAudioAbsentObservation;
}

export const TEAMS_MAIN_AUDIO_GRACE_MS = 15000;

const hasMainAudio = (s: StreamLike): boolean =>
  (s.getAudioTracks?.() || []).some((t) => String(t?.id || '').toLowerCase().startsWith('mainaudio'));

/**
 * @param streams      every mirrored remote stream
 * @param firstMissMs  when the mix was FIRST observed missing (the caller persists this across rescans)
 * @param nowMs        current time
 * @param graceMs      how long to wait for the mix before falling back
 */
export function selectTeamsMixStreams(
  streams: StreamLike[],
  { firstMissMs, nowMs, graceMs = TEAMS_MAIN_AUDIO_GRACE_MS }:
    { firstMissMs: number | null; nowMs: number; graceMs?: number },
): TeamsMixSelection {
  const main = (streams || []).filter(hasMainAudio);
  if (main.length) return { streams: main, outcome: 'main-audio' };

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
