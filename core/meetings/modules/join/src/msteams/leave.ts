import { Page } from "playwright";
import { log, callLeaveCallback } from "../_host";
import { logJSON } from "../_host";
import { BotConfig } from "../_host";
import { teamsLeaveButtonMatchers, teamsPrimaryHangupButtonSelector } from "./selectors";
import { leaveBrowserClick } from "../shared/leave-click";
import { stopTeamsRecording } from "../_host";

// Prepare for recording by exposing necessary functions
export async function prepareForRecording(page: Page, botConfig: BotConfig): Promise<void> {
  // Expose the logBot function to the browser context
  await page.exposeFunction("logBot", (msg: string) => {
    log(msg);
  });

  // Expose bot config for callback functions
  await page.exposeFunction("getBotConfig", (): BotConfig => botConfig);

  // Node-side binding backing the browser-context leave hook: it drives the
  // shared canonical leaveBrowserClick through page.evaluate, so the hook needs
  // no in-page copy of the click logic and cannot drift from the direct path.
  await page.exposeFunction("__vexaTeamsLeaveClick", async (): Promise<boolean> => {
    try {
      return Boolean(await page.evaluate(leaveBrowserClick, teamsLeaveButtonMatchers));
    } catch (err: any) {
      log(`[performLeaveAction] browser leave click failed: ${err?.message}`);
      return false;
    }
  });

  // Ensure leave function is available even before admission. The leave
  // callback to the host is sent from the Node side (leaveMicrosoftTeams), not
  // from this hook.
  await page.evaluate(() => {
    if (typeof (window as any).performLeaveAction !== "function") {
      (window as any).performLeaveAction = async () => {
        try {
          (window as any).logBot?.("🔥 Leave requested from browser context — clicking the leave path...");
          return await (window as any).__vexaTeamsLeaveClick();
        } catch (err: any) {
          (window as any).logBot?.(`Error during Teams leave attempt: ${err?.message}`);
          return false;
        }
      };
    }
  });
}

// --- ADDED: Exported function to trigger leave from Node.js ---
export async function leaveMicrosoftTeams(page: Page | null, botConfig?: BotConfig, reason: string = "manual_leave"): Promise<boolean> {
  log("[leaveMicrosoftTeams] Triggering leave action in browser context...");
  if (!page || page.isClosed()) {
    log("[leaveMicrosoftTeams] Page is not available or closed.");
    return false;
  }

  // Recording is a HOST concern — give the embedder a chance to drain its
  // pipeline (final chunk, upload queue) before the UI leave tears the call down.
  try {
    log("[leaveMicrosoftTeams] Stopping recording pipeline before leave...");
    await stopTeamsRecording(page, botConfig);
  } catch (flushError: any) {
    logJSON({
      level: "error",
      msg: "[leaveMicrosoftTeams] Recording pipeline stop failed",
      error_message: flushError?.message,
      error_name: flushError?.name,
      error_stack: flushError?.stack,
      leave_reason: reason,
    });
  }

  // Call leave callback first to notify the host
  if (botConfig) {
    try {
      log("[leaveMicrosoftTeams] Calling leave callback before attempting to leave");
      await callLeaveCallback(botConfig, reason);
      log("[leaveMicrosoftTeams] Leave callback sent successfully");
    } catch (callbackError: any) {
      logJSON({
        level: "warn",
        msg: "[leaveMicrosoftTeams] Leave callback failed; continuing with leave attempt",
        error_message: callbackError?.message,
        error_name: callbackError?.name,
        leave_reason: reason,
      });
    }
  } else {
    logJSON({
      level: "warn",
      msg: "[leaveMicrosoftTeams] No bot config provided; cannot send leave callback",
    });
  }

  try {
    // First, try using Playwright's native click method for the most reliable selector
    log("[leaveMicrosoftTeams] Attempting to click leave button using Playwright's native click...");

    // Try the most reliable selector first: primary hangup button
    try {
      const hangupButton = page.locator(teamsPrimaryHangupButtonSelector);
      const isVisible = await hangupButton.isVisible({ timeout: 2000 }).catch(() => false);

      if (isVisible) {
        log(`[leaveMicrosoftTeams] Found ${teamsPrimaryHangupButtonSelector}, clicking with Playwright...`);
        await hangupButton.click({ timeout: 5000 });
        log(`[leaveMicrosoftTeams] Successfully clicked ${teamsPrimaryHangupButtonSelector} using Playwright`);

        // Wait for Teams to process the leave
        log("[leaveMicrosoftTeams] Waiting 3 seconds for Teams to process leave...");
        await new Promise(resolve => setTimeout(resolve, 3000));
        log("[leaveMicrosoftTeams] Wait complete. Teams should have processed the leave action.");
        return true;
      }
    } catch (hangupError: any) {
      log(`[leaveMicrosoftTeams] Could not click ${teamsPrimaryHangupButtonSelector} with Playwright: ${hangupError.message}`);
    }

    // Fallback to browser-side click method for other selectors
    log("[leaveMicrosoftTeams] Falling back to browser-side click method...");
    const result = await page.evaluate(async () => {
      if (typeof (window as any).performLeaveAction === "function") {
        return await (window as any).performLeaveAction();
      } else {
        (window as any).logBot?.("[Node Eval Error] performLeaveAction function not found on window.");
        console.error("[Node Eval Error] performLeaveAction function not found on window.");
        return false;
      }
    });
    log(`[leaveMicrosoftTeams] Browser leave action result: ${result}`);

    // Wait a bit after clicking leave to allow Teams to process the leave action
    if (result === true) {
      log("[leaveMicrosoftTeams] Leave button clicked successfully. Waiting 3 seconds for Teams to process leave...");
      await new Promise(resolve => setTimeout(resolve, 3000));
      log("[leaveMicrosoftTeams] Wait complete. Teams should have processed the leave action.");
    }

    // Ensure we return a boolean, not undefined
    return result === true;
  } catch (error: any) {
    log(`[leaveMicrosoftTeams] Error calling performLeaveAction in browser: ${error.message}`);
    return false;
  }
}
