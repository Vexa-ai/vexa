import { Page } from 'playwright';
import { BotConfig } from '../../../types';
import { log, callJoiningCallback } from '../../../utils';
import {
  zoomNameInputSelector,
  zoomJoinButtonSelector,
  zoomPreviewMuteSelector,
  zoomPreviewVideoSelector,
  zoomPermissionDismissSelector,
  zoomMeetingAppSelector,
} from './selectors';

/**
 * Build the Zoom Web Client URL from a meeting invite URL.
 * Input:  https://us05web.zoom.us/j/84335626851?pwd=...
 * Output: https://app.zoom.us/wc/84335626851/join?pwd=...
 *
 * For Zoom Events URLs (events.zoom.us/ejl/...) the URL is returned as-is
 * because the events page handles its own redirect to the web client.
 */
export function buildZoomWebClientUrl(meetingUrl: string): string {
  try {
    const url = new URL(meetingUrl);

    // Zoom Events URLs — return as-is; the events page redirects to the web client
    if (url.hostname === 'events.zoom.us') {
      return meetingUrl;
    }

    // Already a web client URL — return as-is
    if (meetingUrl.includes('/wc/')) return meetingUrl;

    // Extract meeting ID from path: /j/84335626851
    const pathMatch = url.pathname.match(/\/j\/(\d+)/);
    const meetingId = pathMatch?.[1];
    if (!meetingId) {
      throw new Error(`Cannot extract meeting ID from Zoom URL: ${meetingUrl}`);
    }

    const pwd = url.searchParams.get('pwd') || '';
    const wcUrl = new URL(`https://app.zoom.us/wc/${meetingId}/join`);
    if (pwd) wcUrl.searchParams.set('pwd', pwd);

    return wcUrl.toString();
  } catch (err: any) {
    // If already a web client URL or unrecognised format, return as-is
    if (meetingUrl.includes('/wc/')) return meetingUrl;
    throw new Error(`Invalid Zoom meeting URL: ${meetingUrl} — ${err.message}`);
  }
}

const HOST_NOT_STARTED_RETRY_INTERVAL_MS = 15000;
const HOST_NOT_STARTED_MAX_WAIT_MS = 10 * 60 * 1000; // 10 minutes

export async function joinZoomWebMeeting(page: Page | null, botConfig: BotConfig): Promise<void> {
  if (!page) throw new Error('[Zoom Web] Page is required for web-based Zoom join');

  const rawUrl = botConfig.meetingUrl!;
  const webClientUrl = buildZoomWebClientUrl(rawUrl);
  log(`[Zoom Web] Navigating to web client: ${webClientUrl}`);

  // Retry loop: if host hasn't started the meeting yet, page title = "Error - Zoom"
  // and body contains "This meeting link is invalid". Poll until the pre-join page appears.
  const startTime = Date.now();
  while (true) {
    await page.goto(webClientUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(2000);

    const title = await page.title();
    const isError = title === 'Error - Zoom' || title === 'error - Zoom';
    if (!isError) break; // Pre-join page loaded

    const elapsed = Date.now() - startTime;
    if (elapsed >= HOST_NOT_STARTED_MAX_WAIT_MS) {
      throw new Error('[Zoom Web] Host did not start the meeting within the wait timeout');
    }
    log(`[Zoom Web] Host not started yet (title="${title}"). Retrying in ${HOST_NOT_STARTED_RETRY_INTERVAL_MS / 1000}s...`);
    await page.waitForTimeout(HOST_NOT_STARTED_RETRY_INTERVAL_MS);
  }

  // Notify bot-manager: joining
  try {
    await callJoiningCallback(botConfig);
  } catch (e: any) {
    log(`[Zoom Web] Warning: joining callback failed: ${e.message}`);
  }

  // Dismiss the "Use microphone and camera" permission dialog(s).
  // Zoom shows this dialog up to twice (camera+mic, then mic-only).
  // We click "Continue without microphone and camera" each time.
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const dismissBtn = page.locator(zoomPermissionDismissSelector).first();
      const visible = await dismissBtn.isVisible({ timeout: 4000 });
      if (visible) {
        await dismissBtn.click();
        log(`[Zoom Web] Dismissed permission dialog (attempt ${attempt + 1})`);
        await page.waitForTimeout(600);
      } else {
        break;
      }
    } catch {
      break;
    }
  }

  // Wait for the pre-join name input to appear
  log('[Zoom Web] Waiting for pre-join name input...');
  await page.waitForSelector(zoomNameInputSelector, { timeout: 30000 });

  // Fill name using React-compatible native setter
  await page.evaluate(
    ({ selector, name }: { selector: string; name: string }) => {
      const input = document.querySelector(selector) as HTMLInputElement | null;
      if (!input) return;
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
      setter?.call(input, name);
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
    },
    { selector: zoomNameInputSelector, name: botConfig.botName }
  );
  log(`[Zoom Web] Name set to "${botConfig.botName}"`);

  await page.waitForTimeout(300);

  // Ensure mic and video are off (they show as red/active = already muted in preview)
  // The preview buttons are toggles — only click if not already muted
  try {
    const muteBtn = page.locator(zoomPreviewMuteSelector);
    const muteAriaLabel = await muteBtn.getAttribute('aria-label');
    // "Mute" means currently unmuted → click to mute. "Unmute" means already muted → skip.
    if (muteAriaLabel === 'Mute') {
      await muteBtn.click();
      log('[Zoom Web] Muted microphone in preview');
    }
  } catch {
    log('[Zoom Web] Could not toggle preview mic (may already be muted)');
  }

  try {
    const videoBtn = page.locator(zoomPreviewVideoSelector);
    const videoAriaLabel = await videoBtn.getAttribute('aria-label');
    // "Stop Video" means video is on → click to stop. "Start Video" means already off → skip.
    if (videoAriaLabel === 'Stop Video') {
      await videoBtn.click();
      log('[Zoom Web] Stopped video in preview');
    }
  } catch {
    log('[Zoom Web] Could not toggle preview video (may already be off)');
  }

  // Click Join
  log('[Zoom Web] Clicking Join...');
  const joinBtn = page.locator(zoomJoinButtonSelector);
  await joinBtn.waitFor({ state: 'visible', timeout: 10000 });
  await joinBtn.click();
  log('[Zoom Web] Join clicked — waiting for meeting to load...');

  // Wait a moment for page transition
  await page.waitForTimeout(3000);
}
