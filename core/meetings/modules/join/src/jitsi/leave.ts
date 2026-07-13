import { Page } from "playwright";
import { log, logJSON, callLeaveCallback, stopJitsiRecording } from "../_host";
import { BotConfig } from "../_host";
import { jitsiHangupButtonSelectors } from "./selectors";

export async function leaveJitsiMeeting(
  page: Page | null,
  botConfig?: BotConfig,
  reason: string = "manual_leave",
): Promise<boolean> {
  log(`[Jitsi] Leaving meeting (reason: ${reason})`);

  // Notify the host first so it records the leave intent even if the UI flow fails.
  if (botConfig) {
    try {
      await callLeaveCallback(botConfig, reason);
    } catch (callbackError: any) {
      logJSON({
        level: "warn",
        msg: "[Jitsi] Leave callback failed; continuing with leave attempt",
        error_message: callbackError?.message,
        leave_reason: reason,
      });
    }
  }

  if (!page || page.isClosed()) {
    // No UI to interact with — let the host drain its pipeline and bail.
    try { await stopJitsiRecording(page ?? undefined, botConfig); } catch { /* ignore */ }
    log("[Jitsi] Page not available for leave — skipping UI leave");
    return true;
  }

  try {
    // Preferred: the app's own hangup — tears the conference down exactly as the
    // red button does (XMPP presence out + WebRTC close), immune to toolbar DOM
    // drift and to the moderator hangup MENU (leave vs end-for-all) some builds show.
    const viaApp = await page.evaluate(async () => {
      try {
        const app = (globalThis as any).APP;
        if (app?.conference?.hangup) { await app.conference.hangup(); return true; }
        return false;
      } catch { return false; }
    }).catch(() => false);
    if (viaApp) {
      log("[Jitsi] Hung up via APP.conference.hangup()");
      await page.waitForTimeout(1500);
    } else {
      // Fallback: click the hangup control. If a moderator hangup menu opens
      // ("Leave meeting" / "End meeting for all"), pick plain leave.
      const clicked = await page.evaluate((selectors: string[]) => {
        for (const sel of selectors) {
          const btn = document.querySelector(sel) as HTMLElement | null;
          if (btn) { btn.click(); return sel; }
        }
        return null;
      }, jitsiHangupButtonSelectors).catch(() => null);
      if (clicked) {
        log(`[Jitsi] Clicked hangup (selector: ${clicked})`);
        await page.waitForTimeout(500);
        // Hangup-menu variant: a "Leave meeting" item appears after the click.
        await page.evaluate(() => {
          const items = Array.from(document.querySelectorAll('[role="menuitem"], button')) as HTMLElement[];
          const leave = items.find((el) => /^\s*leave\s*(meeting)?\s*$/i.test(el.textContent || ""));
          leave?.click();
        }).catch(() => { /* no menu — the click already hung up */ });
        await page.waitForTimeout(1500);
      } else {
        log("[Jitsi] Hangup selectors all missed — forcing page navigation");
        // Forced navigation tears the WebRTC peer down at the page level.
        await page.goto("about:blank").catch(() => {});
        await page.waitForTimeout(1000);
      }
    }
  } catch (e: any) {
    logJSON({
      level: "error",
      msg: "[Jitsi] Error during leave",
      error_message: e?.message,
      error_name: e?.name,
      leave_reason: reason,
    });
  }

  // Recording is a HOST concern — give the embedder a chance to drain its
  // pipeline (final chunk, upload queue) after the UI leave completes.
  try {
    await stopJitsiRecording(page, botConfig);
  } catch (e: any) {
    logJSON({
      level: "error",
      msg: "[Jitsi] Error stopping recording during leave",
      error_message: e?.message,
      error_name: e?.name,
      leave_reason: reason,
    });
  }

  return true;
}
