import { Page } from "playwright";
import { log, logJSON, callLeaveCallback, stopTelemostRecording } from "../_host";
import { BotConfig } from "../_host";
import { leaveBrowserClick, type BrowserContextButtonMatcher } from "../shared/leave-click";
import { callFrame } from "./admission";
import { telemostLeaveButtonSelectors, telemostLeaveConfirmTexts, telemostShellHangupSelectors } from "./selectors";

/** The leave control, strongest selector first. */
const leaveButtonMatchers: BrowserContextButtonMatcher[] =
  telemostLeaveButtonSelectors.map((css) => ({ css }));

/** The shell call bar's hang-up — the fallback when the call frame's toolbar is unreachable. */
const shellHangupMatchers: BrowserContextButtonMatcher[] =
  telemostShellHangupSelectors.map((css) => ({ css }));

/** The "leave or end for everyone" confirmation — the bot always picks plain leave. */
const leaveConfirmMatchers: BrowserContextButtonMatcher[] =
  telemostLeaveConfirmTexts.map((text) => ({ text }));

export async function leaveTelemostMeeting(
  page: Page | null,
  botConfig?: BotConfig,
  reason: string = "manual_leave",
): Promise<boolean> {
  log(`[Telemost] Leaving meeting (reason: ${reason})`);

  // Notify the host first so it records the leave intent even if the UI flow fails.
  if (botConfig) {
    try {
      await callLeaveCallback(botConfig, reason);
    } catch (callbackError: any) {
      logJSON({
        level: "warn",
        msg: "[Telemost] Leave callback failed; continuing with leave attempt",
        error_message: callbackError?.message,
        leave_reason: reason,
      });
    }
  }

  if (!page || page.isClosed()) {
    try { await stopTelemostRecording(page ?? undefined, botConfig); } catch { /* ignore */ }
    log("[Telemost] Page not available for leave — skipping UI leave");
    return true;
  }

  try {
    const frame = callFrame(page);
    const clicked = await frame.evaluate(leaveBrowserClick, leaveButtonMatchers).catch(() => false)
      || await page.mainFrame().evaluate(leaveBrowserClick, shellHangupMatchers).catch(() => false);
    if (clicked) {
      log("[Telemost] Clicked the leave control");
      await page.waitForTimeout(800);
      // A confirmation dialog may follow; answer it with plain leave. No dialog → no-op.
      const confirmed = await callFrame(page).evaluate(leaveBrowserClick, leaveConfirmMatchers).catch(() => false);
      if (confirmed) log("[Telemost] Confirmed leave in the dialog");
      await page.waitForTimeout(1500);
    } else {
      log("[Telemost] Leave control not found — forcing page navigation");
      // Forced navigation tears the WebRTC peer down at the page level.
      await page.goto("about:blank").catch(() => {});
      await page.waitForTimeout(1000);
    }
  } catch (e: any) {
    logJSON({
      level: "error",
      msg: "[Telemost] Error during leave",
      error_message: e?.message,
      error_name: e?.name,
      leave_reason: reason,
    });
  }

  // Recording is a HOST concern — give the embedder a chance to drain its pipeline.
  try {
    await stopTelemostRecording(page, botConfig);
  } catch (e: any) {
    logJSON({
      level: "error",
      msg: "[Telemost] Error stopping recording during leave",
      error_message: e?.message,
      error_name: e?.name,
      leave_reason: reason,
    });
  }

  return true;
}
