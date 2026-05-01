import StealthPlugin from "puppeteer-extra-plugin-stealth";
import { log } from "./utils";
import { logJSON, setLogContext } from "./utils/log";
import { callStatusChangeCallback, mapExitReasonToStatus } from "./services/unified-callback";
import { chromium } from "playwright-extra";
import { handleGoogleMeet, leaveGoogleMeet } from "./platforms/googlemeet";
import { handleMicrosoftTeams, leaveMicrosoftTeams } from "./platforms/msteams";
import { handleZoom, leaveZoom, leaveZoomWeb } from "./platforms/zoom";
import { reconfigureZoomWebRecording } from "./platforms/zoom/web/recording";
import { getZoomSpeakerEvents } from "./platforms/zoom/strategies/recording";
import { browserArgs, getBrowserArgs, getAuthenticatedBrowserArgs, userAgent } from "./constans";
import { BotConfig } from "./types";
import { RecordingService } from "./services/recording";
import { VideoRecordingService } from "./services/video-recording";
import { TTSPlaybackService } from "./services/tts-playback";
import { MicrophoneService } from "./services/microphone";
import { MeetingChatService, ChatTranscriptConfig } from "./services/chat";
import { ScreenContentService, getVirtualCameraInitScript, getVideoBlockInitScript } from "./services/screen-content";
import { ScreenShareService } from "./services/screen-share"; // kept for Teams; unused for Google Meet camera-feed approach
import { createClient, RedisClientType } from 'redis';
import { Page, Browser, BrowserContext } from 'playwright-core';
import { execSync } from 'child_process';
import { ensureBrowserDataDir, syncBrowserDataFromS3, syncBrowserDataToS3, cleanStaleLocks, BROWSER_DATA_DIR } from './s3-sync';
// HTTP imports removed - using unified callback service instead

// Per-speaker transcription pipeline
import { TranscriptionClient } from './services/transcription-client';
import { SegmentPublisher } from './services/segment-publisher';
import { SpeakerStreamManager } from './services/speaker-streams';
import { resolveSpeakerName, clearSpeakerNameCache, isTrackLocked, isNameTaken, reportTrackAudio, getLockedMapping } from './services/speaker-identity';
import { SileroVAD } from './services/vad';
import { isHallucination } from './services/hallucination-filter';
import { SpeakerStreamHandle } from './services/audio';
import { RawCaptureService } from './services/raw-capture';

// Module-level variables to store current configuration
let currentLanguage: string | null | undefined = null;
let currentTask: string | null | undefined = 'transcribe'; // Default task
let currentRedisUrl: string | null = null;
let currentConnectionId: string | null = null;
let meetingApiCallbackUrl: string | null = null; // ADDED: To store callback URL
let currentPlatform: "google_meet" | "zoom" | "teams" | undefined;
let page: Page | null = null; // Initialize page, will be set in runBot

// --- ADDED: Flag to prevent multiple shutdowns ---
let isShuttingDown = false;
// ---------------------------------------------

// --- ADDED: Redis subscriber client ---
let redisSubscriber: RedisClientType | null = null;
// -----------------------------------

// --- ADDED: Browser instance ---
let browserInstance: Browser | null = null;
// -------------------------------

// --- Recording service reference (set by platform handlers) ---
let activeRecordingService: RecordingService | null = null;
let activeVideoRecordingService: VideoRecordingService | null = null;
// v0.10.5.3 Pack T — module-level holder so performGracefulLeave can read
// the resource summary at exit time and stop the sampler.
let currentBotResourceSampler: ReturnType<typeof startBotResourceSampler> | null = null;
let botPaSinkModuleId: string | null = null; // PulseAudio module ID for per-bot sink cleanup
let currentBotConfig: BotConfig | null = null;
export function setActiveRecordingService(svc: RecordingService | null): void {
  activeRecordingService = svc;
}

/**
 * Feed mixed PulseAudio audio into the per-speaker transcription pipeline.
 * Used by Zoom Web: single mixed stream + DOM speaker polling for attribution.
 * Mirrors handleTeamsAudioData but called from Node.js (not browser).
 */
export async function feedZoomAudio(speakerName: string, audioData: Float32Array): Promise<void> {
  if (!speakerManager || !segmentPublisher) return;

  const speakerId = `zoom-${speakerName.replace(/\s+/g, '_')}`;

  if (!speakerManager.hasSpeaker(speakerId)) {
    log(`[🎙️ ZOOM SPEAKER] "${speakerName}" — first audio received`);
    speakerManager.addSpeaker(speakerId, speakerName);
    await segmentPublisher.publishSpeakerEvent({
      speaker: speakerName,
      type: 'joined',
      timestamp: Date.now(),
    });
  }

  speakerManager.feedAudio(speakerId, audioData);
}

/**
 * Start video recording if the bot config requests it.
 * Called by meetingFlow.ts after admission so video and audio start at the same time.
 */
export function startVideoRecordingIfNeeded(): void {
  if (!currentBotConfig) return;
  const wantsVideoCapture = !!currentBotConfig.recordingEnabled &&
    Array.isArray(currentBotConfig.captureModes) && currentBotConfig.captureModes.includes('video');
  // Zoom Web is the default; opt into Native SDK with ZOOM_SDK=true (and
  // valid ZOOM_CLIENT_ID/SECRET). Legacy ZOOM_WEB=true still forces Web.
  const isZoomNative = currentBotConfig.platform === 'zoom'
    && process.env.ZOOM_SDK === 'true'
    && process.env.ZOOM_WEB !== 'true';

  if (wantsVideoCapture && !isZoomNative) {
    try {
      const sessionUid = currentBotConfig.connectionId || `video-${Date.now()}`;
      activeVideoRecordingService = new VideoRecordingService(currentBotConfig.meeting_id, sessionUid);
      activeVideoRecordingService.start();
      log('[VideoRecording] Screen capture started (post-admission)');
    } catch (err: any) {
      log(`[VideoRecording] Failed to start (non-fatal): ${err.message}`);
      activeVideoRecordingService = null;
    }
  } else if (wantsVideoCapture && isZoomNative) {
    log('[VideoRecording] Video recording not supported for Zoom Native SDK — unset ZOOM_SDK to use the default Web client (screen capture works there)');
  }
}

/**
 * Enter true fullscreen via CDP, hiding all browser chrome (tabs, address bar).
 * Must be called after the page has navigated to a real URL.
 */
export async function enterBrowserFullscreen(): Promise<void> {
  if (!page || page.isClosed()) return;
  try {
    const cdp = await page.context().newCDPSession(page);
    const { windowId } = await cdp.send('Browser.getWindowForTarget');
    await cdp.send('Browser.setWindowBounds', {
      windowId,
      bounds: { windowState: 'fullscreen' },
    });
    log('[Browser] Entered fullscreen via CDP (no tabs/address bar)');
  } catch (err: any) {
    log(`[Browser] CDP fullscreen failed (non-fatal): ${err.message}`);
  }
}
// ----------------------------------------------------------

// --- Voice agent / meeting interaction services ---
let ttsPlaybackService: TTSPlaybackService | null = null;
let microphoneService: MicrophoneService | null = null;
let chatService: MeetingChatService | null = null;
let screenContentService: ScreenContentService | null = null;
let screenShareService: ScreenShareService | null = null;
let redisPublisher: RedisClientType | null = null;
// -------------------------------------------------

// --- Per-speaker transcription pipeline ---
let transcriptionClient: TranscriptionClient | null = null;
let segmentPublisher: SegmentPublisher | null = null;
export function getSegmentPublisher(): SegmentPublisher | null { return segmentPublisher; }
let speakerManager: SpeakerStreamManager | null = null;
let vadModel: SileroVAD | null = null;
/** Per-speaker VAD states for streaming mode (GMeet only) */
import type { VadSpeakerState } from './services/vad';
const vadSpeakerStates: Map<string, VadSpeakerState> = new Map();
/** Whitelist of allowed language codes — if set, segments in other languages are discarded */
let allowedLanguages: string[] | null = null;
/** Per-speaker last detected language — used in onSegmentConfirmed where Whisper result isn't available */
const lastDetectedLanguage: Map<string, string> = new Map();
/** Pipeline telemetry counters — module-level so entry gate VAD can update them */
let pipelineTelemetry: {
  vadChunksProcessed: number;
  vadChunksRejected: number;
  [key: string]: any;
} | null = null;
let pipelineTelemetryInterval: ReturnType<typeof setInterval> | null = null;
let activeSpeakerStreamHandles: SpeakerStreamHandle[] = [];
/** Raw capture service — dumps per-speaker WAVs + events for offline replay (RAW_CAPTURE=true) */
let rawCaptureService: RawCaptureService | null = null;
export function getRawCaptureService(): RawCaptureService | null { return rawCaptureService; }
/** Per-speaker confirmed segment batches — drained on each draft tick, flushed on cleanup */
let confirmedBatches = new Map<string, import('./services/segment-publisher').TranscriptionSegment[]>();
// ------------------------------------------

// --- ADDED: Stop signal tracking ---
let stopSignalReceived = false;
export function hasStopSignalReceived(): boolean {
  return stopSignalReceived || isShuttingDown;
}
// -----------------------------------

// --- Post-admission camera re-enablement ---
// Google Meet may re-negotiate WebRTC tracks when the bot transitions from
// waiting room to the actual meeting, killing our initial canvas track.
// Teams "light meetings" (anonymous/guest) may set video to `inactive` in the
// initial SDP answer, requiring a camera toggle to force SDP renegotiation.
// This function is called by meetingFlow.ts after admission is confirmed
// to ensure the virtual camera is active in the meeting.

async function checkVideoFramesSent(): Promise<number> {
  if (!page || page.isClosed()) return 0;
  return page.evaluate(async () => {
    const pcs = (window as any).__vexa_peer_connections as RTCPeerConnection[] || [];
    for (const pc of pcs) {
      if (pc.connectionState === 'closed') continue;
      try {
        const stats = await pc.getStats();
        let frames = 0;
        stats.forEach((report: any) => {
          if (report.type === 'outbound-rtp' && report.kind === 'video') {
            frames = report.framesSent || 0;
          }
        });
        if (frames > 0) return frames;
      } catch {}
    }
    return 0;
  });
}

export async function triggerPostAdmissionCamera(): Promise<void> {
  if (!screenContentService || !page || page.isClosed()) return;

  log('[VoiceAgent] Post-admission: re-enabling virtual camera...');

  // Quick diagnostic
  try {
    const deepDiag = await page.evaluate(() => {
      const win = window as any;
      return {
        canvasExists: !!(win.__vexa_canvas),
        canvasStreamExists: !!(win.__vexa_canvas_stream),
        gumCallCount: win.__vexa_gum_call_count || 0,
        peerConnections: (win.__vexa_peer_connections || []).length,
        injectedAudioElements: (win.__vexaInjectedAudioElements || []).length,
      };
    });
    log(`[VoiceAgent] Deep diagnostic: ${JSON.stringify(deepDiag)}`);
  } catch (diagErr: any) {
    log(`[VoiceAgent] Diagnostic error: ${diagErr.message}`);
  }

  // Phase 1: Try standard enableCamera (works for Google Meet and some Teams scenarios)
  const PHASE1_ATTEMPTS = 2;
  for (let attempt = 1; attempt <= PHASE1_ATTEMPTS; attempt++) {
    try {
      await screenContentService.enableCamera();
      await new Promise(resolve => setTimeout(resolve, 2000));

      const framesSent = await checkVideoFramesSent();
      if (framesSent > 0) {
        log(`[VoiceAgent] ✅ Post-admission camera active! framesSent=${framesSent} (phase1, attempt ${attempt})`);
        return;
      }
      log(`[VoiceAgent] Post-admission framesSent=0 (phase1, attempt ${attempt})`);
    } catch (err: any) {
      log(`[VoiceAgent] Post-admission camera phase1 attempt ${attempt} failed: ${err.message}`);
    }
    if (attempt < PHASE1_ATTEMPTS) {
      await new Promise(resolve => setTimeout(resolve, 3000));
    }
  }

  // Phase 2: Camera toggle to force SDP renegotiation
  // Teams light meetings (anonymous/guest) may reject video in the initial SDP.
  // Toggling camera off→on forces Teams to issue a new SDP offer with video.
  log('[VoiceAgent] Phase 1 failed — attempting camera toggle for SDP renegotiation...');
  const PHASE2_ATTEMPTS = 3;
  const PHASE2_INTERVALS = [3000, 5000, 8000];

  for (let attempt = 1; attempt <= PHASE2_ATTEMPTS; attempt++) {
    try {
      const toggled = await screenContentService.toggleCameraForRenegotiation();
      if (!toggled) {
        log(`[VoiceAgent] Camera toggle attempt ${attempt}: could not find toggle buttons`);
        // Fallback even on intermediate attempts — Teams may have no usable
        // camera toggles in guest/light mode but still allow transceiver track injection.
        await tryAddTrackFallback();
        continue;
      }

      await new Promise(resolve => setTimeout(resolve, 3000));
      const framesSent = await checkVideoFramesSent();
      if (framesSent > 0) {
        log(`[VoiceAgent] ✅ Post-admission camera active after toggle! framesSent=${framesSent} (phase2, attempt ${attempt})`);
        return;
      }
      log(`[VoiceAgent] Post-admission framesSent=0 after toggle (phase2, attempt ${attempt})`);

      // Toggle succeeded but no frames are being published. Force a direct
      // transceiver/addTrack fallback to trigger fresh negotiation.
      await tryAddTrackFallback();
      await new Promise(resolve => setTimeout(resolve, 1500));
      const fallbackFrames = await checkVideoFramesSent();
      if (fallbackFrames > 0) {
        log(`[VoiceAgent] ✅ Post-admission camera active after addTrack fallback! framesSent=${fallbackFrames} (phase2, attempt ${attempt})`);
        return;
      }
      log(`[VoiceAgent] addTrack fallback still framesSent=0 (phase2, attempt ${attempt})`);
    } catch (err: any) {
      log(`[VoiceAgent] Post-admission camera phase2 attempt ${attempt} failed: ${err.message}`);
    }
    if (attempt < PHASE2_ATTEMPTS) {
      await new Promise(resolve => setTimeout(resolve, PHASE2_INTERVALS[attempt - 1]));
    }
  }

  log('[VoiceAgent] ⚠️ Post-admission camera failed all retries (both phases)');
}

