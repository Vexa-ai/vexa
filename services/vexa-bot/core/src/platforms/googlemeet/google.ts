import { Page } from "playwright";
import { log, randomDelay, callStartupCallback, callJoiningCallback, callAwaitingAdmissionCallback, callLeaveCallback } from "../../utils";
import { BotConfig } from "../../types";
import { generateUUID, createSessionControlMessage, createSpeakerActivityMessage, hasStopSignalReceived } from "../../index";
import { WhisperLiveService } from "../../services/whisperlive";
import { AudioService } from "../../services/audio";
import { WebSocketManager } from "../../utils/websocket";
import { 
  googleInitialAdmissionIndicators,
  googleWaitingRoomIndicators,
  googleRejectionIndicators,
  googleAdmissionIndicators,
  googleParticipantSelectors,
  googleSpeakingClassNames,
  googleSilenceClassNames,
  googleParticipantContainerSelectors,
  googlePrimaryLeaveButtonSelectors,
  googleSecondaryLeaveButtonSelectors,
  googleNameSelectors,
  googleSpeakingIndicators,
  googleRemovalIndicators,
  googleJoinButtonSelectors,
  googleCameraButtonSelectors,
  googleMicrophoneButtonSelectors,
  googleNameInputSelectors,
  googleMeetingContainerSelectors,
  googleParticipantIdSelectors,
  googleLeaveSelectors,
  googlePeopleButtonSelectors
} from "./selectors";


// --- Google Meet-Specific Functions ---

// Function to check if bot has been rejected from the meeting
const checkForGoogleRejection = async (page: Page): Promise<boolean> => {
  try {
    // Check for rejection indicators
    for (const selector of googleRejectionIndicators) {
      try {
        const element = await page.locator(selector).first();
        if (await element.isVisible()) {
          log(`🚨 Google Meet admission rejection detected: Found rejection indicator "${selector}"`);
          return true;
        }
      } catch (e) {
        // Continue checking other selectors
        continue;
      }
    }
    return false;
  } catch (error: any) {
    log(`Error checking for Google Meet rejection: ${error.message}`);
    return false;
  }
};

// Function to check if bot has been removed from the meeting
const checkForGoogleRemoval = async (page: Page): Promise<boolean> => {
  try {
    // Check for removal indicators
    for (const selector of googleRemovalIndicators) {
      try {
        const element = await page.locator(selector).first();
        if (await element.isVisible()) {
          log(`🚨 Google Meet removal detected: Found removal indicator "${selector}"`);
          return true;
        }
      } catch (e) {
        // Continue checking other selectors
        continue;
      }
    }
    return false;
  } catch (error: any) {
    log(`Error checking for Google Meet removal: ${error.message}`);
    return false;
  }
};

// Helper function to check for any visible and enabled admission indicators
const checkForGoogleAdmissionIndicators = async (page: Page): Promise<boolean> => {
  for (const selector of googleInitialAdmissionIndicators) {
    try {
      const element = page.locator(selector).first();
      const isVisible = await element.isVisible();
      if (isVisible) {
        const isDisabled = await element.getAttribute('aria-disabled');
        if (isDisabled !== 'true') {
          log(`✅ Found Google Meet admission indicator: ${selector}`);
          return true;
        }
      }
    } catch (error) {
      // Continue to next selector if this one fails
      continue;
    }
  }
  return false;
};

