import { Page } from 'playwright';
import { BotConfig } from '../../../types';
import { log } from '../../../utils';
import { zoomAudioButtonSelector, zoomChatButtonSelector } from './selectors';

/**
 * Post-admission setup: join computer audio, dismiss any popups.
 */
export async function prepareZoomWebMeeting(page: Page | null, botConfig: BotConfig): Promise<void> {
  if (!page) throw new Error('[Zoom Web] Page required for prepare');

  log('[Zoom Web] Preparing meeting post-admission...');

  // Dismiss any stale "Floating reactions" or other tooltip popups
  try {
    const okBtn = page.locator('button:has-text("OK")').first();
    if (await okBtn.isVisible({ timeout: 1500 })) {
      await okBtn.click();
      log('[Zoom Web] Dismissed OK popup');
      await page.waitForTimeout(500);
    }
  } catch { /* no popup */ }

  // Join computer audio if the audio button shows "Join Audio" state
  try {
    const audioBtn = page.locator(zoomAudioButtonSelector).first();
    const ariaLabel = await audioBtn.getAttribute('aria-label');
    if (ariaLabel && ariaLabel.toLowerCase().includes('join audio')) {
      await audioBtn.click();
      log('[Zoom Web] Clicked Join Audio');
      await page.waitForTimeout(1000);

      // Click "Join with Computer Audio" if dialog appears
      try {
        const computerAudioBtn = page.locator('button:has-text("Join with Computer Audio")').first();
        if (await computerAudioBtn.isVisible({ timeout: 3000 })) {
          await computerAudioBtn.click();
          log('[Zoom Web] Joined with Computer Audio');
        }
      } catch { /* already joined */ }
    }
  } catch (e: any) {
    log(`[Zoom Web] Audio join step skipped: ${e.message}`);
  }

  // Dismiss the "Please enable microphone/camera" notification banner if present
  try {
    const closeNotif = page.locator('button[aria-label="Close notification"], .notification-close, button:has-text("×")').first();
    if (await closeNotif.isVisible({ timeout: 1000 })) {
      await closeNotif.click();
    }
  } catch { /* no banner */ }

  log('[Zoom Web] Meeting preparation complete');
}
