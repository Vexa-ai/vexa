import type {
  Meeting,
  TranscriptSegment,
  CreateBotRequest,
  BotConfigUpdate,
  Platform,
  RecordingData,
} from "@/types/vexa";

class VexaAPIError extends Error {
  constructor(
    message: string,
    public status: number,
    public details?: unknown
  ) {
    super(message);
    this.name = "VexaAPIError";
  }
}

async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const errorText = await response.text();
    let details: unknown;
    let errorMessage = `API request failed: ${response.statusText}`;

    try {
      details = JSON.parse(errorText);
      // Extract error message from common API error formats
      if (typeof details === "object" && details !== null) {
        const errorObj = details as Record<string, unknown>;
        // FastAPI style: { "detail": "error message" }
        if (typeof errorObj.detail === "string") {
          errorMessage = errorObj.detail;
        }
        // Alternative: { "error": "error message" }
        else if (typeof errorObj.error === "string") {
          errorMessage = errorObj.error;
        }
        // Alternative: { "message": "error message" }
        else if (typeof errorObj.message === "string") {
          errorMessage = errorObj.message;
        }
      }
    } catch {
      details = errorText;
      if (errorText) {
        errorMessage = errorText;
      }
    }

    throw new VexaAPIError(errorMessage, response.status, details);
  }
  return response.json();
}

// Map raw API meeting to our Meeting type
interface RawMeeting {
  id: number;
  user_id?: number;
  platform: Platform;
  native_meeting_id: string;
  constructed_meeting_url?: string;
  status: string;
  start_time: string | null;
  end_time: string | null;
  bot_container_id: string | null;
  data: Record<string, unknown>;
  created_at: string;
  updated_at?: string;
}

function mapMeeting(raw: RawMeeting): Meeting {
  return {
    id: raw.id.toString(),
    platform: raw.platform,
    platform_specific_id: raw.native_meeting_id,
    status: raw.status as Meeting["status"],
    start_time: raw.start_time,
    end_time: raw.end_time,
    bot_container_id: raw.bot_container_id,
    data: raw.data as Meeting["data"],
    created_at: raw.created_at,
    updated_at: raw.updated_at,
  };
}

