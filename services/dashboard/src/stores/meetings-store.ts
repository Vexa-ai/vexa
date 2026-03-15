import { create } from "zustand";
import type { Meeting, TranscriptSegment, Platform, MeetingStatus, RecordingData, ChatMessage } from "@/types/vexa";
import { VexaAPIError, vexaAPI } from "@/lib/api";
// deduplicateSegments removed — it merges time-overlapping segments which
// is wrong for per-speaker pipeline where concurrent speakers overlap.

interface MeetingDataUpdate {
  name?: string;
  notes?: string;
  participants?: string[];
  languages?: string[];
}

function isHiddenDeletedMeeting(meeting: Meeting): boolean {
  const redacted = meeting.data?.redacted === true;
  // Backend delete/anonymize flow clears native meeting id.
  const missingNativeId = !meeting.platform_specific_id;
  return redacted || missingNativeId;
}

interface MeetingsState {
  // Data
  meetings: Meeting[];
  currentMeeting: Meeting | null;
  transcripts: TranscriptSegment[];
  recordings: RecordingData[];
  chatMessages: ChatMessage[];

  // Loading states
  isLoadingMeetings: boolean;
  isLoadingMeeting: boolean;
  isLoadingTranscripts: boolean;
  isUpdatingMeeting: boolean;

  // Error states
  error: string | null;
  subscriptionRequired: boolean;

  // Actions
  fetchMeetings: () => Promise<void>;
  fetchMeeting: (id: string, options?: { silent?: boolean }) => Promise<void>;
  refreshMeeting: (id: string) => Promise<void>;
  fetchTranscripts: (platform: Platform, nativeId: string, meetingId?: string) => Promise<void>;
  updateMeetingData: (platform: Platform, nativeId: string, data: MeetingDataUpdate) => Promise<void>;
  deleteMeeting: (platform: Platform, nativeId: string, meetingId?: string) => Promise<void>;
  setCurrentMeeting: (meeting: Meeting | null) => void;
  clearCurrentMeeting: () => void;

  // Real-time updates
  bootstrapTranscripts: (segments: TranscriptSegment[]) => void;
  upsertTranscriptSegments: (segments: TranscriptSegment[]) => void;
  addTranscriptSegment: (segment: TranscriptSegment) => void;
  updateTranscriptSegment: (segment: TranscriptSegment) => void;
  updateMeetingStatus: (meetingId: string, status: MeetingStatus) => void;

  // Chat
  fetchChatMessages: (platform: Platform, nativeId: string) => Promise<void>;
  addChatMessage: (message: ChatMessage) => void;

  // Utilities
  clearError: () => void;
}

let isChatRouteUnavailable = false;
let hasLoggedChatRouteUnavailable = false;

