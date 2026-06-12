import { Page } from "playwright";
import { log } from "../../utils";
import { BotConfig } from "../../types";
import { RecordingService } from '@vexa/recording';
import { getSegmentPublisher, disposeMixedChunkedPipeline } from "../../index";
import { ensureBrowserUtils } from "../../utils/injection";
import { MediaRecorderCapture, UnifiedRecordingPipeline } from '@vexa/audio-pipelines';
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
            // Blue-squares detection lives in the SHARED msteams-speakers
            // module (browser-utils bundle) — the SAME code the extension
            // runs. One implementation, no drift: debug it once, both hosts
            // get the fix.
            const w = window as any;
            if (!w.VexaBrowserUtils?.createTeamsSpeakers) {
              w.logBot('❌ [TeamsSpeakers] VexaBrowserUtils.createTeamsSpeakers missing — stale browser-utils bundle');
              return;
            }
            const selfName = (botConfigData as any)?.botName || (botConfigData as any)?.name || undefined;
            w.__vexaTeamsSpeakers = w.VexaBrowserUtils.createTeamsSpeakers({
              selfName,
              log: (m: string) => w.logBot?.(m),
              onSpeaking: (name: string, id: string, isEnd: boolean, tMs: number) => {
                // 1. Exit-callback speaker events (relative to audio session
                //    start) — persisted by the bot on leave.
                const sessionStartTime = audioService?.getSessionAudioStartTime?.() ?? null;
                if (sessionStartTime !== null) {
                  w.__vexaSpeakerEvents = w.__vexaSpeakerEvents || [];
                  w.__vexaSpeakerEvents.push({
                    event_type: isEnd ? 'SPEAKER_END' : 'SPEAKER_START',
                    participant_name: name,
                    participant_id: id,
                    relative_timestamp_ms: tMs - sessionStartTime,
                  });
                }
                // 2. dom-outline hint for the mixed-channel core's name binder.
                try { w.__vexaTeamsSpeakerHint?.(name, isEnd, tMs); } catch { /* not ready */ }
              },
            });
            w.logBot('[TeamsSpeakers] shared blue-squares detection started (self="' + (selfName || 'unknown') + '")');

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

            // Mixed-channel capture: Teams has ONE mixed stream. Ship it
            // CONTINUOUSLY to Node — no in-page routing, no ring buffer, no
            // RMS gate (the ChunkedTranscriber core owns segmentation,
            // silence gating, and attribution via the blue-squares hints).
            const setupMixedAudioCapture = () => {
              const audioEl = document.querySelector('audio') as HTMLAudioElement | null;
              if (!audioEl || !(audioEl.srcObject instanceof MediaStream)) {
                (window as any).logBot?.('[Teams Mixed] No audio element found, skipping mixed capture');
                return;
              }

              const stream = audioEl.srcObject as MediaStream;
              if (stream.getAudioTracks().length === 0) {
                (window as any).logBot?.('[Teams Mixed] Audio stream has no tracks');
                return;
              }

              const ctx = new AudioContext({ sampleRate: 16000 });
              const source = ctx.createMediaStreamSource(stream);
              const processor = ctx.createScriptProcessor(4096, 1, 1);

              processor.onaudioprocess = (e: AudioProcessingEvent) => {
                const data = e.inputBuffer.getChannelData(0);
                // onaudioprocess fires at buffer END — timestamp the START.
                const tsMs = Date.now() - (data.length / 16000) * 1000;
                try {
                  (window as any).__vexaTeamsMixedAudio?.(Array.from(data), tsMs);
                } catch { /* exposed fn not ready yet */ }
              };

              source.connect(processor);
              processor.connect(ctx.destination);
              (window as any).logBot?.('[Teams Mixed] Continuous mixed-stream capture active (ChunkedTranscriber)');
            };

            // Captions are deliberately NOT consumed for transcription:
            // they may be unavailable (require manual enabling) and the
            // mixed-channel core attributes via the blue-squares signal.

            // Delay slightly to ensure audio element is ready
            setTimeout(setupMixedAudioCapture, 2000);

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
  // Close the mixed-channel core first — its closing pass publishes the
  // final turn; this MUST complete before session_end goes out. Bounded so a
  // wedged transcription service can't hang the leave.
  try {
    await Promise.race([
      disposeMixedChunkedPipeline(),
      new Promise<void>(r => setTimeout(r, 10_000)),
    ]);
  } catch { /* best effort */ }

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
