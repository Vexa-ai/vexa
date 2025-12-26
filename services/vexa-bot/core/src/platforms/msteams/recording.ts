import { Page } from "playwright";
import { log } from "../../utils";
import { BotConfig } from "../../types";
import { WhisperLiveService } from "../../services/whisperlive";
import { ensureBrowserUtils } from "../../utils/injection";
import {
  teamsParticipantSelectors,
  teamsSpeakingClassNames,
  teamsSilenceClassNames,
  teamsParticipantContainerSelectors,
  teamsNameSelectors,
  teamsSpeakingIndicators,
  teamsVoiceLevelSelectors,
  teamsOcclusionSelectors,
  teamsStreamTypeSelectors,
  teamsAudioActivitySelectors,
  teamsParticipantIdSelectors,
  teamsMeetingContainerSelectors
} from "./selectors";

// Modified to use new services - Teams recording functionality
export async function startTeamsRecording(page: Page, botConfig: BotConfig): Promise<void> {
  // Initialize WhisperLive service on Node.js side
  const whisperLiveService = new WhisperLiveService({
    whisperLiveUrl: process.env.WHISPER_LIVE_URL
  });

  // Initialize WhisperLive connection with STUBBORN reconnection - NEVER GIVES UP!
  const whisperLiveUrl = await whisperLiveService.initializeWithStubbornReconnection("Teams");

  log(`[Node.js] Using WhisperLive URL for Teams: ${whisperLiveUrl}`);
  log("Starting Teams recording with WebSocket connection");

  await ensureBrowserUtils(page, require('path').join(__dirname, '../../browser-utils.global.js'));

  // Pass the necessary config fields and the resolved URL into the page context
  await page.evaluate(
    async (pageArgs: {
      botConfigData: BotConfig;
      whisperUrlForBrowser: string;
      selectors: {
        participantSelectors: string[];
        speakingClasses: string[];
        silenceClasses: string[];
        containerSelectors: string[];
        nameSelectors: string[];
        speakingIndicators: string[];
        voiceLevelSelectors: string[];
        occlusionSelectors: string[];
        streamTypeSelectors: string[];
        audioActivitySelectors: string[];
        participantIdSelectors: string[];
        meetingContainerSelectors: string[];
      };
    }) => {
      const { botConfigData, whisperUrlForBrowser, selectors } = pageArgs;
      const selectorsTyped = selectors as any;

      // Use browser utility classes from the global bundle
      const { BrowserAudioService, BrowserWhisperLiveService } = (window as any).VexaBrowserUtils;

      // --- Early reconfigure wiring (event listener only) ---
      (window as any).__vexaPendingReconfigure = null;
      try {
        document.addEventListener('vexa:reconfigure', (ev: Event) => {
          try {
            const detail = (ev as CustomEvent).detail || {};
            const { lang, task } = detail;
            const fn = (window as any).triggerWebSocketReconfigure;
            if (typeof fn === 'function') fn(lang, task);
          } catch {}
        });
      } catch {}
      // ---------------------------------------------
      
      const audioService = new BrowserAudioService({
        targetSampleRate: 16000,
        bufferSize: 4096,
        inputChannels: 1,
        outputChannels: 1
      });

      // Use BrowserWhisperLiveService with stubborn mode for Teams
      const whisperLiveService = new BrowserWhisperLiveService({
        whisperLiveUrl: whisperUrlForBrowser
      }, true); // Enable stubborn mode for Teams

      // Expose references for reconfiguration
      (window as any).__vexaWhisperLiveService = whisperLiveService;
      (window as any).__vexaAudioService = audioService;
      (window as any).__vexaBotConfig = botConfigData;

      // Replace with real reconfigure implementation and apply any queued update
      (window as any).triggerWebSocketReconfigure = async (lang: string | null, task: string | null) => {
        try {
          const svc = (window as any).__vexaWhisperLiveService;
          const cfg = (window as any).__vexaBotConfig || {};
          if (!svc) {
            // Service not ready yet, queue the update
            (window as any).__vexaPendingReconfigure = { lang, task };
            (window as any).logBot?.('[Reconfigure] WhisperLive service not ready; queued for later.');
            return;
          }
          cfg.language = lang;
          cfg.task = task || 'transcribe';
          (window as any).__vexaBotConfig = cfg;
          
          // Close existing connection to establish new session from scratch
          (window as any).logBot?.(`[Reconfigure] Closing existing connection to establish new session...`);
          try { 
            // Use closeForReconfigure to prevent auto-reconnect during manual reconfigure
            if (svc?.closeForReconfigure) {
              svc.closeForReconfigure();
            } else {
              svc.close();
            }
            // Reset audio service session start time so speaker events use new session timestamps
            const audioSvc = (window as any).__vexaAudioService;
            if (audioSvc?.resetSessionStartTime) {
              audioSvc.resetSessionStartTime();
            }
            // Wait a brief moment to ensure socket is fully closed
            await new Promise(resolve => setTimeout(resolve, 100));
          } catch (closeErr: any) {
            (window as any).logBot?.(`[Reconfigure] Error closing connection: ${closeErr?.message || closeErr}`);
          }
          
          // Reconnect with new config - this will generate a new session_uid
          (window as any).logBot?.(`[Reconfigure] Reconnecting with new config: language=${cfg.language}, task=${cfg.task}`);
          await svc.connectToWhisperLive(
            cfg,
            (window as any).__vexaOnMessage,
            (window as any).__vexaOnError,
            (window as any).__vexaOnClose
          );
          (window as any).logBot?.(`[Reconfigure] Successfully reconnected with new session. Language=${cfg.language}, Task=${cfg.task}`);
        } catch (e: any) {
          (window as any).logBot?.(`[Reconfigure] Error applying new config: ${e?.message || e}`);
        }
      };
      try {
        const pending = (window as any).__vexaPendingReconfigure;
        if (pending && typeof (window as any).triggerWebSocketReconfigure === 'function') {
          (window as any).triggerWebSocketReconfigure(pending.lang, pending.task);
          (window as any).__vexaPendingReconfigure = null;
        }
      } catch {}

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
              // Only send after server ready
              if (!whisperLiveService.isReady()) {
                return;
              }
              // Compute simple RMS and peak for diagnostics
              let sumSquares = 0;
              let peak = 0;
              for (let i = 0; i < audioData.length; i++) {
                const v = audioData[i];
                sumSquares += v * v;
                const a = Math.abs(v);
                if (a > peak) peak = a;
              }
              const rms = Math.sqrt(sumSquares / Math.max(1, audioData.length));
              // Diagnostic: send metadata first
              whisperLiveService.sendAudioChunkMetadata(audioData.length, 16000);
              // Send audio data to WhisperLive
              const success = whisperLiveService.sendAudioData(audioData);
              if (!success) {
                (window as any).logBot("Failed to send Teams audio data to WhisperLive");
              }
            });

            // Initialize WhisperLive WebSocket connection with reusable callbacks
            const onMessage = (data: any) => {
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
              }
            };
            const onError = (event: Event) => {
              (window as any).logBot(`[Teams Failover] WebSocket error. This will trigger retry logic.`);
            };
            const onClose = async (event: CloseEvent) => {
              (window as any).logBot(`[Teams Failover] WebSocket connection closed. Code: ${event.code}, Reason: ${event.reason}.`);
            };

            // Save callbacks globally for reuse
            (window as any).__vexaOnMessage = onMessage;
            (window as any).__vexaOnError = onError;
            (window as any).__vexaOnClose = onClose;

            return await whisperLiveService.connectToWhisperLive(
              (window as any).__vexaBotConfig,
              onMessage,
              onError,
              onClose
            );
          }).then(() => {
            // Initialize Teams-specific speaker detection (browser context)
            (window as any).logBot("Initializing Teams speaker detection...");
            
            // Unified Teams speaker detection - NO FALLBACKS (signal-only approach)
            const initializeTeamsSpeakerDetection = (whisperLiveService: any, audioService: any, botConfigData: any) => {
              (window as any).logBot("Setting up ROBUST Teams speaker detection (NO FALLBACKS - signal-only)...");
              
              // Teams-specific configuration for speaker detection
              const participantSelectors = selectors.participantSelectors;
              
              // ============================================================================
              // UNIFIED SPEAKER DETECTION SYSTEM (NO FALLBACKS)
              // ============================================================================
              
              // Participant Identity Cache
              interface ParticipantIdentity {
                id: string;
                name: string;
                element: HTMLElement;
                lastSeen: number;
              }
              
              class ParticipantRegistry {
                private cache = new Map<HTMLElement, ParticipantIdentity>();
                private idToElement = new Map<string, HTMLElement>();

                getIdentity(element: HTMLElement): ParticipantIdentity {
                  if (!this.cache.has(element)) {
                    const id = this.extractId(element);
                    const name = this.extractName(element);
                    
                    const identity: ParticipantIdentity = {
                      id,
                      name,
                      element,
                      lastSeen: Date.now()
                    };
                    
                    this.cache.set(element, identity);
                    this.idToElement.set(id, element);
                  }
                  
                  return this.cache.get(element)!;
                }

                invalidate(element: HTMLElement) {
                  const identity = this.cache.get(element);
                  if (identity) {
                    this.idToElement.delete(identity.id);
                    this.cache.delete(element);
                  }
                }

                private extractId(element: HTMLElement): string {
                  // Use data-acc-element-id as primary (most stable)
                  let id = element.getAttribute('data-acc-element-id') ||
                           element.getAttribute('data-tid') ||
                        element.getAttribute('data-participant-id') ||
                        element.getAttribute('data-user-id') ||
                        element.getAttribute('data-object-id') ||
                        element.getAttribute('id');
                
                if (!id) {
                    const stableChild = element.querySelector(selectorsTyped.participantIdSelectors?.join(', ') || '[data-tid]');
                  if (stableChild) {
                    id = stableChild.getAttribute('data-tid') || 
                         stableChild.getAttribute('data-participant-id') ||
                         stableChild.getAttribute('data-user-id');
                  }
                }
                
                if (!id) {
                  if (!(element as any).dataset.vexaGeneratedId) {
                    (element as any).dataset.vexaGeneratedId = 'teams-id-' + Math.random().toString(36).substr(2, 9);
                  }
                    id = (element as any).dataset.vexaGeneratedId as string;
                }
                
                  return id!;
              }
              
                private extractName(element: HTMLElement): string {
                  const nameSelectors = selectors.nameSelectors || [];
                
                for (const selector of nameSelectors) {
                    const nameElement = element.querySelector(selector) as HTMLElement;
                  if (nameElement) {
                    let nameText = nameElement.textContent || 
                                  nameElement.innerText || 
                                  nameElement.getAttribute('title') ||
                                  nameElement.getAttribute('aria-label');
                    
                    if (nameText && nameText.trim()) {
                      nameText = nameText.trim();
                      
                      const forbiddenSubstrings = [
                        "more_vert", "mic_off", "mic", "videocam", "videocam_off", 
                        "present_to_all", "devices", "speaker", "speakers", "microphone",
                        "camera", "camera_off", "share", "chat", "participant", "user"
                      ];
                      
                        if (!forbiddenSubstrings.some(sub => nameText!.toLowerCase().includes(sub.toLowerCase()))) {
                        if (nameText.length > 1 && nameText.length < 50) {
                          return nameText;
                        }
                      }
                    }
                  }
                }
                
                  const ariaLabel = element.getAttribute('aria-label');
                if (ariaLabel && ariaLabel.includes('name')) {
                  const nameMatch = ariaLabel.match(/name[:\s]+([^,]+)/i);
                  if (nameMatch && nameMatch[1]) {
                    const nameText = nameMatch[1].trim();
                    if (nameText.length > 1 && nameText.length < 50) {
                      return nameText;
                    }
                  }
                }
                
                  const id = this.extractId(element);
                  return `Teams Participant (${id})`;
                }
              }

              // Unified State Machine
              type SpeakingState = 'speaking' | 'silent' | 'unknown';

              interface ParticipantState {
                state: SpeakingState;
                hasSignal: boolean;
                lastChangeTime: number;
                lastEventTime: number;
              }

              class SpeakerStateMachine {
                private state = new Map<string, ParticipantState>();
                private readonly MIN_STATE_CHANGE_MS = 200;

                updateState(participantId: string, detectionResult: { isSpeaking: boolean; hasSignal: boolean }): boolean {
                  const current = this.state.get(participantId);
                  const now = Date.now();

                  if (!detectionResult.hasSignal) {
                    if (current?.hasSignal) {
                      this.state.set(participantId, {
                        state: 'unknown',
                        hasSignal: false,
                        lastChangeTime: now,
                        lastEventTime: current.lastEventTime
                      });
                    }
                    return false;
                  }

                  const newState: SpeakingState = detectionResult.isSpeaking ? 'speaking' : 'silent';

                  if (current?.state === newState && current?.hasSignal) {
                    return false;
                  }

                  if (current && (now - current.lastChangeTime) < this.MIN_STATE_CHANGE_MS) {
                    return false;
                  }

                  this.state.set(participantId, {
                    state: newState,
                    hasSignal: true,
                    lastChangeTime: now,
                    lastEventTime: current?.lastEventTime || 0
                  });

                  return true;
                }

                getState(participantId: string): SpeakingState | null {
                  return this.state.get(participantId)?.state || null;
                }

                remove(participantId: string) {
                  this.state.delete(participantId);
                }
              }

              // Robust Detection Logic (NO FALLBACKS)
              type SpeakingDetectionResult = {
                isSpeaking: boolean;
                hasSignal: boolean;
              };

              class TeamsSpeakingDetector {
                private readonly VOICE_LEVEL_SELECTOR = '[data-tid="voice-level-stream-outline"]';

                detectSpeakingState(element: HTMLElement): SpeakingDetectionResult {
                  const voiceOutline = element.querySelector(this.VOICE_LEVEL_SELECTOR) as HTMLElement | null;
                  
                  if (!voiceOutline) {
                    return { isSpeaking: false, hasSignal: false };
                  }

                  // Check if voice-level-stream-outline or any of its parents has vdi-frame-occlusion class
                  // vdi-frame-occlusion class presence = speaking, absence = not speaking
                  let current: HTMLElement | null = voiceOutline;
                  let hasVdiFrameOcclusion = false;
                  
                  // Check the element itself and walk up the parent chain
                  while (current && !hasVdiFrameOcclusion) {
                    if (current.classList.contains('vdi-frame-occlusion')) {
                      hasVdiFrameOcclusion = true;
                      break;
                    }
                    current = current.parentElement;
                  }
                  
                  return {
                    isSpeaking: hasVdiFrameOcclusion,
                    hasSignal: true
                  };
                }

                hasRequiredSignal(element: HTMLElement): boolean {
                  return element.querySelector(this.VOICE_LEVEL_SELECTOR) !== null;
                }

                private isElementVisible(el: HTMLElement): boolean {
                const cs = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                const ariaHidden = el.getAttribute('aria-hidden') === 'true';
                const transform = cs.transform || '';
                  const scaledToZero = /matrix\((?:[^,]+,){4}\s*0(?:,|\s*\))/.test(transform) ||
                                       transform.includes('scale(0');

                return (
                  rect.width > 0 &&
                  rect.height > 0 &&
                  cs.display !== 'none' &&
                  cs.visibility !== 'hidden' &&
                  cs.opacity !== '0' &&
                  !ariaHidden &&
                    !scaledToZero
                  );
                }
              }

              // Event Debouncer
              class EventDebouncer {
                private timers = new Map<string, number>();
                private readonly delayMs: number;

                constructor(delayMs: number = 300) {
                  this.delayMs = delayMs;
                }

                debounce(key: string, fn: () => void) {
                  if (this.timers.has(key)) {
                    clearTimeout(this.timers.get(key)!);
                  }

                  const timer = setTimeout(() => {
                    fn();
                    this.timers.delete(key);
                  }, this.delayMs) as unknown as number;

                  this.timers.set(key, timer);
                }

                cancel(key: string) {
                  if (this.timers.has(key)) {
                    clearTimeout(this.timers.get(key)!);
                    this.timers.delete(key);
                  }
                }

                cancelAll() {
                  this.timers.forEach(timer => clearTimeout(timer));
                  this.timers.clear();
                }
              }

              // Initialize components
              const registry = new ParticipantRegistry();
              const stateMachine = new SpeakerStateMachine();
              const detector = new TeamsSpeakingDetector();
              const debouncer = new EventDebouncer(300);
              const observers = new Map<HTMLElement, MutationObserver[]>();
              const rafHandles = new Map<string, number>();
              
              // State for tracking speaking status (for cleanup)
              const speakingStates = new Map<string, SpeakingState>();
              
              // Event emission helper
              function sendTeamsSpeakerEvent(eventType: string, identity: ParticipantIdentity) {
                const eventAbsoluteTimeMs = Date.now();
                const sessionStartTime = audioService.getSessionAudioStartTime();
                
                if (sessionStartTime === null) {
                  return;
                }
                
                const relativeTimestampMs = eventAbsoluteTimeMs - sessionStartTime;
                
                try {
                  whisperLiveService.sendSpeakerEvent(
                    eventType,
                    identity.name,
                    identity.id,
                    relativeTimestampMs,
                    botConfigData
                  );
                } catch (error: any) {
                  // Handle errors silently
                }
              }
              // Unified Observer System
              function observeParticipant(element: HTMLElement) {
                if ((element as any).dataset.vexaObserverAttached) {
                  return;
                }

                // ROBUST CHECK: Only observe if signal exists
                if (!detector.hasRequiredSignal(element)) {
                  (window as any).logBot(`⚠️ [Unified] Skipping participant - no voice-level-stream-outline signal found`);
                  return;
                }

                const identity = registry.getIdentity(element);
                (element as any).dataset.vexaObserverAttached = 'true';

                (window as any).logBot(`👁️ [Unified] Observing: ${identity.name} (ID: ${identity.id}) - signal present`);

                const voiceOutline = element.querySelector('[data-tid="voice-level-stream-outline"]') as HTMLElement;
                if (!voiceOutline) {
                  (window as any).logBot(`❌ [Unified] Voice outline disappeared for ${identity.name}`);
                  return;
                }

                // Observer on voice-level element (PRIMARY SIGNAL)
                const voiceObserver = new MutationObserver(() => {
                  checkAndEmit(identity);
                });
                voiceObserver.observe(voiceOutline, {
                  attributes: true,
                  attributeFilter: ['style', 'class', 'aria-hidden'],
                  childList: false,
                  subtree: false
                });

                // Observer on container (detect signal loss)
                const containerObserver = new MutationObserver(() => {
                  if (!detector.hasRequiredSignal(element)) {
                    (window as any).logBot(`⚠️ [Unified] Voice-level signal lost for ${identity.name} - stopping observation`);
                    handleParticipantRemoved(identity);
                    return;
                  }
                  checkAndEmit(identity);
                });
                containerObserver.observe(element, {
                  childList: true,
                  subtree: true,
                  attributes: false
                });

                observers.set(element, [voiceObserver, containerObserver]);

                // rAF-based polling
                scheduleRAFCheck(identity);

                // Initial check
                checkAndEmit(identity);
              }

              function checkAndEmit(identity: ParticipantIdentity) {
                if (!identity.element.isConnected) {
                  handleParticipantRemoved(identity);
                  return;
                }

                const detectionResult = detector.detectSpeakingState(identity.element);

                if (stateMachine.updateState(identity.id, detectionResult)) {
                  if (detectionResult.hasSignal) {
                    const newState: SpeakingState = detectionResult.isSpeaking ? 'speaking' : 'silent';
                    speakingStates.set(identity.id, newState);
                    debouncer.debounce(identity.id, () => {
                      emitEvent(newState, identity);
                    });
                  }
                }
              }

              function scheduleRAFCheck(identity: ParticipantIdentity) {
                const check = () => {
                  if (!identity.element.isConnected) {
                    handleParticipantRemoved(identity);
                    return;
                  }

                  checkAndEmit(identity);
                  
                  const handle = requestAnimationFrame(check);
                  rafHandles.set(identity.id, handle);
                };

                const handle = requestAnimationFrame(check);
                rafHandles.set(identity.id, handle);
              }

              function handleParticipantRemoved(identity: ParticipantIdentity) {
                debouncer.cancel(identity.id);

                if (stateMachine.getState(identity.id) === 'speaking') {
                  emitEvent('silent', identity);
                }

                const obs = observers.get(identity.element);
                if (obs) {
                  obs.forEach(o => o.disconnect());
                  observers.delete(identity.element);
                }

                const rafHandle = rafHandles.get(identity.id);
                if (rafHandle) {
                  cancelAnimationFrame(rafHandle);
                  rafHandles.delete(identity.id);
                }

                stateMachine.remove(identity.id);
                speakingStates.delete(identity.id);
                registry.invalidate(identity.element);
                delete (identity.element as any).dataset.vexaObserverAttached;

                (window as any).logBot(`🗑️ [Unified] Removed: ${identity.name} (ID: ${identity.id})`);
              }

              function emitEvent(state: SpeakingState, identity: ParticipantIdentity) {
                if (state === 'unknown') {
                      return;
                    }

                const eventType = state === 'speaking' ? 'SPEAKER_START' : 'SPEAKER_END';
                const emoji = state === 'speaking' ? '🎤' : '🔇';

                (window as any).logBot(`${emoji} [Unified] ${eventType}: ${identity.name} (ID: ${identity.id}) [signal-based]`);
                sendTeamsSpeakerEvent(eventType, identity);
              }

              function scanAndObserveAll() {
                let foundCount = 0;
                let observedCount = 0;

                // CRITICAL: Also check [role="menuitem"] directly (most reliable selector)
                const allSelectors = [...participantSelectors, '[role="menuitem"]'];
                const seenElements = new WeakSet<HTMLElement>();

                for (const selector of allSelectors) {
                  const elements = document.querySelectorAll(selector);
                  elements.forEach(el => {
                    if (el instanceof HTMLElement && !seenElements.has(el)) {
                      seenElements.add(el);
                      foundCount++;
                      if (detector.hasRequiredSignal(el)) {
                        observeParticipant(el);
                        observedCount++;
                      }
                    }
                  });
                }

                (window as any).logBot(`🔍 [Unified] Scanned ${foundCount} participants, observing ${observedCount} with signal`);
              }

              // Initialize speaker detection
              scanAndObserveAll();
              
              // Monitor for new participants
              const bodyObserver = new MutationObserver((mutationsList) => {
                for (const mutation of mutationsList) {
                  if (mutation.type === 'childList') {
                    mutation.addedNodes.forEach(node => {
                      if (node.nodeType === Node.ELEMENT_NODE) {
                        const elementNode = node as HTMLElement;
                        
                        // Check if the added node matches any participant selector OR [role="menuitem"]
                        const allSelectors = [...participantSelectors, '[role="menuitem"]'];
                        for (const selector of allSelectors) {
                          if (elementNode.matches(selector)) {
                            // observeParticipant will check for signal before observing
                            observeParticipant(elementNode);
                          }
                          
                          // Check children
                          const childElements = elementNode.querySelectorAll(selector);
                          childElements.forEach(childEl => {
                            if (childEl instanceof HTMLElement) {
                              // observeParticipant will check for signal before observing
                              observeParticipant(childEl);
                            }
                          });
                        }
                      }
                    });
                    
                    mutation.removedNodes.forEach(node => {
                      if (node.nodeType === Node.ELEMENT_NODE) {
                        const elementNode = node as HTMLElement;
                        
                        // Check if removed node was a participant
                        for (const selector of participantSelectors) {
                          if (elementNode.matches(selector)) {
                            const identity = registry.getIdentity(elementNode);
                            if (speakingStates.get(identity.id) === 'speaking') {
                              (window as any).logBot(`🔇 [Unified] SPEAKER_END (Participant removed while speaking): ${identity.name} (ID: ${identity.id})`);
                              emitEvent('silent', identity);
                            }
                            handleParticipantRemoved(identity);
                          }
                        }
                      }
                    });
                  }
                }
              });

              // Start observing the Teams meeting container
              const meetingContainer = document.querySelector(selectorsTyped.meetingContainerSelectors[0]) || document.body;
              bodyObserver.observe(meetingContainer, {
                childList: true,
                subtree: true
              });

              // Simple participant counting - poll every 5 seconds using ARIA list
              let currentParticipantCount = 0;
              
              const countParticipants = () => {
                const names = collectAriaParticipants();
                const totalCount = botConfigData?.name ? names.length + 1 : names.length;
                if (totalCount !== currentParticipantCount) {
                  (window as any).logBot(`🔢 Participant count: ${currentParticipantCount} → ${totalCount}`);
                  currentParticipantCount = totalCount;
                }
                return totalCount;
              };
              
              // Do initial count immediately, then poll every 5 seconds
              countParticipants();
              setInterval(countParticipants, 5000);
              
              // Expose participant count for meeting monitoring
              // Accessible-roles based participant collection (robust and simple)
              function collectAriaParticipants(): string[] {
                try {
                  // Find all menuitems in the Participants panel that contain an avatar/image
                  const menuItems = Array.from(document.querySelectorAll('[role="menuitem"]')) as HTMLElement[];
                  const names = new Set<string>();
                  for (const item of menuItems) {
                    const hasImg = !!(item.querySelector('img') || item.querySelector('[role="img"]'));
                    if (!hasImg) continue;
                    // Derive accessible-like name
                    const aria = item.getAttribute('aria-label');
                    let name = aria && aria.trim() ? aria.trim() : '';
                    if (!name) {
                      const text = (item.textContent || '').trim();
                      if (text) name = text;
                    }
                    if (name) {
                      names.add(name);
                    }
                  }
                  return Array.from(names);
                } catch (err: any) {
                  const msg = (err && err.message) ? err.message : String(err);
                  (window as any).logBot?.(`⚠️ [ARIA Participants] Error collecting participants: ${msg}`);
                  return [];
                }
              }

              (window as any).getTeamsActiveParticipantsCount = () => {
                // Use ARIA role-based collection and include the bot if name is known
                const names = collectAriaParticipants();
                const total = botConfigData?.name ? names.length + 1 : names.length;
                return total;
              };
              (window as any).getTeamsActiveParticipants = () => {
                // Return ARIA role-based names plus bot (if known)
                const names = collectAriaParticipants();
                if (botConfigData?.name) names.push(botConfigData.name);
                (window as any).logBot(`🔍 [ARIA Participants] ${JSON.stringify(names)}`);
                return names;
              };
            };

            // Setup Teams meeting monitoring (browser context)
            const setupTeamsMeetingMonitoring = (botConfigData: any, audioService: any, whisperLiveService: any, resolve: any) => {
              (window as any).logBot("Setting up Teams meeting monitoring...");
              
              const leaveCfg = (botConfigData && (botConfigData as any).automaticLeave) || {};
              const startupAloneTimeoutSeconds = Number(leaveCfg.startupAloneTimeoutSeconds ?? 10);
              const everyoneLeftTimeoutSeconds = Number(leaveCfg.everyoneLeftTimeoutSeconds ?? 10);
              
              let aloneTime = 0;
              let lastParticipantCount = 0;
              let speakersIdentified = false;
              let hasEverHadMultipleParticipants = false;

              // Teams removal detection function (browser context)
              const checkForRemoval = () => {
                try {
                  // 1) Strong text heuristics on body text
                  const bodyText = (document.body?.innerText || '').toLowerCase();
                  const removalPhrases = [
                    "you've been removed from this meeting",
                    'you have been removed from this meeting',
                    'removed from meeting',
                    'meeting ended',
                    'call ended'
                  ];
                  if (removalPhrases.some(p => bodyText.includes(p))) {
                    (window as any).logBot('🚨 Teams removal detected via body text');
                    return true;
                  }

                  // 2) Button heuristics
                  const buttons = Array.from(document.querySelectorAll('button')) as HTMLElement[];
                  for (const btn of buttons) {
                    const txt = (btn.textContent || btn.innerText || '').trim().toLowerCase();
                    const aria = (btn.getAttribute('aria-label') || '').toLowerCase();
                    if (txt === 'rejoin' || txt === 'dismiss' || aria.includes('rejoin') || aria.includes('dismiss')) {
                      if (btn.offsetWidth > 0 && btn.offsetHeight > 0) {
                        const cs = getComputedStyle(btn);
                        if (cs.display !== 'none' && cs.visibility !== 'hidden') {
                          (window as any).logBot('🚨 Teams removal detected via visible buttons (Rejoin/Dismiss)');
                          return true;
                        }
                      }
                    }
                  }

                  return false;
                } catch (error: any) {
                  (window as any).logBot(`Error checking for Teams removal: ${error.message}`);
                  return false;
                }
              };

              const checkInterval = setInterval(() => {
                // First check for removal state
                if (checkForRemoval()) {
                  (window as any).logBot("🚨 Bot has been removed from the Teams meeting. Initiating graceful leave...");
                  clearInterval(checkInterval);
                  audioService.disconnect();
                  whisperLiveService.close();
                  reject(new Error("TEAMS_BOT_REMOVED_BY_ADMIN"));
                  return;
                }
                // Check participant count using the comprehensive speaker detection system
                const currentParticipantCount = (window as any).getTeamsActiveParticipantsCount ? (window as any).getTeamsActiveParticipantsCount() : 0;
                
                if (currentParticipantCount !== lastParticipantCount) {
                  (window as any).logBot(`🔢 Teams participant count changed: ${lastParticipantCount} → ${currentParticipantCount}`);
                  const participantList = (window as any).getTeamsActiveParticipants ? (window as any).getTeamsActiveParticipants() : [];
                  (window as any).logBot(`👥 Current participants: ${JSON.stringify(participantList)}`);
                  
                  lastParticipantCount = currentParticipantCount;
                  
                  // Track if we've ever had multiple participants
                  if (currentParticipantCount > 1) {
                    hasEverHadMultipleParticipants = true;
                    speakersIdentified = true; // Once we see multiple participants, we've identified speakers
                    (window as any).logBot("Teams Speakers identified - switching to post-speaker monitoring mode");
                  }
                }

                if (currentParticipantCount === 0) {
                  aloneTime++;
                  
                  // Determine timeout based on whether speakers have been identified
                  const currentTimeout = speakersIdentified ? everyoneLeftTimeoutSeconds : startupAloneTimeoutSeconds;
                  const timeoutDescription = speakersIdentified ? "post-speaker" : "startup";
                  
                  (window as any).logBot(`⏱️ Teams bot alone time: ${aloneTime}s/${currentTimeout}s (${timeoutDescription} mode, speakers identified: ${speakersIdentified})`);
                  
                  if (aloneTime >= currentTimeout) {
                    if (speakersIdentified) {
                      (window as any).logBot(`Teams meeting ended or bot has been alone for ${everyoneLeftTimeoutSeconds} seconds after speakers were identified. Stopping recorder...`);
                      clearInterval(checkInterval);
                      audioService.disconnect();
                      whisperLiveService.close();
                      reject(new Error("TEAMS_BOT_LEFT_ALONE_TIMEOUT"));
                    } else {
                      (window as any).logBot(`Teams bot has been alone for ${startupAloneTimeoutSeconds} seconds during startup with no other participants. Stopping recorder...`);
                      clearInterval(checkInterval);
                      audioService.disconnect();
                      whisperLiveService.close();
                      reject(new Error("TEAMS_BOT_STARTUP_ALONE_TIMEOUT"));
                    }
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

      try {
        const pending = (window as any).__vexaPendingReconfigure;
        if (pending && typeof (window as any).triggerWebSocketReconfigure === 'function') {
          (window as any).triggerWebSocketReconfigure(pending.lang, pending.task);
          (window as any).__vexaPendingReconfigure = null;
        }
      } catch {}
    },
    { 
      botConfigData: botConfig, 
      whisperUrlForBrowser: whisperLiveUrl,
      selectors: {
        participantSelectors: teamsParticipantSelectors,
        speakingClasses: teamsSpeakingClassNames,
        silenceClasses: teamsSilenceClassNames,
        containerSelectors: teamsParticipantContainerSelectors,
        nameSelectors: teamsNameSelectors,
        speakingIndicators: teamsSpeakingIndicators,
        voiceLevelSelectors: teamsVoiceLevelSelectors,
        occlusionSelectors: teamsOcclusionSelectors,
        streamTypeSelectors: teamsStreamTypeSelectors,
        audioActivitySelectors: teamsAudioActivitySelectors,
        participantIdSelectors: teamsParticipantIdSelectors,
        meetingContainerSelectors: teamsMeetingContainerSelectors
      } as any
    }
  );
  
  // After page.evaluate finishes, cleanup services
  await whisperLiveService.cleanup();
}
