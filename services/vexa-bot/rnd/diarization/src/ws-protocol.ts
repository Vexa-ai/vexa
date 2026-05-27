/**
 * Wire format between browser pages and the harness.
 *
 * Two WebSocket channels:
 *   ws://host/audio    — capture page → harness; binary PCM frames
 *   ws://host/transcript — harness → dashboard; JSON segment events
 *
 * AudioFrame protocol (binary):
 *   First 8 bytes : Float64 wall-clock ms (Date.now() when capture chunk arrived)
 *   Rest          : Float32 little-endian PCM samples, 16kHz mono
 *
 * TranscriptEvent (JSON, harness → dashboard):
 *   { kind: "segment", speaker: "speaker_0", t0: number, t1: number, text: string }
 *   { kind: "diarizer-info", name: string, numSpeakers: number }
 *   { kind: "transcription-status", reachable: boolean, url: string, error?: string }
 *   { kind: "speech-state", inSpeech: boolean, speaker: string, ts: number }
 */

export interface SegmentEvent {
  kind: 'segment';
  speaker: string;
  t0: number;
  t1: number;
  text: string;
}

export interface DiarizerInfoEvent {
  kind: 'diarizer-info';
  name: string;
  numSpeakers: number;
}

export interface TranscriptionStatusEvent {
  kind: 'transcription-status';
  reachable: boolean;
  url: string;
  error?: string;
}

export interface SpeechStateEvent {
  kind: 'speech-state';
  inSpeech: boolean;
  speaker: string;
  ts: number;
}

export type DashboardEvent =
  | SegmentEvent
  | DiarizerInfoEvent
  | TranscriptionStatusEvent
  | SpeechStateEvent;

export const SAMPLE_RATE = 16000;