// Last resort: directly call pc.addTrack() to inject our canvas track into the
// active PeerConnection. This triggers negotiationneeded which forces a new
// SDP offer/answer exchange with the video track included.
async function tryAddTrackFallback(): Promise<void> {
  if (!page || page.isClosed()) return;
  log('[VoiceAgent] Trying addTrack fallback to force video negotiation...');
  try {
    const result = await page.evaluate(() => {
      const win = window as any;
      const pcs = (win.__vexa_peer_connections || []) as RTCPeerConnection[];
      const canvasStream = win.__vexa_canvas_stream as MediaStream;
      if (!canvasStream) return { success: false, reason: 'no canvas stream' };

      const canvasTrack = canvasStream.getVideoTracks()[0];
      if (!canvasTrack) return { success: false, reason: 'no canvas video track' };

      for (const pc of pcs) {
        if (pc.connectionState === 'closed') continue;
        const transceivers = pc.getTransceivers();

        // Try to set video on an existing video-capable transceiver first.
        for (const t of transceivers) {
          const receiverKind = t.receiver?.track?.kind;
          const senderKind = t.sender?.track?.kind;
          const isVideoTransceiver = receiverKind === 'video' || senderKind === 'video';
          if (isVideoTransceiver) {
            try {
              t.direction = 'sendrecv';
              t.sender.replaceTrack(canvasTrack);
              return { success: true, method: 'transceiver-replace', mid: t.mid, pcState: pc.connectionState };
            } catch (e) {
              // Continue to next transceiver/fallback path.
            }
          }
        }

        // If no suitable transceiver exists, create one explicitly.
        try {
          const transceiver = pc.addTransceiver(canvasTrack, { direction: 'sendrecv' });
          return {
            success: true,
            method: 'addTransceiver',
            mid: transceiver?.mid ?? null,
            pcState: pc.connectionState
          };
        } catch (e) {
          // Fall back to addTrack below.
        }

        // Last resort: addTrack triggers negotiationneeded.
        try {
          pc.addTrack(canvasTrack, canvasStream);
          return { success: true, method: 'addTrack', pcState: pc.connectionState };
        } catch (e) {
          return { success: false, reason: 'addTrack failed: ' + (e as Error).message };
        }
      }
      return { success: false, reason: 'no suitable PC found' };
    });
    log(`[VoiceAgent] addTrack fallback result: ${JSON.stringify(result)}`);
  } catch (err: any) {
    log(`[VoiceAgent] addTrack fallback error: ${err.message}`);
  }
}
// -------------------------------------------

// --- Post-admission chat observer start ---
// Called by meetingFlow.ts after the bot is admitted to the meeting.
// The chat panel can only be opened and observed when the bot is in the
// actual meeting (not the waiting room / pre-join screen).
export async function triggerPostAdmissionChat(): Promise<void> {
  if (!chatService) return;
  try {
    log('[Chat] Post-admission: starting chat observer...');
    await chatService.startChatObserver();
    log('[Chat] ✅ Post-admission chat observer started');
  } catch (err: any) {
    log(`[Chat] Post-admission observer failed (non-fatal): ${err.message}`);
  }
}
// -------------------------------------------

// Exit reason mapping function moved to services/unified-callback.ts

// --- ADDED: Session Management Utilities ---
/**
 * Generate UUID for session identification
 */
