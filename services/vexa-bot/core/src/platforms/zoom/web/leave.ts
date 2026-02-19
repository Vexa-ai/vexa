import { Page } from 'playwright';
import { log } from '../../../utils';
import { LeaveReason } from '../../shared/meetingFlow';
import { zoomLeaveButtonSelector, zoomLeaveConfirmSelector } from './selectors';
import { stopZoomWebRecording } from './recording';

export async function leaveZoomWebMeeting(
  page: Page | null,
  botConfig?: any,
  reason?: LeaveReason
): Promise<boolean> {
  log(`[Zoom Web] Leaving meeting (reason: ${reason || 'unspecified'})`);

  // Stop recording first
  try {
    await stopZoomWebRecording();
  } catch (e: any) {
    log(`[Zoom Web] Error stopping recording during leave: ${e.message}`);
  }

  if (!page || page.isClosed()) {
    log('[Zoom Web] Page not available for leave — skipping UI leave');
    return true;
  }

  try {
    // Click Leave button
    const leaveBtn = page.locator(zoomLeaveButtonSelector).first();
    const visible = await leaveBtn.isVisible({ timeout: 3000 });
    if (visible) {
      await leaveBtn.click();
      log('[Zoom Web] Clicked Leave button');
      await page.waitForTimeout(1000);

      // Confirm "Leave Meeting" in dialog if it appears
      try {
        const confirmBtn = page.locator(zoomLeaveConfirmSelector).first();
        if (await confirmBtn.isVisible({ timeout: 2000 })) {
          await confirmBtn.click();
          log('[Zoom Web] Confirmed leave');
        }
      } catch { /* no confirmation dialog */ }
    } else {
      log('[Zoom Web] Leave button not visible — meeting may have already ended');
    }
    return true;
  } catch (e: any) {
    log(`[Zoom Web] Error during leave: ${e.message}`);
    return false;
  }
}
