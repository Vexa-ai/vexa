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
  // All checks use timeout:0 — instant visibility check, no waiting.
  // This function is polled every 2s so there's no need to wait for elements to appear.
  const dismissTargets = [
    { selector: '.zm-modal button:has-text("OK")', label: 'AI Companion' },
    { selector: '.relative-tooltip button:has-text("Got it")', label: 'chatting as guest' },
    { selector: '.settings-feature-tips button:has-text("OK")', label: 'feature tip' },
    { selector: '.ReactModal__Content button:has-text("OK")', label: 'modal OK' },
    { selector: '.ReactModal__Content button:has-text("Got it")', label: 'modal Got it' },
    { selector: '[role="presentation"] button:has-text("OK")', label: 'presentation OK' },
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