export function generateUUID(): string {
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

/**
 * Get current timestamp in milliseconds
 */
export function getCurrentTimestamp(): number {
  return Date.now();
}

/**
 * Calculate relative timestamp from session start
 */
export function calculateRelativeTimestamp(sessionStartTimeMs: number | null): number | null {
  if (sessionStartTimeMs === null) {
    return null;
  }
  return Date.now() - sessionStartTimeMs;
}

/**
 * Create session control message
 */
export function createSessionControlMessage(
  event: string,
  sessionUid: string,
  botConfig: { token: string; platform: string; meeting_id: number; nativeMeetingId: string }
) {
  return {
    type: "session_control",
    payload: {
      event: event,
      uid: sessionUid,
      client_timestamp_ms: Date.now(),
      token: botConfig.token,  // MeetingToken (HS256 JWT)
      platform: botConfig.platform,
      meeting_id: botConfig.meeting_id
    }
  };
}

/**
 * Create speaker activity message
 */
export function createSpeakerActivityMessage(
  eventType: string,
  participantName: string,
  participantId: string,
  relativeTimestampMs: number,
  sessionUid: string,
  botConfig: { token: string; platform: string; meeting_id: number; nativeMeetingId: string; meetingUrl: string | null }
) {
  return {
    type: "speaker_activity",
    payload: {
      event_type: eventType,
      participant_name: participantName,
      participant_id_meet: participantId,
      relative_client_timestamp_ms: relativeTimestampMs,
      uid: sessionUid,
      token: botConfig.token,  // MeetingToken (HS256 JWT)
      platform: botConfig.platform,
      meeting_id: botConfig.meeting_id,
      meeting_url: botConfig.meetingUrl
    }
  };
}
// --- ------------------------------------ ---

// --- ADDED: Message Handler ---
// --- MODIFIED: Make async and add page parameter ---
const handleRedisMessage = async (message: string, channel: string, page: Page | null) => {
  // ++ ADDED: Log entry into handler ++
  log(`Received command on ${channel}: ${message.substring(0, 200)}${message.length > 200 ? '...' : ''}`);
  // --- ADDED: Implement reconfigure command handling --- 
  try {
      const command = JSON.parse(message);
      
      // Validate this command is for us (fail-fast)
      const meetingId = (globalThis as any).botConfig?.meeting_id;
      if (command.meeting_id && command.meeting_id !== meetingId) {
        log(`⚠️ Ignoring command for different meeting: ${command.meeting_id} (ours: ${meetingId})`);
        return;
      }
      
      if (command.action === 'reconfigure') {
          log(`Processing reconfigure command: Lang=${command.language}, Task=${command.task}, AllowedLangs=${command.allowed_languages || 'none'}`);

          // Update Node.js state
          currentLanguage = command.language;
          allowedLanguages = command.allowed_languages?.length ? command.allowed_languages : null;
          currentTask = command.task;

          // Zoom Web is the default; isZoomWeb is true unless ZOOM_SDK=true
          // explicitly opts into the native SDK path.
          const isZoomWeb = (globalThis as any).botConfig?.platform === 'zoom'
            && (process.env.ZOOM_WEB === 'true' || process.env.ZOOM_SDK !== 'true');
          if (isZoomWeb) {
            await reconfigureZoomWebRecording(currentLanguage ?? null, currentTask ?? null);
          }

          // Trigger browser-side reconfiguration via the exposed function (for Google Meet / Teams)
          if (!isZoomWeb) {
            if (page && !page.isClosed()) { // Ensure page exists and is open
              try {
                  await page.evaluate(
                      ([lang, task]) => {
                          const tryApply = () => {
                              const fn = (window as any).triggerWebSocketReconfigure;
                              if (typeof fn === 'function') {
                                  try {
                                      fn(lang, task);
                                  } catch (e: any) {
                                      console.error('[Reconfigure] Error invoking triggerWebSocketReconfigure:', e?.message || e);
                                  }
                                  return true;
                              }
                              return false;
                          };
                          if (!tryApply()) {
                              console.warn('[Reconfigure] triggerWebSocketReconfigure not ready. Retrying for up to 15s...');
                              const start = Date.now();
                              const intervalId = setInterval(() => {
                                  if (tryApply() || (Date.now() - start) > 15000) {
                                      clearInterval(intervalId);
                                  }
                              }, 500);
                              try {
                                  const ev = new CustomEvent('vexa:reconfigure', { detail: { lang, task } });
                                  document.dispatchEvent(ev);
                              } catch {}
                          }
                      },
                      [currentLanguage, currentTask] // Pass new config as argument array
                  );
                  log("Sent reconfigure command to browser context (with retry if not yet ready).");
              } catch (evalError: any) {
                  log(`Error evaluating reconfiguration script in browser: ${evalError.message}`);
              }
            } else {
               log("Page not available or closed, cannot send reconfigure command to browser.");
            }
          }
      } else if (command.action === 'leave') {
        // Mark that a stop was requested via Redis
        stopSignalReceived = true;
        log("Received leave command");
        if (!isShuttingDown) {
          // A command-initiated leave is a successful completion, not an error.
          // Exit with code 0 to signal success to Nomad and prevent restarts.
          const pageForLeave = (page && !page.isClosed()) ? page : null;
          await performGracefulLeave(pageForLeave, 0, "self_initiated_leave");
        } else {
           log("Ignoring leave command: Already shutting down.")
        }

      // ==================== Voice Agent Commands ====================

      } else if (command.action === 'speak') {
        // Speak text using TTS
        log(`Processing speak command: "${(command.text || '').substring(0, 50)}..."`);
        await handleSpeakCommand(command, page);

      } else if (command.action === 'speak_audio') {
        // Play pre-rendered audio (URL or base64)
        log(`Processing speak_audio command`);
        await handleSpeakAudioCommand(command);

      } else if (command.action === 'speak_stop') {
        // Interrupt current speech
        log('Processing speak_stop command');
        if (ttsPlaybackService) {
          ttsPlaybackService.interrupt();
          await publishVoiceEvent('speak.interrupted');
        }

      } else if (command.action === 'chat_send') {
        // Send a chat message
        log(`Processing chat_send command: "${(command.text || '').substring(0, 50)}..."`);
        if (chatService) {
          const success = await chatService.sendMessage(command.text);
          if (success) await publishVoiceEvent('chat.sent', { text: command.text });
        } else {
          log('[Chat] Chat service not initialized');
        }

      } else if (command.action === 'chat_read') {
        // Return captured chat messages (publish to response channel)
        log('Processing chat_read command');
        if (chatService) {
          const messages = chatService.getChatMessages();
          await publishVoiceEvent('chat.messages', { messages });
        }

      } else if (command.action === 'screen_show') {
        // Show content on screen (image, video, url)
        log(`Processing screen_show command: type=${command.type}`);
        await handleScreenShowCommand(command, page);

      } else if (command.action === 'screen_stop') {
        // Clear camera feed content (reverts to avatar/black)
        log('Processing screen_stop command');
        if (screenContentService) await screenContentService.clearScreen();
        await publishVoiceEvent('screen.content_cleared');

      } else if (command.action === 'avatar_set') {
        // Set custom avatar image (shown when no screen content is active)
        log(`Processing avatar_set command`);
        if (screenContentService) {
          await screenContentService.setAvatar(command.url || command.image_base64 || '');
          await publishVoiceEvent('avatar.set');
        }

      } else if (command.action === 'avatar_reset') {
        // Reset avatar to the default Vexa logo
        log('Processing avatar_reset command');
        if (screenContentService) {
          await screenContentService.resetAvatar();
          await publishVoiceEvent('avatar.reset');
        }
      }
  } catch (e: any) {
      log(`Error processing Redis message: ${e.message}`);
  }
  // -------------------------------------------------
};
// ----------------------------

// --- ADDED: Graceful Leave Function ---
async function performGracefulLeave(
  page: Page | null, // Allow page to be null for cases where it might not be available
  exitCode: number = 1, // Default to 1 (failure/generic error)
  reason: string = "self_initiated_leave", // Default reason
  errorDetails?: any // Optional detailed error information
): Promise<void> {
  if (isShuttingDown) {
    logJSON({
      level: "warn",
      msg: "[Graceful Leave] Already in progress, ignoring duplicate call",
      reason,
      exit_code: exitCode,
    });
    return;
  }
  isShuttingDown = true;
  // v0.10.5 Pack G.1 (#272 issue 6) — emit the graceful-leave start
  // event with structured fields. This is the diagnostic-critical
  // window: Pack G.2's kubectl-logs capture reads stdout right up to
  // pod termination, so every record in this function MUST be
  // parseable by the operator's grep+jq tooling.
  logJSON({
    level: "info",
    msg: "[Graceful Leave] Initiating graceful shutdown sequence",
    reason,
    exit_code: exitCode,
    error_details: errorDetails ?? null,
  });

  let platformLeaveSuccess = false;

  // Handle Zoom separately — SDK mode uses null page, web mode uses browser page
  if (currentPlatform === "zoom") {
    try {
      // Zoom Web is the default; SDK is opt-in via ZOOM_SDK=true.
      const zoomWebMode = process.env.ZOOM_WEB === 'true' || process.env.ZOOM_SDK !== 'true';
      if (zoomWebMode) {
        log("[Graceful Leave] Attempting Zoom Web cleanup...");
        platformLeaveSuccess = await leaveZoomWeb(page);
      } else {
        log("[Graceful Leave] Attempting Zoom SDK cleanup...");
        platformLeaveSuccess = await leaveZoom(null);
      }
    } catch (error: any) {
      log(`[Graceful Leave] Zoom cleanup error: ${error.message}`);
      platformLeaveSuccess = false;
    }
  } else if (page && !page.isClosed()) { // Browser-based platforms (Google Meet, Teams)
    try {
      log("[Graceful Leave] Attempting platform-specific leave...");
      if (currentPlatform === "google_meet") {
         platformLeaveSuccess = await leaveGoogleMeet(page, currentBotConfig ?? undefined);
      } else if (currentPlatform === "teams") {
         platformLeaveSuccess = await leaveMicrosoftTeams(page, currentBotConfig ?? undefined);
      } else {
         log(`[Graceful Leave] No platform-specific leave defined for ${currentPlatform}. Page will be closed.`);
         platformLeaveSuccess = true;
      }
      log(`[Graceful Leave] Platform leave/close attempt result: ${platformLeaveSuccess}`);
      
      // If leave was successful, wait a bit longer before closing to ensure Teams processes the leave
      if (platformLeaveSuccess === true) {
        log("[Graceful Leave] Leave action successful. Waiting 2 more seconds before cleanup...");
        await new Promise(resolve => setTimeout(resolve, 2000));
      }
    } catch (leaveError: any) {
      log(`[Graceful Leave] Error during platform leave/close attempt: ${leaveError.message}`);
      platformLeaveSuccess = false;
    }
  } else {
    log("[Graceful Leave] Page not available or already closed. Skipping platform-specific leave attempt.");
    // If the page is already gone, we can't perform a UI leave.
    // The provided exitCode and reason will dictate the callback.
    // If reason is 'admission_failed', exitCode would be 2, and platformLeaveSuccess is irrelevant.
  }

  // Cleanup voice agent services
  try {
    if (ttsPlaybackService) { ttsPlaybackService.stop(); ttsPlaybackService = null; }
    if (microphoneService) { microphoneService.clearMuteTimer(); microphoneService = null; }
    if (chatService) { await chatService.cleanup(); chatService = null; }
    if (screenContentService) { await screenContentService.close(); screenContentService = null; }
    if (screenShareService) { screenShareService = null; }
    if (redisPublisher && redisPublisher.isOpen) {
      await redisPublisher.quit();
      redisPublisher = null;
    }
  } catch (vaCleanupErr: any) {
    log(`[Graceful Leave] Voice agent cleanup error: ${vaCleanupErr.message}`);
  }

  // Cleanup per-speaker transcription pipeline
  try {
    await cleanupPerSpeakerPipeline();
  } catch (pipelineCleanupErr: any) {
    log(`[Graceful Leave] Per-speaker pipeline cleanup error: ${pipelineCleanupErr.message}`);
  }

  // Clean up per-bot PulseAudio sink if one was created
  if (botPaSinkModuleId) {
    try {
      execSync(`pactl unload-module ${botPaSinkModuleId}`, { stdio: 'ignore' });
      log(`[Graceful Leave] Unloaded PulseAudio sink module ${botPaSinkModuleId}`);
    } catch (e: any) {
      log(`[Graceful Leave] Warning: Could not unload PulseAudio sink module: ${e.message}`);
    }
    botPaSinkModuleId = null;
  }

  // Stop video recording, mux audio in, and upload the combined file
  if (activeVideoRecordingService && currentBotConfig?.recordingUploadUrl && currentBotConfig?.token) {
    try {
      logJSON({ level: "info", msg: "[Graceful Leave] Stopping video recording" });
      await activeVideoRecordingService.stop();

      // Finalize audio and mux into the video so the upload is a single self-contained file
      if (activeRecordingService) {
        try {
          const audioPath = await activeRecordingService.finalize();
          // Compute how much later audio started compared to video
          const audioDelayMs = activeRecordingService.getStartTime() - activeVideoRecordingService.getStartTime();
          logJSON({
            level: "info",
            msg: "[Graceful Leave] Muxing audio into video",
            audio_delay_ms: audioDelayMs,
          });
          await activeVideoRecordingService.muxAudio(audioPath, audioDelayMs);
        } catch (muxErr: any) {
          logJSON({
            level: "warn",
            msg: "[Graceful Leave] Audio mux failed (will upload video-only)",
            error_message: muxErr?.message,
            error_name: muxErr?.name,
          });
        }
      }

      logJSON({ level: "info", msg: "[Graceful Leave] Uploading video to meeting-api" });
      await activeVideoRecordingService.upload(currentBotConfig.recordingUploadUrl, currentBotConfig.token);
      logJSON({ level: "info", msg: "[Graceful Leave] Video uploaded successfully" });
    } catch (uploadError: any) {
      logJSON({
        level: "error",
        msg: "[Graceful Leave] Video upload failed",
        error_message: uploadError?.message,
        error_name: uploadError?.name,
        error_stack: uploadError?.stack,
      });
    } finally {
      await activeVideoRecordingService.cleanup();
      activeVideoRecordingService = null;
    }
  }

  // Upload audio recording separately (used for audio-only playback / transcription alignment)
  if (activeRecordingService && currentBotConfig?.recordingUploadUrl && currentBotConfig?.token) {
    try {
      logJSON({ level: "info", msg: "[Graceful Leave] Uploading audio recording to meeting-api" });
      await activeRecordingService.upload(currentBotConfig.recordingUploadUrl, currentBotConfig.token);
      logJSON({ level: "info", msg: "[Graceful Leave] Audio recording uploaded successfully" });
    } catch (uploadError: any) {
      // Recording-loss diagnostic: this is the line operators grep for in
      // post-mortems. Emit every field that lets them recover from S3 +
      // determine whether to retry (4xx) or escalate to platform (5xx).
      logJSON({
        level: "error",
        msg: "[Graceful Leave] Audio recording upload failed",
        error_message: uploadError?.message,
        error_name: uploadError?.name,
        error_stack: uploadError?.stack,
      });
    } finally {
      await activeRecordingService.cleanup();
      activeRecordingService = null;
    }
  }

  // Sync browser data back to S3 for authenticated bots (preserves cookies/sessions)
  if (currentBotConfig?.authenticated && currentBotConfig?.userdataS3Path) {
    try {
      log("[Graceful Leave] Syncing browser data to S3 (authenticated bot)...");
      syncBrowserDataToS3(currentBotConfig);
      log("[Graceful Leave] Browser data synced to S3.");
    } catch (syncErr: any) {
      log(`[Graceful Leave] Browser data S3 sync failed: ${syncErr.message}`);
    }
  }

  // Determine final exit code. If the initial intent was a successful exit (code 0),
  // it should always be 0. For error cases (non-zero exit codes), preserve the original error code.
  const finalCallbackExitCode = (exitCode === 0) ? 0 : exitCode;
  const finalCallbackReason = reason;

  // Read accumulated speaker events from browser context (or Zoom module)
  let speakerEvents: any[] = [];
  try {
    if (currentPlatform === "zoom") {
      speakerEvents = getZoomSpeakerEvents();
      log(`[Speaker Events] Read ${speakerEvents.length} speaker events from Zoom module`);
    } else if (page && !page.isClosed()) {
      speakerEvents = await page.evaluate(() => (window as any).__vexaSpeakerEvents || []);
      log(`[Speaker Events] Read ${speakerEvents.length} speaker events from browser context`);
    }
  } catch (e: any) {
    log(`[Speaker Events] Failed to read: ${e?.message}`);
  }

  // v0.10.5.3 Pack T — emit final resource summary as a structured log line
  // BEFORE the exit callback so Pack O's stdout-capture catches it. Then
  // stop the sampler.
  if (currentBotResourceSampler) {
    const resSum = currentBotResourceSampler.summary();
    logJSON({
      level: "info",
      msg: "[BotResource] final summary",
      bot_resources: resSum,
    });
    currentBotResourceSampler.stop();
    currentBotResourceSampler = null;
  }

  if (meetingApiCallbackUrl && currentConnectionId) {
    // Use unified callback for exit status
    const statusMapping = mapExitReasonToStatus(finalCallbackReason, finalCallbackExitCode);

    const botConfig = {
      meetingApiCallbackUrl,
      connectionId: currentConnectionId,
      container_name: process.env.HOSTNAME || 'unknown'
    };

    try {
      await callStatusChangeCallback(
        botConfig,
        statusMapping.status as any,
        finalCallbackReason,
        finalCallbackExitCode,
        errorDetails,
        statusMapping.completionReason,
        statusMapping.failureStage,
        speakerEvents.length > 0 ? speakerEvents : undefined
      );
      logJSON({
        level: "info",
        msg: "[Graceful Leave] Unified exit callback sent successfully",
        status: statusMapping.status,
        completion_reason: statusMapping.completionReason,
        failure_stage: statusMapping.failureStage,
        final_callback_reason: finalCallbackReason,
        final_callback_exit_code: finalCallbackExitCode,
        speaker_event_count: speakerEvents.length,
      });
    } catch (callbackError: any) {
      logJSON({
        level: "error",
        msg: "[Graceful Leave] Error sending unified exit callback",
        error_message: callbackError?.message,
        error_name: callbackError?.name,
        final_callback_reason: finalCallbackReason,
        final_callback_exit_code: finalCallbackExitCode,
      });
    }
  } else {
    logJSON({
      level: "error",
      msg: "[Graceful Leave] Bot manager callback URL or Connection ID not configured — cannot send exit status",
      has_callback_url: Boolean(meetingApiCallbackUrl),
      has_connection_id: Boolean(currentConnectionId),
    });
  }

  if (redisSubscriber && redisSubscriber.isOpen) {
    log("[Graceful Leave] Disconnecting Redis subscriber...");
    try {
        await redisSubscriber.unsubscribe();
        await redisSubscriber.quit();
        log("[Graceful Leave] Redis subscriber disconnected.");
    } catch (err) {
        log(`[Graceful Leave] Error closing Redis connection: ${err}`);
    }
  }

  // Close the browser page if it's still open and wasn't closed by platform leave
  if (page && !page.isClosed()) {
    log("[Graceful Leave] Ensuring page is closed.");
    try {
      await page.close();
      log("[Graceful Leave] Page closed.");
    } catch (pageCloseError: any) {
      log(`[Graceful Leave] Error closing page: ${pageCloseError.message}`);
    }
  }

  // Close the browser instance
  log("[Graceful Leave] Closing browser instance...");
  try {
    if (browserInstance && browserInstance.isConnected()) {
       await browserInstance.close();
       log("[Graceful Leave] Browser instance closed.");
    } else {
       log("[Graceful Leave] Browser instance already closed or not available.");
    }
  } catch (browserCloseError: any) {
    log(`[Graceful Leave] Error closing browser: ${browserCloseError.message}`);
  }

  // Exit the process
  // The process exit code should reflect the overall success/failure.
  // If callback used finalCallbackExitCode, process.exit could use the same.
  // v0.10.5 Pack G.1: emit the final exit record as structured JSON —
  // this is the LAST line in the kubectl-logs capture for a clean pod
  // exit, so it carries the diagnostic burden of "what happened?"
  logJSON({
    level: "info",
    msg: "[Graceful Leave] Exiting process",
    exit_code: finalCallbackExitCode,
    reason: finalCallbackReason,
  });
  process.exit(finalCallbackExitCode);
}
// --- ----------------------------- ---

// --- ADDED: Function to be called from browser to trigger leave ---
// This needs to be defined in a scope where 'page' will be available when it's exposed.
// We will define the actual exposed function inside runBot where 'page' is in scope.
// --- ------------------------------------------------------------ ---

// ==================== Voice Agent Command Handlers ====================

/**
 * Publish a voice agent event to Redis.
 */
async function publishVoiceEvent(event: string, data: any = {}): Promise<void> {
  if (!redisPublisher || !currentBotConfig) return;
  const meetingId = currentBotConfig.meeting_id;
  const payload = JSON.stringify({ event, meeting_id: meetingId, ...data, ts: new Date().toISOString() });
  const channel = `va:meeting:${meetingId}:events`;
  const listKey = `va:meeting:${meetingId}:event_log`;
  try {
    await Promise.all([
      redisPublisher.publish(channel, payload),
      // Persist to list so events can be read after the fact (capped at 200, TTL 1h)
      redisPublisher.rPush(listKey, payload).then(() =>
        Promise.all([
          redisPublisher!.lTrim(listKey, -200, -1),
          redisPublisher!.expire(listKey, 3600)
        ])
      )
    ]);
  } catch (err: any) {
    log(`[VoiceAgent] Failed to publish event ${event}: ${err.message}`);
  }
}

/**
 * Handle "speak" command — synthesize text to speech and play into meeting.
 */
async function handleSpeakCommand(command: any, page: Page | null): Promise<void> {
  if (!ttsPlaybackService) {
    log('[Speak] TTS playback service not initialized');
    return;
  }

  // Unmute mic before speaking
  if (microphoneService) {
    await microphoneService.unmute();
    await new Promise((r) => setTimeout(r, 500)); // Let Meet register unmute before audio
  }

  await publishVoiceEvent('speak.started', { text: command.text });

  try {
    const provider = command.provider || process.env.DEFAULT_TTS_PROVIDER || 'piper';
    const voice = command.voice || process.env.DEFAULT_TTS_VOICE || 'alloy';
    await ttsPlaybackService.synthesizeAndPlay(command.text, provider, voice);
    await publishVoiceEvent('speak.completed');
  } catch (err: any) {
    log(`[Speak] TTS failed: ${err.message}`);
    await publishVoiceEvent('speak.error', { message: err.message });
  }

  // Schedule auto-mute after speech
  if (microphoneService) {
    microphoneService.scheduleAutoMute(2000);
  }
}

/**
 * Handle "speak_audio" command — play pre-rendered audio.
 */
async function handleSpeakAudioCommand(command: any): Promise<void> {
  if (!ttsPlaybackService) {
    log('[SpeakAudio] TTS playback service not initialized');
    return;
  }

  // Unmute mic before playing
  if (microphoneService) {
    await microphoneService.unmute();
    await new Promise((r) => setTimeout(r, 500)); // Let Meet register unmute before audio
  }

  await publishVoiceEvent('speak.started', { source: command.audio_url ? 'url' : 'base64' });

  try {
    if (command.audio_url) {
      log(`[SpeakAudio] Playing from URL: ${command.audio_url}`);
      await ttsPlaybackService.playFromUrl(command.audio_url);
    } else if (command.audio_base64) {
      const format = command.format || 'wav';
      const sampleRate = command.sample_rate || 24000;
      log(`[SpeakAudio] Playing from base64 (${command.audio_base64.length} chars, format=${format}, rate=${sampleRate})`);
      await ttsPlaybackService.playFromBase64(command.audio_base64, format, sampleRate);
    } else {
      log('[SpeakAudio] No audio_url or audio_base64 provided');
      return;
    }
    log('[SpeakAudio] Playback completed');
    await publishVoiceEvent('speak.completed');
  } catch (err: any) {
    log(`[SpeakAudio] Playback failed: ${err.message}`);
    log(`[SpeakAudio] Stack: ${err.stack}`);
    await publishVoiceEvent('speak.error', { message: err.message });
  }

  if (microphoneService) {
    microphoneService.scheduleAutoMute(2000);
  }
}

/**
 * Handle "screen_show" command — display content on the bot's virtual camera feed.
 * Instead of screen sharing, we draw images/text onto a canvas that replaces
 * the bot's camera track via RTCPeerConnection.replaceTrack().
 */
async function handleScreenShowCommand(command: any, page: Page | null): Promise<void> {
  if (!screenContentService) {
    log('[Screen] Screen content service not initialized');
    return;
  }

  try {
    const contentType = command.type || 'image';

    if (contentType === 'image') {
      await screenContentService.showImage(command.url);
    } else if (contentType === 'text') {
      await screenContentService.showText(command.text || command.url);
    } else {
      log(`[Screen] Unsupported content type for camera feed: ${contentType}. Only 'image' and 'text' are supported.`);
      return;
    }

    await publishVoiceEvent('screen.content_updated', { content_type: contentType, url: command.url });
  } catch (err: any) {
    log(`[Screen] Show failed: ${err.message}`);
    await publishVoiceEvent('screen.error', { message: err.message });
  }
}

/**
 * Initialize the virtual camera and default avatar display.
 * Always runs — the bot should show its avatar regardless of voice agent state.
 */
async function initVirtualCamera(
  botConfig: BotConfig,
  page: Page,
): Promise<void> {
  log('[Bot] Initializing virtual camera and avatar...');

  // Screen content (virtual camera feed via canvas)
  screenContentService = new ScreenContentService(page, botConfig.defaultAvatarUrl);
  screenShareService = new ScreenShareService(page, botConfig.platform);
  log('[Bot] Screen content service ready');

  // Auto-enable virtual camera so the default avatar shows from the start.
  // Strategy: start trying early (even before admission — the camera button
  // may be available in the pre-join UI). Keep retrying until frames flow.
  (async () => {
    // Short initial wait for the page to load the meeting UI
    await new Promise(resolve => setTimeout(resolve, 5000));

    if (!screenContentService) return;

    const MAX_ATTEMPTS = 10;
    const RETRY_INTERVALS = [3000, 3000, 5000, 5000, 5000, 8000, 8000, 10000, 10000, 15000]; // ~72s total

    for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
      try {
        log(`[Bot] Auto-camera attempt ${attempt}/${MAX_ATTEMPTS}...`);
        await screenContentService.enableCamera();

        // Wait a moment for encoder to process the track
        await new Promise(resolve => setTimeout(resolve, 2000));

        // Check if frames are actually being sent
        const framesSent = await page.evaluate(async () => {
          const pcs = (window as any).__vexa_peer_connections as RTCPeerConnection[] || [];
          for (const pc of pcs) {
            if (pc.connectionState === 'closed') continue;
            try {
              const stats = await pc.getStats();
              let frames = 0;
              stats.forEach((report: any) => {
                if (report.type === 'outbound-rtp' && report.kind === 'video') {
                  frames = report.framesSent || 0;
                }
              });
              if (frames > 0) return frames;
            } catch {}
          }
          return 0;
        });

        if (framesSent > 0) {
          log(`[Bot] Virtual camera active! framesSent=${framesSent} (attempt ${attempt})`);
          break;
        }

        log(`[Bot] framesSent=0 after attempt ${attempt}, will retry...`);

        if (attempt < MAX_ATTEMPTS) {
          await new Promise(resolve => setTimeout(resolve, RETRY_INTERVALS[attempt - 1]));
        } else {
          log('[Bot] Auto-camera exhausted all retries. Camera may activate on next screen_show command.');
        }
      } catch (err: any) {
        log(`[Bot] Auto-camera attempt ${attempt} failed: ${err.message}`);
        if (attempt < MAX_ATTEMPTS) {
          await new Promise(resolve => setTimeout(resolve, RETRY_INTERVALS[attempt - 1]));
        }
      }
    }
  })();

  log('[Bot] Virtual camera initialization complete');
}

