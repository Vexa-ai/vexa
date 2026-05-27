/**
 * JsonlSegmentPublisher — drop-in for the production bot's SegmentPublisher
 * that writes the EXACT XADD / PUBLISH payloads to a JSONL file instead
 * of contacting Redis.
 *
 * Why: at MVP0 we want full data-shape parity with what the bot would
 * publish to Redis, without bringing up meeting-api JWT minting + a
 * registered numeric meeting_id + a real Redis broker for the dashboard
 * to subscribe to. The JSONL log is the contract-conformance evidence.
 *
 * It composes (not subclasses) the bot's SegmentPublisher type contract.
 * Same public method signatures, same payload shapes, no Redis connection.
 * If we ever want to flip on real Redis at MVP3+, we swap the
 * implementation behind the same interface and the rest of the harness
 * doesn't notice.
 */

import * as fs from 'fs';
import * as path from 'path';

import type {
  TranscriptionSegment,
  SpeakerEvent,
} from '../../../core/src/services/segment-publisher';

export interface JsonlSegmentPublisherOptions {
  /** Path to the JSONL output file. Created if missing, appended otherwise. */
  outPath: string;
  /** Internal meeting ID (synthetic for the RnD harness). */
  meetingId: string;
  /** Session UID (synthesized at harness start). */
  sessionUid: string;
  /** Platform tag — "teams-rnd-mvp0" so downstream readers know this is RnD. */
  platform: string;
  /** Synthetic JWT placeholder; never validated since we don't hit meeting-api. */
  token: string;
  /** Redis stream key for segments. Default "transcription_segments" (production default). */
  segmentStreamKey?: string;
  /** Redis stream key for speaker events. Default "speaker_events_relative". */
  speakerEventStreamKey?: string;
  /** Optional broadcast hook — fires per published transcript bundle so the
   *  harness can stream confirmed/pending updates to the dashboard. */
  onTranscriptBundle?: (bundle: TranscriptBundle) => void;
}

/** What the production bot publishes to `tc:meeting:<id>:mutable` Redis pub/sub. */
export interface TranscriptBundle {
  type: 'transcript';
  meeting: { id: number | string };
  speaker: string;
  confirmed: TranscriptionSegment[];
  pending: TranscriptionSegment[];
  ts: string;
}

/**
 * Mirrors the public surface of `SegmentPublisher` (the bot's class) but
 * never touches Redis. The bot's class is what we're substituting for; the
 * harness wires it in where production wires `SegmentPublisher`.
 */
export class JsonlSegmentPublisher {
  private outPath: string;
  private meetingId: string;
  readonly sessionUid: string;
  private platform: string;
  private token: string;
  private segmentStreamKey: string;
  private speakerEventStreamKey: string;
  private onTranscriptBundle?: (b: TranscriptBundle) => void;
  /** Wall-clock when the session started (ms). Matches production semantics:
   *  set at construction, reset to capture-t0 via resetSessionStart(). */
  sessionStartMs: number;

  constructor(opts: JsonlSegmentPublisherOptions) {
    this.outPath = opts.outPath;
    this.meetingId = opts.meetingId;
    this.sessionUid = opts.sessionUid;
    this.platform = opts.platform;
    this.token = opts.token;
    this.segmentStreamKey = opts.segmentStreamKey ?? 'transcription_segments';
    this.speakerEventStreamKey = opts.speakerEventStreamKey ?? 'speaker_events_relative';
    this.onTranscriptBundle = opts.onTranscriptBundle;
    this.sessionStartMs = Date.now();
    fs.mkdirSync(path.dirname(this.outPath), { recursive: true });
  }

  resetSessionStart(): void {
    this.sessionStartMs = Date.now();
  }

  private writeJsonl(record: Record<string, unknown>): void {
    fs.appendFileSync(this.outPath, JSON.stringify(record) + '\n');
  }

  async publishSessionStart(): Promise<void> {
    const payload = {
      type: 'session_start',
      token: this.token,
      uid: this.sessionUid,
      platform: this.platform,
      meeting_id: this.meetingId,
      start_timestamp: new Date(this.sessionStartMs).toISOString(),
    };
    this.writeJsonl({
      op: 'XADD',
      stream: this.segmentStreamKey,
      fields: { payload: JSON.stringify(payload) },
      _logged_at: new Date().toISOString(),
    });
  }

