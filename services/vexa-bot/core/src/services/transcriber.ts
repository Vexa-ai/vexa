import { log } from '../utils';
import { BotConfig } from '../types';

export interface TranscriberConfig {
  transcriberWsUrl?: string;
  /** @deprecated Use transcriberWsUrl. Kept for backward compatibility. */
  whisperLiveUrl?: string;
}

export interface TranscriberConnection {
  socket: WebSocket | null;
  isServerReady: boolean;
  sessionUid: string;
  allocatedServerUrl: string | null;
}

export class TranscriberService {
  private config: TranscriberConfig;
  private connection: TranscriberConnection | null = null;

  constructor(config: TranscriberConfig) {
    this.config = config;
  }

  /**
   * Initialize transcriber WebSocket URL (e.g. from transcription-gateway or compatible backend).
   */
  async initialize(): Promise<string | null> {
    try {
      const allocatedUrl =
        this.config.transcriberWsUrl ||
        this.config.whisperLiveUrl ||
        (process.env.TRANSCRIBER_WS_URL as string) ||
        (process.env.WHISPER_LIVE_URL as string) ||
        null;
      if (!allocatedUrl) return null;

      this.connection = {
        socket: null,
        isServerReady: false,
        sessionUid: this.generateUUID(),
        allocatedServerUrl: allocatedUrl
      };

      return allocatedUrl;
    } catch (error: any) {
      log(`[Transcriber] Initialization error: ${error.message}`);
      return null;
    }
  }