/**
 * Initialize chat service — always runs so chat read/write works
 * regardless of voiceAgentEnabled.
 */
async function initChatService(
  botConfig: BotConfig,
  page: Page,
): Promise<void> {
  log('[Chat] Initializing chat service...');

  const chatTranscriptConfig: ChatTranscriptConfig = {
    token: botConfig.token,
    platform: botConfig.platform,
    meetingId: botConfig.meeting_id,
    connectionId: botConfig.connectionId,
  };
  chatService = new MeetingChatService(
    page,
    botConfig.platform,
    botConfig.meeting_id,
    botConfig.botName,
    botConfig.redisUrl,
    chatTranscriptConfig
  );
  log('[Chat] Chat service ready');

  // Chat observer will be started post-admission by triggerPostAdmissionChat()
  // (called from meetingFlow.ts after the bot is admitted to the meeting)
}

/**
 * Initialize voice agent services (TTS, mic) after the browser and page are ready.
 * Only called when voiceAgentEnabled is true.
 */
async function initVoiceAgentServices(
  botConfig: BotConfig,
  page: Page,
  browser: Browser
): Promise<void> {
  log('[VoiceAgent] Initializing meeting interaction services...');

  // TTS Playback
  ttsPlaybackService = new TTSPlaybackService();
  log('[VoiceAgent] TTS playback service ready');

  // Microphone toggle
  microphoneService = new MicrophoneService(page, botConfig.platform);
  log('[VoiceAgent] Microphone service ready');

  // Redis publisher for events
  if (botConfig.redisUrl) {
    try {
      redisPublisher = createClient({ url: botConfig.redisUrl }) as RedisClientType;
      redisPublisher.on('error', (err) => log(`[VoiceAgent] Redis publisher error: ${err}`));
      await redisPublisher.connect();
      log('[VoiceAgent] Redis publisher connected');
    } catch (err: any) {
      log(`[VoiceAgent] Redis publisher failed: ${err.message}`);
    }
  }

  await publishVoiceEvent('voice_agent.initialized');
  log('[VoiceAgent] All meeting interaction services initialized');
}

// ==================== Per-Speaker Transcription Pipeline ====================

/**
 * Initialize the per-speaker transcription pipeline (Node.js side).
 * Creates TranscriptionClient, SegmentPublisher, and SpeakerStreamManager.
 * Must be called before `startPerSpeakerAudioCapture()`.
 */
