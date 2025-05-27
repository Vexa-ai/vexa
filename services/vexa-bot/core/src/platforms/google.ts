import { Page } from "playwright";
import { log, randomDelay } from "../utils";
import { BotConfig } from "../types";
import { v4 as uuidv4 } from "uuid"; // Import UUID

// Define the MeetingConfig interface
export interface MeetingConfig {
  sessionUid?: string;
  language?: string;
  token: string;
  meetingId?: string;
  whisperLiveUrl?: string;
}

// Add global declaration for window.vexaSocket property
declare global {
  interface Window {
    vexaSocket: WebSocket;
  }
}

// --- ADDED: Function to generate UUID (if not already present globally) ---
// If you have a shared utils file for this, import from there instead.
function generateUUID() {
  return uuidv4();
}
// --- --------------------------------------------------------- ---

export async function handleGoogleMeet(
  botConfig: BotConfig,
  page: Page
): Promise<void> {
  const leaveButton = `//button[@aria-label="Leave call"]`;

  if (!botConfig.meetingUrl) {
    log("Error: Meeting URL is required for Google Meet but is null.");
    return;
  }

  log("Joining Google Meet");
  try {
    await joinMeeting(page, botConfig.meetingUrl, botConfig.botName);
  } catch (error: any) {
    console.error(error.message);
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
      return;
    }

    log("Successfully admitted to the meeting, starting recording");
    // Pass platform from botConfig to startRecording
    await startRecording(page, botConfig);
  } catch (error: any) {
    console.error(error.message);
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
const startRecording = async (page: Page, botConfig: BotConfig) => {
  // Destructure needed fields from botConfig
  const { meetingUrl, token, connectionId, platform, nativeMeetingId } =
    botConfig; // nativeMeetingId is now in BotConfig type

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

  // Pass the necessary config fields and the resolved URL into the page context. Inisde page.evalute we have the browser context.
  //All code inside page.evalute executes as javascript running in the browser.
  await page.evaluate(
    async (pageArgs: {
      botConfigData: BotConfig;
      whisperUrlForBrowser: string;
    }) => {
      const { botConfigData, whisperUrlForBrowser } = pageArgs;
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

      // Global cleanup function to prevent memory leaks
      let cleanupFunctions: (() => void)[] = [];
      const addCleanupFunction = (fn: () => void) => {
        cleanupFunctions.push(fn);
      };

      const performGlobalCleanup = () => {
        (window as any).logBot("Performing global cleanup...");
        cleanupFunctions.forEach((fn) => {
          try {
            fn();
          } catch (err) {
            console.error("Error during cleanup:", err);
          }
        });
        cleanupFunctions = [];
      };

      await new Promise<void>((resolve, reject) => {
        try {
          (window as any).logBot("Starting recording process.");
          const mediaElements = Array.from(
            document.querySelectorAll("audio, video")
          ).filter((el: any) => !el.paused);
          if (mediaElements.length === 0) {
            return reject(
              new Error(
                "[BOT Error] No active media elements found. Ensure the meeting media is playing."
              )
            );
          }

          // NEW: Create audio context and destination for mixing multiple streams
          (window as any).logBot(
            `Found ${mediaElements.length} active media elements.`
          );
          const audioContext = new AudioContext();
          const destinationNode = audioContext.createMediaStreamDestination();
          let sourcesConnected = 0;

          // Add audio context to cleanup
          addCleanupFunction(() => {
            if (audioContext.state !== "closed") {
              audioContext.close();
            }
          });

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

          // --- MODIFIED: Keep original connectionId but don't use it for WebSocket UID ---
          // const sessionUid = connectionId; // <-- OLD: Reused original connectionId
          (window as any).logBot(
            `Original bot connection ID: ${originalConnectionId}`
          );
          // --- ------------------------------------------------------------------------ ---

          const wsUrl = whisperUrlForBrowser;
          if (!wsUrl) {
            (window as any).logBot?.(
              "CRITICAL: WhisperLive WebSocket URL is missing in browser context!"
            );
            console.error(
              "CRITICAL: WhisperLive WebSocket URL is missing in browser context!"
            );
            return reject(new Error("WhisperLive WebSocket URL is missing"));
          }

          // --- ADD Browser-scope state for current WS config ---
          let currentWsLanguage = initialLanguage;
          let currentWsTask = initialTask;
          // --- -------------------------------------------- ---

          let socket: WebSocket | null = null;
          let isServerReady = false;
          let retryCount = 0;
          const maxRetries = 5;
          const retryDelay = 2000;

          // --- ADDED: New interval reference for advanced speaker monitoring ---
          let micPollingInterval: ReturnType<typeof setInterval> | null = null;
          // --- --------------------------------------------------------------- ---

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

              socket = new WebSocket(wsUrl);

              socket.onopen = function () {
                // --- MODIFIED: Log current config being used ---
                // --- MODIFIED: Generate NEW UUID for this connection ---
                const currentSessionUid = generateUUID();
                (window as any).currentWsUid = currentSessionUid; // Store for speaker updates

                (window as any).logBot(
                  `WebSocket connection opened. Using Lang: ${currentWsLanguage}, Task: ${currentWsTask}, New UID: ${currentSessionUid}`
                );
                retryCount = 0;

                if (socket) {
                  // Construct the initial configuration message using config values
                  const initialConfigPayload = {
                    uid: currentSessionUid, // <-- Use NEWLY generated UUID
                    language: currentWsLanguage || null, // <-- Use browser-scope variable
                    task: currentWsTask || "transcribe", // <-- Use browser-scope variable
                    model: "medium", // Keep default or make configurable if needed
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

                  // --- MODIFIED: Start new advanced speaker monitoring ---
                  startAdvancedSpeakerMonitoring(); // Re-enabled advanced speaker monitoring
                  // --- ------------------------------------------------- ---
                }
              };

              socket.onmessage = (event) => {
                try {
                  (window as any).logBot("Received message: " + event.data);
                  const data = JSON.parse(event.data);

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
                    (window as any).logBot(
                      `Transcription: ${JSON.stringify(data)}`
                    );
                  }
                } catch (err: any) {
                  (window as any).logBot(
                    `Error parsing WebSocket message: ${err.message}`
                  );
                }
              };

              socket.onerror = (event) => {
                (window as any).logBot(
                  `WebSocket error: ${JSON.stringify(event)}`
                );
              };

              socket.onclose = (event) => {
                (window as any).logBot(
                  `WebSocket connection closed. Code: ${event.code}, Reason: ${event.reason}`
                );

                isServerReady = false; // Reset server ready state

                // Clean up intervals when socket closes
                if (micPollingInterval) {
                  clearInterval(micPollingInterval);
                  micPollingInterval = null;
                }

                // Retry logic
                if (retryCount < maxRetries) {
                  const exponentialDelay = retryDelay * Math.pow(2, retryCount);
                  retryCount++;
                  (window as any).logBot(
                    `Attempting to reconnect in ${exponentialDelay}ms. Retry ${retryCount}/${maxRetries}`
                  );

                  setTimeout(() => {
                    (window as any).logBot(
                      `Retrying WebSocket connection (${retryCount}/${maxRetries})...`
                    );
                    setupWebSocket();
                  }, exponentialDelay);
                } else {
                  (window as any).logBot(
                    "Maximum WebSocket reconnection attempts reached. Giving up."
                  );
                  performGlobalCleanup();
                  reject(
                    new Error(
                      "WebSocket connection failed after maximum retries"
                    )
                  );
                }
              };

              // Add socket cleanup
              addCleanupFunction(() => {
                if (socket && socket.readyState === WebSocket.OPEN) {
                  socket.close();
                }
              });
            } catch (e: any) {
              (window as any).logBot(`Error creating WebSocket: ${e.message}`);
              // For initial connection errors, handle with retry logic
              if (retryCount < maxRetries) {
                const exponentialDelay = retryDelay * Math.pow(2, retryCount);
                retryCount++;
                (window as any).logBot(
                  `Attempting to reconnect in ${exponentialDelay}ms. Retry ${retryCount}/${maxRetries}`
                );

                setTimeout(() => {
                  (window as any).logBot(
                    `Retrying WebSocket connection (${retryCount}/${maxRetries})...`
                  );
                  setupWebSocket();
                }, exponentialDelay);
              } else {
                performGlobalCleanup();
                return reject(
                  new Error(
                    `WebSocket creation failed after ${maxRetries} attempts: ${e.message}`
                  )
                );
              }
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
              setupWebSocket();
            }
          };
          // --- ----------------------------------------------------------- ---

          // --- ADDED: Expose leave function to Node context ---
          (window as any).performLeaveAction = async () => {
            (window as any).logBot(
              "Attempting to leave the meeting from browser context..."
            );
            try {
              // Updated selectors to be more robust
              const primaryLeaveButtonXpath = `//button[@aria-label="Leave call" or @aria-label="End call"]`;
              const secondaryLeaveButtonXpath = `//button[contains(.,"Leave") or contains(.,"End call") or contains(.,"Exit")]`;

              const getElementByXpath = (path: string): HTMLElement | null => {
                try {
                  const result = document.evaluate(
                    path,
                    document,
                    null,
                    XPathResult.FIRST_ORDERED_NODE_TYPE,
                    null
                  );
                  return result.singleNodeValue as HTMLElement | null;
                } catch (err: any) {
                  (window as any).logBot(
                    `Error evaluating XPath ${path}: ${err.message}`
                  );
                  return null;
                }
              };

              const primaryLeaveButton = getElementByXpath(
                primaryLeaveButtonXpath
              );
              if (primaryLeaveButton) {
                (window as any).logBot("Clicking primary leave button...");
                primaryLeaveButton.click();
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
                  (window as any).logBot("Secondary leave button not found.");
                }
                (window as any).logBot("Leave sequence completed.");
                performGlobalCleanup(); // Clean up before leaving
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

          // FIXED: Revert to original audio processing that works with whisperlive
          // but use our combined stream as the input source
          const context = new AudioContext();
          const mediaStream = context.createMediaStreamSource(stream); // Use our combined stream
          const recorder = context.createScriptProcessor(4096, 1, 1);

          // Add recorder cleanup
          addCleanupFunction(() => {
            try {
              recorder.disconnect();
              if (context.state !== "closed") {
                context.close();
              }
            } catch (err) {
              console.error("Error cleaning up audio context:", err);
            }
          });

          recorder.onaudioprocess = async (event) => {
            // Check if server is ready AND socket is open
            if (
              !isServerReady ||
              !socket ||
              socket.readyState !== WebSocket.OPEN
            ) {
              return;
            }
            const inputData = event.inputBuffer.getChannelData(0);
            const data = new Float32Array(inputData);
            const targetLength = Math.round(
              data.length * (16000 / context.sampleRate)
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
              socket.send(resampledData); // send teh audio to whisperlive socket.

              //TODO: include speaker information...
            }
          };

          // Connect the audio processing pipeline
          mediaStream.connect(recorder);
          recorder.connect(context.destination);

          (window as any).logBot(
            "Audio processing pipeline connected and sending data."
          );

          // Click the "People" button with better error handling
          const peopleButtonSelectors = [
            'button[aria-label^="People"]',
            'button[aria-label="People"]',
            'button[data-panel-id="1"]',
          ];

          let peopleButton: Element | null = null;
          for (const selector of peopleButtonSelectors) {
            peopleButton = document.querySelector(selector);
            if (peopleButton) break;
          }

          if (!peopleButton) {
            (window as any).logBot(
              "Warning: People button not found using any selector. Speaker detection may not work."
            );
          } else {
            (peopleButton as HTMLElement).click();
          }

          // Monitor participant list every 5 seconds
          let aloneTime = 0;
          const checkInterval = setInterval(() => {
            try {
              const peopleList = document.querySelector('[role="list"]');
              if (!peopleList) {
                (window as any).logBot(
                  "Participant list not found; assuming meeting ended."
                );
                clearInterval(checkInterval);
                performGlobalCleanup();
                resolve();
                return;
              }
              const count = peopleList.childElementCount;
              (window as any).logBot("Participant count: " + count);

              if (count <= 1) {
                aloneTime += 5;
                (window as any).logBot(
                  "Bot appears alone for " + aloneTime + " seconds..."
                );
              } else {
                aloneTime = 0;
              }

              if (aloneTime >= 10 || count === 0) {
                (window as any).logBot(
                  "Meeting ended or bot alone for too long. Stopping recorder..."
                );
                clearInterval(checkInterval);
                performGlobalCleanup();
                resolve();
              }
            } catch (err: any) {
              (window as any).logBot(
                `Error in participant check: ${err.message}`
              );
            }
          }, 5000);

          // Listen for unload and visibility changes
          window.addEventListener("beforeunload", () => {
            (window as any).logBot(
              "Page is unloading. Stopping recorder and speaker detection..."
            );
            if (micPollingInterval) {
              clearInterval(micPollingInterval);
              micPollingInterval = null;
            }
            if (checkInterval) clearInterval(checkInterval);
            recorder.disconnect();
            resolve();
          });

          // Listen for unload and visibility changes
          const handleUnload = () => {
            (window as any).logBot(
              "Page is unloading. Stopping recorder and speaker detection..."
            );
            performGlobalCleanup();
            resolve();
          };

          const handleVisibilityChange = () => {
            if (document.visibilityState === "hidden") {
              (window as any).logBot(
                "Document is hidden. Stopping recorder..."
              );
              if (micPollingInterval) {
                clearInterval(micPollingInterval);
                micPollingInterval = null;
              }
              if (checkInterval) clearInterval(checkInterval);
              recorder.disconnect();
              resolve();
            }
          };

          window.addEventListener("beforeunload", handleUnload);
          document.addEventListener("visibilitychange", handleVisibilityChange);

          // Add event listener cleanup
          addCleanupFunction(() => {
            window.removeEventListener("beforeunload", handleUnload);
            document.removeEventListener(
              "visibilitychange",
              handleVisibilityChange
            );
          });

          // --- ADDED: New Advanced Speaker Monitoring Function ---
          const startAdvancedSpeakerMonitoring = () => {
            (window as any).logBot("Starting advanced speaker monitoring...");

            let participantNodes: Array<{
              id: string;
              name: string;
              el: HTMLElement;
              micNode: HTMLElement;
              micActivity: string[]; // Stores '0' or '1'
            }> = [];
            let callName: string | null = null;
            let nodeRefreshCounter = 0; // To refresh nodes less frequently
            let sendDataCounter = 0; // To send data less frequently than raw polling

            const peopleButtonSelector =
              'button[aria-label="People"][data-panel-id="1"]';

            const ensurePeoplePanelOpen = async () => {
              const peopleButton = document.querySelector(
                peopleButtonSelector
              ) as HTMLElement;
              if (!peopleButton) {
                (window as any).logBot(
                  "WARNING: People button not found for speaker detection."
                );
                return false;
              }
              const isPressed = peopleButton.getAttribute("aria-pressed");
              if (isPressed !== "true") {
                (window as any).logBot(
                  "Opening People panel for speaker detection..."
                );
                peopleButton.click();
                await new Promise((r) => setTimeout(r, 1500)); // Wait for panel to open
              }
              return true;
            };

            micPollingInterval = setInterval(async () => {
              try {
                if (
                  !socket ||
                  socket.readyState !== WebSocket.OPEN ||
                  !isServerReady
                ) {
                  // (window as any).logBot( // Optional: this log can be very noisy
                  //   "AdvancedSpeakerMonitoring: WS not ready or closed, skipping cycle."
                  // );
                  return;
                }

                nodeRefreshCounter++;
                sendDataCounter++;

                // Refresh participant nodes and call name every 50 cycles (50 * 50ms = 2.5 seconds)
                if (nodeRefreshCounter % 50 === 1) {
                  if (!(await ensurePeoplePanelOpen())) {
                    (window as any).logBot(
                      "AdvancedSpeakerMonitoring: Cannot ensure people panel is open. Skipping node refresh."
                    );
                    // Potentially stop or retry opening panel later
                  } else {
                    participantNodes = [];
                    callName =
                      (
                        document.querySelector(
                          "[jscontroller=yEvoid]"
                        ) as HTMLDivElement | null
                      )?.innerText?.trim() || "Unknown Call";

                    const participantElements = Array.from(
                      document.querySelectorAll(
                        'div[role="listitem"][data-participant-id]'
                      )
                    ) as HTMLElement[];

                    (window as any).logBot(
                      `AdvancedSpeakerMonitoring: Found ${participantElements.length} participant elements.`
                    );

                    participantElements.forEach((el: HTMLElement) => {
                      const participantId = el.getAttribute(
                        "data-participant-id"
                      );
                      if (!participantId) return;

                      let nameNode = el.querySelector(
                        "[data-self-name]"
                      ) as HTMLElement;
                      if (!nameNode) {
                        // Fallback selector, adjust if needed based on actual Google Meet DOM for participant names
                        nameNode = el.querySelector(
                          "span.zWGUib"
                        ) as HTMLElement; // Common selector for name
                      }
                      // More robust name extraction, trying different common selectors
                      let name = "Unknown Participant";
                      if (nameNode) {
                        name =
                          nameNode.innerText?.split("\n").pop()?.trim() ||
                          `Participant ${participantId}`;
                      } else {
                        // Try another common selector if the first one fails
                        const secondaryNameNode = el.querySelector(
                          ".cS7aqe.N2K3jd"
                        ) as HTMLElement; // Another possible name element
                        if (secondaryNameNode) {
                          name =
                            secondaryNameNode.innerText?.trim() ||
                            `Participant ${participantId}`;
                        } else {
                          (window as any).logBot(
                            `AdvancedSpeakerMonitoring: Name node not found for participant ${participantId}. Using default.`
                          );
                        }
                      }

                      const micNode = el.querySelector(
                        'div[jscontroller="ES310d"]'
                      ) as HTMLElement; // Google's mic status container

                      if (micNode && name) {
                        // Ensure micNode and name are found
                        participantNodes.push({
                          id: participantId,
                          name: name,
                          el: el,
                          micNode: micNode,
                          micActivity: [],
                        });
                      } else {
                        (window as any).logBot(
                          `AdvancedSpeakerMonitoring: Skipping participant ${participantId} due to missing mic or name node.`
                        );
                      }
                    });
                    (window as any).logBot(
                      `AdvancedSpeakerMonitoring: Refreshed ${participantNodes.length} participant nodes. Call Name: ${callName}`
                    );
                  }
                }

                // Record mic activity for each participant
                participantNodes.forEach((node) => {
                  if (node && node.micNode) {
                    const isSpeaking = node.micNode.classList.contains("HX2H7");
                    node.micActivity.push(isSpeaking ? "1" : "0");
                  }
                });

                // Send data every 10 cycles (10 * 50ms = 0.5 seconds)
                if (sendDataCounter % 10 === 0) {
                  if (
                    socket &&
                    socket.readyState === WebSocket.OPEN &&
                    isServerReady &&
                    participantNodes.length > 0
                  ) {
                    const timestamp = new Date().toISOString();
                    const payload = {
                      type: "speaker_activity_update",
                      uid: (window as any).currentWsUid || generateUUID(),
                      meeting_id: nativeMeetingId,
                      call_name: callName,
                      timestamp: timestamp,
                      speakers: participantNodes.map((p) => ({
                        speaker_id: p.id,
                        speaker_name: p.name,
                        mic_activity_bits: p.micActivity.join(""),
                      })),
                    };
                    let payloadStr;
                    try {
                      payloadStr = JSON.stringify(payload);
                    } catch (stringifyError: any) {
                      (window as any).logBot(
                        `AdvancedSpeakerMonitoring: ERROR stringifying payload: ${stringifyError.message}`
                      );
                      return; // Don't proceed if payload is bad
                    }
                    (window as any).logBot(
                      `AdvancedSpeakerMonitoring: Sending speaker activity payload: ${payloadStr}`
                    );
                    socket.send(payloadStr);
                    (window as any).logBot(
                      `AdvancedSpeakerMonitoring: Sent speaker activity update. Participants: ${payload.speakers.length}`
                    );

                    participantNodes.forEach((p) => (p.micActivity = []));
                  } else if (
                    participantNodes.length === 0 &&
                    nodeRefreshCounter > 50
                  ) {
                    (window as any).logBot(
                      "AdvancedSpeakerMonitoring: No participant nodes to send data for, or socket not ready."
                    );
                  }
                }
              } catch (error: any) {
                (window as any).logBot(
                  `AdvancedSpeakerMonitoring: Interval CRITICAL ERROR: ${
                    error.message
                  }${error.stack ? "\nStack: " + error.stack : ""}`
                );
                // Optional: To prevent repeated errors, though this might hide the root cause.
                // if (micPollingInterval) {
                //   clearInterval(micPollingInterval);
                //   micPollingInterval = null;
                //   (window as any).logBot("AdvancedSpeakerMonitoring: Cleared interval due to critical error.");
                // }
              }
            }, 50); // Poll every 50ms
          };
          // --- END OF New Advanced Speaker Monitoring Function ---
        } catch (error: any) {
          performGlobalCleanup();
          return reject(new Error("[BOT Error] " + error.message));
        }
      });
    },
    { botConfigData: botConfig, whisperUrlForBrowser: whisperLiveUrlFromEnv }
  );
};

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
    return result;
  } catch (error: any) {
    log(
      `[leaveGoogleMeet] Error calling performLeaveAction in browser: ${error.message}`
    );
    return false;
  }
}
// --- ------------------------------------------------------- ---

