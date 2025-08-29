import { Page } from "playwright";
import { log, randomDelay } from "../utils";
import { BotConfig } from "../types";
import { v4 as uuidv4 } from "uuid"; // Import UUID

// --- ADDED: Function to generate UUID (if not already present globally) ---
// If you have a shared utils file for this, import from there instead.
function generateUUID() {
  return uuidv4();
}

// --- ADDED: Function to call startup callback ---
async function callStartupCallback(botConfig: BotConfig): Promise<void> {
  if (!botConfig.botManagerCallbackUrl) {
    log("Warning: No bot manager callback URL configured. Cannot send startup callback.");
    return;
  }

  if (!botConfig.container_name) {
    log("Warning: No container name configured. Cannot send startup callback.");
    return;
  }

  try {
    // Extract the base URL and modify it for the startup callback
    const baseUrl = botConfig.botManagerCallbackUrl.replace('/exited', '/started');
    const startupUrl = baseUrl;
    
    const payload = {
      connection_id: botConfig.connectionId,
      container_id: botConfig.container_name
    };

    log(`Sending startup callback to ${startupUrl} with payload: ${JSON.stringify(payload)}`);
    
    // Use fetch API (available in Node.js 18+)
    const response = await fetch(startupUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload)
    });

    if (response.ok) {
      const result = await response.json();
      log(`Startup callback successful: ${JSON.stringify(result)}`);
    } else {
      log(`Startup callback failed with status ${response.status}: ${response.statusText}`);
    }
  } catch (error: any) {
    log(`Error sending startup callback: ${error.message}`);
  }
}
// --- --------------------------------------------------------- ---