async function initPerSpeakerPipeline(botConfig: BotConfig): Promise<boolean> {
  const transcriptionServiceUrl = botConfig.transcriptionServiceUrl || process.env.TRANSCRIPTION_SERVICE_URL;
  if (!transcriptionServiceUrl) {
    log('[PerSpeaker] WARNING: transcriptionServiceUrl not in config and TRANSCRIPTION_SERVICE_URL not set. Per-speaker transcription disabled.');
    return false;
  }

  const meetingId = botConfig.meeting_id;

  try {
    transcriptionClient = new TranscriptionClient({
      serviceUrl: transcriptionServiceUrl,
      apiToken: botConfig.transcriptionServiceToken || process.env.TRANSCRIPTION_SERVICE_TOKEN,
      maxSpeechDurationSec: process.env.MAX_SPEECH_DURATION_SEC ? parseFloat(process.env.MAX_SPEECH_DURATION_SEC) : undefined,
      minSilenceDurationMs: process.env.MIN_SILENCE_DURATION_MS ? parseInt(process.env.MIN_SILENCE_DURATION_MS) : 100,
    });
    log('[PerSpeaker] TranscriptionClient created');

    segmentPublisher = new SegmentPublisher({
      redisUrl: botConfig.redisUrl || process.env.REDIS_URL || 'redis://localhost:6379',
      meetingId: String(meetingId),
      token: botConfig.token,
      sessionUid: botConfig.connectionId || `bot-${Date.now()}`,
      platform: botConfig.platform,
    });
    log('[PerSpeaker] SegmentPublisher created');

    // Publish session_start so the collector knows when this session began
    await segmentPublisher.publishSessionStart();
    log('[PerSpeaker] Session start published');

    try {
      vadModel = await SileroVAD.create();
      log('[PerSpeaker] Silero VAD loaded');
    } catch (err: any) {
      log(`[PerSpeaker] VAD not available (${err.message}) — will send all audio`);
      vadModel = null;
    }

    // Raw capture: dump per-speaker WAVs + events for offline replay
    if (process.env.RAW_CAPTURE === 'true') {
      rawCaptureService = new RawCaptureService(meetingId);
      log(`[PerSpeaker] Raw capture enabled → ${rawCaptureService.outputPath}`);
    }

    const isGoogleMeet = botConfig.platform === 'google_meet';
    speakerManager = new SpeakerStreamManager({
      sampleRate: 16000,
      minAudioDuration: 3,     // 3s of unconfirmed audio before submission
      submitInterval: 2,       // submit every 2s — lower latency
      confirmThreshold: 2,     // 2 consecutive matches — faster confirmation
      maxBufferDuration: 30,   // force-flush at 30s — matches Whisper training window
      idleTimeoutSec: 15,      // 15s idle → emit + reset
    });
    // VAD gating moved to handlePerSpeakerAudioData entry (per-speaker streaming).
    // SpeakerStreamManager no longer does VAD — it only receives real speech.

    // onSegmentReady: transcribe the buffer (called every submitInterval)
    // Does NOT publish — just transcribes and feeds result back for confirmation.
    // Language is tracked per speaker — auto-detected on first chunk, locked when confident.
    // ── Telemetry counters ──
    const telemetry = {
      whisperCalls: 0,
      whisperFailures: 0,
      totalWhisperMs: 0,
      draftsEmitted: 0,
      segmentsConfirmed: 0,
      segmentsDiscarded: 0,
      totalConfirmLatencyMs: 0,  // time from buffer start to confirmation
      reconfirmations: 0,        // times the same text was confirmed again (wasted work)
      whisperSegmentCounts: [] as number[],  // how many segments Whisper returns per call
      vadChunksProcessed: 0,     // total audio chunks checked by entry VAD
      vadChunksRejected: 0,      // chunks rejected as silence by entry VAD
    };
    pipelineTelemetry = telemetry; // expose to module-level for entry gate VAD
    pipelineTelemetryInterval = setInterval(() => {
      const avgWhisper = telemetry.whisperCalls > 0 ? (telemetry.totalWhisperMs / telemetry.whisperCalls).toFixed(0) : 'n/a';
      const avgConfirmLatency = telemetry.segmentsConfirmed > 0 ? (telemetry.totalConfirmLatencyMs / telemetry.segmentsConfirmed / 1000).toFixed(1) : 'n/a';
      const avgWhisperSegs = telemetry.whisperSegmentCounts.length > 0 ? (telemetry.whisperSegmentCounts.reduce((a,b) => a+b, 0) / telemetry.whisperSegmentCounts.length).toFixed(1) : 'n/a';
      log(`[📊 TELEMETRY] whisper=${telemetry.whisperCalls} (${avgWhisper}ms avg, ${telemetry.whisperFailures} failed) | drafts=${telemetry.draftsEmitted} confirmed=${telemetry.segmentsConfirmed} discarded=${telemetry.segmentsDiscarded} | confirm_latency=${avgConfirmLatency}s | whisper_segs/call=${avgWhisperSegs} | reconfirm=${telemetry.reconfirmations} | vad=${telemetry.vadChunksProcessed}/${telemetry.vadChunksRejected} (checked/rejected)`);
      // Flush arrays to prevent unbounded growth during long meetings
      telemetry.whisperSegmentCounts = [];
    }, 30000);

    speakerManager.onSegmentReady = async (speakerId: string, speakerName: string, audioBuffer: Float32Array) => {
      if (!transcriptionClient) return;

      // Language strategy:
      // - If user explicitly set a language → always use it (respect the choice)
      // - If allowedLanguages has exactly 1 entry → force that language (more accurate than auto-detect)
      // - Otherwise → auto-detect (null)
      const explicitLang = currentLanguage && currentLanguage !== 'auto' ? currentLanguage : null;
      const singleAllowed = !explicitLang && allowedLanguages?.length === 1 ? allowedLanguages[0] : null;
      const lang = explicitLang || singleAllowed || null;

      const whisperStartMs = Date.now();
      try {
        const contextPrompt = speakerManager!.getLastConfirmedText(speakerId);
        const result = await transcriptionClient.transcribe(audioBuffer, lang || undefined, contextPrompt || undefined);
        telemetry.whisperCalls++;
        telemetry.totalWhisperMs += Date.now() - whisperStartMs;
        if (result && result.text) {
          telemetry.whisperSegmentCounts.push(result.segments?.length || 0);
          const prob = result.language_probability ?? 0;
          log(`[🌐 LANGUAGE] ${speakerName} → ${result.language} (prob=${prob.toFixed(2)}${lang ? ', explicit' : ''})`);

          // ── Quality gate: discard low-confidence segments ──────────
          // Short noisy audio → wrong language → hallucinated garbage.
          // Check multiple signals from Whisper before accepting.

          // 1. Language confidence (auto-detect only)
          if (!lang && prob > 0 && prob < 0.3) {
            telemetry.segmentsDiscarded++;
            log(`[🚫 LOW CONFIDENCE] ${speakerName} | lang_prob=${prob.toFixed(2)} | "${result.text}" — discarded`);
            speakerManager!.handleTranscriptionResult(speakerId, '');
            return;
          }

          // 2. Per-segment quality signals (avg_logprob, no_speech_prob, compression_ratio)
          if (result.segments && result.segments.length > 0) {
            const seg = result.segments[0]; // primary segment
            const noSpeech = seg.no_speech_prob ?? 0;
            const logProb = seg.avg_logprob ?? 0;
            const compression = seg.compression_ratio ?? 1;
            const duration = (seg.end || 0) - (seg.start || 0);

            // High no_speech_prob + low logprob = noise, not speech
            if (noSpeech > 0.5 && logProb < -0.7) {
              telemetry.segmentsDiscarded++;
              log(`[🚫 NO SPEECH] ${speakerName} | no_speech=${noSpeech.toFixed(2)} logprob=${logProb.toFixed(2)} | "${result.text}" — discarded`);
              speakerManager!.handleTranscriptionResult(speakerId, '');
              return;
            }

            // Very low logprob on short audio = garbage
            if (logProb < -0.8 && duration < 2.0) {
              telemetry.segmentsDiscarded++;
              log(`[🚫 LOW QUALITY] ${speakerName} | logprob=${logProb.toFixed(2)} dur=${duration.toFixed(1)}s | "${result.text}" — discarded`);
              speakerManager!.handleTranscriptionResult(speakerId, '');
              return;
            }

            // High compression ratio = repetitive output (hallucination pattern)
            if (compression > 2.4) {
              telemetry.segmentsDiscarded++;
              log(`[🚫 REPETITIVE] ${speakerName} | compression=${compression.toFixed(1)} | "${result.text}" — discarded`);
              speakerManager!.handleTranscriptionResult(speakerId, '');
              return;
            }
          }

          // 3. Phrase-based hallucination filter
          if (isHallucination(result.text)) {
            log(`[🚫 HALLUCINATION] ${speakerName} | "${result.text}"`);
            speakerManager!.handleTranscriptionResult(speakerId, '');
            return;
          }

          // Track detected language per speaker (used by onSegmentConfirmed)
          if (result.language) {
            lastDetectedLanguage.set(speakerId, result.language);
          }

          // Store word timestamps for speaker-mapper (Teams: post-transcription attribution)
          const words = result.segments?.flatMap(s => s.words || []) || [];
          if (words.length > 0) {
            latestWhisperWords = words;
          }

          // Process through SpeakerStreamManager — may trigger onSegmentConfirmed
          const lastSeg = result.segments?.[result.segments.length - 1];
          const segEndSec = lastSeg?.end;
          const whisperSegs = result.segments?.map(s => ({
            text: s.text, start: s.start, end: s.end
          }));
          speakerManager!.handleTranscriptionResult(speakerId, result.text, segEndSec, whisperSegs);

          // Publish batch: confirmed (collected by onSegmentConfirmed) + pending (current draft)
          if (segmentPublisher && result.text) {
            const lang = explicitLang || result.language || 'en';
            const bufStart = speakerManager!.getBufferStartMs(speakerId);
            const nowMs = Date.now();
            const startSec = (bufStart - segmentPublisher.sessionStartMs) / 1000;
            const endSec = (nowMs - segmentPublisher.sessionStartMs) / 1000;

            // Pending: one entry per Whisper segment (preserves sentence boundaries)
            const whisperSegments = result.segments || [{ text: result.text, start: 0, end: 0 }];
            const pendingSegs: import('./services/segment-publisher').TranscriptionSegment[] = whisperSegments
              .map(ws => ({
                speaker: speakerName,
                text: (ws.text || '').trim(),
                start: startSec + (ws.start || 0),
                end: startSec + (ws.end || 0),
                language: lang, completed: false,
                absolute_start_time: new Date(bufStart + (ws.start || 0) * 1000).toISOString(),
                absolute_end_time: new Date(bufStart + (ws.end || 0) * 1000).toISOString(),
              }))
              .filter(s => s.text);

            telemetry.draftsEmitted++;
            log(`[📝 DRAFT] ${speakerName} | ${lang} | ${startSec.toFixed(1)}s-${endSec.toFixed(1)}s | "${result.text.substring(0, 50)}"`);

            // Drain only this speaker's confirmed batch
            const speakerConfirmed = confirmedBatches.get(speakerId) || [];
            confirmedBatches.set(speakerId, []);

            // Filter out pending segments that overlap with just-confirmed text
            const confirmedTextList = speakerConfirmed.map(c => c.text.trim());
            const pending = pendingSegs.filter(p => {
              const pt = p.text.trim();
              return !confirmedTextList.some(ct => pt === ct || pt.startsWith(ct) || ct.startsWith(pt));
            });
            log(`[📡 PUBLISH] ${speakerName} | ${speakerConfirmed.length}C ${pending.length}P`);
            await segmentPublisher.publishTranscript(speakerName, speakerConfirmed, pending);
          }
        } else {
          speakerManager!.handleTranscriptionResult(speakerId, '');
        }
      } catch (err: any) {
        telemetry.whisperCalls++;
        telemetry.whisperFailures++;
        telemetry.totalWhisperMs += Date.now() - whisperStartMs;
        log(`[❌ FAILED] ${speakerName}: ${err.message}`);
        speakerManager!.handleTranscriptionResult(speakerId, '');
      }
    };

    // Reset confirmed batches for this session
    confirmedBatches = new Map();

    // onSegmentConfirmed: collect into batch (published atomically with pending)
    speakerManager.onSegmentConfirmed = (speakerId: string, speakerName: string, transcript: string, bufferStartMs: number, bufferEndMs: number, segmentId: string) => {
      if (!segmentPublisher) return;
      if (isHallucination(transcript)) {
        log(`[🚫 HALLUCINATION] ${speakerName} | confirmed but filtered: "${transcript}"`);
        return;
      }
      const explicitLang = currentLanguage && currentLanguage !== 'auto' ? currentLanguage : null;
      const lang = explicitLang || lastDetectedLanguage.get(speakerId) || 'en';
      const startSec = (bufferStartMs - segmentPublisher.sessionStartMs) / 1000;
      const endSec = (bufferEndMs - segmentPublisher.sessionStartMs) / 1000;
      const fullSegmentId = `${segmentPublisher.sessionUid}:${segmentId}`;

      const confirmLatencyMs = bufferEndMs - bufferStartMs;
      const segDurationSec = endSec - startSec;
      const wordCount = transcript.split(/\s+/).length;
      telemetry.segmentsConfirmed++;
      telemetry.totalConfirmLatencyMs += confirmLatencyMs;
      log(`[📝 CONFIRMED] ${speakerName} | ${lang} | ${startSec.toFixed(1)}s-${endSec.toFixed(1)}s (${segDurationSec.toFixed(1)}s, ${wordCount}w, latency=${(confirmLatencyMs/1000).toFixed(1)}s) | ${fullSegmentId} | "${transcript}"`);
      if (rawCaptureService) rawCaptureService.logSegmentConfirmed(speakerName, transcript);

      if (!confirmedBatches.has(speakerId)) confirmedBatches.set(speakerId, []);
      confirmedBatches.get(speakerId)!.push({
        speaker: speakerName, text: transcript, start: startSec, end: endSec,
        language: lang, completed: true, segment_id: fullSegmentId,
        absolute_start_time: new Date(bufferStartMs).toISOString(),
        absolute_end_time: new Date(bufferEndMs).toISOString(),
      });
    };

    log('[PerSpeaker] SpeakerStreamManager created and wired');
    return true;
  } catch (err: any) {
    log(`[PerSpeaker] Pipeline initialization failed: ${err.message}`);
    return false;
  }
}

/**
 * Handle per-speaker audio data arriving from the browser.
 * Called via page.exposeFunction from the browser's per-speaker audio streams.
 *
 * @param speakerIndex - index of the media element
 * @param audioDataArray - the Float32 audio samples as a plain number array (serialized from browser)
 */
/** Track last re-resolution time per unmapped speaker */
const lastReResolveTime = new Map<string, number>();
/** Track last known participant count to detect joins/leaves */
let lastParticipantCount = 0;
/** Per-speaker last audio received timestamp (Node.js side) — for silence monitoring */
const speakerLastAudioMs: Map<string, number> = new Map();

/** Check if a name is already assigned to a different speaker in the SpeakerStreamManager. */
function isDuplicateSpeakerName(name: string, excludeSpeakerId: string): boolean {
  if (!speakerManager) return false;
  for (const sid of speakerManager.getActiveSpeakers()) {
    if (sid !== excludeSpeakerId && speakerManager.getSpeakerName(sid) === name) return true;
  }
  return false;
}

async function handlePerSpeakerAudioData(speakerIndex: number, audioDataArray: number[]): Promise<void> {
  if (!speakerManager || !segmentPublisher || !page || page.isClosed()) return;

  // Report audio activity for Zoom active-speaker disambiguation
  reportTrackAudio(speakerIndex);

  const speakerId = `speaker-${speakerIndex}`;
  const audioData = new Float32Array(audioDataArray);

  const platformKey = currentPlatform === 'google_meet' ? 'googlemeet'
    : currentPlatform === 'teams' ? 'msteams'
    : currentPlatform || 'unknown';

  // ─── GMeet / Teams / Zoom: voting + locking ────────────────────────────────
  // All three platforms use the shared resolveSpeakerName() pipeline with
  // per-platform DOM resolvers (resolveGoogleMeetSpeakerName,
  // resolveTeamsSpeakerName, resolveZoomSpeakerName).
  //
  // Live observation 2026-04-26 confirmed Zoom Web's MediaStream pool is
  // small (~6 streams) and stream_id is stable per speaker (88%+ of audible
  // ticks per stream attribute to one dominant speaker). speakerIndex is
  // captured at first connectElement() per stream_id and never changes for
  // that stream — equivalent to gmeet's stable per-tile track. Vote-and-lock
  // works correctly here; PR #181's per-chunk DOM-poll override was the bug.
  if (!speakerManager.hasSpeaker(speakerId)) {
    log(`[🔊 NEW SPEAKER] Track ${speakerIndex} — first audio received, resolving name...`);
    const name = await resolveSpeakerName(page, speakerIndex, platformKey, currentBotConfig?.botName);
    // Start unmapped — only assign if name is genuinely unique
    const safeName = (name && !isDuplicateSpeakerName(name, speakerId)) ? name : '';
    log(`[🎙️ SPEAKER ACTIVE] Track ${speakerIndex} → "${safeName || '(unmapped)'}" — streaming audio`);
    speakerManager.addSpeaker(speakerId, safeName);
    lastReResolveTime.set(speakerId, Date.now());
    if (safeName) {
      await segmentPublisher.publishSpeakerEvent({
        speaker: safeName,
        type: 'joined',
        timestamp: Date.now(),
      });
      log(`[📡 SPEAKER EVENT] "${safeName}" joined → Redis`);
    }
  } else {
    const currentName = speakerManager.getSpeakerName(speakerId) || '';
    let locked = isTrackLocked(speakerIndex);

    // Sync locked name → speaker-streams buffer. Once a track is locked, the
    // re-resolve block below is skipped (!locked gate). If the buffer was empty
    // when the lock happened (e.g. addSpeaker('') on first audio before voting
    // completed), the locked name never propagates. Fix: on every audio chunk,
    // check if locked and buffer name is stale, and update.
    if (locked) {
      const lockedName = getLockedMapping(speakerIndex);
      if (lockedName && lockedName !== currentName) {
        log(`[🔒 LOCK SYNC] Track ${speakerIndex}: "${currentName}" → "${lockedName}" (syncing locked name to buffer)`);
        speakerManager.updateSpeakerName(speakerId, lockedName);
        if (!currentName) {
          await segmentPublisher.publishSpeakerEvent({
            speaker: lockedName,
            type: 'joined',
            timestamp: Date.now(),
          });
          log(`[📡 SPEAKER EVENT] "${lockedName}" joined → Redis`);
        }
      }
    }

    // Detect participant count change → invalidate ALL mappings (including locks)
    // Google Meet reassigns audio tracks when participants join/leave.
    const lastResolve = lastReResolveTime.get(speakerId) || 0;
    const reResolveInterval = locked ? 5_000 : (currentName ? 5_000 : 1_000);
    if (Date.now() - lastResolve > reResolveInterval) {
      lastReResolveTime.set(speakerId, Date.now());

      try {
        const currentCount = await page.evaluate(() => {
          if (typeof (window as any).getGoogleMeetActiveParticipantsCount === 'function') {
            return (window as any).getGoogleMeetActiveParticipantsCount();
          }
          const teamsTiles = document.querySelectorAll('[data-tid*="video-tile"], [data-tid*="participant"]');
          if (teamsTiles.length > 0) return teamsTiles.length;
          return 0;
        });
        if (lastParticipantCount > 0 && currentCount !== lastParticipantCount) {
          log(`[SpeakerIdentity] Participant count changed: ${lastParticipantCount} → ${currentCount}. Invalidating all mappings (including locks).`);
          clearSpeakerNameCache();
          locked = false; // Force re-resolve below
        }
        lastParticipantCount = currentCount;
      } catch {}
    }

    // Re-resolve if: unmapped OR not locked yet OR locks were just cleared
    if (!locked && Date.now() - lastResolve > reResolveInterval) {
      lastReResolveTime.set(speakerId, Date.now());

      const newName = await resolveSpeakerName(page, speakerIndex, platformKey, currentBotConfig?.botName);
      if (newName && newName !== currentName && !isDuplicateSpeakerName(newName, speakerId)) {
        log(`[🔄 SPEAKER MAPPED] Track ${speakerIndex}: "${currentName}" → "${newName}"`);
        speakerManager.updateSpeakerName(speakerId, newName);
        await segmentPublisher.publishSpeakerEvent({
          speaker: newName,
          type: 'joined',
          timestamp: Date.now(),
        });
        log(`[📡 SPEAKER EVENT] "${newName}" joined → Redis`);
      }

      // Fallback: if unmapped for 15s+ and GMeet, assign by participant list order.
      // This handles the case where speaking detection is completely broken (stale CSS selectors).
      if (!currentName && platformKey === 'googlemeet') {
        const firstAudio = speakerLastAudioMs.get(speakerId) || Date.now();
        if (Date.now() - firstAudio > 15_000) {
          try {
            const state = await page.evaluate((selfName: string) => {
              const getNames = (window as any).__vexaGetAllParticipantNames;
              if (typeof getNames !== 'function') return null;
              const data = getNames() as { names: Record<string, string>; speaking: string[] };
              const selfLower = selfName.toLowerCase();
              return Object.values(data.names).filter(n => {
                const lower = n.toLowerCase();
                return !(lower.includes(selfLower) || selfLower.includes(lower));
              });
            }, currentBotConfig?.botName || 'Vexa Bot');

            if (state && speakerIndex < state.length) {
              const fallbackName = state[speakerIndex];
              if (fallbackName && !isDuplicateSpeakerName(fallbackName, speakerId)) {
                log(`[🔄 SPEAKER FALLBACK] Track ${speakerIndex}: assigning "${fallbackName}" by participant order (no votes after 15s)`);
                speakerManager.updateSpeakerName(speakerId, fallbackName);
                await segmentPublisher.publishSpeakerEvent({
                  speaker: fallbackName,
                  type: 'joined',
                  timestamp: Date.now(),
                });
              }
            }
          } catch {}
        }
      }
    }
  }

  // Per-speaker streaming VAD gate (GMeet only).
  // Filters ambient noise BEFORE feedAudio() so lastAudioTimestamp only updates
  // on real speech. This lets the 15s idle timeout fire correctly when a speaker
  // stops talking but their mic still emits low-level room noise.
  const isGMeet = currentPlatform === 'google_meet';
  if (isGMeet && vadModel) {
    // Get or create per-speaker VAD state
    if (!vadSpeakerStates.has(speakerId)) {
      vadSpeakerStates.set(speakerId, vadModel.createSpeakerState());
    }
    const vadState = vadSpeakerStates.get(speakerId)!;
    const isSpeech = await vadModel.isSpeechStreaming(audioData, vadState);
    if (pipelineTelemetry) pipelineTelemetry.vadChunksProcessed++;

    if (!isSpeech) {
      if (pipelineTelemetry) pipelineTelemetry.vadChunksRejected++;
      return; // Skip feedAudio — ambient noise, don't update lastAudioTimestamp
    }
  }

  // Track audio arrival for silence monitoring (only reached for real speech)
  const prevMs = speakerLastAudioMs.get(speakerId);
  const nowMs = Date.now();
  if (prevMs && (nowMs - prevMs) > 30000) {
    const speakerName = speakerManager.getSpeakerName(speakerId) || speakerId;
    log(`[🔊 AUDIO RESUMED] ${speakerName} — audio arrived after ${((nowMs - prevMs) / 1000).toFixed(0)}s silence`);
  }
  speakerLastAudioMs.set(speakerId, nowMs);

  // Raw capture: dump audio for offline replay
  if (rawCaptureService) {
    const resolvedName = speakerManager.getSpeakerName(speakerId) || '';
    rawCaptureService.feedAudio(speakerIndex, audioData, resolvedName);
  }

  speakerManager.feedAudio(speakerId, audioData);
}

