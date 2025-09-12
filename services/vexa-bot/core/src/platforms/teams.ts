import { Page } from "playwright";
import { log, randomDelay } from "../utils";
import { BotConfig } from "../types";
import { generateUUID, createSessionControlMessage, createSpeakerActivityMessage } from "../index";
import { WhisperLiveService } from "../services/whisperlive";
import { AudioService } from "../services/audio";
import { WebSocketManager } from "../utils/websocket";

// --- ADDED: Function to call startup callback ---
async function callStartupCallback(botConfig: BotConfig): Promise<void> {
  if (!botConfig.botManagerCallbackUrl) {
    log("Warning: No bot manager callback URL configured. Cannot send startup callback.");
    return;
  }

  if (!botConfig.container_name) {
    log("Warning: No container name configured. Cannot send startup callback.");
    return;
  }

  try {
    // Extract the base URL and modify it for the startup callback
    const baseUrl = botConfig.botManagerCallbackUrl.replace('/exited', '/started');
    const startupUrl = baseUrl;
    
    const payload = {
      connection_id: botConfig.connectionId,
      container_id: botConfig.container_name
    };

    log(`Sending startup callback to ${startupUrl} with payload: ${JSON.stringify(payload)}`);
    
    // Use fetch API (available in Node.js 18+)
    const response = await fetch(startupUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload)
    });

    if (response.ok) {
      const result = await response.json();
      log(`Startup callback successful: ${JSON.stringify(result)}`);
    } else {
      log(`Startup callback failed with status ${response.status}: ${response.statusText}`);
    }
  } catch (error: any) {
    log(`Error sending startup callback: ${error.message}`);
  }
}

// --- Teams-Specific Functions ---

