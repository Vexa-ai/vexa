import { Page } from 'playwright';
import { log } from '../../../utils';
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
    const clicked = await page.evaluate(() => {
      const btn = document.querySelector('[footer-section="right"] button[aria-label="Leave"]') as HTMLElement | null;
      if (btn) { btn.click(); return true; }
      return false;
    });
    if (clicked) {
      log('[Zoom Web] Clicked Leave button');

      // Small delay for the confirmation dialog to animate in before we query it.
      await page.waitForTimeout(500);

      // Wait for confirmation dialog then click "Leave Meeting" via native DOM click.
      // NOTE: Do NOT press Enter as a fallback — Enter dismisses/cancels the dialog.
      try {
        const confirmBtn = page.locator(zoomLeaveConfirmSelector).first();
        await confirmBtn.waitFor({ state: 'visible', timeout: 4000 });
        await page.evaluate(() => {
          const btn = document.querySelector('button.leave-meeting-options__btn--danger') as HTMLElement | null;
          if (btn) btn.click();
        });
        log('[Zoom Web] Confirmed leave');
        confirmed = true;
        await page.waitForTimeout(1500);
      } catch {
        log('[Zoom Web] Leave confirm dialog not found — navigating away to force WebRTC disconnect');
        await page.goto('about:blank').catch(() => {});
        await page.waitForTimeout(1000);
      }
    } else {
      log('[Zoom Web] Leave button not visible after footer reveal — forcing page navigation');
      await page.goto('about:blank').catch(() => {});
      await page.waitForTimeout(1000);
    }
  } catch (e: any) {
    log(`[Zoom Web] Error during leave: ${e.message}`);
  }

  // Stop recording after the UI leave so popupDismissInterval stays active until we're done
  try {
    await stopZoomWebRecording();
  } catch (e: any) {
    log(`[Zoom Web] Error stopping recording during leave: ${e.message}`);
  }

  return true;
}
