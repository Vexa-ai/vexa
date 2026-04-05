import { create } from "zustand";
import type { Meeting, TranscriptSegment, Platform, MeetingStatus } from "@/types/vexa";

/**
 * Sort segments by speech time (start_time seconds), not buffer confirmation time.
 * start_time is relative to meeting start and reflects when speech occurred.
 * absolute_start_time reflects when the buffer was processed, which can be out of order
 * for different speakers (independent per-speaker buffers).
 */
function sortSegments(segments: TranscriptSegment[]): TranscriptSegment[] {
  return segments.sort((a, b) => {
    // Primary: sort by start_time (speech time in meeting)
    const aStart = a.start_time ?? 0;
    const bStart = b.start_time ?? 0;
    if (aStart !== bStart) return aStart - bStart;
    // Secondary: absolute_start_time for segments with same start_time
    return a.absolute_start_time.localeCompare(b.absolute_start_time);
  });
}

interface LiveMeetingState {
  // Current live meeting
  activeMeeting: Meeting | null;
  liveTranscripts: TranscriptSegment[];

  // Connection state
  isConnecting: boolean;
  isConnected: boolean;
  connectionError: string | null;

  // Bot state
  botStatus: MeetingStatus | null;

  // Actions
  setActiveMeeting: (meeting: Meeting | null) => void;
  addLiveTranscript: (segment: TranscriptSegment) => void;
  updateLiveTranscript: (segment: TranscriptSegment) => void;
  bootstrapLiveTranscripts: (segments: TranscriptSegment[]) => void;
  setBotStatus: (status: MeetingStatus) => void;
  setConnectionState: (isConnecting: boolean, isConnected: boolean, error?: string) => void;
  clearLiveSession: () => void;
}

export const useLiveStore = create<LiveMeetingState>((set, get) => ({
  activeMeeting: null,
  liveTranscripts: [],
  isConnecting: false,
  isConnected: false,
  connectionError: null,
  botStatus: null,

  setActiveMeeting: (meeting: Meeting | null) => {
    set({
      activeMeeting: meeting,
      botStatus: meeting?.status || null,
      liveTranscripts: [],
    });
  },

  addLiveTranscript: (segment: TranscriptSegment) => {
    const { liveTranscripts } = get();

    // Use segment_id for identity when available, fall back to absolute_start_time
    const segKey = segment.segment_id || segment.absolute_start_time;
    const existingIndex = liveTranscripts.findIndex(
      (t) => (t.segment_id || t.absolute_start_time) === segKey
    );

    let updated: TranscriptSegment[];

    if (existingIndex !== -1) {
      // Same segment — update in place (latest version wins)
      updated = [...liveTranscripts];
      updated[existingIndex] = segment;
    } else {
      updated = [...liveTranscripts, segment];

      // When a confirmed segment arrives, remove drafts from the same speaker
      // that overlap in time. This prevents the "show, disappear, come back"
      // flash where draft and confirmed coexist briefly.
      if (segment.completed && segment.speaker) {
        const segStart = segment.start_time ?? 0;
        const segEnd = segment.end_time ?? segStart;
        updated = updated.filter((t) => {
          if (t === segment) return true; // keep the new segment
          if (t.completed) return true; // keep other confirmed segments
          if (t.speaker !== segment.speaker) return true; // keep other speakers
          // Remove same-speaker drafts that overlap with this confirmed segment
          const tStart = t.start_time ?? 0;
          const tEnd = t.end_time ?? tStart;
          const overlaps = tStart < segEnd && tEnd > segStart;
          return !overlaps;
        });
      }
    }

    set({ liveTranscripts: sortSegments(updated) });
  },

  updateLiveTranscript: (segment: TranscriptSegment) => {
    const { liveTranscripts } = get();
    const segKey = segment.segment_id || segment.absolute_start_time;
    const updated = liveTranscripts.map((t) =>
      (t.segment_id || t.absolute_start_time) === segKey ? segment : t
    );
    set({ liveTranscripts: sortSegments(updated) });
  },

  bootstrapLiveTranscripts: (segments: TranscriptSegment[]) => {
    // Filter out segments without absolute_start_time or empty text
    const validSegments = segments.filter(
      (seg) => seg.absolute_start_time && seg.text?.trim()
    );

    // Dedup by segment_id (stable) or absolute_start_time (legacy)
    const transcriptMap = new Map<string, TranscriptSegment>();
    for (const segment of validSegments) {
      const key = segment.segment_id || segment.absolute_start_time;
      transcriptMap.set(key, segment);
    }

    set({ liveTranscripts: sortSegments(Array.from(transcriptMap.values())) });
  },

  setBotStatus: (status: MeetingStatus) => {
    const { activeMeeting } = get();
    set({
      botStatus: status,
      activeMeeting: activeMeeting ? { ...activeMeeting, status } : null,
    });
  },

  setConnectionState: (isConnecting: boolean, isConnected: boolean, error?: string) => {
    set({
      isConnecting,
      isConnected,
      connectionError: error || null,
    });
  },

  clearLiveSession: () => {
    set({
      activeMeeting: null,
      liveTranscripts: [],
      isConnecting: false,
      isConnected: false,
      connectionError: null,
      botStatus: null,
    });
  },
}));
