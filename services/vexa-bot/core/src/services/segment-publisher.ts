import { createClient, RedisClientType } from 'redis';
import { log } from '../utils';

export interface TranscriptionSegment {
  speaker: string;
  text: string;
  /** Relative to session start (seconds) */
  start: number;
  /** Relative to session start (seconds) */
  end: number;
  language: string;
  /** Whether this segment is finalized */
  completed?: boolean;
  /** Absolute UTC start time as ISO string */
  absolute_start_time?: string;
  /** Absolute UTC end time as ISO string */
  absolute_end_time?: string;
  /** Stable segment identity: {session_uid}:{speakerId}:{sequenceNumber} */
  segment_id?: string;
}

export interface SpeakerEvent {
  speaker: string;
  type: 'joined' | 'left' | 'started_speaking' | 'stopped_speaking';
  timestamp: number;
}

export interface SegmentPublisherConfig {
  /** Redis URL, e.g. "redis://localhost:6379" */
  redisUrl: string;
  /** Internal meeting ID (numeric) */
  meetingId: string;
  /** MeetingToken JWT (HS256, signed by bot-manager) */
  token: string;
  /** Session UID (connectionId from bot config) */
  sessionUid: string;
  /** Platform identifier */
  platform: string;
  /** Redis stream key for transcription segments. Default: "transcription_segments" */
  segmentStreamKey?: string;
  /** Redis stream key for speaker events. Default: "speaker_events_relative" */
  speakerEventStreamKey?: string;
}

/**
 * Publishes transcription segments and speaker events to Redis
 * in the format expected by transcription-collector.
 *
 * Segments: XADD with { payload: JSON } to stream, PUBLISH flat JSON to pub/sub.
 * Speaker events: XADD flat fields to speaker_events_relative stream.
 */
export class SegmentPublisher {
  private redisUrl: string;
  private meetingId: string;
  private token: string;
  readonly sessionUid: string;
  private platform: string;
  private segmentStreamKey: string;
  private speakerEventStreamKey: string;
  private client: RedisClientType | null = null;
  private connected: boolean = false;
  /** Wall-clock time when the session started (ms). Defaults to construction time,
   *  should be reset via resetSessionStart() when audio capture actually begins
   *  so that segment start_time aligns with the recording. */
  sessionStartMs: number;

  constructor(config: SegmentPublisherConfig) {
    this.redisUrl = config.redisUrl;
    this.meetingId = config.meetingId;
    this.token = config.token;
    this.sessionUid = config.sessionUid;
    this.platform = config.platform;
    this.segmentStreamKey = config.segmentStreamKey ?? 'transcription_segments';
    this.speakerEventStreamKey = config.speakerEventStreamKey ?? 'speaker_events_relative';
    this.sessionStartMs = Date.now();
  }

  /**
   * Reset session start to now. Call when audio capture actually begins
   * so that segment start_time values align with the recording timeline.
   */
  resetSessionStart(): void {
    this.sessionStartMs = Date.now();
  }

  /**
   * Ensure the Redis client is connected. Creates and connects on first call.
   */
  private async ensureConnected(): Promise<RedisClientType> {
    if (this.client && this.connected) {
      return this.client;
    }

    try {
      this.client = createClient({ url: this.redisUrl }) as RedisClientType;

      this.client.on('error', (err) => {
        log(`[SegmentPublisher] Redis client error: ${err.message}`);
        this.connected = false;
      });

      await this.client.connect();
      this.connected = true;
      log(`[SegmentPublisher] Connected to Redis at ${this.redisUrl}`);
      return this.client;
    } catch (err: any) {
      log(`[SegmentPublisher] Failed to connect to Redis: ${err.message}`);
      this.connected = false;
      throw err;
    }
  }

  /**
   * Publish a session_start message to the stream.
   * Called once when the per-speaker pipeline is initialized.
   */
  async publishSessionStart(): Promise<void> {
    try {
      const client = await this.ensureConnected();

      const payload = JSON.stringify({
        type: 'session_start',
        token: this.token,
        uid: this.sessionUid,
        platform: this.platform,
        meeting_id: this.meetingId,
        start_timestamp: new Date(this.sessionStartMs).toISOString(),
      });

      await client.xAdd(this.segmentStreamKey, '*', { payload });
      log(`[SegmentPublisher] Published session_start for session ${this.sessionUid}`);
    } catch (err: any) {
      log(`[SegmentPublisher] Failed to publish session_start: ${err.message}`);
    }
  }

