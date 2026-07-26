import { Page } from "playwright";
import { log, callAwaitingAdmissionCallback, callBlockedCallback } from "../_host";
import { BotConfig } from "../_host";
import { checkEscalation, triggerEscalation, getEscalationExtensionMs } from "../shared/escalation";
import {
  googleInitialAdmissionIndicators,
  googleWaitingRoomIndicators,
  googleRejectionIndicators,
  googleConsentPromptIndicators
} from "./selectors";

// AdmissionError + AdmissionOutcome moved to ../shared/admission so every platform (jitsi/zoom/
// teams) throws the SAME typed error the JoinDriver maps, without one platform depending on another.
import { AdmissionError } from "../shared/admission";

// A LIVE reCAPTCHA challenge is a VISIBLE, challenge-sized `iframe[src*="recaptcha"]` —
// the "I'm not a robot" anchor or the opened bframe. Frame-URL presence is NOT a
// challenge: Google Meet loads reCAPTCHA Enterprise INVISIBLY on every normal join (a
// background bot-scoring frame whose iframe is display:none), so a `/recaptcha/` frame
// exists on clean joins and on host-denial screens alike. A live challenge keeps the bot
// ON the page (instead of quitting) so it can be solved by a human over VNC or an agent
// over CDP — after which the normal admission poll proceeds into the meeting.
const CAPTCHA_MIN_WIDTH = 120;
const CAPTCHA_MIN_HEIGHT = 40;
export async function hasRecaptchaChallenge(page: Page): Promise<boolean> {
  try {
    const frames = page.frames().filter(f => (f.url() || "").includes("/recaptcha/"));
    if (frames.length === 0) return false;
    const iframes = page.locator('iframe[src*="recaptcha"]');
    const count = await iframes.count().catch(() => 0);
    for (let i = 0; i < count; i++) {
      const el = iframes.nth(i);
      if (!(await el.isVisible().catch(() => false))) continue;
      // Size guard: a scoring/telemetry stub can be a 1x1 or 0x0 visible node; a real
      // challenge widget is at least checkbox-sized. Unmeasurable box → keep the
      // conservative "stay for solve" reading (the stay is now bounded, see below).
      const box = typeof (el as any).boundingBox === "function"
        ? await (el as any).boundingBox().catch(() => null)
        : null;
      if (!box) return true;
      if (box.width >= CAPTCHA_MIN_WIDTH && box.height >= CAPTCHA_MIN_HEIGHT) return true;
    }
    return false;
  } catch {
    return false;
  }
}

// EXPLICIT host-denial copy. `googleRejectionIndicators` mixes two very different things:
// unambiguous "the host said no" text, and generic error affordances ("Try again",
// "Go back", "Access denied") that also render on Google's bot-block / invalid-state
// pages. Only the second kind may ever be re-read as bot-detection; an explicit denial is
// the host's answer and no captcha on the page can overturn it.
const EXPLICIT_HOST_DENIAL_COPY = [
  "denied your request",
  "request to join was denied",
  "you were denied",
  "weren't allowed to join",
  "weren’t allowed to join",
  "not allowed to join",
  "not admitted",
  "ask to join again",
];
export function isExplicitDenialIndicator(selector: string): boolean {
  const s = (selector || "").toLowerCase();
  return EXPLICIT_HOST_DENIAL_COPY.some(copy => s.includes(copy));
}

// Bound on the stay-for-solve path. Suppressing a rejection indicator because a captcha
// is on screen is a WAGER that a human/agent will solve it; the wager must expire, or the
// bot polls until the admission timeout and the meeting never reaches a terminal state.
export const CAPTCHA_SOLVE_GRACE_MS = 120_000;
// First moment we suppressed a rejection indicator for this page. Per-page (WeakMap) so a
// bot's pages never share the clock and nothing leaks after the page is gone.
const captchaSuppressionSince = new WeakMap<object, number>();