  /**
   * Create WebSocket connection to transcriber backend.
   */
  async connectToTranscriber(
    botConfig: BotConfig,
    onMessage: (data: any) => void,
    onError: (error: Event) => void,
    onClose: (event: CloseEvent) => void
  ): Promise<WebSocket | null> {
    if (!this.connection?.allocatedServerUrl) {
      log("[Transcriber] No allocated server URL available");
      return null;
    }

    try {
      const socket = new WebSocket(this.connection.allocatedServerUrl);

      socket.onopen = () => {
        log(`[Transcriber] Connected to ${this.connection!.allocatedServerUrl}`);
        this.connection!.sessionUid = this.generateUUID();
        this.connection!.isServerReady = false;
        this.sendInitialConfig(socket, botConfig);
      };

      socket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        onMessage(data);
      };

      socket.onerror = onError;
      socket.onclose = onClose;

      this.connection.socket = socket;
      return socket;
    } catch (error: any) {
      log(`[Transcriber] Connection error: ${error.message}`);
      return null;
    }
  }

  private sendInitialConfig(socket: WebSocket, botConfig: BotConfig): void {
    const configPayload = {
      uid: this.connection!.sessionUid,
      language: botConfig.language || null,
      task: botConfig.task || "transcribe",
      model: null,
      use_vad: false,
      platform: botConfig.platform,
      token: botConfig.token,
      meeting_id: botConfig.meeting_id,
      meeting_url: botConfig.meetingUrl || null,
    };

    const jsonPayload = JSON.stringify(configPayload);
    log(`[Transcriber] Sending initial config: ${jsonPayload}`);
    socket.send(jsonPayload);
  }

  sendAudioData(audioData: Float32Array): boolean {
    if (!this.connection?.socket || this.connection.socket.readyState !== WebSocket.OPEN) {
      return false;
    }
    try {
      this.connection.socket.send(audioData);
      return true;
    } catch (error: any) {
      log(`[Transcriber] Error sending audio data: ${error.message}`);
      return false;
    }
  }

  sendAudioChunkMetadata(chunkLength: number, sampleRate: number): boolean {
    if (!this.connection?.socket || this.connection.socket.readyState !== WebSocket.OPEN) {
      return false;
    }
    const meta = {
      type: "audio_chunk_metadata",
      payload: {
        length: chunkLength,
        sample_rate: sampleRate,
        client_timestamp_ms: Date.now(),
      },
    };
    try {
      this.connection.socket.send(JSON.stringify(meta));
      return true;
    } catch (error: any) {
      log(`[Transcriber] Error sending audio chunk metadata: ${error.message}`);
      return false;
    }
  }

  sendSpeakerEvent(
    eventType: string,
    participantName: string,
    participantId: string,
    relativeTimestampMs: number,
    botConfig: BotConfig
  ): boolean {
    if (!this.connection?.socket || this.connection.socket.readyState !== WebSocket.OPEN) {
      return false;
    }
    const speakerEventMessage = {
      type: "speaker_activity",
      payload: {
        event_type: eventType,
        participant_name: participantName,
        participant_id_meet: participantId,
        relative_client_timestamp_ms: relativeTimestampMs,
        uid: this.connection.sessionUid,
        token: botConfig.token,
        platform: botConfig.platform,
        meeting_id: botConfig.nativeMeetingId,
        meeting_url: botConfig.meetingUrl
      }
    };
    try {
      this.connection.socket.send(JSON.stringify(speakerEventMessage));
      return true;
    } catch (error: any) {
      log(`[Transcriber] Error sending speaker event: ${error.message}`);
      return false;
    }
  }

  sendSessionControl(event: string, botConfig: BotConfig): boolean {
    if (!this.connection?.socket || this.connection.socket.readyState !== WebSocket.OPEN) {
      return false;
    }
    const sessionControlMessage = {
      type: "session_control",
      payload: {
        event: event,
        uid: this.connection.sessionUid,
        client_timestamp_ms: Date.now(),
        token: botConfig.token,
        platform: botConfig.platform,
        meeting_id: botConfig.nativeMeetingId
      }
    };
    try {
      this.connection.socket.send(JSON.stringify(sessionControlMessage));
      return true;
    } catch (error: any) {
      log(`[Transcriber] Error sending session control: ${error.message}`);
      return false;
    }
  }

  async getNextCandidate(failedUrl: string | null): Promise<string | null> {
    log(`[Transcriber] getNextCandidate called. Failed URL: ${failedUrl}`);
    return (
      this.connection?.allocatedServerUrl ||
      this.config.transcriberWsUrl ||
      this.config.whisperLiveUrl ||
      (process.env.TRANSCRIBER_WS_URL as string) ||
      (process.env.WHISPER_LIVE_URL as string) ||
      null
    );
  }

  isReady(): boolean {
    return this.connection?.isServerReady || false;
  }

  setServerReady(ready: boolean): void {
    if (this.connection) {
      this.connection.isServerReady = ready;
    }
  }

  getSessionUid(): string | null {
    return this.connection?.sessionUid || null;
  }

  async cleanup(): Promise<void> {
    if (this.connection?.socket) {
      this.connection.socket.close();
      this.connection.socket = null;
    }
    this.connection = null;
  }

  /**
   * Initialize transcriber connection with stubborn reconnection (retries until connected).
   */
  async initializeWithStubbornReconnection(platform: string): Promise<string> {
    let url = await this.initialize();
    let retryCount = 0;
    while (!url) {
      retryCount++;
      const delay = Math.min(2000 * Math.pow(1.5, Math.min(retryCount, 10)), 10000);
      log(
        `[Transcriber] Could not initialize transcriber for ${platform} (attempt ${retryCount}). Retrying in ${delay}ms...`
      );
      await new Promise((resolve) => setTimeout(resolve, delay));
      url = await this.initialize();
      if (url) {
        log(`[Transcriber] Transcriber initialized for ${platform} after ${retryCount} attempts.`);
        break;
      }
    }
    return url!;
  }

  private generateUUID(): string {
    if (typeof crypto !== "undefined" && crypto.randomUUID) {
      return crypto.randomUUID();
    }
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      const r = (Math.random() * 16) | 0;
      const v = c == "x" ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }
}
