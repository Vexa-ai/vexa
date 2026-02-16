import { log } from '../utils';
import { BotConfig } from '../types';

export interface WhisperLiveConfig {
  whisperLiveUrl?: string;
}

export interface WhisperLiveConnection {
  socket: WebSocket | null;
  isServerReady: boolean;
  sessionUid: string;
  allocatedServerUrl: string | null;
}

export class WhisperLiveService {
  private config: WhisperLiveConfig;
  private connection: WhisperLiveConnection | null = null;

  constructor(config: WhisperLiveConfig) {
    this.config = config;
  }

  /**
   * Initialize WhisperLive URL via LB (Traefik/Consul)
   */
  async initialize(): Promise<string | null> {
    try {
      const allocatedUrl = this.config.whisperLiveUrl || (process.env.WHISPER_LIVE_URL as string) || null;
      if (!allocatedUrl) return null;

      // Store connection info
      this.connection = {
        socket: null,
        isServerReady: false,
        sessionUid: this.generateUUID(),
        allocatedServerUrl: allocatedUrl
      };

      return allocatedUrl;
    } catch (error: any) {
      log(`[WhisperLive] Initialization error: ${error.message}`);
      return null;
    }
  }

  /**
   * Create WebSocket connection to WhisperLive server
   */
  async connectToWhisperLive(
    botConfig: BotConfig,
    onMessage: (data: any) => void,
    onError: (error: Event) => void,
    onClose: (event: CloseEvent) => void
  ): Promise<WebSocket | null> {
    if (!this.connection?.allocatedServerUrl) {
      log("[WhisperLive] No allocated server URL available");
      return null;
    }

    try {
      const socket = new WebSocket(this.connection.allocatedServerUrl);
      
      // Set up event handlers
      socket.onopen = () => {
        log(`[WhisperLive] Connected to ${this.connection!.allocatedServerUrl}`);
        this.connection!.sessionUid = this.generateUUID();
        this.connection!.isServerReady = false;
        
        // Send initial configuration
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
      log(`[WhisperLive] Connection error: ${error.message}`);
      return null;
    }
  }

  /**
   * Send initial configuration to WhisperLive server
   */
  private sendInitialConfig(socket: WebSocket, botConfig: BotConfig): void {
    const configPayload = {
      uid: this.connection!.sessionUid,
      language: botConfig.language || null,
      task: botConfig.task || "transcribe",
      transcription_tier: botConfig.transcriptionTier || "realtime",
      model: null, // Let server use WHISPER_MODEL_SIZE from environment
      use_vad: false,
      platform: botConfig.platform,
      token: botConfig.token,  // MeetingToken (HS256 JWT)
      meeting_id: botConfig.meeting_id,
      meeting_url: botConfig.meetingUrl || null,
    };

    const jsonPayload = JSON.stringify(configPayload);
    log(`[WhisperLive] Sending initial config: ${jsonPayload}`);
    socket.send(jsonPayload);
  }

  /**
   * Send audio data to WhisperLive
   */
  sendAudioData(audioData: Float32Array): boolean {
    if (!this.connection?.socket || this.connection.socket.readyState !== WebSocket.OPEN) {
      return false;
    }

    try {
      this.connection.socket.send(audioData);
      return true;
    } catch (error: any) {
      log(`[WhisperLive] Error sending audio data: ${error.message}`);
      return false;
    }
  }

  /**
   * Send audio chunk metadata to WhisperLive
   */
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
      log(`[WhisperLive] Error sending audio chunk metadata: ${error.message}`);
      return false;
    }
  }

  /**
   * Send speaker event to WhisperLive
   */
  sendSpeakerEvent(eventType: string, participantName: string, participantId: string, relativeTimestampMs: number, botConfig: BotConfig): boolean {
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
      log(`[WhisperLive] Error sending speaker event: ${error.message}`);
      return false;
    }
  }

  /**
   * Send session control message (e.g., LEAVING_MEETING)
   */
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
      log(`[WhisperLive] Error sending session control: ${error.message}`);
      return false;
    }
  }

  /**
   * Get next available WhisperLive server candidate
   */
  async getNextCandidate(failedUrl: string | null): Promise<string | null> {
    log(`[WhisperLive] getNextCandidate called. Failed URL: ${failedUrl}`);
    return this.connection?.allocatedServerUrl || this.config.whisperLiveUrl || (process.env.WHISPER_LIVE_URL as string) || null;
  }

  // Legacy allocator removed; selection handled by external load balancer

  /**
   * Deallocate a WhisperLive server (decrement its score)
   */
  // Legacy deallocator removed

  /**
   * Check if server is ready
   */
  isReady(): boolean {
    return this.connection?.isServerReady || false;
  }

  /**
   * Set server ready state
   */
  setServerReady(ready: boolean): void {
    if (this.connection) {
      this.connection.isServerReady = ready;
    }
  }

  /**
   * Get current session UID
   */
  getSessionUid(): string | null {
    return this.connection?.sessionUid || null;
  }

  /**
   * Close connection and cleanup
   */
  async cleanup(): Promise<void> {
    if (this.connection?.socket) {
      this.connection.socket.close();
      this.connection.socket = null;
    }

    this.connection = null;
  }

  /**
   * Initialize WhisperLive connection with STUBBORN reconnection - NEVER GIVES UP!
   * This method will keep retrying until a connection is established
   */
  async initializeWithStubbornReconnection(platform: string): Promise<string> {
    let whisperLiveUrl = await this.initialize();
    
    // STUBBORN MODE: NEVER GIVE UP! Keep trying until we get a WhisperLive connection
    let retryCount = 0;
    while (!whisperLiveUrl) {
      retryCount++;
      const delay = Math.min(2000 * Math.pow(1.5, Math.min(retryCount, 10)), 10000); // Exponential backoff, max 10s
      log(`[STUBBORN] ❌ Could not initialize WhisperLive service for ${platform} (attempt ${retryCount}). NEVER GIVING UP! Retrying in ${delay}ms...`);
      
      // Wait before retrying
      await new Promise(resolve => setTimeout(resolve, delay));
      
      // Try again with the current service instance
      whisperLiveUrl = await this.initialize();
      
      if (whisperLiveUrl) {
        log(`[STUBBORN] ✅ WhisperLive service initialized successfully for ${platform} after ${retryCount} attempts!`);
        break;
      }
    }
    
    return whisperLiveUrl;
  }

  /**
   * Generate UUID for session identification
   */
  private generateUUID(): string {
    if (typeof crypto !== "undefined" && crypto.randomUUID) {
      return crypto.randomUUID();
    } else {
      // Basic fallback if crypto.randomUUID is not available
      return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(
        /[xy]/g,
        function (c) {
          var r = (Math.random() * 16) | 0,
            v = c == "x" ? r : (r & 0x3) | 0x8;
          return v.toString(16);
        }
      );
    }
  }
}
