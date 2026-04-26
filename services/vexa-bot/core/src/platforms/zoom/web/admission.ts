import { Page } from 'playwright';
import { BotConfig } from '../../../types';
import { AdmissionDecision } from '../../shared/meetingFlow';
import { log, callAwaitingAdmissionCallback } from '../../../utils';
import { checkEscalation, triggerEscalation, getEscalationExtensionMs } from '../../shared/escalation';
import {
  zoomLeaveButtonSelector,
  zoomMeetingAppSelector,
  zoomWaitingRoomTexts,
  zoomRemovalTexts,
} from './selectors';

/**
 * Check if the bot is confirmed inside the meeting.
 * Primary:   Leave button visible (footer is showing).
 * Fallback1: .meeting-app container present (footer may be auto-hidden).
 * Fallback2: live <audio> elements AND no pre-join-page indicators —
 *            Zoom Web preloads audio streams on the pre-join page itself
 *            (local mic preview), so audio presence alone is NOT enough.
 *            Require the pre-join name input AND join button to be absent.
 *            (Observed 2026-04-26 meeting_id=31: bot was at
 *            "Enter Meeting Info"/passcode-entry screen with 3 live audio
 *            elements; an earlier audio-only fallback falsely reported
 *            admitted, status=active appeared on the dashboard while the
 *            bot was actually still pre-join.)
 */
async function isAdmitted(page: Page): Promise<boolean> {
  try {
    const leaveBtn = page.locator(zoomLeaveButtonSelector).first();
    if (await leaveBtn.isVisible({ timeout: 500 })) return true;

    // Footer may be auto-hidden — check for the meeting app container
    const meetingApp = page.locator(zoomMeetingAppSelector).first();
    if (await meetingApp.isVisible({ timeout: 500 })) return true;

    // Audio-routing fallback: live <audio> elements AND no pre-join
    // indicators (name input gone, join-button gone). The combined check
    // distinguishes "in meeting, audio routing" from "pre-join page with
    // mic preview audio".
    const state = await page.evaluate(() => {
      const liveAudioCount = Array.from(document.querySelectorAll('audio'))
        .filter((el: any) =>
          !el.paused &&
          el.srcObject instanceof MediaStream &&
          el.srcObject.getAudioTracks().length > 0 &&
          el.srcObject.getAudioTracks()[0].readyState === 'live')
        .length;
      const preJoinPresent = !!(
        document.querySelector('#input-for-name') ||
        document.querySelector('button.preview-join-button') ||
        document.querySelector('input[placeholder*="passcode" i], input[placeholder*="password" i]')
      );
      const bodyText = (document.body?.innerText || '').toLowerCase();
      const preJoinTextHints = ['enter meeting info', 'meeting passcode'].some(t => bodyText.includes(t));
      return { liveAudioCount, preJoinPresent, preJoinTextHints };
    }).catch(() => ({ liveAudioCount: 0, preJoinPresent: true, preJoinTextHints: true }));
    return state.liveAudioCount > 0 && !state.preJoinPresent && !state.preJoinTextHints;
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
  let unknownStateDuration = 0;
  const effectiveTimeout = () => timeoutMs + getEscalationExtensionMs();

  while (Date.now() - startTime < effectiveTimeout()) {
    await page.waitForTimeout(pollInterval);

    if (await isRejectedOrEnded(page)) {
      log('[Zoom Web] Bot was rejected or meeting ended during admission wait');
      throw new Error('Bot was rejected from the Zoom meeting or meeting ended');
    }

    if (await isAdmitted(page)) {
      log('[Zoom Web] Bot admitted — Leave button now visible');
      return true;
    }

    // Track unknown state (neither admitted, nor waiting room, nor rejected)
    const inWaitingNow = await isInWaitingRoom(page);
    if (!inWaitingNow) {
      unknownStateDuration += pollInterval;
    } else {
      unknownStateDuration = 0;
    }

    // Escalation check
    const elapsedMs = Date.now() - startTime;
    const escalation = checkEscalation(elapsedMs, timeoutMs, unknownStateDuration);
    if (escalation) {
      await triggerEscalation(botConfig, escalation.reason);
    }

    const elapsed = Math.round(elapsedMs / 1000);
    log(`[Zoom Web] Still waiting for admission... ${elapsed}s elapsed`);
  }

  throw new Error(`[Zoom Web] Bot not admitted within ${effectiveTimeout()}ms timeout`);
}

export async function checkZoomWebAdmissionSilent(page: Page | null): Promise<boolean> {
  if (!page) return false;
  // Retry up to 3 times with 1s delay — Zoom UI may briefly hide elements
  // during popup dismissals, tooltips, or layout transitions after admission.
  for (let attempt = 0; attempt < 3; attempt++) {
    if (await isAdmitted(page)) return true;
    if (attempt < 2) {
      await page.waitForTimeout(1000);
    }
  }
  return false;
}
