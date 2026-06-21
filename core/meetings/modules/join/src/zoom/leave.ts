import { Page } from "playwright";
import { log, logJSON, callLeaveCallback, stopZoomRecording } from "../_host";
import { BotConfig } from "../_host";
import { zoomLeaveConfirmSelector } from "./selectors";

/**
 * Dismiss known Zoom Web popups/modals that overlay meeting content.
 * Safe to call repeatedly — each check is short-circuited if the popup isn't visible.
 */
export async function dismissZoomPopups(page: Page): Promise<void> {
  // All checks use timeout:0 — instant visibility check, no waiting.
  // This function may be polled so there's no need to wait for elements to appear.
  const dismissTargets = [
    { selector: '.zm-modal button:has-text("OK")', label: 'AI Companion' },
    { selector: '.relative-tooltip button:has-text("Got it")', label: 'chatting as guest' },
    { selector: '.settings-feature-tips button:has-text("OK")', label: 'feature tip' },
    { selector: '.ReactModal__Content button:has-text("OK")', label: 'modal OK' },
    { selector: '.ReactModal__Content button:has-text("Got it")', label: 'modal Got it' },
    { selector: '[role="presentation"] button:has-text("OK")', label: 'presentation OK' },
    // Zoom advisory modal: "Your mic is muted in system or browser settings."
    // Doesn't block joining but spams logs and remains on screen
    // until manually dismissed. Click any of OK / Dismiss / Got it / Continue.
    { selector: '.zm-modal:has-text("mic is muted") button:has-text("OK"), .zm-modal:has-text("mic is muted") button:has-text("Got it"), .zm-modal:has-text("mic is muted") button:has-text("Dismiss"), .zm-modal:has-text("mic is muted") button:has-text("Continue")', label: 'mic-muted advisory' },
  ];

  for (const { selector, label } of dismissTargets) {
    try {
      const btn = page.locator(selector).first();
      if (await btn.isVisible({ timeout: 0 })) {
        await btn.click();
        log(`[Zoom Web] Dismissed "${label}" popup`);
      }
    } catch { /* not present or already gone */ }
  }
}

export async function leaveZoomMeeting(
  page: Page | null,
  botConfig?: BotConfig,
  reason: string = "manual_leave"
): Promise<boolean> {
  log(`[Zoom Web] Leaving meeting (reason: ${reason})`);

  // Notify the host first so it can record the leave intent even if the UI flow fails.
  if (botConfig) {
    try {
      await callLeaveCallback(botConfig, reason);
    } catch (callbackError: any) {
      logJSON({
        level: "warn",
        msg: "[Zoom Web] Leave callback failed; continuing with leave attempt",
        error_message: callbackError?.message,
        leave_reason: reason,
      });
    }
  }

  if (!page || page.isClosed()) {
    // No UI to interact with — let the host drain its pipeline and bail
    try { await stopZoomRecording(page ?? undefined, botConfig); } catch { /* ignore */ }
    log('[Zoom Web] Page not available for leave — skipping UI leave');
    return true;
  }

  let confirmed = false;
  try {
    // Dismiss any popups (AI Companion, feedback prompts, etc.) that could block the leave dialog
    await dismissZoomPopups(page).catch(() => {});

    // Click Leave button via native DOM click — Playwright's synthetic events don't
    // always trigger Zoom's React handlers reliably.
    //
    // v0.10.5 — Multi-selector fallback. Previous selector
    // `[footer-section="right"] button[aria-label="Leave"]` is DOM-structure-fragile;
    // when N bots target the same Zoom meeting, ~2/N hit a transient DOM state
    // and the strict selector fails → click never fires → bot exits with WebRTC
    // session still active → ORPHAN bot stays visible in meeting from Zoom's
    // perspective until WebRTC keepalive timeout (30-60s).
    const clicked = await page.evaluate(() => {
      // Try each selector in priority order — first match wins
      const selectors = [
        '[footer-section="right"] button[aria-label="Leave"]',
        'button[aria-label="Leave"]',
        'button[aria-label*="Leave"]',
      ];
      for (const sel of selectors) {
        const btn = document.querySelector(sel) as HTMLElement | null;
        if (btn) { btn.click(); return sel; }
      }
      return null;
    });
    if (clicked) {
      log(`[Zoom Web] Clicked Leave button (selector: ${clicked})`);

      // Small delay for the confirmation dialog to animate in before we query it.
      await page.waitForTimeout(500);

      // Wait for confirmation dialog then click "Leave Meeting" via native DOM click.
      // NOTE: Do NOT press Enter as a fallback — Enter dismisses/cancels the dialog.
      try {
        const confirmBtn = page.locator(zoomLeaveConfirmSelector).first();
        await confirmBtn.waitFor({ state: 'visible', timeout: 4000 });
        // v0.10.5 — return whether the confirm click actually fired so we can
        // verify rather than assume. Pre-fix this was fire-and-forget; if the
        // selector missed, leave silently failed and bot orphaned.
        const confirmClicked = await page.evaluate(() => {
          const selectors = [
            'button.leave-meeting-options__btn--danger',
            'button.leave-meeting-options__btn',
            'button.zm-btn--danger[aria-label*="Leave"]',
          ];
          for (const sel of selectors) {
            const btn = document.querySelector(sel) as HTMLElement | null;
            if (btn) { btn.click(); return sel; }
          }
          return null;
        });
        if (confirmClicked) {
          log(`[Zoom Web] Confirmed leave (selector: ${confirmClicked})`);
          confirmed = true;
          // Hold the page open long enough for the WebRTC peer to actually
          // disconnect — pre-fix the 1.5s wait was sometimes insufficient.
          await page.waitForTimeout(2500);
        } else {
          log('[Zoom Web] Confirm-Leave button selectors all missed — falling back to navigation');
          await page.goto('about:blank').catch(() => {});
          await page.waitForTimeout(1000);
        }
      } catch {
        log('[Zoom Web] Leave confirm dialog not found — navigating away to force WebRTC disconnect');
        await page.goto('about:blank').catch(() => {});
        await page.waitForTimeout(1000);
      }
    } else {
      log('[Zoom Web] Leave button selectors all missed — forcing page navigation');
      // Forced navigation tears the WebRTC peer down at the page level —
      // belt-and-suspenders for selector-failure case.
      await page.goto('about:blank').catch(() => {});
      await page.waitForTimeout(1000);
    }
  } catch (e: any) {
    logJSON({
      level: "error",
      msg: "[Zoom Web] Error during leave",
      error_message: e?.message,
      error_name: e?.name,
      leave_reason: reason,
      confirmed,
    });
  }

  // Recording is a HOST concern — give the embedder a chance to drain its
  // pipeline (final chunk, upload queue) after the UI leave completes.
  try {
    await stopZoomRecording(page, botConfig);
  } catch (e: any) {
    logJSON({
      level: "error",
      msg: "[Zoom Web] Error stopping recording during leave",
      error_message: e?.message,
      error_name: e?.name,
      leave_reason: reason,
    });
  }

  return true;
}
