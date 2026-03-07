import { Page } from 'playwright';
import { BotConfig } from '../../../types';
import { AdmissionDecision } from '../../shared/meetingFlow';
import { log, callAwaitingAdmissionCallback } from '../../../utils';
import {
  zoomLeaveButtonSelector,
  zoomMeetingAppSelector,
  zoomWaitingRoomTexts,
  zoomRemovalTexts,
} from './selectors';

/**
 * Check if the bot is confirmed inside the meeting (Leave button visible).
 */
async function isAdmitted(page: Page): Promise<boolean> {
  try {
    const leaveBtn = page.locator(zoomLeaveButtonSelector).first();
    return await leaveBtn.isVisible({ timeout: 500 });
  } catch {
    return false;
  }
}

/**
 * Check if the bot is currently in the waiting room.
 * Zoom waiting room shows specific text strings — no unique CSS class.
 */
async function isInWaitingRoom(page: Page): Promise<boolean> {
  try {
    for (const text of zoomWaitingRoomTexts) {
      const el = page.locator(`text=${text}`).first();
      const visible = await el.isVisible({ timeout: 300 }).catch(() => false);
      if (visible) return true;
    }
    // Also check via JS text scan (more reliable for partial matches)
    return await page.evaluate((texts: string[]) => {
      const bodyText = document.body.innerText || '';
      return texts.some(t => bodyText.includes(t));
    }, zoomWaitingRoomTexts);
  } catch {
    return false;
  }
}

/**
 * Check if the bot was rejected / meeting ended.
 */
async function isRejectedOrEnded(page: Page): Promise<boolean> {
  try {
    return await page.evaluate((texts: string[]) => {
      const bodyText = document.body.innerText || '';
      return texts.some(t => bodyText.includes(t));
    }, zoomRemovalTexts);
  } catch {
    return false;
  }
}

export async function waitForZoomWebAdmission(
  page: Page | null,
  timeoutMs: number,
  botConfig: BotConfig
): Promise<boolean | AdmissionDecision> {
  if (!page) throw new Error('[Zoom Web] Page required for admission check');

  log('[Zoom Web] Checking admission state...');

  // Fast path: already admitted (host was present and let us in immediately)
  if (await isAdmitted(page)) {
    log('[Zoom Web] Bot immediately admitted — Leave button visible');
    return true;
  }

  // Check if in waiting room
  const inWaiting = await isInWaitingRoom(page);
  if (inWaiting) {
    log('[Zoom Web] Bot is in waiting room — waiting for host admission');
    try {
      await callAwaitingAdmissionCallback(botConfig);
    } catch (e: any) {
      log(`[Zoom Web] Warning: awaiting_admission callback failed: ${e.message}`);
    }
  }

  // Poll loop
  const startTime = Date.now();
  const pollInterval = 2000;

  while (Date.now() - startTime < timeoutMs) {
    await page.waitForTimeout(pollInterval);

    if (await isRejectedOrEnded(page)) {
      log('[Zoom Web] Bot was rejected or meeting ended during admission wait');
      throw new Error('Bot was rejected from the Zoom meeting or meeting ended');
    }

    if (await isAdmitted(page)) {
      log('[Zoom Web] Bot admitted — Leave button now visible');
      return true;
    }

    const elapsed = Math.round((Date.now() - startTime) / 1000);
    log(`[Zoom Web] Still waiting for admission... ${elapsed}s elapsed`);
  }

  throw new Error(`[Zoom Web] Bot not admitted within ${timeoutMs}ms timeout`);
}

export async function checkZoomWebAdmissionSilent(page: Page | null): Promise<boolean> {
  if (!page) return false;
  return isAdmitted(page);
}
