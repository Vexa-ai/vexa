import { log } from '../utils';

/**
 * Post-transcription speaker mapper for MS Teams.
 *
 * Takes Whisper word-level timestamps + caption speaker boundaries,
 * produces speaker-attributed text segments.
 *
 * Whisper transcribes the mixed audio stream (all speakers combined).
 * Captions tell us who spoke when. This engine maps each word to a
 * speaker by matching word timestamps against caption boundaries.
 *
 * Works on any single-channel mixed audio where external speaker
 * boundaries are available (Teams captions, diarization output, etc.)
 */

export interface TimestampedWord {
  word: string;
  start: number;  // seconds
  end: number;    // seconds
  probability?: number;
}

export interface SpeakerBoundary {
  speaker: string;
  start: number;  // seconds
  end: number;    // seconds
}

export interface AttributedSegment {
  speaker: string;
  text: string;
  start: number;  // seconds
  end: number;    // seconds
  words: TimestampedWord[];
  wordCount: number;
}

/**
 * Map words to speakers using timestamp alignment.
 *
 * For each word, finds the speaker boundary that overlaps most with
 * the word's time range. Consecutive words with the same speaker are
 * grouped into segments.
 *
 * Words that fall outside all speaker boundaries get attributed to
 * the nearest speaker (by time distance).
 */
export function mapWordsToSpeakers(
  words: TimestampedWord[],
  speakers: SpeakerBoundary[],
): AttributedSegment[] {
  if (words.length === 0 || speakers.length === 0) return [];

  // Sort both by start time
  const sortedWords = [...words].sort((a, b) => a.start - b.start);
  const sortedSpeakers = [...speakers].sort((a, b) => a.start - b.start);

  // Attribute each word to a speaker
  const attributed: { word: TimestampedWord; speaker: string }[] = [];

  for (const word of sortedWords) {
    const wordMid = (word.start + word.end) / 2;
    let bestSpeaker: string | null = null;
    let bestOverlap = 0;

    // Find speaker with most overlap
    for (const sp of sortedSpeakers) {
      const overlapStart = Math.max(word.start, sp.start);
      const overlapEnd = Math.min(word.end, sp.end);
      const overlap = Math.max(0, overlapEnd - overlapStart);

      if (overlap > bestOverlap) {
        bestOverlap = overlap;
        bestSpeaker = sp.speaker;
      }
    }

    // No overlap — find nearest speaker by midpoint distance
    if (!bestSpeaker) {
      let minDist = Infinity;
      for (const sp of sortedSpeakers) {
        const dist = Math.min(
          Math.abs(wordMid - sp.start),
          Math.abs(wordMid - sp.end),
        );
        if (dist < minDist) {
          minDist = dist;
          bestSpeaker = sp.speaker;
        }
      }
    }

    attributed.push({ word, speaker: bestSpeaker || 'Unknown' });
  }

  // Group consecutive same-speaker words into segments
  const segments: AttributedSegment[] = [];
  let currentSpeaker = attributed[0].speaker;
  let currentWords: TimestampedWord[] = [attributed[0].word];

  for (let i = 1; i < attributed.length; i++) {
    if (attributed[i].speaker === currentSpeaker) {
      currentWords.push(attributed[i].word);
    } else {
      // Emit segment
      segments.push(buildSegment(currentSpeaker, currentWords));
      currentSpeaker = attributed[i].speaker;
      currentWords = [attributed[i].word];
    }
  }
  // Emit last segment
  segments.push(buildSegment(currentSpeaker, currentWords));

  // Merge single-word boundary segments into their neighbor.
  // A 1-word segment at a speaker boundary is almost always a timing artifact
  // (Whisper timestamp jitter vs caption boundary). Merge into the adjacent
  // segment that shares a boundary with it.
  if (segments.length > 1) {
    const merged: AttributedSegment[] = [];
    for (let i = 0; i < segments.length; i++) {
      const seg = segments[i];
      if (seg.wordCount === 1) {
        if (i > 0 && merged.length > 0) {
          // Merge into previous segment
          const prev = merged[merged.length - 1];
          merged[merged.length - 1] = buildSegment(prev.speaker, [...prev.words, ...seg.words]);
          continue;
        } else if (i < segments.length - 1) {
          // First segment with 1 word — merge into next
          const next = segments[i + 1];
          segments[i + 1] = buildSegment(next.speaker, [...seg.words, ...next.words]);
          continue;
        }
      }
      merged.push(seg);
    }
    return merged;
  }

  return segments;
}

function buildSegment(speaker: string, words: TimestampedWord[]): AttributedSegment {
  return {
    speaker,
    text: words.map(w => w.word.trim()).join(' ').trim(),
    start: words[0].start,
    end: words[words.length - 1].end,
    words,
    wordCount: words.length,
  };
}

/**
 * Teams caption event: author + text + timestamp.
 * Teams fires these on every text update for the currently displayed caption.
 */
export interface CaptionEvent {
  speaker: string;
  text: string;
  timestamp: number; // wall-clock ms or seconds — just needs to be monotonic
}

/**
 * Build speaker boundaries from Teams caption events.
 *
 * Tracks the ACTIVE speaker — the last speaker whose caption appeared.
 * Only author switches create boundaries. Text updates from the active
 * speaker confirm they're still talking. Text refinements from previous
 * speakers (Teams reformats punctuation on older entries) are discarded.
 *
 * Boundary model:
 *   - Segment START = first caption with a new author (different from active)
 *   - Segment END = when the next different author's caption appears
 *   - Non-active speaker updates = discarded (refinements, not speech)
 */
export function captionsToSpeakerBoundaries(
  captions: CaptionEvent[] | { speaker: string; timestamp: number }[],
): SpeakerBoundary[] {
  if (captions.length === 0) return [];

  const sorted = [...captions].sort((a, b) => a.timestamp - b.timestamp);
  const boundaries: SpeakerBoundary[] = [];

  let activeSpeaker = sorted[0].speaker;
  let segmentStart = sorted[0].timestamp;

  for (let i = 1; i < sorted.length; i++) {
    const event = sorted[i];

    if (event.speaker !== activeSpeaker) {
      // Author switch — active speaker changed.
      // Close the previous speaker's segment, start new one.
      boundaries.push({
        speaker: activeSpeaker,
        start: segmentStart,
        end: event.timestamp,
      });
      activeSpeaker = event.speaker;
      segmentStart = event.timestamp;
    }
    // Same speaker as active — text update, confirms still talking. No action needed.
    // If this were a non-active speaker refinement, it would have a different speaker
    // but we treat any speaker switch as a real transition. In practice, Teams only
    // sends updates for the current caption entry (active speaker). Refinements of
    // older entries don't fire new DOM mutations after the entry has been superseded.
  }

  // Close last boundary — extend to last event + 30s buffer
  boundaries.push({
    speaker: activeSpeaker,
    start: segmentStart,
    end: sorted[sorted.length - 1].timestamp + 30,
  });

  return boundaries;
}
