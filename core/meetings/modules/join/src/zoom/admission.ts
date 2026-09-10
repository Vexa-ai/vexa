import { AdmissionError } from '../shared/admission';
import { Page } from "playwright";
import { log, callAwaitingAdmissionCallback, callBlockedCallback } from "../_host";
import { BotConfig } from "../_host";
import { checkEscalation, triggerEscalation, getEscalationExtensionMs } from "../shared/escalation";
import {
  zoomLeaveButtonSelector,
  zoomInMeetingMarkers,
  zoomMeetingAppSelector,
  zoomWaitingRoomTexts,
  zoomRemovalTexts,
  zoomBotBlockTexts,
} from "./selectors";

/**
 * Detect Zoom's post-Join anti-bot wall.
 *
 * After the bot clicks Join, meetings/accounts with the RTMS-required anti-bot
 * setting serve an admission-phase wall instead of the waiting room or the
 * meeting:
 *   "We detected you may be a bot. Automated bots aren't allowed to join this
 *    meeting or webinar and must use Zoom RTMS. … Sign in to join" + reCAPTCHA.
 *
 * This is NOT IP reputation — verified identical from a datacenter IP and a
 * residential IP on the same meeting, so it is keyed to the meeting/account.
 * The sanctioned path the wall itself points to is Zoom RTMS (Realtime Media
 * Streams), which is a server-side API, not a browser join — so there is no
 * honest in-browser way past it. We detect it and FAIL FAST with a structured
 * reason (`zoom_requires_rtms`) so the host stops polling "waiting for
 * admission" forever and can route to RTMS.
 *
 * Case-insensitive substring scan of the live page text against the known wall
 * phrases (selectors.ts: zoomBotBlockTexts). Returns the matched phrase, or null.
 */
async function detectZoomBotBlock(page: Page): Promise<string | null> {
  try {
    return await page.evaluate((phrases: string[]) => {
      const body = (document.body?.innerText || '').toLowerCase();
      for (const p of phrases) {
        if (body.includes(p.toLowerCase())) return p;
      }
      return null;
    }, zoomBotBlockTexts);
  } catch {
    return null;
  }
}

/**
 * Check if the bot is confirmed inside the meeting.
 *
 * Signal order, strongest first:
 *   1. Leave button VISIBLE — footer is showing. Never renders in the lobby.
 *   2. Any in-meeting footer marker PRESENT in the DOM (zoomInMeetingMarkers),
 *      with no pre-join form. Presence, not visibility: Zoom's footer toolbar
 *      auto-hides after a few seconds without pointer movement, which makes
 *      every isVisible() probe lie about a bot that is genuinely in the call.
 *   3. Lobby text — only consulted once 1 and 2 have found nothing. It then
 *      vetoes the weak fallback below.
 *   4. Weak fallback: `.meeting-app` shell, no lobby copy, no pre-join form.
 *
 * WHY 2 OUTRANKS 3. zoomWaitingRoomTexts contains substrings as generic as
 * 'Please wait' and 'waiting room'. Zoom renders those in-meeting too
 * (connecting-audio toasts, host-control notices) and can leave lobby copy in
 * the DOM after admission. Previously the text probe ran BEFORE both fallbacks,
 * so a bot admitted seconds earlier — footer already auto-hidden — returned
 * "not admitted" and its caller left the meeting. Footer markers never render
 * in the lobby, so they are safe to rank above a fuzzy text match.
 *
 * WHY 3 STILL VETOES 4. Zoom renders the waiting room INSIDE `.meeting-app`,
 * so the container alone reports admitted while the bot is still in the lobby,
 * and the dashboard skips `awaiting_admission` entirely. Observed 2026-04-26
 * meeting_id=36: screenshot showed "Host has joined. We've let them know you're
 * here." while the bot reported admitted=true. `.meeting-app` therefore stays a
 * WEAK signal and is deliberately excluded from zoomInMeetingMarkers.
 *
 * The pre-join guard is retained throughout: observed 2026-04-26 meeting_id=31,
 * bot at the "Enter Meeting Info"/passcode screen while an earlier audio-only
 * fallback reported admitted.
 *
 * The former live-<audio>/srcObject fallback is REMOVED: modern Zoom Web routes
 * audio through the Web Audio API and no longer creates <audio> elements with
 * srcObject (#318), so that branch could never return true. It read as a third
 * layer of protection while contributing nothing.
 */