/**
 * Tear down the per-speaker transcription pipeline and release resources.
 */
async function cleanupPerSpeakerPipeline(): Promise<void> {
  // Clear telemetry interval
  if (pipelineTelemetryInterval) {
    clearInterval(pipelineTelemetryInterval);
    pipelineTelemetryInterval = null;
  }

  // Stop browser-side audio capture
  for (const handle of activeSpeakerStreamHandles) {
    try {
      handle.stop();
      handle.cleanup();
    } catch (err: any) {
      log(`[PerSpeaker] Error cleaning up stream handle: ${err.message}`);
    }
  }
  activeSpeakerStreamHandles = [];

  // Clean up browser-side monitoring intervals
  if (page && !page.isClosed()) {
    try {
      await page.evaluate(() => {
        const intervals = (window as any).__vexaPerSpeakerIntervals || [];
        intervals.forEach((id: any) => clearInterval(id));
        (window as any).__vexaPerSpeakerIntervals = [];
      });
    } catch {}
  }

  // Finalize raw capture — flush all tracks to WAV files
  if (rawCaptureService) {
    const outputPath = rawCaptureService.finalize();
    log(`[PerSpeaker] Raw capture finalized → ${outputPath}`);
    rawCaptureService = null;
  }

  // Flush remaining speaker buffers
  if (speakerManager) {
    speakerManager.removeAll();
    speakerManager = null;
  }

  // Flush remaining confirmed batches before session_end
  if (segmentPublisher && confirmedBatches.size > 0) {
    for (const [speakerId, batch] of confirmedBatches) {
      if (batch.length > 0) {
        const speakerName = batch[0].speaker;
        log(`[PerSpeaker] Flushing ${batch.length} confirmed segment(s) for ${speakerName}`);
        await segmentPublisher.publishTranscript(speakerName, batch, []);
      }
    }
    confirmedBatches = new Map();
  }

  // Publish session_end and close Redis connections
  if (segmentPublisher) {
    await segmentPublisher.publishSessionEnd();
    await segmentPublisher.close();
    segmentPublisher = null;
  }

  transcriptionClient = null;
  log('[PerSpeaker] Pipeline cleaned up');
}

/**
 * Handle audio from Teams' single mixed stream, routed by speaker name.
 * Teams has one audio element; the browser routes chunks based on either:
 *   - Caption-driven routing (primary): captions identify speaker with real speech
 *   - DOM blue squares (fallback): voice-level-stream-outline + vdi-frame-occlusion
 * Speaker name is known from DOM/caption events — no voting/locking needed.
 */
async function handleTeamsAudioData(speakerName: string, audioDataArray: number[]): Promise<void> {
  if (!speakerManager || !segmentPublisher || !page || page.isClosed()) return;

  const speakerId = `teams-${speakerName.replace(/\s+/g, '_')}`;
  const audioData = new Float32Array(audioDataArray);

  // Add speaker if new — name is already known from DOM/caption
  if (!speakerManager.hasSpeaker(speakerId)) {
    log(`[🎙️ TEAMS SPEAKER] "${speakerName}" — first audio received`);
    speakerManager.addSpeaker(speakerId, speakerName);
    await segmentPublisher.publishSpeakerEvent({
      speaker: speakerName,
      type: 'joined',
      timestamp: Date.now(),
    });
  }

  // No VAD for Teams — caption-driven routing already gates audio.
  // Small ring buffer chunks are too short for Silero VAD to reliably detect speech.
  speakerManager.feedAudio(speakerId, audioData);
}

/**
 * Handle caption data from Teams live captions.
 * Captions provide speaker-attributed text directly from Teams' ASR.
 * Used for:
 *   1. Speaker boundary detection (triggers ring buffer lookback)
 *   2. Caption text storage alongside audio transcription
 *   3. Future: fuzzy text matching for segment reconciliation
 */
let lastCaptionSpeakerId: string | null = null;
/** Accumulated caption events for speaker-mapper boundaries (Teams only) */
const captionEventLog: { speaker: string; text: string; timestamp: number }[] = [];
/** Latest word timestamps from Whisper (replaced on each submission) */
let latestWhisperWords: { word: string; start: number; end: number; probability: number }[] = [];

async function handleTeamsCaptionData(speakerName: string, captionText: string, timestampMs: number): Promise<void> {
  if (!segmentPublisher || !page || page.isClosed()) return;

  const speakerId = `teams-${speakerName.replace(/\s+/g, '_')}`;

  // When caption speaker changes, flush the PREVIOUS speaker's buffer immediately.
  // This prevents cross-speaker contamination — the old speaker's buffer gets emitted
  // before any of the new speaker's audio leaks into it.
  if (lastCaptionSpeakerId && lastCaptionSpeakerId !== speakerId && speakerManager) {
    log(`[PerSpeaker] Caption speaker change: flushing "${speakerManager.getSpeakerName(lastCaptionSpeakerId) || lastCaptionSpeakerId}" buffer`);
    await speakerManager.flushSpeaker(lastCaptionSpeakerId);
  }
  lastCaptionSpeakerId = speakerId;

  // Accumulate for speaker-mapper boundaries.
  // Store timestamp as session-relative seconds to match Whisper word timestamps
  // (which are offset by bufferStartMs - sessionStartMs). Absolute wall-clock
  // timestamps would be in a completely different domain (~1.7B vs ~200s).
  const sessionRelativeSec = segmentPublisher.sessionStartMs
    ? (timestampMs - segmentPublisher.sessionStartMs) / 1000
    : timestampMs / 1000;
  captionEventLog.push({ speaker: speakerName, text: captionText, timestamp: sessionRelativeSec });

  // Publish caption as a speaker event for downstream consumers
  await segmentPublisher.publishSpeakerEvent({
    speaker: speakerName,
    type: 'started_speaking',
    timestamp: timestampMs,
  });

  log(`[📝 TEAMS CAPTION] "${speakerName}": ${captionText.substring(0, 80)}${captionText.length > 80 ? '...' : ''}`);
}

/**
 * Expose the per-speaker audio callback to the browser and set up
 * per-speaker audio capture inside the page.
 *
 * Called by platform handlers after media elements are available.
 * Google Meet: per-element streams (1 element = 1 participant).
 * Teams: single stream routed by DOM speaker events (handled in recording.ts).
 */
export async function startPerSpeakerAudioCapture(pageToCaptureFrom: Page): Promise<void> {
  if (!speakerManager) {
    log('[PerSpeaker] Pipeline not initialized, skipping audio capture setup');
    return;
  }


  const isTeams = currentPlatform === 'teams';

  // Expose Teams audio callback — browser routes single stream by speaker name
  // and caption callback for caption-driven speaker detection
  if (isTeams) {
    try {
      await pageToCaptureFrom.exposeFunction('__vexaTeamsAudioData', handleTeamsAudioData);
      log('[PerSpeaker] Teams audio callback exposed — browser-side routing via DOM/caption speaker events');
    } catch (err: any) {
      if (!err.message.includes('has been already registered')) {
        log(`[PerSpeaker] Failed to expose Teams audio callback: ${err.message}`);
      }
    }
    try {
      await pageToCaptureFrom.exposeFunction('__vexaTeamsCaptionData', handleTeamsCaptionData);
      log('[PerSpeaker] Teams caption callback exposed — caption text will be stored for reconciliation');
    } catch (err: any) {
      if (!err.message.includes('has been already registered')) {
        log(`[PerSpeaker] Failed to expose Teams caption callback: ${err.message}`);
      }
    }
    // Teams audio routing + caption observer is set up in recording.ts page.evaluate
    return;
  }

  // Google Meet: expose per-element callback and set up per-element streams
  try {
    await pageToCaptureFrom.exposeFunction('__vexaPerSpeakerAudioData', handlePerSpeakerAudioData);
  } catch (err: any) {
    if (!err.message.includes('has been already registered')) {
      log(`[PerSpeaker] Failed to expose audio callback: ${err.message}`);
      return;
    }
  }

  // Set up per-speaker audio streams inside the browser using raw Web Audio API
  const handleCount = await pageToCaptureFrom.evaluate(async () => {
    const TARGET_SAMPLE_RATE = 16000;
    const BUFFER_SIZE = 4096;

    // Find active media elements with audio tracks (retry up to 10 times)
    let mediaElements: HTMLMediaElement[] = [];
    for (let attempt = 0; attempt < 10; attempt++) {
      mediaElements = Array.from(document.querySelectorAll('audio, video')).filter((el: any) =>
        !el.paused &&
        el.srcObject instanceof MediaStream &&
        el.srcObject.getAudioTracks().length > 0
      ) as HTMLMediaElement[];
      if (mediaElements.length > 0) break;
      await new Promise(r => setTimeout(r, 2000));
      (window as any).logBot?.(`[PerSpeaker] No media elements yet, retry ${attempt + 1}/10...`);
    }

    if (mediaElements.length === 0) {
      (window as any).logBot?.('[PerSpeaker] No active media elements with audio found');
      return 0;
    }

    (window as any).logBot?.(`[PerSpeaker] Found ${mediaElements.length} media elements with audio`);

    // Track connected streams by MediaStream ID to avoid double-binding
    const connectedStreamIds = new Set<string>();
    // Track per-stream audio activity for health monitoring
    const streamCallCounts = new Map<number, number>();
    const streamLastActive = new Map<number, number>();
    let nextStreamIndex = 0;

    function connectElement(el: HTMLMediaElement, index: number): boolean {
      try {
        const stream: MediaStream = (el as any).srcObject;
        if (!stream || stream.getAudioTracks().length === 0) return false;
        const streamId = stream.id;
        if (connectedStreamIds.has(streamId)) return false;

        const ctx = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
        const source = ctx.createMediaStreamSource(stream);
        const processor = ctx.createScriptProcessor(BUFFER_SIZE, 1, 1);

        streamCallCounts.set(index, 0);
        streamLastActive.set(index, Date.now());

        processor.onaudioprocess = (e: AudioProcessingEvent) => {
          const data = e.inputBuffer.getChannelData(0);
          streamCallCounts.set(index, (streamCallCounts.get(index) || 0) + 1);
          // Only send if there's actual audio (not silence)
          const maxVal = Math.max(...Array.from(data).map(Math.abs));
          if (maxVal > 0.005) {
            streamLastActive.set(index, Date.now());
            (window as any).__vexaPerSpeakerAudioData(index, Array.from(data));
          }
        };

        source.connect(processor);
        processor.connect(ctx.destination);
        connectedStreamIds.add(streamId);

        // Monitor track ending — log when MediaStreamTrack becomes "ended"
        const track = stream.getAudioTracks()[0];
        track.addEventListener('ended', () => {
          (window as any).logBot?.(`[PerSpeaker] Track ${index} ENDED (streamId=${streamId.substring(0, 12)})`);
          connectedStreamIds.delete(streamId);
        });

        (window as any).logBot?.(`[PerSpeaker] Stream ${index} started (track: ${track.id.substring(0, 12)}, streamId: ${streamId.substring(0, 12)})`);
        return true;
      } catch (err: any) {
        (window as any).logBot?.(`[PerSpeaker] Stream ${index} error: ${err.message}`);
        return false;
      }
    }

    let streamCount = 0;
    for (let i = 0; i < mediaElements.length; i++) {
      if (connectElement(mediaElements[i], i)) streamCount++;
    }
    nextStreamIndex = mediaElements.length;

    // Periodic re-scan: discover new audio elements (late joiners, element recycling)
    const rescanInterval = setInterval(() => {
      const currentElements = Array.from(document.querySelectorAll('audio, video')).filter((el: any) =>
        !el.paused &&
        el.srcObject instanceof MediaStream &&
        el.srcObject.getAudioTracks().length > 0
      ) as HTMLMediaElement[];

      let newStreams = 0;
      for (const el of currentElements) {
        const stream: MediaStream = (el as any).srcObject;
        if (stream && !connectedStreamIds.has(stream.id)) {
          if (connectElement(el, nextStreamIndex)) {
            newStreams++;
            nextStreamIndex++;
          }
        }
      }
      if (newStreams > 0) {
        (window as any).logBot?.(`[PerSpeaker] Re-scan: connected ${newStreams} new stream(s) (total tracked: ${connectedStreamIds.size})`);
      }
    }, 15000);

    // Health monitoring: detect stale streams
    const healthInterval = setInterval(() => {
      const now = Date.now();
      for (const [idx, lastActive] of streamLastActive) {
        const silentMs = now - lastActive;
        if (silentMs > 30000) {
          const calls = streamCallCounts.get(idx) || 0;
          (window as any).logBot?.(`[PerSpeaker] Stream ${idx} silent for ${(silentMs/1000).toFixed(0)}s (onaudioprocess calls: ${calls})`);
        }
      }
    }, 30000);

    // Store intervals for cleanup
    (window as any).__vexaPerSpeakerIntervals = [rescanInterval, healthInterval];

    return streamCount;
  });

  log(`[PerSpeaker] Browser-side audio capture started with ${handleCount} streams`);
}