export type GoogleRejectionReason = "host_denial" | "error_page" | "captcha_unsolved";
export type GoogleRejectionVerdict = {
  rejected: boolean;
  reason: GoogleRejectionReason | null;
  selector: string | null;
};

/**
 * Classify the page into a rejection verdict. The discrimination rule, in order:
 *
 *  1. EXPLICIT host-denial copy visible → REJECTED (`host_denial`), whatever else is on
 *     the page. A reCAPTCHA element cannot overturn a host's "no" (#840).
 *  2. Ambiguous error affordance + LIVE captcha challenge → not rejected, stay for solve,
 *     but only within CAPTCHA_SOLVE_GRACE_MS of the first suppression.
 *  3. Grace expired → REJECTED (`captcha_unsolved`): terminal beats hanging.
 *  4. Ambiguous error affordance, no live challenge → REJECTED (`error_page`) — the
 *     pre-existing #444 conflation, unchanged by this fix.
 */
export async function classifyGoogleRejection(
  page: Page,
  now: number = Date.now(),
): Promise<GoogleRejectionVerdict> {
  const isVisible = async (selector: string): Promise<boolean> => {
    try {
      return await (await page.locator(selector).first()).isVisible();
    } catch {
      return false;
    }
  };
  try {
    // PASS 1 — explicit host denials, scanned BEFORE anything ambiguous so the verdict
    // does not depend on the order of googleRejectionIndicators.
    for (const selector of googleRejectionIndicators) {
      if (!isExplicitDenialIndicator(selector)) continue;
      if (!(await isVisible(selector))) continue;
      captchaSuppressionSince.delete(page as unknown as object);
      log(`🚨 Google Meet admission rejection detected: explicit host denial "${selector}" (wins over any reCAPTCHA element on the page)`);
      return { rejected: true, reason: "host_denial", selector };
    }

    // PASS 2 — ambiguous error affordances; these, and only these, may be re-read as
    // bot-detection while a live challenge is on screen.
    for (const selector of googleRejectionIndicators) {
      if (isExplicitDenialIndicator(selector)) continue;
      try {
        if (!(await isVisible(selector))) continue;

        if (await hasRecaptchaChallenge(page)) {
          const since = captchaSuppressionSince.get(page as unknown as object) ?? now;
          captchaSuppressionSince.set(page as unknown as object, since);
          const waited = now - since;
          if (waited < CAPTCHA_SOLVE_GRACE_MS) {
            log(`🤖 Live reCAPTCHA challenge alongside ambiguous indicator "${selector}" — treating as bot-detection, NOT admin rejection. Staying for manual/agent solve (${Math.round(waited / 1000)}s/${Math.round(CAPTCHA_SOLVE_GRACE_MS / 1000)}s).`);
            return { rejected: false, reason: null, selector };
          }
          captchaSuppressionSince.delete(page as unknown as object);
          log(`⏱️ reCAPTCHA stay-for-solve bound exceeded (${Math.round(waited / 1000)}s > ${Math.round(CAPTCHA_SOLVE_GRACE_MS / 1000)}s) with "${selector}" still on screen — concluding terminal instead of polling forever.`);
          return { rejected: true, reason: "captcha_unsolved", selector };
        }

        captchaSuppressionSince.delete(page as unknown as object);
        log(`🚨 Google Meet admission rejection detected: Found rejection indicator "${selector}"`);
        return { rejected: true, reason: "error_page", selector };
      } catch (e) {
        // Continue checking other selectors
        continue;
      }
    }
    captchaSuppressionSince.delete(page as unknown as object);
    return { rejected: false, reason: null, selector: null };
  } catch (error: any) {
    log(`Error checking for Google Meet rejection: ${error.message}`);
    return { rejected: false, reason: null, selector: null };
  }
}

