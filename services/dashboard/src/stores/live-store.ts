import { create } from "zustand";
import type { Meeting, TranscriptSegment, Platform, MeetingStatus } from "@/types/vexa";
import {
  sortByStartTime,
  addSegment,
  bootstrapSegments,
} from "@vexaai/transcript-rendering";

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
    set({ liveTranscripts: sortByStartTime(addSegment(liveTranscripts, segment)) });
  },

  updateLiveTranscript: (segment: TranscriptSegment) => {
    const { liveTranscripts } = get();
    set({ liveTranscripts: sortByStartTime(addSegment(liveTranscripts, segment)) });
  },

  bootstrapLiveTranscripts: (segments: TranscriptSegment[]) => {
    set({ liveTranscripts: sortByStartTime(bootstrapSegments(segments)) });
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
