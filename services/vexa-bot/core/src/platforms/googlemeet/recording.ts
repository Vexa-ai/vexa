import { Page } from "playwright";
import { log } from "../../utils";
import { BotConfig } from "../../types";
import { WhisperLiveService } from "../../services/whisperlive";
import {
  googleParticipantSelectors,
  googleSpeakingClassNames,
  googleSilenceClassNames,
  googleParticipantContainerSelectors,
  googleNameSelectors,
  googleSpeakingIndicators,
  googlePeopleButtonSelectors
} from "./selectors";

// Modified to use new services - Google Meet recording functionality
export async function startGoogleRecording(page: Page, botConfig: BotConfig): Promise<void> {
  // Initialize WhisperLive service on Node.js side
  const whisperLiveService = new WhisperLiveService({
    redisUrl: botConfig.redisUrl,
    maxClients: parseInt(process.env.WL_MAX_CLIENTS || '10', 10),
    whisperLiveUrl: process.env.WHISPER_LIVE_URL
  });

  // Initialize WhisperLive connection
  const whisperLiveUrl = await whisperLiveService.initialize();
  if (!whisperLiveUrl) {
    log("ERROR: Could not initialize WhisperLive service for Google Meet. Aborting recording.");
    return;
  }

  log(`[Node.js] Using WhisperLive URL for Google Meet: ${whisperLiveUrl}`);
  log("Starting Google Meet recording with WebSocket connection");

  // Load browser utility classes from the bundled global file
  try {
    await page.addScriptTag({
      path: require('path').join(__dirname, '../../browser-utils.global.js'),
    });
  } catch (error: any) {
    log(`Warning: Could not load browser utils via addScriptTag: ${error.message}`);
    log("Attempting alternative loading method...");
    
    // Alternative: Load script content and evaluate it
    const fs = require('fs');
    const path = require('path');
    const scriptPath = path.join(__dirname, '../../browser-utils.global.js');
    
    try {
      const scriptContent = fs.readFileSync(scriptPath, 'utf8');
      await page.evaluate(async (script) => {
        try {
          // Use Trusted Types to inject inline script text, or fallback to Blob URL
          const injectWithTrustedTypes = () => {
            const policy = (window as any).trustedTypes?.createPolicy('vexaPolicy', {
              createScript: (s: string) => s,
              createScriptURL: (s: string) => s
            });
            const scriptEl = document.createElement('script');
            if (policy) {
              // Assign TrustedScript to text to satisfy Meet's Trusted Types policy
              (scriptEl as any).text = policy.createScript(script);
              document.head.appendChild(scriptEl);
              return Promise.resolve();
            }
            return Promise.reject(new Error('Trusted Types not available'));
          };

          const injectWithBlobUrl = () => new Promise<void>((resolve, reject) => {
            try {
              const blob = new Blob([script], { type: 'text/javascript' });
              const url = URL.createObjectURL(blob);
              const policy = (window as any).trustedTypes?.createPolicy('vexaPolicy', {
                createScriptURL: (u: string) => u
              });
              const scriptEl = document.createElement('script');
              // If Trusted Types enforced for src, use policy-created URL
              const finalUrl = policy ? (policy as any).createScriptURL(url) : url;
              (scriptEl as any).src = finalUrl as any;
              scriptEl.onload = () => {
                resolve();
              };
              scriptEl.onerror = (e) => {
                reject(new Error('Failed to load browser utils via blob URL'));
              };
              document.head.appendChild(scriptEl);
            } catch (err) {
              reject(err as any);
            }
          });

          // Try Trusted Types inline first, then fallback to blob URL
          try {
            await injectWithTrustedTypes();
          } catch {
            await injectWithBlobUrl();
          }

          // Verify availability on window
          const utils = (window as any).VexaBrowserUtils;
          if (!utils) {
            console.error('VexaBrowserUtils not found after injection');
          } else {
            console.log('VexaBrowserUtils loaded keys:', Object.keys(utils));
          }
        } catch (error) {
          console.error('Error injecting browser utils script:', (error as any)?.message || error);
          throw error;
        }
      }, scriptContent);
      log("Browser utils loaded and available as window.VexaBrowserUtils");
    } catch (evalError: any) {
      log(`Error loading browser utils via evaluate: ${evalError.message}`);
      throw new Error(`Failed to load browser utilities: ${evalError.message}`);
    }
  }

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
        peopleButtonSelectors: string[];
      };
    }) => {
      const { botConfigData, whisperUrlForBrowser, selectors } = pageArgs;

      // Use browser utility classes from the global bundle
      const browserUtils = (window as any).VexaBrowserUtils;
      (window as any).logBot(`Browser utils available: ${Object.keys(browserUtils || {}).join(', ')}`);
      
      const audioService = new browserUtils.BrowserAudioService({
        targetSampleRate: 16000,
        bufferSize: 4096,
        inputChannels: 1,
        outputChannels: 1
      });

      // Use BrowserWhisperLiveService with stubborn mode to enable reconnection on Google Meet
      const whisperLiveService = new browserUtils.BrowserWhisperLiveService({
        whisperLiveUrl: whisperUrlForBrowser
      }, true); // Enable stubborn mode for Google Meet


      await new Promise<void>((resolve, reject) => {
        try {
          (window as any).logBot("Starting Google Meet recording process with new services.");
          
          // Find and create combined audio stream
          audioService.findMediaElements().then(async (mediaElements: HTMLMediaElement[]) => {
            if (mediaElements.length === 0) {
              reject(
                new Error(
                  "[Google Meet BOT Error] No active media elements found after multiple retries. Ensure the Google Meet meeting media is playing."
                )
              );
              return;
            }

            // Create combined audio stream
            return await audioService.createCombinedAudioStream(mediaElements);
          }).then(async (combinedStream: MediaStream | undefined) => {
            if (!combinedStream) {
              reject(new Error("[Google Meet BOT Error] Failed to create combined audio stream"));
              return;
            }
            // Initialize audio processor
            return await audioService.initializeAudioProcessor(combinedStream);
          }).then(async (processor: any) => {
            // Setup audio data processing
            audioService.setupAudioDataProcessor(async (audioData: Float32Array, sessionStartTime: number | null) => {
              // Only send after server ready (canonical Teams pattern)
              if (!whisperLiveService.isReady()) {
                // Skip sending until server is ready
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
                (window as any).logBot("Failed to send Google Meet audio data to WhisperLive");
              }
            });

            // Initialize WhisperLive WebSocket connection with simple reconnection wrapper
            const connectWhisper = async () => {
              try {
                await whisperLiveService.connectToWhisperLive(
                  botConfigData,
                  (data: any) => {
                const logFn = (window as any).logBot;
                // Reduce log spam: log only important status changes and completed transcript segments
                if (!data || typeof data !== 'object') {
                  return;
                }
                if (data["status"] === "ERROR") {
                  logFn(`Google Meet WebSocket Server Error: ${data["message"]}`);
                  return;
                }
                if (data["status"] === "WAIT") {
                  logFn(`Google Meet Server busy: ${data["message"]}`);
                  return;
                }
                if (!whisperLiveService.isReady() && data["status"] === "SERVER_READY") {
                  whisperLiveService.setServerReady(true);
                  logFn("Google Meet Server is ready.");
                  return;
                }
                if (data["language"]) {
                  if (!(window as any).__vexaLangLogged) {
                    (window as any).__vexaLangLogged = true;
                    logFn(`Google Meet Language detected: ${data["language"]}`);
                  }
                  // do not return; language can accompany segments
                }
                if (data["message"] === "DISCONNECT") {
                  logFn("Google Meet Server requested disconnect.");
                  whisperLiveService.close();
                  return;
                }
                // Log only completed transcript segments, with deduplication
                if (Array.isArray(data.segments)) {
                  const completedTexts = data.segments
                    .filter((s: any) => s && s.completed && s.text)
                    .map((s: any) => s.text as string);
                  if (completedTexts.length > 0) {
                    const transcriptKey = completedTexts.join(' ').trim();
                    if (transcriptKey && transcriptKey !== (window as any).__lastTranscript) {
                      (window as any).__lastTranscript = transcriptKey;
                      logFn(`Transcript: ${transcriptKey}`);
                    }
                  }
                }
                  },
                  (event: Event) => {
                    (window as any).logBot(`[Google Meet Failover] WebSocket error. This will trigger retry logic.`);
                  },
                  async (event: CloseEvent) => {
                    (window as any).logBot(`[Google Meet Failover] WebSocket connection closed. Code: ${event.code}, Reason: ${event.reason}. Attempting reconnect in 2s...`);
                    try { whisperLiveService.setServerReady(false); } catch {}
                    setTimeout(() => {
                      // Best-effort reconnect; BrowserWhisperLiveService stubborn mode should also help
                      connectWhisper().catch(() => {});
                    }, 2000);
                  }
                );
              } catch (e) {
                (window as any).logBot(`Google Meet connect error: ${(e as any)?.message || e}. Retrying in 2s...`);
                setTimeout(() => { connectWhisper().catch(() => {}); }, 2000);
              }
            };
            return await connectWhisper();
          }).then(() => {
            // Initialize Google-specific speaker detection (Teams-style with Google selectors)
            (window as any).logBot("Initializing Google Meet speaker detection...");

            const initializeGoogleSpeakerDetection = (whisperLiveService: any, audioService: any, botConfigData: any) => {
              const selectorsTyped = selectors as any;

              const speakingStates = new Map<string, string>();

              function getGoogleParticipantId(element: HTMLElement) {
                let id = element.getAttribute('data-participant-id');
                if (!id) {
                  const stableChild = element.querySelector('[jsinstance]') as HTMLElement | null;
                  if (stableChild) {
                    id = stableChild.getAttribute('jsinstance') || undefined as any;
                  }
                }
                if (!id) {
                  if (!(element as any).dataset.vexaGeneratedId) {
                    (element as any).dataset.vexaGeneratedId = 'gm-id-' + Math.random().toString(36).substr(2, 9);
                  }
                  id = (element as any).dataset.vexaGeneratedId;
                }
                return id as string;
              }

              function getGoogleParticipantName(participantElement: HTMLElement) {
                // Prefer explicit Meet name spans
                const notranslate = participantElement.querySelector('span.notranslate') as HTMLElement | null;
                if (notranslate && notranslate.textContent && notranslate.textContent.trim()) {
                  const t = notranslate.textContent.trim();
                  if (t.length > 1 && t.length < 50) return t;
                }

                // Try configured name selectors
                const nameSelectors: string[] = selectorsTyped.nameSelectors || [];
                for (const sel of nameSelectors) {
                  const el = participantElement.querySelector(sel) as HTMLElement | null;
                  if (el) {
                    let nameText = el.textContent || el.innerText || el.getAttribute('data-self-name') || el.getAttribute('aria-label') || '';
                    if (nameText) {
                      nameText = nameText.trim();
                      if (nameText && nameText.length > 1 && nameText.length < 50) return nameText;
                    }
                  }
                }

                // Fallbacks
                const selfName = participantElement.getAttribute('data-self-name');
                if (selfName && selfName.trim()) return selfName.trim();
                const idToDisplay = getGoogleParticipantId(participantElement);
                return `Google Participant (${idToDisplay})`;
              }

              function isVisible(el: HTMLElement): boolean {
                const cs = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                const ariaHidden = el.getAttribute('aria-hidden') === 'true';
                return (
                  rect.width > 0 &&
                  rect.height > 0 &&
                  cs.display !== 'none' &&
                  cs.visibility !== 'hidden' &&
                  cs.opacity !== '0' &&
                  !ariaHidden
                );
              }

              function hasSpeakingIndicator(container: HTMLElement): boolean {
                const indicators: string[] = selectorsTyped.speakingIndicators || [];
                for (const sel of indicators) {
                  const ind = container.querySelector(sel) as HTMLElement | null;
                  if (ind && isVisible(ind)) return true;
                }
                return false;
              }

              function inferSpeakingFromClasses(container: HTMLElement, mutatedClassList?: DOMTokenList): { speaking: boolean } {
                const speakingClasses: string[] = selectorsTyped.speakingClasses || [];
                const silenceClasses: string[] = selectorsTyped.silenceClasses || [];

                const classList = mutatedClassList || container.classList;
                const descendantSpeaking = speakingClasses.some(cls => container.querySelector('.' + cls));
                const hasSpeaking = speakingClasses.some(cls => classList.contains(cls)) || descendantSpeaking;
                const hasSilent = silenceClasses.some(cls => classList.contains(cls));
                if (hasSpeaking) return { speaking: true };
                if (hasSilent) return { speaking: false };
                return { speaking: false };
              }

              function sendGoogleSpeakerEvent(eventType: string, participantElement: HTMLElement) {
                const sessionStartTime = audioService.getSessionAudioStartTime();
                if (sessionStartTime === null) {
                  return;
                }
                const relativeTimestampMs = Date.now() - sessionStartTime;
                const participantId = getGoogleParticipantId(participantElement);
                const participantName = getGoogleParticipantName(participantElement);
                try {
                  whisperLiveService.sendSpeakerEvent(
                    eventType,
                    participantName,
                    participantId,
                    relativeTimestampMs,
                    botConfigData
                  );
                } catch {}
              }

              function logGoogleSpeakerEvent(participantElement: HTMLElement, mutatedClassList?: DOMTokenList) {
                const participantId = getGoogleParticipantId(participantElement);
                const participantName = getGoogleParticipantName(participantElement);
                const previousLogicalState = speakingStates.get(participantId) || 'silent';

                // Primary: indicators; Fallback: classes
                const indicatorSpeaking = hasSpeakingIndicator(participantElement);
                const classInference = inferSpeakingFromClasses(participantElement, mutatedClassList);
                const isCurrentlySpeaking = indicatorSpeaking || classInference.speaking;

                if (isCurrentlySpeaking) {
                  if (previousLogicalState !== 'speaking') {
                    (window as any).logBot(`🎤 [Google] SPEAKER_START: ${participantName} (ID: ${participantId})`);
                    sendGoogleSpeakerEvent('SPEAKER_START', participantElement);
                  }
                  speakingStates.set(participantId, 'speaking');
                } else {
                  if (previousLogicalState === 'speaking') {
                    (window as any).logBot(`🔇 [Google] SPEAKER_END: ${participantName} (ID: ${participantId})`);
                    sendGoogleSpeakerEvent('SPEAKER_END', participantElement);
                  }
                  speakingStates.set(participantId, 'silent');
                }
              }

              function observeGoogleParticipant(participantElement: HTMLElement) {
                const participantId = getGoogleParticipantId(participantElement);
                speakingStates.set(participantId, 'silent');

                // Initial scan
                logGoogleSpeakerEvent(participantElement);

                const callback = function(mutationsList: MutationRecord[]) {
                  for (const mutation of mutationsList) {
                    if (mutation.type === 'attributes' && mutation.attributeName === 'class') {
                      const targetElement = mutation.target as HTMLElement;
                      if (participantElement.contains(targetElement) || participantElement === targetElement) {
                        logGoogleSpeakerEvent(participantElement, targetElement.classList);
                      }
                    }
                  }
                };

                const observer = new MutationObserver(callback);
                observer.observe(participantElement, {
                  attributes: true,
                  attributeFilter: ['class'],
                  subtree: true
                });

                if (!(participantElement as any).dataset.vexaObserverAttached) {
                  (participantElement as any).dataset.vexaObserverAttached = 'true';
                }
              }

              function scanForAllGoogleParticipants() {
                const participantSelectors: string[] = selectorsTyped.participantSelectors || [];
                for (const sel of participantSelectors) {
                  document.querySelectorAll(sel).forEach((el) => {
                    const elh = el as HTMLElement;
                    if (!(elh as any).dataset.vexaObserverAttached) {
                      observeGoogleParticipant(elh);
                    }
                  });
                }
              }

              // Attempt to click People button to stabilize DOM if available
              try {
                const peopleSelectors: string[] = selectorsTyped.peopleButtonSelectors || [];
                for (const sel of peopleSelectors) {
                  const btn = document.querySelector(sel) as HTMLElement | null;
                  if (btn && isVisible(btn)) { btn.click(); break; }
                }
              } catch {}

              // Initialize
              scanForAllGoogleParticipants();

              // Polling fallback to catch speaking indicators not driven by class mutations
              const lastSpeakingById = new Map<string, boolean>();
              setInterval(() => {
                const participantSelectors: string[] = selectorsTyped.participantSelectors || [];
                const elements: HTMLElement[] = [];
                participantSelectors.forEach(sel => {
                  document.querySelectorAll(sel).forEach(el => elements.push(el as HTMLElement));
                });
                elements.forEach((container) => {
                  const id = getGoogleParticipantId(container);
                  const indicatorSpeaking = hasSpeakingIndicator(container) || inferSpeakingFromClasses(container).speaking;
                  const prev = lastSpeakingById.get(id) || false;
                  if (indicatorSpeaking && !prev) {
                    (window as any).logBot(`[Google Poll] SPEAKER_START ${getGoogleParticipantName(container)}`);
                    sendGoogleSpeakerEvent('SPEAKER_START', container);
                    lastSpeakingById.set(id, true);
                    speakingStates.set(id, 'speaking');
                  } else if (!indicatorSpeaking && prev) {
                    (window as any).logBot(`[Google Poll] SPEAKER_END ${getGoogleParticipantName(container)}`);
                    sendGoogleSpeakerEvent('SPEAKER_END', container);
                    lastSpeakingById.set(id, false);
                    speakingStates.set(id, 'silent');
                  } else if (!lastSpeakingById.has(id)) {
                    lastSpeakingById.set(id, indicatorSpeaking);
                  }
                });
              }, 500);
            };

            initializeGoogleSpeakerDetection(whisperLiveService, audioService, botConfigData);

            // Simple single-strategy participant extraction from main video area
            (window as any).logBot("Initializing simplified participant counting (main frame text scan)...");

            const extractParticipantsFromMain = (botName: string | undefined): string[] => {
              const participants: string[] = [];
              const mainElement = document.querySelector('main');
              if (mainElement) {
                const nameElements = mainElement.querySelectorAll('*');
                nameElements.forEach((el: Element) => {
                  const element = el as HTMLElement;
                  const text = (element.textContent || '').trim();
                  if (text && element.children.length === 0) {
                    // Basic length validation only (allow numbers, parentheses, etc.)
                    if ((text.length > 1 && text.length < 50) || (botName && text === botName)) {
                      participants.push(text);
                    }
                  }
                });
              }
              const tooltips = document.querySelectorAll('main [role="tooltip"]');
              tooltips.forEach((el: Element) => {
                const text = (el.textContent || '').trim();
                // Basic length validation only (allow numbers, parentheses, etc.)
                if (text && ((text.length > 1 && text.length < 50) || (botName && text === botName))) {
                  participants.push(text);
                }
              });
              return Array.from(new Set(participants));
            };

            (window as any).getGoogleMeetActiveParticipants = () => {
              const names = extractParticipantsFromMain((botConfigData as any)?.botName);
              (window as any).logBot(`🔍 [Google Meet Participants] ${JSON.stringify(names)}`);
              return names;
            };
            (window as any).getGoogleMeetActiveParticipantsCount = () => {
              return (window as any).getGoogleMeetActiveParticipants().length;
            };
            
            // Setup Google Meet meeting monitoring (browser context)
            const setupGoogleMeetingMonitoring = (botConfigData: any, audioService: any, whisperLiveService: any, resolve: any) => {
              (window as any).logBot("Setting up Google Meet meeting monitoring...");
              
              const leaveCfg = (botConfigData && (botConfigData as any).automaticLeave) || {};
              const startupAloneTimeoutSeconds = Number(leaveCfg.startupAloneTimeoutSeconds ?? (20 * 60));
              const everyoneLeftTimeoutSeconds = Number(leaveCfg.everyoneLeftTimeoutSeconds ?? 10);
              
              let aloneTime = 0;
              let lastParticipantCount = 0;
              let speakersIdentified = false;
              let hasEverHadMultipleParticipants = false;

              const checkInterval = setInterval(() => {
                // Check participant count using the comprehensive helper
                const currentParticipantCount = (window as any).getGoogleMeetActiveParticipantsCount ? (window as any).getGoogleMeetActiveParticipantsCount() : 0;
                
                if (currentParticipantCount !== lastParticipantCount) {
                  (window as any).logBot(`Participant check: Found ${currentParticipantCount} unique participants from central list.`);
                  lastParticipantCount = currentParticipantCount;
                  
                  // Track if we've ever had multiple participants
                  if (currentParticipantCount > 1) {
                    hasEverHadMultipleParticipants = true;
                    speakersIdentified = true; // Once we see multiple participants, we've identified speakers
                    (window as any).logBot("Speakers identified - switching to post-speaker monitoring mode");
                  }
                }

                if (currentParticipantCount <= 1) {
                  aloneTime++;
                  
                  // Determine timeout based on whether speakers have been identified
                  const currentTimeout = speakersIdentified ? everyoneLeftTimeoutSeconds : startupAloneTimeoutSeconds;
                  const timeoutDescription = speakersIdentified ? "post-speaker" : "startup";
                  
                  if (aloneTime >= currentTimeout) {
                    if (speakersIdentified) {
                      (window as any).logBot(`Google Meet meeting ended or bot has been alone for ${everyoneLeftTimeoutSeconds} seconds after speakers were identified. Stopping recorder...`);
                      clearInterval(checkInterval);
                      audioService.disconnect();
                      whisperLiveService.close();
                      reject(new Error("GOOGLE_MEET_BOT_LEFT_ALONE_TIMEOUT"));
                    } else {
                      (window as any).logBot(`Google Meet bot has been alone for ${startupAloneTimeoutSeconds/60} minutes during startup with no other participants. Stopping recorder...`);
                      clearInterval(checkInterval);
                      audioService.disconnect();
                      whisperLiveService.close();
                      reject(new Error("GOOGLE_MEET_BOT_STARTUP_ALONE_TIMEOUT"));
                    }
                  } else if (aloneTime > 0 && aloneTime % 10 === 0) { // Log every 10 seconds to avoid spam
                    if (speakersIdentified) {
                      (window as any).logBot(`Bot has been alone for ${aloneTime} seconds (${timeoutDescription} mode). Will leave in ${currentTimeout - aloneTime} more seconds.`);
                    } else {
                      const remainingMinutes = Math.floor((currentTimeout - aloneTime) / 60);
                      const remainingSeconds = (currentTimeout - aloneTime) % 60;
                      (window as any).logBot(`Bot has been alone for ${aloneTime} seconds during startup. Will leave in ${remainingMinutes}m ${remainingSeconds}s.`);
                    }
                  }
                } else {
                  aloneTime = 0; // Reset if others are present
                  if (hasEverHadMultipleParticipants && !speakersIdentified) {
                    speakersIdentified = true;
                    (window as any).logBot("Speakers identified - switching to post-speaker monitoring mode");
                  }
                }
              }, 1000);

              // Listen for page unload
              window.addEventListener("beforeunload", () => {
                (window as any).logBot("Page is unloading. Stopping recorder...");
                clearInterval(checkInterval);
                audioService.disconnect();
                whisperLiveService.close();
                resolve();
              });

              document.addEventListener("visibilitychange", () => {
                if (document.visibilityState === "hidden") {
                  (window as any).logBot("Document is hidden. Stopping recorder...");
                  clearInterval(checkInterval);
                  audioService.disconnect();
                  whisperLiveService.close();
                  resolve();
                }
              });
            };

            setupGoogleMeetingMonitoring(botConfigData, audioService, whisperLiveService, resolve);
          }).catch((err: any) => {
            reject(err);
          });

        } catch (error: any) {
          return reject(new Error("[Google Meet BOT Error] " + error.message));
        }
      });
    },
    { 
      botConfigData: botConfig, 
      whisperUrlForBrowser: whisperLiveUrl,
      selectors: {
        participantSelectors: googleParticipantSelectors,
        speakingClasses: googleSpeakingClassNames,
        silenceClasses: googleSilenceClassNames,
        containerSelectors: googleParticipantContainerSelectors,
        nameSelectors: googleNameSelectors,
        speakingIndicators: googleSpeakingIndicators,
        peopleButtonSelectors: googlePeopleButtonSelectors
      } as any
    }
  );
  
  // After page.evaluate finishes, cleanup services
  await whisperLiveService.cleanup();
}
