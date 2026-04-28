import { Page } from "playwright";
import { log, callAwaitingAdmissionCallback } from "../../utils";
import { BotConfig } from "../../types";
import { checkEscalation, triggerEscalation, getEscalationExtensionMs } from "../shared/escalation";
import {
  teamsInitialAdmissionIndicators,
  teamsWaitingRoomIndicators,
  teamsRejectionIndicators
} from "./selectors";

// Returns true if any known waiting-room/lobby indicator is currently visible.
// Used to be a single-selector check on teamsWaitingRoomIndicators[0]
// (English-only), which silently failed on the Teams 2 light-meetings page
// in German locales and caused the bot to never enter the proper 5 min
// waiting-room loop.
async function isLobbyVisible(page: Page): Promise<boolean> {
  for (const selector of teamsWaitingRoomIndicators) {
    try {
      const visible = await page.locator(selector).first().isVisible();
      if (visible) {
        return true;
      }
    } catch {
      continue;
    }
  }
  return false;
}

// Function to check if bot has been rejected from the meeting
export async function checkForTeamsRejection(page: Page): Promise<boolean> {
  try {
    // Check for rejection indicators
    for (const selector of teamsRejectionIndicators) {
      try {
        const element = await page.locator(selector).first();
        if (await element.isVisible()) {
          log(`🚨 Teams admission rejection detected: Found rejection indicator "${selector}"`);
          return true;
        }
      } catch (e) {
        // Continue checking other selectors
        continue;
      }
    }
    return false;
  } catch (error: any) {
    log(`Error checking for Teams rejection: ${error.message}`);
    return false;
  }
}

// Helper function to check for any visible and enabled Leave button
export async function checkForAdmissionIndicators(page: Page): Promise<boolean> {
  for (const selector of teamsInitialAdmissionIndicators) {
    try {
      const element = page.locator(selector).first();
      const isVisible = await element.isVisible();
      if (isVisible) {
        const isDisabled = await element.getAttribute('aria-disabled');
        if (isDisabled !== 'true') {
          log(`✅ Found admission indicator: ${selector}`);
          return true;
        }
      }
    } catch (error) {
      // Continue to next selector if this one fails
      continue;
    }
  }
  return false;
}

// Silent admission check (doesn't send callbacks) - used for verification
export async function checkForTeamsAdmissionSilent(page: Page): Promise<boolean> {
  // Just check indicators without sending any callbacks
  return await checkForAdmissionIndicators(page);
}