export async function handleGoogleMeet(
  botConfig: BotConfig,
  page: Page,
  gracefulLeaveFunction: (page: Page | null, exitCode: number, reason: string, errorDetails?: any) => Promise<void>
): Promise<void> {
  
  if (!botConfig.meetingUrl) {
    log("Error: Meeting URL is required for Google Meet but is null.");
    await gracefulLeaveFunction(page, 1, "missing_meeting_url");
    return;
  }

  log("Joining Google Meet");
  try {
    await joinGoogleMeeting(page, botConfig.meetingUrl, botConfig.botName, botConfig);
  } catch (error: any) {
    console.error("Error during joinGoogleMeeting: " + error.message);
    log("Error during joinGoogleMeeting: " + error.message + ". Triggering graceful leave.");
    
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

  // Add stop signal guard before admission wait (canonical Teams pattern)
  if (hasStopSignalReceived()) {
    log("⛔ Stop signal detected before admission wait. Exiting without joining.");
    await gracefulLeaveFunction(page, 0, "stop_requested_pre_admission");
    return;
  }

  // Setup websocket connection and meeting admission concurrently
  log("Starting WebSocket connection while waiting for Google Meet meeting admission");
  try {
    // Run both processes concurrently
    const [admissionResult] = await Promise.all([
      // Wait for admission to the meeting
      waitForGoogleMeetingAdmission(page, botConfig.automaticLeave.waitingRoomTimeout, botConfig).catch((error) => {
        log("Google Meet meeting admission failed: " + error.message);
        
        // Check if the error indicates rejection by admin
        if (error.message.includes("rejected by meeting admin")) {
          return { admitted: false, rejected: true, reason: "admission_rejected_by_admin" };
        }
        
        return { admitted: false, rejected: false, reason: "admission_timeout" };
      }),

      // Prepare for recording (expose functions, etc.) while waiting for admission
      prepareForRecording(page, botConfig),
    ]);

    // Handle different admission outcomes
    const isAdmitted = admissionResult === true || (typeof admissionResult === 'object' && admissionResult.admitted);
    
    if (!isAdmitted) {
      const rejectionInfo = typeof admissionResult === 'object' ? admissionResult : { reason: "admission_timeout" };
      
      if ('rejected' in rejectionInfo && rejectionInfo.rejected) {
        log("🚨 Bot was rejected from the Google Meet meeting by admin. Exiting gracefully...");
        
        // For rejection, we don't need to attempt leave since we're not in the meeting
        await gracefulLeaveFunction(page, 0, rejectionInfo.reason);
        return;
      } else {
        log("Bot was not admitted into the Google Meet meeting within the timeout period. Attempting graceful leave...");
        
        // Attempt stateless leave before calling gracefulLeaveFunction (for timeout scenarios)
        try {
          const result = await page.evaluate(async () => {
            if (typeof (window as any).performLeaveAction === "function") {
              return await (window as any).performLeaveAction();
            }
            return false;
          });
          
          if (result) {
            log("✅ Successfully performed graceful leave during admission timeout");
          } else {
            log("⚠️ Could not perform graceful leave during admission timeout - continuing with normal exit");
          }
        } catch (leaveError: any) {
          log(`⚠️ Error during graceful leave attempt: ${leaveError.message} - continuing with normal exit`);
        }
        
        await gracefulLeaveFunction(page, 0, rejectionInfo.reason);
        return;
      }
    }

    log("Successfully admitted to the Google Meet meeting, starting recording");
    
    // --- Call startup callback to notify bot-manager that bot is active ---
    try {
      await callStartupCallback(botConfig);
      log("Startup callback sent successfully");
    } catch (callbackError: any) {
      log(`Warning: Failed to send startup callback: ${callbackError.message}. Continuing with recording...`);
    }
    
    // Start recording with Google Meet-specific logic
    await startGoogleRecording(page, botConfig);
    
    // If we reach here, recording finished normally (not due to removal)
    log("Google Meet recording completed normally");
    await gracefulLeaveFunction(page, 0, "normal_completion");
  } catch (error: any) {
    // Handle removal detection specifically (check for the error message with or without page.evaluate prefix)
    if (error.message === "GOOGLE_MEET_BOT_REMOVED_BY_ADMIN" || error.message.includes("GOOGLE_MEET_BOT_REMOVED_BY_ADMIN")) {
      log("🚨 Bot was removed from Google Meet meeting by admin. Exiting gracefully...");
      await gracefulLeaveFunction(page, 0, "removed_by_admin");
      return;
    }
    
    // Handle left alone timeout scenarios
    if (error.message === "GOOGLE_MEET_BOT_LEFT_ALONE_TIMEOUT" || error.message.includes("GOOGLE_MEET_BOT_LEFT_ALONE_TIMEOUT")) {
      log("⏰ Bot was left alone in Google Meet meeting for 10 seconds. Exiting gracefully...");
      await gracefulLeaveFunction(page, 0, "left_alone_timeout");
      return;
    }
    
    if (error.message === "GOOGLE_MEET_BOT_STARTUP_ALONE_TIMEOUT" || error.message.includes("GOOGLE_MEET_BOT_STARTUP_ALONE_TIMEOUT")) {
      log("⏰ Bot was alone during startup for 20 minutes. Exiting gracefully...");
      await gracefulLeaveFunction(page, 0, "startup_alone_timeout");
      return;
    }
    
    console.error("Error after Google Meet join attempt (admission/recording setup): " + error.message);
    log("Error after Google Meet join attempt (admission/recording setup): " + error.message + ". Triggering graceful leave.");
    
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

// New function to wait for Google Meet meeting admission (canonical Teams-style)
const waitForGoogleMeetingAdmission = async (
  page: Page,
  timeout: number,
  botConfig: BotConfig
): Promise<boolean> => {
  try {
    log("Waiting for Google Meet meeting admission...");
    
    // Take screenshot at start of admission check
    await page.screenshot({ path: '/app/storage/screenshots/bot-checkpoint-1-admission-start.png', fullPage: true });
    log("📸 Screenshot taken: Start of admission check");
    
    // FIRST: Check if bot is already admitted (no waiting room needed)
    log("Checking if bot is already admitted to the Google Meet meeting...");
    
    // Check for any visible admission indicator (multiple selectors for robustness)
    const initialAdmissionFound = await checkForGoogleAdmissionIndicators(page);
    
    // Negative check: ensure we're not still in lobby/pre-join
    const initialLobbyStillVisible = await checkForWaitingRoomIndicators(page);
    
    if (initialAdmissionFound && !initialLobbyStillVisible) {
      log(`Found Google Meet admission indicator: visible meeting controls - Bot is already admitted to the meeting!`);
      
      // Take screenshot when already admitted
      await page.screenshot({ path: '/app/storage/screenshots/bot-checkpoint-2-admitted.png', fullPage: true });
      log("📸 Screenshot taken: Bot confirmed already admitted to meeting");
      
      // --- Call awaiting admission callback even for immediate admission ---
      try {
        await callAwaitingAdmissionCallback(botConfig);
        log("Awaiting admission callback sent successfully (immediate admission)");
      } catch (callbackError: any) {
        log(`Warning: Failed to send awaiting admission callback: ${callbackError.message}. Continuing...`);
      }
      
      log("Successfully admitted to the Google Meet meeting - no waiting room required");
      return true;
    }
    
    log("Bot not yet admitted - checking for Google Meet waiting room indicators...");
    
    // Check for waiting room indicators using visibility checks
    let stillInWaitingRoom = false;
    
    const waitingRoomVisible = await checkForWaitingRoomIndicators(page);
    
    if (waitingRoomVisible) {
      log(`Found Google Meet waiting room indicator - Bot is still in waiting room`);
      
      // Take screenshot when waiting room indicator found
      await page.screenshot({ path: '/app/storage/screenshots/bot-checkpoint-4-waiting-room.png', fullPage: true });
      log("📸 Screenshot taken: Bot confirmed in waiting room");
      
      // --- Call awaiting admission callback to notify bot-manager that bot is waiting ---
      try {
        await callAwaitingAdmissionCallback(botConfig);
        log("Awaiting admission callback sent successfully");
      } catch (callbackError: any) {
        log(`Warning: Failed to send awaiting admission callback: ${callbackError.message}. Continuing with admission wait...`);
      }
      
      stillInWaitingRoom = true;
    }
    
    // If we're in waiting room, wait for the full timeout period for admission
    if (stillInWaitingRoom) {
      log(`Bot is in Google Meet waiting room. Waiting for ${timeout}ms for admission...`);
      
      const checkInterval = 2000; // Check every 2 seconds for faster detection
      const startTime = Date.now();
      
      while (Date.now() - startTime < timeout) {
        // Check if we're still in waiting room using visibility
        const stillWaiting = await checkForWaitingRoomIndicators(page);
        
        if (!stillWaiting) {
          log("Google Meet waiting room indicator disappeared - checking if bot was admitted or rejected...");
          
          // CRITICAL: Check for rejection first since that's a definitive outcome
          const isRejected = await checkForGoogleRejection(page);
          if (isRejected) {
            log("🚨 Bot was rejected from the Google Meet meeting by admin");
            throw new Error("Bot admission was rejected by meeting admin");
          }
          
          // Check for admission indicators since waiting room disappeared and no rejection found
          const admissionFound = await checkForGoogleAdmissionIndicators(page);
          
          if (admissionFound) {
            log(`✅ Bot was admitted to the Google Meet meeting: meeting controls confirmed`);
            return true;
          }
          
          // Keep waiting if neither admitted nor rejected
        }
        
        // Wait before next check
        await page.waitForTimeout(checkInterval);
        log(`Still in Google Meet waiting room... ${Math.round((Date.now() - startTime) / 1000)}s elapsed`);
      }
      
      // After waiting, check if we're still in waiting room using visibility
      const finalWaitingCheck = await checkForWaitingRoomIndicators(page);
      
      if (finalWaitingCheck) {
        throw new Error("Bot is still in the Google Meet waiting room after timeout - not admitted to the meeting");
      }
    } else {
      // Not in waiting room and not admitted yet: actively poll during the timeout
      log(`No waiting room detected. Polling for admission for up to ${timeout}ms...`);
      const checkInterval = 2000;
      const startTime = Date.now();
      while (Date.now() - startTime < timeout) {
        // Rejection check first
        const isRejected = await checkForGoogleRejection(page);
        if (isRejected) {
          log("🚨 Bot was rejected from the Google Meet meeting by admin (polling mode)");
          throw new Error("Bot admission was rejected by meeting admin");
        }

        // Admission indicators
        const admissionFound = await checkForGoogleAdmissionIndicators(page);
        const lobbyVisible = await checkForWaitingRoomIndicators(page);
        if (admissionFound && !lobbyVisible) {
          log("✅ Bot admitted during polling window (meeting controls visible)");
          return true;
        }

        // If lobby appears later, switch to waiting-room handling by breaking
        if (lobbyVisible) {
          log("ℹ️ Waiting room appeared during polling. Switching to waiting-room monitoring...");
          
          // --- Call awaiting admission callback when waiting room appears during polling ---
          try {
            await callAwaitingAdmissionCallback(botConfig);
            log("Awaiting admission callback sent successfully (during polling)");
          } catch (callbackError: any) {
            log(`Warning: Failed to send awaiting admission callback: ${callbackError.message}. Continuing...`);
          }
          
          stillInWaitingRoom = true;
          break;
        }

        await page.waitForTimeout(checkInterval);
        log(`Polling for Google Meet admission... ${Math.round((Date.now() - startTime) / 1000)}s elapsed`);
      }

      if (stillInWaitingRoom) {
        // Re-run the waiting room loop with the remaining time
        const elapsed = Date.now() - (Date.now()); // placeholder to keep types; we will just run a short bounded loop
        const checkInterval = 2000;
        const startTime2 = Date.now();
        while (Date.now() - startTime2 < timeout) {
          const stillWaiting = await checkForWaitingRoomIndicators(page);
          if (!stillWaiting) {
            const isRejected2 = await checkForGoogleRejection(page);
            if (isRejected2) throw new Error("Bot admission was rejected by meeting admin");
            const admissionFound2 = await checkForGoogleAdmissionIndicators(page);
            if (admissionFound2) return true;
          }
          await page.waitForTimeout(checkInterval);
        }
      }
    }
    
    // Final check after waiting/polling
    log("Performing final admission check after waiting/polling window...");
    const finalAdmissionFound = await checkForGoogleAdmissionIndicators(page);
    const finalLobbyVisible = await checkForWaitingRoomIndicators(page);
    if (finalAdmissionFound && !finalLobbyVisible) {
      await page.screenshot({ path: '/app/storage/screenshots/bot-checkpoint-2-admitted.png', fullPage: true });
      log("📸 Screenshot taken: Bot confirmed admitted to meeting");
      log("Successfully admitted to the Google Meet meeting");
      return true;
    }

    // Before concluding failure, check for rejection one last time
    log("No admission indicators after timeout - checking rejection one last time...");
    const finalRejected = await checkForGoogleRejection(page);
    if (finalRejected) {
      throw new Error("Bot admission was rejected by meeting admin");
    }

    await page.screenshot({ path: '/app/storage/screenshots/bot-checkpoint-3-no-indicators.png', fullPage: true });
    log("📸 Screenshot taken: No meeting indicators found after timeout");
    throw new Error("Bot failed to join the Google Meet meeting - no meeting indicators found within timeout");
    
  } catch (error: any) {
    throw new Error(
      `Bot was not admitted into the Google Meet meeting within the timeout period: ${error.message}`
    );
  }
};

// Helper function to check for waiting room indicators
const checkForWaitingRoomIndicators = async (page: Page): Promise<boolean> => {
  for (const waitingIndicator of googleWaitingRoomIndicators) {
    try {
      const element = await page.locator(waitingIndicator).first();
      if (await element.isVisible()) {
        return true;
      }
    } catch {
      continue;
    }
  }
  return false;
};

const joinGoogleMeeting = async (page: Page, meetingUrl: string, botName: string, botConfig: BotConfig) => {
  const enterNameField = 'input[type="text"][aria-label="Your name"]';
  const joinButton = '//button[.//span[text()="Ask to join"]]';
  const muteButton = '[aria-label*="Turn off microphone"]';
  const cameraOffButton = '[aria-label*="Turn off camera"]';

  await page.goto(meetingUrl, { waitUntil: "networkidle" });
  await page.bringToFront();

  // Take screenshot after navigation
  await page.screenshot({ path: '/app/storage/screenshots/bot-checkpoint-0-after-navigation.png', fullPage: true });
  log("📸 Screenshot taken: After navigation to meeting URL");
  
  // --- Call joining callback to notify bot-manager that bot is joining ---
  try {
    await callJoiningCallback(botConfig);
    log("Joining callback sent successfully");
  } catch (callbackError: any) {
    log(`Warning: Failed to send joining callback: ${callbackError.message}. Continuing with join process...`);
  }

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
  await page.screenshot({ path: '/app/storage/screenshots/bot-checkpoint-0-name-field-found.png', fullPage: true });
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
  log(`${botName} joined the Google Meet Meeting.`);
  
  // Take screenshot after clicking "Ask to join"
  await page.screenshot({ path: '/app/storage/screenshots/bot-checkpoint-0-after-ask-to-join.png', fullPage: true });
  log("📸 Screenshot taken: After clicking 'Ask to join'");
};

// Prepare for recording by exposing necessary functions
const prepareForRecording = async (page: Page, botConfig: BotConfig): Promise<void> => {
  // Expose the logBot function to the browser context
  await page.exposeFunction("logBot", (msg: string) => {
    log(msg);
  });

  // Expose selectors/constants for browser context consumers
  await page.exposeFunction("getGoogleMeetSelectors", (): { googlePrimaryLeaveButtonSelectors: string[]; googleSecondaryLeaveButtonSelectors: string[]; googleLeaveSelectors: string[] } => ({
    googlePrimaryLeaveButtonSelectors,
    googleSecondaryLeaveButtonSelectors,
    googleLeaveSelectors
  }));

  // Expose bot config for callback functions
  await page.exposeFunction("getBotConfig", (): BotConfig => botConfig);

  // Ensure leave function is available even before admission
  await page.evaluate((selectorsData) => {
    if (typeof (window as any).performLeaveAction !== "function") {
      (window as any).performLeaveAction = async () => {
        try {
          // Call leave callback first to notify bot-manager
          (window as any).logBot?.("🔥 Calling leave callback before attempting to leave...");
          try {
            const botConfig = (window as any).getBotConfig?.();
            if (botConfig) {
              // We need to call the callback from Node.js context, not browser context
              // This will be handled by the Node.js side when leaveGoogleMeet is called
              (window as any).logBot?.("📡 Leave callback will be sent from Node.js context");
            }
          } catch (callbackError: any) {
            (window as any).logBot?.(`⚠️ Warning: Could not prepare leave callback: ${callbackError.message}`);
          }

          // Use directly injected selectors (stateless approach)
          const leaveSelectors = selectorsData.googleLeaveSelectors || [];

          (window as any).logBot?.("🔍 Starting stateless Google Meet leave button detection...");
          (window as any).logBot?.(`📋 Will try ${leaveSelectors.length} selectors until one works`);
          
          // Try each selector until one works (stateless iteration)
          for (let i = 0; i < leaveSelectors.length; i++) {
            const selector = leaveSelectors[i];
            try {
              (window as any).logBot?.(`🔍 [${i + 1}/${leaveSelectors.length}] Trying selector: ${selector}`);
              
              const button = document.querySelector(selector) as HTMLElement;
              if (button) {
                // Check if button is visible and clickable
                const rect = button.getBoundingClientRect();
                const computedStyle = getComputedStyle(button);
                const isVisible = rect.width > 0 && rect.height > 0 && 
                                computedStyle.display !== 'none' && 
                                computedStyle.visibility !== 'hidden' &&
                                computedStyle.opacity !== '0';
                
                if (isVisible) {
                  const ariaLabel = button.getAttribute('aria-label');
                  const textContent = button.textContent?.trim();
                  
                  (window as any).logBot?.(`✅ Found clickable button: aria-label="${ariaLabel}", text="${textContent}"`);
                  
                  // Scroll into view and click
                  button.scrollIntoView({ behavior: 'smooth', block: 'center' });
                  await new Promise((resolve) => setTimeout(resolve, 500));
                  
                  (window as any).logBot?.(`🖱️ Clicking Google Meet button...`);
                  button.click();
                  await new Promise((resolve) => setTimeout(resolve, 1000));
                  
                  (window as any).logBot?.(`✅ Successfully clicked button with selector: ${selector}`);
                  return true;
                } else {
                  (window as any).logBot?.(`ℹ️ Button found but not visible for selector: ${selector}`);
                }
              } else {
                (window as any).logBot?.(`ℹ️ No button found for selector: ${selector}`);
              }
            } catch (e: any) {
              (window as any).logBot?.(`❌ Error with selector ${selector}: ${e.message}`);
              continue;
            }
          }
          
          (window as any).logBot?.("❌ No working leave/cancel button found - tried all selectors");
          return false;
        } catch (err: any) {
          (window as any).logBot?.(`Error during Google Meet leave attempt: ${err.message}`);
          return false;
        }
      };
    }
  }, { googleLeaveSelectors });
};

// Modified to use new services - Google Meet recording functionality
const startGoogleRecording = async (page: Page, botConfig: BotConfig) => {
  // Initialize WhisperLive service on Node.js side
  const whisperLiveService = new WhisperLiveService({
    redisUrl: botConfig.redisUrl,
    maxClients: parseInt(process.env.WL_MAX_CLIENTS || '10', 10),
    whisperLiveUrl: process.env.WHISPER_LIVE_URL
  });

  // Initialize WhisperLive connection
  const whisperLiveUrl = await whisperLiveService.initialize();
  if (!whisperLiveUrl) {
    log("ERROR: Could not initialize WhisperLive service for Google Meet. Aborting recording.");
    return;
  }

  log(`[Node.js] Using WhisperLive URL for Google Meet: ${whisperLiveUrl}`);
  log("Starting Google Meet recording with WebSocket connection");

  // Load browser utility classes from the bundled global file
  try {
    await page.addScriptTag({
      path: require('path').join(__dirname, '../../browser-utils.global.js'),
    });
  } catch (error: any) {
    log(`Warning: Could not load browser utils via addScriptTag: ${error.message}`);
    log("Attempting alternative loading method...");
    
    // Alternative: Load script content and evaluate it
    const fs = require('fs');
    const path = require('path');
    const scriptPath = path.join(__dirname, '../../browser-utils.global.js');
    
    try {
      const scriptContent = fs.readFileSync(scriptPath, 'utf8');
      await page.evaluate(async (script) => {
        try {
          // Use Trusted Types to inject inline script text, or fallback to Blob URL
          const injectWithTrustedTypes = () => {
            const policy = (window as any).trustedTypes?.createPolicy('vexaPolicy', {
              createScript: (s: string) => s,
              createScriptURL: (s: string) => s
            });
            const scriptEl = document.createElement('script');
            if (policy) {
              // Assign TrustedScript to text to satisfy Meet's Trusted Types policy
              (scriptEl as any).text = policy.createScript(script);
              document.head.appendChild(scriptEl);
              return Promise.resolve();
            }
            return Promise.reject(new Error('Trusted Types not available'));
          };

          const injectWithBlobUrl = () => new Promise<void>((resolve, reject) => {
            try {
              const blob = new Blob([script], { type: 'text/javascript' });
              const url = URL.createObjectURL(blob);
              const policy = (window as any).trustedTypes?.createPolicy('vexaPolicy', {
                createScriptURL: (u: string) => u
              });
              const scriptEl = document.createElement('script');
              // If Trusted Types enforced for src, use policy-created URL
              const finalUrl = policy ? (policy as any).createScriptURL(url) : url;
              (scriptEl as any).src = finalUrl as any;
              scriptEl.onload = () => {
                resolve();
              };
              scriptEl.onerror = (e) => {
                reject(new Error('Failed to load browser utils via blob URL'));
              };
              document.head.appendChild(scriptEl);
            } catch (err) {
              reject(err as any);
            }
          });

          // Try Trusted Types inline first, then fallback to blob URL
          try {
            await injectWithTrustedTypes();
          } catch {
            await injectWithBlobUrl();
          }

          // Verify availability on window
          const utils = (window as any).VexaBrowserUtils;
          if (!utils) {
            console.error('VexaBrowserUtils not found after injection');
          } else {
            console.log('VexaBrowserUtils loaded keys:', Object.keys(utils));
          }
        } catch (error) {
          console.error('Error injecting browser utils script:', (error as any)?.message || error);
          throw error;
        }
      }, scriptContent);
      log("Browser utils loaded and available as window.VexaBrowserUtils");
    } catch (evalError: any) {
      log(`Error loading browser utils via evaluate: ${evalError.message}`);
      throw new Error(`Failed to load browser utilities: ${evalError.message}`);
    }
  }

  // Pass the necessary config fields and the resolved URL into the page context
  await page.evaluate(
    async (pageArgs: {
      botConfigData: BotConfig;
      whisperUrlForBrowser: string;
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
      const { botConfigData, whisperUrlForBrowser, selectors } = pageArgs;

      // Use browser utility classes from the global bundle
      const browserUtils = (window as any).VexaBrowserUtils;
      (window as any).logBot(`Browser utils available: ${Object.keys(browserUtils || {}).join(', ')}`);
      
      const audioService = new browserUtils.BrowserAudioService({
        targetSampleRate: 16000,
        bufferSize: 4096,
        inputChannels: 1,
        outputChannels: 1
      });

      // Use BrowserWhisperLiveService with simple mode for Google Meet
      const whisperLiveService = new browserUtils.BrowserWhisperLiveService({
        whisperLiveUrl: whisperUrlForBrowser
      }, false); // Use simple mode for Google Meet


      await new Promise<void>((resolve, reject) => {
        try {
          (window as any).logBot("Starting Google Meet recording process with new services.");
          
          // Find and create combined audio stream
          audioService.findMediaElements().then(async (mediaElements: HTMLMediaElement[]) => {
            if (mediaElements.length === 0) {
              reject(
                new Error(
                  "[Google Meet BOT Error] No active media elements found after multiple retries. Ensure the Google Meet meeting media is playing."
                )
              );
              return;
            }

            // Create combined audio stream
            return await audioService.createCombinedAudioStream(mediaElements);
          }).then(async (combinedStream: MediaStream | undefined) => {
            if (!combinedStream) {
              reject(new Error("[Google Meet BOT Error] Failed to create combined audio stream"));
              return;
            }
            // Initialize audio processor
            return await audioService.initializeAudioProcessor(combinedStream);
          }).then(async (processor: any) => {
            // Setup audio data processing
            audioService.setupAudioDataProcessor(async (audioData: Float32Array, sessionStartTime: number | null) => {
              // Only send after server ready (canonical Teams pattern)
              if (!whisperLiveService.isReady()) {
                // Skip sending until server is ready
                return;
              }
              // Compute simple RMS and peak for diagnostics
              let sumSquares = 0;
              let peak = 0;
              for (let i = 0; i < audioData.length; i++) {
                const v = audioData[i];
                sumSquares += v * v;
                const a = Math.abs(v);
                if (a > peak) peak = a;
              }
              const rms = Math.sqrt(sumSquares / Math.max(1, audioData.length));
              // Diagnostic: send metadata first
              whisperLiveService.sendAudioChunkMetadata(audioData.length, 16000);
              // Send audio data to WhisperLive
              const success = whisperLiveService.sendAudioData(audioData);
              if (!success) {
                (window as any).logBot("Failed to send Google Meet audio data to WhisperLive");
              }
            });

            // Initialize WhisperLive WebSocket connection
              return await whisperLiveService.connectToWhisperLive(
              botConfigData,
              (data: any) => {
                const logFn = (window as any).logBot;
                // Reduce log spam: log only important status changes and completed transcript segments
                if (!data || typeof data !== 'object') {
                  return;
                }
                if (data["status"] === "ERROR") {
                  logFn(`Google Meet WebSocket Server Error: ${data["message"]}`);
                  return;
                }
                if (data["status"] === "WAIT") {
                  logFn(`Google Meet Server busy: ${data["message"]}`);
                  return;
                }
                if (!whisperLiveService.isReady() && data["status"] === "SERVER_READY") {
                  whisperLiveService.setServerReady(true);
                  logFn("Google Meet Server is ready.");
                  return;
                }
                if (data["language"]) {
                  if (!(window as any).__vexaLangLogged) {
                    (window as any).__vexaLangLogged = true;
                    logFn(`Google Meet Language detected: ${data["language"]}`);
                  }
                  // do not return; language can accompany segments
                }
                if (data["message"] === "DISCONNECT") {
                  logFn("Google Meet Server requested disconnect.");
                  whisperLiveService.close();
                  return;
                }
                // Log only completed transcript segments, with deduplication
                if (Array.isArray(data.segments)) {
                  const completedTexts = data.segments
                    .filter((s: any) => s && s.completed && s.text)
                    .map((s: any) => s.text as string);
                  if (completedTexts.length > 0) {
                    const transcriptKey = completedTexts.join(' ').trim();
                    if (transcriptKey && transcriptKey !== (window as any).__lastTranscript) {
                      (window as any).__lastTranscript = transcriptKey;
                      logFn(`Transcript: ${transcriptKey}`);
                    }
                  }
                }
              },
              (event: Event) => {
                (window as any).logBot(`[Google Meet Failover] WebSocket error. This will trigger retry logic.`);
              },
              async (event: CloseEvent) => {
                (window as any).logBot(`[Google Meet Failover] WebSocket connection closed. Code: ${event.code}, Reason: ${event.reason}.`);
                // Retry logic would be handled by WebSocketManager
              }
            );
          }).then(() => {
            // Initialize Google-specific speaker detection (Teams-style with Google selectors)
            (window as any).logBot("Initializing Google Meet speaker detection...");

            const initializeGoogleSpeakerDetection = (whisperLiveService: any, audioService: any, botConfigData: any) => {
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
                try {
                  whisperLiveService.sendSpeakerEvent(
                    eventType,
                    participantName,
                    participantId,
                    relativeTimestampMs,
                    botConfigData
                  );
                } catch {}
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

            initializeGoogleSpeakerDetection(whisperLiveService, audioService, botConfigData);

            // Simple single-strategy participant extraction from main video area
            (window as any).logBot("Initializing simplified participant counting (main frame text scan)...");

            const extractParticipantsFromMain = (botName: string | undefined): string[] => {
              const participants: string[] = [];
              const mainElement = document.querySelector('main');
              if (mainElement) {
                const nameElements = mainElement.querySelectorAll('*');
                nameElements.forEach((el: Element) => {
                  const element = el as HTMLElement;
                  const text = (element.textContent || '').trim();
                  if (text && element.children.length === 0) {
                    if (/^[A-Z][a-z]+\s[A-Z][a-z]+$/.test(text) || (botName && text === botName)) {
                      participants.push(text);
                    }
                  }
                });
              }
              const tooltips = document.querySelectorAll('main [role="tooltip"]');
              tooltips.forEach((el: Element) => {
                const text = (el.textContent || '').trim();
                if (text && (/^[A-Z][a-z]+\s[A-Z][a-z]+$/.test(text) || (botName && text === botName))) {
                  participants.push(text);
                }
              });
              return Array.from(new Set(participants));
            };

            (window as any).getGoogleMeetActiveParticipants = () => {
              const names = extractParticipantsFromMain((botConfigData as any)?.botName);
              (window as any).logBot(`🔍 [Google Meet Participants] ${JSON.stringify(names)}`);
              return names;
            };
            (window as any).getGoogleMeetActiveParticipantsCount = () => {
              return (window as any).getGoogleMeetActiveParticipants().length;
            };
            
            // Setup Google Meet meeting monitoring (browser context)
            const setupGoogleMeetingMonitoring = (botConfigData: any, audioService: any, whisperLiveService: any, resolve: any) => {
              (window as any).logBot("Setting up Google Meet meeting monitoring...");
              
              const startupAloneTimeoutSeconds = 20 * 60; // 20 minutes during startup
              const everyoneLeftTimeoutSeconds = 10; // 10 seconds after speakers identified
              
              let aloneTime = 0;
              let lastParticipantCount = 0;
              let speakersIdentified = false;
              let hasEverHadMultipleParticipants = false;

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
                      clearInterval(checkInterval);
                      audioService.disconnect();
                      whisperLiveService.close();
                      reject(new Error("GOOGLE_MEET_BOT_LEFT_ALONE_TIMEOUT"));
                    } else {
                      (window as any).logBot(`Google Meet bot has been alone for ${startupAloneTimeoutSeconds/60} minutes during startup with no other participants. Stopping recorder...`);
                      clearInterval(checkInterval);
                      audioService.disconnect();
                      whisperLiveService.close();
                      reject(new Error("GOOGLE_MEET_BOT_STARTUP_ALONE_TIMEOUT"));
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

              // Use previously defined simplified helpers; no fallbacks

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

            setupGoogleMeetingMonitoring(botConfigData, audioService, whisperLiveService, resolve);
          }).catch((err: any) => {
            reject(err);
          });

        } catch (error: any) {
          return reject(new Error("[Google Meet BOT Error] " + error.message));
        }
      });
    },
    { 
      botConfigData: botConfig, 
      whisperUrlForBrowser: whisperLiveUrl,
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
  
  // Start periodic removal checking from Node.js side (does not exit the process; caller handles gracefulLeave)
  log("Starting periodic Google Meet removal monitoring...");
  let removalDetected = false;
  const removalCheckInterval = setInterval(async () => {
    try {
      const isRemoved = await checkForGoogleRemoval(page);
      if (isRemoved && !removalDetected) {
        removalDetected = true; // Prevent duplicate detection
        log("🚨 Google Meet removal detected from Node.js side. Initiating graceful shutdown...");
        clearInterval(removalCheckInterval);
        
        try {
          // Attempt to click any dismiss buttons to close the modal gracefully
          await page.evaluate(() => {
            const clickIfVisible = (el: HTMLElement | null) => {
              if (!el) return;
              const rect = el.getBoundingClientRect();
              const cs = getComputedStyle(el);
              if (rect.width > 0 && rect.height > 0 && cs.display !== 'none' && cs.visibility !== 'hidden') {
                el.click();
              }
            };
            const btns = Array.from(document.querySelectorAll('button')) as HTMLElement[];
            for (const b of btns) {
              const t = (b.textContent || b.innerText || '').trim().toLowerCase();
              const a = (b.getAttribute('aria-label') || '').toLowerCase();
              if (t === 'dismiss' || a.includes('dismiss') || t === 'ok' || a.includes('ok')) { 
                clickIfVisible(b); 
                break; 
              }
            }
          });
        } catch {}
        
        // Properly exit with removal reason - this will prevent the normal completion flow
        throw new Error("GOOGLE_MEET_BOT_REMOVED_BY_ADMIN");
      }
    } catch (error: any) {
      if (error.message === "GOOGLE_MEET_BOT_REMOVED_BY_ADMIN") {
        // Re-throw the removal signal
        throw error;
      }
      log(`Error during Google Meet removal check: ${error.message}`);
    }
  }, 1500);

  // After page.evaluate finishes, cleanup services
  await whisperLiveService.cleanup();
  
  // Clear removal check interval
  clearInterval(removalCheckInterval);
};

// --- ADDED: Exported function to trigger leave from Node.js ---
export async function leaveGoogleMeet(page: Page | null, botConfig?: BotConfig, reason: string = "manual_leave"): Promise<boolean> {
  log("[leaveGoogleMeet] Triggering leave action in browser context...");
  if (!page || page.isClosed()) {
    log("[leaveGoogleMeet] Page is not available or closed.");
    return false;
  }

  // Call leave callback first to notify bot-manager
  if (botConfig) {
    try {
      log("🔥 Calling leave callback before attempting to leave...");
      await callLeaveCallback(botConfig, reason);
      log("✅ Leave callback sent successfully");
    } catch (callbackError: any) {
      log(`⚠️ Warning: Failed to send leave callback: ${callbackError.message}. Continuing with leave attempt...`);
    }
  } else {
    log("⚠️ Warning: No bot config provided, cannot send leave callback");
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
