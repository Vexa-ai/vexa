import { AdmissionError } from '../shared/admission';
import { Page } from "playwright";
import { log, callJoiningCallback } from "../_host";
import { BotConfig } from "../_host";
import {
  zoomNameInputSelector,
  zoomJoinButtonSelector,
  zoomPreviewMuteSelector,
  zoomPreviewVideoSelector,
  zoomPermissionDismissSelector,
} from "./selectors";
import { checkUiLocale } from '../shared/ui-locale';
import { readZoomPreJoinSnapshot, classifyZoomPreJoin, describeZoomPreJoin } from './prejoin-state';
import { redactMeetingUrl, redactUrlsIn, rethrowRedacted } from '../shared/url-redaction';

// NOTE vs the monolith: the audio-join flow (prepareZoomWebMeeting) and the
// per-speaker capture pipeline are RECORDING/HOST concerns and stay outside
// this brick. The join layer only enters and observes; the "Allow" permission
// clicks below remain because Zoom gates ADMISSION-adjacent UI behind them.

/**
 * Build the Zoom Web Client URL from a meeting invite URL.
 * Input:  https://us05web.zoom.us/j/84335626851?pwd=...
 * Output: https://app.zoom.us/wc/84335626851/join?pwd=...
 *
 * For Zoom Events URLs (events.zoom.us/ejl/...) the URL is returned as-is
 * because the events page handles its own redirect to the web client.
 *
 * v0.10.5 — White-label / enterprise URL support. Some organizations
 * front Zoom behind their own portal:
 *   https://zoom-lfx.platform.linuxfoundation.org/meeting/96088138284?password=...
 *   https://corp.example.com/m/96088138284?password=...
 * Two questions arise:
 *   (a) Can we navigate the canonical Zoom web client directly,
 *       skipping the portal? Sometimes — if the portal is just a
 *       branded landing page that proxies to the same /wc/ flow.
 *   (b) Should we? NO. Many portals (LFX, AWS Chime, Bloomberg) embed
 *       extra steps the user must complete in-page (T&C consent,
 *       guest-name pre-fill, captcha, magic-link auth). Bypassing
 *       them means the bot sometimes joins, but the user can never
 *       VNC in and assist when it doesn't.
 *
 * Strategy: ONLY rewrite when the host is canonical zoom.us / *.zoom.us
 * AND the path matches /j/<digits>. Anything else returns as-is and the
 * bot navigates the original URL — letting a human VNC in to click through
 * whatever extra page the portal renders. Once past the portal, the bot's
 * selector waits pick up from the standard Zoom pre-join page (name input, etc.).
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

    // Detect canonical Zoom: hostname is zoom.us or *.zoom.us
    // (NOT zoom-lfx.platform.linuxfoundation.org, which would slip past
    // a substring check).
    const isCanonicalZoomHost =
      url.hostname === 'zoom.us' || url.hostname.endsWith('.zoom.us');

    // For canonical zoom.us URLs we rewrite to the web-client URL — gives
    // a faster, more reliable join (no portal, no redirects).
    if (isCanonicalZoomHost) {
      const pathMatch = url.pathname.match(/\/j\/(\d+)/);
      const meetingId = pathMatch?.[1];
      if (!meetingId) {
        throw new Error(`Cannot extract meeting ID from Zoom URL: ${redactMeetingUrl(meetingUrl)}`);
      }
      const pwd = url.searchParams.get('pwd') || '';
      const wcUrl = new URL(`https://app.zoom.us/wc/${meetingId}/join`);
      if (pwd) wcUrl.searchParams.set('pwd', pwd);
      return wcUrl.toString();
    }

    // White-label / enterprise portal — return as-is so the bot navigates
    // the portal page. The user can VNC in and assist with any
    // extra-step UI (T&C, guest-name confirm, etc.). Bot picks back up
    // once Zoom's pre-join name input renders.
    return meetingUrl;
  } catch (err: any) {
    // If already a web client URL or unrecognised format, return as-is
    if (meetingUrl.includes('/wc/')) return meetingUrl;
    throw new Error(`Invalid Zoom meeting URL: ${redactMeetingUrl(meetingUrl)} — ${redactUrlsIn(err.message)}`);
  }
}

const HOST_NOT_STARTED_RETRY_INTERVAL_MS = 15000;
const HOST_NOT_STARTED_MAX_WAIT_MS = 10 * 60 * 1000; // 10 minutes
const ZOOM_PREJOIN_POLL_MS = 500;

/** Reason codes that lead a typed pre-join failure's message (they ride into the run's last_error). */
export const ZOOM_PREJOIN_HELD = 'zoom_prejoin_held';
export const ZOOM_NAME_INPUT_MISSING = 'name_input_missing';