// First, add a speaker detection function that will be run in the browser context

// Add a speakerDetection module
export async function runWithSpeakerDetection(page: Page, socket: WebSocket) {
  // Implement speaker detection and send updates through the socket
  await page.evaluate((socketUrl: string) => {
    console.log("Starting speaker detection in Google Meet...");

    // Initialize WebSocket connection if it doesn't exist
    // Note: In the actual implementation, this socket is created by WhisperLive client
    // and we would just reuse it instead of creating a new one.
    // This is a simplification for this code snippet.
    const socket = new WebSocket(socketUrl);

    // Keep track of the current active speaker
    let currentSpeakerId: string | null = null;
    let currentSpeakerName: string | null = null;
    let speakerPanelOpen = false;

    // Function to check if the speakers panel is open
    function isSpeakerPanelOpen(): boolean {
      const peopleButton = document.querySelector(
        'button[aria-label="People"][data-panel-id="1"]'
      );
      return (
        !!peopleButton && peopleButton.getAttribute("aria-pressed") === "true"
      );
    }

    // Function to open the speakers panel if closed
    function openSpeakersPanel(): boolean {
      const peopleButton = document.querySelector(
        'button[aria-label="People"][data-panel-id="1"]'
      );
      if (
        peopleButton &&
        peopleButton.getAttribute("aria-pressed") === "false"
      ) {
        console.log("Opening speakers panel...");
        (peopleButton as HTMLElement).click();
        return true;
      }
      return false;
    }

    // Function to detect the active speaker from the participant list
    function detectActiveSpeaker(): void {
      try {
        // First check if the panel is open
        speakerPanelOpen = isSpeakerPanelOpen();
        if (!speakerPanelOpen) {
          const wasOpened = openSpeakersPanel();
          if (wasOpened) {
            // Give time for panel to open
            setTimeout(detectActiveSpeaker, 500);
            return;
          }
        }

        // Look for speaking indicators in the participant list
        const participants = document.querySelectorAll(
          'div[role="listitem"][jscontroller="ZHOeze"]'
        );

        let newSpeakerId: string | null = null;
        let newSpeakerName: string | null = null;

        participants.forEach((participant) => {
          // Check for the speaking indicator class (Google's active speaker highlight)
          const isSpeaking = participant.classList.contains("v21hqf"); // Active speaker class

          if (isSpeaking) {
            // Get participant name and ID
            const nameElement = participant.querySelector(".zWGUib");
            const participantId = participant.getAttribute(
              "data-participant-id"
            );

            if (nameElement && participantId) {
              newSpeakerName = nameElement.textContent?.trim() || null;
              newSpeakerId = participantId;

              // Only send update if speaker changed
              if (
                newSpeakerId !== currentSpeakerId ||
                newSpeakerName !== currentSpeakerName
              ) {
                console.log(
                  `Active speaker: ${newSpeakerName} (${newSpeakerId})`
                );
                currentSpeakerId = newSpeakerId;
                currentSpeakerName = newSpeakerName;

                // Send the speaker update through WebSocket
                if (socket && socket.readyState === WebSocket.OPEN) {
                  socket.send(
                    JSON.stringify({
                      type: "speaker_update",
                      speaker_id: newSpeakerId,
                      speaker_name: newSpeakerName,
                      timestamp: new Date().toISOString(),
                    })
                  );
                }
              }
            }
          }
        });

        // If no active speaker found, but we had one before
        if (!newSpeakerId && currentSpeakerId) {
          console.log("No active speaker detected (not sending null update)");
          currentSpeakerId = null;
          currentSpeakerName = null;

          /* Commenting out the null update
          // Send update that no one is speaking
          if (socket && socket.readyState === WebSocket.OPEN) {
            socket.send(
              JSON.stringify({
                type: "speaker_update",
                speaker_id: null,
                speaker_name: null,
                timestamp: new Date().toISOString(),
              })
            );
          }
          */
        }
      } catch (error) {
        console.error("Error detecting active speaker:", error);
      }
    }

    // Run the speaker detection periodically
    const speakerDetectionInterval = setInterval(detectActiveSpeaker, 1000);

    // Clean up
    window.addEventListener("beforeunload", () => {
      clearInterval(speakerDetectionInterval);
    });
  }, socket.url);
}

