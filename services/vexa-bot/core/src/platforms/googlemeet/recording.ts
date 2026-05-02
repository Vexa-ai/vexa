import { Page } from "playwright";
import { log } from "../../utils";
import { BotConfig } from "../../types";
import { RecordingService } from "../../services/recording";
import { getSegmentPublisher } from "../../index";
import { ensureBrowserUtils } from "../../utils/injection";
import { MediaRecorderCapture, UnifiedRecordingPipeline } from "../../services/audio-pipeline";
import {
  googleParticipantSelectors,
  googleSpeakingClassNames,
  googleSilenceClassNames,
  googleParticipantContainerSelectors,
  googleNameSelectors,
  googleSpeakingIndicators,
  googlePeopleButtonSelectors
} from "./selectors";

// Pack U.2 (v0.10.6): module-level pipeline holder so the leave path
// (leaveGoogleMeet → stopGoogleRecording) can drive shutdown without
// reaching back through window globals like the old __vexaFlushRecordingBlob.
let pipeline: UnifiedRecordingPipeline | null = null;
let recordingService: RecordingService | null = null;

export async function startGoogleRecording(page: Page, botConfig: BotConfig): Promise<void> {
  log("Starting Google Meet recording");

  // (Segment publisher session-start re-alignment is owned by
  // UnifiedRecordingPipeline — it subscribes to source.on('started')
  // which fires on the first audio sample. Same hook for all 3 platforms.)

  const wantsAudioCapture =
    !!botConfig.recordingEnabled &&
    (!Array.isArray(botConfig.captureModes) || botConfig.captureModes.includes("audio"));
  const sessionUid = botConfig.connectionId || `gm-${Date.now()}`;

  // Pack U.2 (v0.10.6): unified audio pipeline. The bot encodes WebM/Opus
  // chunks via a browser-side MediaRecorder (BrowserMediaRecorderPipeline)
  // and uploads each chunk to meeting-api as it's produced; the master is
  // built server-side by recording_finalizer.py at bot_exit_callback. No
  // local-disk WAV scaffold, no __vexaSaveRecordingBlob full-blob path —
  // those were dead under chunked upload.
  if (wantsAudioCapture) {
    if (!botConfig.recordingUploadUrl || !botConfig.token) {
      log("[Google Recording] recordingUploadUrl or token missing — skipping audio capture");
    } else {
      recordingService = new RecordingService(botConfig.meeting_id, sessionUid);

      // CRITICAL: inject browser-utils bundle BEFORE constructing the
      // MediaRecorderCapture pipeline. The pipeline's startBrowserCapture
      // callback runs page.evaluate which accesses
      // (window as any).VexaBrowserUtils.BrowserAudioService /
      // BrowserMediaRecorderPipeline. If ensureBrowserUtils hasn't run
      // yet, those classes are undefined → page.evaluate throws inside
      // the async callback, the error is silently absorbed by the
      // promise chain, and the bot runs to completion having captured
      // ZERO audio chunks (#regression: Pack U.2 ordering bug; classifier
      // then fires STOPPED_WITH_NO_AUDIO → meeting.status=failed).
      // The ensureBrowserUtils call MUST stay before pipeline.start().
      await ensureBrowserUtils(page, require('path').join(__dirname, '../../browser-utils.global.js'));

      // (Note: __vexaRecordingStarted is now exposed inside MediaRecorderCapture
      // and the publisher.resetSessionStart() wiring is owned by
      // UnifiedRecordingPipeline — same hook for all 3 platforms via the
      // AudioCaptureSource 'started' event. No per-platform handler needed.)

      const audioCapture = new MediaRecorderCapture({
        page,
        botConfig,
        sessionUid,
        platform: "gmeet",
        timesliceMs: 30000,
        startBrowserCapture: async (page, timesliceMs) => {
          await page.evaluate(async ({ timesliceMs }) => {
            const u = (window as any).VexaBrowserUtils;
            (window as any).logBot(`[Google Recording] Browser utils available: ${Object.keys(u || {}).join(', ')}`);

            const audioService = new u.BrowserAudioService({
              targetSampleRate: 16000,
              bufferSize: 4096,
              inputChannels: 1,
              outputChannels: 1,
            });
            (window as any).__vexaAudioService = audioService;

            // Wait for media elements to initialize after admission.
            (window as any).logBot("[Google Recording] Waiting 2s for media elements after admission...");
            await new Promise((resolve) => setTimeout(resolve, 2000));

            // 10 retries × 3s delay = up to 30s wait time.
            const mediaElements: HTMLMediaElement[] = await audioService.findMediaElements(10, 3000);
            if (mediaElements.length === 0) {
              (window as any).logBot(
                "[Google Meet BOT Warning] No active media elements found after retries; " +
                "continuing in degraded monitoring mode (session remains active)."
              );
              (window as any).__vexaDegradedNoMedia = true;
              return;
            }

            const combinedStream: MediaStream = await audioService.createCombinedAudioStream(mediaElements);

            // Spin up the unified browser-side MediaRecorder pipeline.
            const pipeline = new u.BrowserMediaRecorderPipeline({
              stream: combinedStream,
              timesliceMs,
              chunkCallback: (window as any).__vexaSaveRecordingChunk,
            });
            (window as any).__vexaMediaRecorderPipeline = pipeline;
            // Keep __vexaMediaRecorder pointing at the underlying MediaRecorder
            // for any legacy code that pokes at it directly (e.g. visibility
            // change handlers in the speaker-detection page.evaluate below).
            await pipeline.start();
            (window as any).__vexaMediaRecorder = pipeline.getMediaRecorder();
            // Signal Node.js that recording started — re-aligns segment timestamps
            (window as any).__vexaRecordingStarted?.();

            // Initialize the audio data processor for the
            // alone-cross-validation hook (Layer 2 of #285). The per-speaker
            // transcription pipeline runs on the Node side; this hook only
            // needs RMS energy to detect speech activity.
            const processor = await audioService.initializeAudioProcessor(combinedStream);
            if (processor) {
              (window as any).__vexaLastAudioActivityTs = 0;
              const AUDIO_ACTIVITY_THRESHOLD = 0.005; // RMS above silence baseline
              audioService.setupAudioDataProcessor((audioData: Float32Array) => {
                if (!audioData || audioData.length === 0) return;
                try {
                  let maxAbs = 0;
                  // Cheap scan: 1-of-32 sample stride is plenty to detect non-silence
                  for (let i = 0; i < audioData.length; i += 32) {
                    const v = Math.abs(audioData[i]);
                    if (v > maxAbs) maxAbs = v;
                    if (maxAbs > AUDIO_ACTIVITY_THRESHOLD) break;
                  }
                  if (maxAbs > AUDIO_ACTIVITY_THRESHOLD) {
                    (window as any).__vexaLastAudioActivityTs = Date.now();
                  }
                } catch {}
              });
            }
          }, { timesliceMs });
        },
        stopBrowserCapture: async (page) => {
          await page.evaluate(async () => {
            const p = (window as any).__vexaMediaRecorderPipeline;
            if (p && typeof p.stop === "function") {
              await p.stop();
            }
          });
        },
      });

      pipeline = new UnifiedRecordingPipeline({
        source: audioCapture,
        recordingService,
        uploadUrl: botConfig.recordingUploadUrl,
        token: botConfig.token,
        platform: "gmeet",
      });
      await pipeline.start();
      log("[Google Recording] Unified recording pipeline started (MediaRecorder → chunked upload)");
    }
  } else {
    log("[Google Recording] Audio capture disabled by config.");
    // Even with capture disabled, speaker detection still needs the
    // browser-utils bundle for DOM observation. Ensure it's loaded.
    await ensureBrowserUtils(page, require('path').join(__dirname, '../../browser-utils.global.js'));
  }

  // Speaker detection + meeting monitoring: this is the platform-specific DOM
  // logic that stays. It's structurally independent of audio capture (the
  // pipeline handles audio; this evaluator handles DOM observation +
  // alone-time monitoring).
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
      (window as any).__vexaBotConfig = botConfigData;

      await new Promise<void>((resolve, reject) => {
        try {
          (window as any).logBot("Starting Google Meet speaker detection + monitoring.");

          const audioService = (window as any).__vexaAudioService;
          // No audioService means audio capture wasn't started (recordingEnabled=false
          // or upload URL missing); we still want speaker observation, but with no
          // session-start anchor for events we can't accumulate them.
          const degradedNoMedia = !!(window as any).__vexaDegradedNoMedia;

          // Initialize Google-specific speaker detection (Teams-style with Google selectors)
          if (!degradedNoMedia) {
            (window as any).logBot("Initializing Google Meet speaker detection...");
          }

          const initializeGoogleSpeakerDetection = (audioService: any, botConfigData: any) => {
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

              // Helper: reject junk names (fallback-generated IDs, not real names)
              const isJunkName = (name: string): boolean => {
                return /^Google Participant \(/.test(name) ||
                       /spaces\//.test(name) ||
                       /devices\//.test(name);
              };

              // Fallbacks
              const selfName = participantElement.getAttribute('data-self-name');
              if (selfName && selfName.trim() && !isJunkName(selfName.trim())) return selfName.trim();

              // aria-label on the container or any descendant (catches Spaces/Chat device participants)
              const ariaLabel = participantElement.getAttribute('aria-label');
              if (ariaLabel && ariaLabel.trim().length > 1 && ariaLabel.trim().length < 50 && !isJunkName(ariaLabel.trim())) return ariaLabel.trim();
              const ariaChild = participantElement.querySelector('[aria-label]') as HTMLElement | null;
              if (ariaChild) {
                const childLabel = ariaChild.getAttribute('aria-label')?.trim();
                if (childLabel && childLabel.length > 1 && childLabel.length < 50 && !isJunkName(childLabel)) return childLabel;
              }

              // data-tooltip on any descendant
              const tooltipEl = participantElement.querySelector('[data-tooltip]') as HTMLElement | null;
              if (tooltipEl) {
                const tooltip = tooltipEl.getAttribute('data-tooltip')?.trim();
                if (tooltip && tooltip.length > 1 && tooltip.length < 50 && !isJunkName(tooltip)) return tooltip;
              }

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
              const sessionStartTime = audioService?.getSessionAudioStartTime?.() ?? null;
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

            // Debug: log all class mutations to discover current Google Meet speaking classes
            let classMutationCount = 0;
            function debugClassMutation(participantElement: HTMLElement, mutatedClassList?: DOMTokenList) {
              classMutationCount++;
              // Log first 20 mutations and then every 50th to avoid spam
              if (classMutationCount <= 20 || classMutationCount % 50 === 0) {
                const id = getGoogleParticipantId(participantElement);
                const name = getGoogleParticipantName(participantElement);
                const classes = mutatedClassList ? Array.from(mutatedClassList).join(' ') : '(no classList)';
                (window as any).logBot(`[SpeakerDebug] #${classMutationCount} ${name} (${id}): classes=[${classes}]`);
              }
            }

            function logGoogleSpeakerEvent(participantElement: HTMLElement, mutatedClassList?: DOMTokenList) {
              const participantId = getGoogleParticipantId(participantElement);
              const participantName = getGoogleParticipantName(participantElement);
              const previousLogicalState = speakingStates.get(participantId) || 'silent';

              // Debug: log class mutations
              debugClassMutation(participantElement, mutatedClassList);

              // Primary: indicators; Fallback: classes
              const indicatorSpeaking = hasSpeakingIndicator(participantElement);
              const classInference = inferSpeakingFromClasses(participantElement, mutatedClassList);
              const isCurrentlySpeaking = indicatorSpeaking || classInference.speaking;

              if (isCurrentlySpeaking) {
                if (previousLogicalState !== 'speaking') {
                  (window as any).logBot(`[SpeakerDebug] SPEAKING START: ${participantName} (indicator=${indicatorSpeaking}, classInference=${classInference.speaking})`);
                  sendGoogleSpeakerEvent('SPEAKER_START', participantElement);
                }
                speakingStates.set(participantId, 'speaking');
              } else {
                if (previousLogicalState === 'speaking') {
                  (window as any).logBot(`[SpeakerDebug] SPEAKING END: ${participantName}`);
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
              // Debug: dump participant tile structure on first scan
              (window as any).logBot(`[SpeakerDebug] Scanning for participants with selectors: ${participantSelectors.join(', ')}`);
              let foundCount = 0;
              for (const sel of participantSelectors) {
                document.querySelectorAll(sel).forEach((el) => {
                  foundCount++;
                  const elh = el as HTMLElement;
                  const outerClasses = elh.className;
                  const childClasses = Array.from(elh.querySelectorAll('*')).slice(0, 5).map(c => (c as HTMLElement).className).filter(Boolean);
                  (window as any).logBot(`[SpeakerDebug] Participant tile (${sel}): classes=[${outerClasses}], children=[${childClasses.join(' | ')}], innerHTML=${elh.innerHTML.substring(0, 200)}`);
                });
              }
              (window as any).logBot(`[SpeakerDebug] Found ${foundCount} participant tiles total`);
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
                  // Poll speaker start — debug level
                  sendGoogleSpeakerEvent('SPEAKER_START', container);
                  lastSpeakingById.set(id, true);
                  speakingStates.set(id, 'speaking');
                } else if (!indicatorSpeaking && prev) {
                  // Poll speaker end — debug level
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

          // v0.10.5 #285 Layer 1 — multi-selector fallback for participant counting.
          // Single-selector `[data-participant-id]` is fragile to GMeet UI changes;
          // any rename/relocation of that attribute takes the count to 0 globally
          // and triggers false LEFT_ALONE_TIMEOUT. Add parallel selectors so a single
          // DOM-shape change doesn't take the count to 0:
          //   - `[role="region"][aria-label*="articipant"]` — tile region heuristic
          //     (matches "participant" / "Participant" / "participants")
          //   - `[data-self-name]` — bot's own self-tile (always present once admitted)
          const countParticipantTiles = (): number => {
            const ids = new Set<string>();
            // Primary: data-participant-id tiles
            document.querySelectorAll('[data-participant-id]').forEach((el: Element) => {
              const id = el.getAttribute('data-participant-id');
              if (id) ids.add(id);
            });
            // Fallback 1: aria-labelled participant regions
            try {
              document.querySelectorAll('[role="region"][aria-label*="articipant"]').forEach((el: Element, idx: number) => {
                const label = el.getAttribute('aria-label') || `region-${idx}`;
                ids.add(`region-fallback:${label}`);
              });
            } catch {}
            // Fallback 2: self-name marker — always present after admission
            try {
              document.querySelectorAll('[data-self-name]').forEach((el: Element) => {
                const name = el.getAttribute('data-self-name') || 'self';
                ids.add(`self-fallback:${name}`);
              });
            } catch {}
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
            // Only log participant count changes, not every poll
            if (tileCount !== lastKnownParticipantCount) {
              (window as any).logBot(`🔍 [Google Meet Participants] ${tileCount} tiles, inMeeting=${inMeeting}`);
            }
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

            const stopMonitoring = (
              reason: string,
              finish: () => void
            ) => {
              if (monitoringStopped) return;
              monitoringStopped = true;
              clearInterval(checkInterval);
              try {
                if (audioService && typeof audioService.disconnect === "function") {
                  audioService.disconnect();
                }
              } catch (err: any) {
                (window as any).logBot?.(
                  `[Google Recording] audioService.disconnect error during shutdown (${reason}): ${err?.message || err}`
                );
              }
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
                // v0.10.5 #285 Layer 2 — audio cross-validate before incrementing
                // aloneTime. If audio activity within last 120s, the meeting has
                // speakers regardless of what the DOM count says (DOM heuristic
                // is single-selector fragile; audio is independent ground truth).
                // Two independent signals can't both fail in the same way without
                // something architecturally wrong.
                const lastAudioMs = (window as any).__vexaLastAudioActivityTs || 0;
                const audioRecentlyActive = lastAudioMs > 0 && (Date.now() - lastAudioMs) < 120000;
                if (audioRecentlyActive) {
                  if (aloneTime > 0) {
                    (window as any).logBot(
                      `🎤 [Google Meet Cross-Validate] DOM count=${currentParticipantCount} but audio activity ${Math.round((Date.now() - lastAudioMs)/1000)}s ago — refusing to count alone-time (false-LEFT_ALONE guard)`
                    );
                  }
                  aloneTime = 0;
                  return; // skip alone-time block entirely this tick
                }
                aloneTime++;

                // Determine timeout based on whether speakers have been identified
                const currentTimeout = speakersIdentified ? everyoneLeftTimeoutSeconds : startupAloneTimeoutSeconds;
                const timeoutDescription = speakersIdentified ? "post-speaker" : "startup";

                if (aloneTime >= currentTimeout) {
                  if (speakersIdentified) {
                    (window as any).logBot(`Google Meet meeting ended or bot has been alone for ${everyoneLeftTimeoutSeconds} seconds after speakers were identified. Stopping recorder...`);
                    stopMonitoring("left_alone_timeout", () =>
                      reject(new Error("GOOGLE_MEET_BOT_LEFT_ALONE_TIMEOUT"))
                    );
                  } else {
                    (window as any).logBot(`Google Meet bot has been alone for ${startupAloneTimeoutSeconds/60} minutes during startup with no other participants. Stopping recorder...`);
                    stopMonitoring("startup_alone_timeout", () =>
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
              stopMonitoring("beforeunload", () => resolve());
            });

            document.addEventListener("visibilitychange", () => {
              if (document.visibilityState === "hidden") {
                (window as any).logBot("Document is hidden. Stopping recorder...");
                stopMonitoring("visibility_hidden", () => resolve());
              }
            });
          };

          setupGoogleMeetingMonitoring(botConfigData, audioService, resolve);
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

/**
 * Stop the unified recording pipeline. Called from leaveGoogleMeet before the
 * UI leave + process shutdown, replacing the old __vexaFlushRecordingBlob
 * browser-side fn. Drains the upload queue (including the final isFinal=true
 * chunk) so meeting-api flips Recording.status to COMPLETED before the bot
 * exits.
 */
export async function stopGoogleRecording(): Promise<void> {
  if (!pipeline) {
    log("[Google Recording] stopGoogleRecording: no active pipeline");
    return;
  }
  log("[Google Recording] Stopping unified pipeline (drain final chunk)");
  try {
    await pipeline.stop();
  } catch (err: any) {
    log(`[Google Recording] pipeline.stop() error: ${err?.message || err}`);
  }
  pipeline = null;
  recordingService = null;
}

export function getGoogleRecordingService(): RecordingService | null {
  return recordingService;
}
