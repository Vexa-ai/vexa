import { Page } from "playwright";
import { log, randomDelay } from "../utils";
import { BotConfig } from "../types";
import { v4 as uuidv4 } from "uuid"; // Import UUID
import { BotCallbacks } from "../gateways/BotCallbacks";

// --- ADDED: Function to generate UUID (if not already present globally) ---
// If you have a shared utils file for this, import from there instead.
function generateUUID() {
  return uuidv4();
}

export async function handleGoogleMeet(
  botConfig: BotConfig,
  page: Page,
  gracefulLeaveFunction: (
    page: Page | null,
    exitCode: number,
    reason: string
  ) => Promise<void>,
  botCallbacks?: BotCallbacks
): Promise<void> {
  const leaveButton = `//button[@aria-label="Leave call"]`;

  if (!botConfig.meetingUrl) {
    log("Error: Meeting URL is required for Google Meet but is null.");
    // If meeting URL is missing, we can't join, so trigger graceful leave.
    await gracefulLeaveFunction(page, 1, "missing_meeting_url");
    return;
  }

  log("Joining Google Meet");
  try {
    await joinMeeting(page, botConfig.meetingUrl, botConfig.botName);
  } catch (error: any) {
    console.error("Error during joinMeeting: " + error.message);
    log(
      "Error during joinMeeting: " +
        error.message +
        ". Triggering graceful leave."
    );
    await gracefulLeaveFunction(page, 1, "join_meeting_error");
    return;
  }

  // Setup websocket connection and meeting admission concurrently
  log("Starting WebSocket connection while waiting for meeting admission");
  try {
    // Run both processes concurrently
    const [isAdmitted] = await Promise.all([
      // Wait for admission to the meeting
      waitForMeetingAdmission(
        page,
        leaveButton,
        botConfig.automaticLeave.waitingRoomTimeout
      ).catch((error) => {
        log("Meeting admission failed: " + error.message);
        return false;
      }),

      // Prepare for recording (expose functions, etc.) while waiting for admission
      prepareForRecording(page),
    ]);

    if (!isAdmitted) {
      console.error("Bot was not admitted into the meeting");
      log(
        "Bot not admitted. Triggering graceful leave with admission_failed reason."
      );

      await gracefulLeaveFunction(page, 2, "admission_failed");
      return;
    }

    log("Successfully admitted to the meeting, starting recording");
    // Pass platform from botConfig to startRecording
    await startRecording(page, botConfig, botCallbacks);
  } catch (error: any) {
    console.error(
      "Error after join attempt (admission/recording setup): " + error.message
    );
    log(
      "Error after join attempt (admission/recording setup): " +
        error.message +
        ". Triggering graceful leave."
    );
    // Use a general error code here, as it could be various issues.
    await gracefulLeaveFunction(page, 1, "post_join_setup_error");
    return;
  }
}

// New function to wait for meeting admission
const waitForMeetingAdmission = async (
  page: Page,
  leaveButton: string,
  timeout: number
): Promise<boolean> => {
  try {
    await page.waitForSelector(leaveButton, { timeout });
    log("Successfully admitted to the meeting");
    return true;
  } catch {
    throw new Error(
      "Bot was not admitted into the meeting within the timeout period"
    );
  }
};

// Prepare for recording by exposing necessary functions
const prepareForRecording = async (page: Page): Promise<void> => {
  // Expose the logBot function to the browser context
  await page.exposeFunction("logBot", (msg: string) => {
    log(msg);
  });
};

const joinMeeting = async (page: Page, meetingUrl: string, botName: string) => {
  const enterNameField = 'input[type="text"][aria-label="Your name"]';
  const joinButton = '//button[.//span[text()="Ask to join"]]';
  const muteButton = '[aria-label*="Turn off microphone"]';
  const cameraOffButton = '[aria-label*="Turn off camera"]';

  await page.goto(meetingUrl, { waitUntil: "networkidle" });
  await page.bringToFront();

  // Add a longer, fixed wait after navigation for page elements to settle
  log("Waiting for page elements to settle after navigation...");
  await page.waitForTimeout(5000); // Wait 5 seconds

  // Enter name and join
  // Keep the random delay before interacting, but ensure page is settled first
  await page.waitForTimeout(randomDelay(1000));
  log("Attempting to find name input field...");
  // Increase timeout drastically
  await page.waitForSelector(enterNameField, { timeout: 120000 }); // 120 seconds
  log("Name input field found.");

  await page.waitForTimeout(randomDelay(1000));
  await page.fill(enterNameField, botName);

  // Mute mic and camera if available
  try {
    await page.waitForTimeout(randomDelay(500));
    await page.click(muteButton, { timeout: 200 });
    await page.waitForTimeout(200);
  } catch (e) {
    log("Microphone already muted or not found.");
  }
  try {
    await page.waitForTimeout(randomDelay(500));
    await page.click(cameraOffButton, { timeout: 200 });
    await page.waitForTimeout(200);
  } catch (e) {
    log("Camera already off or not found.");
  }

  await page.waitForSelector(joinButton, { timeout: 60000 });
  await page.click(joinButton);
  log(`${botName} joined the Meeting.`);
};