export async function joinZoomMeeting(
  page: Page,
  meetingUrl: string,
  botName: string,
  botConfig: BotConfig,
): Promise<void> {
  if (!page) throw new Error('[Zoom Web] Page is required for web-based Zoom join');

  // Authenticated mode: the caller hands in a persistent context already signed in to
  // Zoom (see @vexa/remote-browser). The signed-in web client uses the account identity
  // instead of a guest name, so we skip the guest name-entry flow below. THE EXPERIMENT:
  // does being a real signed-in user clear the "sign in to join / automated bots aren't
  // allowed / use Zoom RTMS" wall that blocks anonymous web joins?
  const authenticated = !!botConfig.authenticated;

  const rawUrl = meetingUrl;
  const webClientUrl = buildZoomWebClientUrl(rawUrl);
  log(`[Zoom Web] Navigating to web client: ${redactMeetingUrl(webClientUrl)}`);

  // White-label / enterprise URLs (LFX, AWS Chime, Bloomberg-style portals) often render an extra
  // page in front of Zoom's pre-join: T&C consent, guest-name confirm, captcha, magic-link auth. The
  // bot can't automate those, but a human can VNC in and click through, so a non-canonical URL
  // (buildZoomWebClientUrl returned it as-is) gets 5 min to reach the name field. Canonical zoom.us
  // URLs keep 30 s — there is no portal layer to navigate.
  const isWhiteLabel = rawUrl === webClientUrl && !rawUrl.includes('/wc/');
  const nameInputTimeoutMs = isWhiteLabel ? 5 * 60 * 1000 : 30_000;
  if (!authenticated && isWhiteLabel) {
    log(`[Zoom Web] White-label URL — waiting up to 5 min for pre-join name input ` +
        `(human can VNC in to click through any portal page).`);
  }

  // Read the page until it settles on the pre-join. Zoom replaces the first page client-side with
  // `…/join?wpk=…`, and what lives there may reload itself, so the page is read again on every poll
  // (prejoin-state.ts) — never once before the redirect.
  const startTime = Date.now();
  await page.goto(webClientUrl, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(rethrowRedacted);
  let navigatedAt = Date.now();
  let nameDeadline = navigatedAt + nameInputTimeoutMs;
  let lastHeadline: string | null = null;
  while (true) {
    await page.waitForTimeout(ZOOM_PREJOIN_POLL_MS);
    const snapshot = await readZoomPreJoinSnapshot(page);
    const state = classifyZoomPreJoin(snapshot);

    if (state.kind === 'name_field') break;

    if (state.kind === 'auth_required') {
      if (!authenticated) {
        log('[Zoom Web] Sign-in page detected — meeting requires authenticated users');
        // PERMANENT: the host restricted entry to signed-in users and this bot has no session — a
        // re-spawn joins just as signed-out. Same sealed outcome the authenticated-mode dead-profile
        // path uses; the retry classifier already treats it as no-retry.
        throw new AdmissionError('auth_session_missing', '[Zoom Web] auth_required: meeting host has restricted entry to authenticated Zoom users; bot cannot join without a Zoom account session');
      }
      log('[Zoom Web] Authenticated mode: sign-in text present, proceeding (the persistent context should already carry a Zoom session)');
      break;
    }

    if (state.kind === 'host_not_started') {
      if (Date.now() - startTime >= HOST_NOT_STARTED_MAX_WAIT_MS) {
        throw new Error('[Zoom Web] Host did not start the meeting within the wait timeout');
      }
      log(`[Zoom Web] Host not started yet (title="${snapshot?.title}"). Retrying in ${HOST_NOT_STARTED_RETRY_INTERVAL_MS / 1000}s...`);
      await page.waitForTimeout(HOST_NOT_STARTED_RETRY_INTERVAL_MS);
      await page.goto(webClientUrl, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(rethrowRedacted);
      navigatedAt = Date.now();
      nameDeadline = navigatedAt + nameInputTimeoutMs;
      continue;
    }

    if (state.kind === 'hold') {
      // The page reloads itself; waiting is the whole job. The name clock starts over once it leaves.
      if (state.headline !== lastHeadline) {
        log(`[Zoom Web] Zoom is holding the join on a self-refreshing page ("${state.headline}") — waiting for it to open (up to ${HOST_NOT_STARTED_MAX_WAIT_MS / 60000} min)`);
        lastHeadline = state.headline;
      }
      if (Date.now() - startTime >= HOST_NOT_STARTED_MAX_WAIT_MS) {
        // Host-side: the host's account stayed in another meeting for the whole wait. Same outcome as
        // a waiting room nobody answered (transient, a legitimate retry later).
        throw new AdmissionError('lobby_timeout', `[Zoom Web] ${ZOOM_PREJOIN_HELD}: Zoom held the join for ${HOST_NOT_STARTED_MAX_WAIT_MS / 60000} min on "${state.headline}"; ${describeZoomPreJoin(snapshot)}`);
      }
      nameDeadline = Date.now() + nameInputTimeoutMs;
      continue;
    }

    // pending: a redirect or a render is still in flight.
    if (authenticated && Date.now() - navigatedAt >= 2000) break; // signed-in pre-join may have no name field
    if (Date.now() >= nameDeadline) {
      throw new AdmissionError('join_failure', `[Zoom Web] ${ZOOM_NAME_INPUT_MISSING}: no pre-join name field ${Math.round(nameInputTimeoutMs / 1000)}s after the page loaded; ${describeZoomPreJoin(snapshot)}`);
    }
  }

  // Notify the host: joining
  await callJoiningCallback(botConfig);
  await checkUiLocale(page, "zoom", "pre-join");

  // Dismiss the OneTrust cookie-consent banner if present.
  //
  // The CLASSIC web client (app.zoom.us/wc/join/<id>) renders a OneTrust
  // consent banner that overlays the pre-join card — verified via
  // document.elementFromPoint() over the name field returning
  // #onetrust-reject-all-handler. Left up, it intercepts pointer events so
  // the name input can't be focused and Join never enables. Accept (or fall
  // back to reject) to clear it. Harmless no-op on the React client, which
  // doesn't render this banner.
  for (const otSel of ['#onetrust-accept-btn-handler', '#onetrust-reject-all-handler']) {
    try {
      const otBtn = page.locator(otSel).first();
      if (await otBtn.isVisible({ timeout: 1500 })) {
        await otBtn.click();
        log(`[Zoom Web] Dismissed cookie-consent banner (${otSel})`);
        await page.waitForTimeout(400);
        break;
      }
    } catch { /* no banner — continue */ }
  }

  // Handle the "Use microphone and camera" permission dialog(s).
  // Zoom shows this dialog up to twice (camera+mic, then mic-only).
  // ALL bots must click "Allow" to join the audio channel — without it, Zoom
  // never creates <audio> elements for other participants and an embedder's
  // capture pipeline gets no audio data. Recorder bots mute their mic in preview
  // (below) so they don't transmit, but they still need to join audio to RECEIVE.
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      // Click "Allow" to grant audio permission (needed to receive meeting audio)
      const allowBtn = page.locator('button:has-text("Allow")').first();
      const allowVisible = await allowBtn.isVisible({ timeout: 4000 });
      if (allowVisible) {
        await allowBtn.click();
        log(`[Zoom Web] Granted audio permission (attempt ${attempt + 1})`);
        await page.waitForTimeout(600);
        continue;
      }
      // Fallback: if "Allow" not found, check for dismiss button — but log a warning
      // since skipping audio permission means no audio capture
      const dismissBtn = page.locator(zoomPermissionDismissSelector).first();
      const visible = await dismissBtn.isVisible({ timeout: 1000 });
      if (visible) {
        log(`[Zoom Web] WARNING: No "Allow" button found, falling back to dismiss — audio capture may not work (attempt ${attempt + 1})`);
        await dismissBtn.click();
        await page.waitForTimeout(600);
      } else {
        break;
      }
    } catch {
      break;
    }
  }

  if (authenticated) {
    // Signed-in pre-join uses the account identity — there may be no guest name field,
    // so don't block on it; just let the signed-in pre-join settle.
    log('[Zoom Web] Authenticated mode — signed-in pre-join; not waiting on a guest name field');
    await page.waitForTimeout(3000);
  } else {
    log('[Zoom Web] Pre-join name input present');
  }

  // Some meetings show a passcode-entry pre-join page that includes a
  // passcode input ABOVE the name input. If a passcode field is visible
  // and we have a passcode in botConfig, fill it. If a passcode field is
  // visible but we have NO passcode, fail fast with a structured reason —
  // the join button stays disabled forever and the bot would otherwise
  // sit on the pre-join page indefinitely.
  const passcodeInputSelector = 'input[placeholder*="passcode" i], input[placeholder*="password" i], input[type="password"]';
  const hasPasscodeField = await page.locator(passcodeInputSelector).first().isVisible({ timeout: 1000 }).catch(() => false);
  if (hasPasscodeField) {
    const passcode = botConfig.passcode || '';
    if (passcode) {
      await page.locator(passcodeInputSelector).first().fill(passcode);
      log(`[Zoom Web] Filled passcode field`);
    } else {
      throw new Error('[Zoom Web] passcode_required: meeting requires a passcode but botConfig.passcode is empty; pass passcode to the embedder or include ?pwd=... in the meeting URL');
    }
  }

  // Fill name using REAL keyboard events.
  //
  // Earlier versions used a "React-compatible native setter" trick that
  // synthetically dispatched input/change events. On the current Zoom Web
  // UI version (observed 2026-04-26 in meeting_id=29), that doesn't fully
  // satisfy Zoom's React form validation — the Join button stays disabled
  // (class="zm-btn preview-join-button disabled ..."), and Playwright's
  // 30s click retry loop times out with the failure mode:
  //   "<div class="preview-meeting-info">…</div> intercepts pointer events".
  //
  // Real keyboard events (focus + type) trigger Zoom's full input pipeline
  // including the validation that enables the Join button.
  if (authenticated) {
    // Signed-in: keep the account identity. Leave any pre-filled name as-is; only type a
    // fallback if the field is unexpectedly empty (so the Join button can still enable).
    const nameField = page.locator(zoomNameInputSelector).first();
    if (await nameField.isVisible({ timeout: 1500 }).catch(() => false)) {
      const current = await nameField.inputValue().catch(() => '');
      if (current) {
        log(`[Zoom Web] Signed-in name pre-filled ("${current}") — using account identity`);
      } else {
        await nameField.click({ timeout: 5000 }).catch(() => {});
        await page.keyboard.type(botName, { delay: 30 });
        log(`[Zoom Web] Signed-in name field empty — typed fallback "${botName}"`);
      }
    } else {
      log('[Zoom Web] No name field — signed-in client uses the account name directly');
    }
  } else {
    await page.locator(zoomNameInputSelector).first().click({ timeout: 5000 }).catch(() => {});
    await page.locator(zoomNameInputSelector).first().fill('');
    await page.keyboard.type(botName, { delay: 30 });
    log(`[Zoom Web] Name typed: "${botName}"`);
  }

  // Wait for Zoom to enable the Join button. The CLASSIC web client gates
  // #joinBtn behind a Google reCAPTCHA ("I'm not a robot" + image challenge)
  // that an automated agent cannot clear — a human must solve it via noVNC.
  // When a reCAPTCHA frame is present we extend the wait to 15 minutes and
  // poll for the button to enable; the React client (no captcha) enables it
  // within a second or two of typing.
  const captchaPresent = await page.locator('iframe[src*="recaptcha"]').first()
    .isVisible({ timeout: 500 }).catch(() => false);
  // Second human gate: some meetings hard-block anonymous bots with a "Sign in to
  // join / Automated bots aren't allowed … must use Zoom RTMS" modal. Join stays
  // disabled until a human signs in (e.g. as a real Zoom account) via noVNC.
  const signInWall = await page.locator(
    'text=/sign in to join|bots aren.?t allowed|must use Zoom RTMS/i'
  ).first().isVisible({ timeout: 500 }).catch(() => false);
  const humanGate = captchaPresent || signInWall;
  const joinEnableTimeoutMs = humanGate ? 15 * 60 * 1000 : 8000;
  if (humanGate) {
    const which = signInWall ? 'sign-in / bots-not-allowed wall' : 'reCAPTCHA';
    log(`[Zoom Web] ⚠️ ${which} is gating the Join button — a HUMAN must clear it ` +
        `via noVNC (sign in as a Zoom account / solve the captcha). ` +
        `Holding the browser open up to 15 min for Join to become enabled...`);
  }
  await page.waitForFunction(
    (sel: string) => {
      const btn = document.querySelector(sel) as HTMLButtonElement | null;
      return !!btn && !btn.classList.contains('disabled') && !btn.disabled;
    },
    zoomJoinButtonSelector,
    { timeout: joinEnableTimeoutMs },
  ).then(() => log('[Zoom Web] Join button enabled — proceeding to click'))
   .catch(() => log('[Zoom Web] WARNING: Join button still disabled after wait; will attempt click anyway'));

  // Ensure mic is muted in preview for recorder bots (they only need to receive audio).
  // Voice agent bots keep mic unmuted so Zoom grants audio access for TTS output.
  const isVoiceAgent = !!(botConfig as any).voiceAgentEnabled;
  if (!isVoiceAgent) {
    try {
      const muteBtn = page.locator(zoomPreviewMuteSelector);
      const muteAriaLabel = await muteBtn.getAttribute('aria-label');
      // "Mute" means currently unmuted → click to mute. "Unmute" means already muted → skip.
      if (muteAriaLabel === 'Mute') {
        await muteBtn.click();
        log('[Zoom Web] Muted microphone in preview (recorder bot — receive-only audio)');
      }
    } catch {
      log('[Zoom Web] Could not toggle preview mic (may already be muted)');
    }
  } else {
    log('[Zoom Web] Voice agent: keeping mic enabled in preview for TTS');
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

  // Click Join via DOM, bypassing Playwright's pointer-event interception
  // checks. Zoom's preview screen sometimes overlays a `.preview-meeting-info`
  // div on top of the Join button — Playwright's `.click()` waits for the
  // element to become hit-testable (no overlapping z-index intercepting
  // pointer events) and times out after 30s. Calling `.click()` programmatically
  // via the DOM bypasses that hit-test entirely; the underlying React handler
  // fires regardless of overlapping elements.
  log('[Zoom Web] Clicking Join (DOM-direct)...');
  const clicked = await page.evaluate((sel: string) => {
    const btn = document.querySelector(sel) as HTMLButtonElement | null;
    if (!btn) return false;
    if (btn.classList.contains('disabled') || btn.disabled) return false;
    btn.click();
    return true;
  }, zoomJoinButtonSelector);
  if (!clicked) {
    log('[Zoom Web] WARNING: Join button not clickable via DOM (still disabled?); falling back to Playwright click...');
    const joinBtn = page.locator(zoomJoinButtonSelector);
    await joinBtn.waitFor({ state: 'visible', timeout: 10000 });
    await joinBtn.click({ force: true, timeout: 10000 });
  }
  log('[Zoom Web] Join clicked — waiting for meeting to load...');

  // Wait a moment for page transition
  await page.waitForTimeout(3000);
}
