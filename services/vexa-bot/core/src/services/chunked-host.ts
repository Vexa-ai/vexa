/**
 * Shared host wiring for ChunkedTranscriber — the ONE place that maps the
 * core's callbacks onto the frozen publisher envelope. Consumed by both the
 * ingest server (in-tab extension sessions) and the bot's Zoom/Teams mixed
 * paths, so the algorithm AND its publishing semantics stay identical
 * everywhere a single mixed channel is transcribed.
 *
 * Envelope rules implemented here (validated live on the extension path):
 *  - segment_id = `${sessionUid}:${coreSegmentId}` — stable across renames,
 *    PG upserts on (meeting_id, segment_id).
 *  - start/end are seconds relative to segmentPublisher.sessionStartMs;
 *    absolute_* are ISO wall-clock.
 *  - confirmed + surviving pending tail travel in ONE publishTranscript call
 *    (splitting them deletes the client's draft block — the "vanishing
 *    transcript" bug).
 *  - rename = clear the OLD name's pending (empty bundle) + republish the
 *    SAME segment ids under the new name.
 */

import { ChunkedTranscriberCallbacks, ChunkSegment } from './chunked-transcriber';
import { TranscriptionClient } from '@vexa/speaker-streams';
import { SegmentPublisher, TranscriptionSegment } from './segment-publisher';

export interface ChunkedHostDeps {
  transcriptionClient: TranscriptionClient;
  segmentPublisher: SegmentPublisher;
  /** Getter, not a value — the bot's language can change mid-session. Return
   *  undefined for auto-detect (enables the language-probability gate). */
  language: () => string | undefined;
  log?: (m: string) => void;
}

/** ChunkSegment (audio-time ms) → publisher TranscriptionSegment. */
export function mapChunkSegments(
  pub: SegmentPublisher,
  speaker: string,
  segments: ChunkSegment[],
  completed = true,
): TranscriptionSegment[] {
  return segments.map(s => ({
    speaker,
    text: s.text,
    start: (s.startMs - pub.sessionStartMs) / 1000,
    end: (s.endMs - pub.sessionStartMs) / 1000,
    language: s.language,
    completed,
    segment_id: `${pub.sessionUid}:${s.segmentId}`,
    absolute_start_time: new Date(s.startMs).toISOString(),
    absolute_end_time: new Date(s.endMs).toISOString(),
  }));
}

export function createChunkedHost(deps: ChunkedHostDeps): ChunkedTranscriberCallbacks {
  const pub = deps.segmentPublisher;
  return {
    log: deps.log,
    get language(): string | undefined { return deps.language(); },
    transcribe: (pcm, prompt) => deps.transcriptionClient.transcribe(pcm, deps.language(), prompt),
    publish: (speaker, confirmed, pending) => {
      void pub.publishTranscript(
        speaker,
        mapChunkSegments(pub, speaker, confirmed),
        mapChunkSegments(pub, speaker, pending, false),
      );
    },
    publishPending: (speaker, segments) => {
      void pub.publishTranscript(speaker, [], mapChunkSegments(pub, speaker, segments, false));
    },
    clearPending: (speaker) => {
      void pub.publishTranscript(speaker, [], []);
    },
    rename: (oldSpeaker, newSpeaker, segments) => {
      void pub.publishTranscript(oldSpeaker, [], []);
      void pub.publishTranscript(newSpeaker, mapChunkSegments(pub, newSpeaker, segments), []);
      deps.log?.(`[Chunked] republished ${segments.length} segment(s) "${oldSpeaker}" → "${newSpeaker}"`);
    },
  };
}
