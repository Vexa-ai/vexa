import StealthPlugin from "puppeteer-extra-plugin-stealth";
import { log } from "./utils";
import { callStatusChangeCallback, mapExitReasonToStatus } from "./services/unified-callback";
import { chromium } from "playwright-extra";
import { handleGoogleMeet, leaveGoogleMeet } from "./platforms/googlemeet";
import { handleMicrosoftTeams, leaveMicrosoftTeams } from "./platforms/msteams";
import { handleZoom, leaveZoom } from "./platforms/zoom";
import { browserArgs, getBrowserArgs, userAgent } from "./constans";
import { BotConfig } from "./types";
import { RecordingService } from "./services/recording";
import { TTSPlaybackService } from "./services/tts-playback";
import { MicrophoneService } from "./services/microphone";
import { MeetingChatService, ChatTranscriptConfig } from "./services/chat";
import { ScreenContentService, getVirtualCameraInitScript } from "./services/screen-content";
import { ScreenShareService } from "./services/screen-share"; // kept for Teams; unused for Google Meet camera-feed approach
import { createClient, RedisClientType } from 'redis';
import { Page, Browser } from 'playwright-core';
// HTTP imports removed - using unified callback service instead

// Module-level variables to store current configuration
let currentLanguage: string | null | undefined = null;
let currentTask: string | null | undefined = 'transcribe'; // Default task
let currentRedisUrl: string | null = null;
let currentConnectionId: string | null = null;
let botManagerCallbackUrl: string | null = null; // ADDED: To store callback URL
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
let currentBotConfig: BotConfig | null = null;
export function setActiveRecordingService(svc: RecordingService | null): void {
  activeRecordingService = svc;
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
  log(`[DEBUG] handleRedisMessage entered for channel ${channel}. Message: ${message.substring(0, 100)}...`);
  // ++++++++++++++++++++++++++++++++++
  log(`Received command on ${channel}: ${message}`);
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
          log(`Processing reconfigure command: Lang=${command.language}, Task=${command.task}`);

          // Update Node.js state
          currentLanguage = command.language;
          currentTask = command.task;

          // Trigger browser-side reconfiguration via the exposed function
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
      } else if (command.action === 'leave') {
        // Mark that a stop was requested via Redis
        stopSignalReceived = true;
        // TODO: Implement leave logic (Phase 4)
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
    log("[Graceful Leave] Already in progress, ignoring duplicate call.");
    return;
  }
  isShuttingDown = true;
  log(`[Graceful Leave] Initiating graceful shutdown sequence... Reason: ${reason}, Exit Code: ${exitCode}`);

  let platformLeaveSuccess = false;

  // Handle SDK-based platforms (Zoom) separately - they don't use Playwright page
  if (currentPlatform === "zoom") {
    try {
      log("[Graceful Leave] Attempting Zoom SDK cleanup...");
      platformLeaveSuccess = await leaveZoom(null); // Zoom doesn't use page
    } catch (error: any) {
      log(`[Graceful Leave] Zoom cleanup error: ${error.message}`);
      platformLeaveSuccess = false;
    }
  } else if (page && !page.isClosed()) { // Browser-based platforms (Google Meet, Teams)
    try {
      log("[Graceful Leave] Attempting platform-specific leave...");
      if (currentPlatform === "google_meet") {
         platformLeaveSuccess = await leaveGoogleMeet(page);
      } else if (currentPlatform === "teams") {
         platformLeaveSuccess = await leaveMicrosoftTeams(page);
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

  // Upload recording if available
  if (activeRecordingService && currentBotConfig?.recordingUploadUrl && currentBotConfig?.token) {
    try {
      log("[Graceful Leave] Uploading recording to bot-manager...");
      await activeRecordingService.upload(currentBotConfig.recordingUploadUrl, currentBotConfig.token);
      log("[Graceful Leave] Recording uploaded successfully.");
    } catch (uploadError: any) {
      log(`[Graceful Leave] Recording upload failed: ${uploadError.message}`);
    } finally {
      await activeRecordingService.cleanup();
      activeRecordingService = null;
    }
  }

  // Determine final exit code. If the initial intent was a successful exit (code 0),
  // it should always be 0. For error cases (non-zero exit codes), preserve the original error code.
  const finalCallbackExitCode = (exitCode === 0) ? 0 : exitCode;
  const finalCallbackReason = reason;

  if (botManagerCallbackUrl && currentConnectionId) {
    // Use unified callback for exit status
    const statusMapping = mapExitReasonToStatus(finalCallbackReason, finalCallbackExitCode);
    
    const botConfig = {
      botManagerCallbackUrl,
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
        statusMapping.failureStage
      );
      log(`[Graceful Leave] Unified exit callback sent successfully`);
    } catch (callbackError: any) {
      log(`[Graceful Leave] Error sending unified exit callback: ${callbackError.message}`);
    }
  } else {
    log("[Graceful Leave] Bot manager callback URL or Connection ID not configured. Cannot send exit status.");
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
  log(`[Graceful Leave] Exiting process with code ${finalCallbackExitCode} (Reason: ${finalCallbackReason}).`);
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
  try {
    await redisPublisher.publish(
      `va:meeting:${meetingId}:events`,
      JSON.stringify({ event, meeting_id: meetingId, ...data, ts: new Date().toISOString() })
    );
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
    const provider = command.provider || process.env.DEFAULT_TTS_PROVIDER || 'openai';
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
      await ttsPlaybackService.playFromUrl(command.audio_url);
    } else if (command.audio_base64) {
      const format = command.format || 'wav';
      const sampleRate = command.sample_rate || 24000;
      await ttsPlaybackService.playFromBase64(command.audio_base64, format, sampleRate);
    } else {
      log('[SpeakAudio] No audio_url or audio_base64 provided');
      return;
    }
    await publishVoiceEvent('speak.completed');
  } catch (err: any) {
    log(`[SpeakAudio] Playback failed: ${err.message}`);
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

// ==================================================================

export async function runBot(botConfig: BotConfig): Promise<void> {// Store botConfig globally for command validation
  (globalThis as any).botConfig = botConfig;
  
  // --- UPDATED: Parse and store config values ---
  currentLanguage = botConfig.language;
  currentTask = botConfig.transcribeEnabled === false ? null : (botConfig.task || 'transcribe');
  currentRedisUrl = botConfig.redisUrl;
  currentConnectionId = botConfig.connectionId;
  botManagerCallbackUrl = botConfig.botManagerCallbackUrl || null; // ADDED: Get callback URL from botConfig
  currentPlatform = botConfig.platform; // Set currentPlatform here
  currentBotConfig = botConfig; // Store full config for recording upload

  // Destructure other needed config values
  const { meetingUrl, platform, botName } = botConfig;

  log(
    `Starting bot for ${platform} with URL: ${meetingUrl}, name: ${botName}, language: ${currentLanguage}, ` +
    `task: ${currentTask}, transcribeEnabled: ${botConfig.transcribeEnabled !== false}, connectionId: ${currentConnectionId}`
  );

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

  // Simple browser setup like simple-bot.js
  if (botConfig.platform === "teams") {
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
      bypassCSP: true
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

    // Inject virtual camera init script for avatar display
    try {
      await context.addInitScript(getVirtualCameraInitScript());
      log('[Bot] Virtual camera init script injected (Teams)');
    } catch (e: any) {
      log(`[Bot] Warning: addInitScript failed (Teams): ${e.message}`);
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
      viewport: {
        width: 1280,
        height: 720
      }
    });

    // Inject virtual camera RTCPeerConnection patch BEFORE page loads
    // so Google Meet gets our canvas stream from the start.
    // Always inject — the avatar should show regardless of voice agent state.
    try {
      await context.addInitScript(getVirtualCameraInitScript());
      log('[Bot] Virtual camera init script injected');
    } catch (e: any) {
      log(`[Bot] Warning: addInitScript failed: ${e.message}`);
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

  // Monitor frames for WebRTC usage (Teams may use iframes)
  page.on('frameattached', (frame) => {
    log(`[Frame] New frame attached: ${frame.url() || '(empty)'}`);
  });
  page.on('framenavigated', (frame) => {
    if (frame !== page!.mainFrame()) {
      log(`[Frame] Sub-frame navigated: ${frame.url()}`);
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

  // Always initialize virtual camera and avatar display
  try {
    await initVirtualCamera(botConfig, page);
  } catch (err: any) {
    log(`[Bot] Virtual camera initialization failed (non-fatal): ${err.message}`);
  }

  // Always initialize chat service so chat read/write works for every bot
  try {
    await initChatService(botConfig, page);
  } catch (err: any) {
    log(`[Chat] Initialization failed (non-fatal): ${err.message}`);
  }

  // Always initialize TTS playback so speak commands work for all bots
  if (!ttsPlaybackService) {
    ttsPlaybackService = new TTSPlaybackService();
    log('[TTS] Playback service initialized (available for all bots)');
  }

  // Initialize full voice agent services (mic, Redis events, etc.) if enabled
  if (botConfig.voiceAgentEnabled && browserInstance) {
    try {
      await initVoiceAgentServices(botConfig, page, browserInstance);
    } catch (err: any) {
      log(`[VoiceAgent] Initialization failed (non-fatal): ${err.message}`);
    }
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

// --- ADDED: Basic Signal Handling (for future Phase 5) ---
// Setup signal handling to also trigger graceful leave
const gracefulShutdown = async (signal: string) => {
    log(`Received signal: ${signal}. Triggering graceful shutdown.`);
    if (!isShuttingDown) {
        // Determine the correct page instance if multiple are possible, or use a global 'currentPage'
        // For now, assuming 'page' (if defined globally/module-scoped) or null
        const pageToClose = typeof page !== 'undefined' ? page : null;
        await performGracefulLeave(pageToClose, signal === 'SIGINT' ? 130 : 143, `signal_${signal.toLowerCase()}`);
    } else {
         log("[Signal Shutdown] Shutdown already in progress.");
    }
};

process.on('SIGTERM', () => gracefulShutdown('SIGTERM'));
process.on('SIGINT', () => gracefulShutdown('SIGINT'));
// --- ------------------------------------------------- ---
