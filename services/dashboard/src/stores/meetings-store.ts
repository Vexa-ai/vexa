import { create } from "zustand";
import type { Meeting, TranscriptSegment, Platform, MeetingStatus, RecordingData, ChatMessage } from "@/types/vexa";
import { VexaAPIError, vexaAPI } from "@/lib/api";

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

  // Internal state for best-known-transcript model
  _confirmed: Map<string, TranscriptSegment>;
  _pendingBySpeaker: Map<string, TranscriptSegment[]>;

  // Pagination
  hasMore: boolean;
  isLoadingMore: boolean;

  // Loading states
  isLoadingMeetings: boolean;
  isLoadingMeeting: boolean;
  isLoadingTranscripts: boolean;
  isUpdatingMeeting: boolean;

  // Error states
  error: string | null;
  subscriptionRequired: boolean;

  // Filters (server-side)
  _filters: { search?: string; status?: string; platform?: string };

  // Actions
  fetchMeetings: (filters?: { search?: string; status?: string; platform?: string }) => Promise<void>;
  fetchMoreMeetings: () => Promise<void>;
  fetchMeeting: (id: string, options?: { silent?: boolean }) => Promise<void>;
  refreshMeeting: (id: string) => Promise<void>;
  fetchTranscripts: (platform: Platform, nativeId: string, meetingId?: string) => Promise<void>;
  updateMeetingData: (platform: Platform, nativeId: string, data: MeetingDataUpdate) => Promise<void>;
  deleteMeeting: (platform: Platform, nativeId: string, meetingId?: string) => Promise<void>;
  setCurrentMeeting: (meeting: Meeting | null) => void;
  clearCurrentMeeting: () => void;

  // Real-time updates
  bootstrapTranscripts: (segments: TranscriptSegment[]) => void;
  upsertTranscriptSegments: (confirmed: TranscriptSegment[], pending?: TranscriptSegment[], speaker?: string) => void;
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

/**
 * Recompute the sorted transcripts array from confirmed + pending maps.
 * Confirmed segments (by segment_id) + non-stale pending, sorted by absolute_start_time.
 * Pending is skipped if confirmed already has the same text for that speaker
 * (stale pending that hasn't been replaced by the speaker's next tick yet).
 */
function recomputeTranscripts(
  confirmed: Map<string, TranscriptSegment>,
  pendingBySpeaker: Map<string, TranscriptSegment[]>,
): TranscriptSegment[] {
  // Build set of confirmed texts per speaker for stale-pending filtering
  const confirmedBySpeaker = new Map<string, Set<string>>();
  for (const seg of confirmed.values()) {
    const speaker = seg.speaker || "";
    if (!confirmedBySpeaker.has(speaker)) confirmedBySpeaker.set(speaker, new Set());
    confirmedBySpeaker.get(speaker)!.add((seg.text || "").trim());
  }

  const all: TranscriptSegment[] = [...confirmed.values()];
  for (const [speaker, segs] of pendingBySpeaker) {
    const confirmedTexts = confirmedBySpeaker.get(speaker);
    for (const seg of segs) {
      // Skip stale pending that overlaps with a confirmed segment
      const pt = (seg.text || "").trim();
      let isStale = false;
      if (confirmedTexts) {
        for (const ct of confirmedTexts) {
          if (pt === ct || pt.startsWith(ct) || ct.startsWith(pt)) {
            isStale = true;
            break;
          }
        }
      }
      if (isStale) continue;
      all.push(seg);
    }
  }
  all.sort((a, b) => a.absolute_start_time.localeCompare(b.absolute_start_time));
  return all;
}

