import { create } from "zustand";
import type { Meeting, TranscriptSegment, Platform, MeetingStatus } from "@/types/vexa";
import { deduplicateSegments } from "@vexaai/transcript-rendering";

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

    // Check if segment already exists (by absolute_start_time)
    const existingIndex = liveTranscripts.findIndex(
      (t) => t.absolute_start_time === segment.absolute_start_time
    );

    if (existingIndex !== -1) {
      // Update existing segment
      const existing = liveTranscripts[existingIndex];
      
      // For real-time updates: always update if text is different, regardless of timestamp
      // This ensures that segment changes are reflected immediately
      const existingText = (existing.text || "").trim();
      const newText = (segment.text || "").trim();
      const completedChanged = Boolean(existing.completed) !== Boolean(segment.completed);
      if (existingText !== newText || completedChanged) {
        // Text changed - always update (real-time transcription update)
        const updated = [...liveTranscripts];
        updated[existingIndex] = segment;
        // Sort and deduplicate after update
        const sorted = updated.sort(
          (a, b) => a.absolute_start_time.localeCompare(b.absolute_start_time)
        );
        const deduped = deduplicateSegments(sorted);
        set({ liveTranscripts: deduped });
      } else if (segment.updated_at && existing.updated_at) {
        // Text is the same - use timestamp-based deduplication
        if (new Date(segment.updated_at) > new Date(existing.updated_at)) {
          const updated = [...liveTranscripts];
          updated[existingIndex] = segment;
          // Sort and deduplicate after update
          const sorted = updated.sort(
            (a, b) => a.absolute_start_time.localeCompare(b.absolute_start_time)
          );
          const deduped = deduplicateSegments(sorted);
          set({ liveTranscripts: deduped });
        }
      } else {
        // No timestamps - update anyway (fallback)
        const updated = [...liveTranscripts];
        updated[existingIndex] = segment;
        // Sort and deduplicate after update
        const sorted = updated.sort(
          (a, b) => a.absolute_start_time.localeCompare(b.absolute_start_time)
        );
        const deduped = deduplicateSegments(sorted);
        set({ liveTranscripts: deduped });
      }
    } else {
      // Add new segment and sort by absolute_start_time
      const updated = [...liveTranscripts, segment].sort(
        (a, b) => a.absolute_start_time.localeCompare(b.absolute_start_time)
      );
      // Deduplicate overlapping segments
      const deduped = deduplicateSegments(updated);
      set({ liveTranscripts: deduped });
    }
  },

  updateLiveTranscript: (segment: TranscriptSegment) => {
    const { liveTranscripts } = get();
    const updated = liveTranscripts.map((t) =>
      t.absolute_start_time === segment.absolute_start_time ? segment : t
    );
    // Sort and deduplicate after update
    const sorted = updated.sort(
      (a, b) => a.absolute_start_time.localeCompare(b.absolute_start_time)
    );
    const deduped = deduplicateSegments(sorted);
    set({ liveTranscripts: deduped });
  },

  bootstrapLiveTranscripts: (segments: TranscriptSegment[]) => {
    // Filter out segments without absolute_start_time or empty text
    const validSegments = segments.filter(
      (seg) => seg.absolute_start_time && seg.text?.trim()
    );

    // Create a map keyed by absolute_start_time (deduplication key)
    const transcriptMap = new Map<string, TranscriptSegment>();
    for (const segment of validSegments) {
      transcriptMap.set(segment.absolute_start_time, segment);
    }

    // Convert map to array and sort by absolute_start_time
    const sortedTranscripts = Array.from(transcriptMap.values()).sort(
      (a, b) => a.absolute_start_time.localeCompare(b.absolute_start_time)
    );

    // Deduplicate overlapping segments (expansion, tail-repeat, containment)
    const dedupedTranscripts = deduplicateSegments(sortedTranscripts);

    set({ liveTranscripts: dedupedTranscripts });
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