export const vexaAPI = {
  // Meetings
  async getMeetings(): Promise<Meeting[]> {
    const response = await fetch("/api/vexa/meetings");
    const data = await handleResponse<{ meetings: RawMeeting[] }>(response);
    return (data.meetings || []).map(mapMeeting);
  },

  async getMeeting(id: string): Promise<Meeting> {
    const response = await fetch(`/api/vexa/meetings/${id}`);
    return handleResponse<Meeting>(response);
  },

  // Transcripts
  async getTranscripts(
    platform: Platform,
    nativeId: string
  ): Promise<TranscriptSegment[]> {
    const result = await this.getMeetingWithTranscripts(platform, nativeId);
    return result.segments;
  },

  // Get meeting info with transcripts - returns full meeting data from transcripts endpoint
  async getMeetingWithTranscripts(
    platform: Platform,
    nativeId: string
  ): Promise<{ meeting: Meeting; segments: TranscriptSegment[]; recordings: RecordingData[] }> {
    const response = await fetch(`/api/vexa/transcripts/${platform}/${nativeId}`);
    interface RawSegment {
      start: number;
      end: number;
      text: string;
      speaker: string | null;
      language: string;
      absolute_start_time: string;
      absolute_end_time: string;
      created_at: string;
    }
    interface RawTranscriptResponse {
      id: number;
      platform: Platform;
      native_meeting_id: string;
      constructed_meeting_url?: string;
      status: string;
      start_time: string | null;
      end_time: string | null;
      data?: Record<string, unknown>;
      error?: string;
      error_code?: string;
      failure_reason?: string;
      segments: RawSegment[];
      recordings?: RecordingData[];
    }
    const data = await handleResponse<RawTranscriptResponse>(response);

    // Map to Meeting type
    const meeting: Meeting = {
      id: data.id.toString(),
      platform: data.platform,
      platform_specific_id: data.native_meeting_id,
      status: data.status as Meeting["status"],
      start_time: data.start_time,
      end_time: data.end_time,
      bot_container_id: null,
      data: {
        ...(data.data || {}),
        error: data.error,
        error_code: data.error_code,
        failure_reason: data.failure_reason,
      } as Meeting["data"],
      created_at: data.start_time || "",
    };

    // Map segments
    const segments: TranscriptSegment[] = (data.segments || []).map((seg, index) => ({
      id: `${index}`,
      meeting_id: nativeId,
      start_time: seg.start,
      end_time: seg.end,
      absolute_start_time: seg.absolute_start_time,
      absolute_end_time: seg.absolute_end_time,
      text: seg.text,
      speaker: seg.speaker || "Unknown",
      language: seg.language,
      session_uid: "",
      created_at: seg.created_at,
    }));

    // Extract recordings from response (populated from meeting.data.recordings by backend)
    const recordings: RecordingData[] = data.recordings || [];

    return { meeting, segments, recordings };
  },

  // Create short-lived public transcript URL (for ChatGPT "Read from URL")
  async createTranscriptShare(
    platform: Platform,
    nativeId: string,
    meetingId?: string,
    ttlSeconds?: number
  ): Promise<{ share_id: string; url: string; expires_at: string; expires_in_seconds: number }> {
    const params = new URLSearchParams();
    if (meetingId) params.set("meeting_id", meetingId);
    if (ttlSeconds) params.set("ttl_seconds", String(ttlSeconds));
    const qs = params.toString();

    const response = await fetch(`/api/vexa/transcripts/${platform}/${nativeId}/share${qs ? `?${qs}` : ""}`, {
      method: "POST",
    });
    return handleResponse<{ share_id: string; url: string; expires_at: string; expires_in_seconds: number }>(response);
  },

  // Bots
  async createBot(request: CreateBotRequest): Promise<Meeting> {
    const response = await fetch("/api/vexa/bots", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
    const raw = await handleResponse<RawMeeting>(response);
    return mapMeeting(raw);
  },

  async stopBot(platform: Platform, nativeId: string): Promise<void> {
    const response = await fetch(`/api/vexa/bots/${platform}/${nativeId}`, {
      method: "DELETE",
    });
    if (!response.ok) {
      throw new VexaAPIError(
        "Failed to stop bot",
        response.status,
        await response.text()
      );
    }
  },

  async updateBotConfig(
    platform: Platform,
    nativeId: string,
    config: BotConfigUpdate
  ): Promise<void> {
    const response = await fetch(`/api/vexa/bots/${platform}/${nativeId}/config`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
    if (!response.ok) {
      const errorText = await response.text();
      let message = "Failed to update bot config";
      try {
        const parsed = JSON.parse(errorText) as Record<string, unknown>;
        if (typeof parsed.detail === "string") message = parsed.detail;
        else if (typeof parsed.error === "string") message = parsed.error;
        else if (typeof parsed.message === "string") message = parsed.message;
      } catch {
        if (errorText) message = errorText;
      }
      throw new VexaAPIError(message, response.status, errorText);
    }
  },

  // Bot status - check if bots are actually running
  async getBotStatus(): Promise<{ running_bots: Array<{ container_id: string; meeting_id: number; platform: string; native_meeting_id: string }> }> {
    const response = await fetch("/api/vexa/bots/status");
    return handleResponse<{ running_bots: Array<{ container_id: string; meeting_id: number; platform: string; native_meeting_id: string }> }>(response);
  },

  // Check if a specific bot is running
  async isBotRunning(platform: Platform, nativeId: string): Promise<boolean> {
    try {
      const status = await this.getBotStatus();
      return status.running_bots.some(
        (bot) => bot.platform === platform && bot.native_meeting_id === nativeId
      );
    } catch {
      return false;
    }
  },

  // Update meeting data (title, notes, participants, languages)
  async updateMeetingData(
    platform: Platform,
    nativeId: string,
    data: {
      name?: string;
      notes?: string;
      participants?: string[];
      languages?: string[];
    }
  ): Promise<Meeting> {
    const response = await fetch(`/api/vexa/meetings/${platform}/${nativeId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ data }),
    });
    const raw = await handleResponse<RawMeeting>(response);
    return mapMeeting(raw);
  },

  async deleteMeeting(platform: Platform, nativeId: string): Promise<void> {
    const response = await fetch(`/api/vexa/meetings/${platform}/${nativeId}`, {
      method: "DELETE",
    });
    if (!response.ok) {
      const errorText = await response.text();
      let message = "Failed to delete meeting";
      try {
        const parsed = JSON.parse(errorText) as Record<string, unknown>;
        if (typeof parsed.detail === "string") message = parsed.detail;
        else if (typeof parsed.error === "string") message = parsed.error;
        else if (typeof parsed.message === "string") message = parsed.message;
      } catch {
        if (errorText) message = errorText;
      }
      throw new VexaAPIError(message, response.status, errorText);
    }
  },

  // Chat messages captured by the bot from the meeting chat
  async getChatMessages(
    platform: Platform,
    nativeId: string
  ): Promise<{ messages: Array<{ sender: string; text: string; timestamp: number; is_from_bot: boolean }>; meeting_id: number }> {
    const response = await fetch(`/api/vexa/bots/${platform}/${nativeId}/chat`);
    return handleResponse(response);
  },

  // Recordings - get the proxied URL for streaming audio via /raw endpoint
  getRecordingAudioUrl(recordingId: number, mediaFileId: number): string {
    return `/api/vexa/recordings/${recordingId}/media/${mediaFileId}/raw`;
  },

  // Connection test
  async testConnection(): Promise<{ success: boolean; error?: string }> {
    try {
      const response = await fetch("/api/vexa/meetings");
      if (response.ok) {
        return { success: true };
      }
      return { success: false, error: `HTTP ${response.status}: ${response.statusText}` };
    } catch (error) {
      return { success: false, error: (error as Error).message };
    }
  },
};

export { VexaAPIError };
