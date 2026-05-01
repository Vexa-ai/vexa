import { withBasePath } from "@/lib/base-path";
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
  async getMeetings(params?: {
    limit?: number;
    offset?: number;
    search?: string;
    status?: string;
    platform?: string;
  }): Promise<{ meetings: Meeting[]; has_more: boolean }> {
    const query = new URLSearchParams();
    if (params?.limit) query.set("limit", String(params.limit));
    if (params?.offset) query.set("offset", String(params.offset));
    if (params?.search) query.set("search", params.search);
    if (params?.status) query.set("status", params.status);
    if (params?.platform) query.set("platform", params.platform);
    const qs = query.toString();
    const response = await fetch(withBasePath(`/api/vexa/meetings${qs ? `?${qs}` : ""}`));
    const data = await handleResponse<{ meetings: RawMeeting[]; has_more?: boolean }>(response);
    return {
      meetings: (data.meetings || []).map(mapMeeting),
      has_more: data.has_more ?? false,
    };
  },

  async getMeeting(id: string): Promise<Meeting> {
    const response = await fetch(withBasePath(`/api/vexa/meetings/${id}`));
    const raw = await handleResponse<RawMeeting>(response);
    return mapMeeting(raw);
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
    nativeId: string,
    meetingId?: string
  ): Promise<{ meeting: Meeting; segments: TranscriptSegment[]; recordings: RecordingData[] }> {
    const params = meetingId ? `?meeting_id=${meetingId}` : "";
    const response = await fetch(withBasePath(`/api/vexa/transcripts/${platform}/${nativeId}${params}`));
    interface RawSegment {
      start: number;
      end: number;
      text: string;
      speaker: string | null;
      language: string;
      absolute_start_time: string;
      absolute_end_time: string;
      created_at: string;
      segment_id?: string | null;
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
      id: seg.segment_id || `${index}`,
      meeting_id: nativeId,
      start_time: seg.start,
      end_time: seg.end,
      absolute_start_time: seg.absolute_start_time,
      absolute_end_time: seg.absolute_end_time,
      text: seg.text,
      speaker: seg.speaker || "",
      language: seg.language,
      completed: true,
      session_uid: "",
      created_at: seg.created_at,
      segment_id: seg.segment_id || undefined,
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

    const response = await fetch(withBasePath(`/api/vexa/transcripts/${platform}/${nativeId}/share${qs ? `?${qs}` : ""}`), {
      method: "POST",
    });
    return handleResponse<{ share_id: string; url: string; expires_at: string; expires_in_seconds: number }>(response);
  },

  // Bots
  async createBot(request: CreateBotRequest): Promise<Meeting> {
    const response = await fetch(withBasePath("/api/vexa/bots"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
    const raw = await handleResponse<RawMeeting>(response);
    return mapMeeting(raw);
  },

  async stopBot(platform: Platform, nativeId: string): Promise<void> {
    const response = await fetch(withBasePath(`/api/vexa/bots/${platform}/${nativeId}`), {
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
    const response = await fetch(withBasePath(`/api/vexa/bots/${platform}/${nativeId}/config`), {
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
    const response = await fetch(withBasePath("/api/vexa/bots/status"));
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
    const response = await fetch(withBasePath(`/api/vexa/meetings/${platform}/${nativeId}`), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ data }),
    });
    const raw = await handleResponse<RawMeeting>(response);
    return mapMeeting(raw);
  },

  async deleteMeeting(platform: Platform, nativeId: string): Promise<void> {
    const response = await fetch(withBasePath(`/api/vexa/meetings/${platform}/${nativeId}`), {
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
    const response = await fetch(withBasePath(`/api/vexa/bots/${platform}/${nativeId}/chat`));
    return handleResponse(response);
  },

  // v0.10.5.3 Pack D-3 (#288 sibling): get a presigned S3/MinIO URL for
  // playback. The /download endpoint returns either a presigned URL (S3/
  // MinIO storage_backend — browser streams directly with HTTP Range
  // support) or a /raw proxy path (local storage_backend — explicit
  // decision per Pack P, not a runtime fallback). Pre-fix we always
  // returned the /raw proxy path which buffered the whole file in
  // meeting-api memory before serving (#288). For 24-min meetings @ 10 MB
  // that's ~10s of dead-air on first byte.
  async getRecordingAudioUrl(recordingId: number, mediaFileId: number): Promise<string> {
    const response = await fetch(
      withBasePath(`/api/vexa/recordings/${recordingId}/media/${mediaFileId}/download`)
    );
    if (!response.ok) {
      throw new Error(`Failed to get download URL: ${response.status}`);
    }
    const data = await response.json() as { download_url: string };
    // The /download endpoint returns either a full https://... presigned URL
    // (S3/MinIO) or a /recordings/.../raw relative path (local storage).
    // Pass through; if it's a relative path, prepend basePath. If it's an
    // absolute URL, leave as-is (browser fetches directly from S3).
    if (/^https?:\/\//.test(data.download_url)) {
      return data.download_url;
    }
    return withBasePath(`/api/vexa${data.download_url}`);
  },

  // Same shape for video (the /download endpoint serves both audio + video
  // — content_type determined by media_file format).
  async getRecordingVideoUrl(recordingId: number, mediaFileId: number): Promise<string> {
    return this.getRecordingAudioUrl(recordingId, mediaFileId);
  },

  // v0.10.5.3 Pack D-3: explicit synchronous fallback to the /raw endpoint
  // for callers that can't await (e.g. JSX src= prop on first paint). Used
  // by audio-player.tsx for the synchronous initial src; once the
  // presigned URL fetches, audio.src is updated to the streaming URL.
  // This is an explicit decision (file PII-D-3 in scope.yaml proves[]),
  // not a runtime fallback per Pack P.
  getRecordingAudioRawUrl(recordingId: number, mediaFileId: number): string {
    return withBasePath(`/api/vexa/recordings/${recordingId}/media/${mediaFileId}/raw`);
  },

  // Transcribe a recorded meeting (deferred transcription)
  async transcribeMeeting(
    meetingId: string | number,
    language?: string
  ): Promise<{ status: string; segment_count: number; language: string }> {
    const body: Record<string, string> = {};
    if (language) body.language = language;
    const response = await fetch(withBasePath(`/api/vexa/meetings/${meetingId}/transcribe`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return handleResponse<{ status: string; segment_count: number; language: string }>(response);
  },

  // Connection test
  async testConnection(): Promise<{ success: boolean; error?: string }> {
    try {
      const response = await fetch(withBasePath("/api/vexa/meetings"));
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
