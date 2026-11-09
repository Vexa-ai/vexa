/**
 * createTeamsPageCapture: Microsoft Teams' PageCapture (@vexa/capture-codec's contract, matched by shape).
 * Teams delivers the meeting as one server-side mix, so this is the bot's mixed lane (createMixedLaneCapture,
 * @vexa/mixed-capture-core, injected by the host: this package carries no runtime dependency) on ONE channel,
 * each frame named by createTeamsSpeakers' voice-level outline: the one participant (by tile id) speaking at
 * the frame's capture time, or null when nobody, several, or someone whose name did not resolve are (never a guess). The bot names the same lane server-side from the same
 * hints; per-frame naming here is coarser (a turn that changes speaker without a pause keeps its first name).
 */
import { createTeamsSpeakers } from './msteams-speakers.js';

type Lane = { channels(): number; contextState(): string; stop(): void };
export interface TeamsPageCaptureOptions {
  onAudio: (channel: number, name: string | null, pcm: Float32Array, tsMs: number) => void;
  selfName?: string;
  log?: (msg: string) => void;
  observe?: (kind: string, data: Record<string, unknown>, tMs: number) => void;
  /** The remote audio streams the WebRTC hook has mirrored. */
  streams: () => MediaStream[];
  /** The mixed lane (@vexa/mixed-capture-core createMixedLaneCapture). */
  createLane: (o: {
    streams: () => MediaStream[]; teamsMainAudio: boolean; platform: string;
    onAudio: (pcm: Float32Array, tsMs: number | undefined) => void;
    log?: (m: string) => void; observe?: (k: string, d: Record<string, unknown>, t: number) => void;
  }) => Lane;
  makeSpeakers?: typeof createTeamsSpeakers;
  /** The shared speaker timeline (@vexa/mixed-capture-core createSpeakerTimeline). */
  createTimeline: () => { speak(id: string, name: string, tMs: number, isEnd: boolean): void; unnamed(edge: 'start' | 'end', tMs: number): void; nameAt(ts: number): string | null };
  onSpeakerHint?: (name: string, tMs: number, isEnd: boolean) => void;
}

export function createTeamsPageCapture(opts: TeamsPageCaptureOptions) {
  let stopped = false;
  const timeline = opts.createTimeline();
  const speakers = (opts.makeSpeakers ?? createTeamsSpeakers)({
    selfName: opts.selfName,
    log: (m: string) => opts.log?.('[TeamsSpeakers] ' + m),
    onSpeaking: (name: string, id: string, isEnd: boolean, tMs: number) => {
      timeline.speak(id, name, tMs, isEnd);
      opts.onSpeakerHint?.(name, tMs, isEnd);
    },
    onNameUnresolved: (o: { edge: 'start' | 'end'; tMs: number }) => timeline.unnamed(o.edge, o.tMs),
    onObservation: (o: Record<string, unknown>) => opts.observe?.('teams-speakers', o, Date.now()),
  } as any);
  const lane = opts.createLane({
    streams: opts.streams, teamsMainAudio: true, platform: 'teams', log: opts.log, observe: opts.observe,
    onAudio: (pcm, tsMs) => {
      if (stopped) return;
      const ts = typeof tsMs === 'number' ? tsMs : Date.now();
      opts.onAudio(0, timeline.nameAt(ts), pcm, ts);
    },
  });
  return {
    channels: () => (lane.channels() ? 1 : 0),
    contextState: () => lane.contextState(),
    stop(): void {
      if (stopped) return;
      stopped = true;
      try { lane.stop(); } catch { /* gone */ }
      try { (speakers as any)?.destroy?.(); } catch { /* gone */ }
    },
  };
}
