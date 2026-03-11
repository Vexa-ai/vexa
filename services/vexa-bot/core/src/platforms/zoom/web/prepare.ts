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

  // Dismiss popups that overlay the meeting content
  await dismissZoomPopups(page);

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

/**
 * Dismiss known Zoom Web popups/modals that overlay meeting content.
 * Safe to call repeatedly — each check is short-circuited if the popup isn't visible.
 */
export async function dismissZoomPopups(page: Page): Promise<void> {
  // 1. "AI Companion is on." modal — has an OK button inside .zm-modal
  try {
    const aiModal = page.locator('.zm-modal button:has-text("OK")').first();
    if (await aiModal.isVisible({ timeout: 800 })) {
      await aiModal.click();
      log('[Zoom Web] Dismissed "AI Companion" popup');
      await page.waitForTimeout(300);
    }
  } catch { /* not present */ }

  // 2. "You're chatting as a guest" tooltip — has a "Got it" button
  try {
    const gotItBtn = page.locator('.relative-tooltip button:has-text("Got it")').first();
    if (await gotItBtn.isVisible({ timeout: 800 })) {
      await gotItBtn.click();
      log('[Zoom Web] Dismissed "chatting as guest" popup');
      await page.waitForTimeout(300);
    }
  } catch { /* not present */ }

  // 3. Generic OK/Got it buttons (catch-all for other Zoom modals)
  try {
    const genericOk = page.locator('.ReactModal__Content button:has-text("OK"), .ReactModal__Content button:has-text("Got it")').first();
    if (await genericOk.isVisible({ timeout: 500 })) {
      await genericOk.click();
      log('[Zoom Web] Dismissed generic modal popup');
    }
  } catch { /* not present */ }
}