  /**
   * Publish a session_end message to the stream.
   * Called on pipeline cleanup.
   */
  async publishSessionEnd(): Promise<void> {
    try {
      const client = await this.ensureConnected();

      const payload = JSON.stringify({
        type: 'session_end',
        token: this.token,
        uid: this.sessionUid,
      });

      await client.xAdd(this.segmentStreamKey, '*', { payload });
      log(`[SegmentPublisher] Published session_end for session ${this.sessionUid}`);
    } catch (err: any) {
      log(`[SegmentPublisher] Failed to publish session_end: ${err.message}`);
    }
  }

  /**
   * Publish a transcription segment to Redis.
   * - XADD to transcription_segments stream (collector format: { payload: JSON })
   * - PUBLISH to meeting:{meetingId}:segments channel (flat JSON for gateway/dashboard)
   *
   * Errors are logged but do not throw (bot should not crash on Redis failure).
   */
  async publishSegment(segment: TranscriptionSegment): Promise<void> {
    try {
      const client = await this.ensureConnected();

      // XADD: collector format — single 'payload' field with JSON
      const payload = JSON.stringify({
        type: 'transcription',
        token: this.token,
        uid: this.sessionUid,
        platform: this.platform,
        meeting_id: this.meetingId,
        segments: [{
          start: segment.start,
          end: segment.end,
          text: segment.text,
          language: segment.language,
          completed: segment.completed ?? true,
          speaker: segment.speaker,
          segment_id: segment.segment_id,
          ...(segment.absolute_start_time && { absolute_start_time: segment.absolute_start_time }),
          ...(segment.absolute_end_time && { absolute_end_time: segment.absolute_end_time }),
        }],
      });

      await client.xAdd(this.segmentStreamKey, '*', { payload });

      // PUBLISH: flat JSON for real-time delivery via gateway → WebSocket → dashboard
      const channel = `meeting:${this.meetingId}:segments`;
      await client.publish(channel, JSON.stringify({
        ...segment,
        meeting_id: this.meetingId,
        timestamp: Date.now(),
        ...(segment.absolute_start_time && { absolute_start_time: segment.absolute_start_time }),
        ...(segment.absolute_end_time && { absolute_end_time: segment.absolute_end_time }),
      }));
    } catch (err: any) {
      log(`[SegmentPublisher] Failed to publish segment: ${err.message}`);
    }
  }

  /**
   * Publish a speaker lifecycle event to Redis.
   * Format matches what transcription-collector expects:
   *   stream: speaker_events_relative
   *   fields: uid, relative_client_timestamp_ms, event_type, participant_name
   *
   * Errors are logged but do not throw.
   */
  async publishSpeakerEvent(event: SpeakerEvent): Promise<void> {
    try {
      const client = await this.ensureConnected();

      // Map bot event types to collector's expected event_type values
      const eventTypeMap: Record<string, string> = {
        'joined': 'SPEAKER_START',
        'started_speaking': 'SPEAKER_START',
        'stopped_speaking': 'SPEAKER_END',
        'left': 'SPEAKER_END',
      };

      const fields: Record<string, string> = {
        uid: this.sessionUid,
        relative_client_timestamp_ms: String(event.timestamp - this.sessionStartMs),
        event_type: eventTypeMap[event.type] || event.type,
        participant_name: event.speaker,
        meeting_id: this.meetingId,
      };

      await client.xAdd(this.speakerEventStreamKey, '*', fields);
    } catch (err: any) {
      log(`[SegmentPublisher] Failed to publish speaker event: ${err.message}`);
    }
  }

  /**
   * Disconnect from Redis and clean up.
   */
  async close(): Promise<void> {
    if (this.client) {
      try {
        await this.client.disconnect();
        log(`[SegmentPublisher] Redis connection closed`);
      } catch (err: any) {
        log(`[SegmentPublisher] Error closing Redis connection: ${err.message}`);
      }
      this.client = null;
      this.connected = false;
    }
  }
}