export async function waitForTeamsMeetingAdmission(
  page: Page,
  timeout: number,
  botConfig: BotConfig
): Promise<boolean> {
  try {
    log("Waiting for Teams meeting admission...");
    
    // FIRST: Check if bot is already admitted (no waiting room needed)
    log("Checking if bot is already admitted to the Teams meeting...");
    
    // Check for any visible Leave button (multiple selectors for robustness)
    const initialLeaveButtonFound = await checkForAdmissionIndicators(page);
    
    // Negative check: ensure we're not still in lobby/pre-join.
    // We rely ONLY on lobby text — the visible "Join now" button is a pre-join
    // control, not a lobby indicator, and treating it as such caused
    // false-positive awaiting_admission loops when Step 6 silently failed.
    const initialLobbyTextVisible = await isLobbyVisible(page);

    if (initialLeaveButtonFound && !initialLobbyTextVisible) {
      log(`Found Teams admission indicator: visible Leave button - Bot is already admitted to the meeting!`);
      
      // CRITICAL FIX: When bot is immediately admitted, skip awaiting_admission callback
      // The bot should go directly from "joining" -> "active", not "joining" -> "awaiting_admission" -> "active"
      // Sending awaiting_admission here causes a race condition where the callback arrives before
      // the "joining" callback is processed, causing REQUESTED -> AWAITING_ADMISSION (invalid transition)
      log("Bot immediately admitted - skipping awaiting_admission callback to avoid race condition");
      
      log("Successfully admitted to the Teams meeting - no waiting room required");
      return true;
    }
    
    log("Bot not yet admitted - checking for Teams waiting room indicators...");
    
    // Check for waiting room indicators using visibility checks
    let stillInWaitingRoom = false;

    // Lobby-text only — see note above on Join button false positives.
    const waitingLobbyTextVisible = await isLobbyVisible(page);

    if (waitingLobbyTextVisible) {
      log(`Found Teams waiting room indicator: lobby text visible - Bot is still in waiting room`);
      
      // CRITICAL: Wait a moment to ensure "joining" callback is processed before sending "awaiting_admission"
      // This prevents race condition where awaiting_admission arrives before joining is processed
      log("Waiting 1 second to ensure joining callback is processed before sending awaiting_admission...");
      await new Promise(resolve => setTimeout(resolve, 1000));
      
      // --- Call awaiting admission callback to notify meeting-api that bot is waiting ---
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
      log(`Bot is in Teams waiting room. Waiting for ${timeout}ms for admission...`);

      const checkInterval = 2000; // Check every 2 seconds for faster detection
      const startTime = Date.now();
      let unknownStateDuration = 0;
      const effectiveTimeout = () => timeout + getEscalationExtensionMs();

      while (Date.now() - startTime < effectiveTimeout()) {
        // Lobby-text only — a visible "Join now" button means we never left
        // the pre-join screen, NOT that we're in the lobby.
        const lobbyTextStillVisible = await isLobbyVisible(page);

        const stillWaiting = lobbyTextStillVisible;

        if (!stillWaiting) {
          log("Teams waiting room indicator disappeared - checking if bot was admitted or rejected...");
          unknownStateDuration += checkInterval;

          // CRITICAL: Check for rejection first since that's a definitive outcome
          const isRejected = await checkForTeamsRejection(page);
          if (isRejected) {
            log("🚨 Bot was rejected from the Teams meeting by admin");
            throw new Error("Bot admission was rejected by meeting admin");
          }

          // Check for admission indicators since waiting room disappeared and no rejection found
          const leaveButtonNowFound = await checkForAdmissionIndicators(page);

          if (leaveButtonNowFound) {
            log(`✅ Bot was admitted to the Teams meeting: Leave button confirmed`);
            return true;
          } else {
            log("⚠️ Teams waiting room disappeared but no clear admission indicators found - assuming admitted");
            return true; // Fallback: if waiting room disappeared and no rejection, assume admitted
          }
        } else {
          unknownStateDuration = 0;
        }

        // Escalation check
        const elapsedMs = Date.now() - startTime;
        const escalation = checkEscalation(elapsedMs, timeout, unknownStateDuration);
        if (escalation) {
          await triggerEscalation(botConfig, escalation.reason);
        }

        // Wait before next check
        await page.waitForTimeout(checkInterval);
        log(`Still in Teams waiting room... ${Math.round((Date.now() - startTime) / 1000)}s elapsed`);
      }
      
      // After waiting, check if we're still in waiting room using visibility
      const finalLobbyTextVisible = await isLobbyVisible(page);

      const finalWaitingCheck = finalLobbyTextVisible;

      if (finalWaitingCheck) {
        throw new Error("Bot is still in the Teams waiting room after timeout - not admitted to the meeting");
      }
    }
    
    // PRIORITY: Check for Teams meeting controls/toolbar (most reliable indicator)
    log("Checking for Teams meeting controls as primary admission indicator...");
    
    // Check for any visible Leave button (multiple selectors for robustness)
    log("Checking for visible Leave button in meeting toolbar...");
    
    const finalLeaveButtonFound = await checkForAdmissionIndicators(page);
    
    // Negative check: ensure we're not still in lobby/pre-join (lobby text only).
    const postLobbyTextVisible = await isLobbyVisible(page);

    const admitted = finalLeaveButtonFound && !postLobbyTextVisible;
    
    if (admitted) {
      log(`Found Teams admission indicator: visible Leave button - Bot is admitted to the meeting`);
    }
    
    if (!admitted) {
      // The bot may still be transitioning OR sitting in a lobby that we
      // don't yet have selectors for. Poll for the full waitingRoomTimeout
      // (default 5 min) before giving up — a hardcoded 30s here was much
      // shorter than the time hosts typically take to admit a bot, and
      // produced spurious admission_timeout exits.
      log(`No Teams meeting indicators found yet — polling for up to ${Math.round(timeout / 1000)}s...`);
      const pollStart = Date.now();
      const pollTimeout = timeout;
      const pollInterval = 2000;

      while (Date.now() - pollStart < pollTimeout) {
        await page.waitForTimeout(pollInterval);

        // Check for rejection first
        const isRejected = await checkForTeamsRejection(page);
        if (isRejected) {
          log("🚨 Bot was rejected from the Teams meeting by admin");
          throw new Error("Bot admission was rejected by meeting admin");
        }

        // Check for admission
        const leaveButtonFound = await checkForAdmissionIndicators(page);
        if (leaveButtonFound) {
          log("✅ Bot admitted during polling (Leave button found)");
          return true;
        }

        // Check for waiting room (enter the waiting loop)
        const lobbyText = await isLobbyVisible(page).catch(() => false);
        if (lobbyText) {
          log("Found Teams lobby text — entering waiting room loop...");
          // Re-enter the waiting room logic from here
          try {
            await callAwaitingAdmissionCallback(botConfig);
            log("Awaiting admission callback sent successfully");
          } catch (callbackError: any) {
            log(`Warning: Failed to send awaiting admission callback: ${callbackError.message}`);
          }

          // Wait for admission in the lobby
          const lobbyStart = Date.now();
          while (Date.now() - lobbyStart < timeout) {
            const stillInLobby = await isLobbyVisible(page).catch(() => false);
            if (!stillInLobby) {
              const admittedNow = await checkForAdmissionIndicators(page);
              if (admittedNow) {
                log("✅ Bot was admitted from the lobby!");
                return true;
              }
              const rejectedNow = await checkForTeamsRejection(page);
              if (rejectedNow) {
                throw new Error("Bot admission was rejected by meeting admin");
              }
            }
            await page.waitForTimeout(2000);
            log(`Still in Teams waiting room... ${Math.round((Date.now() - lobbyStart) / 1000)}s elapsed`);
          }
          throw new Error("Bot is still in the Teams waiting room after timeout");
        }

        log(`Polling for admission... ${Math.round((Date.now() - pollStart) / 1000)}s elapsed`);
      }

      // After polling timeout, final check
      log("Polling timeout reached — final admission check...");
      const finalCheck = await checkForAdmissionIndicators(page);
      if (finalCheck) {
        log("✅ Bot admitted after polling timeout (Leave button found)");
        return true;
      }

      log("No admission, rejection, or lobby indicators found after polling — bot failed to join");
      throw new Error("Bot failed to join the Teams meeting - no meeting indicators found after polling");
    }
    
    if (admitted) {
      log("Successfully admitted to the Teams meeting");
      return true;
    } else {
      throw new Error("Could not determine Teams admission status");
    }
    
  } catch (error: any) {
    throw new Error(
      `Bot was not admitted into the Teams meeting within the timeout period: ${error.message}`
    );
  }
}
