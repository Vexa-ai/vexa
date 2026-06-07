import { Page } from "playwright";
import { log, callAwaitingAdmissionCallback } from "../../utils";
import { BotConfig } from "../../types";
import { checkEscalation, triggerEscalation, getEscalationExtensionMs } from "../shared/escalation";
import {
  googleInitialAdmissionIndicators,
  googleWaitingRoomIndicators,
  googleRejectionIndicators
} from "./selectors";

/**
 * Distinct admission outcomes emitted by the detector.
 * denial        — host explicitly rejected the bot from the waiting room.
 * lobby_timeout — bot stayed in the waiting room past the admission timeout.
 * join_failure  — bot never reached the lobby / no admission indicators appeared.
 */
export type AdmissionOutcome = "denial" | "lobby_timeout" | "join_failure";

export class AdmissionError extends Error {
  readonly outcome: AdmissionOutcome;
  constructor(outcome: AdmissionOutcome, message: string) {
    super(message);
    this.name = "AdmissionError";
    this.outcome = outcome;
  }
}

// Detect an active reCAPTCHA (enterprise) challenge. Google renders it in iframes whose
// URL contains "/recaptcha/"; it can sit on the same screen as a "Return to home screen"
// button, which otherwise reads exactly like an admin rejection. Used to keep the bot ON
// the page (instead of quitting) so the challenge can be solved by a human over VNC or an
// agent over CDP — after which the normal admission poll proceeds into the meeting.
export async function hasRecaptchaChallenge(page: Page): Promise<boolean> {
  try {
    for (const frame of page.frames()) {
      if ((frame.url() || "").includes("/recaptcha/")) return true;
    }
    const iframe = page.locator('iframe[src*="recaptcha"]').first();
    return await iframe.isVisible().catch(() => false);
  } catch {
    return false;
  }
}