// ==================================================================
// v0.10.5.3 Pack T — bot resource telemetry (cgroup mem + CPU sampler)
// ==================================================================

interface BotResourceSampler {
  stop: () => void;
  summary: () => {
    samples: number;
    peak_memory_bytes: number | null;
    last_memory_bytes: number | null;
    cpu_usage_usec_total: number | null;
    sampler_started_at: string;
    cgroup_available: boolean;
  };
}

/**
 * Periodically polls /sys/fs/cgroup/memory.current and /sys/fs/cgroup/cpu.stat
 * (cgroup v2). Emits a structured log line per sample. Tracks peak memory
 * + last sample for the exit-callback to attach to bot_resources field.
 *
 * Designed to be safe on hosts where cgroup paths don't exist (dev macOS
 * runs, non-cgroup-v2 systems): all file reads are wrapped in try/catch
 * and the sampler emits a single "cgroup_unavailable" log line then
 * stops sampling. Per Pack P (no fallbacks unless explicitly decided),
 * we do NOT silently swap to a per-process /proc/self/status reader —
 * if cgroup is unavailable the telemetry is unavailable, full stop.
 */
function startBotResourceSampler(): BotResourceSampler {
  const fs = require("fs") as typeof import("fs");
  const sampleIntervalMs = 30_000;
  const startedAt = new Date().toISOString();
  const memoryPath = "/sys/fs/cgroup/memory.current";
  const cpuStatPath = "/sys/fs/cgroup/cpu.stat";
  let samples = 0;
  let peakMemory: number | null = null;
  let lastMemory: number | null = null;
  let cumulativeCpuUsec: number | null = null;
  let cgroupAvailable = false;

  // Probe cgroup availability once at start.
  try {
    fs.statSync(memoryPath);
    fs.statSync(cpuStatPath);
    cgroupAvailable = true;
  } catch {
    logJSON({
      level: "info",
      msg: "[BotResource] cgroup_unavailable — sampler disabled (expected on dev macOS / non-cgroup-v2 hosts)",
      memory_path: memoryPath,
      cpu_stat_path: cpuStatPath,
    });
  }

  const tick = () => {
    if (!cgroupAvailable) return;
    try {
      const memRaw = fs.readFileSync(memoryPath, "utf-8").trim();
      const memBytes = parseInt(memRaw, 10);
      if (!Number.isNaN(memBytes)) {
        lastMemory = memBytes;
        if (peakMemory === null || memBytes > peakMemory) peakMemory = memBytes;
      }
      const cpuStat = fs.readFileSync(cpuStatPath, "utf-8");
      const cpuUsageMatch = cpuStat.match(/usage_usec\s+(\d+)/);
      if (cpuUsageMatch) cumulativeCpuUsec = parseInt(cpuUsageMatch[1], 10);
      samples += 1;
      logJSON({
        level: "debug",
        msg: "[BotResource] sample",
        sample_index: samples,
        memory_bytes: memBytes,
        cpu_usage_usec: cumulativeCpuUsec,
        peak_memory_bytes: peakMemory,
      });
    } catch (err: any) {
      // Per Pack P: no silent fallback. Log and stop sampling on error.
      logJSON({
        level: "warn",
        msg: "[BotResource] sample failed — disabling sampler",
        error_message: err?.message || String(err),
      });
      cgroupAvailable = false;
    }
  };

  // Sample once immediately for a baseline, then on interval.
  tick();
  const handle = setInterval(tick, sampleIntervalMs);

  return {
    stop: () => clearInterval(handle),
    summary: () => ({
      samples,
      peak_memory_bytes: peakMemory,
      last_memory_bytes: lastMemory,
      cpu_usage_usec_total: cumulativeCpuUsec,
      sampler_started_at: startedAt,
      cgroup_available: cgroupAvailable,
    }),
  };
}

// ==================================================================

