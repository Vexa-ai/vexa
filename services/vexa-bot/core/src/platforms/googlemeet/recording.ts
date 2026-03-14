import { Page } from "playwright";
import { log } from "../../utils";
import { BotConfig } from "../../types";
import { RecordingService } from "../../services/recording";
import { setActiveRecordingService } from "../../index";
import { ensureBrowserUtils } from "../../utils/injection";
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
  log("Starting Google Meet recording");

  const wantsAudioCapture =
    !!botConfig.recordingEnabled &&
    (!Array.isArray(botConfig.captureModes) || botConfig.captureModes.includes("audio"));
  const sessionUid = botConfig.connectionId || `gm-${Date.now()}`;
  let recordingService: RecordingService | null = null;

  if (wantsAudioCapture) {
    recordingService = new RecordingService(botConfig.meeting_id, sessionUid);
    setActiveRecordingService(recordingService);

    await page.exposeFunction("__vexaSaveRecordingBlob", async (payload: { base64: string; mimeType?: string }) => {
      try {
        if (!recordingService) {
          log("[Google Recording] Recording service not initialized; dropping blob.");
          return false;
        }

        const mimeType = (payload?.mimeType || "").toLowerCase();
        let format = "webm";
        if (mimeType.includes("wav")) format = "wav";
        else if (mimeType.includes("ogg")) format = "ogg";
        else if (mimeType.includes("mp4") || mimeType.includes("m4a")) format = "m4a";

        const blobBuffer = Buffer.from(payload.base64 || "", "base64");
        if (!blobBuffer.length) {
          log("[Google Recording] Received empty audio blob.");
          return false;
        }

        await recordingService.writeBlob(blobBuffer, format);
        log(`[Google Recording] Saved browser audio blob (${blobBuffer.length} bytes, ${format}).`);
        return true;
      } catch (error: any) {
        log(`[Google Recording] Failed to persist browser blob: ${error?.message || String(error)}`);
        return false;
      }
    });
  } else {
    log("[Google Recording] Audio capture disabled by config.");
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
        peopleButtonSelectors: string[];
      };
    }) => {
      const { botConfigData, selectors } = pageArgs;

      // Use browser utility classes from the global bundle
      const browserUtils = (window as any).VexaBrowserUtils;
      (window as any).logBot(`Browser utils available: ${Object.keys(browserUtils || {}).join(', ')}`);

      const audioService = new browserUtils.BrowserAudioService({
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
                (window as any).logBot?.(`[Google Recording] No media chunks to flush (${reason}).`);
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
                  `[Google Recording] Flushed ${bytes.length} bytes (${blob.type || mimeType}) on ${reason}.`
                );
              } else {
                (window as any).logBot?.("[Google Recording] Node blob sink is not available.");
              }
            } catch (err: any) {
              (window as any).logBot?.(
                `[Google Recording] Failed to flush blob: ${err?.message || err}`
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
                // Recorder may already be stopping; resolve after a short delay.
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
            `[Google Recording] Unexpected flush error: ${err?.message || err}`
          );
        }
      };

      (window as any).__vexaFlushRecordingBlob = flushBrowserRecordingBlob;

      await new Promise<void>((resolve, reject) => {
        try {
          (window as any).logBot("Starting Google Meet recording process with new services.");
          
          // Wait a bit for media elements to initialize after admission, then start the chain
          (async () => {
            let degradedNoMedia = false;
            // Wait 2 seconds for media elements to initialize after admission
            (window as any).logBot("Waiting 2 seconds for media elements to initialize after admission...");
            await new Promise(resolve => setTimeout(resolve, 2000));
            
            // Find and create combined audio stream with enhanced retry logic
            // Use 10 retries with 3s delay = 30s total wait time
            audioService.findMediaElements(10, 3000).then(async (mediaElements: HTMLMediaElement[]) => {
            if (mediaElements.length === 0) {
              degradedNoMedia = true;
              (window as any).logBot(
                "[Google Meet BOT Warning] No active media elements found after retries; " +
                "continuing in degraded monitoring mode (session remains active)."
              );
              return undefined;
            }

            // Create combined audio stream
            return await audioService.createCombinedAudioStream(mediaElements);
          }).then(async (combinedStream: MediaStream | undefined) => {
            if (!combinedStream) {
              if (!degradedNoMedia) {
                reject(new Error("[Google Meet BOT Error] Failed to create combined audio stream"));
                return;
              }
              return null;
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
                  `[Google Recording] MediaRecorder started (${recorder.mimeType || mimeType || "default"}).`
                );
              } catch (err: any) {
                (window as any).logBot?.(
                  `[Google Recording] Failed to start MediaRecorder: ${err?.message || err}`
                );
              }
            }

            // Initialize audio processor
            return await audioService.initializeAudioProcessor(combinedStream);
          }).then(async (processor: any) => {
            if (!processor) {
              return null;
            }
            // Setup audio data processing
            // Audio data processor — no-op now; per-speaker pipeline handles transcription
            audioService.setupAudioDataProcessor(async (_audioData: Float32Array, _sessionStartTime: number | null) => {
              // Per-speaker pipeline (speaker-streams.ts) handles transcription.
              // This processor is kept for MediaRecorder / recording only.
            });

            return null;
          }).then(() => {
            // Initialize Google-specific speaker detection (Teams-style with Google selectors)
            if (!degradedNoMedia) {
              (window as any).logBot("Initializing Google Meet speaker detection...");
            }

            const initializeGoogleSpeakerDetection = (audioService: any, botConfigData: any) => {
              const selectorsTyped = selectors as any;

              const speakingStates = new Map<string, string>();
              function hashStr(s: string): string {
                // small non-crypto hash to avoid logging PII
                let h = 5381;
                for (let i = 0; i < s.length; i++) h = ((h << 5) + h) ^ s.charCodeAt(i);
                return (h >>> 0).toString(16).slice(0, 8);
              }

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
                // Accumulate for persistence (direct bot accumulation)
                (window as any).__vexaSpeakerEvents = (window as any).__vexaSpeakerEvents || [];
                (window as any).__vexaSpeakerEvents.push({
                  event_type: eventType,
                  participant_name: participantName,
                  participant_id: participantId,
                  relative_timestamp_ms: relativeTimestampMs,
                });
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

              // Expose participant name lookup to Node (used by speaker-identity.ts)
              // Returns a map of all known participant names from DOM tiles,
              // keyed by participant-id, plus a list of currently-speaking names.
              (window as any).__vexaGetAllParticipantNames = (): { names: Record<string, string>; speaking: string[] } => {
                const names: Record<string, string> = {};
                const speaking: string[] = [];
                const participantSelectors: string[] = selectorsTyped.participantSelectors || [];
                const seen = new Set<string>();
                participantSelectors.forEach(sel => {
                  document.querySelectorAll(sel).forEach(el => {
                    const elh = el as HTMLElement;
                    const id = getGoogleParticipantId(elh);
                    if (seen.has(id)) return;
                    seen.add(id);
                    const name = getGoogleParticipantName(elh);
                    names[id] = name;
                    if (speakingStates.get(id) === 'speaking') {
                      speaking.push(name);
                    }
                  });
                });
                return { names, speaking };
              };

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

            if (!degradedNoMedia) {
              initializeGoogleSpeakerDetection(audioService, botConfigData);
            }

            // Participant counting: uses data-participant-id tiles, but falls back to
            // "Leave call" button visibility to avoid false-positive "alone" during screen share.
            // Google Meet removes participant tiles from the DOM during presentation mode,
            // but the "Leave call" button remains visible as long as the bot is in the meeting.
            (window as any).logBot("Initializing participant counting (data-participant-id + leave-button fallback)...");

            let lastKnownParticipantCount = 0;

            const countParticipantTiles = (): number => {
              const participantElements = document.querySelectorAll('[data-participant-id]');
              const ids = new Set<string>();
              participantElements.forEach((el: Element) => {
                const id = el.getAttribute('data-participant-id');
                if (id) ids.add(id);
              });
              return ids.size;
            };

            const isBotStillInMeeting = (): boolean => {
              // "Leave call" button is the most reliable signal — it's always visible while in a meeting
              const leaveBtn = document.querySelector('button[aria-label*="Leave call"]');
              return leaveBtn !== null;
            };

            (window as any).getGoogleMeetActiveParticipants = () => {
              const tileCount = countParticipantTiles();
              const inMeeting = isBotStillInMeeting();
              // If tiles show 0 but we're still in the meeting (e.g. screen share mode),
              // keep the last known count (minimum 2) to avoid false "alone" triggers
              if (tileCount === 0 && inMeeting && lastKnownParticipantCount > 1) {
                (window as any).logBot(`🔍 [Google Meet Participants] 0 tiles but Leave button present — keeping last count ${lastKnownParticipantCount} (screen share mode)`);
                return new Array(lastKnownParticipantCount).fill('placeholder');
              }
              if (tileCount > 0) {
                lastKnownParticipantCount = tileCount;
              }
              (window as any).logBot(`🔍 [Google Meet Participants] ${tileCount} tiles, inMeeting=${inMeeting}`);
              return new Array(tileCount).fill('placeholder');
            };
            (window as any).getGoogleMeetActiveParticipantsCount = () => {
              return (window as any).getGoogleMeetActiveParticipants().length;
            };
            
            // Setup Google Meet meeting monitoring (browser context)
            const setupGoogleMeetingMonitoring = (botConfigData: any, audioService: any, resolve: any) => {
              (window as any).logBot("Setting up Google Meet meeting monitoring...");
              
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
                    `[Google Recording] Flush error during shutdown (${reason}): ${flushErr?.message || flushErr}`
                  );
                }
                audioService.disconnect();
                finish();
              };

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
                      void stopWithFlush("left_alone_timeout", () =>
                        reject(new Error("GOOGLE_MEET_BOT_LEFT_ALONE_TIMEOUT"))
                      );
                    } else {
                      (window as any).logBot(`Google Meet bot has been alone for ${startupAloneTimeoutSeconds/60} minutes during startup with no other participants. Stopping recorder...`);
                      void stopWithFlush("startup_alone_timeout", () =>
                        reject(new Error("GOOGLE_MEET_BOT_STARTUP_ALONE_TIMEOUT"))
                      );
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
                void stopWithFlush("beforeunload", () => resolve());
              });

              document.addEventListener("visibilitychange", () => {
                if (document.visibilityState === "hidden") {
                  (window as any).logBot("Document is hidden. Stopping recorder...");
                  void stopWithFlush("visibility_hidden", () => resolve());
                }
              });
            };

            setupGoogleMeetingMonitoring(botConfigData, audioService, resolve);
          }).catch((err: any) => {
            reject(err);
          });
          })(); // Close async IIFE

        } catch (error: any) {
          return reject(new Error("[Google Meet BOT Error] " + error.message));
        }
      });

    },
    {
      botConfigData: botConfig,
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
}