// New function to wait for Teams meeting admission
const waitForTeamsMeetingAdmission = async (
  page: Page,
  timeout: number
): Promise<boolean> => {
  try {
    log("Waiting for Teams meeting admission...");
    
    // Take screenshot at start of admission check
    await page.screenshot({ path: '/app/screenshots/teams-admission-start.png', fullPage: true });
    log("📸 Screenshot taken: Start of Teams admission check");
    
    // FIRST: Check if bot is already admitted (no waiting room needed)
    log("Checking if bot is already admitted to the Teams meeting...");
    const initialAdmissionIndicators = [
      'button[aria-label*="People"]',
      'button[aria-label*="people"]',
      'button[aria-label*="Chat"]', 
      'button[aria-label*="chat"]',
      'button[aria-label*="Leave"]',
      'button[aria-label*="leave"]',
      'button[aria-label*="End meeting"]',
      'button[aria-label*="end meeting"]',
      '[role="toolbar"]',
      'button[aria-label*="Turn off microphone"]',
      'button[aria-label*="Turn on microphone"]',
      'button[aria-label*="Mute"]',
      'button[aria-label*="mute"]',
      'button[aria-label*="Camera"]',
      'button[aria-label*="camera"]'
    ];
    
    for (const indicator of initialAdmissionIndicators) {
      try {
        await page.waitForSelector(indicator, { timeout: 2000 });
        log(`Found Teams admission indicator: ${indicator} - Bot is already admitted to the meeting!`);
        
        // Take screenshot when already admitted
        await page.screenshot({ path: '/app/screenshots/teams-admitted.png', fullPage: true });
        log("📸 Screenshot taken: Bot confirmed already admitted to Teams meeting");
        
        log("Successfully admitted to the Teams meeting - no waiting room required");
        return true;
      } catch {
        continue;
      }
    }
    
    log("Bot not yet admitted - checking for Teams waiting room indicators...");
    
    // Second, check if we're still in waiting room
    const waitingRoomIndicators = [
      'text="You\'re in the lobby"',
      'text="Waiting for someone to let you in"',
      'text="Please wait until someone admits you"',
      'text="Wait for someone to admit you"',
      'text="Waiting to be admitted"',
      '[aria-label*="waiting"]',
      '[aria-label*="lobby"]',
      'text="Your request to join has been sent"',
      'text="Meeting not found"'
    ];
    
    // Check for waiting room indicators first, but don't exit immediately
    let stillInWaitingRoom = false;
    for (const waitingIndicator of waitingRoomIndicators) {
      try {
        await page.waitForSelector(waitingIndicator, { timeout: 2000 });
        log(`Found Teams waiting room indicator: ${waitingIndicator} - Bot is still in waiting room`);
        
        // Take screenshot when waiting room indicator found
        await page.screenshot({ path: '/app/screenshots/teams-waiting-room.png', fullPage: true });
        log("📸 Screenshot taken: Bot confirmed in Teams waiting room");
        
        stillInWaitingRoom = true;
        break;
      } catch {
        // Continue to next indicator if this one wasn't found
        continue;
      }
    }
    
    // If we're in waiting room, wait for the full timeout period for admission
    if (stillInWaitingRoom) {
      log(`Bot is in Teams waiting room. Waiting for ${timeout}ms for admission...`);
      
      // Wait for the full timeout period, checking periodically for admission
      const checkInterval = 5000; // Check every 5 seconds
      const startTime = Date.now();
      let screenshotCounter = 0;
      
      while (Date.now() - startTime < timeout) {
        // Take periodic screenshot for debugging
        screenshotCounter++;
        await page.screenshot({ path: `/app/screenshots/teams-waiting-periodic-${screenshotCounter}.png`, fullPage: true });
        log(`📸 Periodic screenshot ${screenshotCounter} taken during Teams waiting period`);
        
        // Check if we're still in waiting room
        let stillWaiting = false;
        for (const waitingIndicator of waitingRoomIndicators) {
          try {
            await page.waitForSelector(waitingIndicator, { timeout: 1000 });
            stillWaiting = true;
            break;
          } catch {
            continue;
          }
        }
        
        if (!stillWaiting) {
          log("Teams waiting room indicator disappeared - bot is likely admitted, checking for admission indicators...");
          
          // Immediately check for admission indicators since waiting room disappeared
          let quickAdmissionCheck = false;
          for (const indicator of initialAdmissionIndicators) {
            try {
              await page.waitForSelector(indicator, { timeout: 1000 });
              log(`Found Teams admission indicator: ${indicator} - Bot is confirmed admitted!`);
              quickAdmissionCheck = true;
              break;
            } catch {
              continue;
            }
          }
          
          if (quickAdmissionCheck) {
            log("Successfully admitted to the Teams meeting - waiting room disappeared and admission indicators found");
            return true;
          } else {
            log("Teams waiting room disappeared but no admission indicators found yet - continuing to wait...");
            // Continue waiting for admission indicators to appear
          }
        }
        
        // Wait before next check
        await page.waitForTimeout(checkInterval);
        log(`Still in Teams waiting room... ${Math.round((Date.now() - startTime) / 1000)}s elapsed`);
      }
      
      // After waiting, check if we're still in waiting room
      let finalWaitingCheck = false;
      for (const waitingIndicator of waitingRoomIndicators) {
        try {
          await page.waitForSelector(waitingIndicator, { timeout: 2000 });
          finalWaitingCheck = true;
          break;
        } catch {
          continue;
        }
      }
      
      if (finalWaitingCheck) {
        throw new Error("Bot is still in the Teams waiting room after timeout - not admitted to the meeting");
      }
    }
    
    // PRIORITY: Check for Teams meeting controls/toolbar (most reliable indicator)
    log("Checking for Teams meeting controls as primary admission indicator...");
    
    // Take initial screenshot before checking for admission indicators
    await page.screenshot({ path: '/app/screenshots/teams-checking-admission-start.png', fullPage: true });
    log("📸 Screenshot taken: Starting Teams admission indicator check");
    
    // Wait for Teams meeting indicators - these are the most reliable signs of admission
    // ORDERED BY LIKELIHOOD: Most common indicators first for faster detection
    const teamsAdmissionIndicators = [
      // Most common Teams meeting indicators (check these first!)
      'button[aria-label*="Chat"]',
      'button[aria-label*="chat"]', 
      'button[aria-label*="People"]',
      'button[aria-label*="people"]',
      'button[aria-label*="Participants"]',
      'button[aria-label*="Leave"]',
      'button[aria-label*="leave"]',
      // Audio/video controls that appear when in Teams meeting
      'button[aria-label*="Turn off microphone"]',
      'button[aria-label*="Turn on microphone"]',
      'button[aria-label*="Mute"]',
      'button[aria-label*="mute"]',
      'button[aria-label*="Turn off camera"]',
      'button[aria-label*="Turn on camera"]',
      'button[aria-label*="Camera"]',
      'button[aria-label*="camera"]',
      // Share and present buttons
      'button[aria-label*="Share"]',
      'button[aria-label*="share"]',
      'button[aria-label*="Present"]',
      'button[aria-label*="present"]',
      // Meeting toolbar and controls
      '[role="toolbar"]',
      // Teams specific meeting UI
      '[data-tid*="meeting"]',
      '[data-tid*="call"]'
    ];
    
    let admitted = false;
    let admissionCheckCounter = 0;
    
    // Use much faster timeout for each check (500ms instead of timeout/indicators.length)
    const fastTimeout = 500;
    
    for (const indicator of teamsAdmissionIndicators) {
      try {
        admissionCheckCounter++;
        log(`Checking Teams admission indicator ${admissionCheckCounter}/${teamsAdmissionIndicators.length}: ${indicator}`);
        
        // Take screenshot before checking each indicator
        await page.screenshot({ path: `/app/screenshots/teams-admission-check-${admissionCheckCounter}.png`, fullPage: true });
        log(`📸 Screenshot taken: Checking Teams admission indicator ${admissionCheckCounter}`);
        
        await page.waitForSelector(indicator, { timeout: fastTimeout });
        log(`Found Teams admission indicator: ${indicator} - Bot is admitted to the meeting`);
        
        // Take screenshot when admission indicator is found
        await page.screenshot({ path: '/app/screenshots/teams-admitted-confirmed.png', fullPage: true });
        log("📸 Screenshot taken: Bot confirmed admitted to Teams meeting via admission indicators");
        
        admitted = true;
        break;
      } catch {
        // Continue to next indicator
        continue;
      }
    }
    
    if (!admitted) {
      // Take screenshot when no meeting indicators found
      await page.screenshot({ path: '/app/screenshots/teams-no-indicators.png', fullPage: true });
      log("📸 Screenshot taken: No Teams meeting indicators found");
      
      // If we can't find any meeting indicators, the bot likely failed to join
      log("No Teams meeting indicators found - bot likely failed to join or is in unknown state");
      throw new Error("Bot failed to join the Teams meeting - no meeting indicators found");
    }
    
    if (admitted) {
      log("Successfully admitted to the Teams meeting");
      return true;
    } else {
      throw new Error("Could not determine Teams admission status");
    }
    
  } catch (error: any) {
    throw new Error(
      `Bot was not admitted into the Teams meeting within the timeout period: ${error.message}`
    );
  }
};