// Function to check if bot has been rejected from the meeting
export async function checkForGoogleRejection(page: Page, now: number = Date.now()): Promise<boolean> {
  return (await classifyGoogleRejection(page, now)).rejected;
}

// Helper function to check for any visible and enabled admission indicators
/**
 * Diagnostic: dump the exact DOM truth the admission oracle keys on, so a live
 * run can be cross-checked against the host participant list (no false pos/neg).
 * Gated on DEBUG_ADMISSION so it never runs in production.
 */
/**
 * Count REAL participant tiles, excluding the self "Backgrounds and effects" panel.
 *
 * Google Meet gives the local effects/self-preview element a `data-participant-id`
 * too (its label leads with the "visual_effects" icon ligature / "Backgrounds and
 * effects"), so it is present in BOTH the lobby and the call. Raw `[data-participant-id]`
 * count is therefore >=1 even in the lobby (verified live 2026-06-12 vs host ground
 * truth) — making admission depend entirely on the waiting-room negative guard.
 * A REAL participant tile (self once in-call, or any remote) has a human-name label.
 * Presence of >=1 such tile is a POSITIVE admitted signal independent of that guard.
 */
const EFFECTS_TILE = /visual_effects|backgrounds and effects/i;
export async function countRealParticipantTiles(page: Page): Promise<number> {
  try {
    const labels = await page.locator("[data-participant-id]").evaluateAll(
      els => els.map(e => e.getAttribute("aria-label") || (e.textContent || "").trim()),
    );
    return labels.filter(l => l && !EFFECTS_TILE.test(l)).length;
  } catch {
    return 0;
  }
}

export async function dumpAdmissionState(page: Page, tag: string): Promise<void> {
  if (!process.env.DEBUG_ADMISSION) return;
  try {
    const url = page.url();
    const wr = await checkForWaitingRoomIndicators(page).catch(() => null);
    const pid = await page.locator("[data-participant-id]").evaluateAll(
      els => els.map(e => ({ id: e.getAttribute("data-participant-id"), label: e.getAttribute("aria-label") || (e.textContent || "").trim().slice(0, 30) })),
    ).catch(() => []);
    const self = await page.locator("[data-self-name]").evaluateAll(
      els => els.map(e => e.getAttribute("data-self-name") || ""),
    ).catch(() => []);
    const recaptchaFrames = page.frames().filter(f => (f.url() || "").includes("/recaptcha/")).length;
    const realTiles = await countRealParticipantTiles(page);
    log(`🔎 [ADMIT-DUMP ${tag}] url=${url} waitingRoom=${wr} realTiles=${realTiles} participantTiles=${pid.length} ${JSON.stringify(pid)} selfName=${self.length}${self.length ? " " + JSON.stringify(self) : ""} recaptchaFrames=${recaptchaFrames}`);
  } catch (e: any) {
    log(`🔎 [ADMIT-DUMP ${tag}] dump error: ${e?.message}`);
  }
}