async function isAdmitted(page: Page): Promise<boolean> {
  try {
    // Strong positive: Leave button is footer-only, never appears in
    // pre-join or waiting room. Trust it without further checks.
    const leaveBtn = page.locator(zoomLeaveButtonSelector).first();
    if (await leaveBtn.isVisible({ timeout: 500 })) return true;

    // Everything below in ONE page.evaluate, so all signals read a single
    // consistent DOM snapshot instead of racing Zoom's UI transitions.
    const state = await page.evaluate((cfg: { inMeeting: string[]; waiting: string[]; meetingApp: string }) => {
      const present = cfg.inMeeting.filter(s => {
        try { return !!document.querySelector(s); } catch { return false; }
      });
      const bodyText = document.body?.innerText || '';
      const waitingHit = cfg.waiting.find(t => bodyText.includes(t)) || null;
      const preJoinPresent = !!(
        document.querySelector('#input-for-name') ||
        document.querySelector('button.preview-join-button') ||
        document.querySelector('input[placeholder*="passcode" i], input[placeholder*="password" i]')
      );
      const meetingAppPresent = !!document.querySelector(cfg.meetingApp);
      return { present, waitingHit, preJoinPresent, meetingAppPresent };
    }, { inMeeting: zoomInMeetingMarkers, waiting: zoomWaitingRoomTexts, meetingApp: zoomMeetingAppSelector })
      .catch(() => ({ present: [] as string[], waitingHit: null as string | null, preJoinPresent: true, meetingAppPresent: false }));

    // Hard DOM evidence of the in-meeting footer OUTRANKS the text probe.
    //
    // Previously the innerText match returned false BEFORE these markers were
    // consulted, and zoomWaitingRoomTexts contains substrings as generic as
    // 'Please wait' and 'waiting room' which Zoom also renders in-meeting
    // (connecting-audio toasts, host-control notices) and can leave in the DOM
    // after admission. Combined with the footer toolbar auto-hiding — which
    // defeats the isVisible() probe above — a bot that had just been admitted
    // returned "not admitted" and left the call.
    //
    // The pre-join form is the one exception: if the name/passcode form is on
    // screen we are demonstrably not in the meeting, whatever else matched.
    if (state.present.length > 0 && !state.preJoinPresent) return true;

    // No footer evidence — now the lobby text is meaningful, and must veto the
    // weak fallback below. Zoom renders the waiting room INSIDE `.meeting-app`,
    // so without this the container alone would report admitted while still in
    // the lobby (observed 2026-04-26 meeting_id=36).
    if (state.waitingHit) return false;

    // Weak fallback: meeting shell, no lobby copy, no pre-join form.
    if (state.meetingAppPresent && !state.preJoinPresent) return true;

    return false;
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

export async function waitForZoomMeetingAdmission(
  page: Page,
  timeoutMs: number,
  botConfig: BotConfig
): Promise<boolean> {
  if (!page) throw new Error('[Zoom Web] Page required for admission check');

  log('[Zoom Web] Checking admission state...');

  // Fast path: already admitted (host was present and let us in immediately).
  // isAdmitted() rules out the waiting room before its weaker fallbacks fire,
  // so a true here means the bot is genuinely in the meeting.
  if (await isAdmitted(page)) {
    log('[Zoom Web] Bot immediately admitted (no waiting room detected)');
    return true;
  }

  // Terminal anti-bot wall: Zoom serves the "must use Zoom RTMS" / "automated
  // bots aren't allowed" wall in the admission phase for RTMS-required
  // meetings/accounts. It renders immediately after Join, so check before the
  // poll loop and fail fast — otherwise the bot loops "waiting for admission"
  // forever (the wall never becomes the waiting room or the meeting).
  {
    const wall = await detectZoomBotBlock(page);
    if (wall) {
      log(`[Zoom Web] 🚫 Anti-bot wall detected (matched: "${wall}") — this meeting requires Zoom RTMS; bots cannot join via the web client. Failing fast.`);
      await callBlockedCallback(botConfig, 'zoom_requires_rtms', { matched: wall, phase: 'pre_admission_poll' });
      // PERMANENT platform verdict: Zoom itself refuses browser bots here — a re-spawn hits the
      // same wall. `denial` is the closest sealed outcome (a distinct `blocked` CompletionReason
      // needs lane:contract — see join-driver.ts).
      throw new AdmissionError('denial', '[Zoom Web] zoom_requires_rtms: meeting/account blocks automated browser joins and requires Zoom RTMS (Realtime Media Streams); route to the RTMS path');
    }
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
      throw new AdmissionError('denial', 'Bot was rejected from the Zoom meeting or meeting ended');
    }

    // Anti-bot wall can also appear a beat after Join (the reCAPTCHA frame and
    // wall text stream in just after the page transition). Re-scan each poll so
    // we transition to terminal `blocked` instead of accruing unknown-state time.
    const wall = await detectZoomBotBlock(page);
    if (wall) {
      log(`[Zoom Web] 🚫 Anti-bot wall detected during poll (matched: "${wall}") — requires Zoom RTMS. Failing fast.`);
      await callBlockedCallback(botConfig, 'zoom_requires_rtms', { matched: wall, phase: 'admission_poll' });
      throw new AdmissionError('denial', '[Zoom Web] zoom_requires_rtms: meeting/account blocks automated browser joins and requires Zoom RTMS (Realtime Media Streams); route to the RTMS path');
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

  throw new AdmissionError('lobby_timeout', `[Zoom Web] Bot not admitted within ${effectiveTimeout()}ms timeout`);
}

export async function checkForZoomAdmissionSilent(page: Page): Promise<boolean> {
  if (!page) return false;
  // Retry with a wider window — the lobby -> in-call DOM swap can take longer
  // than the old 2s budget allowed. This check only ever gates a decision to
  // LEAVE a meeting we believe we are in, so being slow to conclude "not
  // admitted" is far cheaper than being wrong: a real ejection persists and is
  // caught on a later attempt, while a premature false reads to the user as the
  // bot abandoning a live call seconds after being let in.
  const ATTEMPTS = 6;
  for (let attempt = 0; attempt < ATTEMPTS; attempt++) {
    if (await isAdmitted(page)) return true;
    if (attempt < ATTEMPTS - 1) {
      await page.waitForTimeout(1500);
    }
  }
  return false;
}