// Modified to use new services - Teams recording functionality
const startTeamsRecording = async (page: Page, botConfig: BotConfig) => {
  // Initialize WhisperLive service on Node.js side
  const whisperLiveService = new WhisperLiveService({
    redisUrl: botConfig.redisUrl,
    maxClients: parseInt(process.env.WL_MAX_CLIENTS || '10', 10),
    whisperLiveUrl: process.env.WHISPER_LIVE_URL
  });

  // Initialize WhisperLive connection
  const whisperLiveUrl = await whisperLiveService.initialize();
  if (!whisperLiveUrl) {
    log("ERROR: Could not initialize WhisperLive service for Teams. Aborting recording.");
    return;
  }

  log(`[Node.js] Using WhisperLive URL for Teams: ${whisperLiveUrl}`);
  log("Starting Teams recording with WebSocket connection");

  // Pass the necessary config fields and the resolved URL into the page context
  await page.evaluate(
    async (pageArgs: {
      botConfigData: BotConfig;
      whisperUrlForBrowser: string;
    }) => {
      const { botConfigData, whisperUrlForBrowser } = pageArgs;

      // Create browser-compatible AudioService implementation (same as Google Meet)
      class BrowserAudioService {
        private config: any;
        private processor: any = null;

        constructor(config: any) {
          this.config = config;
        }

        async findMediaElements(retries: number = 5, delay: number = 2000): Promise<HTMLMediaElement[]> {
          for (let i = 0; i < retries; i++) {
            const mediaElements = Array.from(
              document.querySelectorAll("audio, video")
            ).filter((el: any) => 
              !el.paused && 
              el.srcObject instanceof MediaStream && 
              el.srcObject.getAudioTracks().length > 0
            ) as HTMLMediaElement[];

            if (mediaElements.length > 0) {
              (window as any).logBot(`Found ${mediaElements.length} active Teams media elements with audio tracks after ${i + 1} attempt(s).`);
              return mediaElements;
            }
            (window as any).logBot(`[Teams Audio] No active media elements found. Retrying in ${delay}ms... (Attempt ${i + 2}/${retries})`);
            await new Promise(resolve => setTimeout(resolve, delay));
          }
          return [];
        }

        async createCombinedAudioStream(mediaElements: HTMLMediaElement[]): Promise<MediaStream> {
          if (mediaElements.length === 0) {
            throw new Error("No Teams media elements provided for audio stream creation");
          }

          (window as any).logBot(`Found ${mediaElements.length} active Teams media elements.`);
          const audioContext = new AudioContext();
          const destinationNode = audioContext.createMediaStreamDestination();
          let sourcesConnected = 0;

          // Connect all media elements to the destination node
          mediaElements.forEach((element: any, index: number) => {
            try {
              const elementStream =
                element.srcObject ||
                (element.captureStream && element.captureStream()) ||
                (element.mozCaptureStream && element.mozCaptureStream());

              if (
                elementStream instanceof MediaStream &&
                elementStream.getAudioTracks().length > 0
              ) {
                const sourceNode = audioContext.createMediaStreamSource(elementStream);
                sourceNode.connect(destinationNode);
                sourcesConnected++;
                (window as any).logBot(`Connected Teams audio stream from element ${index + 1}/${mediaElements.length}.`);
              }
            } catch (error: any) {
              (window as any).logBot(`Could not connect Teams element ${index + 1}: ${error.message}`);
            }
          });

          if (sourcesConnected === 0) {
            throw new Error("Could not connect any Teams audio streams. Check media permissions.");
          }

          (window as any).logBot(`Successfully combined ${sourcesConnected} Teams audio streams.`);
          return destinationNode.stream;
        }

        async initializeAudioProcessor(combinedStream: MediaStream): Promise<any> {
          const audioContext = new AudioContext();
          const destinationNode = audioContext.createMediaStreamDestination();
          const mediaStream = audioContext.createMediaStreamSource(combinedStream);
          const recorder = audioContext.createScriptProcessor(
            this.config.bufferSize,
            this.config.inputChannels,
            this.config.outputChannels
          );
          const gainNode = audioContext.createGain();
          gainNode.gain.value = 0; // Silent playback

          // Connect the audio processing pipeline
          mediaStream.connect(recorder);
          recorder.connect(gainNode);
          gainNode.connect(audioContext.destination);

          this.processor = {
            audioContext,
            destinationNode,
            recorder,
            mediaStream,
            gainNode,
            sessionAudioStartTimeMs: null
          };

          (window as any).logBot("Teams audio processing pipeline connected and ready.");
          return this.processor;
        }

        setupAudioDataProcessor(onAudioData: (audioData: Float32Array, sessionStartTime: number | null) => void): void {
          if (!this.processor) {
            throw new Error("Teams audio processor not initialized");
          }

          this.processor.recorder.onaudioprocess = async (event: any) => {
            // Set session start time on first audio chunk
            if (this.processor!.sessionAudioStartTimeMs === null) {
              this.processor!.sessionAudioStartTimeMs = Date.now();
              (window as any).logBot(`[Teams Audio] Session audio start time set: ${this.processor!.sessionAudioStartTimeMs}`);
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

        disconnect(): void {
          if (this.processor) {
            try {
              this.processor.recorder.disconnect();
              this.processor.mediaStream.disconnect();
              this.processor.gainNode.disconnect();
              this.processor.audioContext.close();
              (window as any).logBot("Teams audio processing pipeline disconnected.");
            } catch (error: any) {
              (window as any).logBot(`Error disconnecting Teams audio pipeline: ${error.message}`);
            }
            this.processor = null;
          }
        }
      }

      // Create browser-compatible WhisperLiveService implementation (same as Google Meet)
      class BrowserWhisperLiveService {
        private whisperLiveUrl: string;
        private socket: WebSocket | null = null;
        private isServerReady: boolean = false;

        constructor(config: any) {
          this.whisperLiveUrl = config.whisperLiveUrl;
        }

        async connectToWhisperLive(
          botConfigData: any,
          onMessage: (data: any) => void,
          onError: (error: Event) => void,
          onClose: (event: CloseEvent) => void
        ): Promise<WebSocket | null> {
          try {
            this.socket = new WebSocket(this.whisperLiveUrl);
            
            this.socket.onopen = () => {
              (window as any).logBot(`[Teams] WebSocket connection opened successfully to ${this.whisperLiveUrl}. New UID: ${generateUUID()}. Lang: ${botConfigData.language}, Task: ${botConfigData.task}`);
              
              const configPayload = {
                uid: generateUUID(),
                language: botConfigData.language || null,
                task: botConfigData.task || "transcribe",
                model: null,
                use_vad: true,
                platform: botConfigData.platform,
                token: botConfigData.token,
                meeting_id: botConfigData.nativeMeetingId,
                meeting_url: botConfigData.meetingUrl || null,
              };

              (window as any).logBot(`Teams sending initial config message: ${JSON.stringify(configPayload)}`);
              this.socket!.send(JSON.stringify(configPayload));
            };

            this.socket.onmessage = (event) => {
              const data = JSON.parse(event.data);
              onMessage(data);
            };

            this.socket.onerror = onError;
            this.socket.onclose = onClose;

            return this.socket;
          } catch (error: any) {
            (window as any).logBot(`[Teams WhisperLive] Connection error: ${error.message}`);
            return null;
          }
        }

        sendAudioData(audioData: Float32Array): boolean {
          if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
            return false;
          }

          try {
            this.socket.send(audioData);
            return true;
          } catch (error: any) {
            (window as any).logBot(`[Teams WhisperLive] Error sending audio data: ${error.message}`);
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
              uid: generateUUID(),
              token: botConfigData.token,
              platform: botConfigData.platform,
              meeting_id: botConfigData.nativeMeetingId,
              meeting_url: botConfigData.meetingUrl
            }
          };

          try {
            this.socket.send(JSON.stringify(speakerEventMessage));
            return true;
          } catch (error: any) {
            (window as any).logBot(`[Teams WhisperLive] Error sending speaker event: ${error.message}`);
            return false;
          }
        }

        sendSessionControl(event: string, botConfigData: any): boolean {
          if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
            return false;
          }

          const sessionControlMessage = {
            type: "session_control",
            payload: {
              event: event,
              uid: generateUUID(),
              client_timestamp_ms: Date.now(),
              token: botConfigData.token,
              platform: botConfigData.platform,
              meeting_id: botConfigData.nativeMeetingId
            }
          };

          try {
            this.socket.send(JSON.stringify(sessionControlMessage));
            return true;
          } catch (error: any) {
            (window as any).logBot(`[Teams WhisperLive] Error sending session control: ${error.message}`);
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
          if (this.socket) {
            this.socket.close();
            this.socket = null;
          }
        }
      }

      // Helper function for UUID generation
      function generateUUID(): string {
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

      // Initialize services in browser context
      const audioService = new BrowserAudioService({
        targetSampleRate: 16000,
        bufferSize: 4096,
        inputChannels: 1,
        outputChannels: 1
      });

      const whisperLiveService = new BrowserWhisperLiveService({
        whisperLiveUrl: whisperUrlForBrowser
      });

      await new Promise<void>((resolve, reject) => {
        try {
          (window as any).logBot("Starting Teams recording process with new services.");
          
          // Find and create combined audio stream
          audioService.findMediaElements().then(async (mediaElements: HTMLMediaElement[]) => {
            if (mediaElements.length === 0) {
              reject(
                new Error(
                  "[Teams BOT Error] No active media elements found after multiple retries. Ensure the Teams meeting media is playing."
                )
              );
              return;
            }

            // Create combined audio stream
            return await audioService.createCombinedAudioStream(mediaElements);
          }).then(async (combinedStream: MediaStream | undefined) => {
            if (!combinedStream) {
              reject(new Error("[Teams BOT Error] Failed to create combined audio stream"));
              return;
            }
            // Initialize audio processor
            return await audioService.initializeAudioProcessor(combinedStream);
          }).then(async (processor: any) => {
            // Setup audio data processing
            audioService.setupAudioDataProcessor(async (audioData: Float32Array, sessionStartTime: number | null) => {
              // Send audio data to WhisperLive
              const success = whisperLiveService.sendAudioData(audioData);
              if (!success) {
                (window as any).logBot("Failed to send Teams audio data to WhisperLive");
              }
            });

            // Initialize WhisperLive WebSocket connection
            return await whisperLiveService.connectToWhisperLive(
              botConfigData,
              (data: any) => {
                (window as any).logBot("Teams received message: " + JSON.stringify(data));
                if (data["status"] === "ERROR") {
                  (window as any).logBot(`Teams WebSocket Server Error: ${data["message"]}`);
                } else if (data["status"] === "WAIT") {
                  (window as any).logBot(`Teams Server busy: ${data["message"]}`);
                } else if (!whisperLiveService.isReady() && data["status"] === "SERVER_READY") {
                  whisperLiveService.setServerReady(true);
                  (window as any).logBot("Teams Server is ready.");
                } else if (data["language"]) {
                  (window as any).logBot(`Teams Language detected: ${data["language"]}`);
                } else if (data["message"] === "DISCONNECT") {
                  (window as any).logBot("Teams Server requested disconnect.");
                  whisperLiveService.close();
                } else {
                  (window as any).logBot(`Teams Transcription: ${JSON.stringify(data)}`);
                }
              },
              (event: Event) => {
                (window as any).logBot(`[Teams Failover] WebSocket error. This will trigger retry logic.`);
              },
              async (event: CloseEvent) => {
                (window as any).logBot(`[Teams Failover] WebSocket connection closed. Code: ${event.code}, Reason: ${event.reason}.`);
                // Retry logic would be handled by WebSocketManager
              }
            );
          }).then(() => {
            // Initialize Teams-specific speaker detection (browser context)
            (window as any).logBot("Initializing Teams speaker detection...");
            
            // Teams-specific speaker detection logic (simplified for now)
            const initializeTeamsSpeakerDetection = (whisperLiveService: any, audioService: any, botConfigData: any) => {
              (window as any).logBot("Setting up Teams speaker detection...");
              
              // Monitor for participant changes in Teams
              const participantObserver = new MutationObserver((mutations) => {
                mutations.forEach((mutation) => {
                  if (mutation.type === 'childList') {
                    // Check for new participants or speaker changes in Teams
                    const speakerElements = document.querySelectorAll('[data-tid*="participant"], [aria-label*="participant"]');
                    speakerElements.forEach((element: any, index: number) => {
                      const participantId = element.getAttribute('data-tid') || `teams-participant-${index}`;
                      const participantName = element.textContent || element.getAttribute('aria-label') || 'Unknown Teams User';
                      
                      if (participantId && participantId !== botConfigData.nativeMeetingId) {
                        // Send speaker activity event
                        const sessionStartTime = audioService.getSessionAudioStartTime();
                        const relativeTimestamp = sessionStartTime ? Date.now() - sessionStartTime : 0;
                        
                        whisperLiveService.sendSpeakerEvent(
                          'speaker_active',
                          participantName,
                          participantId,
                          relativeTimestamp,
                          botConfigData
                        );
                      }
                    });
                  }
                });
              });

              // Start observing the Teams meeting container
              const meetingContainer = document.querySelector('[role="main"]') || document.body;
              participantObserver.observe(meetingContainer, {
                childList: true,
                subtree: true
              });

              (window as any).logBot("Teams speaker detection initialized");
            };

            // Setup Teams meeting monitoring (browser context)
            const setupTeamsMeetingMonitoring = (botConfigData: any, audioService: any, whisperLiveService: any, resolve: any) => {
              (window as any).logBot("Setting up Teams meeting monitoring...");
              
              const startupAloneTimeoutSeconds = 20 * 60; // 20 minutes during startup
              const everyoneLeftTimeoutSeconds = 10; // 10 seconds after speakers identified
              
              let aloneTime = 0;
              let lastParticipantCount = 0;
              let speakersIdentified = false;
              let hasEverHadMultipleParticipants = false;

              const checkInterval = setInterval(() => {
                // Check participant count in Teams
                const participantElements = document.querySelectorAll('[data-tid*="participant"], [aria-label*="participant"]');
                const currentParticipantCount = participantElements.length;
                
                if (currentParticipantCount !== lastParticipantCount) {
                  (window as any).logBot(`Teams Participant check: Found ${currentParticipantCount} unique participants.`);
                  lastParticipantCount = currentParticipantCount;
                  
                  // Track if we've ever had multiple participants
                  if (currentParticipantCount > 1) {
                    hasEverHadMultipleParticipants = true;
                    speakersIdentified = true; // Once we see multiple participants, we've identified speakers
                    (window as any).logBot("Teams Speakers identified - switching to post-speaker monitoring mode");
                  }
                }

                if (currentParticipantCount <= 1) {
                  aloneTime++;
                  
                  // Determine timeout based on whether speakers have been identified
                  const currentTimeout = speakersIdentified ? everyoneLeftTimeoutSeconds : startupAloneTimeoutSeconds;
                  const timeoutDescription = speakersIdentified ? "post-speaker" : "startup";
                  
                  if (aloneTime >= currentTimeout) {
                    if (speakersIdentified) {
                      (window as any).logBot(`Teams meeting ended or bot has been alone for ${everyoneLeftTimeoutSeconds} seconds after speakers were identified. Stopping recorder...`);
                    } else {
                      (window as any).logBot(`Teams bot has been alone for ${startupAloneTimeoutSeconds/60} minutes during startup with no other participants. Stopping recorder...`);
                    }
                    clearInterval(checkInterval);
                    audioService.disconnect();
                    whisperLiveService.close();
                    resolve();
                  } else if (aloneTime > 0 && aloneTime % 10 === 0) { // Log every 10 seconds to avoid spam
                    if (speakersIdentified) {
                      (window as any).logBot(`Teams bot has been alone for ${aloneTime} seconds (${timeoutDescription} mode). Will leave in ${currentTimeout - aloneTime} more seconds.`);
                    } else {
                      const remainingMinutes = Math.floor((currentTimeout - aloneTime) / 60);
                      const remainingSeconds = (currentTimeout - aloneTime) % 60;
                      (window as any).logBot(`Teams bot has been alone for ${aloneTime} seconds during startup. Will leave in ${remainingMinutes}m ${remainingSeconds}s.`);
                    }
                  }
                } else {
                  aloneTime = 0; // Reset if others are present
                  if (hasEverHadMultipleParticipants && !speakersIdentified) {
                    speakersIdentified = true;
                    (window as any).logBot("Teams speakers identified - switching to post-speaker monitoring mode");
                  }
                }
              }, 1000);

              // Listen for page unload
              window.addEventListener("beforeunload", () => {
                (window as any).logBot("Teams page is unloading. Stopping recorder...");
                clearInterval(checkInterval);
                audioService.disconnect();
                whisperLiveService.close();
                resolve();
              });

              document.addEventListener("visibilitychange", () => {
                if (document.visibilityState === "hidden") {
                  (window as any).logBot("Teams document is hidden. Stopping recorder...");
                  clearInterval(checkInterval);
                  audioService.disconnect();
                  whisperLiveService.close();
                  resolve();
                }
              });
            };

            // Initialize Teams-specific speaker detection
            initializeTeamsSpeakerDetection(whisperLiveService, audioService, botConfigData);
            
            // Setup Teams meeting monitoring
            setupTeamsMeetingMonitoring(botConfigData, audioService, whisperLiveService, resolve);
          }).catch((err: any) => {
            reject(err);
          });

        } catch (error: any) {
          return reject(new Error("[Teams BOT Error] " + error.message));
        }
      });
    },
    { botConfigData: botConfig, whisperUrlForBrowser: whisperLiveUrl }
  );
  
  // After page.evaluate finishes, cleanup services
  await whisperLiveService.cleanup();
};

// Prepare for recording by exposing necessary functions
const prepareForRecording = async (page: Page): Promise<void> => {
  // Expose the logBot function to the browser context
  await page.exposeFunction("logBot", (msg: string) => {
    log(msg);
  });

  // Ensure leave function is available even before admission
  await page.evaluate(() => {
    if (typeof (window as any).performLeaveAction !== "function") {
      (window as any).performLeaveAction = async () => {
        try {
          // Teams-specific leave button selectors
          const primaryLeaveButtonSelectors = [
            'button[aria-label*="Leave"]',
            'button[aria-label*="leave"]', 
            'button[aria-label*="End meeting"]',
            'button[aria-label*="end meeting"]',
            'button[aria-label*="Hang up"]',
            'button[aria-label*="hang up"]'
          ];

          const secondaryLeaveButtonSelectors = [
            'button:has-text("Leave meeting")',
            'button:has-text("Leave")',
            'button:has-text("End meeting")',
            'button:has-text("Hang up")'
          ];

          // Try primary leave buttons
          for (const selector of primaryLeaveButtonSelectors) {
            try {
              const leaveButton = document.querySelector(selector) as HTMLElement;
              if (leaveButton) {
                (window as any).logBot?.(`Clicking Teams primary leave button: ${selector}`);
                leaveButton.click();
                await new Promise((resolve) => setTimeout(resolve, 1000));
                
                // Try secondary/confirmation buttons
                for (const secondarySelector of secondaryLeaveButtonSelectors) {
                  try {
                    const confirmButton = document.querySelector(secondarySelector) as HTMLElement;
                    if (confirmButton) {
                      (window as any).logBot?.(`Clicking Teams confirmation leave button: ${secondarySelector}`);
                      confirmButton.click();
                      await new Promise((resolve) => setTimeout(resolve, 500));
                      break;
                    }
                  } catch (e) {
                    continue;
                  }
                }
                
                (window as any).logBot?.("Teams leave sequence completed.");
                return true;
              }
            } catch (e) {
              continue;
            }
          }
          
          (window as any).logBot?.("Teams primary leave button not found.");
          return false;
        } catch (err: any) {
          (window as any).logBot?.(`Error during Teams leave attempt: ${err.message}`);
          return false;
        }
      };
    }
  });
};

export async function handleMicrosoftTeams(
  botConfig: BotConfig,
  page: Page,
  gracefulLeaveFunction: (page: Page | null, exitCode: number, reason: string, errorDetails?: any) => Promise<void>
): Promise<void> {
  log("Starting Microsoft Teams bot - Using simple approach with MS Edge");
  
  if (!botConfig.meetingUrl) {
    log("Error: Meeting URL is required for Microsoft Teams but is null.");
    await gracefulLeaveFunction(page, 1, "missing_meeting_url");
    return;
  }

  try {
    // Step 1: Navigate to Teams meeting (exactly like simple-bot.js)
    log(`Step 1: Navigating to Teams meeting: ${botConfig.meetingUrl}`);
    await page.goto(botConfig.meetingUrl, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(5000);
    
    // Take initial screenshot
    await page.screenshot({ path: '/app/screenshots/teams-step-1-initial.png', fullPage: true });
    log("📸 Screenshot: Step 1 - Initial page load");

    // Step 2: Try to find and click "Continue on this browser" button (exactly like simple-bot.js)
    log("Step 2: Looking for 'Continue on this browser' button...");
    try {
      const continueButton = page.getByRole('button', { name: 'Continue on this browser' });
      await continueButton.waitFor({ timeout: 10000 });
      await continueButton.click();
      log("✅ Clicked 'Continue on this browser'");
      await page.waitForTimeout(3000);
    } catch (error) {
      log("ℹ️ 'Continue on this browser' button not found, trying alternative selectors...");
      
      // Try alternative selectors for continue button
      try {
        const altContinueButton = page.locator('button:has-text("Continue")').first();
        await altContinueButton.waitFor({ timeout: 5000 });
        await altContinueButton.click();
        log("✅ Clicked alternative continue button");
        await page.waitForTimeout(3000);
      } catch (altError) {
        log("ℹ️ Alternative continue button not found, continuing...");
      }
    }

    await page.screenshot({ path: '/app/screenshots/teams-step-2-after-continue.png', fullPage: true });
    log("📸 Screenshot: Step 2 - After continue click");

    // Step 3: Try to find and click "Join now" button (exactly like simple-bot.js)
    log("Step 3: Looking for 'Join now' button...");
    try {
      const joinNowButton = page.getByRole('button', { name: 'Join now' });
      await joinNowButton.waitFor({ timeout: 10000 });
      await joinNowButton.click();
      log("✅ Clicked 'Join now'");
      await page.waitForTimeout(3000);
    } catch (error) {
      log("ℹ️ 'Join now' button not found, trying alternative selectors...");
      
      // Try alternative selectors for join button
      try {
        const altJoinButton = page.locator('button:has-text("Join")').first();
        await altJoinButton.waitFor({ timeout: 5000 });
        await altJoinButton.click();
        log("✅ Clicked alternative join button");
        await page.waitForTimeout(3000);
      } catch (altError) {
        log("ℹ️ Alternative join button not found, continuing...");
      }
    }

    // Step 4: Try to turn off camera (exactly like simple-bot.js)
    log("Step 4: Trying to turn off camera...");
    try {
      const cameraButton = page.getByRole('button', { name: 'Turn off camera' });
      await cameraButton.waitFor({ timeout: 5000 });
      await cameraButton.click();
      log("✅ Camera turned off");
    } catch (error) {
      log("ℹ️ Camera button not found or already off");
    }

    // Step 5: Try to set display name (exactly like simple-bot.js)
    log("Step 5: Trying to set display name...");
    try {
      const nameInput = page.getByRole('textbox', { name: 'Display name' });
      await nameInput.waitFor({ timeout: 5000 });
      await nameInput.fill(botConfig.botName);
      log(`✅ Display name set to "${botConfig.botName}"`);
    } catch (error) {
      log("ℹ️ Display name input not found, trying alternative selectors...");
      
      // Try alternative selectors for name input
      try {
        const altNameInput = page.locator('input[placeholder*="name"], input[placeholder*="Name"], input[type="text"]').first();
        await altNameInput.waitFor({ timeout: 5000 });
        await altNameInput.fill(botConfig.botName);
        log(`✅ Display name set to "${botConfig.botName}" using alternative selector`);
      } catch (altError) {
        log("ℹ️ Alternative name input not found, trying text-based selector...");
        
        // Try text-based selector
        try {
          const textNameInput = page.locator('input:has-text("Type your name")').first();
          await textNameInput.waitFor({ timeout: 3000 });
          await textNameInput.fill(botConfig.botName);
          log(`✅ Display name set to "${botConfig.botName}" using text selector`);
        } catch (textError) {
          log("ℹ️ Text-based name input not found, trying CSS selector...");
          
          // Try CSS selector for the name input
          try {
            const cssNameInput = page.locator('input[type="text"]').first();
            await cssNameInput.waitFor({ timeout: 3000 });
            await cssNameInput.fill(botConfig.botName);
            log(`✅ Display name set to "${botConfig.botName}" using CSS selector`);
          } catch (cssError) {
            log("ℹ️ CSS selector name input not found");
          }
        }
      }
    }

    // Step 6: Try to find final join button (exactly like simple-bot.js)
    log("Step 6: Looking for final join button...");
    try {
      const finalJoinButton = page.getByRole('button', { name: 'Join' });
      await finalJoinButton.waitFor({ timeout: 10000 });
      await finalJoinButton.click();
      log("✅ Clicked final join button");
    await page.waitForTimeout(5000);
    } catch (error) {
      log("ℹ️ Final join button not found, trying alternative selectors...");
      
      // Try alternative selectors for join button
      try {
        const altJoinButton = page.locator('button:has-text("Join now"), button:has-text("Join")').first();
        await altJoinButton.waitFor({ timeout: 5000 });
        await altJoinButton.click();
        log("✅ Clicked alternative join button");
        await page.waitForTimeout(5000);
      } catch (altError) {
        log("ℹ️ Alternative join button not found");
      }
    }

    await page.screenshot({ path: '/app/screenshots/teams-step-3-after-permissions.png', fullPage: true });
    log("📸 Screenshot: Step 3 - After join attempts");

    // Step 7: Check current state (exactly like simple-bot.js)
    log("Step 7: Checking current state...");
    const currentUrl = page.url();
    log(`📍 Current URL: ${currentUrl}`);
    
    // Setup websocket connection and meeting admission concurrently
    log("Starting WebSocket connection while waiting for Teams meeting admission");
    try {
      // Run both processes concurrently
      const [isAdmitted] = await Promise.all([
        // Wait for admission to the Teams meeting
        waitForTeamsMeetingAdmission(page, botConfig.automaticLeave.waitingRoomTimeout).catch((error) => {
          log("Teams meeting admission failed: " + error.message);
          return false;
        }),

        // Prepare for recording (expose functions, etc.) while waiting for admission
        prepareForRecording(page),
      ]);
    
    if (!isAdmitted) {
      log("Bot was not admitted into the Teams meeting within the timeout period. This is a normal completion.");
      await gracefulLeaveFunction(page, 0, "admission_failed");
      return;
    }

    log("Successfully admitted to the Teams meeting, starting recording");
    
    // --- ADDED: Call startup callback to notify bot-manager that bot is active ---
    try {
      await callStartupCallback(botConfig);
      log("Startup callback sent successfully");
    } catch (callbackError: any) {
      log(`Warning: Failed to send startup callback: ${callbackError.message}. Continuing with recording...`);
    }
    
    // Start recording with Teams-specific logic
    await startTeamsRecording(page, botConfig);
    } catch (error: any) {
      console.error("Error after Teams join attempt (admission/recording setup): " + error.message);
      log("Error after Teams join attempt (admission/recording setup): " + error.message + ". Triggering graceful leave.");
      
      // Capture detailed error information for debugging
      const errorDetails = {
        error_message: error.message,
        error_stack: error.stack,
        error_name: error.name,
        context: "post_join_setup_error",
        platform: "teams",
        timestamp: new Date().toISOString()
      };
      
      // Use a general error code here, as it could be various issues.
      await gracefulLeaveFunction(page, 1, "post_join_setup_error", errorDetails);
      return;
    }

  } catch (error: any) {
    log(`❌ Error in Microsoft Teams bot: ${error.message}`);
    await gracefulLeaveFunction(page, 1, "teams_error", error);
  }
}

// --- ADDED: Exported function to trigger leave from Node.js ---
export async function leaveMicrosoftTeams(page: Page | null): Promise<boolean> {
  log("[leaveMicrosoftTeams] Triggering leave action in browser context...");
  if (!page || page.isClosed()) {
    log("[leaveMicrosoftTeams] Page is not available or closed.");
    return false;
  }
  try {
    const result = await page.evaluate(async () => {
      if (typeof (window as any).performLeaveAction === "function") {
        return await (window as any).performLeaveAction();
      } else {
        (window as any).logBot?.("[Node Eval Error] performLeaveAction function not found on window.");
        console.error("[Node Eval Error] performLeaveAction function not found on window.");
        return false;
      }
    });
    log(`[leaveMicrosoftTeams] Browser leave action result: ${result}`);
    return result;
  } catch (error: any) {
    log(`[leaveMicrosoftTeams] Error calling performLeaveAction in browser: ${error.message}`);
    return false;
  }
}