// Modified to have only the actual recording functionality
const startRecording = async (
  page: Page,
  botConfig: BotConfig,
  botCallbacks?: BotCallbacks
) => {
  // Destructure needed fields from botConfig
  const { meetingUrl, token, connectionId, platform, nativeMeetingId } =
    botConfig; // nativeMeetingId is now in BotConfig type

  log(`startRecording : ${botCallbacks}`);

  const videoFilePath = await page.video()?.path();
  if (videoFilePath) {
    await botCallbacks?.onStartRecording(videoFilePath, botConfig.connectionId);
  }

  //NOTE: The environment variables passed by docker_utils.py will be available to the Node.js process started by your entrypoint.sh.
  // --- Read WHISPER_LIVE_URL from Node.js environment ---
  const whisperLiveUrlFromEnv = process.env.WHISPER_LIVE_URL;

  if (!whisperLiveUrlFromEnv) {
    // Use the Node-side 'log' utility here
    log(
      "ERROR: WHISPER_LIVE_URL environment variable is not set for vexa-bot in its Node.js environment. Cannot start recording."
    );
    // Potentially throw an error or return to prevent further execution
    // For example: throw new Error("WHISPER_LIVE_URL is not configured for the bot.");
    return; // Or handle more gracefully
  }
  log(`[Node.js] WHISPER_LIVE_URL for vexa-bot is: ${whisperLiveUrlFromEnv}`);
  // --- ------------------------------------------------- ---

  log("Starting actual recording with WebSocket connection");

  // Read exit logic configuration from environment variables (with fallbacks)
  const exitLogicConfig = {
    speechActivationThresholdSeconds: process.env
      .SPEECH_ACTIVATION_THRESHOLD_SECONDS
      ? parseInt(process.env.SPEECH_ACTIVATION_THRESHOLD_SECONDS, 10)
      : 5,
    deadMeetingTimeoutSeconds: process.env.DEAD_MEETING_TIMEOUT_SECONDS
      ? parseInt(process.env.DEAD_MEETING_TIMEOUT_SECONDS, 10)
      : 5 * 60,
    absoluteSilenceTimeoutSeconds: process.env.ABSOLUTE_SILENCE_TIMEOUT_SECONDS
      ? parseInt(process.env.ABSOLUTE_SILENCE_TIMEOUT_SECONDS, 10)
      : 10 * 60,
    recentSpeechThresholdSeconds: process.env.RECENT_SPEECH_THRESHOLD_SECONDS
      ? parseInt(process.env.RECENT_SPEECH_THRESHOLD_SECONDS, 10)
      : 2 * 60,
    silentParticipantsCountdownSeconds: process.env
      .SILENT_PARTICIPANTS_COUNTDOWN_SECONDS
      ? parseInt(process.env.SILENT_PARTICIPANTS_COUNTDOWN_SECONDS, 10)
      : 3 * 60,
  };

  // Pass the necessary config fields and the resolved URL into the page context. Inisde page.evalute we have the browser context.
  //All code inside page.evalute executes as javascript running in the browser.
  await page.evaluate(
    async (pageArgs: {
      botConfigData: BotConfig;
      whisperUrlForBrowser: string;
      exitLogicConfig: {
        speechActivationThresholdSeconds: number;
        deadMeetingTimeoutSeconds: number;
        absoluteSilenceTimeoutSeconds: number;
        recentSpeechThresholdSeconds: number;
        silentParticipantsCountdownSeconds: number;
      };
    }) => {
      const { botConfigData, whisperUrlForBrowser, exitLogicConfig } = pageArgs;
      // Destructure from botConfigData as needed
      const {
        meetingUrl,
        token,
        connectionId: originalConnectionId,
        platform,
        nativeMeetingId,
        language: initialLanguage,
        task: initialTask,
      } = botConfigData; // Use the nested botConfigData

      // --- ADD Helper function to generate UUID in browser context ---
      const generateUUID = () => {
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
      };
      // --- --------------------------------------------------------- ---

      await new Promise<void>((resolve, reject) => {
        try {
          (window as any).logBot("Starting recording process.");

          // --- ADDED: More robust media element finding function ---
          const findMediaElements = async (
            retries = 5,
            delay = 2000
          ): Promise<HTMLMediaElement[]> => {
            for (let i = 0; i < retries; i++) {
              const mediaElements = Array.from(
                document.querySelectorAll("audio, video")
              ).filter(
                (el: any) =>
                  !el.paused &&
                  el.srcObject instanceof MediaStream &&
                  el.srcObject.getAudioTracks().length > 0
              ) as HTMLMediaElement[];

              if (mediaElements.length > 0) {
                (window as any).logBot(
                  `Found ${
                    mediaElements.length
                  } active media elements with audio tracks after ${
                    i + 1
                  } attempt(s).`
                );
                return mediaElements;
              }
              (window as any).logBot(
                `[Audio] No active media elements found. Retrying in ${delay}ms... (Attempt ${
                  i + 2
                }/${retries})`
              );
              await new Promise((resolve) => setTimeout(resolve, delay));
            }
            return [];
          };
          // --- END FUNCTION ---

          findMediaElements()
            .then((mediaElements) => {
              if (mediaElements.length === 0) {
                return reject(
                  new Error(
                    "[BOT Error] No active media elements found after multiple retries. Ensure the meeting media is playing."
                  )
                );
              }

              // NEW: Create audio context and destination for mixing multiple streams
              (window as any).logBot(
                `Found ${mediaElements.length} active media elements.`
              );
              const audioContext = new AudioContext();
              const destinationNode =
                audioContext.createMediaStreamDestination();
              let sourcesConnected = 0;

              // NEW: Connect all media elements to the destination node
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
                    const sourceNode =
                      audioContext.createMediaStreamSource(elementStream);
                    sourceNode.connect(destinationNode);
                    sourcesConnected++;
                    (window as any).logBot(
                      `Connected audio stream from element ${index + 1}/${
                        mediaElements.length
                      }.`
                    );
                  }
                } catch (error: any) {
                  (window as any).logBot(
                    `Could not connect element ${index + 1}: ${error.message}`
                  );
                }
              });

              if (sourcesConnected === 0) {
                return reject(
                  new Error(
                    "[BOT Error] Could not connect any audio streams. Check media permissions."
                  )
                );
              }

              // Use the combined stream instead of a single element's stream
              const stream = destinationNode.stream;
              (window as any).logBot(
                `Successfully combined ${sourcesConnected} audio streams.`
              );

              // --- NEW: Start MediaRecorder to save audio using the COMBINED stream ---
              try {
                const audioRecorder = new MediaRecorder(stream, {
                  mimeType: "audio/webm",
                });

                audioRecorder.ondataavailable = async (event: BlobEvent) => {
                  if (event.data.size > 0) {
                    const reader = new FileReader();
                    reader.onloadend = () => {
                      const base64 = (reader.result as string).split(",")[1];
                      (window as any).onAudioChunk(base64);
                    };
                    reader.readAsDataURL(event.data);
                  }
                };

                audioRecorder.start(1000); // Get audio chunk every 1 second
                (window as any).logBot(
                  "[AudioRecord] MediaRecorder for audio saving has started."
                );

                // Stop recorder on leave
                const originalLeave = (window as any).triggerNodeGracefulLeave;
                (window as any).triggerNodeGracefulLeave = async () => {
                  (window as any).logBot(
                    `triggerNodeGracefulLeave for connection ID: ${originalConnectionId}`
                  );

                  if (audioRecorder.state === "recording") {
                    audioRecorder.stop();
                    (window as any).logBot(
                      "[AudioRecord] MediaRecorder for audio saving has stopped."
                    );
                  }

                  try {
                    await botCallbacks?.onMeetingEnd(originalConnectionId);
                  } catch (error) {
                    (window as any).logBot(
                      `triggerNodeGracefulLeave error: ${error}`
                    );
                  } finally {
                    originalLeave();
                  }
                };
              } catch (err: any) {
                (window as any).logBot(
                  `[AudioRecord] Error starting MediaRecorder: ${err.message}`
                );
                // Don't block transcription if audio fails
              }
              // --- END NEW ---

              let socket = new WebSocket(whisperUrlForBrowser);
              socket.binaryType = "arraybuffer";

              // --- MODIFIED: Keep original connectionId but don't use it for WebSocket UID ---
              const sessionUid = generateUUID(); // UID for whisper, separate from connectionId
              (window as any).logBot(
                `Original bot connection ID: ${originalConnectionId}`
              );
              // --- ------------------------------------------------------------------------ ---

              // --- ADDED: Add secondary leave button selector for confirmation ---
              const secondaryLeaveButtonSelector = `//button[.//span[text()='Leave meeting']] | //button[.//span[text()='Just leave the meeting']]`; // Example, adjust based on actual UI
              // --- ----------------------------------------------------------- ---

              // const wsUrl = "ws://whisperlive:9090";
              // (window as any).logBot(`Attempting to connect WebSocket to: ${wsUrl} with platform: ${platform}, session UID: ${sessionUid}`); // Log the correct UID

              // --- ADD Browser-scope state for current WS config ---
              let currentWsLanguage = initialLanguage;
              let currentWsTask = initialTask;
              // --- -------------------------------------------- ---

              let isServerReady = false;
              let retryCount = 0;
              const configuredInterval = botConfigData.reconnectionIntervalMs;
              const baseRetryDelay =
                configuredInterval && configuredInterval <= 1000
                  ? configuredInterval
                  : 1000; // Use configured if <= 1s, else 1s

              let sessionAudioStartTimeMs: number | null = null; // ADDED: For relative speaker timestamps

              // Speech activity tracking variables for intelligent meeting end detection
              let meetingHasHadSpeech = false; // Tracks if anyone has spoken during the meeting
              let lastSpeechTime: number | null = null; // Timestamp of last speech activity
              let spokenSpeakers = new Set<string>(); // Participant IDs of people who have spoken (to handle name capitalization variations)
              let silenceCountdown = 0; // Case 4 countdown timer in seconds (0 = not active)
              let meetingJoinTime = Date.now(); // When bot joined the meeting
              let isInSilenceCountdown = false; // Flag to prevent multiple countdown starts
              let currentlySpeakingParticipants = new Set<string>(); // Names of participants currently speaking (from SPEAKER_START events)
              let speakerDurationCollector = new Map<string, number>(); // Speaker ID -> accumulated speaking duration in seconds
              let processedSegments = new Set<string>(); // Track processed segments to avoid double-counting (key: "startSec,endSec")
              let activeSpeakerStarts = new Map<string, number>(); // Speaker ID -> SPEAKER_START timestamp (ms) for currently speaking participants
              let speakerIdToNameMap = new Map<string, string>(); // Speaker ID -> name (persistent, never cleared, to show names even after participants leave)

              // Local storage for speaker events with timestamps (for time-based overlap matching)
              interface SpeakerEvent {
                event_type: "SPEAKER_START" | "SPEAKER_END";
                participant_name: string;
                participant_id_meet: string;
                relative_client_timestamp_ms: number;
              }
              let localSpeakerEvents: SpeakerEvent[] = []; // Array of events sorted by timestamp

              // Configuration: Minimum accumulated speaking duration (in seconds) required to mark meeting as having had speech
              const SPEECH_ACTIVATION_THRESHOLD_SECONDS =
                exitLogicConfig.speechActivationThresholdSeconds;

              // Configuration: Timeout (in seconds) for dead meeting detection (no speech detected after bot joins)
              const DEAD_MEETING_TIMEOUT_SECONDS =
                exitLogicConfig.deadMeetingTimeoutSeconds;

              // Configuration: Absolute silence timeout (in seconds) - if no speech for this long, leave regardless of participants
              const ABSOLUTE_SILENCE_TIMEOUT_SECONDS =
                exitLogicConfig.absoluteSilenceTimeoutSeconds;

              // Configuration: Recent speech threshold (in seconds) - if speech occurred within this time, meeting is considered active
              const RECENT_SPEECH_THRESHOLD_SECONDS =
                exitLogicConfig.recentSpeechThresholdSeconds;

              // Configuration: Silent participants countdown (in seconds) - Case 4 countdown duration when all remaining participants are silent
              const SILENT_PARTICIPANTS_COUNTDOWN_SECONDS =
                exitLogicConfig.silentParticipantsCountdownSeconds;

              // Configuration: Buffer time (in ms) for fetching speaker events around segment time range
              const SPEAKER_EVENT_BUFFER_MS = 500;

              function setsEqual<T>(a: Set<T>, b: Set<T>): boolean {
                if (a.size !== b.size) return false;
                for (const x of a) if (!b.has(x)) return false;
                return true;
              }

              // Check if set A is a subset of set B (all elements of A are in B)
              function isSubset<T>(subset: Set<T>, superset: Set<T>): boolean {
                for (const x of subset) {
                  if (!superset.has(x)) return false;
                }
                return true;
              }

              // Helper function to get speaker name from ID using activeParticipants map
              function getSpeakerNameFromId(speakerId: string): string | null {
                const participant = activeParticipants.get(speakerId);
                return participant ? participant.name : null;
              }

              // Time-based overlap matching function (similar to speaker_mapper.py)
              function mapSpeakerToSegment(
                segmentStartMs: number,
                segmentEndMs: number
              ): { speakerName: string | null; speakerId: string | null } {
                if (
                  !sessionAudioStartTimeMs ||
                  localSpeakerEvents.length === 0
                ) {
                  return { speakerName: null, speakerId: null };
                }

                // Find candidate speakers whose active periods overlap with the segment
                const candidateSpeakers = new Map<string, SpeakerEvent>(); // participant_id -> last START event

                // Filter events in the relevant time range
                const minTime = segmentStartMs - SPEAKER_EVENT_BUFFER_MS;
                const maxTime = segmentEndMs + SPEAKER_EVENT_BUFFER_MS;

                for (const event of localSpeakerEvents) {
                  const eventTs = event.relative_client_timestamp_ms;

                  // Skip events outside our time range
                  if (eventTs < minTime || eventTs > maxTime) continue;

                  if (event.event_type === "SPEAKER_START") {
                    // If START is before or at segment end, could be speaking during segment
                    if (eventTs <= segmentEndMs) {
                      candidateSpeakers.set(event.participant_id_meet, event);
                    }
                  } else if (event.event_type === "SPEAKER_END") {
                    // If END is before segment starts, remove from candidates
                    if (
                      eventTs < segmentStartMs &&
                      candidateSpeakers.has(event.participant_id_meet)
                    ) {
                      candidateSpeakers.delete(event.participant_id_meet);
                    }
                  }
                }

                // Calculate overlap for each candidate
                const activeSpeakers: Array<{
                  name: string;
                  id: string;
                  overlapDuration: number;
                }> = [];

                for (const [
                  participantId,
                  startEvent,
                ] of candidateSpeakers.entries()) {
                  const startTs = startEvent.relative_client_timestamp_ms;

                  // Find corresponding END event (or use segment end as default)
                  let endTs = segmentEndMs; // Default to segment end if no END event found
                  for (const event of localSpeakerEvents) {
                    if (
                      (event.participant_id_meet === participantId ||
                        event.participant_name === participantId) &&
                      event.event_type === "SPEAKER_END" &&
                      event.relative_client_timestamp_ms >= startTs
                    ) {
                      endTs = event.relative_client_timestamp_ms;
                      break;
                    }
                  }

                  // Calculate overlap
                  const overlapStart = Math.max(startTs, segmentStartMs);
                  const overlapEnd = Math.min(endTs, segmentEndMs);

                  if (overlapStart < overlapEnd) {
                    activeSpeakers.push({
                      name: startEvent.participant_name,
                      id: participantId,
                      overlapDuration: overlapEnd - overlapStart,
                    });
                  }
                }

                // Select speaker with longest overlap (or single speaker if only one)
                if (activeSpeakers.length === 0) {
                  return { speakerName: null, speakerId: null };
                } else if (activeSpeakers.length === 1) {
                  return {
                    speakerName: activeSpeakers[0].name,
                    speakerId: activeSpeakers[0].id,
                  };
                } else {
                  // Multiple speakers - choose the one with longest overlap
                  activeSpeakers.sort(
                    (a, b) => b.overlapDuration - a.overlapDuration
                  );
                  return {
                    speakerName: activeSpeakers[0].name,
                    speakerId: activeSpeakers[0].id,
                  };
                }
              }

              // Intelligent meeting end detection logic
              const shouldLeaveMeeting = (
                participantCount: number
              ): { shouldLeave: boolean; reason: string } => {
                const now = Date.now();
                const timeSinceJoin = now - meetingJoinTime;

                // Case 1: Dead meeting detection (never had speech)
                if (!meetingHasHadSpeech) {
                  if (timeSinceJoin > DEAD_MEETING_TIMEOUT_SECONDS * 1000) {
                    return {
                      shouldLeave: true,
                      reason:
                        "Dead meeting - no speech detected since bot joined",
                    };
                  }
                  return {
                    shouldLeave: false,
                    reason:
                      "Waiting for first speech activity to activate meeting",
                  };
                }

                // Case 2: Meeting had speech, now checking for end conditions
                if (!lastSpeechTime) {
                  return {
                    shouldLeave: false,
                    reason: "Meeting had speech but timing data unavailable",
                  };
                }

                const timeSinceLastSpeech = now - lastSpeechTime;
                const recentSpeechThresholdMs =
                  RECENT_SPEECH_THRESHOLD_SECONDS * 1000;
                const absoluteSilenceTimeoutMs =
                  ABSOLUTE_SILENCE_TIMEOUT_SECONDS * 1000;

                // Case 3: Recent speech activity - meeting is active
                if (timeSinceLastSpeech < recentSpeechThresholdMs) {
                  return {
                    shouldLeave: false,
                    reason: `Meeting is active - speech detected ${Math.round(
                      timeSinceLastSpeech / 1000
                    )} seconds ago`,
                  };
                }

                // Case 3.5: Absolute silence check - if no speech for 10+ minutes, leave immediately
                // (meeting became dead even though it had speech before)
                if (timeSinceLastSpeech >= absoluteSilenceTimeoutMs) {
                  return {
                    shouldLeave: true,
                    reason: `Absolute silence timeout - no speech for ${Math.round(
                      timeSinceLastSpeech / 1000 / 60
                    )} minutes (meeting became inactive)`,
                  };
                }

                // Case 4: No speech for 2+ minutes, check remaining participants
                // Use participant IDs to avoid name capitalization issues
                const remainingParticipantIds = new Set(
                  activeParticipants.keys()
                );
                const silentParticipantIds = new Set(
                  Array.from(remainingParticipantIds).filter(
                    (participantId) => !spokenSpeakers.has(participantId)
                  )
                );

                // If all remaining participants have never spoken, start/continue countdown
                // Check if remainingParticipantIds is a subset of silentParticipantIds
                // (some silent participants may have already left)
                if (
                  isSubset(remainingParticipantIds, silentParticipantIds) &&
                  remainingParticipantIds.size > 0
                ) {
                  if (!isInSilenceCountdown) {
                    // Start countdown
                    isInSilenceCountdown = true;
                    silenceCountdown = SILENT_PARTICIPANTS_COUNTDOWN_SECONDS;
                    (window as any).logBot(
                      `🕐 Starting 3-minute countdown - all ${remainingParticipantIds.size} remaining participants are silent`
                    );
                  }

                  // Continue countdown
                  if (silenceCountdown > 0) {
                    return {
                      shouldLeave: false,
                      reason: `All remaining participants are silent - countdown: ${Math.round(
                        silenceCountdown
                      )}s remaining`,
                    };
                  } else {
                    return {
                      shouldLeave: true,
                      reason:
                        "Silence countdown completed - all remaining participants are silent, meeting appears ended",
                    };
                  }
                }

                // Case 5: Some remaining participants have spoken before - keep waiting
                return {
                  shouldLeave: false,
                  reason: `Waiting for previously speaking participants - ${
                    remainingParticipantIds.size - silentParticipantIds.size
                  } of ${
                    remainingParticipantIds.size
                  } remaining participants have spoken before`,
                };
              };

              const setupWebSocket = () => {
                try {
                  if (socket) {
                    // Close previous socket if it exists
                    try {
                      socket.close();
                    } catch (err) {
                      // Ignore errors when closing
                    }
                  }

                  socket = new WebSocket(whisperUrlForBrowser);

                  // --- NEW: Force-close if connection cannot be established quickly ---
                  const connectionTimeoutMs = 3000; // 3-second timeout for CONNECTING state
                  let connectionTimeoutHandle: number | null =
                    window.setTimeout(() => {
                      if (
                        socket &&
                        socket.readyState === WebSocket.CONNECTING
                      ) {
                        (window as any).logBot(
                          `Connection attempt timed out after ${connectionTimeoutMs}ms. Forcing close.`
                        );
                        try {
                          socket.close(); // Triggers onclose -> retry logic
                        } catch (_) {
                          /* ignore */
                        }
                      }
                    }, connectionTimeoutMs);

                  socket.onopen = function () {
                    if (connectionTimeoutHandle !== null) {
                      clearTimeout(connectionTimeoutHandle); // Clear connection watchdog
                      connectionTimeoutHandle = null;
                    }
                    // --- MODIFIED: Log current config being used ---
                    // --- MODIFIED: Generate NEW UUID for this connection ---
                    currentSessionUid = generateUUID(); // Update the currentSessionUid
                    sessionAudioStartTimeMs = null; // ADDED: Reset for new WebSocket session
                    (window as any).logBot(
                      `[RelativeTime] WebSocket connection opened. New UID: ${currentSessionUid}. sessionAudioStartTimeMs reset. Lang: ${currentWsLanguage}, Task: ${currentWsTask}`
                    );
                    retryCount = 0;

                    if (socket) {
                      // Construct the initial configuration message using config values
                      const initialConfigPayload = {
                        uid: currentSessionUid, // <-- Use NEWLY generated UUID
                        language: currentWsLanguage || null, // <-- Use browser-scope variable
                        task: currentWsTask || "transcribe", // <-- Use browser-scope variable
                        model: "tiny", // Keep default or make configurable if needed
                        use_vad: true, // Keep default or make configurable if needed
                        platform: platform, // From config
                        token: token, // From config
                        meeting_id: nativeMeetingId, // From config
                        meeting_url: meetingUrl || null, // From config, default to null
                      };

                      const jsonPayload = JSON.stringify(initialConfigPayload);

                      // Log the exact payload being sent
                      (window as any).logBot(
                        `Sending initial config message: ${jsonPayload}`
                      );
                      socket.send(jsonPayload);
                    }
                  };

                  socket.onmessage = (event) => {
                    (window as any).logBot("Received message: " + event.data);
                    const data = JSON.parse(event.data);
                    // NOTE: The check `if (data["uid"] !== sessionUid) return;` is removed
                    // because we no longer have a single sessionUid for the lifetime of the evaluate block.
                    // Each message *should* contain the UID associated with the specific WebSocket
                    // connection it came from. Downstream needs to handle this if correlation is needed.
                    // For now, we assume messages are relevant to the current bot context.
                    // Consider re-introducing a check if whisperlive echoes back the UID and it's needed.

                    if (data["status"] === "ERROR") {
                      (window as any).logBot(
                        `WebSocket Server Error: ${data["message"]}`
                      );
                    } else if (data["status"] === "WAIT") {
                      (window as any).logBot(`Server busy: ${data["message"]}`);
                    } else if (!isServerReady) {
                      isServerReady = true;
                      (window as any).logBot("Server is ready.");
                    } else if (data["language"]) {
                      (window as any).logBot(
                        `Language detected: ${data["language"]}`
                      );
                    } else if (data["message"] === "DISCONNECT") {
                      (window as any).logBot("Server requested disconnect.");
                      if (socket) {
                        socket.close();
                      }
                    } else {
                      // --- ADDED: Collect transcription segments for SRT ---
                      const transcriptionData = data["segments"] || data;
                      (window as any).logBot(
                        `[DEBUG] Calling processSpeechActivity with type=${
                          Array.isArray(transcriptionData)
                            ? "array"
                            : typeof transcriptionData
                        }`
                      );

                      (window as any).logBot(
                        `[DEBUG] Transcription data: ${JSON.stringify(
                          transcriptionData
                        )}`
                      );
                      // botCallbacks?.onTranscriptionSegmentsReceived(
                      //   transcriptionData
                      // );

                      // Process speech activity for intelligent meeting end detection
                      processSpeechActivity(transcriptionData);

                      (window as any).logBot(
                        `Transcription: ${JSON.stringify(data)}`
                      );
                    }
                  };

                  // Helper function to process transcription segments for speaker identification
                  // Note: Durations are calculated from SPEAKER_START/SPEAKER_END events, not from segments
                  // Segments are only used to identify which speaker said what (via mapSpeakerToSegment)
                  const processSpeechActivity = (transcriptionData: any) => {
                    // Handle both single segment and array of segments
                    const segments = Array.isArray(transcriptionData)
                      ? transcriptionData
                      : transcriptionData.segments || [transcriptionData];

                    (window as any).logBot(
                      `[DEBUG] processSpeechActivity: segmentsCount=${segments.length}`
                    );

                    segments.forEach((segment: any) => {
                      const startSec = Number(segment.start);
                      const endSec = Number(segment.end);

                      if (
                        !Number.isFinite(startSec) ||
                        !Number.isFinite(endSec) ||
                        !sessionAudioStartTimeMs
                      ) {
                        if (!sessionAudioStartTimeMs) {
                          (window as any).logBot(
                            `[DEBUG] Skipping segment: sessionAudioStartTimeMs not set yet`
                          );
                        } else {
                          (window as any).logBot(
                            `[DEBUG] Skipping segment with non-numeric times: start=${segment.start}, end=${segment.end}`
                          );
                        }
                        return;
                      }

                      const segmentDuration = endSec - startSec;

                      // Skip segments with zero or negative duration
                      if (segmentDuration <= 0) {
                        return;
                      }

                      // Skip incomplete segments - they may be extended in later messages
                      // Only process completed segments to avoid double-counting when they get extended
                      if (segment.completed === false) {
                        return;
                      }

                      // Check if we've already processed this segment (WhisperLive sends cumulative segments)
                      const segmentKey = `${startSec},${endSec}`;
                      if (processedSegments.has(segmentKey)) {
                        // Already processed, skip to avoid double-counting
                        return;
                      }

                      // Mark this segment as processed
                      processedSegments.add(segmentKey);

                      // Convert segment times from seconds (relative to WhisperLive start) to milliseconds (relative to sessionAudioStartTimeMs)
                      // WhisperLive timestamps start from 0 when audio stream begins
                      // We need to align them with our sessionAudioStartTimeMs reference
                      const segmentStartMs = startSec * 1000;
                      const segmentEndMs = endSec * 1000;

                      // Use time-based overlap matching to identify speaker
                      const speakerMapping = mapSpeakerToSegment(
                        segmentStartMs,
                        segmentEndMs
                      );

                      const speakerName = speakerMapping.speakerName;
                      const speakerId = speakerMapping.speakerId;

                      // Update last speech time (note: durations are now calculated from SPEAKER_START/SPEAKER_END events)
                      lastSpeechTime = Date.now();

                      // Log segment processing (segments used for speaker identification via mapSpeakerToSegment;
                      // durations are calculated from SPEAKER_START/SPEAKER_END events)
                      if (speakerName && speakerId) {
                        (window as any).logBot(
                          `📄 Segment processed: ${speakerName} (ID: ${speakerId}) - Duration: ${segmentDuration.toFixed(
                            1
                          )}s (timestamps handled by SPEAKER_START/SPEAKER_END)`
                        );
                      } else {
                        (window as any).logBot(
                          `⚠️ Could not identify speaker for segment (duration: ${segmentDuration.toFixed(
                            1
                          )}s, start=${startSec.toFixed(
                            2
                          )}s, end=${endSec.toFixed(2)}s)`
                        );
                      }

                      // Reset silence countdown if we were in one
                      if (isInSilenceCountdown) {
                        isInSilenceCountdown = false;
                        silenceCountdown = 0;
                        (window as any).logBot(
                          "🔄 Speech detected (segment) - resetting silence countdown"
                        );
                      }
                    });
                  };

                  socket.onerror = (event) => {
                    if (connectionTimeoutHandle !== null) {
                      clearTimeout(connectionTimeoutHandle);
                      connectionTimeoutHandle = null;
                    }
                    (window as any).logBot(
                      `WebSocket error: ${JSON.stringify(event)}`
                    );
                  };

                  socket.onclose = (event) => {
                    if (connectionTimeoutHandle !== null) {
                      clearTimeout(connectionTimeoutHandle);
                      connectionTimeoutHandle = null;
                    }
                    (window as any).logBot(
                      `WebSocket connection closed. Code: ${event.code}, Reason: ${event.reason}`
                    );

                    // Retry logic - now retries indefinitely
                    retryCount++;
                    (window as any).logBot(
                      `Attempting to reconnect in ${baseRetryDelay}ms. Retry attempt ${retryCount}`
                    );

                    setTimeout(() => {
                      (window as any).logBot(
                        `Retrying WebSocket connection (attempt ${retryCount})...`
                      );
                      setupWebSocket();
                    }, baseRetryDelay);
                  };
                } catch (e: any) {
                  (window as any).logBot(
                    `Error creating WebSocket: ${e.message}`
                  );
                  // For initial connection errors, handle with retry logic - now retries indefinitely
                  retryCount++;
                  (window as any).logBot(
                    `Error during WebSocket setup. Attempting to reconnect in ${baseRetryDelay}ms. Retry attempt ${retryCount}`
                  );

                  setTimeout(() => {
                    (window as any).logBot(
                      `Retrying WebSocket connection (attempt ${retryCount})...`
                    );
                    setupWebSocket();
                  }, baseRetryDelay);
                }
              };

              // --- ADD Function exposed to Node.js for triggering reconfigure ---
              (window as any).triggerWebSocketReconfigure = (
                newLang: string | null,
                newTask: string | null
              ) => {
                (window as any).logBot(
                  `[Node->Browser] Received reconfigure. New Lang: ${newLang}, New Task: ${newTask}`
                );
                currentWsLanguage = newLang; // Update browser state
                currentWsTask = newTask || "transcribe"; // Update browser state, default task if null

                if (socket && socket.readyState === WebSocket.OPEN) {
                  (window as any).logBot(
                    "[Node->Browser] Closing WebSocket to reconnect with new config."
                  );
                  socket.close(); // Triggers onclose -> setupWebSocket which now reads updated vars
                } else if (
                  socket &&
                  (socket.readyState === WebSocket.CONNECTING ||
                    socket.readyState === WebSocket.CLOSING)
                ) {
                  (window as any).logBot(
                    "[Node->Browser] Socket is connecting or closing, cannot close now. Reconnect will use new config when it opens."
                  );
                } else {
                  // Socket is null or already closed
                  (window as any).logBot(
                    "[Node->Browser] Socket is null or closed. Attempting to setupWebSocket directly."
                  );
                  // Directly calling setupWebSocket might cause issues if the old one is mid-retry
                  // Relying on the existing retry logic in onclose is likely safer.
                  // If setupWebSocket is called here, ensure it handles potential double connections.
                  // setupWebSocket();
                }
              };
              // --- ----------------------------------------------------------- ---

              // --- ADDED: Expose leave function to Node context ---
              (window as any).performLeaveAction = async () => {
                (window as any).logBot(
                  "Attempting to leave the meeting from browser context..."
                );
                try {
                  // *** FIXED: Use document.evaluate for XPath ***
                  const primaryLeaveButtonXpath = `//button[@aria-label="Leave call"]`;
                  const secondaryLeaveButtonXpath = `//button[.//span[text()='Leave meeting']] | //button[.//span[text()='Just leave the meeting']]`;

                  const getElementByXpath = (
                    path: string
                  ): HTMLElement | null => {
                    const result = document.evaluate(
                      path,
                      document,
                      null,
                      XPathResult.FIRST_ORDERED_NODE_TYPE,
                      null
                    );
                    return result.singleNodeValue as HTMLElement | null;
                  };

                  const primaryLeaveButton = getElementByXpath(
                    primaryLeaveButtonXpath
                  );
                  if (primaryLeaveButton) {
                    (window as any).logBot("Clicking primary leave button...");
                    primaryLeaveButton.click(); // No need to cast HTMLElement if getElementByXpath returns it
                    await new Promise((resolve) => setTimeout(resolve, 1000)); // Wait a bit for potential confirmation dialog

                    // Try clicking secondary/confirmation button if it appears
                    const secondaryLeaveButton = getElementByXpath(
                      secondaryLeaveButtonXpath
                    );
                    if (secondaryLeaveButton) {
                      (window as any).logBot(
                        "Clicking secondary/confirmation leave button..."
                      );
                      secondaryLeaveButton.click();
                      await new Promise((resolve) => setTimeout(resolve, 500)); // Short wait after final click
                    } else {
                      (window as any).logBot(
                        "Secondary leave button not found."
                      );
                    }
                    (window as any).logBot("Leave sequence completed.");
                    return true; // Indicate leave attempt was made
                  } else {
                    (window as any).logBot("Primary leave button not found.");
                    return false; // Indicate leave button wasn't found
                  }
                } catch (err: any) {
                  (window as any).logBot(
                    `Error during leave attempt: ${err.message}`
                  );
                  return false; // Indicate error during leave
                }
              };
              // --- --------------------------------------------- ---

              setupWebSocket();

              // --- ADD: Speaker Detection Logic (Adapted from speakers_console_test.js) ---
              // Configuration for speaker detection
              const participantSelector = "div[data-participant-id]"; // UPDATED: More specific selector
              const speakingClasses = ["Oaajhc", "HX2H7", "wEsLMd", "OgVli"]; // Speaking/animation classes
              const silenceClass = "gjg47c"; // Class indicating the participant is silent
              const nameSelectors = [
                // Try these selectors to find participant's name
                "[data-participant-id]", // Attribute for participant ID
              ];

              // State for tracking speaking status
              const speakingStates = new Map(); // Stores the logical speaking state for each participant ID
              const activeParticipants = new Map(); // NEW: Central map for all known participants

              // Track current session UID for speaker events
              let currentSessionUid = generateUUID(); // Initialize with a new UID

              // Helper functions for speaker detection
              function getParticipantId(element: HTMLElement) {
                let id = element.getAttribute("data-participant-id");
                if (!id) {
                  const stableChild = element.querySelector("[jsinstance]");
                  if (stableChild) {
                    id = stableChild.getAttribute("jsinstance");
                  }
                }
                if (!id) {
                  if (!(element as any).dataset.vexaGeneratedId) {
                    (element as any).dataset.vexaGeneratedId =
                      "vexa-id-" + Math.random().toString(36).substr(2, 9);
                  }
                  id = (element as any).dataset.vexaGeneratedId;
                }
                return id;
              }

              function getParticipantName(participantElement: HTMLElement) {
                const mainTile = participantElement.closest(
                  "[data-participant-id]"
                ) as HTMLElement;
                if (mainTile) {
                  const userExampleNameElement =
                    mainTile.querySelector("span.notranslate");
                  if (
                    userExampleNameElement &&
                    userExampleNameElement.textContent &&
                    userExampleNameElement.textContent.trim()
                  ) {
                    const nameText = userExampleNameElement.textContent.trim();
                    if (
                      nameText.length > 1 &&
                      nameText.length < 50 &&
                      /^[\p{L}\s.'-]+$/u.test(nameText)
                    ) {
                      const forbiddenSubstrings = [
                        "more_vert",
                        "mic_off",
                        "mic",
                        "videocam",
                        "videocam_off",
                        "present_to_all",
                        "devices",
                        "speaker",
                        "speakers",
                        "microphone",
                      ];
                      if (
                        !forbiddenSubstrings.some((sub) =>
                          nameText.toLowerCase().includes(sub.toLowerCase())
                        )
                      ) {
                        return nameText;
                      }
                    }
                  }
                  const googleTsNameSelectors = [
                    "[data-self-name]",
                    ".zWGUib",
                    ".cS7aqe.N2K3jd",
                    ".XWGOtd",
                    '[data-tooltip*="name"]',
                  ];
                  for (const selector of googleTsNameSelectors) {
                    const nameElement = mainTile.querySelector(
                      selector
                    ) as HTMLElement;
                    if (nameElement) {
                      let nameText =
                        (nameElement as HTMLElement).textContent ||
                        (nameElement as HTMLElement).innerText ||
                        nameElement.getAttribute("data-self-name") ||
                        nameElement.getAttribute("data-tooltip");
                      if (nameText && nameText.trim()) {
                        if (
                          selector.includes("data-tooltip") &&
                          nameText.includes("Tooltip for ")
                        ) {
                          nameText = nameText
                            .replace("Tooltip for ", "")
                            .trim();
                        }
                        if (nameText && nameText.trim()) {
                          const forbiddenSubstrings = [
                            "more_vert",
                            "mic_off",
                            "mic",
                            "videocam",
                            "videocam_off",
                            "present_to_all",
                            "devices",
                            "speaker",
                            "speakers",
                            "microphone",
                          ];
                          if (
                            !forbiddenSubstrings.some((sub) =>
                              nameText!
                                .toLowerCase()
                                .includes(sub.toLowerCase())
                            )
                          ) {
                            const trimmedName = nameText!
                              .split("\n")
                              .pop()
                              ?.trim();
                            return trimmedName || "Unknown (Filtered)";
                          }
                        }
                      }
                    }
                  }
                }
                for (const selector of nameSelectors) {
                  const nameElement = participantElement.querySelector(
                    selector
                  ) as HTMLElement;
                  if (nameElement) {
                    let nameText =
                      (nameElement as HTMLElement).textContent ||
                      (nameElement as HTMLElement).innerText ||
                      nameElement.getAttribute("data-self-name");
                    if (nameText && nameText.trim()) {
                      // ADDED: Apply forbidden substrings and trimming logic here too
                      const forbiddenSubstrings = [
                        "more_vert",
                        "mic_off",
                        "mic",
                        "videocam",
                        "videocam_off",
                        "present_to_all",
                        "devices",
                        "speaker",
                        "speakers",
                        "microphone",
                      ];
                      if (
                        !forbiddenSubstrings.some((sub) =>
                          nameText!.toLowerCase().includes(sub.toLowerCase())
                        )
                      ) {
                        const trimmedName = nameText!.split("\n").pop()?.trim();
                        if (
                          trimmedName &&
                          trimmedName.length > 1 &&
                          trimmedName.length < 50 &&
                          /^[\p{L}\s.'-]+$/u.test(trimmedName)
                        ) {
                          // Added basic length and char validation
                          return trimmedName;
                        }
                      }
                      // If it was forbidden or failed validation, it won't return, allowing loop to continue or fallback.
                    }
                  }
                }
                if (
                  participantElement.textContent &&
                  participantElement.textContent.includes("You") &&
                  participantElement.textContent.length < 20
                ) {
                  return "You";
                }
                const idToDisplay = mainTile
                  ? getParticipantId(mainTile)
                  : getParticipantId(participantElement);
                return `Participant (${idToDisplay})`;
              }

              function sendSpeakerEvent(
                eventType: string,
                participantElement: HTMLElement
              ) {
                const eventAbsoluteTimeMs = Date.now();
                let relativeTimestampMs: number | null = null;

                if (sessionAudioStartTimeMs === null) {
                  (window as any).logBot(
                    `[RelativeTime] SKIPPING speaker event: ${eventType} for ${getParticipantName(
                      participantElement
                    )}. sessionAudioStartTimeMs not yet set. UID: ${currentSessionUid}`
                  );
                  return; // Do not send if audio hasn't started for this session
                }

                relativeTimestampMs =
                  eventAbsoluteTimeMs - sessionAudioStartTimeMs;

                const participantId = getParticipantId(participantElement);
                const participantName = getParticipantName(participantElement);

                // Store event locally for time-based overlap matching (only if we have valid participant info)
                if (participantId && participantName) {
                  const localEvent: SpeakerEvent = {
                    event_type: eventType as "SPEAKER_START" | "SPEAKER_END",
                    participant_name: participantName,
                    participant_id_meet: participantId,
                    relative_client_timestamp_ms: relativeTimestampMs,
                  };
                  localSpeakerEvents.push(localEvent);
                  // Keep events sorted by timestamp for efficient querying
                  localSpeakerEvents.sort(
                    (a, b) =>
                      a.relative_client_timestamp_ms -
                      b.relative_client_timestamp_ms
                  );

                  // Calculate speaker duration from SPEAKER_START/SPEAKER_END events
                  if (eventType === "SPEAKER_START") {
                    // Store start timestamp for this speaker
                    activeSpeakerStarts.set(participantId, relativeTimestampMs);
                    (window as any).logBot(
                      `📊 SPEAKER_START tracked: ${participantName} (ID: ${participantId}) at ${relativeTimestampMs}ms`
                    );
                  } else if (eventType === "SPEAKER_END") {
                    // Find matching START event and calculate duration
                    const startTimestampMs =
                      activeSpeakerStarts.get(participantId);
                    if (startTimestampMs !== undefined) {
                      // Calculate duration in seconds
                      const durationSeconds =
                        (relativeTimestampMs - startTimestampMs) / 1000;

                      // Only add positive durations (avoid errors from out-of-order events)
                      if (durationSeconds > 0) {
                        // Accumulate duration for this speaker
                        const currentDuration =
                          speakerDurationCollector.get(participantId) || 0;
                        const newDuration = currentDuration + durationSeconds;
                        speakerDurationCollector.set(
                          participantId,
                          newDuration
                        );

                        // Add speaker ID to spokenSpeakers set (using ID to handle name capitalization variations)
                        if (!spokenSpeakers.has(participantId)) {
                          spokenSpeakers.add(participantId);
                          (window as any).logBot(
                            `📝 New speaker detected: ${participantName} (ID: ${participantId}, ${durationSeconds.toFixed(
                              1
                            )}s)`
                          );
                        }

                        // Store ID -> name mapping persistently (so we can show name even after participant leaves)
                        speakerIdToNameMap.set(participantId, participantName);

                        // Update last speech time
                        lastSpeechTime = Date.now();

                        // Log duration accumulation
                        (window as any).logBot(
                          `📊 SPEAKER_END: ${participantName} (ID: ${participantId}) at ${relativeTimestampMs}ms`
                        );
                        (window as any).logBot(
                          `⏱️ Speaker duration: ${participantName} (ID: ${participantId}) - Segment: ${durationSeconds.toFixed(
                            1
                          )}s | Total: ${newDuration.toFixed(1)}s`
                        );

                        // Check if any speaker has accumulated enough speech duration
                        // This marks the meeting as having had meaningful speech activity
                        if (!meetingHasHadSpeech) {
                          // Check if current speaker just reached threshold
                          if (
                            newDuration >= SPEECH_ACTIVATION_THRESHOLD_SECONDS
                          ) {
                            meetingHasHadSpeech = true;
                            (window as any).logBot(
                              `🎤 Meeting has had speech: ${participantName} reached ${newDuration.toFixed(
                                1
                              )}s (${SPEECH_ACTIVATION_THRESHOLD_SECONDS}s threshold) - speech tracking now active`
                            );
                          } else {
                            // Check all speakers to see if any has reached threshold
                            for (const [
                              id,
                              duration,
                            ] of speakerDurationCollector.entries()) {
                              if (
                                duration >= SPEECH_ACTIVATION_THRESHOLD_SECONDS
                              ) {
                                const participant = activeParticipants.get(id);
                                const speakerDisplayName = participant
                                  ? participant.name
                                  : `Unknown (${id})`;
                                meetingHasHadSpeech = true;
                                (window as any).logBot(
                                  `🎤 Meeting has had speech: ${speakerDisplayName} reached ${duration.toFixed(
                                    1
                                  )}s (${SPEECH_ACTIVATION_THRESHOLD_SECONDS}s threshold) - speech tracking now active`
                                );
                                break;
                              }
                            }
                          }
                        }

                        // Reset silence countdown if active (speech detected)
                        if (isInSilenceCountdown) {
                          isInSilenceCountdown = false;
                          silenceCountdown = 0;
                          (window as any).logBot(
                            "🔄 Speech detected (SPEAKER_END) - resetting silence countdown"
                          );
                        }
                      } else {
                        (window as any).logBot(
                          `⚠️ Invalid duration calculated: ${durationSeconds.toFixed(
                            1
                          )}s for ${participantName} (END: ${relativeTimestampMs}ms, START: ${startTimestampMs}ms)`
                        );
                      }

                      // Remove from active speakers
                      activeSpeakerStarts.delete(participantId);
                    } else {
                      (window as any).logBot(
                        `⚠️ SPEAKER_END without matching START for ${participantName} (ID: ${participantId})`
                      );
                    }
                  }
                }

                // Send speaker event via WebSocket if connected
                if (socket && socket.readyState === WebSocket.OPEN) {
                  const speakerEventMessage = {
                    type: "speaker_activity",
                    payload: {
                      event_type: eventType,
                      participant_name: participantName,
                      participant_id_meet: participantId,
                      relative_client_timestamp_ms: relativeTimestampMs, // UPDATED
                      uid: currentSessionUid, // Use the current session UID
                      token: token,
                      platform: platform,
                      meeting_id: nativeMeetingId,
                      meeting_url: meetingUrl,
                    },
                  };

                  try {
                    socket.send(JSON.stringify(speakerEventMessage));
                    (window as any).logBot(
                      `[RelativeTime] Speaker event sent: ${eventType} for ${participantName} (${participantId}). RelativeTs: ${relativeTimestampMs}ms. UID: ${currentSessionUid}. (AbsoluteEventMs: ${eventAbsoluteTimeMs}, SessionT0Ms: ${sessionAudioStartTimeMs})`
                    );
                  } catch (error: any) {
                    (window as any).logBot(
                      `Error sending speaker event: ${error.message}`
                    );
                  }
                } else {
                  (window as any).logBot(
                    `WebSocket not ready, speaker event queued: ${eventType} for ${participantName}`
                  );
                }
              }

              function logSpeakerEvent(
                participantElement: HTMLElement,
                mutatedClassList: DOMTokenList
              ) {
                const participantId = getParticipantId(participantElement);
                const participantName = getParticipantName(participantElement);
                const previousLogicalState =
                  speakingStates.get(participantId) || "silent";

                const isNowVisiblySpeaking = speakingClasses.some((cls) =>
                  mutatedClassList.contains(cls)
                );
                const isNowVisiblySilent =
                  mutatedClassList.contains(silenceClass);

                if (isNowVisiblySpeaking) {
                  if (previousLogicalState !== "speaking") {
                    (window as any).logBot(
                      `🎤 SPEAKER_START: ${participantName} (ID: ${participantId})`
                    );
                    sendSpeakerEvent("SPEAKER_START", participantElement);
                    // Track this participant as currently speaking
                    currentlySpeakingParticipants.add(participantName);
                  }
                  speakingStates.set(participantId, "speaking");
                } else if (isNowVisiblySilent) {
                  if (previousLogicalState === "speaking") {
                    (window as any).logBot(
                      `🔇 SPEAKER_END: ${participantName} (ID: ${participantId})`
                    );
                    sendSpeakerEvent("SPEAKER_END", participantElement);
                    // Remove this participant from currently speaking
                    currentlySpeakingParticipants.delete(participantName);
                  }
                  speakingStates.set(participantId, "silent");
                }
              }

              function observeParticipant(participantElement: HTMLElement) {
                const participantId = getParticipantId(participantElement);

                // Determine initial logical state based on current classes
                speakingStates.set(participantId, "silent"); // Initialize participant as silent. logSpeakerEvent will handle transitions.

                let classListForInitialScan = participantElement.classList; // Default to the main participant element's classes
                // Check if any descendant has a speaking class
                for (const cls of speakingClasses) {
                  const descendantElement = participantElement.querySelector(
                    "." + cls
                  ); // Corrected selector
                  if (descendantElement) {
                    classListForInitialScan = descendantElement.classList;
                    break;
                  }
                }
                // If no speaking descendant was found, classListForInitialScan remains participantElement.classList.
                // This is correct for checking if participantElement itself has a speaking or silence class.

                (window as any).logBot(
                  `👁️ Observing: ${getParticipantName(
                    participantElement
                  )} (ID: ${participantId}). Performing initial participant state analysis.`
                );
                // Call logSpeakerEvent with the determined classList.
                // It will compare against the "silent" state and emit SPEAKER_START if currently speaking,
                // or do nothing if currently silent (matching the initialized state).
                logSpeakerEvent(participantElement, classListForInitialScan);

                // NEW: Add participant to our central map
                activeParticipants.set(participantId, {
                  name: getParticipantName(participantElement),
                  element: participantElement,
                });

                const callback = function (
                  mutationsList: MutationRecord[],
                  observer: MutationObserver
                ) {
                  for (const mutation of mutationsList) {
                    if (
                      mutation.type === "attributes" &&
                      mutation.attributeName === "class"
                    ) {
                      const targetElement = mutation.target as HTMLElement;
                      if (
                        targetElement.matches(participantSelector) ||
                        participantElement.contains(targetElement)
                      ) {
                        const finalTarget = targetElement.matches(
                          participantSelector
                        )
                          ? targetElement
                          : participantElement;
                        // logSpeakerEvent(finalTarget, finalTarget.classList); // Old line
                        logSpeakerEvent(finalTarget, targetElement.classList); // Corrected line
                      }
                    }
                  }
                };

                const observer = new MutationObserver(callback);
                observer.observe(participantElement, {
                  attributes: true,
                  attributeFilter: ["class"],
                  subtree: true,
                });

                if (!(participantElement as any).dataset.vexaObserverAttached) {
                  (participantElement as any).dataset.vexaObserverAttached =
                    "true";
                }
              }

              function scanForAllParticipants() {
                const participantElements =
                  document.querySelectorAll(participantSelector);
                for (let i = 0; i < participantElements.length; i++) {
                  const el = participantElements[i] as HTMLElement;
                  if (!(el as any).dataset.vexaObserverAttached) {
                    observeParticipant(el);
                  }
                }
              }

              // Initialize speaker detection
              scanForAllParticipants();

              // Monitor for new participants
              const bodyObserver = new MutationObserver((mutationsList) => {
                for (const mutation of mutationsList) {
                  if (mutation.type === "childList") {
                    mutation.addedNodes.forEach((node) => {
                      if (node.nodeType === Node.ELEMENT_NODE) {
                        const elementNode = node as HTMLElement;
                        if (
                          elementNode.matches(participantSelector) &&
                          !(elementNode as any).dataset.vexaObserverAttached
                        ) {
                          observeParticipant(elementNode);
                        }
                        const childElements =
                          elementNode.querySelectorAll(participantSelector);
                        for (let i = 0; i < childElements.length; i++) {
                          const childEl = childElements[i] as HTMLElement;
                          if (!(childEl as any).dataset.vexaObserverAttached) {
                            observeParticipant(childEl);
                          }
                        }
                      }
                    });
                    mutation.removedNodes.forEach((node) => {
                      if (node.nodeType === Node.ELEMENT_NODE) {
                        const elementNode = node as HTMLElement;
                        if (elementNode.matches(participantSelector)) {
                          const participantId = getParticipantId(elementNode);
                          const participantName =
                            getParticipantName(elementNode);
                          if (
                            speakingStates.get(participantId) === "speaking"
                          ) {
                            // Send synthetic SPEAKER_END if they were speaking when removed
                            (window as any).logBot(
                              `🔇 SPEAKER_END (Participant removed while speaking): ${participantName} (ID: ${participantId})`
                            );
                            sendSpeakerEvent("SPEAKER_END", elementNode);
                          }
                          speakingStates.delete(participantId);
                          delete (elementNode as any).dataset
                            .vexaObserverAttached;
                          delete (elementNode as any).dataset.vexaGeneratedId;
                          (window as any).logBot(
                            `🗑️ Removed observer for: ${participantName} (ID: ${participantId})`
                          );

                          // Remove participant from currently speaking tracking
                          currentlySpeakingParticipants.delete(participantName);

                          // Clean up activeSpeakerStarts if participant was removed mid-speech
                          // (The synthetic SPEAKER_END above will also handle this, but this is a safety cleanup)
                          if (participantId) {
                            if (activeSpeakerStarts.has(participantId)) {
                              (window as any).logBot(
                                `⚠️ Participant ${
                                  participantName || "Unknown"
                                } removed while speaking (no matching SPEAKER_END) - cleaning up activeSpeakerStarts`
                              );
                              activeSpeakerStarts.delete(participantId);
                            }
                          }

                          // NEW: Remove participant from our central map
                          if (participantId) {
                            activeParticipants.delete(participantId);
                          }
                        }
                      }
                    });
                  }
                }
              });

              bodyObserver.observe(document.body, {
                childList: true,
                subtree: true,
              });

              // --- ADD: Enhanced Leave Function with Session End Signal ---
              (window as any).performLeaveAction = async () => {
                (window as any).logBot(
                  "Attempting to leave the meeting from browser context..."
                );

                // Send LEAVING_MEETING signal before closing WebSocket
                if (socket && socket.readyState === WebSocket.OPEN) {
                  try {
                    const sessionControlMessage = {
                      type: "session_control",
                      payload: {
                        event: "LEAVING_MEETING",
                        uid: currentSessionUid,
                        client_timestamp_ms: Date.now(),
                        token: token,
                        platform: platform,
                        meeting_id: nativeMeetingId,
                      },
                    };

                    socket.send(JSON.stringify(sessionControlMessage));
                    (window as any).logBot(
                      "LEAVING_MEETING signal sent to WhisperLive"
                    );

                    // Wait a brief moment for the message to be sent
                    await new Promise((resolve) => setTimeout(resolve, 500));
                  } catch (error: any) {
                    (window as any).logBot(
                      `Error sending LEAVING_MEETING signal: ${error.message}`
                    );
                  }
                }

                try {
                  // *** FIXED: Use document.evaluate for XPath ***
                  const primaryLeaveButtonXpath = `//button[@aria-label="Leave call"]`;
                  const secondaryLeaveButtonXpath = `//button[.//span[text()='Leave meeting']] | //button[.//span[text()='Just leave the meeting']]`;

                  const getElementByXpath = (
                    path: string
                  ): HTMLElement | null => {
                    const result = document.evaluate(
                      path,
                      document,
                      null,
                      XPathResult.FIRST_ORDERED_NODE_TYPE,
                      null
                    );
                    return result.singleNodeValue as HTMLElement | null;
                  };

                  const primaryLeaveButton = getElementByXpath(
                    primaryLeaveButtonXpath
                  );
                  if (primaryLeaveButton) {
                    (window as any).logBot("Clicking primary leave button...");
                    primaryLeaveButton.click(); // No need to cast HTMLElement if getElementByXpath returns it
                    await new Promise((resolve) => setTimeout(resolve, 1000)); // Wait a bit for potential confirmation dialog

                    // Try clicking secondary/confirmation button if it appears
                    const secondaryLeaveButton = getElementByXpath(
                      secondaryLeaveButtonXpath
                    );
                    if (secondaryLeaveButton) {
                      (window as any).logBot(
                        "Clicking secondary/confirmation leave button..."
                      );
                      secondaryLeaveButton.click();
                      await new Promise((resolve) => setTimeout(resolve, 500)); // Short wait after final click
                    } else {
                      (window as any).logBot(
                        "Secondary leave button not found."
                      );
                    }
                    (window as any).logBot("Leave sequence completed.");
                    return true; // Indicate leave attempt was made
                  } else {
                    (window as any).logBot("Primary leave button not found.");
                    return false; // Indicate leave button wasn't found
                  }
                } catch (err: any) {
                  (window as any).logBot(
                    `Error during leave attempt: ${err.message}`
                  );
                  return false; // Indicate error during leave
                }
              };
              // --- --------------------------------------------- ---

              // FIXED: Revert to original audio processing that works with whisperlive
              // but use our combined stream as the input source
              const audioDataCache = [];
              const mediaStream = audioContext.createMediaStreamSource(stream); // Use our combined stream
              const recorder = audioContext.createScriptProcessor(4096, 1, 1);

              recorder.onaudioprocess = async (event) => {
                // Check if server is ready AND socket is open
                if (
                  !isServerReady ||
                  !socket ||
                  socket.readyState !== WebSocket.OPEN
                ) {
                  // (window as any).logBot("WS not ready or closed, skipping audio data send."); // Optional debug log
                  return;
                }

                // ADDED: Set sessionAudioStartTimeMs on the first audio chunk for this session
                if (sessionAudioStartTimeMs === null) {
                  sessionAudioStartTimeMs = Date.now();
                  (window as any).logBot(
                    `[RelativeTime] sessionAudioStartTimeMs set for UID ${currentSessionUid}: ${sessionAudioStartTimeMs} (at first audio data process)`
                  );
                }

                const inputData = event.inputBuffer.getChannelData(0);
                const data = new Float32Array(inputData);
                const targetLength = Math.round(
                  data.length * (16000 / audioContext.sampleRate)
                );
                const resampledData = new Float32Array(targetLength);
                const springFactor = (data.length - 1) / (targetLength - 1);
                resampledData[0] = data[0];
                resampledData[targetLength - 1] = data[data.length - 1];
                for (let i = 1; i < targetLength - 1; i++) {
                  const index = i * springFactor;
                  const leftIndex = Math.floor(index);
                  const rightIndex = Math.ceil(index);
                  const fraction = index - leftIndex;
                  resampledData[i] =
                    data[leftIndex] +
                    (data[rightIndex] - data[leftIndex]) * fraction;
                }
                // Send resampledData
                if (socket && socket.readyState === WebSocket.OPEN) {
                  // Double check before sending
                  // Ensure sessionAudioStartTimeMs is set before sending audio.
                  // This check is more of a safeguard; it should be set by the logic above.
                  if (sessionAudioStartTimeMs === null) {
                    (window as any).logBot(
                      `[RelativeTime] CRITICAL WARNING: sessionAudioStartTimeMs is STILL NULL before sending audio data for UID ${currentSessionUid}. This should not happen.`
                    );
                    // Optionally, set it here as a last resort, though it might be slightly delayed.
                    // sessionAudioStartTimeMs = Date.now();
                    // (window as any).logBot(`[RelativeTime] sessionAudioStartTimeMs set LATE for UID ${currentSessionUid}: ${sessionAudioStartTimeMs}`);
                    return; // Or decide if you want to send audio even if T0 was missed. For now, skipping if T0 is critical.
                  }
                  socket.send(resampledData); // send teh audio to whisperlive socket.
                }
              };

              // Connect the audio processing pipeline
              mediaStream.connect(recorder);
              const gainNode = audioContext.createGain();
              gainNode.gain.value = 0;
              recorder.connect(gainNode);
              gainNode.connect(audioContext.destination);

              (window as any).logBot(
                "Audio processing pipeline connected and sending data silently."
              );

              // Click the "People" button - Updated with multiple selector strategies
              const peopleButtonSelectors = [
                'button[aria-label^="People"]',
                'button[aria-label*="people"]',
                'button[aria-label*="Participants"]',
                'button[aria-label*="participants"]',
                'button[aria-label*="Show people"]',
                'button[aria-label*="show people"]',
                'button[aria-label*="View people"]',
                'button[aria-label*="view people"]',
                'button[aria-label*="Meeting participants"]',
                'button[aria-label*="meeting participants"]',
                // Try text content based selectors
                'button:has(span:contains("People"))',
                'button:has(span:contains("people"))',
                'button:has(span:contains("Participants"))',
                'button:has(span:contains("participants"))',
                // Try icon-based selectors
                "button[data-mdc-dialog-action]",
                'button[data-tooltip*="people"]',
                'button[data-tooltip*="People"]',
                'button[data-tooltip*="participants"]',
                'button[data-tooltip*="Participants"]',
              ];

              let peopleButton: HTMLElement | null = null;
              let usedSelector = "";

              // Try each selector until we find the button
              for (const selector of peopleButtonSelectors) {
                try {
                  const button = document.querySelector(selector);
                  if (button) {
                    peopleButton = button as HTMLElement;
                    usedSelector = selector;
                    (window as any).logBot(
                      `Found People button using selector: ${selector}`
                    );
                    break;
                  }
                } catch (e) {
                  // Some selectors might not be supported in older browsers
                  continue;
                }
              }

              if (!peopleButton) {
                // Fallback: If we can't find the People button, we can still monitor participants
                // using the existing MutationObserver system that watches for participant elements
                (window as any).logBot(
                  `People button not found, but continuing with fallback participant monitoring via MutationObserver`
                );
                (window as any).peopleButtonClicked = false;
              } else {
                // Log which selector worked
                (window as any).logBot(
                  `Successfully found People button using selector: ${usedSelector}`
                );
                peopleButton.click();

                // Set a flag that we successfully clicked the People button
                (window as any).peopleButtonClicked = true;
              }

              // Monitor participant list every 5 seconds
              let aloneTime = 0;
              const checkInterval = setInterval(() => {
                // UPDATED: Use the size of our central map as the source of truth
                const count = activeParticipants.size;
                const participantIds = Array.from(activeParticipants.keys());
                (window as any).logBot(
                  `Participant check: Found ${count} unique participants from central list. IDs: ${JSON.stringify(
                    participantIds
                  )}`
                );

                // If count is 0, it could mean everyone left, OR the participant list area itself is gone.
                if (count === 0) {
                  const peopleListContainer =
                    document.querySelector('[role="list"]'); // Check the original list container
                  if (
                    !peopleListContainer ||
                    !document.body.contains(peopleListContainer)
                  ) {
                    (window as any).logBot(
                      "Participant list container not found (and participant count is 0); assuming meeting ended."
                    );
                    clearInterval(checkInterval);
                    recorder.disconnect();
                    (window as any).triggerNodeGracefulLeave();
                    resolve(); // Resolve the main promise from page.evaluate
                    return; // Exit setInterval callback
                  }
                }

                // Intelligent meeting end detection
                const meetingEndDecision = shouldLeaveMeeting(count);

                // CRITICAL: Handle "alone in meeting" case first (preserve existing behavior)
                if (count <= 1) {
                  // Bot is alone - use original logic for immediate response
                  aloneTime += 5; // Keep existing aloneTime tracking

                  if (aloneTime >= 10) {
                    (window as any).logBot(
                      "Meeting ended or bot has been alone for 10 seconds. Stopping recorder..."
                    );
                    clearInterval(checkInterval);
                    recorder.disconnect();
                    (window as any).triggerNodeGracefulLeave();
                    resolve();
                    return;
                  }

                  // Log countdown if timer has started
                  if (aloneTime > 0) {
                    (window as any).logBot(
                      `Bot has been alone for ${aloneTime} seconds. Will leave in ${
                        10 - aloneTime
                      } more seconds.`
                    );
                  }
                  return; // Skip new speech-based logic when alone
                }

                // NEW: Apply speech-based logic only when other participants are present

                // Update silence countdown if active
                if (isInSilenceCountdown && silenceCountdown > 0) {
                  silenceCountdown -= 5; // Subtract 5 seconds (interval duration)
                }

                // Handle new participants joining during countdown
                if (count > activeParticipants.size) {
                  (window as any).logBot(
                    `New participant detected - updating participant tracking (${activeParticipants.size} → ${count})`
                  );

                  // Reset countdown if we were in one (new participant might speak)
                  if (isInSilenceCountdown) {
                    isInSilenceCountdown = false;
                    silenceCountdown = 0;
                    (window as any).logBot(
                      "🔄 New participant joined - resetting silence countdown"
                    );
                  }

                  // Update activeParticipants size for next comparison
                  // Note: The actual participant data will be updated by the observer logic
                }

                // Build duration summary with speaker names (using persistent map to show names even after participants leave)
                const durationSummary: Record<string, number> = {};
                speakerDurationCollector.forEach((duration, speakerId) => {
                  // First try persistent map, then activeParticipants, then fallback to ID
                  const displayName =
                    speakerIdToNameMap.get(speakerId) ||
                    activeParticipants.get(speakerId)?.name ||
                    `Unknown (${speakerId})`;
                  durationSummary[displayName] = duration;
                });

                // Enhanced debug logging for speech activity
                const debugInfo = {
                  participants: count,
                  hasHadSpeech: meetingHasHadSpeech,
                  lastSpeechTime: lastSpeechTime
                    ? Math.round((Date.now() - lastSpeechTime) / 1000)
                    : "never",
                  spokenSpeakers: Array.from(spokenSpeakers).map((id) => {
                    const participant = activeParticipants.get(id);
                    return participant ? `${participant.name} (${id})` : id;
                  }),
                  speakerDurations: durationSummary, // Total speaking duration per speaker (in seconds)
                  silenceCountdown: silenceCountdown,
                  isInCountdown: isInSilenceCountdown,
                  decision: meetingEndDecision.reason,
                };

                (window as any).logBot(
                  `🎯 Meeting Debug: ${JSON.stringify(debugInfo, null, 2)}`
                );

                // Decide whether to leave
                if (meetingEndDecision.shouldLeave) {
                  (window as any).logBot(
                    `🚪 Leaving meeting: ${meetingEndDecision.reason}`
                  );
                  clearInterval(checkInterval);
                  recorder.disconnect();
                  (window as any).triggerNodeGracefulLeave();
                  resolve();
                }
              }, 5000);

              // Listen for unload and visibility changes
              window.addEventListener("beforeunload", () => {
                (window as any).logBot(
                  "Page is unloading. Stopping recorder..."
                );
                clearInterval(checkInterval);
                recorder.disconnect();
                (window as any).triggerNodeGracefulLeave();
                resolve();
              });
              document.addEventListener("visibilitychange", () => {
                if (document.visibilityState === "hidden") {
                  (window as any).logBot(
                    "Document is hidden. Stopping recorder..."
                  );
                  clearInterval(checkInterval);
                  recorder.disconnect();
                  (window as any).triggerNodeGracefulLeave();
                  resolve();
                }
              });
            })
            .catch((err) => {
              reject(err);
            });
        } catch (error: any) {
          return reject(new Error("[BOT Error] " + error.message));
        }
      });
    },
    {
      botConfigData: botConfig,
      whisperUrlForBrowser: whisperLiveUrlFromEnv,
      exitLogicConfig: exitLogicConfig,
    }
  ); // Pass arguments to page.evaluate
};

