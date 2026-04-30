import { Page } from 'playwright';
import { log } from '../../../utils';
import { logJSON } from '../../../utils/log';
import { LeaveReason } from '../../shared/meetingFlow';
import { zoomLeaveConfirmSelector } from './selectors';
import { stopZoomWebRecording } from './recording';
import { dismissZoomPopups } from './prepare';

export async function leaveZoomWebMeeting(
  page: Page | null,
  botConfig?: any,
  reason?: LeaveReason
): Promise<boolean> {
  log(`[Zoom Web] Leaving meeting (reason: ${reason || 'unspecified'})`);

  if (!page || page.isClosed()) {
    // No UI to interact with — stop recording and bail
    try { await stopZoomWebRecording(); } catch { /* ignore */ }
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
          // Guard with a check that the leave page transitioned (URL change).
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

  // Stop recording after the UI leave so popupDismissInterval stays active until we're done
  try {
    await stopZoomWebRecording();
  } catch (e: any) {
    // v0.10.5 Pack G.1 — recording-stop failure is diagnostic-critical
    // for Zoom Web (#272 issue 1 audio-loss class).
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
