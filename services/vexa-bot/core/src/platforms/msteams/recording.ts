import { Page } from "playwright";
import { log } from "../../utils";
import { BotConfig } from "../../types";
import { RecordingService } from "../../services/recording";
import { setActiveRecordingService } from "../../index";
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
  log("Starting Teams recording");

  const wantsAudioCapture =
    !!botConfig.recordingEnabled &&
    (!Array.isArray(botConfig.captureModes) || botConfig.captureModes.includes("audio"));
  const sessionUid = botConfig.connectionId || `teams-${Date.now()}`;
  let recordingService: RecordingService | null = null;

  if (wantsAudioCapture) {
    recordingService = new RecordingService(botConfig.meeting_id, sessionUid);
    setActiveRecordingService(recordingService);

    await page.exposeFunction("__vexaSaveRecordingBlob", async (payload: { base64: string; mimeType?: string }) => {
      try {
        if (!recordingService) {
          log("[Teams Recording] Recording service not initialized; dropping blob.");
          return false;
        }

        const mimeType = (payload?.mimeType || "").toLowerCase();
        let format = "webm";
        if (mimeType.includes("wav")) format = "wav";
        else if (mimeType.includes("ogg")) format = "ogg";
        else if (mimeType.includes("mp4") || mimeType.includes("m4a")) format = "m4a";

        const blobBuffer = Buffer.from(payload.base64 || "", "base64");
        if (!blobBuffer.length) {
          log("[Teams Recording] Received empty audio blob.");
          return false;
        }

        await recordingService.writeBlob(blobBuffer, format);
        log(`[Teams Recording] Saved browser audio blob (${blobBuffer.length} bytes, ${format}).`);
        return true;
      } catch (error: any) {
        log(`[Teams Recording] Failed to persist browser blob: ${error?.message || String(error)}`);
        return false;
      }
    });
  } else {
    log("[Teams Recording] Audio capture disabled by config.");
  }

  await ensureBrowserUtils(page, require('path').join(__dirname, '../../browser-utils.global.js'));

  // Pass the necessary config fields and the resolved URL into the page context
  await page.evaluate(
    async (pageArgs: {
      botConfigData: BotConfig;
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
      const { botConfigData, selectors } = pageArgs;
      const selectorsTyped = selectors as any;

      // Use browser utility classes from the global bundle
      const { BrowserAudioService } = (window as any).VexaBrowserUtils;

      const audioService = new BrowserAudioService({
        targetSampleRate: 16000,
        bufferSize: 4096,
        inputChannels: 1,
        outputChannels: 1
      });

      (window as any).__vexaAudioService = audioService;
      (window as any).__vexaBotConfig = botConfigData;
      (window as any).__vexaMediaRecorder = null;
      (window as any).__vexaRecordedChunks = [];
      (window as any).__vexaRecordingFlushed = false;

      const isAudioRecordingEnabled =
        !!(botConfigData as any)?.recordingEnabled &&
        (!Array.isArray((botConfigData as any)?.captureModes) ||
          (botConfigData as any)?.captureModes.includes("audio"));

      const getSupportedMediaRecorderMimeType = (): string => {
        const candidates = [
          "audio/webm;codecs=opus",
          "audio/webm",
          "audio/ogg;codecs=opus",
          "audio/ogg",
        ];
        for (const mime of candidates) {
          try {
            if ((window as any).MediaRecorder?.isTypeSupported?.(mime)) {
              return mime;
            }
          } catch {}
        }
        return "";
      };

      const flushBrowserRecordingBlob = async (reason: string): Promise<void> => {
        if (!isAudioRecordingEnabled) return;
        if ((window as any).__vexaRecordingFlushed) return;

        try {
          const recorder: MediaRecorder | null = (window as any).__vexaMediaRecorder;
          const chunks: Blob[] = (window as any).__vexaRecordedChunks || [];

          const finalizeAndSend = async () => {
            if ((window as any).__vexaRecordingFlushed) return;
            (window as any).__vexaRecordingFlushed = true;

            try {
              const recorded = (window as any).__vexaRecordedChunks || [];
              if (!recorded.length) {
                (window as any).logBot?.(`[Teams Recording] No media chunks to flush (${reason}).`);
                return;
              }

              const mimeType =
                (window as any).__vexaMediaRecorder?.mimeType || "audio/webm";
              const blob = new Blob(recorded, { type: mimeType });
              const buffer = await blob.arrayBuffer();
              const bytes = new Uint8Array(buffer);
              let binary = "";
              const chunkSize = 0x8000;
              for (let i = 0; i < bytes.length; i += chunkSize) {
                binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
              }
              const base64 = btoa(binary);

              if (typeof (window as any).__vexaSaveRecordingBlob === "function") {
                await (window as any).__vexaSaveRecordingBlob({
                  base64,
                  mimeType: blob.type || mimeType,
                });
                (window as any).logBot?.(
                  `[Teams Recording] Flushed ${bytes.length} bytes (${blob.type || mimeType}) on ${reason}.`
                );
              } else {
                (window as any).logBot?.("[Teams Recording] Node blob sink is not available.");
              }
            } catch (err: any) {
              (window as any).logBot?.(
                `[Teams Recording] Failed to flush blob: ${err?.message || err}`
              );
            } finally {
              (window as any).__vexaRecordedChunks = [];
            }
          };

          if (recorder && recorder.state !== "inactive") {
            await new Promise<void>((resolveStop) => {
              const onStop = async () => {
                recorder.removeEventListener("stop", onStop as any);
                await finalizeAndSend();
                resolveStop();
              };
              recorder.addEventListener("stop", onStop as any, { once: true });
              try {
                recorder.stop();
              } catch {
                setTimeout(async () => {
                  await finalizeAndSend();
                  resolveStop();
                }, 200);
              }
            });
          } else if (chunks.length > 0) {
            await finalizeAndSend();
          }
        } catch (err: any) {
          (window as any).logBot?.(
            `[Teams Recording] Unexpected flush error: ${err?.message || err}`
          );
        }
      };

      (window as any).__vexaFlushRecordingBlob = flushBrowserRecordingBlob;

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

            if (isAudioRecordingEnabled) {
              try {
                const mimeType = getSupportedMediaRecorderMimeType();
                const recorderOptions = mimeType ? ({ mimeType } as MediaRecorderOptions) : undefined;
                const recorder = recorderOptions
                  ? new MediaRecorder(combinedStream, recorderOptions)
                  : new MediaRecorder(combinedStream);

                (window as any).__vexaMediaRecorder = recorder;
                (window as any).__vexaRecordedChunks = [];
                (window as any).__vexaRecordingFlushed = false;

                recorder.ondataavailable = (event: BlobEvent) => {
                  if (event.data && event.data.size > 0) {
                    (window as any).__vexaRecordedChunks.push(event.data);
                  }
                };

                recorder.start(1000);
                (window as any).logBot?.(
                  `[Teams Recording] MediaRecorder started (${recorder.mimeType || mimeType || "default"}).`
                );
              } catch (err: any) {
                (window as any).logBot?.(
                  `[Teams Recording] Failed to start MediaRecorder: ${err?.message || err}`
                );
              }
            }

            // Initialize audio processor
            return await audioService.initializeAudioProcessor(combinedStream);
          }).then(async (processor: any) => {
            // Audio data processor — no-op now; per-speaker pipeline handles transcription
            audioService.setupAudioDataProcessor(async (_audioData: Float32Array, _sessionStartTime: number | null) => {
              // Per-speaker pipeline (speaker-streams.ts) handles transcription.
              // This processor is kept for MediaRecorder / recording only.
            });

            return null;
          }).then(() => {
            // Initialize Teams-specific speaker detection (browser context)
            (window as any).logBot("Initializing Teams speaker detection...");

            // Unified Teams speaker detection - NO FALLBACKS (signal-only approach)
            const initializeTeamsSpeakerDetection = (audioService: any, botConfigData: any) => {
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

                // Accumulate for persistence (direct bot accumulation)
                (window as any).__vexaSpeakerEvents = (window as any).__vexaSpeakerEvents || [];
                (window as any).__vexaSpeakerEvents.push({
                  event_type: eventType,
                  participant_name: identity.name,
                  participant_id: identity.id,
                  relative_timestamp_ms: relativeTimestampMs,
                });
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
            const setupTeamsMeetingMonitoring = (botConfigData: any, audioService: any, resolve: any) => {
              (window as any).logBot("Setting up Teams meeting monitoring...");
              
              const leaveCfg = (botConfigData && (botConfigData as any).automaticLeave) || {};
              // Config values are in milliseconds, convert to seconds
              const startupAloneTimeoutSeconds = leaveCfg.noOneJoinedTimeout
                ? Math.floor(Number(leaveCfg.noOneJoinedTimeout) / 1000)
                : Number(leaveCfg.startupAloneTimeoutSeconds ?? (20 * 60));
              const everyoneLeftTimeoutSeconds = leaveCfg.everyoneLeftTimeout
                ? Math.floor(Number(leaveCfg.everyoneLeftTimeout) / 1000)
                : Number(leaveCfg.everyoneLeftTimeoutSeconds ?? 60);
              
              let aloneTime = 0;
              let lastParticipantCount = 0;
              let speakersIdentified = false;
              let hasEverHadMultipleParticipants = false;
              let monitoringStopped = false;

              const stopWithFlush = async (
                reason: string,
                finish: () => void
              ) => {
                if (monitoringStopped) return;
                monitoringStopped = true;
                clearInterval(checkInterval);
                try {
                  if (typeof (window as any).__vexaFlushRecordingBlob === "function") {
                    await (window as any).__vexaFlushRecordingBlob(reason);
                  }
                } catch (flushErr: any) {
                  (window as any).logBot?.(
                    `[Teams Recording] Flush error during shutdown (${reason}): ${flushErr?.message || flushErr}`
                  );
                }
                audioService.disconnect();
                finish();
              };

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
                  void stopWithFlush("removed_by_admin", () =>
                    reject(new Error("TEAMS_BOT_REMOVED_BY_ADMIN"))
                  );
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
                      void stopWithFlush("left_alone_timeout", () =>
                        reject(new Error("TEAMS_BOT_LEFT_ALONE_TIMEOUT"))
                      );
                    } else {
                      (window as any).logBot(`Teams bot has been alone for ${startupAloneTimeoutSeconds} seconds during startup with no other participants. Stopping recorder...`);
                      void stopWithFlush("startup_alone_timeout", () =>
                        reject(new Error("TEAMS_BOT_STARTUP_ALONE_TIMEOUT"))
                      );
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
                void stopWithFlush("beforeunload", () => resolve());
              });

              document.addEventListener("visibilitychange", () => {
                if (document.visibilityState === "hidden") {
                  (window as any).logBot("Teams document is hidden. Stopping recorder...");
                  void stopWithFlush("visibility_hidden", () => resolve());
                }
              });
            };

            // Initialize Teams-specific speaker detection
            initializeTeamsSpeakerDetection(audioService, botConfigData);
            
            // Setup Teams meeting monitoring
            setupTeamsMeetingMonitoring(botConfigData, audioService, resolve);
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
}