// Remove the compatibility shim 'recordMeeting' if no longer needed,
// otherwise, ensure it constructs a valid BotConfig object.
// Example if keeping:
/*
const recordMeeting = async (page: Page, meetingUrl: string, token: string, connectionId: string, platform: "google_meet" | "zoom" | "teams") => {
  await prepareForRecording(page);
  // Construct a minimal BotConfig - adjust defaults as needed
  const dummyConfig: BotConfig = {
      platform: platform,
      meetingUrl: meetingUrl,
      botName: "CompatibilityBot",
      token: token,
      connectionId: connectionId,
      nativeMeetingId: "", // Might need to derive this if possible
      automaticLeave: { waitingRoomTimeout: 300000, noOneJoinedTimeout: 300000, everyoneLeftTimeout: 300000 },
  };
  await startRecording(page, dummyConfig);
};
*/

// --- ADDED: Exported function to trigger leave from Node.js ---
export async function leaveGoogleMeet(page: Page): Promise<boolean> {
  log("[leaveGoogleMeet] Triggering leave action in browser context...");
  if (!page || page.isClosed()) {
    log("[leaveGoogleMeet] Page is not available or closed.");
    return false;
  }
  try {
    // Call the function exposed within the page's evaluate context
    const result = await page.evaluate(async () => {
      if (typeof (window as any).performLeaveAction === "function") {
        return await (window as any).performLeaveAction();
      } else {
        (window as any).logBot?.(
          "[Node Eval Error] performLeaveAction function not found on window."
        );
        console.error(
          "[Node Eval Error] performLeaveAction function not found on window."
        );
        return false;
      }
    });
    log(`[leaveGoogleMeet] Browser leave action result: ${result}`);
    return result; // Return true if leave was attempted, false otherwise
  } catch (error: any) {
    log(
      `[leaveGoogleMeet] Error calling performLeaveAction in browser: ${error.message}`
    );
    return false;
  }
}
// --- ------------------------------------------------------- ---