export const useMeetingsStore = create<MeetingsState>((set, get) => ({
  // Initial state
  meetings: [],
  currentMeeting: null,
  transcripts: [],
  recordings: [],
  chatMessages: [],
  _confirmed: new Map(),
  _pendingBySpeaker: new Map(),
  hasMore: false,
  isLoadingMore: false,
  _filters: {},
  isLoadingMeetings: false,
  isLoadingMeeting: false,
  isLoadingTranscripts: false,
  isUpdatingMeeting: false,
  error: null,
  subscriptionRequired: false,

  // Fetch first page of meetings (with optional server-side filters)
  fetchMeetings: async (filters?: { search?: string; status?: string; platform?: string }) => {
    const activeFilters = filters ?? get()._filters;
    set({ isLoadingMeetings: true, error: null, _filters: activeFilters });
    try {
      const result = await vexaAPI.getMeetings({ limit: 50, offset: 0, ...activeFilters });
      const meetings = result.meetings.filter((m) => !isHiddenDeletedMeeting(m));
      meetings.sort((a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      );
      set({ meetings, hasMore: result.has_more, isLoadingMeetings: false, subscriptionRequired: false });
    } catch (error) {
      if (error instanceof VexaAPIError && error.status === 402) {
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

  // Fetch next page and append
  fetchMoreMeetings: async () => {
    const { meetings, hasMore, isLoadingMore, _filters } = get();
    if (!hasMore || isLoadingMore) return;
    set({ isLoadingMore: true });
    try {
      const result = await vexaAPI.getMeetings({ limit: 50, offset: meetings.length, ..._filters });
      const newMeetings = result.meetings.filter((m) => !isHiddenDeletedMeeting(m));
      const merged = [...meetings, ...newMeetings];
      merged.sort((a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      );
      set({ meetings: merged, hasMore: result.has_more, isLoadingMore: false });
    } catch (error) {
      set({ isLoadingMore: false });
      console.error("Failed to load more meetings:", error);
    }
  },

  // Fetch single meeting — checks loaded list first, then fetches by ID
  // Use silent: true to avoid showing loading state (for polling/refresh)
  fetchMeeting: async (id: string, options?: { silent?: boolean }) => {
    const { silent = false } = options || {};

    if (!silent) {
      set({ isLoadingMeeting: true, error: null });
    }

    try {
      // Check already-loaded meetings first (may include paginated results)
      const { meetings: existing } = get();
      let meeting = existing.find((m) => m.id.toString() === id);

      if (!meeting) {
        // Not in local list — fetch directly by ID
        try {
          meeting = await vexaAPI.getMeeting(id);
        } catch (e) {
          if (e instanceof VexaAPIError && e.status === 404) {
            set({ error: `Meeting with ID ${id} not found`, isLoadingMeeting: false });
            return;
          }
          throw e;
        }
      }

      set({ currentMeeting: meeting, isLoadingMeeting: false });
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
      const meeting = await vexaAPI.getMeeting(id);
      if (meeting) {
        const { currentMeeting, meetings } = get();
        if (currentMeeting?.status !== meeting.status ||
            currentMeeting?.updated_at !== meeting.updated_at) {
          // Update in meetings list if present
          const updatedMeetings = meetings.map((m) =>
            m.id.toString() === id ? meeting : m
          );
          set({ meetings: updatedMeetings, currentMeeting: meeting });
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
      ...(shouldClearCurrent ? { currentMeeting: null, transcripts: [], recordings: [], chatMessages: [], _confirmed: new Map(), _pendingBySpeaker: new Map() } : {}),
    });
  },

  setCurrentMeeting: (meeting: Meeting | null) => {
    set({ currentMeeting: meeting });
  },

  clearCurrentMeeting: () => {
    set({
      currentMeeting: null, transcripts: [], recordings: [], chatMessages: [],
      _confirmed: new Map(), _pendingBySpeaker: new Map(),
    });
  },

  // Bootstrap from REST API: confirmed segments only, clears pending
  bootstrapTranscripts: (segments: TranscriptSegment[]) => {
    const confirmed = new Map<string, TranscriptSegment>();
    for (const seg of segments) {
      if (!seg.absolute_start_time || !seg.text?.trim()) continue;
      const key = seg.segment_id || seg.absolute_start_time;
      confirmed.set(key, seg);
    }

    const pendingBySpeaker = new Map<string, TranscriptSegment[]>();
    const transcripts = recomputeTranscripts(confirmed, pendingBySpeaker);

    const firstTime = transcripts.length > 0 ? transcripts[0].absolute_start_time : null;
    const { currentMeeting } = get();
    const updatedMeeting = firstTime && currentMeeting && !currentMeeting.start_time
      ? { ...currentMeeting, start_time: firstTime }
      : currentMeeting;

    set({
      _confirmed: confirmed, _pendingBySpeaker: pendingBySpeaker, transcripts,
      ...(updatedMeeting !== currentMeeting ? { currentMeeting: updatedMeeting } : {}),
    });
  },

  // WS update: append confirmed (by segment_id), replace pending for speaker
  upsertTranscriptSegments: (confirmedSegs: TranscriptSegment[], pendingSegs?: TranscriptSegment[], speaker?: string) => {
    const { _confirmed, _pendingBySpeaker } = get();

    // Append confirmed segments (keyed by segment_id — no duplicates possible)
    let changed = false;
    for (const seg of confirmedSegs) {
      if (!seg.absolute_start_time || !seg.text?.trim()) continue;
      const key = seg.segment_id || seg.absolute_start_time;
      _confirmed.set(key, seg);
      changed = true;
      // NOTE: do NOT clear other speakers' pending here.
      // Confirmed for speaker A often arrives inside speaker B's tick.
      // Clearing A's pending would make A's text vanish until A's next tick.
      // The stale pending is cleaned up naturally when A's next tick replaces it.
    }

    // Replace pending snapshot for THIS speaker only (full replace)
    if (speaker !== undefined && speaker !== null) {
      const validPending = (pendingSegs || []).filter(s =>
        s.absolute_start_time && s.text?.trim()
      );
      if (validPending.length > 0) {
        _pendingBySpeaker.set(speaker, validPending);
      } else {
        _pendingBySpeaker.delete(speaker);
      }
      changed = true;
    }

    if (!changed) return;

    const transcripts = recomputeTranscripts(_confirmed, _pendingBySpeaker);

    const firstTime = transcripts.length > 0 ? transcripts[0].absolute_start_time : null;
    const { currentMeeting } = get();
    const updatedMeeting = firstTime && currentMeeting && !currentMeeting.start_time
      ? { ...currentMeeting, start_time: firstTime }
      : currentMeeting;

    set({
      _confirmed, _pendingBySpeaker, transcripts,
      ...(updatedMeeting !== currentMeeting ? { currentMeeting: updatedMeeting } : {}),
    });
  },

  // Real-time: Add new transcript segment (legacy — treats as confirmed)
  addTranscriptSegment: (segment: TranscriptSegment) => {
    get().upsertTranscriptSegments([segment]);
  },

  // Real-time: Update existing transcript segment
  updateTranscriptSegment: (segment: TranscriptSegment) => {
    get().upsertTranscriptSegments([segment]);
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
