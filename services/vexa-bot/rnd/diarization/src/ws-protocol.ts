/**
 * Wire format between browser pages and the harness.
 *
 *   ws://host/audio       — capture page → harness; binary PCM frames
 *   ws://host/transcript  — harness → dashboard; JSON events
 *
 * AudioFrame protocol (binary): Float64 wall-clock ms (8 bytes) +
 * Float32 little-endian PCM samples, 16 kHz mono.
 *
 * The dashboard event shapes mirror the bot's production Redis publish
 * shapes (see services/vexa-bot/core/src/services/segment-publisher.ts):
 *   - `TranscriptBundle` matches the bot's `tc:meeting:<id>:mutable` pub/sub
 *     payload (confirmed[] + pending[] per speaker, atomic update).
 *   - `SpeakerEventWire` matches the `speaker_events_relative` XADD fields.
 *   - `SessionStart` / `SessionEnd` mirror the bot's session lifecycle.
 *
 * Downstream consumers (dashboard.js, future automated subscribers) read
 * the same shape they would get from a production bot run. The transport
 * is WebSocket instead of Redis only because MVP0 skips the Redis broker.
 */

import type { TranscriptionSegment } from '../../../core/src/services/segment-publisher';
import type { MetricsSnapshot } from './metrics';

export const SAMPLE_RATE = 16000;

export interface SessionInfoEvent {
  kind: 'session_info';
  diarizer_name: string;
  num_speakers: number;
  session_uid: string;
  meeting_id: string;
  platform: string;
  transcription_url: string;
  transcription_reachable: boolean;
  transcription_error?: string;
}

export interface SessionStartEvent {
  kind: 'session_start';
  uid: string;
  meeting_id: string;
  platform: string;
  start_timestamp: string;
}

export interface SessionEndEvent {
  kind: 'session_end';
  uid: string;
}

export interface TranscriptEvent {
  kind: 'transcript';
  meeting_id: string;
  speaker: string;
  confirmed: TranscriptionSegment[];
  pending: TranscriptionSegment[];
  ts: string;
}

export interface SpeakerEventWire {
  kind: 'speaker_event';
  speaker: string;
  event_type: 'SPEAKER_START' | 'SPEAKER_END';
  timestamp_ms: number;
  relative_ms: number;
}

export interface MetricsEvent {
  kind: 'metrics';
  snapshot: MetricsSnapshot;
}

export type DashboardEvent =
  | SessionInfoEvent
  | SessionStartEvent
  | SessionEndEvent
  | TranscriptEvent
  | SpeakerEventWire
  | MetricsEvent;
