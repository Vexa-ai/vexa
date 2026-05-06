import { Page } from "playwright";
import { log } from "../../utils";
import { BotConfig } from "../../types";
import { RecordingService } from "../../services/recording";
import { getSegmentPublisher } from "../../index";
import { ensureBrowserUtils } from "../../utils/injection";
import { MediaRecorderCapture, UnifiedRecordingPipeline } from "../../services/audio-pipeline";
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
  teamsMeetingContainerSelectors,
  teamsCaptionSelectors
} from "./selectors";

// Pack U.3 (v0.10.6): module-level pipeline holder so the leave path
// (leaveMicrosoftTeams → stopTeamsRecording) can drive shutdown without
// reaching back through window globals like the old __vexaFlushRecordingBlob.
let pipeline: UnifiedRecordingPipeline | null = null;
let recordingService: RecordingService | null = null;

// Modified to use new services - Teams recording functionality
export async function startTeamsRecording(page: Page, botConfig: BotConfig): Promise<void> {
  log("Starting Teams recording");

  // (Segment publisher session-start re-alignment is owned by
  // UnifiedRecordingPipeline — same hook for all 3 platforms via the
  // AudioCaptureSource 'started' event.)

  const wantsAudioCapture =
    !!botConfig.recordingEnabled &&
    (!Array.isArray(botConfig.captureModes) || botConfig.captureModes.includes("audio"));
  const sessionUid = botConfig.connectionId || `teams-${Date.now()}`;

  // Pack U.3 (v0.10.6): unified audio pipeline. The bot encodes WebM/Opus
  // chunks via a browser-side MediaRecorder (BrowserMediaRecorderPipeline)
  // and uploads each chunk to meeting-api as it's produced; the master is
  // built server-side by recording_finalizer.py at bot_exit_callback. No
  // local-disk WAV scaffold, no __vexaSaveRecordingBlob full-blob path —
  // those were dead under chunked upload.
  if (wantsAudioCapture) {
    if (!botConfig.recordingUploadUrl || !botConfig.token) {
      log("[Teams Recording] recordingUploadUrl or token missing — skipping audio capture");
    } else {
      recordingService = new RecordingService(botConfig.meeting_id, sessionUid);

      // CRITICAL: inject browser-utils bundle BEFORE constructing the
      // MediaRecorderCapture pipeline. The pipeline's startBrowserCapture
      // callback runs page.evaluate which accesses window.VexaBrowserUtils.
      // If ensureBrowserUtils hasn't run yet, those classes are undefined →
      // page.evaluate throws inside the async callback, the error is silently
      // absorbed, and the bot runs to completion having captured ZERO audio
      // chunks (#regression: Pack U.3 ordering bug; classifier then fires
      // STOPPED_WITH_NO_AUDIO → meeting.status=failed).
      // Mirrors the GMeet fix in googlemeet/recording.ts.
      await ensureBrowserUtils(page, require('path').join(__dirname, '../../browser-utils.global.js'));

      // (Note: __vexaRecordingStarted is now exposed inside MediaRecorderCapture
      // and publisher.resetSessionStart() is owned by UnifiedRecordingPipeline —
      // same hook for all 3 platforms via the AudioCaptureSource 'started' event.)

      const audioCapture = new MediaRecorderCapture({
        page,
        botConfig,
        sessionUid,
        platform: "teams",
        timesliceMs: 30000,
        startBrowserCapture: async (page, timesliceMs) => {
          await page.evaluate(async ({ timesliceMs }) => {
            const u = (window as any).VexaBrowserUtils;
            (window as any).logBot(`[Teams Recording] Browser utils available: ${Object.keys(u || {}).join(', ')}`);

            const audioService = new u.BrowserAudioService({
              targetSampleRate: 16000,
              bufferSize: 4096,
              inputChannels: 1,
              outputChannels: 1,
            });
            (window as any).__vexaAudioService = audioService;

            // 10 retries × 3s delay = up to 30s wait time.
            const mediaElements: HTMLMediaElement[] = await audioService.findMediaElements(10, 3000);
            if (mediaElements.length === 0) {
              (window as any).logBot(
                "[Teams BOT Warning] No active media elements found after retries; " +
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
            // for any legacy code that pokes at it directly.
            await pipeline.start();
            (window as any).__vexaMediaRecorder = pipeline.getMediaRecorder();
            // Signal Node.js that recording started — re-aligns segment timestamps
            (window as any).__vexaRecordingStarted?.();

            // Initialize the audio data processor for the alone-cross-validation
            // hook (mirrors GMeet pattern). The per-speaker transcription
            // pipeline runs separately; this hook only needs RMS energy to
            // detect speech activity.
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
        platform: "teams",
      });
      await pipeline.start();
      log("[Teams Recording] Unified recording pipeline started (MediaRecorder → chunked upload)");
    }
  } else {
    log("[Teams Recording] Audio capture disabled by config.");
    // Speaker detection still needs the browser-utils bundle for DOM observation.
    await ensureBrowserUtils(page, require('path').join(__dirname, '../../browser-utils.global.js'));
  }

  // Speaker detection + meeting monitoring + caption-driven per-speaker routing:
  // platform-specific DOM logic that stays. It's structurally independent of
  // audio capture (the pipeline handles audio; this evaluator handles DOM
  // observation + caption polling + alone-time monitoring).
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
        captionSelectors: {
          rendererWrapper: string;
          captionItem: string;
          authorName: string;
          captionText: string;
          virtualListContent: string;
        };
      };
    }) => {
      const { botConfigData, selectors } = pageArgs;
      const selectorsTyped = selectors as any;

      (window as any).__vexaBotConfig = botConfigData;

      await new Promise<void>((resolve, reject) => {
        try {
          (window as any).logBot("Starting Teams speaker detection + monitoring.");

          const audioService = (window as any).__vexaAudioService;
          // No audioService means audio capture wasn't started (recordingEnabled=false
          // or upload URL missing); we still want speaker observation, but with no
          // session-start anchor for events we can't accumulate them.
          const degradedNoMedia = !!(window as any).__vexaDegradedNoMedia;

          // Initialize Teams-specific speaker detection (browser context)
          if (!degradedNoMedia) {
            (window as any).logBot("Initializing Teams speaker detection...");
          }

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
                // data-acc-element-id is the most stable Teams id attribute
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
                const forbiddenSubstrings = [
                  "more_vert", "mic_off", "mic", "videocam", "videocam_off",
                  "present_to_all", "devices", "speaker", "speakers", "microphone",
                  "camera", "camera_off", "share", "chat", "participant", "user"
                ];
                for (const selector of nameSelectors) {
                  const nameElement = element.querySelector(selector) as HTMLElement;
                  if (!nameElement) continue;
                  let nameText = nameElement.textContent ||
                                 nameElement.innerText ||
                                 nameElement.getAttribute('title') ||
                                 nameElement.getAttribute('aria-label');
                  if (!nameText || !nameText.trim()) continue;
                  nameText = nameText.trim();
                  if (forbiddenSubstrings.some(sub => nameText!.toLowerCase().includes(sub.toLowerCase()))) continue;
                  if (nameText.length > 1 && nameText.length < 50) return nameText;
                }
                const ariaLabel = element.getAttribute('aria-label');
                if (ariaLabel && ariaLabel.includes('name')) {
                  const nameMatch = ariaLabel.match(/name[:\s]+([^,]+)/i);
                  if (nameMatch && nameMatch[1]) {
                    const nameText = nameMatch[1].trim();
                    if (nameText.length > 1 && nameText.length < 50) return nameText;
                  }
                }
                return `Teams Participant (${this.extractId(element)})`;
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
                    this.state.set(participantId, { state: 'unknown', hasSignal: false, lastChangeTime: now, lastEventTime: current.lastEventTime });
                  }
                  return false;
                }
                const newState: SpeakingState = detectionResult.isSpeaking ? 'speaking' : 'silent';
                if (current?.state === newState && current?.hasSignal) return false;
                if (current && (now - current.lastChangeTime) < this.MIN_STATE_CHANGE_MS) return false;
                this.state.set(participantId, { state: newState, hasSignal: true, lastChangeTime: now, lastEventTime: current?.lastEventTime || 0 });
                return true;
              }

              getState(participantId: string): SpeakingState | null {
                return this.state.get(participantId)?.state || null;
              }

              remove(participantId: string) {
                this.state.delete(participantId);
              }
            }

            // Detection: voice-level-stream-outline + vdi-frame-occlusion (NO FALLBACKS)
            class TeamsSpeakingDetector {
              private readonly VOICE_LEVEL_SELECTOR = '[data-tid="voice-level-stream-outline"]';

              detectSpeakingState(element: HTMLElement): { isSpeaking: boolean; hasSignal: boolean } {
                const voiceOutline = element.querySelector(this.VOICE_LEVEL_SELECTOR) as HTMLElement | null;
                if (!voiceOutline) return { isSpeaking: false, hasSignal: false };
                // vdi-frame-occlusion class presence (on voiceOutline or any
                // ancestor) = speaking; absence = not speaking.
                let current: HTMLElement | null = voiceOutline;
                while (current) {
                  if (current.classList.contains('vdi-frame-occlusion')) return { isSpeaking: true, hasSignal: true };
                  current = current.parentElement;
                }
                return { isSpeaking: false, hasSignal: true };
              }

              hasRequiredSignal(element: HTMLElement): boolean {
                return element.querySelector(this.VOICE_LEVEL_SELECTOR) !== null;
              }
            }

            class EventDebouncer {
              private timers = new Map<string, number>();
              constructor(private readonly delayMs: number = 300) {}
              debounce(key: string, fn: () => void) {
                if (this.timers.has(key)) clearTimeout(this.timers.get(key)!);
                const timer = setTimeout(() => { fn(); this.timers.delete(key); }, this.delayMs) as unknown as number;
                this.timers.set(key, timer);
              }
              cancel(key: string) {
                if (this.timers.has(key)) { clearTimeout(this.timers.get(key)!); this.timers.delete(key); }
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
              const sessionStartTime = audioService?.getSessionAudioStartTime?.() ?? null;

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
              if ((element as any).dataset.vexaObserverAttached) return;
              // Only observe if voice-level-stream-outline signal exists
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
              const voiceObserver = new MutationObserver(() => checkAndEmit(identity));
              voiceObserver.observe(voiceOutline, { attributes: true, attributeFilter: ['style', 'class', 'aria-hidden'] });
              // Observer on container (detect signal loss)
              const containerObserver = new MutationObserver(() => {
                if (!detector.hasRequiredSignal(element)) {
                  (window as any).logBot(`⚠️ [Unified] Voice-level signal lost for ${identity.name} - stopping observation`);
                  handleParticipantRemoved(identity);
                  return;
                }
                checkAndEmit(identity);
              });
              containerObserver.observe(element, { childList: true, subtree: true });
              observers.set(element, [voiceObserver, containerObserver]);
              scheduleRAFCheck(identity);
              checkAndEmit(identity);
            }

            function checkAndEmit(identity: ParticipantIdentity) {
              if (!identity.element.isConnected) { handleParticipantRemoved(identity); return; }
              const detectionResult = detector.detectSpeakingState(identity.element);
              if (stateMachine.updateState(identity.id, detectionResult) && detectionResult.hasSignal) {
                const newState: SpeakingState = detectionResult.isSpeaking ? 'speaking' : 'silent';
                speakingStates.set(identity.id, newState);
                debouncer.debounce(identity.id, () => emitEvent(newState, identity));
              }
            }

            function scheduleRAFCheck(identity: ParticipantIdentity) {
              const check = () => {
                if (!identity.element.isConnected) { handleParticipantRemoved(identity); return; }
                checkAndEmit(identity);
                rafHandles.set(identity.id, requestAnimationFrame(check));
              };
              rafHandles.set(identity.id, requestAnimationFrame(check));
            }

            function handleParticipantRemoved(identity: ParticipantIdentity) {
              debouncer.cancel(identity.id);
              if (stateMachine.getState(identity.id) === 'speaking') emitEvent('silent', identity);
              const obs = observers.get(identity.element);
              if (obs) { obs.forEach(o => o.disconnect()); observers.delete(identity.element); }
              const rafHandle = rafHandles.get(identity.id);
              if (rafHandle) { cancelAnimationFrame(rafHandle); rafHandles.delete(identity.id); }
              stateMachine.remove(identity.id);
              speakingStates.delete(identity.id);
              registry.invalidate(identity.element);
              delete (identity.element as any).dataset.vexaObserverAttached;
              (window as any).logBot(`🗑️ [Unified] Removed: ${identity.name} (ID: ${identity.id})`);
            }

            function emitEvent(state: SpeakingState, identity: ParticipantIdentity) {
              if (state === 'unknown') return;
              const eventType = state === 'speaking' ? 'SPEAKER_START' : 'SPEAKER_END';
              const emoji = state === 'speaking' ? '🎤' : '🔇';
              (window as any).logBot(`${emoji} [Unified] ${eventType}: ${identity.name} (ID: ${identity.id}) [signal-based]`);
              sendTeamsSpeakerEvent(eventType, identity);
            }

            function scanAndObserveAll() {
              let foundCount = 0, observedCount = 0;
              // Include [role="menuitem"] directly (most reliable selector)
              const allSelectors = [...participantSelectors, '[role="menuitem"]'];
              const seenElements = new WeakSet<HTMLElement>();
              for (const selector of allSelectors) {
                document.querySelectorAll(selector).forEach(el => {
                  if (el instanceof HTMLElement && !seenElements.has(el)) {
                    seenElements.add(el);
                    foundCount++;
                    if (detector.hasRequiredSignal(el)) { observeParticipant(el); observedCount++; }
                  }
                });
              }
              (window as any).logBot(`🔍 [Unified] Scanned ${foundCount} participants, observing ${observedCount} with signal`);
            }

            // Initialize speaker detection
            scanAndObserveAll();

            // Monitor for new/removed participants
            const bodyObserver = new MutationObserver((mutationsList) => {
              const allSelectors = [...participantSelectors, '[role="menuitem"]'];
              for (const mutation of mutationsList) {
                if (mutation.type !== 'childList') continue;
                mutation.addedNodes.forEach(node => {
                  if (node.nodeType !== Node.ELEMENT_NODE) return;
                  const elementNode = node as HTMLElement;
                  for (const selector of allSelectors) {
                    if (elementNode.matches(selector)) observeParticipant(elementNode);
                    elementNode.querySelectorAll(selector).forEach(childEl => {
                      if (childEl instanceof HTMLElement) observeParticipant(childEl);
                    });
                  }
                });
                mutation.removedNodes.forEach(node => {
                  if (node.nodeType !== Node.ELEMENT_NODE) return;
                  const elementNode = node as HTMLElement;
                  for (const selector of participantSelectors) {
                    if (!elementNode.matches(selector)) continue;
                    const identity = registry.getIdentity(elementNode);
                    if (speakingStates.get(identity.id) === 'speaking') {
                      (window as any).logBot(`🔇 [Unified] SPEAKER_END (Participant removed while speaking): ${identity.name} (ID: ${identity.id})`);
                      emitEvent('silent', identity);
                    }
                    handleParticipantRemoved(identity);
                  }
                });
              }
            });
            const meetingContainer = document.querySelector(selectorsTyped.meetingContainerSelectors[0]) || document.body;
            bodyObserver.observe(meetingContainer, { childList: true, subtree: true });

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

            // Per-speaker audio routing: Teams has ONE mixed stream. Caption
            // text drives speaker boundaries (captions only fire on real
            // speech, so no false activations). A ring buffer holds recent
            // audio to look back across the caption delay; flush on text
            // growth (new words) — refinements (punctuation/case) are ignored.
            const MAX_QUEUE_AGE_MS = 10000;
            const MIN_TEXT_GROWTH = 3; // chars — below this = refinement
            interface QueuedChunk {
              data: Float32Array;
              timestamp: number;
            }
            const audioQueue: QueuedChunk[] = [];
            let captionsEnabled = false;
            let lastCaptionSpeaker: string | null = null;
            let lastFlushedTextLength: number = 0;

            const setupPerSpeakerAudioRouting = () => {
              const audioEl = document.querySelector('audio') as HTMLAudioElement | null;
              if (!audioEl || !(audioEl.srcObject instanceof MediaStream)) {
                (window as any).logBot?.('[Teams PerSpeaker] No audio element found, skipping per-speaker routing');
                return;
              }

              const stream = audioEl.srcObject as MediaStream;
              if (stream.getAudioTracks().length === 0) {
                (window as any).logBot?.('[Teams PerSpeaker] Audio stream has no tracks');
                return;
              }

              const ctx = new AudioContext({ sampleRate: 16000 });
              const source = ctx.createMediaStreamSource(stream);
              const processor = ctx.createScriptProcessor(4096, 1, 1);
              const botNameLower = ((botConfigData as any)?.botName || (botConfigData as any)?.name || 'vexa').toLowerCase();

              processor.onaudioprocess = (e: AudioProcessingEvent) => {
                const data = e.inputBuffer.getChannelData(0);
                const now = Date.now();

                // Skip silence — don't queue chunks with no speech energy.
                // This prevents silence from being flushed to the wrong speaker
                // on speaker transitions.
                let sum = 0;
                for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
                const rms = Math.sqrt(sum / data.length);
                if (rms < 0.01) return;

                audioQueue.push({ data: new Float32Array(data), timestamp: now });

                // Drop entries older than MAX_QUEUE_AGE_MS
                while (audioQueue.length > 0 && now - audioQueue[0].timestamp > MAX_QUEUE_AGE_MS) {
                  audioQueue.shift();
                }
              };

              source.connect(processor);
              processor.connect(ctx.destination);
              (window as any).logBot?.('[Teams PerSpeaker] Audio routing active (caption-aware with ring buffer)');
            };

            // Caption observer: watches Teams live captions for speaker name +
            // text. Caption DOM differs host vs guest (host has items-renderer
            // wrapper, guest doesn't), but [data-tid="author"] +
            // [data-tid="closed-caption-text"] are stable across both. We pair
            // them by document order — robust to container restructuring.
            const captionSels = selectorsTyped.captionSelectors;
            let captionObserver: MutationObserver | null = null;

            let lastProcessedCaptionKey = '';

            const processCaptions = () => {
              const wrapper = document.querySelector(captionSels.rendererWrapper);
              if (!wrapper) return;

              // Find author/text atoms directly — the only stable data-tids
              const authorEls = wrapper.querySelectorAll('[data-tid="author"]');
              const textEls = wrapper.querySelectorAll('[data-tid="closed-caption-text"]');

              if (authorEls.length === 0 || textEls.length === 0) return;

              // Use the LAST pair — most recent caption entry.
              // Authors and texts appear in matched pairs in document order.
              const lastAuthor = authorEls[authorEls.length - 1];
              const lastText = textEls[textEls.length - 1];

              const speaker = (lastAuthor.textContent || '').trim();
              const text = (lastText.textContent || '').trim();
              if (!speaker || !text) return;

              // Deduplicate: Teams updates text in-place as ASR refines.
              // Only process when speaker changes or text grows significantly.
              const captionKey = speaker + '::' + text;
              if (captionKey === lastProcessedCaptionKey) return;
              lastProcessedCaptionKey = captionKey;

              const now = Date.now();
              const botNameLower2 = ((botConfigData as any)?.botName || (botConfigData as any)?.name || 'vexa').toLowerCase();
              const speakerLower = speaker.toLowerCase();
              if (speakerLower.includes(botNameLower2) || speakerLower.includes('vexa')) return;

              if (speaker !== lastCaptionSpeaker) {
                // Speaker changed. Queue contains new speaker's audio
                // (~1-1.5s accumulated during caption delay). Flush to
                // new speaker to preserve their opening words.
                lastFlushedTextLength = 0;
                const queued = audioQueue.length;
                if (queued > 0 && !speakerLower.includes(botNameLower2) && !speakerLower.includes('vexa')) {
                  // Only flush recent chunks (last 2s) — the caption delay lookback.
                  // Older chunks are stale silence from the gap between speakers.
                  const lookbackCutoff = now - 2000;
                  let discarded = 0;
                  while (audioQueue.length > 0 && audioQueue[0].timestamp < lookbackCutoff) {
                    audioQueue.shift();
                    discarded++;
                  }
                  let flushed = 0;
                  while (audioQueue.length > 0) {
                    const entry = audioQueue.shift()!;
                    if (typeof (window as any).__vexaTeamsAudioData === 'function') {
                      (window as any).__vexaTeamsAudioData(speaker, Array.from(entry.data));
                    }
                    flushed++;
                  }
                  (window as any).logBot?.('[Teams Captions] Speaker change: ' +
                    (lastCaptionSpeaker || '(none)') + ' → ' + speaker +
                    ' (flushed ' + flushed + ' chunks, discarded ' + discarded + ' stale)');
                } else {
                  (window as any).logBot?.('[Teams Captions] Speaker change: ' +
                    (lastCaptionSpeaker || '(none)') + ' → ' + speaker);
                }
              }

              lastCaptionSpeaker = speaker;

              // Flush only when text GREW (new words). Refinements
              // (punctuation/case) change text by 1-2 chars; new words grow by
              // 5+. Compare against PREVIOUS text length, not cumulative max
              // (Teams replaces caption text per entry, not appends).
              const textGrowth = text.length - lastFlushedTextLength;
              if (textGrowth > MIN_TEXT_GROWTH || text.length < lastFlushedTextLength) {
                if (!speakerLower.includes(botNameLower2) && !speakerLower.includes('vexa')) {
                  let flushed = 0;
                  while (audioQueue.length > 0) {
                    const entry = audioQueue.shift()!;
                    if (typeof (window as any).__vexaTeamsAudioData === 'function') {
                      (window as any).__vexaTeamsAudioData(speaker, Array.from(entry.data));
                    }
                    flushed++;
                  }
                  if (flushed > 0) {
                    (window as any).logBot?.('[Teams Captions] Flushed ' + flushed + ' chunks to ' + speaker +
                      ' (text ' + (textGrowth > 0 ? '+' + textGrowth : textGrowth) + ' chars)');
                  }
                }
                lastFlushedTextLength = text.length;
              }

              if (typeof (window as any).__vexaTeamsCaptionData === 'function') {
                (window as any).__vexaTeamsCaptionData(speaker, text, now);
              }
            };

            const startCaptionObserver = () => {
              const wrapper = document.querySelector(captionSels.rendererWrapper);
              if (!wrapper) return false; // captions not enabled yet — check again later
              captionsEnabled = true;
              (window as any).logBot?.('[Teams Captions] Caption wrapper found — caption-driven routing ACTIVE');
              captionObserver = new MutationObserver(processCaptions);
              captionObserver.observe(wrapper, { childList: true, subtree: true, characterData: true });
              processCaptions();
              // Backup poll in case MutationObserver misses virtual-DOM updates.
              setInterval(processCaptions, 200);
              return true;
            };

            // Try to detect if captions are already enabled; poll until found or give up
            const captionDetectionInterval = setInterval(() => {
              if (startCaptionObserver()) {
                clearInterval(captionDetectionInterval);
              }
            }, 2000);

            // Also watch for the wrapper to appear via body mutation
            const captionWrapperWatcher = new MutationObserver(() => {
              if (!captionsEnabled && startCaptionObserver()) {
                captionWrapperWatcher.disconnect();
                clearInterval(captionDetectionInterval);
              }
            });
            captionWrapperWatcher.observe(document.body, { childList: true, subtree: true });

            // Delay slightly to ensure audio element is ready
            setTimeout(setupPerSpeakerAudioRouting, 2000);

            // ARIA-roles-based participant collection (find menuitems in
            // Participants panel that contain an avatar/image).
            function collectAriaParticipants(): string[] {
              try {
                const menuItems = Array.from(document.querySelectorAll('[role="menuitem"]')) as HTMLElement[];
                const names = new Set<string>();
                for (const item of menuItems) {
                  const hasImg = !!(item.querySelector('img') || item.querySelector('[role="img"]'));
                  if (!hasImg) continue;
                  const aria = item.getAttribute('aria-label');
                  let name = aria && aria.trim() ? aria.trim() : (item.textContent || '').trim();
                  if (name) names.add(name);
                }
                return Array.from(names);
              } catch (err: any) {
                (window as any).logBot?.(`⚠️ [ARIA Participants] Error: ${err?.message || String(err)}`);
                return [];
              }
            }

            (window as any).getTeamsActiveParticipantsCount = () => {
              const names = collectAriaParticipants();
              return botConfigData?.name ? names.length + 1 : names.length;
            };
            (window as any).getTeamsActiveParticipants = () => {
              const names = collectAriaParticipants();
              if (botConfigData?.name) names.push(botConfigData.name);
              (window as any).logBot(`🔍 [ARIA Participants] ${JSON.stringify(names)}`);
              return names;
            };
          };

          // Setup Teams meeting monitoring (browser context)
          // Pack U.3 (v0.10.6): no longer flushes recording from browser context.
          // The audio pipeline is drained by stopTeamsRecording() (Node side) via
          // leaveMicrosoftTeams. Here we just disconnect audioService and signal
          // the outer promise.
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
                  `[Teams Recording] audioService.disconnect error during shutdown (${reason}): ${err?.message || err}`
                );
              }
              finish();
            };

            // Teams removal detection: text heuristics + Rejoin/Dismiss buttons.
            const checkForRemoval = () => {
              try {
                const bodyText = (document.body?.innerText || '').toLowerCase();
                const removalPhrases = [
                  "you've been removed from this meeting", 'you have been removed from this meeting',
                  'removed from meeting', 'meeting ended', 'call ended'
                ];
                if (removalPhrases.some(p => bodyText.includes(p))) {
                  (window as any).logBot('🚨 Teams removal detected via body text');
                  return true;
                }
                const buttons = Array.from(document.querySelectorAll('button')) as HTMLElement[];
                for (const btn of buttons) {
                  const txt = (btn.textContent || btn.innerText || '').trim().toLowerCase();
                  const aria = (btn.getAttribute('aria-label') || '').toLowerCase();
                  if (!(txt === 'rejoin' || txt === 'dismiss' || aria.includes('rejoin') || aria.includes('dismiss'))) continue;
                  if (btn.offsetWidth <= 0 || btn.offsetHeight <= 0) continue;
                  const cs = getComputedStyle(btn);
                  if (cs.display === 'none' || cs.visibility === 'hidden') continue;
                  (window as any).logBot('🚨 Teams removal detected via visible buttons (Rejoin/Dismiss)');
                  return true;
                }
                return false;
              } catch (error: any) {
                (window as any).logBot(`Error checking for Teams removal: ${error.message}`);
                return false;
              }
            };

            const checkInterval = setInterval(() => {
              if (checkForRemoval()) {
                (window as any).logBot("🚨 Bot has been removed from the Teams meeting. Initiating graceful leave...");
                stopMonitoring("removed_by_admin", () => reject(new Error("TEAMS_BOT_REMOVED_BY_ADMIN")));
                return;
              }
              const currentParticipantCount = (window as any).getTeamsActiveParticipantsCount ? (window as any).getTeamsActiveParticipantsCount() : 0;

              if (currentParticipantCount !== lastParticipantCount) {
                (window as any).logBot(`🔢 Teams participant count changed: ${lastParticipantCount} → ${currentParticipantCount}`);
                const participantList = (window as any).getTeamsActiveParticipants ? (window as any).getTeamsActiveParticipants() : [];
                (window as any).logBot(`👥 Current participants: ${JSON.stringify(participantList)}`);
                lastParticipantCount = currentParticipantCount;
                if (currentParticipantCount > 1) {
                  hasEverHadMultipleParticipants = true;
                  speakersIdentified = true;
                  (window as any).logBot("Teams Speakers identified - switching to post-speaker monitoring mode");
                }
              }

              if (currentParticipantCount === 0) {
                aloneTime++;
                const currentTimeout = speakersIdentified ? everyoneLeftTimeoutSeconds : startupAloneTimeoutSeconds;
                const timeoutDescription = speakersIdentified ? "post-speaker" : "startup";
                if (aloneTime >= currentTimeout) {
                  if (speakersIdentified) {
                    (window as any).logBot(`Teams meeting ended or bot has been alone for ${everyoneLeftTimeoutSeconds} seconds after speakers were identified. Stopping recorder...`);
                    stopMonitoring("left_alone_timeout", () => reject(new Error("TEAMS_BOT_LEFT_ALONE_TIMEOUT")));
                  } else {
                    (window as any).logBot(`Teams bot has been alone for ${startupAloneTimeoutSeconds} seconds during startup with no other participants. Stopping recorder...`);
                    stopMonitoring("startup_alone_timeout", () => reject(new Error("TEAMS_BOT_STARTUP_ALONE_TIMEOUT")));
                  }
                } else if (aloneTime > 0 && aloneTime % 10 === 0) { // log every 10s to avoid spam
                  if (speakersIdentified) {
                    (window as any).logBot(`Teams bot has been alone for ${aloneTime} seconds (${timeoutDescription} mode). Will leave in ${currentTimeout - aloneTime} more seconds.`);
                  } else {
                    const remainingMinutes = Math.floor((currentTimeout - aloneTime) / 60);
                    const remainingSeconds = (currentTimeout - aloneTime) % 60;
                    (window as any).logBot(`Teams bot has been alone for ${aloneTime} seconds during startup. Will leave in ${remainingMinutes}m ${remainingSeconds}s.`);
                  }
                }
              } else {
                aloneTime = 0;
                if (hasEverHadMultipleParticipants && !speakersIdentified) {
                  speakersIdentified = true;
                  (window as any).logBot("Teams speakers identified - switching to post-speaker monitoring mode");
                }
              }
            }, 1000);

            // Listen for page unload
            window.addEventListener("beforeunload", () => {
              (window as any).logBot("Teams page is unloading. Stopping recorder...");
              stopMonitoring("beforeunload", () => resolve());
            });

            document.addEventListener("visibilitychange", () => {
              if (document.visibilityState === "hidden") {
                (window as any).logBot("Teams document is hidden. Stopping recorder...");
                stopMonitoring("visibility_hidden", () => resolve());
              }
            });
          };

          // Initialize Teams-specific speaker detection
          if (!degradedNoMedia) {
            initializeTeamsSpeakerDetection(audioService, botConfigData);
          }

          // Setup Teams meeting monitoring
          setupTeamsMeetingMonitoring(botConfigData, audioService, resolve);
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
        meetingContainerSelectors: teamsMeetingContainerSelectors,
        captionSelectors: teamsCaptionSelectors
      } as any
    }
  );
}

/**
 * Stop the unified recording pipeline. Called from leaveMicrosoftTeams before
 * the UI leave + process shutdown, replacing the old __vexaFlushRecordingBlob
 * browser-side fn. Drains the upload queue (including the final isFinal=true
 * chunk) so meeting-api flips Recording.status to COMPLETED before the bot
 * exits.
 */
export async function stopTeamsRecording(): Promise<void> {
  if (!pipeline) {
    log("[Teams Recording] stopTeamsRecording: no active pipeline");
    return;
  }
  log("[Teams Recording] Stopping unified pipeline (drain final chunk)");
  try {
    await pipeline.stop();
  } catch (err: any) {
    log(`[Teams Recording] pipeline.stop() error: ${err?.message || err}`);
  }
  pipeline = null;
  recordingService = null;
}

export function getTeamsRecordingService(): RecordingService | null {
  return recordingService;
}