export const useMeetingsStore = create<MeetingsState>((set, get) => ({
  // Initial state
  meetings: [],
  currentMeeting: null,
  transcripts: [],
  recordings: [],
  chatMessages: [],
  isLoadingMeetings: false,
  isLoadingMeeting: false,
  isLoadingTranscripts: false,
  isUpdatingMeeting: false,
  error: null,
  subscriptionRequired: false,

  // Fetch all meetings
  fetchMeetings: async () => {
    set({ isLoadingMeetings: true, error: null });
    try {
      const meetings = (await vexaAPI.getMeetings()).filter((m) => !isHiddenDeletedMeeting(m));
      // Sort by created_at descending (most recent first)
      meetings.sort((a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      );
      set({ meetings, isLoadingMeetings: false, subscriptionRequired: false });
    } catch (error) {
      if (error instanceof VexaAPIError && error.status === 402) {
        // Subscription required — keep any cached meetings but flag the state
        set({
          subscriptionRequired: true,
          isLoadingMeetings: false,
          error: null,
        });
        return;
      }
      set({
        error: (error as Error).message,
        isLoadingMeetings: false
      });
    }
  },

  // Fetch single meeting (from list since API doesn't support /meetings/{id})
  // Use silent: true to avoid showing loading state (for polling/refresh)
  fetchMeeting: async (id: string, options?: { silent?: boolean }) => {
    const { silent = false } = options || {};

    // Only show loading state on initial load (when no currentMeeting exists)
    if (!silent) {
      set({ isLoadingMeeting: true, error: null });
    }

    try {
      // Always fetch fresh data from the API to ensure we have the latest meeting state
      const meetings = (await vexaAPI.getMeetings()).filter((m) => !isHiddenDeletedMeeting(m));
      meetings.sort((a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      );
      set({ meetings });

      const meeting = meetings.find((m) => m.id.toString() === id);

      if (meeting) {
        set({ currentMeeting: meeting, isLoadingMeeting: false });
      } else {
        set({
          error: `Meeting with ID ${id} not found`,
          isLoadingMeeting: false
        });
      }
    } catch (error) {
      if (error instanceof VexaAPIError && error.status === 402) {
        set({ subscriptionRequired: true, isLoadingMeeting: false, error: null });
        return;
      }
      set({
        error: (error as Error).message,
        isLoadingMeeting: false
      });
    }
  },

  // Silently refresh meeting data (for polling without UI flicker)
  refreshMeeting: async (id: string) => {
    try {
      const meetings = (await vexaAPI.getMeetings()).filter((m) => !isHiddenDeletedMeeting(m));
      meetings.sort((a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      );

      const meeting = meetings.find((m) => m.id.toString() === id);

      if (meeting) {
        // Only update if something changed
        const { currentMeeting } = get();
        if (currentMeeting?.status !== meeting.status ||
            currentMeeting?.updated_at !== meeting.updated_at) {
          set({ meetings, currentMeeting: meeting });
        } else {
          set({ meetings });
        }
      }
    } catch (error) {
      // Silent refresh - don't show errors for polling failures
      console.error("Failed to refresh meeting:", error);
    }
  },

  // Fetch transcripts for a meeting
  fetchTranscripts: async (platform: Platform, nativeId: string, meetingId?: string) => {
    set({ isLoadingTranscripts: true, error: null });
    try {
      const result = await vexaAPI.getMeetingWithTranscripts(platform, nativeId, meetingId);
      // Reuse the same canonical pipeline as WS/bootstraps:
      // - filter invalid
      // - sort by absolute_start_time
      // - collapse overlap (containment / expansion / tail-repeat)
      get().bootstrapTranscripts(result.segments);
      // Store recordings from the transcript response
      if (result.recordings.length > 0) {
        set({ recordings: result.recordings });
      }
      set({ isLoadingTranscripts: false });
    } catch (error) {
      if (error instanceof VexaAPIError && error.status === 402) {
        set({ subscriptionRequired: true, isLoadingTranscripts: false, error: null });
        return;
      }
      set({
        error: (error as Error).message,
        isLoadingTranscripts: false
      });
    }
  },

  // Update meeting data (title, notes, etc.)
  updateMeetingData: async (platform: Platform, nativeId: string, data: MeetingDataUpdate) => {
    set({ isUpdatingMeeting: true });
    try {
      const updatedMeeting = await vexaAPI.updateMeetingData(platform, nativeId, data);

      // Update current meeting if it matches
      const { currentMeeting, meetings } = get();
      if (currentMeeting?.platform_specific_id === nativeId) {
        set({ currentMeeting: updatedMeeting });
      }

      // Update in meetings list
      const updatedMeetings = meetings.map((m) =>
        m.platform_specific_id === nativeId ? updatedMeeting : m
      );
      set({ meetings: updatedMeetings, isUpdatingMeeting: false });
    } catch (error) {
      set({ isUpdatingMeeting: false });
      throw error; // Re-throw so UI can handle it
    }
  },

  deleteMeeting: async (platform: Platform, nativeId: string, meetingId?: string) => {
    await vexaAPI.deleteMeeting(platform, nativeId);

    const targetId = meetingId ? String(meetingId) : null;
    const { meetings, currentMeeting } = get();

    const updatedMeetings = meetings.filter((m) => {
      if (targetId) return String(m.id) !== targetId;
      return !(m.platform === platform && m.platform_specific_id === nativeId);
    });

    const shouldClearCurrent =
      currentMeeting &&
      (targetId
        ? String(currentMeeting.id) === targetId
        : currentMeeting.platform === platform && currentMeeting.platform_specific_id === nativeId);

    set({
      meetings: updatedMeetings,
      ...(shouldClearCurrent ? { currentMeeting: null, transcripts: [], recordings: [], chatMessages: [] } : {}),
    });
  },

  setCurrentMeeting: (meeting: Meeting | null) => {
    set({ currentMeeting: meeting });
  },

  clearCurrentMeeting: () => {
    set({ currentMeeting: null, transcripts: [], recordings: [], chatMessages: [] });
  },

  // Bootstrap transcripts from REST API (Step 1 of algorithm)
  // Seeds the in-memory map keyed by absolute_start_time
  bootstrapTranscripts: (segments: TranscriptSegment[]) => {
    // Filter out segments without absolute_start_time or empty text
    const validSegments = segments.filter(
      (seg) => seg.absolute_start_time && seg.text?.trim()
    );

    // Key by segment_id (stable, per-speaker) or absolute_start_time (legacy)
    const transcriptMap = new Map<string, TranscriptSegment>();
    for (const segment of validSegments) {
      const key = segment.segment_id || segment.absolute_start_time;
      transcriptMap.set(key, segment);
    }

    // Sort by absolute_start_time
    const dedupedTranscripts = Array.from(transcriptMap.values()).sort(
      (a, b) => a.absolute_start_time.localeCompare(b.absolute_start_time)
    );

    // Get the first segment's absolute_start_time to use as meeting start time
    const firstSegmentTime = dedupedTranscripts.length > 0 
      ? dedupedTranscripts[0].absolute_start_time 
      : null;

    // Update current meeting's start_time if not set and we have a first segment
    const { currentMeeting } = get();
    const updatedMeeting = firstSegmentTime && currentMeeting && !currentMeeting.start_time
      ? {
          ...currentMeeting,
          start_time: firstSegmentTime,
        }
      : currentMeeting;

    set({ 
      transcripts: dedupedTranscripts,
      ...(updatedMeeting !== currentMeeting ? { currentMeeting: updatedMeeting } : {}),
    });
  },

  // Upsert segments from WebSocket (Step 2 of algorithm)
  // Implements deduplication by absolute_start_time with updated_at comparison
  upsertTranscriptSegments: (segments: TranscriptSegment[]) => {
    const { transcripts } = get();

    if (!segments || segments.length === 0) return;

    // Key by segment_id (stable, per-speaker) or absolute_start_time (legacy)
    const transcriptMap = new Map<string, TranscriptSegment>();
    for (const seg of transcripts) {
      const key = seg.segment_id || seg.absolute_start_time;
      if (key) transcriptMap.set(key, seg);
    }

    // Upsert new segments
    let hasUpdates = false;
    for (const segment of segments) {
      if (!segment.absolute_start_time || !segment.text?.trim()) continue;

      const key = segment.segment_id || segment.absolute_start_time;
      const existing = transcriptMap.get(key);

      if (existing) {
        const existingText = (existing.text || "").trim();
        const newText = (segment.text || "").trim();
        const completedChanged = Boolean(existing.completed) !== Boolean(segment.completed);
        if (existingText !== newText || completedChanged) {
          transcriptMap.set(key, segment);
          hasUpdates = true;
          continue;
        }
        // Same text — keep newer
        if (existing.updated_at && segment.updated_at && existing.updated_at >= segment.updated_at) {
          continue;
        }
      }

      transcriptMap.set(key, segment);
      hasUpdates = true;
    }

    // Sort by absolute_start_time
    const dedupedTranscripts = Array.from(transcriptMap.values()).sort(
      (a, b) => a.absolute_start_time.localeCompare(b.absolute_start_time)
    );
    
    // Get the first segment's absolute_start_time to use as meeting start time
    const firstSegmentTime = dedupedTranscripts.length > 0 
      ? dedupedTranscripts[0].absolute_start_time 
      : null;

    // Update current meeting's start_time if not set and we have a first segment
    const { currentMeeting } = get();
    const updatedMeeting = firstSegmentTime && currentMeeting && !currentMeeting.start_time
      ? {
          ...currentMeeting,
          start_time: firstSegmentTime,
        }
      : currentMeeting;
    
    // Update store immediately - Zustand's set() is synchronous, ensuring immediate UI updates
    // Always set to ensure React detects changes (new array reference)
    set({ 
      transcripts: dedupedTranscripts,
      ...(updatedMeeting !== currentMeeting ? { currentMeeting: updatedMeeting } : {}),
    });
  },

  // Real-time: Add new transcript segment (legacy method, kept for compatibility)
  addTranscriptSegment: (segment: TranscriptSegment) => {
    get().upsertTranscriptSegments([segment]);
  },

  // Real-time: Update existing transcript segment
  updateTranscriptSegment: (segment: TranscriptSegment) => {
    const { transcripts } = get();
    const segKey = segment.segment_id || segment.absolute_start_time;
    const updated = transcripts.map((t) =>
      (t.segment_id || t.absolute_start_time) === segKey ? segment : t
    );
    set({ transcripts: updated });
  },

  // Update meeting status from WebSocket
  updateMeetingStatus: (meetingId: string, status: MeetingStatus) => {
    const { meetings, currentMeeting } = get();
    const targetId = String(meetingId);

    // Update in meetings list
    const updatedMeetings = meetings.map((m) =>
      String(m.id) === targetId ? { ...m, status } : m
    );
    set({ meetings: updatedMeetings });

    // Update current meeting if it matches
    if (currentMeeting && String(currentMeeting.id) === targetId) {
      set({ currentMeeting: { ...currentMeeting, status } });
    }
  },

  // Fetch chat messages via REST API (bootstrap)
  fetchChatMessages: async (platform: Platform, nativeId: string) => {
    if (isChatRouteUnavailable) {
      return;
    }

    try {
      const result = await vexaAPI.getChatMessages(platform, nativeId);
      set({ chatMessages: result.messages });
    } catch (error) {
      if (error instanceof VexaAPIError && error.status === 404) {
        // Backward compatibility: older backends do not expose this endpoint.
        const isMissingRoute = error.message === "Not Found";
        if (isMissingRoute) {
          isChatRouteUnavailable = true;
          if (!hasLoggedChatRouteUnavailable) {
            hasLoggedChatRouteUnavailable = true;
            console.info("[Chat] Chat endpoint is not available on this backend; disabling chat bootstrap fetches.");
          }
        }

        // Non-fatal: chat may not exist for this meeting.
        set({ chatMessages: [] });
        return;
      }

      // Non-fatal — chat may not be available (network/auth/transient failures).
      console.error("[Chat] Failed to fetch chat messages:", error);
    }
  },

  // Add a single chat message from WebSocket (real-time)
  addChatMessage: (message: ChatMessage) => {
    const { chatMessages } = get();
    // Deduplicate by timestamp + sender + text
    const exists = chatMessages.some(
      (m) => m.timestamp === message.timestamp && m.sender === message.sender && m.text === message.text
    );
    if (!exists) {
      set({ chatMessages: [...chatMessages, message] });
    }
  },

  clearError: () => {
    set({ error: null });
  },
}));
