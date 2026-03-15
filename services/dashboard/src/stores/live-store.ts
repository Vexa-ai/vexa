import { create } from "zustand";
import type { Meeting, TranscriptSegment, Platform, MeetingStatus } from "@/types/vexa";

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

    if (existingIndex !== -1) {
      // Same segment — update in place (latest version wins)
      const updated = [...liveTranscripts];
      updated[existingIndex] = segment;
      const sorted = updated.sort(
        (a, b) => a.absolute_start_time.localeCompare(b.absolute_start_time)
      );
      set({ liveTranscripts: sorted });
    } else {
      // New segment
      const updated = [...liveTranscripts, segment].sort(
        (a, b) => a.absolute_start_time.localeCompare(b.absolute_start_time)
      );
      set({ liveTranscripts: updated });
    }
  },

  updateLiveTranscript: (segment: TranscriptSegment) => {
    const { liveTranscripts } = get();
    const segKey = segment.segment_id || segment.absolute_start_time;
    const updated = liveTranscripts.map((t) =>
      (t.segment_id || t.absolute_start_time) === segKey ? segment : t
    );
    const sorted = updated.sort(
      (a, b) => a.absolute_start_time.localeCompare(b.absolute_start_time)
    );
    set({ liveTranscripts: sorted });
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

    const sorted = Array.from(transcriptMap.values()).sort(
      (a, b) => a.absolute_start_time.localeCompare(b.absolute_start_time)
    );

    set({ liveTranscripts: sorted });
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