  async publishSessionEnd(): Promise<void> {
    const payload = {
      type: 'session_end',
      token: this.token,
      uid: this.sessionUid,
    };
    this.writeJsonl({
      op: 'XADD',
      stream: this.segmentStreamKey,
      fields: { payload: JSON.stringify(payload) },
      _logged_at: new Date().toISOString(),
    });
  }

  async publishSegment(segment: TranscriptionSegment): Promise<void> {
    // XADD to transcription_segments — collector path (Postgres persistence)
    const xaddPayload = {
      type: 'transcription',
      token: this.token,
      uid: this.sessionUid,
      platform: this.platform,
      meeting_id: this.meetingId,
      segments: [this.mapSeg(segment)],
    };
    this.writeJsonl({
      op: 'XADD',
      stream: this.segmentStreamKey,
      fields: { payload: JSON.stringify(xaddPayload) },
      _logged_at: new Date().toISOString(),
    });

    // PUBLISH to meeting:<id>:segments — gateway WS path
    const publishPayload = {
      ...segment,
      meeting_id: this.meetingId,
      timestamp: Date.now(),
    };
    this.writeJsonl({
      op: 'PUBLISH',
      channel: `meeting:${this.meetingId}:segments`,
      message: JSON.stringify(publishPayload),
      _logged_at: new Date().toISOString(),
    });
  }

  async publishTranscript(
    speaker: string,
    confirmed: TranscriptionSegment[],
    pending: TranscriptionSegment[],
  ): Promise<void> {
    // XADD confirmed segments (collector persists to Postgres)
    for (const seg of confirmed) {
      const payload = {
        type: 'transcription',
        token: this.token,
        uid: this.sessionUid,
        platform: this.platform,
        meeting_id: this.meetingId,
        segments: [this.mapSeg(seg)],
      };
      this.writeJsonl({
        op: 'XADD',
        stream: this.segmentStreamKey,
        fields: { payload: JSON.stringify(payload) },
        _logged_at: new Date().toISOString(),
      });
    }

    // SET pending snapshot per speaker (60s TTL in production; here just logged)
    const pendingKey = `meeting:${this.meetingId}:pending:${speaker}`;
    if (pending.length > 0) {
      this.writeJsonl({
        op: 'SET',
        key: pendingKey,
        value: JSON.stringify(pending.map((s) => this.mapSeg(s))),
        ex_seconds: 60,
        _logged_at: new Date().toISOString(),
      });
    } else {
      this.writeJsonl({ op: 'DEL', key: pendingKey, _logged_at: new Date().toISOString() });
    }

    // PUBLISH atomic bundle to WS channel — the dashboard's actual input shape
    const bundle: TranscriptBundle = {
      type: 'transcript',
      meeting: { id: this.meetingId },
      speaker,
      confirmed: confirmed.map((s) => this.mapSeg(s)),
      pending: pending.map((s) => this.mapSeg(s)),
      ts: new Date().toISOString(),
    };
    this.writeJsonl({
      op: 'PUBLISH',
      channel: `tc:meeting:${this.meetingId}:mutable`,
      message: JSON.stringify(bundle),
      _logged_at: new Date().toISOString(),
    });
    this.onTranscriptBundle?.(bundle);
  }

  async publishSpeakerEvent(event: SpeakerEvent): Promise<void> {
    const eventTypeMap: Record<string, string> = {
      joined: 'SPEAKER_START',
      started_speaking: 'SPEAKER_START',
      stopped_speaking: 'SPEAKER_END',
      left: 'SPEAKER_END',
    };
    const fields = {
      uid: this.sessionUid,
      relative_client_timestamp_ms: String(event.timestamp - this.sessionStartMs),
      event_type: eventTypeMap[event.type] ?? event.type,
      participant_name: event.speaker,
      meeting_id: this.meetingId,
    };
    this.writeJsonl({
      op: 'XADD',
      stream: this.speakerEventStreamKey,
      fields,
      _logged_at: new Date().toISOString(),
    });
  }

  async close(): Promise<void> {
    // Nothing to close — appendFileSync flushes per write.
  }

  private mapSeg(s: TranscriptionSegment): TranscriptionSegment {
    return {
      start: s.start,
      end: s.end,
      text: s.text,
      language: s.language,
      completed: s.completed ?? true,
      speaker: s.speaker,
      segment_id: s.segment_id,
      ...(s.absolute_start_time && { absolute_start_time: s.absolute_start_time }),
      ...(s.absolute_end_time && { absolute_end_time: s.absolute_end_time }),
    };
  }
}
