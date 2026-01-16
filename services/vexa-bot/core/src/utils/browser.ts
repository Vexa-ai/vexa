/**
 * Browser context utilities and services
 * These classes run inside page.evaluate() browser context
 */

/**
 * Generate UUID for browser context
 */
export function generateBrowserUUID(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  } else {
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

/**
 * Browser-compatible AudioService for browser context
 */
export class BrowserAudioService {
  private config: any;
  private processor: any = null;
  private audioContext: AudioContext | null = null;
  private destinationNode: MediaStreamAudioDestinationNode | null = null;

  constructor(config: any) {
    this.config = config;
  }

  async findMediaElements(retries: number = 10, delay: number = 3000): Promise<HTMLMediaElement[]> {
    for (let i = 0; i < retries; i++) {
      // Get all media elements
      const allMediaElements = Array.from(document.querySelectorAll("audio, video")) as HTMLMediaElement[];
      (window as any).logBot(`[Audio] Attempt ${i + 1}/${retries}: Found ${allMediaElements.length} total media elements in DOM`);
      
      // Filter for active media elements with proper checks
      const mediaElements = allMediaElements.filter((el: any) => {
        // Check if element has srcObject
        if (!el.srcObject) {
          return false;
        }
        
        // Check if srcObject is a MediaStream
        if (!(el.srcObject instanceof MediaStream)) {
          return false;
        }
        
        // Check if MediaStream has audio tracks
        const audioTracks = el.srcObject.getAudioTracks();
        if (audioTracks.length === 0) {
          return false;
        }
        
        // Check if element is not paused (like Node.js version)
        if (el.paused) {
          (window as any).logBot(`[Audio] Element found but is paused (readyState: ${el.readyState})`);
          return false;
        }
        
        // Check readyState - prefer elements that have loaded metadata or more
        // 0 = HAVE_NOTHING, 1 = HAVE_METADATA, 2 = HAVE_CURRENT_DATA, 3 = HAVE_FUTURE_DATA, 4 = HAVE_ENOUGH_DATA
        if (el.readyState < 1) {
          (window as any).logBot(`[Audio] Element found but readyState is ${el.readyState} (HAVE_NOTHING)`);
          return false;
        }
        
        // Check if audio tracks are enabled
        const hasEnabledTracks = audioTracks.some((track: MediaStreamTrack) => track.enabled && !track.muted);
        if (!hasEnabledTracks) {
          (window as any).logBot(`[Audio] Element found but all audio tracks are disabled or muted`);
          return false;
        }
        
        return true;
      });

      if (mediaElements.length > 0) {
        (window as any).logBot(`✅ Found ${mediaElements.length} active media elements with audio tracks after ${i + 1} attempt(s).`);
        // Log details about found elements
        mediaElements.forEach((el: any, idx: number) => {
          const tracks = el.srcObject.getAudioTracks();
          (window as any).logBot(`  Element ${idx + 1}: paused=${el.paused}, readyState=${el.readyState}, tracks=${tracks.length}, enabled=${tracks.filter((t: MediaStreamTrack) => t.enabled).length}`);
        });
        return mediaElements;
      }
      
      // Enhanced diagnostic logging
      if (allMediaElements.length > 0) {
        (window as any).logBot(`[Audio] Found ${allMediaElements.length} media elements but none are active. Details:`);
        allMediaElements.forEach((el: any, idx: number) => {
          const hasSrcObject = !!el.srcObject;
          const isMediaStream = el.srcObject instanceof MediaStream;
          const audioTracks = isMediaStream ? el.srcObject.getAudioTracks().length : 0;
          (window as any).logBot(`  Element ${idx + 1}: paused=${el.paused}, readyState=${el.readyState}, hasSrcObject=${hasSrcObject}, isMediaStream=${isMediaStream}, audioTracks=${audioTracks}`);
        });
      } else {
        (window as any).logBot(`[Audio] No media elements found in DOM at all`);
      }
      
      (window as any).logBot(`[Audio] Retrying in ${delay}ms... (Attempt ${i + 2}/${retries})`);
      await new Promise(resolve => setTimeout(resolve, delay));
    }
    
    (window as any).logBot(`❌ No active media elements found after ${retries} attempts`);
    return [];
  }

  async createCombinedAudioStream(mediaElements: HTMLMediaElement[]): Promise<MediaStream> {
    if (mediaElements.length === 0) {
      throw new Error("No media elements provided for audio stream creation");
    }

    (window as any).logBot(`Found ${mediaElements.length} active media elements.`);
    if (!this.audioContext) {
      this.audioContext = new AudioContext();
    }
    if (!this.destinationNode) {
      this.destinationNode = this.audioContext.createMediaStreamDestination();
    }
    let sourcesConnected = 0;

    // Connect all media elements to the destination node
    mediaElements.forEach((element: any, index: number) => {
      try {
        // Ensure element is actually audible
        if (typeof element.muted === "boolean") element.muted = false;
        if (typeof element.volume === "number") element.volume = 1.0;
        if (typeof element.play === "function") {
          element.play().catch(() => {});
        }

        const elementStream =
          element.srcObject ||
          (element.captureStream && element.captureStream()) ||
          (element.mozCaptureStream && element.mozCaptureStream());

        // Debug audio tracks and unmute them
        if (elementStream instanceof MediaStream) {
          const audioTracks = elementStream.getAudioTracks();
          (window as any).logBot(`Element ${index + 1}: Found ${audioTracks.length} audio tracks`);
          audioTracks.forEach((track, trackIndex) => {
            (window as any).logBot(`  Track ${trackIndex}: enabled=${track.enabled}, muted=${track.muted}, label=${track.label}`);
            
            // Unmute muted audio tracks
            if (track.muted) {
              track.enabled = true;
              // Force unmute by setting muted to false
              try {
                (track as any).muted = false;
                (window as any).logBot(`  Unmuted track ${trackIndex} (enabled=${track.enabled}, muted=${track.muted})`);
              } catch (e: unknown) {
                const message = e instanceof Error ? e.message : String(e);
                (window as any).logBot(`  Could not unmute track ${trackIndex}: ${message}`);
              }
            }
          });
        }

        if (
          elementStream instanceof MediaStream &&
          elementStream.getAudioTracks().length > 0
        ) {
          // Connect regardless of the read-only muted flag; WebAudio can still pull samples
          const sourceNode = this.audioContext!.createMediaStreamSource(elementStream);
          sourceNode.connect(this.destinationNode!);
          sourcesConnected++;
          (window as any).logBot(`Connected audio stream from element ${index + 1}/${mediaElements.length}. Tracks=${elementStream.getAudioTracks().length}`);
        } else {
          (window as any).logBot(`Skipping element ${index + 1}: No audio tracks found`);
        }
      } catch (error: any) {
        (window as any).logBot(`Could not connect element ${index + 1}: ${error.message}`);
      }
    });

    if (sourcesConnected === 0) {
      throw new Error("Could not connect any audio streams. Check media permissions.");
    }

    (window as any).logBot(`Successfully combined ${sourcesConnected} audio streams.`);
    return this.destinationNode!.stream;
  }

  async initializeAudioProcessor(combinedStream: MediaStream): Promise<any> {
    // Reuse existing context if available
    if (!this.audioContext) {
      this.audioContext = new AudioContext();
    }
    if (!this.destinationNode) {
      this.destinationNode = this.audioContext.createMediaStreamDestination();
    }

    const mediaStream = this.audioContext.createMediaStreamSource(combinedStream);
    const recorder = this.audioContext.createScriptProcessor(
      this.config.bufferSize,
      this.config.inputChannels,
      this.config.outputChannels
    );
    const gainNode = this.audioContext.createGain();
    gainNode.gain.value = 0; // Silent playback

    // Connect the audio processing pipeline
    mediaStream.connect(recorder);
    recorder.connect(gainNode);
    gainNode.connect(this.audioContext.destination);

    this.processor = {
      audioContext: this.audioContext,
      destinationNode: this.destinationNode,
      recorder,
      mediaStream,
      gainNode,
      sessionAudioStartTimeMs: null
    };

    try { await this.audioContext.resume(); } catch {}
    (window as any).logBot("Audio processing pipeline connected and ready.");
    return this.processor;
  }

  setupAudioDataProcessor(onAudioData: (audioData: Float32Array, sessionStartTime: number | null) => void): void {
    if (!this.processor) {
      throw new Error("Audio processor not initialized");
    }

    this.processor.recorder.onaudioprocess = async (event: any) => {
      // Set session start time on first audio chunk
      if (this.processor!.sessionAudioStartTimeMs === null) {
        this.processor!.sessionAudioStartTimeMs = Date.now();
        (window as any).logBot(`[Audio] Session audio start time set: ${this.processor!.sessionAudioStartTimeMs}`);
      }

      const inputData = event.inputBuffer.getChannelData(0);
      const resampledData = this.resampleAudioData(inputData, this.processor!.audioContext.sampleRate);
      
      onAudioData(resampledData, this.processor!.sessionAudioStartTimeMs);
    };
  }

  private resampleAudioData(inputData: Float32Array, sourceSampleRate: number): Float32Array {
    const targetLength = Math.round(
      inputData.length * (this.config.targetSampleRate / sourceSampleRate)
    );
    const resampledData = new Float32Array(targetLength);
    const springFactor = (inputData.length - 1) / (targetLength - 1);
    
    resampledData[0] = inputData[0];
    resampledData[targetLength - 1] = inputData[inputData.length - 1];
    
    for (let i = 1; i < targetLength - 1; i++) {
      const index = i * springFactor;
      const leftIndex = Math.floor(index);
      const rightIndex = Math.ceil(index);
      const fraction = index - leftIndex;
      resampledData[i] =
        inputData[leftIndex] +
        (inputData[rightIndex] - inputData[leftIndex]) * fraction;
    }
    
    return resampledData;
  }

  getSessionAudioStartTime(): number | null {
    return this.processor?.sessionAudioStartTimeMs || null;
  }

  resetSessionStartTime(): void {
    if (this.processor) {
      const oldTime = this.processor.sessionAudioStartTimeMs;
      this.processor.sessionAudioStartTimeMs = null;
      (window as any).logBot(`[Audio] Reset session audio start time: ${oldTime} -> null (will be set on next audio chunk)`);
    }
  }

  disconnect(): void {
    if (this.processor) {
      try {
        this.processor.recorder.disconnect();
        this.processor.mediaStream.disconnect();
        this.processor.gainNode.disconnect();
        this.processor.audioContext.close();
        (window as any).logBot("Audio processing pipeline disconnected.");
      } catch (error: any) {
        (window as any).logBot(`Error disconnecting audio pipeline: ${error.message}`);
      }
      this.processor = null;
    }
  }
}

/**
 * Browser-compatible WhisperLiveService for browser context
 * Supports both simple and stubborn reconnection modes
 */
export class BrowserWhisperLiveService {
  private whisperLiveUrl: string;
  private socket: WebSocket | null = null;
  private isServerReady: boolean = false;
  private botConfigData: any;
  private currentUid: string | null = null;
  private onMessageCallback: ((data: any) => void) | null = null;
  private onErrorCallback: ((error: Event) => void) | null = null;
  private onCloseCallback: ((event: CloseEvent) => void) | null = null;
  private reconnectInterval: any = null;
  private retryCount: number = 0;
  private maxRetries: number = Number.MAX_SAFE_INTEGER; // TRULY NEVER GIVE UP!
  private retryDelayMs: number = 2000;
  private stubbornMode: boolean = false;
  private isManualReconnect: boolean = false; // Flag to prevent auto-reconnect during manual reconfigure

  constructor(config: any, stubbornMode: boolean = false) {
    this.whisperLiveUrl = config.whisperLiveUrl;
    this.stubbornMode = stubbornMode;
  }

  async connectToWhisperLive(
    botConfigData: any,
    onMessage: (data: any) => void,
    onError: (error: Event) => void,
    onClose: (event: CloseEvent) => void
  ): Promise<WebSocket | null> {
    // Store callbacks for reconnection
    this.botConfigData = botConfigData;
    this.onMessageCallback = onMessage;
    this.onErrorCallback = onError;
    this.onCloseCallback = onClose;

    if (this.stubbornMode) {
      return this.attemptConnection();
    } else {
      return this.simpleConnection();
    }
  }

  private async simpleConnection(): Promise<WebSocket | null> {
    try {
      this.socket = new WebSocket(this.whisperLiveUrl);
      
      this.socket.onopen = () => {
        this.currentUid = generateBrowserUUID();
        (window as any).logBot(`[Failover] WebSocket connection opened successfully to ${this.whisperLiveUrl}. New UID: ${this.currentUid}. Lang: ${this.botConfigData.language}, Task: ${this.botConfigData.task}`);
        
        const configPayload = {
          uid: this.currentUid,
          language: this.botConfigData.language || null,
          task: this.botConfigData.task || "transcribe",
          model: null,
          use_vad: false,
          platform: this.botConfigData.platform,
          token: this.botConfigData.token,  // MeetingToken (HS256 JWT)
          meeting_id: this.botConfigData.meeting_id,
          meeting_url: this.botConfigData.meetingUrl || null,
        };

        (window as any).logBot(`Sending initial config message: ${JSON.stringify(configPayload)}`);
        this.socket!.send(JSON.stringify(configPayload));
      };

      this.socket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (this.onMessageCallback) {
          this.onMessageCallback(data);
        }
      };

      this.socket.onerror = this.onErrorCallback;
      this.socket.onclose = this.onCloseCallback;

      return this.socket;
    } catch (error: any) {
      (window as any).logBot(`[WhisperLive] Connection error: ${error.message}`);
      return null;
    }
  }

  private async attemptConnection(): Promise<WebSocket | null> {
    try {
      (window as any).logBot(`[STUBBORN] 🚀 Connecting to WhisperLive with NEVER-GIVE-UP reconnection: ${this.whisperLiveUrl} (attempt ${this.retryCount + 1})`);
      
      this.socket = new WebSocket(this.whisperLiveUrl);
      
      this.socket.onopen = (event) => {
        (window as any).logBot(`[STUBBORN] ✅ WebSocket CONNECTED to ${this.whisperLiveUrl}! Retry count reset from ${this.retryCount}.`);
        this.retryCount = 0; // Reset on successful connection
        this.clearReconnectInterval(); // Stop any ongoing reconnection attempts
        this.isServerReady = false; // Will be set to true when SERVER_READY received
        
        this.currentUid = generateBrowserUUID();
        
        const configPayload = {
          uid: this.currentUid,
          language: this.botConfigData.language || null,
          task: this.botConfigData.task || "transcribe",
          model: null,
          use_vad: false,
          platform: this.botConfigData.platform,
          token: this.botConfigData.token,  // MeetingToken (HS256 JWT)
          meeting_id: this.botConfigData.meeting_id,
          meeting_url: this.botConfigData.meetingUrl || null,
        };

        (window as any).logBot(`Sending initial config message: ${JSON.stringify(configPayload)}`);
        if (this.socket) {
          this.socket.send(JSON.stringify(configPayload));
        }
      };

      this.socket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (this.onMessageCallback) {
          this.onMessageCallback(data);
        }
      };

      this.socket.onerror = (event) => {
        (window as any).logBot(`[STUBBORN] ❌ WebSocket ERROR. Manual reconnect: ${this.isManualReconnect}`);
        if (this.onErrorCallback) {
          this.onErrorCallback(event);
        }
        // Only start stubborn reconnection if not manual reconnect
        if (!this.isManualReconnect) {
          this.startStubbornReconnection();
        } else {
          (window as any).logBot(`[STUBBORN] Skipping auto-reconnect on error (manual reconfigure in progress)`);
        }
      };

      this.socket.onclose = (event) => {
        (window as any).logBot(`[STUBBORN] ❌ WebSocket CLOSED. Code: ${event.code}, Reason: "${event.reason}". Manual reconnect: ${this.isManualReconnect}`);
        this.isServerReady = false;
        this.socket = null;
        if (this.onCloseCallback) {
          this.onCloseCallback(event);
        }
        // Only start stubborn reconnection if not manual reconnect
        if (!this.isManualReconnect) {
          this.startStubbornReconnection();
        } else {
          (window as any).logBot(`[STUBBORN] Skipping auto-reconnect (manual reconfigure in progress)`);
          this.isManualReconnect = false; // Reset flag
        }
      };

      return this.socket;
    } catch (error: any) {
      (window as any).logBot(`[STUBBORN] ❌ Connection creation error: ${error.message}. WILL KEEP TRYING!`);
      this.startStubbornReconnection();
      return null;
    }
  }

  private startStubbornReconnection(): void {
    if (this.reconnectInterval) {
      return; // Already reconnecting
    }

    // Exponential backoff with max delay of 10 seconds
    const delay = Math.min(this.retryDelayMs * Math.pow(1.5, Math.min(this.retryCount, 10)), 10000);
    
    (window as any).logBot(`[STUBBORN] 🔄 Starting STUBBORN reconnection in ${delay}ms (attempt ${this.retryCount + 1}/∞ - WE NEVER GIVE UP!)...`);
    
    this.reconnectInterval = setTimeout(async () => {
      this.reconnectInterval = null;
      this.retryCount++;
      
      if (this.retryCount >= 1000) { // Reset counter every 1000 attempts to prevent overflow
        (window as any).logBot(`[STUBBORN] 🔄 Resetting retry counter after 1000 attempts. WE WILL NEVER GIVE UP! EVER!`);
        this.retryCount = 0; // Reset and keep going - NEVER GIVE UP!
      }
      
      if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
        (window as any).logBot(`[STUBBORN] 🔄 Attempting reconnection (retry ${this.retryCount})...`);
        await this.attemptConnection();
      } else {
        (window as any).logBot(`[STUBBORN] ✅ Connection already restored!`);
      }
    }, delay);
  }

  private clearReconnectInterval(): void {
    if (this.reconnectInterval) {
      clearTimeout(this.reconnectInterval);
      this.reconnectInterval = null;
    }
  }

  sendAudioData(audioData: Float32Array): boolean {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      return false;
    }

    try {
      // Send Float32Array directly as WhisperLive expects (matching google_old.ts approach)
      this.socket.send(audioData);
      return true;
    } catch (error: any) {
      (window as any).logBot(`[WhisperLive] Error sending audio data: ${error.message}`);
      return false;
    }
  }

  sendAudioChunkMetadata(chunkLength: number, sampleRate: number): boolean {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
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
      this.socket.send(JSON.stringify(meta));
      return true;
    } catch (error: any) {
      (window as any).logBot(`[WhisperLive] Error sending audio metadata: ${error.message}`);
      return false;
    }
  }

  sendSpeakerEvent(eventType: string, participantName: string, participantId: string, relativeTimestampMs: number, botConfigData: any): boolean {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      return false;
    }

    const speakerEventMessage = {
      type: "speaker_activity",
      payload: {
        event_type: eventType,
        participant_name: participantName,
        participant_id_meet: participantId,
        relative_client_timestamp_ms: relativeTimestampMs,
        uid: this.currentUid,
        token: botConfigData.token,  // MeetingToken (HS256 JWT)
        platform: botConfigData.platform,
        meeting_id: botConfigData.meeting_id,
        meeting_url: botConfigData.meetingUrl
      }
    };

    try {
      this.socket.send(JSON.stringify(speakerEventMessage));
      return true;
    } catch (error: any) {
      (window as any).logBot(`[WhisperLive] Error sending speaker event: ${error.message}`);
      return false;
    }
  }

  getCurrentUid(): string | null {
    return this.currentUid;
  }

  sendSessionControl(event: string, botConfigData: any): boolean {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      return false;
    }

    const sessionControlMessage = {
      type: "session_control",
      payload: {
        event: event,
        uid: generateBrowserUUID(),
        client_timestamp_ms: Date.now(),
        token: botConfigData.token,  // MeetingToken (HS256 JWT)
        platform: botConfigData.platform,
        meeting_id: botConfigData.meeting_id
      }
    };

    try {
      this.socket.send(JSON.stringify(sessionControlMessage));
      return true;
    } catch (error: any) {
      (window as any).logBot(`[WhisperLive] Error sending session control: ${error.message}`);
      return false;
    }
  }

  isReady(): boolean {
    return this.isServerReady;
  }

  setServerReady(ready: boolean): void {
    this.isServerReady = ready;
  }

  isOpen(): boolean {
    return this.socket?.readyState === WebSocket.OPEN;
  }

  close(): void {
    (window as any).logBot(`[STUBBORN] 🛑 Closing WebSocket and stopping reconnection...`);
    this.clearReconnectInterval();
    // Clear currentUid to ensure a new session is created on next connection
    const oldUid = this.currentUid;
    this.currentUid = null;
    (window as any).logBot(`[STUBBORN] Cleared session UID: ${oldUid} -> null`);
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
  }

  // Method to close and prepare for manual reconnect (prevents auto-reconnect)
  closeForReconfigure(): void {
    this.isManualReconnect = true;
    (window as any).logBot(`[STUBBORN] 🛑 Closing for manual reconfigure (will not auto-reconnect)...`);
    this.close();
  }
}