// Modify the existing joinMeeting function to add speaker detection
export async function joinMeetingWithSpeakerDetection(
  page: Page,
  meetingUrl: string,
  config: MeetingConfig
): Promise<void> {
  await page.goto(meetingUrl, { waitUntil: "networkidle" });

  // Execute existing joining logic...

  // After successfully joining, set up audio capture and speaker detection
  const socket = await setupAudioCapture(page, config);

  // Now also run speaker detection on the same page
  if (socket) {
    await runWithSpeakerDetection(page, socket);
  }
}

// This is the updated setupAudioCapture function
async function setupAudioCapture(
  page: Page,
  config: MeetingConfig
): Promise<WebSocket | null> {
  try {
    // Get the WebSocket URL from config or default
    const wsUrl = config.whisperLiveUrl || "ws://localhost:9090";

    // Create WebSocket connection for sending audio data
    const socket = new WebSocket(wsUrl);

    // Use addEventListener instead of .on method (which doesn't exist on the WebSocket interface)
    socket.addEventListener("open", () => {
      const options = {
        uid: config.sessionUid || uuidv4(),
        language: config.language || "en",
        task: "transcribe",
        initial_prompt: "",
        platform: "google_meet",
        meeting_url: page.url(),
        token: config.token,
        meeting_id: config.meetingId || extractMeetingIdFromUrl(page.url()),
      };

      socket.send(JSON.stringify(options));
    });

    // Set up audio capture in the browser
    await page.evaluate((socketUrl: string) => {
      // Define vexaSocket on the window for TypeScript
      window.vexaSocket = null as unknown as WebSocket;

      const audioContext = new AudioContext();
      const destination = audioContext.createMediaStreamDestination();

      // Create audio processor
      const processor = audioContext.createScriptProcessor(4096, 1, 1);
      processor.connect(destination);

      processor.onaudioprocess = (e) => {
        // Convert audio data to Float32Array
        const input = e.inputBuffer.getChannelData(0);

        // Send to server if socket is ready
        if (
          window.vexaSocket &&
          window.vexaSocket.readyState === WebSocket.OPEN
        ) {
          window.vexaSocket.send(input);
        }
      };

      // Connect to all audio elements as they appear
      const connectToAudioElements = () => {
        const audioElements = document.querySelectorAll("audio");
        audioElements.forEach((audio) => {
          if (!audio.dataset.connected) {
            try {
              const source = audioContext.createMediaElementSource(audio);
              source.connect(processor);
              source.connect(audioContext.destination);
              audio.dataset.connected = "true";
              console.log("Connected to audio element:", audio);
            } catch (err) {
              console.error("Error connecting to audio:", err);
            }
          }
        });
      };

      // Initial connection
      connectToAudioElements();

      // Observe DOM for new audio elements
      const observer = new MutationObserver(() => {
        connectToAudioElements();
      });

      observer.observe(document.body, {
        childList: true,
        subtree: true,
      });

      // Create WebSocket and store in window for global access
      const socket = new WebSocket(socketUrl);
      window.vexaSocket = socket;

      // Clean up on page unload
      window.addEventListener("beforeunload", () => {
        processor.disconnect();
        observer.disconnect();
        if (window.vexaSocket) {
          window.vexaSocket.close();
        }
      });
    }, wsUrl);

    return socket;
  } catch (error) {
    console.error("Error setting up audio capture:", error);
    return null;
  }
}

// Helper function for meeting URL parsing
function extractMeetingIdFromUrl(url: string): string {
  const match = url.match(/\/([a-z]{3}-[a-z]{4}-[a-z]{3})/);
  return match ? match[1] : "";
}
