import { Page } from "playwright";
import { log, callAwaitingAdmissionCallback } from "../../utils";
import { BotConfig } from "../../types";
import {
  googleInitialAdmissionIndicators,
  googleWaitingRoomIndicators,
  googleRejectionIndicators
} from "./selectors";

// Function to check if bot has been rejected from the meeting
export async function checkForGoogleRejection(page: Page): Promise<boolean> {
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
}

// Helper function to check for any visible and enabled admission indicators
// Returns true only if at least 2 indicators are found (to reduce false positives)
export async function checkForGoogleAdmissionIndicators(page: Page): Promise<boolean> {
  const foundSelectors: string[] = [];
  for (const selector of googleInitialAdmissionIndicators) {
    try {
      const element = page.locator(selector).first();
      const isVisible = await element.isVisible();
      if (isVisible) {
        const isDisabled = await element.getAttribute('aria-disabled');
        if (isDisabled !== 'true') {
          foundSelectors.push(selector);log(`✅ Found Google Meet admission indicator: ${selector}`);
        }
      }
    } catch (error) {
      // Continue to next selector if this one fails
      continue;
    }
  }// CRITICAL: Require at least 2 indicators to reduce false positives
  // Single indicator (like "Leave call" button) can appear in waiting room UI
  if (foundSelectors.length >= 2) {
    log(`✅ Multiple admission indicators found (${foundSelectors.length}), confirming admission`);
    return true;
  }
  if (foundSelectors.length === 1) {
    log(`⚠️ Only one admission indicator found (${foundSelectors[0]}), rejecting as likely false positive`);
    // Reject single indicator to prevent false positives
    return false;
  }
  return false;
}

// Silent admission check (doesn't send callbacks) - used for verification
export async function checkForGoogleAdmissionSilent(page: Page): Promise<boolean> {
  // Just check indicators without sending any callbacks
  return await checkForGoogleAdmissionIndicators(page);
}

// Helper function to check for waiting room indicators
export async function checkForWaitingRoomIndicators(page: Page): Promise<boolean> {
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
}

// New function to wait for Google Meet meeting admission (canonical Teams-style)
export async function waitForGoogleMeetingAdmission(
  page: Page,
  timeout: number,
  botConfig: BotConfig
): Promise<boolean> {
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
      
      // CRITICAL FIX: When bot is immediately admitted, skip awaiting_admission callback
      // The bot should go directly from "joining" -> "active", not "joining" -> "awaiting_admission" -> "active"
      // Sending awaiting_admission here causes a race condition where the callback arrives before
      // the "joining" callback is processed, causing REQUESTED -> AWAITING_ADMISSION (invalid transition)
      log("Bot immediately admitted - skipping awaiting_admission callback to avoid race condition");
      
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
      
      // CRITICAL: Wait a moment to ensure "joining" callback is processed before sending "awaiting_admission"
      // This prevents race condition where awaiting_admission arrives before joining is processed
      log("Waiting 1 second to ensure joining callback is processed before sending awaiting_admission...");
      await new Promise(resolve => setTimeout(resolve, 1000));
      
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
        // CRITICAL: Check for meeting ended/user left while waiting
        const { checkForGoogleRemoval } = await import("./removal");
        const isRemoved = await checkForGoogleRemoval(page);
        if (isRemoved) {
          log("🚨 Meeting ended or user left while bot was waiting for admission");
          throw new Error("Meeting ended or user left while waiting for admission");
        }
        
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
        // CRITICAL: Check for meeting ended/user left while polling
        const { checkForGoogleRemoval } = await import("./removal");
        const isRemoved = await checkForGoogleRemoval(page);
        if (isRemoved) {
          log("🚨 Meeting ended or user left while bot was polling for admission");
          throw new Error("Meeting ended or user left while waiting for admission");
        }
        
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
}
