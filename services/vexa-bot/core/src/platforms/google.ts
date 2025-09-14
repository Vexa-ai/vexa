import { Page } from "playwright";
import { log, randomDelay, callStartupCallback } from "../utils";
import { BrowserAudioService, BrowserWhisperLiveService, generateBrowserUUID } from "../utils/browser";
import { BotConfig } from "../types";
import { generateUUID, createSessionControlMessage, createSpeakerActivityMessage } from "../index";
import { WhisperLiveService } from "../services/whisperlive";
import { AudioService } from "../services/audio";
import { WebSocketManager } from "../utils/websocket";


export async function handleGoogleMeet(
  botConfig: BotConfig,
  page: Page,
  gracefulLeaveFunction: (page: Page | null, exitCode: number, reason: string, errorDetails?: any) => Promise<void>
): Promise<void> {
  const leaveButton = `//button[@aria-label="Leave call"]`;

  if (!botConfig.meetingUrl) {
    log("Error: Meeting URL is required for Google Meet but is null.");
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

// --- Google Meet-Specific Functions ---

// New function to wait for meeting admission
const waitForMeetingAdmission = async (
  page: Page,
  leaveButton: string,
  timeout: number
): Promise<boolean> => {
  try {
    log("Waiting for meeting admission...");
    
    // Take screenshot at start of admission check
    await page.screenshot({ path: '/app/screenshots/bot-checkpoint-1-admission-start.png', fullPage: true });
    log("📸 Screenshot taken: Start of admission check");
    
    // FIRST: Check if bot is already admitted (no waiting room needed)
    log("Checking if bot is already admitted to the meeting...");
    const initialAdmissionIndicators = [
      'button[aria-label*="People"]',
      'button[aria-label*="people"]',
      'button[aria-label*="Chat"]',
      'button[aria-label*="chat"]',
      'button[aria-label*="Leave call"]',
      'button[aria-label*="Leave meeting"]',
      '[role="toolbar"]',
      '[data-participant-id]',
      'button[aria-label*="Turn off microphone"]',
      'button[aria-label*="Turn on microphone"]'
    ];
    
    for (const indicator of initialAdmissionIndicators) {
      try {
        await page.waitForSelector(indicator, { timeout: 2000 });
        log(`Found admission indicator: ${indicator} - Bot is already admitted to the meeting!`);
        
        // Take screenshot when already admitted
        await page.screenshot({ path: '/app/screenshots/bot-checkpoint-2-admitted.png', fullPage: true });
        log("📸 Screenshot taken: Bot confirmed already admitted to meeting");
        
        log("Successfully admitted to the meeting - no waiting room required");
        return true;
      } catch {
        continue;
      }
    }
    
    log("Bot not yet admitted - checking for waiting room indicators...");
    
    // Second, check if we're still in waiting room
    const waitingRoomIndicators = [
      'text="Please wait until a meeting host brings you into the call"',
      'text="Waiting for the host to let you in"',
      'text="You\'re in the waiting room"',
      'text="Asking to be let in"',
      '[aria-label*="waiting room"]',
      '[aria-label*="Asking to be let in"]',
      'text="Ask to join"',
      'text="Join now"',
      'text="Can\'t join the meeting"',
      'text="Meeting not found"'
    ];
    
    // Check for waiting room indicators first, but don't exit immediately
    let stillInWaitingRoom = false;
    for (const waitingIndicator of waitingRoomIndicators) {
      try {
        await page.waitForSelector(waitingIndicator, { timeout: 2000 });
        log(`Found waiting room indicator: ${waitingIndicator} - Bot is still in waiting room`);
        
        // Take screenshot when waiting room indicator found
        await page.screenshot({ path: '/app/screenshots/bot-checkpoint-4-waiting-room.png', fullPage: true });
        log("📸 Screenshot taken: Bot confirmed in waiting room");
        
        stillInWaitingRoom = true;
        break;
      } catch {
        // Continue to next indicator if this one wasn't found
        continue;
      }
    }
    
    // If we're in waiting room, wait for the full timeout period for admission
    if (stillInWaitingRoom) {
      log(`Bot is in waiting room. Waiting for ${timeout}ms for admission...`);
      
      // Wait for the full timeout period, checking periodically for admission
      const checkInterval = 5000; // Check every 5 seconds
      const startTime = Date.now();
      let screenshotCounter = 0;
      
      while (Date.now() - startTime < timeout) {
        // Take periodic screenshot for debugging
        screenshotCounter++;
        await page.screenshot({ path: `/app/screenshots/bot-waiting-periodic-${screenshotCounter}.png`, fullPage: true });
        log(`📸 Periodic screenshot ${screenshotCounter} taken during waiting period`);
        
        // Check if we're still in waiting room
        let stillWaiting = false;
        for (const waitingIndicator of waitingRoomIndicators) {
          try {
            await page.waitForSelector(waitingIndicator, { timeout: 1000 });
            stillWaiting = true;
            break;
          } catch {
            continue;
          }
        }
        
        if (!stillWaiting) {
          log("Waiting room indicator disappeared - bot is likely admitted, checking for admission indicators...");
          
          // Immediately check for admission indicators since waiting room disappeared
          let quickAdmissionCheck = false;
          const quickAdmissionIndicators = [
            'button[aria-label*="People"]',
            'button[aria-label*="people"]',
            'button[aria-label*="Chat"]',
            'button[aria-label*="chat"]',
            'button[aria-label*="Leave call"]',
            'button[aria-label*="Leave meeting"]',
            '[role="toolbar"]',
            '[data-participant-id]'
          ];
          
          for (const indicator of quickAdmissionIndicators) {
            try {
              await page.waitForSelector(indicator, { timeout: 1000 });
              log(`Found admission indicator: ${indicator} - Bot is confirmed admitted!`);
              quickAdmissionCheck = true;
              break;
            } catch {
              continue;
            }
          }
          
          if (quickAdmissionCheck) {
            log("Successfully admitted to the meeting - waiting room disappeared and admission indicators found");
            return true;
          } else {
            log("Waiting room disappeared but no admission indicators found yet - continuing to wait...");
            // Continue waiting for admission indicators to appear
          }
        }
        
        // Wait before next check
        await page.waitForTimeout(checkInterval);
        log(`Still in waiting room... ${Math.round((Date.now() - startTime) / 1000)}s elapsed`);
      }
      
      // After waiting, check if we're still in waiting room
      let finalWaitingCheck = false;
      for (const waitingIndicator of waitingRoomIndicators) {
        try {
          await page.waitForSelector(waitingIndicator, { timeout: 2000 });
          finalWaitingCheck = true;
          break;
        } catch {
          continue;
        }
      }
      
      if (finalWaitingCheck) {
        throw new Error("Bot is still in the waiting room after timeout - not admitted to the meeting");
      }
    }
    
    // PRIORITY: Check for audio/transcription activity first (most reliable indicator)
    log("Checking for audio/transcription activity as primary admission indicator...");
    
    // Take initial screenshot before checking for admission indicators
    await page.screenshot({ path: '/app/screenshots/bot-checking-admission-start.png', fullPage: true });
    log("📸 Screenshot taken: Starting admission indicator check");
    
    // Wait for audio activity indicators - these are the most reliable signs of admission
    // ORDERED BY LIKELIHOOD: Most common indicators first for faster detection
    const audioIndicators = [
      // Most common meeting indicators (check these first!)
      'button[aria-label*="Chat"]',
      'button[aria-label*="chat"]',
      'button[aria-label*="People"]',
      'button[aria-label*="people"]',
      'button[aria-label*="Participants"]',
      'button[aria-label*="Leave call"]',
      'button[aria-label*="Leave meeting"]',
      // Audio/video controls that appear when in meeting
      'button[aria-label*="Turn off microphone"]',
      'button[aria-label*="Turn on microphone"]',
      'button[aria-label*="Turn off camera"]',
      'button[aria-label*="Turn on camera"]',
      // Share and present buttons
      'button[aria-label*="Share screen"]',
      'button[aria-label*="Present now"]',
      // Meeting toolbar and controls
      '[role="toolbar"]',
      '[data-participant-id]',
      '[data-self-name]',
      // Audio level indicators
      '[data-audio-level]',
      '[aria-label*="microphone"]',
      '[aria-label*="camera"]',
      // Meeting controls toolbar
      '[data-tooltip*="microphone"]',
      '[data-tooltip*="camera"]',
      // Video tiles and meeting UI
      '[aria-label*="meeting"]',
      'div[data-meeting-id]'
    ];
    
    let admitted = false;
    let audioCheckCounter = 0;
    
    // Use much faster timeout for each check (500ms instead of timeout/indicators.length)
    const fastTimeout = 500;
    
    for (const indicator of audioIndicators) {
      try {
        audioCheckCounter++;
        log(`Checking audio indicator ${audioCheckCounter}/${audioIndicators.length}: ${indicator}`);
        
        // Take screenshot before checking each indicator
        await page.screenshot({ path: `/app/screenshots/bot-audio-check-${audioCheckCounter}.png`, fullPage: true });
        log(`📸 Screenshot taken: Checking audio indicator ${audioCheckCounter}`);
        
        await page.waitForSelector(indicator, { timeout: fastTimeout });
        log(`Found audio indicator: ${indicator} - Bot is admitted to the meeting`);
        
        // Take screenshot when audio indicator is found
        await page.screenshot({ path: '/app/screenshots/bot-checkpoint-2-admitted.png', fullPage: true });
        log("📸 Screenshot taken: Bot confirmed admitted to meeting via audio indicators");
        
        admitted = true;
        break;
      } catch {
        // Continue to next indicator
        continue;
      }
    }
    
    // FALLBACK: If no audio indicators found, check for other meeting UI elements
    if (!admitted) {
      log("No audio indicators found, checking for other meeting UI elements...");
      
      const meetingIndicators = [
        // People button indicates meeting UI is fully loaded
        'button[aria-label*="People"]',
        'button[aria-label*="people"]',
        'button[aria-label*="Participants"]',
        // Chat button indicates meeting is active
        'button[aria-label*="Chat"]',
        'button[aria-label*="chat"]',
        // Participant list or meeting tiles
        '[data-participant-id]',
        // Meeting toolbar
        '[role="toolbar"]',
        // Video tiles
        '[data-self-name]',
        // Meeting info
        '[aria-label*="meeting"]'
      ];
      
      for (const indicator of meetingIndicators) {
        try {
          await page.waitForSelector(indicator, { timeout: timeout / meetingIndicators.length });
          log(`Found meeting indicator: ${indicator} - Bot is admitted to the meeting`);
          
          // Take screenshot when meeting indicator is found
          await page.screenshot({ path: '/app/screenshots/bot-checkpoint-2-admitted.png', fullPage: true });
          log("📸 Screenshot taken: Bot confirmed admitted to meeting via UI indicators");
          
          admitted = true;
          break;
        } catch {
          // Continue to next indicator
          continue;
        }
      }
    }
    
    if (!admitted) {
      // Take screenshot when no meeting indicators found
      await page.screenshot({ path: '/app/screenshots/bot-checkpoint-3-no-indicators.png', fullPage: true });
      log("📸 Screenshot taken: No meeting indicators found");
      
      // If we can't find any meeting indicators, the bot likely failed to join
      log("No meeting indicators found - bot likely failed to join or is in unknown state");
      throw new Error("Bot failed to join the meeting - no meeting indicators found");
    }
    
    if (admitted) {
      log("Successfully admitted to the meeting");
      return true;
    } else {
      throw new Error("Could not determine admission status");
    }
    
  } catch (error: any) {
    throw new Error(
      `Bot was not admitted into the meeting within the timeout period: ${error.message}`
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

  // Take screenshot after navigation
  await page.screenshot({ path: '/app/screenshots/bot-checkpoint-0-after-navigation.png', fullPage: true });
  log("📸 Screenshot taken: After navigation to meeting URL");

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
  
  // Take screenshot after finding name field
  await page.screenshot({ path: '/app/screenshots/bot-checkpoint-0-name-field-found.png', fullPage: true });
  log("📸 Screenshot taken: Name input field found");

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
  
  // Take screenshot after clicking "Ask to join"
  await page.screenshot({ path: '/app/screenshots/bot-checkpoint-0-after-ask-to-join.png', fullPage: true });
  log("📸 Screenshot taken: After clicking 'Ask to join'");
};

// Modified to use new services - only the actual recording functionality
const startRecording = async (page: Page, botConfig: BotConfig) => {
  // Initialize WhisperLive service on Node.js side
  const whisperLiveService = new WhisperLiveService({
    redisUrl: botConfig.redisUrl,
    maxClients: parseInt(process.env.WL_MAX_CLIENTS || '10', 10),
    whisperLiveUrl: process.env.WHISPER_LIVE_URL
  });

  // Initialize WhisperLive connection
  const whisperLiveUrl = await whisperLiveService.initialize();
  if (!whisperLiveUrl) {
    log("ERROR: Could not initialize WhisperLive service. Aborting recording.");
    return;
  }

  log(`[Node.js] Using WhisperLive URL: ${whisperLiveUrl}`);
  log("Starting actual recording with WebSocket connection");

  // Pass the necessary config fields and the resolved URL into the page context
  await page.evaluate(
    async (pageArgs: {
      botConfigData: BotConfig;
      whisperUrlForBrowser: string;
    }) => {
      const { botConfigData, whisperUrlForBrowser } = pageArgs;

      // Use shared BrowserAudioService
      const audioService = new BrowserAudioService({
        targetSampleRate: 16000,
        bufferSize: 4096,
        inputChannels: 1,
        outputChannels: 1
      });

      // Use shared BrowserWhisperLiveService with simple mode for Google Meet
      const whisperLiveService = new BrowserWhisperLiveService({
        whisperLiveUrl: whisperUrlForBrowser
      }, false); // Use simple mode for Google Meet


      await new Promise<void>((resolve, reject) => {
        try {
          (window as any).logBot("Starting recording process with new services.");
          
          // Find and create combined audio stream
          audioService.findMediaElements().then(async (mediaElements: HTMLMediaElement[]) => {
            if (mediaElements.length === 0) {
              reject(
                new Error(
                  "[BOT Error] No active media elements found after multiple retries. Ensure the meeting media is playing."
                )
              );
              return;
            }

            // Create combined audio stream
            return await audioService.createCombinedAudioStream(mediaElements);
          }).then(async (combinedStream: MediaStream | undefined) => {
            if (!combinedStream) {
              reject(new Error("[BOT Error] Failed to create combined audio stream"));
              return;
            }
            // Initialize audio processor
            return await audioService.initializeAudioProcessor(combinedStream);
          }).then(async (processor: any) => {
            // Setup audio data processing
            audioService.setupAudioDataProcessor(async (audioData: Float32Array, sessionStartTime: number | null) => {
              // Send audio data to WhisperLive
              const success = whisperLiveService.sendAudioData(audioData);
              if (!success) {
                (window as any).logBot("Failed to send audio data to WhisperLive");
              }
            });

            // Initialize WhisperLive WebSocket connection
            return await whisperLiveService.connectToWhisperLive(
              botConfigData,
              (data: any) => {
                (window as any).logBot("Received message: " + JSON.stringify(data));
                if (data["status"] === "ERROR") {
                  (window as any).logBot(`WebSocket Server Error: ${data["message"]}`);
                } else if (data["status"] === "WAIT") {
                  (window as any).logBot(`Server busy: ${data["message"]}`);
                } else if (!whisperLiveService.isReady() && data["status"] === "SERVER_READY") {
                  whisperLiveService.setServerReady(true);
                  (window as any).logBot("Server is ready.");
                } else if (data["language"]) {
                  (window as any).logBot(`Language detected: ${data["language"]}`);
                } else if (data["message"] === "DISCONNECT") {
                  (window as any).logBot("Server requested disconnect.");
                  whisperLiveService.close();
                } else {
                  (window as any).logBot(`Transcription: ${JSON.stringify(data)}`);
                }
              },
              (event: Event) => {
                (window as any).logBot(`[Failover] WebSocket error. This will trigger retry logic.`);
              },
              async (event: CloseEvent) => {
                (window as any).logBot(`[Failover] WebSocket connection closed. Code: ${event.code}, Reason: ${event.reason}.`);
                // Retry logic would be handled by WebSocketManager
              }
            );
          }).then(() => {
            // Initialize Google Meet-specific speaker detection (browser context)
            (window as any).logBot("Initializing Google Meet speaker detection...");
            
            // Google Meet-specific speaker detection logic
            const initializeGoogleMeetSpeakerDetection = (whisperLiveService: any, audioService: any, botConfigData: any) => {
              (window as any).logBot("Setting up Google Meet speaker detection...");
              
              // Monitor for participant changes
              const participantObserver = new MutationObserver((mutations) => {
                mutations.forEach((mutation) => {
                  if (mutation.type === 'childList') {
                    // Check for new participants or speaker changes
                    const speakerElements = document.querySelectorAll('[data-participant-id]');
                    speakerElements.forEach((element: any) => {
                      const participantId = element.getAttribute('data-participant-id');
                      const participantName = element.textContent || 'Unknown';
                      
                      if (participantId && participantId !== botConfigData.nativeMeetingId) {
                        // Send speaker activity event
                        const sessionStartTime = audioService.getSessionAudioStartTime();
                        const relativeTimestamp = sessionStartTime ? Date.now() - sessionStartTime : 0;
                        
                        whisperLiveService.sendSpeakerEvent(
                          'speaker_active',
                          participantName,
                          participantId,
                          relativeTimestamp,
                          botConfigData
                        );
                      }
                    });
                  }
                });
              });

              // Start observing the meeting container
              const meetingContainer = document.querySelector('[jsname="BOHaEe"]') || document.body;
              participantObserver.observe(meetingContainer, {
                childList: true,
                subtree: true
              });

              (window as any).logBot("Google Meet speaker detection initialized");
            };

            // Setup meeting monitoring (browser context)
            const setupMeetingMonitoring = (botConfigData: any, audioService: any, whisperLiveService: any, resolve: any) => {
              (window as any).logBot("Setting up meeting monitoring...");
              
              const startupAloneTimeoutSeconds = 20 * 60; // 20 minutes during startup
              const everyoneLeftTimeoutSeconds = 10; // 10 seconds after speakers identified
              
              let aloneTime = 0;
              let lastParticipantCount = 0;
              let speakersIdentified = false;
              let hasEverHadMultipleParticipants = false;

              const checkInterval = setInterval(() => {
                // Check participant count
                const participantElements = document.querySelectorAll('[data-participant-id]');
                const currentParticipantCount = participantElements.length;
                
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
                      (window as any).logBot(`Meeting ended or bot has been alone for ${everyoneLeftTimeoutSeconds} seconds after speakers were identified. Stopping recorder...`);
                    } else {
                      (window as any).logBot(`Bot has been alone for ${startupAloneTimeoutSeconds/60} minutes during startup with no other participants. Stopping recorder...`);
                    }
                    clearInterval(checkInterval);
                    audioService.disconnect();
                    whisperLiveService.close();
                    resolve();
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

            // Initialize Google Meet-specific speaker detection
            initializeGoogleMeetSpeakerDetection(whisperLiveService, audioService, botConfigData);
            
            // Setup meeting monitoring
            setupMeetingMonitoring(botConfigData, audioService, whisperLiveService, resolve);
          }).catch((err: any) => {
            reject(err);
          });

        } catch (error: any) {
          return reject(new Error("[BOT Error] " + error.message));
        }
      });
    },
    { botConfigData: botConfig, whisperUrlForBrowser: whisperLiveUrl }
  );
  
  // After page.evaluate finishes, cleanup services
  await whisperLiveService.cleanup();
};

// --- Google Meet-Specific Speaker Detection ---
function initializeGoogleMeetSpeakerDetection(
  whisperLiveService: any,
  audioService: any,
  botConfigData: any
) {
  // Configuration for Google Meet speaker detection
  const participantSelector = 'div[data-participant-id]';
  const speakingClasses = ['Oaajhc', 'HX2H7', 'wEsLMd', 'OgVli'];
  const silenceClass = 'gjg47c';

  // State for tracking speaking status
  const speakingStates = new Map();
  const activeParticipants = new Map();

  // Helper functions for Google Meet speaker detection
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
    
    // Fallback name extraction logic
    for (const selector of ['[data-participant-id]']) {
      const nameElement = participantElement.querySelector(selector) as HTMLElement;
      if (nameElement) {
        let nameText = (nameElement as HTMLElement).textContent || 
                      (nameElement as HTMLElement).innerText || 
                      nameElement.getAttribute('data-self-name');
        if (nameText && nameText.trim()) {
          const forbiddenSubstrings = ["more_vert", "mic_off", "mic", "videocam", "videocam_off", "present_to_all", "devices", "speaker", "speakers", "microphone"];
          if (!forbiddenSubstrings.some(sub => nameText!.toLowerCase().includes(sub.toLowerCase()))) {
            const trimmedName = nameText!.split('\n').pop()?.trim();
            if (trimmedName && trimmedName.length > 1 && trimmedName.length < 50 && /^[\p{L}\s.'-]+$/u.test(trimmedName)) {
               return trimmedName;
            }
          }
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
    const sessionStartTime = audioService.getSessionAudioStartTime();
    if (sessionStartTime === null) {
      (window as any).logBot(`[RelativeTime] SKIPPING speaker event: ${eventType} for ${getParticipantName(participantElement)}. sessionAudioStartTimeMs not yet set.`);
      return;
    }

    const relativeTimestampMs = Date.now() - sessionStartTime;
    const participantId = getParticipantId(participantElement);
    const participantName = getParticipantName(participantElement);

    // Send speaker event via WhisperLive service
    const success = whisperLiveService.sendSpeakerEvent(
      eventType,
      participantName,
      participantId,
      relativeTimestampMs,
      botConfigData
    );

    if (success) {
      (window as any).logBot(`[RelativeTime] Speaker event sent: ${eventType} for ${participantName} (${participantId}). RelativeTs: ${relativeTimestampMs}ms.`);
    } else {
      (window as any).logBot(`Failed to send speaker event: ${eventType} for ${participantName}`);
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
    
    speakingStates.set(participantId, "silent");

    let classListForInitialScan = participantElement.classList;
    for (const cls of speakingClasses) {
      const descendantElement = participantElement.querySelector('.' + cls);
      if (descendantElement) {
        classListForInitialScan = descendantElement.classList;
        break;
      }
    }

    (window as any).logBot(`👁️ Observing: ${getParticipantName(participantElement)} (ID: ${participantId}).`);
    logSpeakerEvent(participantElement, classListForInitialScan);
    
    activeParticipants.set(participantId, { name: getParticipantName(participantElement), element: participantElement });

    const callback = function(mutationsList: MutationRecord[], observer: MutationObserver) {
      for (const mutation of mutationsList) {
        if (mutation.type === 'attributes' && mutation.attributeName === 'class') {
          const targetElement = mutation.target as HTMLElement;
          if (targetElement.matches(participantSelector) || participantElement.contains(targetElement)) {
            const finalTarget = targetElement.matches(participantSelector) ? targetElement : participantElement;
            logSpeakerEvent(finalTarget, targetElement.classList);
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
                  (window as any).logBot(`🔇 SPEAKER_END (Participant removed while speaking): ${participantName} (ID: ${participantId})`);
                  sendSpeakerEvent("SPEAKER_END", elementNode);
               }
               speakingStates.delete(participantId);
               delete (elementNode as any).dataset.vexaObserverAttached;
               delete (elementNode as any).dataset.vexaGeneratedId;
               (window as any).logBot(`🗑️ Removed observer for: ${participantName} (ID: ${participantId})`);
               
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
}

// --- Meeting Monitoring and Leave Functions ---
function setupMeetingMonitoring(
  botConfigData: any,
  audioService: any,
  whisperLiveService: any,
  resolve: () => void
) {
  // Click the "People" button to open participant list
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
    'button:has(span:contains("People"))',
    'button:has(span:contains("people"))',
    'button:has(span:contains("Participants"))',
    'button:has(span:contains("participants"))',
    'button[data-mdc-dialog-action]',
    'button[data-tooltip*="people"]',
    'button[data-tooltip*="People"]',
    'button[data-tooltip*="participants"]',
    'button[data-tooltip*="Participants"]'
  ];

  let peopleButton: HTMLElement | null = null;
  let usedSelector = '';

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
      continue;
    }
  }

  if (!peopleButton) {
    (window as any).logBot(`People button not found, but continuing with fallback participant monitoring`);
    (window as any).peopleButtonClicked = false;
  } else {
    (window as any).logBot(`Successfully found People button using selector: ${usedSelector}`);
    peopleButton.click();
    (window as any).peopleButtonClicked = true;
  }

  // Monitor participant count every 5 seconds
  let aloneTime = 0;
  const checkInterval = setInterval(() => {
    // Get participant count from activeParticipants map (from speaker detection)
    const participantElements = document.querySelectorAll('div[data-participant-id]');
    const count = participantElements.length;
    const participantIds = Array.from(participantElements).map(el => el.getAttribute('data-participant-id'));
    (window as any).logBot(`Participant check: Found ${count} participants. IDs: ${JSON.stringify(participantIds)}`);

    // If count is 0, it could mean everyone left, OR the participant list area itself is gone.
    if (count === 0) {
      const peopleListContainer = document.querySelector('[role="list"]');
      if (!peopleListContainer || !document.body.contains(peopleListContainer)) {
        (window as any).logBot("Participant list container not found (and participant count is 0); assuming meeting ended.");
        clearInterval(checkInterval);
        audioService.disconnect();
        (window as any).triggerNodeGracefulLeave();
        resolve();
        return;
      }
    }

    // Track alone time
    if (count === 1) { // Bot is the only participant (count = 1)
      aloneTime += 5; // It's a 5-second interval
    } else if (count === 0) {
      // No participants at all - meeting might have ended
      aloneTime += 5;
    } else {
      // Multiple participants (count > 1) - someone else is here, reset the timer
      if (aloneTime > 0) {
        (window as any).logBot('Another participant joined. Resetting alone timer.');
      }
      aloneTime = 0;
    }

    const everyoneLeftTimeoutSeconds = botConfigData.automaticLeave.everyoneLeftTimeout / 1000;
    if (aloneTime >= everyoneLeftTimeoutSeconds) {
      (window as any).logBot(`Meeting ended or bot has been alone for ${everyoneLeftTimeoutSeconds} seconds. Stopping recorder...`);
      clearInterval(checkInterval);
      audioService.disconnect();
      (window as any).triggerNodeGracefulLeave();
      resolve();
    } else if (aloneTime > 0) {
      (window as any).logBot(`Bot has been alone for ${aloneTime} seconds. Will leave in ${everyoneLeftTimeoutSeconds - aloneTime} more seconds.`);
    }
  }, 5000);

  // Enhanced Leave Function with Session End Signal
  (window as any).performLeaveAction = async () => {
    (window as any).logBot("Attempting to leave the meeting from browser context...");
    
    // Send LEAVING_MEETING signal before closing WebSocket
    if (whisperLiveService && whisperLiveService.isOpen()) {
      try {
        whisperLiveService.sendSessionControl("LEAVING_MEETING", botConfigData);
        (window as any).logBot("LEAVING_MEETING signal sent to WhisperLive");
        
        // Wait a brief moment for the message to be sent
        await new Promise(resolve => setTimeout(resolve, 500));
      } catch (error: any) {
        (window as any).logBot(`Error sending LEAVING_MEETING signal: ${error.message}`);
      }
    }

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
        (window as any).logBot("Clicking primary leave button...");
        primaryLeaveButton.click();
        await new Promise((resolve) => setTimeout(resolve, 1000));

        const secondaryLeaveButton = getElementByXpath(secondaryLeaveButtonXpath);
        if (secondaryLeaveButton) {
          (window as any).logBot("Clicking secondary/confirmation leave button...");
          secondaryLeaveButton.click();
          await new Promise((resolve) => setTimeout(resolve, 500));
        } else {
          (window as any).logBot("Secondary leave button not found.");
        }
        (window as any).logBot("Leave sequence completed.");
        return true;
      } else {
        (window as any).logBot("Primary leave button not found.");
        return false;
      }
    } catch (err: any) {
      (window as any).logBot(`Error during leave attempt: ${err.message}`);
      return false;
    }
  };

  // Listen for unload and visibility changes
  window.addEventListener("beforeunload", () => {
    (window as any).logBot("Page is unloading. Stopping recorder...");
    clearInterval(checkInterval);
    audioService.disconnect();
    (window as any).triggerNodeGracefulLeave();
    resolve();
  });
  
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      (window as any).logBot("Document is hidden. Stopping recorder...");
      clearInterval(checkInterval);
      audioService.disconnect();
      (window as any).triggerNodeGracefulLeave();
      resolve();
    }
  });
}

// --- ADDED: Exported function to trigger leave from Node.js ---
export async function leaveGoogleMeet(page: Page): Promise<boolean> {
  log("[leaveGoogleMeet] Triggering leave action in browser context...");
  if (!page || page.isClosed()) {
    log("[leaveGoogleMeet] Page is not available or closed.");
    return false;
  }
  try {
    const result = await page.evaluate(async () => {
      if (typeof (window as any).performLeaveAction === "function") {
        return await (window as any).performLeaveAction();
      } else {
        (window as any).logBot?.("[Node Eval Error] performLeaveAction function not found on window.");
        console.error("[Node Eval Error] performLeaveAction function not found on window.");
        return false;
      }
    });
    log(`[leaveGoogleMeet] Browser leave action result: ${result}`);
    return result;
  } catch (error: any) {
    log(`[leaveGoogleMeet] Error calling performLeaveAction in browser: ${error.message}`);
    return false;
  }
}