export async function checkForGoogleAdmissionIndicators(page: Page): Promise<boolean> {
  await dumpAdmissionState(page, "check");
  // 1. NEGATIVE GUARD: If any waiting room indicator is visible,
  // the bot is NOT admitted — lobby toolbar buttons are false positives.
  const inWaitingRoom = await checkForWaitingRoomIndicators(page);
  if (inWaitingRoom) {
    log(`⚠️ Waiting room indicator visible — suppressing admission (lobby buttons are false positives)`);
    return false;
  }

  // 1b. NEGATIVE GUARD: a Gemini "take notes" consent prompt is a pre-admission
  // consent gate. Meeting controls can be visible behind it, but the bot is not
  // truly participating until a human accepts/declines — reporting admitted here
  // yields "status active, 0 transcriptions" (Vexa-ai/vexa#429). Suppress admission.
  const consentPending = await hasConsentPrompt(page);
  if (consentPending) {
    log(`⚠️ Gemini consent prompt visible — suppressing admission (consent pending; bot not truly in the call)`);
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
        // Real participant present (effects-panel phantom excluded) = positive admitted
        // signal, not just "a tile exists". See countRealParticipantTiles.
        if (selector === '[data-participant-id]') {
          const real = await countRealParticipantTiles(page);
          if (real > 0) {
            log(`✅ Admitted: ${real} real participant tile(s) present (effects phantom excluded)`);
            return true;
          }
          continue;
        }
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

// Detect Google's Gemini "take notes for me" in-call consent prompt — a consent
// gate where the bot isn't truly participating until a human accepts/declines
// (Vexa-ai/vexa#429). Mirrors checkForWaitingRoomIndicators: a pre-admission
// state that suppresses the "admitted" signal. Consent must be a human decision,
// so callers escalate to needs_human_help rather than auto-clicking it.
export async function hasConsentPrompt(page: Page): Promise<boolean> {
  for (const selector of googleConsentPromptIndicators) {
    try {
      const element = await page.locator(selector).first();
      if (await element.isVisible()) {
        return true;
      }
    } catch {
      continue;
    }
  }
  return false;
}

// Terminal admission verdicts carry an honest reason: a host denial says so, and an
// unsolved captcha says THAT — both map to AdmissionError("denial") (PERMANENT, no
// re-knock), which the JoinDriver records as `awaiting_admission_rejected`.
const REJECTION_MESSAGE: Record<GoogleRejectionReason, string> = {
  host_denial: "Bot admission was rejected by meeting admin",
  error_page: "Bot admission was rejected by meeting admin",
  captcha_unsolved: `Bot faced a reCAPTCHA challenge that went unsolved for ${Math.round(CAPTCHA_SOLVE_GRACE_MS / 1000)}s alongside a rejection screen`,
};

async function throwIfGoogleAdmissionRejected(page: Page, context: string): Promise<void> {
  const verdict = await classifyGoogleRejection(page);
  if (verdict.rejected) {
    log(`🚨 Bot admission concluded terminal for the Google Meet meeting (${verdict.reason}, ${context})`);
    throw new AdmissionError("denial", REJECTION_MESSAGE[verdict.reason!]);
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

    // Consent gate: if Google's Gemini "take notes" consent prompt is present,
    // the bot is held behind a human decision (accept/decline) — not admitted.
    // Do NOT auto-click it; consent is the user's choice (Vexa-ai/vexa#429).
    // Summon a human via needs_human_help and keep polling, so admission
    // proceeds once consent is granted (mirrors the reCAPTCHA "stay for human
    // solve" handling).
    if (await hasConsentPrompt(page)) {
      log("🧑‍⚖️ Gemini consent prompt detected — bot is behind a consent gate (not admitted). Escalating to needs_human_help; not auto-consenting.");
      await triggerEscalation(botConfig, "consent_required");
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
        throw new AdmissionError("lobby_timeout", "Bot is still in the Google Meet waiting room after timeout - not admitted to the meeting");
      }
    } else {
      // Not in waiting room and not admitted yet: actively poll during the timeout
      log(`No waiting room detected. Polling for admission for up to ${timeout}ms...`);
      const checkInterval = 2000;
      const startTime = Date.now();
      let unknownStateDuration2 = 0;
      const effectiveTimeout2 = () => timeout + getEscalationExtensionMs();
      while (Date.now() - startTime < effectiveTimeout2()) {
        // #444 — the `blocked` state is wired (callBlockedCallback), but NOT emitted yet.
        // hasRecaptchaChallenge() detects a VISIBLE, challenge-sized widget, which is the
        // narrow half of the signal; the blank block page half needs a run that actually
        // reproduces a block (datacenter egress arm) before it can drive `blocked` without
        // false positives.

        // Rejection check first
        await throwIfGoogleAdmissionRejected(page, "polling mode");

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
    await throwIfGoogleAdmissionRejected(page, "final check");

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