// Function to check if bot has been rejected from the meeting
export async function checkForGoogleRejection(page: Page): Promise<boolean> {
  try {
    // Check for rejection indicators
    for (const selector of googleRejectionIndicators) {
      try {
        const element = await page.locator(selector).first();
        if (await element.isVisible()) {
          // A reCAPTCHA challenge renders the SAME "Return to home screen" affordance as
          // an admin rejection. If a captcha is on screen, this is Google bot-detection,
          // NOT a host denial — classifying it as a rejection makes the bot quit before
          // the captcha can be solved. Stay instead; the admission poll keeps running so a
          // solve (human via VNC / agent via CDP) leads straight into admission.
          if (await hasRecaptchaChallenge(page)) {
            log(`🤖 reCAPTCHA present alongside rejection indicator "${selector}" — treating as bot-detection, NOT admin rejection. Staying for manual/agent solve.`);
            return false;
          }
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
export async function checkForGoogleAdmissionIndicators(page: Page): Promise<boolean> {
  // 1. NEGATIVE GUARD: If any waiting room indicator is visible,
  // the bot is NOT admitted — lobby toolbar buttons are false positives.
  const inWaitingRoom = await checkForWaitingRoomIndicators(page);
  if (inWaitingRoom) {
    log(`⚠️ Waiting room indicator visible — suppressing admission (lobby buttons are false positives)`);
    return false;
  }

  // Wake the UI before probing. Google Meet auto-hides the in-call toolbar
  // (mic/camera/present/leave) after a few seconds of no pointer activity — and the
  // bot never moves a real mouse. Once admitted (especially when a participant is
  // *presenting*, which restyles the chrome), every toolbar selector reads
  // isVisible:false, so the bot wrongly concludes "not admitted", keeps polling, and
  // false-escalates to unknown_blocking_state / needs_human_help while actually sitting
  // in the call (observed live: meeting in-progress, "X (Presenting)" visible, 0
  // transcripts). A synthetic pointer move re-reveals the toolbar so isVisible() is
  // meaningful again. Best-effort; ignore failures.
  try {
    await page.mouse.move(640, 360);
    await page.mouse.move(960, 540);
  } catch { /* headless/no-input edge — fall through to presence checks */ }

  // 2. DOM SELECTORS: participant tiles, self-name, share/present buttons.
  // NOTE: MediaStream-based detection was tested but Google Meet's lobby has
  // active media elements (self-preview audio tracks), causing false positives.
  // Filtering self vs. remote streams is needed — tracked as follow-up.
  //
  // Structural selectors ([data-participant-id], [data-self-name]) do NOT exist in the
  // lobby (see selectors.ts) and do NOT auto-hide — so DOM PRESENCE (count>0), not
  // visibility, is the reliable admitted signal. The waiting-room negative guard above
  // already rules out the lobby, so presence here means we're in the call. Toolbar
  // buttons remain visibility-gated (they legitimately exist disabled in some states).
  const presenceSelectors = new Set(['[data-participant-id]', '[data-self-name]']);
  for (const selector of googleInitialAdmissionIndicators) {
    try {
      if (presenceSelectors.has(selector)) {
        const count = await page.locator(selector).count();
        if (count > 0) {
          log(`✅ Found Google Meet admission indicator (DOM presence, auto-hide-proof): ${selector}`);
          return true;
        }
        continue;
      }
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
}

// Silent admission check (doesn't send callbacks) - used for verification
export async function checkForGoogleAdmissionSilent(page: Page): Promise<boolean> {
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

async function throwIfGoogleAdmissionRejected(page: Page, context: string): Promise<void> {
  const isRejected = await checkForGoogleRejection(page);
  if (isRejected) {
    log(`🚨 Bot was rejected from the Google Meet meeting by admin (${context})`);
    throw new AdmissionError("denial", "Bot admission was rejected by meeting admin");
  }
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
    // If meeting controls are visible, the bot is admitted — lobby indicators are unreliable
    const initialAdmissionFound = await checkForGoogleAdmissionIndicators(page);

    if (initialAdmissionFound) {
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
      log(`Bot is in Google Meet waiting room. Waiting for ${timeout}ms for admission...`);

      const checkInterval = 2000; // Check every 2 seconds for faster detection
      const startTime = Date.now();
      let unknownStateDuration = 0;
      const effectiveTimeout = () => timeout + getEscalationExtensionMs();

      while (Date.now() - startTime < effectiveTimeout()) {
        // Host denial can leave stale waiting-room text in the DOM. Check the
        // terminal rejection state before treating the page as still waiting.
        await throwIfGoogleAdmissionRejected(page, "waiting-room polling");

        // Check if we're still in waiting room using visibility
        const stillWaiting = await checkForWaitingRoomIndicators(page);

        if (!stillWaiting) {
          log("Google Meet waiting room indicator disappeared - checking if bot was admitted or rejected...");
          unknownStateDuration += checkInterval;

          // Check for admission indicators since waiting room disappeared and no rejection found
          const admissionFound = await checkForGoogleAdmissionIndicators(page);

          if (admissionFound) {
            log(`✅ Bot was admitted to the Google Meet meeting: meeting controls confirmed`);
            return true;
          }

          // Keep waiting if neither admitted nor rejected
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
      let unknownStateDuration2 = 0;
      const effectiveTimeout2 = () => timeout + getEscalationExtensionMs();
      while (Date.now() - startTime < effectiveTimeout2()) {
        // Rejection check first
        const isRejected = await checkForGoogleRejection(page);
        if (isRejected) {
          log("🚨 Bot was rejected from the Google Meet meeting by admin (polling mode)");
          throw new AdmissionError("denial", "Bot admission was rejected by meeting admin");
        }

        // Admission indicators — if meeting controls are visible, bot is admitted
        // regardless of any residual lobby-like elements in the DOM
        const admissionFound = await checkForGoogleAdmissionIndicators(page);
        if (admissionFound) {
          log("✅ Bot admitted during polling window (meeting controls visible)");
          return true;
        }

        // If lobby appears later, switch to waiting-room handling by breaking
        const lobbyVisible = await checkForWaitingRoomIndicators(page);
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
          unknownStateDuration2 = 0;
          break;
        }

        // Track unknown state for escalation
        unknownStateDuration2 += checkInterval;
        const elapsedMs = Date.now() - startTime;
        const escalation = checkEscalation(elapsedMs, timeout, unknownStateDuration2);
        if (escalation) {
          await triggerEscalation(botConfig, escalation.reason);
        }

        await page.waitForTimeout(checkInterval);
        log(`Polling for Google Meet admission... ${Math.round((Date.now() - startTime) / 1000)}s elapsed`);
      }

      if (stillInWaitingRoom) {
        // Re-run the waiting room loop with the remaining time
        const checkInterval = 2000;
        const startTime2 = Date.now();
        while (Date.now() - startTime2 < timeout) {
          await throwIfGoogleAdmissionRejected(page, "late waiting-room polling");

          const stillWaiting = await checkForWaitingRoomIndicators(page);
          if (!stillWaiting) {
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
      throw new AdmissionError("denial", "Bot admission was rejected by meeting admin");
    }

    // Distinguish lobby-timeout from join-failure by checking waiting-room state
    const lobbyStillVisible = await checkForWaitingRoomIndicators(page);
    await page.screenshot({ path: '/app/storage/screenshots/bot-checkpoint-3-no-indicators.png', fullPage: true });
    log("📸 Screenshot taken: No meeting indicators found after timeout");
    if (lobbyStillVisible) {
      throw new AdmissionError("lobby_timeout", "Bot is still in the Google Meet waiting room after timeout — host did not admit");
    }
    throw new AdmissionError("join_failure", "Bot failed to join the Google Meet meeting — no meeting indicators found within timeout");

  } catch (error: any) {
    // Re-throw AdmissionError instances unchanged so callers can inspect outcome.
    if (error instanceof AdmissionError) throw error;
    throw new AdmissionError("join_failure",
      `Bot was not admitted into the Google Meet meeting within the timeout period: ${error.message}`
    );
  }
}
