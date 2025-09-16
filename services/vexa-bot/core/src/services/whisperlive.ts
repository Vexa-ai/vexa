import { createClient, RedisClientType } from 'redis';
import { log } from '../utils';
import { BotConfig } from '../types';

export interface WhisperLiveConfig {
  redisUrl: string;
  maxClients?: number;
  whisperLiveUrl?: string;
}

export interface WhisperLiveConnection {
  socket: WebSocket | null;
  isServerReady: boolean;
  sessionUid: string;
  allocatedServerUrl: string | null;
  redisClient: any; // Simplified to avoid Redis type conflicts
}

export class WhisperLiveService {
  private config: WhisperLiveConfig;
  private connection: WhisperLiveConnection | null = null;

  constructor(config: WhisperLiveConfig) {
    this.config = config;
  }

  /**
   * Initialize Redis connection and allocate a WhisperLive server
   */
  async initialize(): Promise<string | null> {
    try {
      // Create Redis client
      const redisClient = createClient({ url: this.config.redisUrl });
      await redisClient.connect();
      
      // Allocate server
      const allocatedUrl = await this.allocateServer(redisClient);
      
      if (!allocatedUrl) {
        await redisClient.quit();
        return null;
      }

      // Store connection info
      this.connection = {
        socket: null,
        isServerReady: false,
        sessionUid: this.generateUUID(),
        allocatedServerUrl: allocatedUrl,
        redisClient
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
      model: null, // Let server use WHISPER_MODEL_SIZE from environment
      use_vad: false,
      platform: botConfig.platform,
      token: botConfig.token,
      meeting_id: botConfig.nativeMeetingId,
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
    
    if (failedUrl && this.connection?.redisClient) {
      try {
        // Deallocate the failed server
        await this.deallocateServer(failedUrl);
        // Remove from registry
        await this.connection.redisClient.zRem("wl:rank", failedUrl);
      } catch (error: any) {
        log(`[WhisperLive] Error handling failed candidate: ${error.message}`);
      }
    }
    
    // Get next available server
    return await this.allocateServer(this.connection?.redisClient || createClient({ url: this.config.redisUrl }));
  }

  /**
   * Atomically allocate a WhisperLive server using Redis Lua script
   */
  private async allocateServer(redisClient: any): Promise<string | null> {
    const maxClients = this.config.maxClients || 10;
    
    const luaScript = `
      -- Find server with lowest score that has capacity
      local servers = redis.call('ZRANGE', 'wl:rank', 0, -1, 'WITHSCORES')
      if #servers == 0 then
        return nil
      end
      
      -- Iterate through servers (format: url1, score1, url2, score2, ...)
      for i = 1, #servers, 2 do
        local url = servers[i]
        local score = tonumber(servers[i + 1])
        
        -- Check if server has capacity
        if score < tonumber(ARGV[1]) then
          -- Atomically increment score and return URL
          redis.call('ZINCRBY', 'wl:rank', 1, url)
          return url
        end
      end
      
      -- No server has capacity
      return nil
    `;
    
    try {
      log("[WhisperLive] Atomically allocating server using Lua script...");
      const allocatedUrl = await redisClient.eval(luaScript, {
        keys: [],
        arguments: [maxClients.toString()]
      }) as string | null;
      
      if (allocatedUrl) {
        log(`[WhisperLive] Allocated server: ${allocatedUrl} (capacity maxClients=${maxClients})`);
      } else {
        log(`[WhisperLive] No servers available with capacity (maxClients=${maxClients})`);
      }
      
      return allocatedUrl;
    } catch (error: any) {
      log(`[WhisperLive] Error in atomic server allocation: ${error.message}`);
      return null;
    }
  }

  /**
   * Deallocate a WhisperLive server (decrement its score)
   */
  private async deallocateServer(serverUrl: string): Promise<void> {
    if (!this.connection?.redisClient) return;
    
    try {
      log(`[WhisperLive] Deallocating server: ${serverUrl}`);
      await this.connection.redisClient.zIncrBy("wl:rank", -1, serverUrl);
      log(`[WhisperLive] Successfully deallocated server: ${serverUrl}`);
    } catch (error: any) {
      log(`[WhisperLive] Error deallocating server: ${error.message}`);
    }
  }

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

    if (this.connection?.redisClient) {
      try {
        await this.connection.redisClient.quit();
      } catch (error: any) {
        log(`[WhisperLive] Error closing Redis connection: ${error.message}`);
      }
    }

    this.connection = null;
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