export async function runBot(botConfig: BotConfig): Promise<void> {// Store botConfig globally for command validation
  (globalThis as any).botConfig = botConfig;

  // --- UPDATED: Parse and store config values ---
  currentLanguage = botConfig.language;
  allowedLanguages = botConfig.allowedLanguages?.length ? botConfig.allowedLanguages : null;
  currentTask = botConfig.transcribeEnabled === false ? null : (botConfig.task || 'transcribe');
  currentRedisUrl = botConfig.redisUrl;
  currentConnectionId = botConfig.connectionId;
  meetingApiCallbackUrl = botConfig.meetingApiCallbackUrl || null; // ADDED: Get callback URL from botConfig
  currentPlatform = botConfig.platform; // Set currentPlatform here
  currentBotConfig = botConfig; // Store full config for recording upload

  // v0.10.5 Pack G.1 (#272 issue 6) — populate the structured-logger
  // context once per-bot from BotConfig. Every subsequent log line
  // (whether emitted via the legacy `log(message)` shim or directly
  // via `logJSON({...})`) automatically carries meeting_id, session_uid,
  // platform, container_name, connection_id. K8s `kubectl logs` capture
  // (Pack G.2) becomes deterministically grep+jq-able from this point.
  setLogContext({
    meeting_id: botConfig.meeting_id,
    session_uid: botConfig.connectionId,
    platform: botConfig.platform,
    container_name: process.env.HOSTNAME || undefined,
    connection_id: botConfig.connectionId,
    bot_name: botConfig.botName,
  });

  // v0.10.5.3 Pack T — bot resource telemetry (cgroup-based mem + CPU sampler).
  // Reads /sys/fs/cgroup/memory.current + /sys/fs/cgroup/cpu.stat every 30s
  // and emits a structured-log line per sample. Pack O ingests these into
  // the per-meeting JSONB so operators can answer "did the bot run out of
  // memory?" without guesswork. Cost: 2 file reads × every 30s ~= 0; the
  // sampler is non-blocking and skips sampling on hosts where cgroup paths
  // don't exist (dev macOS / non-cgroupv2 hosts). Sampler is stored at
  // module scope so performGracefulLeave can fetch the summary at exit.
  currentBotResourceSampler = startBotResourceSampler();

  // Destructure other needed config values
  const { meetingUrl, platform, botName } = botConfig;

  // Initial bot-startup record uses logJSON directly so the operator
  // sees the structured fields (rather than the prefix-parser making
  // its best guess on an unprefixed message).
  logJSON({
    level: "info",
    msg: "Bot startup",
    meeting_url: meetingUrl,
    platform,
    bot_name: botName,
    language: currentLanguage,
    allowed_languages: allowedLanguages ?? null,
    task: currentTask,
    transcribe_enabled: botConfig.transcribeEnabled !== false,
    connection_id: currentConnectionId,
  });

  // Fail fast: meeting_id must be present for control-plane commands
  const meetingId = botConfig.meeting_id;
  if (meetingId === undefined || meetingId === null) {
    log("ERROR: BOT_CONFIG missing required meeting_id. Exiting.");
    process.exit(2);
    return;
  }

  // --- ADDED: Redis Client Setup and Subscription ---
  if (currentRedisUrl && meetingId !== undefined && meetingId !== null) {
    log("Setting up Redis subscriber...");
    try {
      redisSubscriber = createClient({ url: currentRedisUrl });

      redisSubscriber.on('error', (err) => log(`Redis Client Error: ${err}`));
      // ++ ADDED: Log connection events ++
      redisSubscriber.on('connect', () => log('[DEBUG] Redis client connecting...'));
      redisSubscriber.on('ready', () => log('[DEBUG] Redis client ready.'));
      redisSubscriber.on('reconnecting', () => log('[DEBUG] Redis client reconnecting...'));
      redisSubscriber.on('end', () => log('[DEBUG] Redis client connection ended.'));
      // ++++++++++++++++++++++++++++++++++

      await redisSubscriber.connect();
      log(`Connected to Redis at ${currentRedisUrl}`);

      const commandChannel = `bot_commands:meeting:${meetingId}`;
      // Pass the page object when subscribing
      // ++ MODIFIED: Add logging inside subscribe callback ++
      await redisSubscriber.subscribe(commandChannel, (message, channel) => {
          log(`[DEBUG] Redis subscribe callback fired for channel ${channel}.`); // Log before handling
          handleRedisMessage(message, channel, page)
      }); 
      // ++++++++++++++++++++++++++++++++++++++++++++++++
      log(`Subscribed to Redis channel: ${commandChannel}`);

    } catch (err) {
      log(`*** Failed to connect or subscribe to Redis: ${err} ***`);
      // Decide how to handle this - exit? proceed without command support?
      // For now, log the error and proceed without Redis.
      redisSubscriber = null; // Ensure client is null if setup failed
    }
  } else {
    log("Redis URL or meeting_id missing, skipping Redis setup.");
  }
  // -------------------------------------------------

  // For Zoom Web (default): create a per-bot PulseAudio null sink so
  // concurrent bots don't cross-contaminate each other's audio via the
  // shared zoom_sink.monitor. Skip for the native-SDK path.
  if (botConfig.platform === 'zoom'
      && (process.env.ZOOM_WEB === 'true' || process.env.ZOOM_SDK !== 'true')) {
    const sinkName = `bot_sink_${botConfig.meeting_id}`;
    try {
      const moduleId = execSync(
        `pactl load-module module-null-sink sink_name=${sinkName} sink_properties=device.description="BotSink_${botConfig.meeting_id}"`,
        { stdio: ['ignore', 'pipe', 'ignore'] }
      ).toString().trim();
      botPaSinkModuleId = moduleId;
      process.env.PULSE_SINK = sinkName;
      log(`[Bot] Per-bot PulseAudio sink created: ${sinkName} (module ${moduleId})`);
    } catch (e: any) {
      log(`[Bot] Warning: Could not create per-bot PulseAudio sink: ${e.message}. Falling back to shared zoom_sink.`);
    }
  }

  // --- Authenticated bot: use persistent context with userdata from S3 ---
  if (botConfig.authenticated && botConfig.userdataS3Path) {
    log('[Bot] Authenticated mode: downloading userdata from S3...');
    ensureBrowserDataDir();
    syncBrowserDataFromS3(botConfig);
    cleanStaleLocks(BROWSER_DATA_DIR);

    const authArgs = getAuthenticatedBrowserArgs();
    const context: BrowserContext = await chromium.launchPersistentContext(BROWSER_DATA_DIR, {
      headless: false,
      ignoreDefaultArgs: ['--enable-automation'],
      args: authArgs,
      viewport: null,
    });

    log('[Bot] Authenticated persistent context launched');

    // Apply init scripts to the persistent context
    const isVoiceAgent = !!botConfig.voiceAgentEnabled;
    await context.addInitScript(`window.__vexa_voice_agent_enabled = ${isVoiceAgent};`);

    if (botConfig.cameraEnabled) {
      try {
        await context.addInitScript(getVirtualCameraInitScript());
        log('[Bot] Video OUT: virtual camera init script injected (authenticated)');
      } catch (e: any) {
        log(`[Bot] Warning: virtual camera addInitScript failed (authenticated): ${e.message}`);
      }
    }

    if (!botConfig.videoReceiveEnabled) {
      try {
        await context.addInitScript(getVideoBlockInitScript());
        log('[Bot] Video IN: blocked (authenticated, saving CPU)');
      } catch (e: any) {
        log(`[Bot] Warning: video block addInitScript failed (authenticated): ${e.message}`);
      }
    }

    const pages = context.pages();
    page = pages.length > 0 ? pages[0] : await context.newPage();
  }
  // Simple browser setup like simple-bot.js
  else if (botConfig.platform === "teams") {
    // Use shared browser args so Teams gets the same fake-device flags as Google Meet.
    // This ensures Chromium creates a fake video device that enumerateDevices can see,
    // allowing Teams to enable the camera button and our getUserMedia patch to intercept.
    const teamsLaunchArgs = getBrowserArgs(!!botConfig.voiceAgentEnabled);

    try {
      log("Using MS Edge browser for Teams platform");
      // Preferred path: Edge channel
      browserInstance = await chromium.launch({
        headless: false,
        channel: 'msedge',
        args: teamsLaunchArgs
      });
    } catch (edgeLaunchError: any) {
      // Runtime guard: if Edge isn't installed in the image, don't crash the bot process.
      log(`MS Edge launch failed for Teams (${edgeLaunchError?.message || edgeLaunchError}). Falling back to bundled Chromium.`);
      browserInstance = await chromium.launch({
        headless: false,
        args: teamsLaunchArgs
      });
    }
    
    // Create context with CSP bypass to allow script injection (like Google Meet)
    const context = await browserInstance.newContext({
      permissions: ['microphone', 'camera'],
      ignoreHTTPSErrors: true,
      bypassCSP: true,
      viewport: null, // CDP fullscreen removes browser chrome; window fills the 1920x1080 Xvfb display
    });
    
    // Pre-inject browser utils before any page scripts (affects current + future navigations)
    try {
      await context.addInitScript({
        path: require('path').join(__dirname, 'browser-utils.global.js'),
      });
    } catch (e) {
      log(`Warning: context.addInitScript failed: ${(e as any)?.message || e}`);
    }

    // Diagnostic: verify addInitScript works for Teams
    try {
      await context.addInitScript(() => {
        (window as any).__vexa_initscript_test = true;
        console.log('[Vexa] Init script test: running in frame ' + window.location.href);
      });
    } catch {}

    // Set voice agent flag before virtual camera script so it knows
    // whether to disable incoming video tracks (saves ~87% CPU per bot).
    const isVoiceAgentTeams = !!botConfig.voiceAgentEnabled;
    await context.addInitScript(`window.__vexa_voice_agent_enabled = ${isVoiceAgentTeams};`);

    // Video OUT (avatar/camera): controlled by cameraEnabled (default off)
    if (botConfig.cameraEnabled) {
      try {
        await context.addInitScript(getVirtualCameraInitScript());
        log('[Bot] Video OUT: virtual camera init script injected (Teams)');
      } catch (e: any) {
        log(`[Bot] Warning: virtual camera addInitScript failed (Teams): ${e.message}`);
      }
    }

    // Video IN (receive participant video): controlled by videoReceiveEnabled (default off)
    // When off, disables incoming video tracks to save ~87% CPU per bot.
    if (!botConfig.videoReceiveEnabled) {
      try {
        await context.addInitScript(getVideoBlockInitScript());
        log('[Bot] Video IN: blocked (Teams, saving CPU)');
      } catch (e: any) {
        log(`[Bot] Warning: video block addInitScript failed (Teams): ${e.message}`);
      }
    }

    page = await context.newPage();
  } else {
    log("Using Chrome browser for non-Teams platform");
    // Use Stealth Plugin for non-Teams platforms
    const stealthPlugin = StealthPlugin();
    stealthPlugin.enabledEvasions.delete("iframe.contentWindow");
    stealthPlugin.enabledEvasions.delete("media.codecs");
    chromium.use(stealthPlugin);

    browserInstance = await chromium.launch({
      headless: false,
      args: getBrowserArgs(!!botConfig.voiceAgentEnabled),
    });

    // Create a new page with permissions and viewport for non-Teams
    const context = await browserInstance.newContext({
      permissions: ["camera", "microphone"],
      userAgent: userAgent,
      viewport: null, // CDP fullscreen removes browser chrome; window fills the 1920x1080 Xvfb display
    });

    // Set voice agent flag before virtual camera script so it knows
    // whether to disable incoming video tracks (saves ~87% CPU per bot).
    const isVoiceAgent = !!botConfig.voiceAgentEnabled;
    await context.addInitScript(`window.__vexa_voice_agent_enabled = ${isVoiceAgent};`);

    // Video OUT (avatar/camera): controlled by cameraEnabled (default off)
    if (botConfig.cameraEnabled) {
      try {
        await context.addInitScript(getVirtualCameraInitScript());
        log('[Bot] Video OUT: virtual camera init script injected');
      } catch (e: any) {
        log(`[Bot] Warning: virtual camera addInitScript failed: ${e.message}`);
      }
    }

    // Video IN (receive participant video): controlled by videoReceiveEnabled (default off)
    // When off, disables incoming video tracks to save ~87% CPU per bot.
    if (!botConfig.videoReceiveEnabled) {
      try {
        await context.addInitScript(getVideoBlockInitScript());
        log('[Bot] Video IN: blocked (saving CPU)');
      } catch (e: any) {
        log(`[Bot] Warning: video block addInitScript failed: ${e.message}`);
      }
    }

    page = await context.newPage();
  }

  // Forward browser console messages tagged [Vexa] to Node.js log
  // Also capture getUserMedia and RTC-related messages for diagnostics
  page.on('console', (msg) => {
    const text = msg.text();
    if (text.includes('[Vexa]') || text.includes('getUserMedia') || text.includes('RTCPeerConnection') || text.includes('enumerateDevices')) {
      log(`[BrowserConsole] ${text}`);
    }
  });

  // v0.10.5 #284 diagnostic — capture page navigation/crash events that destroy
  // the page.evaluate execution context. Pre-fix the framenavigated handler
  // explicitly EXCLUDED main-frame navigations, hiding the root cause of #284
  // ("page.evaluate: Execution context was destroyed, most likely because of a
  // navigation"). Now we log every navigation, dialog, popup, and crash so the
  // post-incident audit shows EXACTLY what URL/event killed the context.
  page.on('frameattached', (frame) => {
    log(`[Frame] New frame attached: ${frame.url() || '(empty)'}`);
  });
  page.on('framenavigated', (frame) => {
    const isMain = frame === page!.mainFrame();
    log(`[Frame] ${isMain ? 'MAIN-FRAME' : 'sub-frame'} navigated: ${frame.url()}`);
  });
  page.on('crash', () => {
    log(`[Page] !!! TAB CRASHED — Chromium tab process died. Likely OOM or sandbox kill. Last URL: ${page?.url() || '(unknown)'}`);
  });
  page.on('close', () => {
    log(`[Page] PAGE CLOSED — last URL: ${page?.url() || '(unknown)'}`);
  });
  page.on('dialog', async (dialog) => {
    log(`[Page] DIALOG fired (type=${dialog.type()}, msg=${dialog.message()}) — accepting to prevent block`);
    try { await dialog.accept(); } catch { /* dialog already dismissed */ }
  });
  page.on('popup', (popup) => {
    log(`[Page] POPUP opened: ${popup.url()}`);
  });
  page.on('pageerror', (err: Error) => {
    log(`[Page] PAGE-ERROR: ${err.name}: ${err.message}${err.stack ? '\n' + err.stack.split('\n').slice(0, 3).join('\n') : ''}`);
  });
  page.on('requestfailed', (request) => {
    // Only log failed requests on the main GMeet domain — too noisy otherwise
    const url = request.url();
    if (url.includes('meet.google.com') || url.includes('googleusercontent') || url.includes('googleapis')) {
      log(`[Page] REQUEST-FAILED: ${request.method()} ${url} — ${request.failure()?.errorText || 'unknown'}`);
    }
  });

  // --- ADDED: Expose a function for browser to trigger Node.js graceful leave ---
  await page.exposeFunction("triggerNodeGracefulLeave", async () => {
    log("[Node.js] Received triggerNodeGracefulLeave from browser context.");
    if (!isShuttingDown) {
      await performGracefulLeave(page, 0, "self_initiated_leave_from_browser");
    } else {
      log("[Node.js] Ignoring triggerNodeGracefulLeave as shutdown is already in progress.");
    }
  });
  // --- ----------------------------------------------------------------------- ---

  // Setup anti-detection measures
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "webdriver", { get: () => undefined });
    Object.defineProperty(navigator, "plugins", {
      get: () => [{ name: "Chrome PDF Plugin" }, { name: "Chrome PDF Viewer" }],
    });
    Object.defineProperty(navigator, "languages", {
      get: () => ["en-US", "en"],
    });
    Object.defineProperty(navigator, "hardwareConcurrency", { get: () => 4 });
    Object.defineProperty(navigator, "deviceMemory", { get: () => 8 });
    Object.defineProperty(window, "innerWidth", { get: () => 1920 });
    Object.defineProperty(window, "innerHeight", { get: () => 1080 });
    Object.defineProperty(window, "outerWidth", { get: () => 1920 });
    Object.defineProperty(window, "outerHeight", { get: () => 1080 });
  });

  // Virtual camera is controlled by cameraEnabled (independent of voiceAgentEnabled).
  // TTS speaker bots can speak without streaming an avatar.
  if (botConfig.cameraEnabled) {
    try {
      await initVirtualCamera(botConfig, page);
    } catch (err: any) {
      log(`[Bot] Virtual camera initialization failed (non-fatal): ${err.message}`);
    }
  } else {
    log('[Bot] Skipping virtual camera init (camera not enabled)');
  }

  // Always initialize chat service so chat read/write works for every bot
  try {
    await initChatService(botConfig, page);
  } catch (err: any) {
    log(`[Chat] Initialization failed (non-fatal): ${err.message}`);
  }

  // Always initialize TTS + mic so any bot can speak on demand
  if (!ttsPlaybackService) {
    ttsPlaybackService = new TTSPlaybackService();
    log('[TTS] Playback service initialized (available for all bots)');
  }
  if (!microphoneService) {
    microphoneService = new MicrophoneService(page, botConfig.platform);
    log('[Mic] Microphone service initialized (available for all bots)');
  }

  // Initialize full voice agent services (Redis events, etc.) if enabled
  if (botConfig.voiceAgentEnabled && browserInstance) {
    try {
      await initVoiceAgentServices(botConfig, page, browserInstance);
    } catch (err: any) {
      log(`[VoiceAgent] Initialization failed (non-fatal): ${err.message}`);
    }
  }

  // Initialize per-speaker transcription pipeline (Node.js side).
  if (botConfig.transcribeEnabled !== false) {
    try {
      const pipelineReady = await initPerSpeakerPipeline(botConfig);
      if (pipelineReady) {
        log('[Bot] Per-speaker transcription pipeline initialized');
      }
    } catch (err: any) {
      log(`[Bot] Per-speaker pipeline init failed (non-fatal): ${err.message}`);
    }
  } else {
    log('[Bot] Transcription disabled, skipping per-speaker pipeline');
  }

  // Call the appropriate platform handler
  try {
    if (botConfig.platform === "google_meet") {
      await handleGoogleMeet(botConfig, page, performGracefulLeave);
    } else if (botConfig.platform === "zoom") {
      await handleZoom(botConfig, page, performGracefulLeave);
    } else if (botConfig.platform === "teams") {
      await handleMicrosoftTeams(botConfig, page, performGracefulLeave);
    } else {
      log(`Unknown platform: ${botConfig.platform}`);
      await performGracefulLeave(page, 1, "unknown_platform");
    }
  } catch (error: any) {
    log(`Error during platform handling: ${error.message}`);
    await performGracefulLeave(page, 1, "platform_handler_exception");
  }

  // If we reached here without an explicit shutdown (e.g., admission failed path returned, or normal end),
  // force a graceful exit to ensure the container terminates cleanly.
  await performGracefulLeave(page, 0, "normal_completion");
}

// --- Signal Handling with shutdown timeout ---
const SHUTDOWN_TIMEOUT_MS = 30_000; // 30 seconds max for graceful shutdown

const gracefulShutdown = async (signal: string) => {
    log(`Received signal: ${signal}. Triggering graceful shutdown.`);
    if (!isShuttingDown) {
        const pageToClose = typeof page !== 'undefined' ? page : null;

        // Race cleanup against a hard timeout to prevent zombie processes
        const timeoutPromise = new Promise<void>((_, reject) =>
            setTimeout(() => reject(new Error("Shutdown timeout exceeded")), SHUTDOWN_TIMEOUT_MS)
        );

        try {
            await Promise.race([
                performGracefulLeave(pageToClose, signal === 'SIGINT' ? 130 : 143, `signal_${signal.toLowerCase()}`),
                timeoutPromise,
            ]);
        } catch (err: any) {
            log(`[Signal Shutdown] ${err.message} — forcing exit`);
            process.exit(1);
        }
    } else {
         log("[Signal Shutdown] Shutdown already in progress.");
         // If already shutting down but signal received again, force exit after timeout
         setTimeout(() => {
             log("[Signal Shutdown] Second signal timeout — forcing exit");
             process.exit(1);
         }, SHUTDOWN_TIMEOUT_MS);
    }
};

process.on('SIGTERM', () => gracefulShutdown('SIGTERM'));
process.on('SIGINT', () => gracefulShutdown('SIGINT'));
// --- ------------------------------------------------- ---
