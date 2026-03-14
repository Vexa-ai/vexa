import { createClient, RedisClientType } from 'redis';
import { log } from '../utils';

export interface TranscriptionSegment {
  speaker: string;
  text: string;
  start: number;
  end: number;
  language: string;
}

export interface SpeakerEvent {
  speaker: string;
  type: 'joined' | 'left' | 'started_speaking' | 'stopped_speaking';
  timestamp: number;
}

export interface SegmentPublisherConfig {
  /** Redis URL, e.g. "redis://localhost:6379" */
  redisUrl: string;
  /** Meeting ID used for pub/sub channel naming */
  meetingId: string;
  /** Redis stream key for transcription segments. Default: "transcription_segments" */
  segmentStreamKey?: string;
  /** Redis stream key for speaker events. Default: "speaker_events" */
  speakerEventStreamKey?: string;
}

/**
 * Publishes transcription segments and speaker events to Redis.
 * Two operations per segment:
 *   1. XADD to a Redis stream (for persistence via transcription-collector)
 *   2. PUBLISH to a pub/sub channel (for real-time delivery via gateway)
 */
export class SegmentPublisher {
  private redisUrl: string;
  private meetingId: string;
  private segmentStreamKey: string;
  private speakerEventStreamKey: string;
  private client: RedisClientType | null = null;
  private connected: boolean = false;

  constructor(config: SegmentPublisherConfig) {
    this.redisUrl = config.redisUrl;
    this.meetingId = config.meetingId;
    this.segmentStreamKey = config.segmentStreamKey ?? 'transcription_segments';
    this.speakerEventStreamKey = config.speakerEventStreamKey ?? 'speaker_events';
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
   * Publish a transcription segment to Redis.
   * - XADD to transcription_segments stream
   * - PUBLISH to meeting:{meetingId}:segments channel
   *
   * Errors are logged but do not throw (bot should not crash on Redis failure).
   */
  async publishSegment(segment: TranscriptionSegment): Promise<void> {
    try {
      const client = await this.ensureConnected();

      const fields: Record<string, string> = {
        speaker: segment.speaker,
        text: segment.text,
        start: segment.start.toString(),
        end: segment.end.toString(),
        language: segment.language,
        meeting_id: this.meetingId,
        timestamp: Date.now().toString(),
      };

      // XADD to the stream for persistence
      await client.xAdd(this.segmentStreamKey, '*', fields);

      // PUBLISH to pub/sub channel for real-time delivery
      const channel = `meeting:${this.meetingId}:segments`;
      await client.publish(channel, JSON.stringify({
        ...segment,
        meeting_id: this.meetingId,
        timestamp: Date.now(),
      }));
    } catch (err: any) {
      log(`[SegmentPublisher] Failed to publish segment: ${err.message}`);
    }
  }

  /**
   * Publish a speaker lifecycle event to Redis.
   * - XADD to speaker_events stream
   *
   * Errors are logged but do not throw.
   */
  async publishSpeakerEvent(event: SpeakerEvent): Promise<void> {
    try {
      const client = await this.ensureConnected();

      const fields: Record<string, string> = {
        speaker: event.speaker,
        type: event.type,
        timestamp: event.timestamp.toString(),
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