export async function handleGoogleMeet(
  botConfig: BotConfig,
  page: Page,
  gracefulLeaveFunction: (page: Page | null, exitCode: number, reason: string, errorDetails?: any) => Promise<void>
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
    log("Error during joinMeeting: " + error.message + ". Triggering graceful leave.");
    
    const errorDetails = {
      error_message: error.message,
      error_stack: error.stack,
      error_name: error.name,
      context: "join_meeting_error",
      platform: "google_meet",
      timestamp: new Date().toISOString()
    };
    
    await gracefulLeaveFunction(page, 1, "join_meeting_error", errorDetails);
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
      log("Bot was not admitted into the meeting within the timeout period. This is a normal completion.");
      
      await gracefulLeaveFunction(page, 0, "admission_failed");
      return; 
    }

    log("Successfully admitted to the meeting, starting recording");
    
    // --- ADDED: Call startup callback to notify bot-manager that bot is active ---
    try {
      await callStartupCallback(botConfig);
      log("Startup callback sent successfully");
    } catch (callbackError: any) {
      log(`Warning: Failed to send startup callback: ${callbackError.message}. Continuing with recording...`);
    }
    
    // Pass platform from botConfig to startRecording
    await startRecording(page, botConfig);
  } catch (error: any) {
    console.error("Error after join attempt (admission/recording setup): " + error.message);
    log("Error after join attempt (admission/recording setup): " + error.message + ". Triggering graceful leave.");
    
    // Capture detailed error information for debugging
    const errorDetails = {
      error_message: error.message,
      error_stack: error.stack,
      error_name: error.name,
      context: "post_join_setup_error",
      platform: "google_meet",
      timestamp: new Date().toISOString()
    };
    
    // Use a general error code here, as it could be various issues.
    await gracefulLeaveFunction(page, 1, "post_join_setup_error", errorDetails);
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

  // Ensure leave function is available even before admission
  await page.evaluate(() => {
    if (typeof (window as any).performLeaveAction !== "function") {
      (window as any).performLeaveAction = async () => {
        try {
          const primaryLeaveButtonXpath = `//button[@aria-label="Leave call"]`;
          const secondaryLeaveButtonXpath = `//button[.//span[text()='Leave meeting']] | //button[.//span[text()='Just leave the meeting']]`;

          const getElementByXpath = (path: string): HTMLElement | null => {
            const result = document.evaluate(
              path,
              document,
              null,
              XPathResult.FIRST_ORDERED_NODE_TYPE,
              null
            );
            return result.singleNodeValue as HTMLElement | null;
          };

          const primaryLeaveButton = getElementByXpath(primaryLeaveButtonXpath);
          if (primaryLeaveButton) {
            (window as any).logBot?.("Clicking primary leave button...");
            primaryLeaveButton.click();
            await new Promise((resolve) => setTimeout(resolve, 1000));
            const secondaryLeaveButton = getElementByXpath(secondaryLeaveButtonXpath);
            if (secondaryLeaveButton) {
              (window as any).logBot?.("Clicking secondary/confirmation leave button...");
              secondaryLeaveButton.click();
              await new Promise((resolve) => setTimeout(resolve, 500));
            }
            (window as any).logBot?.("Leave sequence completed.");
            return true;
          } else {
            (window as any).logBot?.("Primary leave button not found.");
            return false;
          }
        } catch (err: any) {
          (window as any).logBot?.(`Error during leave attempt: ${err.message}`);
          return false;
        }
      };
    }
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

  // --- Start: Minimal Refactor for Self-Healing WS Connection ---
  const { createClient } = await import("redis");
  const redisUrl = process.env.REDIS_URL || "redis://redis:6379/0";
  const redisClient = createClient({ url: redisUrl });
  await redisClient.connect();

  // Atomic allocation function using Redis Lua script
  const allocateServer = async (): Promise<string | null> => {
    // Capacity configured via env, default to 10
    const envMax = process && process.env && process.env.WL_MAX_CLIENTS;
    const maxClients = envMax ? parseInt(envMax, 10) : 10; // WhisperLive server capacity
    
    const luaScript = `
      -- Find server with lowest score that has capacity
      local servers = redis.call('ZRANGE', 'wl:rank', 0, -1, 'WITHSCORES')
      if #servers == 0 then
        return nil
      end
      
      -- Iterate through servers (format: url1, score1, url2, score2, ...)
      for i = 1, #servers, 2 do
        local url = servers[i]
        local score = tonumber(servers[i + 1])
        
        -- Check if server has capacity
        if score < tonumber(ARGV[1]) then
          -- Atomically increment score and return URL
          redis.call('ZINCRBY', 'wl:rank', 1, url)
          return url
        end
      end
      
      -- No server has capacity
      return nil
    `;
    
    try {
      log("[Node.js] Atomically allocating server using Lua script...");
      const allocatedUrl = await redisClient.eval(luaScript, {
        keys: [],
        arguments: [maxClients.toString()]
      }) as string | null;
      
      if (allocatedUrl) {
        log(`[Node.js] Allocated server: ${allocatedUrl} (capacity maxClients=${maxClients})`);
      } else {
        log(`{Node.js] No servers available with capacity (maxClients=${maxClients})`);
      }
      
      return allocatedUrl;
    } catch (e: any) {
      log(`[Node.js] Error in atomic server allocation: ${e.message}`);
      return null;
    }
  };

  // Deallocate function to decrement server score when bot disconnects
  const deallocateServer = async (serverUrl: string): Promise<void> => {
    try {
      log(`[Node.js] Deallocating server: ${serverUrl}`);
      await redisClient.zIncrBy("wl:rank", -1, serverUrl);
      log(`[Node.js] Successfully deallocated server: ${serverUrl}`);
    } catch (e: any) {
      log(`[Node.js] Error deallocating server: ${e.message}`);
    }
  };

  // Updated getNextCandidate that handles failed URLs and uses atomic allocation
  const getNextCandidate = async (failedUrl: string | null): Promise<string | null> => {
    log(`[Node.js] getNextCandidate called. Failed URL: ${failedUrl}`);
    
    if (failedUrl) {
      try {
        log(`[Node.js] Deallocating and removing failed server: ${failedUrl}`);
        // First deallocate the slot this bot was using
        await deallocateServer(failedUrl);
        // Then remove the failed server from the registry
        await redisClient.zRem("wl:rank", failedUrl);
      } catch (e: any) {
        log(`[Node.js] Error handling failed candidate: ${e.message}`);
      }
    }
    
    // Use atomic allocation to get next available server
    return await allocateServer();
  };

  await page.exposeFunction('getNextCandidate', getNextCandidate);
  
  // Expose deallocateServer function to browser context for cleanup
  await page.exposeFunction('deallocateServer', deallocateServer);
  
  // Track current allocated server for cleanup
  let currentAllocatedServer: string | null = null;
  
  // Resolve WhisperLive WebSocket URL dynamically – env override > initial Redis fetch  
  let whisperLiveUrlResolved: string | null = process.env.WHISPER_LIVE_URL || await getNextCandidate(null);
  const resolvedMaxClients = (process && process.env && process.env.WL_MAX_CLIENTS) ? parseInt(process.env.WL_MAX_CLIENTS as string, 10) : 10;
  log(`[Node.js] Effective capacity (maxClients) for allocation: ${resolvedMaxClients}`);
  
  // Track the initially allocated server (only if we used atomic allocation)
  if (whisperLiveUrlResolved && !process.env.WHISPER_LIVE_URL) {
    currentAllocatedServer = whisperLiveUrlResolved;
  }
  // --- End: Minimal Refactor ---

  if (!whisperLiveUrlResolved) {
    log("ERROR: Could not resolve WhisperLive WebSocket URL via env or Redis. Aborting recording.");
    await redisClient.quit(); // Ensure redis client is closed
    return;
  }
  log(`[Node.js] Using initial WhisperLive URL: ${whisperLiveUrlResolved}`);
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

      await new Promise<void>((resolve, reject) => {
        try {
          (window as any).logBot("Starting recording process.");
          
          // --- ADDED: More robust media element finding function ---
          const findMediaElements = async (retries = 5, delay = 2000): Promise<HTMLMediaElement[]> => {
            for (let i = 0; i < retries; i++) {
                const mediaElements = Array.from(
                    document.querySelectorAll("audio, video")
                ).filter((el: any) => 
                    !el.paused && 
                    el.srcObject instanceof MediaStream && 
                    el.srcObject.getAudioTracks().length > 0
                ) as HTMLMediaElement[];

                if (mediaElements.length > 0) {
                    (window as any).logBot(`Found ${mediaElements.length} active media elements with audio tracks after ${i + 1} attempt(s).`);
                    return mediaElements;
                }
                (window as any).logBot(`[Audio] No active media elements found. Retrying in ${delay}ms... (Attempt ${i + 2}/${retries})`);
                await new Promise(resolve => setTimeout(resolve, delay));
            }
            return [];
          };
          // --- END FUNCTION ---

          findMediaElements().then(mediaElements => {
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
            const destinationNode = audioContext.createMediaStreamDestination();
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

            // --- MODIFIED: Keep original connectionId but don't use it for WebSocket UID ---
            // const sessionUid = connectionId; // <-- OLD: Reused original connectionId
            (window as any).logBot(
              `Original bot connection ID: ${originalConnectionId}`
            );
            // --- ------------------------------------------------------------------------ ---

            // --- ADDED: Add secondary leave button selector for confirmation ---
            const secondaryLeaveButtonSelector = `//button[.//span[text()='Leave meeting']] | //button[.//span[text()='Just leave the meeting']]`; // Example, adjust based on actual UI
            // --- ----------------------------------------------------------- ---

            // const wsUrl = "ws://whisperlive:9090";
            const wsUrl = whisperUrlForBrowser;
            if (!wsUrl) {
              (window as any).logBot?.(
                "CRITICAL: WhisperLive WebSocket URL is missing in browser context!"
              );
              console.error(
                "CRITICAL: WhisperLive WebSocket URL is missing in browser context!"
              );
              return;
            }
            // (window as any).logBot(`Attempting to connect WebSocket to: ${wsUrl} with platform: ${platform}, session UID: ${sessionUid}`); // Log the correct UID

            // --- ADD Browser-scope state for current WS config ---
            let currentWsLanguage = initialLanguage;
            let currentWsTask = initialTask;
            // --- -------------------------------------------- ---

            let socket: WebSocket | null = null;
            let isServerReady = false;
            
            let sessionAudioStartTimeMs: number | null = null; // ADDED: For relative speaker timestamps

            const connectToWhisperLive = (wsUrl: string | null) => {
              if (!wsUrl) {
                (window as any).logBot("[Failover] No WhisperLive URL available. Will retry lookup in 5s.");
                setTimeout(async () => {
                  const freshUrl = await (window as any).getNextCandidate(null);
                  connectToWhisperLive(freshUrl);
                }, 5000);
                return;
              }

              try {
                socket = new WebSocket(wsUrl);

                // --- NEW: Force-close if connection cannot be established quickly ---
                const connectionTimeoutMs = 5000; // 5-second timeout for CONNECTING state
                const connectionTimeoutHandle = window.setTimeout(() => {
                  if (socket && socket.readyState === WebSocket.CONNECTING) {
                    (window as any).logBot(
                      `[Failover] Connection to ${wsUrl} timed out after ${connectionTimeoutMs}ms. Forcing close.`
                    );
                    socket.close(); // Triggers onclose -> retry logic
                  }
                }, connectionTimeoutMs);

                socket.onopen = function () {
                  clearTimeout(connectionTimeoutHandle);
                  
                  currentSessionUid = generateUUID(); 
                  sessionAudioStartTimeMs = null; 
                  (window as any).logBot(
                    `[Failover] WebSocket connection opened successfully to ${wsUrl}. New UID: ${currentSessionUid}. Lang: ${currentWsLanguage}, Task: ${currentWsTask}`
                  );
                  isServerReady = false; // Reset ready state for new connection

                  if (socket) {
                    const initialConfigPayload = {
                      uid: currentSessionUid,
                      language: currentWsLanguage || null,
                      task: currentWsTask || "transcribe",
                      model: null, // Let the server use WHISPER_MODEL_SIZE from environment
                      use_vad: true,
                      platform: platform,
                      token: token,
                      meeting_id: nativeMeetingId,
                      meeting_url: meetingUrl || null,
                    };

                    const jsonPayload = JSON.stringify(initialConfigPayload);
                    (window as any).logBot(
                      `Sending initial config message: ${jsonPayload}`
                    );
                    socket.send(jsonPayload);
                  }
                };

                socket.onmessage = (event) => {
                  (window as any).logBot("Received message: " + event.data);
                  const data = JSON.parse(event.data);

                  if (data["status"] === "ERROR") {
                    (window as any).logBot(`WebSocket Server Error: ${data["message"]}`);
                  } else if (data["status"] === "WAIT") {
                    (window as any).logBot(`Server busy: ${data["message"]}`);
                  } else if (!isServerReady && data["status"] === "SERVER_READY") {
                    isServerReady = true;
                    (window as any).logBot("Server is ready.");
                  } else if (data["language"]) {
                    (window as any).logBot(`Language detected: ${data["language"]}`);
                  } else if (data["message"] === "DISCONNECT") {
                    (window as any).logBot("Server requested disconnect.");
                    if (socket) socket.close();
                  } else {
                    (window as any).logBot(`Transcription: ${JSON.stringify(data)}`);
                  }
                };

                socket.onerror = (event) => {
                  clearTimeout(connectionTimeoutHandle);
                  (window as any).logBot(
                    `[Failover] WebSocket error for ${wsUrl}. This will trigger onclose.`
                  );
                };

                socket.onclose = async (event) => {
                  clearTimeout(connectionTimeoutHandle);
                  (window as any).logBot(
                    `[Failover] WebSocket connection to ${wsUrl} closed. Code: ${event.code}, Reason: ${event.reason}.`
                  );

                  // Call the exposed Node.js function to remove the bad URL and get a new one
                  (window as any).logBot(`[Failover] Asking for next candidate...`);
                  const nextUrl = await (window as any).getNextCandidate(wsUrl);

                  if (nextUrl) {
                    (window as any).logBot(`[Failover] Got next candidate: ${nextUrl}. Retrying in 1s.`);
                    setTimeout(() => connectToWhisperLive(nextUrl), 1000);
                  } else {
                    (window as any).logBot("[Failover] No more candidates available. Retrying lookup in 5s.");
                    setTimeout(async () => {
                      const freshUrl = await (window as any).getNextCandidate(null);
                      connectToWhisperLive(freshUrl);
                    }, 5000);
                  }
                };
              } catch (e: any) {
                (window as any).logBot(`[Failover] Critical error creating WebSocket: ${e.message}. Retrying...`);
                setTimeout(async () => {
                    const freshUrl = await (window as any).getNextCandidate(wsUrl);
                    connectToWhisperLive(freshUrl); // This will now handle null gracefully
                }, 5000);
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

                const getElementByXpath = (path: string): HTMLElement | null => {
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
                    (window as any).logBot("Secondary leave button not found.");
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

            connectToWhisperLive(whisperUrlForBrowser);

            // --- ADD: Speaker Detection Logic (Adapted from speakers_console_test.js) ---
            // Configuration for speaker detection
            const participantSelector = 'div[data-participant-id]'; // UPDATED: More specific selector
            const speakingClasses = ['Oaajhc', 'HX2H7', 'wEsLMd', 'OgVli']; // Speaking/animation classes
            const silenceClass = 'gjg47c';        // Class indicating the participant is silent
            const nameSelectors = [               // Try these selectors to find participant's name
                '[data-participant-id]'           // Attribute for participant ID
            ];

            // State for tracking speaking status
            const speakingStates = new Map(); // Stores the logical speaking state for each participant ID
            const activeParticipants = new Map(); // NEW: Central map for all known participants

            // Track current session UID for speaker events
            let currentSessionUid = generateUUID(); // Initialize with a new UID

            // Helper functions for speaker detection
            function getParticipantId(element: HTMLElement) {
                let id = element.getAttribute('data-participant-id');
                if (!id) {
                    const stableChild = element.querySelector('[jsinstance]');
                    if (stableChild) {
                        id = stableChild.getAttribute('jsinstance');
                    }
                }
                if (!id) {
                    if (!(element as any).dataset.vexaGeneratedId) {
                        (element as any).dataset.vexaGeneratedId = 'vexa-id-' + Math.random().toString(36).substr(2, 9);
                    }
                    id = (element as any).dataset.vexaGeneratedId;
                }
                return id;
            }

            function getParticipantName(participantElement: HTMLElement) {
                const mainTile = participantElement.closest('[data-participant-id]') as HTMLElement;
                if (mainTile) {
                    const userExampleNameElement = mainTile.querySelector('span.notranslate');
                    if (userExampleNameElement && userExampleNameElement.textContent && userExampleNameElement.textContent.trim()) {
                        const nameText = userExampleNameElement.textContent.trim();
                        if (nameText.length > 1 && nameText.length < 50 && /^[\p{L}\s.'-]+$/u.test(nameText)) {
                            const forbiddenSubstrings = ["more_vert", "mic_off", "mic", "videocam", "videocam_off", "present_to_all", "devices", "speaker", "speakers", "microphone"];
                            if (!forbiddenSubstrings.some(sub => nameText.toLowerCase().includes(sub.toLowerCase()))) {
                                return nameText;
                            }
                        }
                    }
                    const googleTsNameSelectors = [
                        '[data-self-name]', '.zWGUib', '.cS7aqe.N2K3jd', '.XWGOtd', '[data-tooltip*="name"]'
                    ];
                    for (const selector of googleTsNameSelectors) {
                        const nameElement = mainTile.querySelector(selector) as HTMLElement;
                        if (nameElement) {
                            let nameText = (nameElement as HTMLElement).textContent || 
                                          (nameElement as HTMLElement).innerText || 
                                          nameElement.getAttribute('data-self-name') || 
                                          nameElement.getAttribute('data-tooltip');
                            if (nameText && nameText.trim()) {
                                if (selector.includes('data-tooltip') && nameText.includes("Tooltip for ")) {
                                    nameText = nameText.replace("Tooltip for ", "").trim();
                                }
                                if (nameText && nameText.trim()) {
                                    const forbiddenSubstrings = ["more_vert", "mic_off", "mic", "videocam", "videocam_off", "present_to_all", "devices", "speaker", "speakers", "microphone"];
                                    if (!forbiddenSubstrings.some(sub => nameText!.toLowerCase().includes(sub.toLowerCase()))) {
                                        const trimmedName = nameText!.split('\n').pop()?.trim();
                                        return trimmedName || 'Unknown (Filtered)';
                                    }
                                }
                            }
                        }
                    }
                }
                for (const selector of nameSelectors) {
                    const nameElement = participantElement.querySelector(selector) as HTMLElement;
                    if (nameElement) {
                        let nameText = (nameElement as HTMLElement).textContent || 
                                      (nameElement as HTMLElement).innerText || 
                                      nameElement.getAttribute('data-self-name');
                        if (nameText && nameText.trim()) {
                            // ADDED: Apply forbidden substrings and trimming logic here too
                            const forbiddenSubstrings = ["more_vert", "mic_off", "mic", "videocam", "videocam_off", "present_to_all", "devices", "speaker", "speakers", "microphone"];
                            if (!forbiddenSubstrings.some(sub => nameText!.toLowerCase().includes(sub.toLowerCase()))) {
                                const trimmedName = nameText!.split('\n').pop()?.trim();
                                if (trimmedName && trimmedName.length > 1 && trimmedName.length < 50 && /^[\p{L}\s.'-]+$/u.test(trimmedName)) { // Added basic length and char validation
                                   return trimmedName;
                                }
                            }
                            // If it was forbidden or failed validation, it won't return, allowing loop to continue or fallback.
                        }
                    }
                }
                if (participantElement.textContent && participantElement.textContent.includes("You") && participantElement.textContent.length < 20) {
                    return "You";
                }
                const idToDisplay = mainTile ? getParticipantId(mainTile) : getParticipantId(participantElement);
                return `Participant (${idToDisplay})`;
            }

            function sendSpeakerEvent(eventType: string, participantElement: HTMLElement) {
                const eventAbsoluteTimeMs = Date.now();
                let relativeTimestampMs: number | null = null;

                if (sessionAudioStartTimeMs === null) {
                    (window as any).logBot(`[RelativeTime] SKIPPING speaker event: ${eventType} for ${getParticipantName(participantElement)}. sessionAudioStartTimeMs not yet set. UID: ${currentSessionUid}`);
                    return; // Do not send if audio hasn't started for this session
                }

                relativeTimestampMs = eventAbsoluteTimeMs - sessionAudioStartTimeMs;

                const participantId = getParticipantId(participantElement);
                const participantName = getParticipantName(participantElement);

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
                            meeting_url: meetingUrl
                        }
                    };

                    try {
                        socket.send(JSON.stringify(speakerEventMessage));
                        (window as any).logBot(`[RelativeTime] Speaker event sent: ${eventType} for ${participantName} (${participantId}). RelativeTs: ${relativeTimestampMs}ms. UID: ${currentSessionUid}. (AbsoluteEventMs: ${eventAbsoluteTimeMs}, SessionT0Ms: ${sessionAudioStartTimeMs})`);
                    } catch (error: any) {
                        (window as any).logBot(`Error sending speaker event: ${error.message}`);
                    }
                } else {
                    (window as any).logBot(`WebSocket not ready, speaker event queued: ${eventType} for ${participantName}`);
                }
            }

            function logSpeakerEvent(participantElement: HTMLElement, mutatedClassList: DOMTokenList) {
                const participantId = getParticipantId(participantElement);
                const participantName = getParticipantName(participantElement);
                const previousLogicalState = speakingStates.get(participantId) || "silent";

                const isNowVisiblySpeaking = speakingClasses.some(cls => mutatedClassList.contains(cls));
                const isNowVisiblySilent = mutatedClassList.contains(silenceClass);

                if (isNowVisiblySpeaking) {
                    if (previousLogicalState !== "speaking") {
                        (window as any).logBot(`🎤 SPEAKER_START: ${participantName} (ID: ${participantId})`);
                        sendSpeakerEvent("SPEAKER_START", participantElement);
                    }
                    speakingStates.set(participantId, "speaking");
                } else if (isNowVisiblySilent) {
                    if (previousLogicalState === "speaking") {
                        (window as any).logBot(`🔇 SPEAKER_END: ${participantName} (ID: ${participantId})`);
                        sendSpeakerEvent("SPEAKER_END", participantElement);
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
                    const descendantElement = participantElement.querySelector('.' + cls); // Corrected selector
                    if (descendantElement) {
                        classListForInitialScan = descendantElement.classList;
                        break;
                    }
                }
                // If no speaking descendant was found, classListForInitialScan remains participantElement.classList.
                // This is correct for checking if participantElement itself has a speaking or silence class.

                (window as any).logBot(`👁️ Observing: ${getParticipantName(participantElement)} (ID: ${participantId}). Performing initial participant state analysis.`);
                // Call logSpeakerEvent with the determined classList.
                // It will compare against the "silent" state and emit SPEAKER_START if currently speaking,
                // or do nothing if currently silent (matching the initialized state).
                logSpeakerEvent(participantElement, classListForInitialScan);
                
                // NEW: Add participant to our central map
                activeParticipants.set(participantId, { name: getParticipantName(participantElement), element: participantElement });

                const callback = function(mutationsList: MutationRecord[], observer: MutationObserver) {
                    for (const mutation of mutationsList) {
                        if (mutation.type === 'attributes' && mutation.attributeName === 'class') {
                            const targetElement = mutation.target as HTMLElement;
                            if (targetElement.matches(participantSelector) || participantElement.contains(targetElement)) {
                                const finalTarget = targetElement.matches(participantSelector) ? targetElement : participantElement;
                                // logSpeakerEvent(finalTarget, finalTarget.classList); // Old line
                                logSpeakerEvent(finalTarget, targetElement.classList); // Corrected line
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

            function scanForAllParticipants() {
                const participantElements = document.querySelectorAll(participantSelector);
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
                    if (mutation.type === 'childList') {
                        mutation.addedNodes.forEach(node => {
                            if (node.nodeType === Node.ELEMENT_NODE) {
                                const elementNode = node as HTMLElement;
                                if (elementNode.matches(participantSelector) && !(elementNode as any).dataset.vexaObserverAttached) {
                                    observeParticipant(elementNode);
                                }
                                const childElements = elementNode.querySelectorAll(participantSelector);
                                for (let i = 0; i < childElements.length; i++) {
                                    const childEl = childElements[i] as HTMLElement;
                                    if (!(childEl as any).dataset.vexaObserverAttached) {
                                        observeParticipant(childEl);
                                    }
                                }
                            }
                        });
                        mutation.removedNodes.forEach(node => {
                             if (node.nodeType === Node.ELEMENT_NODE) {
                                const elementNode = node as HTMLElement;
                                if (elementNode.matches(participantSelector)) {
                                   const participantId = getParticipantId(elementNode);
                                   const participantName = getParticipantName(elementNode);
                                   if (speakingStates.get(participantId) === 'speaking') {
                                        // Send synthetic SPEAKER_END if they were speaking when removed
                                        (window as any).logBot(`🔇 SPEAKER_END (Participant removed while speaking): ${participantName} (ID: ${participantId})`);
                                        sendSpeakerEvent("SPEAKER_END", elementNode);
                                   }
                                   speakingStates.delete(participantId);
                                   delete (elementNode as any).dataset.vexaObserverAttached;
                                   delete (elementNode as any).dataset.vexaGeneratedId;
                                   (window as any).logBot(`🗑️ Removed observer for: ${participantName} (ID: ${participantId})`);
                                   
                                   // NEW: Remove participant from our central map
                                   activeParticipants.delete(participantId);
                                }
                             }
                        });
                    }
                }
            });

            bodyObserver.observe(document.body, {
                childList: true,
                subtree: true
            });

            // --- ADD: Enhanced Leave Function with Session End Signal ---
            (window as any).performLeaveAction = async () => {
                (window as any).logBot("Attempting to leave the meeting from browser context...");
                
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
                                meeting_id: nativeMeetingId
                            }
                        };
                        
                        socket.send(JSON.stringify(sessionControlMessage));
                        (window as any).logBot("LEAVING_MEETING signal sent to WhisperLive");
                        
                        // Wait a brief moment for the message to be sent
                        await new Promise(resolve => setTimeout(resolve, 500));
                    } catch (error: any) {
                        (window as any).logBot(`Error sending LEAVING_MEETING signal: ${error.message}`);
                    }
                }

                try {
                    // *** FIXED: Use document.evaluate for XPath ***
                    const primaryLeaveButtonXpath = `//button[@aria-label="Leave call"]`;
                    const secondaryLeaveButtonXpath = `//button[.//span[text()='Leave meeting']] | //button[.//span[text()='Just leave the meeting']]`;

                    const getElementByXpath = (path: string): HTMLElement | null => {
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
                        (window as any).logBot("Secondary leave button not found.");
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
                  (window as any).logBot(`[RelativeTime] sessionAudioStartTimeMs set for UID ${currentSessionUid}: ${sessionAudioStartTimeMs} (at first audio data process)`);
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
                  (window as any).logBot(`[RelativeTime] CRITICAL WARNING: sessionAudioStartTimeMs is STILL NULL before sending audio data for UID ${currentSessionUid}. This should not happen.`);
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
              'button[data-mdc-dialog-action]',
              'button[data-tooltip*="people"]',
              'button[data-tooltip*="People"]',
              'button[data-tooltip*="participants"]',
              'button[data-tooltip*="Participants"]'
            ];

            let peopleButton: HTMLElement | null = null;
            let usedSelector = '';

            // Try each selector until we find the button
            for (const selector of peopleButtonSelectors) {
              try {
                const button = document.querySelector(selector);
                if (button) {
                  peopleButton = button as HTMLElement;
                  usedSelector = selector;
                  (window as any).logBot(`Found People button using selector: ${selector}`);
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
              (window as any).logBot(`People button not found, but continuing with fallback participant monitoring via MutationObserver`);
              (window as any).peopleButtonClicked = false;
            } else {
              // Log which selector worked
              (window as any).logBot(`Successfully found People button using selector: ${usedSelector}`);
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
              (window as any).logBot(`Participant check: Found ${count} unique participants from central list. IDs: ${JSON.stringify(participantIds)}`);

              // If count is 0, it could mean everyone left, OR the participant list area itself is gone.
              if (count === 0) {
                  const peopleListContainer = document.querySelector('[role="list"]'); // Check the original list container
                  if (!peopleListContainer || !document.body.contains(peopleListContainer)) {
                       (window as any).logBot(
                          "Participant list container not found (and participant count is 0); assuming meeting ended."
                       );
                       clearInterval(checkInterval);
                       recorder.disconnect();
                       (window as any).triggerNodeGracefulLeave();
                       resolve(); // Resolve the main promise from page.evaluate
                       return;   // Exit setInterval callback
                  }
              }

              // FIXED: Correct logic for tracking alone time
              if (count <= 1) { // Bot is 1, so count <= 1 means bot is alone
                aloneTime += 5; // It's a 5-second interval
              } else {
                // Someone else is here, so reset the timer.
                if (aloneTime > 0) {
                    (window as any).logBot('Another participant joined. Resetting alone timer.');
                }
                aloneTime = 0;
              }

              if (aloneTime >= 10) { // If bot has been alone for 10 seconds...
                (window as any).logBot(
                  "Meeting ended or bot has been alone for 10 seconds. Stopping recorder..."
                );
                clearInterval(checkInterval);
                recorder.disconnect();
                (window as any).triggerNodeGracefulLeave();
                resolve();
              } else if (aloneTime > 0) { // Log countdown if timer has started
                 (window as any).logBot(
                  `Bot has been alone for ${aloneTime} seconds. Will leave in ${10 - aloneTime} more seconds.`
                );
              }
            }, 5000);

            // Listen for unload and visibility changes
            window.addEventListener("beforeunload", () => {
              (window as any).logBot("Page is unloading. Stopping recorder...");
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
          }).catch(err => {
              reject(err);
          });
        } catch (error: any) {
          return reject(new Error("[BOT Error] " + error.message));
        }
      });
    },
    { botConfigData: botConfig, whisperUrlForBrowser: whisperLiveUrlResolved }
  );
  
  // After page.evaluate finishes (e.g., on graceful leave), close the redis client
  await redisClient.quit();
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
