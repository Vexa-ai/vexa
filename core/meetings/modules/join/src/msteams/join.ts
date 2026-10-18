import { Page } from "playwright";
import { log, callJoiningCallback } from "../_host";
import { BotConfig } from "../_host";
import {
  teamsContinueButtonSelectors,
  teamsContinueWithoutMediaSelectors,
  teamsJoinButtonSelectors,
  teamsCameraButtonSelectors,
  teamsVideoOptionsButtonSelectors,
  teamsNameInputSelectors,
  teamsComputerAudioRadioSelectors,
  teamsDontUseAudioRadioSelectors,
  teamsSpeakerEnableSelectors,
  teamsSpeakerDisableSelectors
} from "./selectors";
import { dismissTeamsAvConfirmModal, isTeamsAvConfirmModalVisible } from "./modals";
import {
  authRedirectError,
  classifyNonMeetingUrl,
  isMicrosoftLoginUrl,
  meetingOriginHost,
  redactUrl,
} from "./auth-redirect";
import { TeamsPreJoinBlockedError, teamsDisplayNameDisallowedChars } from "./prejoin-blocked";

// NOTE vs the monolith: the WebRTC remote-audio hook and the voice-agent
// virtual-camera flow are RECORDING/HOST concerns and stay outside this brick.
// An embedder that records installs its own page.addInitScript BEFORE calling
// joinMicrosoftTeams — the join layer only enters and observes.

async function warmUpTeamsMediaDevices(page: Page): Promise<void> {
  try {
    const result = await page.evaluate(async () => {
      try {
        if (!navigator.mediaDevices?.getUserMedia) {
          return "getUserMedia unavailable";
        }
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: true });
        const tracks = stream.getTracks();
        tracks.forEach((track) => track.stop());
        return `media warm-up success (tracks=${tracks.length})`;
      } catch (err: any) {
        return `media warm-up failed: ${err?.message || err}`;
      }
    });
    log(`[Teams Join] ${result}`);
  } catch (err: any) {
    log(`[Teams Join] Media warm-up evaluate failed: ${err?.message || err}`);
  }
}

/**
 * Wait for the anonymous pre-join screen.
 *
 * The loop watches the URL as well as the DOM: Teams can bounce the meetup-join to the Microsoft
 * sign-in host at any point (it is a client-side navigation, so it happens mid-wait as readily as
 * before the first tick). A sign-in page is terminal on sight — waiting out the timeout there
 * buys nothing but a misleading "pre-join readiness" verdict on a page that has no pre-join.
 *
 * `requestedHost` is the host of the meeting link we were asked to open. A readiness timeout is
 * terminal when the page ended up on neither that host nor a Teams host — there is no pre-join to
 * be slow. Timing out ON one of those hosts stays advisory: a genuinely slow Teams pre-join is a
 * thing, and the later steps still get their chance at it.
 */
async function waitForTeamsPreJoinReadiness(
  page: Page,
  timeoutMs: number,
  requestedHost: string | null,
): Promise<boolean> {
  const start = Date.now();
  let mediaWarmupAttempted = false;
  let continueClickAttempts = 0;
  let continueWithoutMediaClickAttempts = 0;

  while (Date.now() - start < timeoutMs) {
    if (isMicrosoftLoginUrl(page.url())) throw authRedirectError(page.url());

    // v0.10.5 — "Continue without audio or video" confirmation modal.
    // Teams renders this BEFORE the prejoin name input when Chromium's
    // media-permission state is "denied". The modal is intermittent
    // (depends on permission cache state at page boot) but when it
    // appears it BLOCKS the prejoin from rendering — Join now never
    // enables until the modal is dismissed. Click through it eagerly
    // so the prejoin can proceed.
    const continueWithoutMediaSelector = teamsContinueWithoutMediaSelectors.join(", ");
    const continueWithoutMediaVisible = await page
      .locator(continueWithoutMediaSelector)
      .first()
      .isVisible()
      .catch(() => false);
    if (continueWithoutMediaVisible && continueWithoutMediaClickAttempts < 3) {
      continueWithoutMediaClickAttempts += 1;
      log(
        `ℹ️ "Continue without audio or video" modal detected, clicking through ` +
        `(attempt ${continueWithoutMediaClickAttempts})...`,
      );
      try {
        await page.locator(continueWithoutMediaSelector).first().click({ timeout: 5000 });
        log('✅ Dismissed "Continue without audio or video" modal');
      } catch (err: any) {
        log(`ℹ️ Could not click "Continue without audio or video": ${err?.message || err}`);
      }
      await page.waitForTimeout(500);
      continue;
    }

    const joinNowVisible = await page.locator('button:has-text("Join now"), [aria-label*="Join now"]').first().isVisible().catch(() => false);
    const cancelVisible = await page.locator('button:has-text("Cancel"), [aria-label*="Cancel"]').first().isVisible().catch(() => false);
    const nameInputVisible = await page.locator(teamsNameInputSelectors.join(", ")).first().isVisible().catch(() => false);
    const cameraControlVisible = await page
      .locator([
        'button[aria-label="Turn on video"]',
        'button[aria-label="Turn off video"]',
        'button[aria-label="Turn on camera"]',
        'button[aria-label="Turn off camera"]',
        'button[aria-label="Turn camera on"]',
        'button[aria-label="Turn camera off"]',
        ...teamsVideoOptionsButtonSelectors
      ].join(", "))
      .first()
      .isVisible()
      .catch(() => false);
    const computerAudioVisible = await page.locator(teamsComputerAudioRadioSelectors.join(", ")).first().isVisible().catch(() => false);

    if (joinNowVisible || (cancelVisible && (nameInputVisible || cameraControlVisible || computerAudioVisible))) {
      log("✅ Teams pre-join controls are ready");
      return true;
    }

    const continueVisible = await page.locator(teamsContinueButtonSelectors[0]).first().isVisible().catch(() => false);
    if (continueVisible && continueClickAttempts < 2) {
      continueClickAttempts += 1;
      log(`ℹ️ Continue button still visible, clicking again (attempt ${continueClickAttempts})...`);
      try {
        await page.locator(teamsContinueButtonSelectors[0]).first().click();
      } catch {}
      await page.waitForTimeout(500);
      continue;
    }

    const permissionGateVisible = await page
      .locator('text=/Select Allow to let Microsoft Teams use your mic and camera/i')
      .first()
      .isVisible()
      .catch(() => false);
    if (permissionGateVisible && !mediaWarmupAttempted) {
      mediaWarmupAttempted = true;
      log("ℹ️ Teams permission gate detected on light-meetings page; running media warm-up...");
      await warmUpTeamsMediaDevices(page);
    }

    await page.waitForTimeout(300);
  }

  const finalUrl = page.url();
  const offMeeting = classifyNonMeetingUrl(finalUrl, requestedHost);
  if (offMeeting) throw offMeeting;
  log(`⚠️ Timed out waiting for Teams pre-join readiness after ${timeoutMs}ms (url=${redactUrl(finalUrl)})`);
  return false;
}

/** How long a visible "Join now" may stay disabled after the name is filled before the join stops.
 *  Teams enables it within a frame of a valid name; the budget only absorbs a slow render. */
const JOIN_NOW_ENABLE_BUDGET_MS = 10000;

/** In-page read of Teams' own pre-join validation text (plain string: runs in the browser). */
const READ_TEAMS_PREJOIN_VALIDATION = `(() => {
  const text = (document.body && document.body.innerText) || "";
  const line = text.split("\\n").map((l) => l.trim()).find((l) => /can only include/i.test(l));
  return line ? line.slice(0, 200) : null;
})()`;

async function isLocatorEnabled(locator: any): Promise<boolean> {
  // A stand-in Page without isEnabled cannot report a disabled button; treat it as enabled so
  // only positive evidence (a real disabled button) stops the join.
  if (typeof locator?.isEnabled !== "function") return true;
  try {
    return await locator.isEnabled();
  } catch {
    return true;
  }
}

/**
 * The pre-join gate (#1780). A visible "Join now" that stays DISABLED after the display name was
 * entered means Teams refused the name (or something else on the pre-join): the bot cannot join.
 * Throw a typed terminal naming it, instead of letting the click time out quietly and the
 * admission wait read that visible button as a lobby. See prejoin-blocked.ts.
 *
 * The AV-confirm modal (#467) can also hold the button disabled; it is dismissed here before the
 * verdict, so only a button that is still disabled with nothing left to dismiss stops the join.
 */
async function assertTeamsJoinNowEnabled(page: Page, joinNowButton: any, botName: string): Promise<void> {
  const start = Date.now();
  while (true) {
    if (await isLocatorEnabled(joinNowButton)) return;
    if (Date.now() - start >= JOIN_NOW_ENABLE_BUDGET_MS) break;
    await dismissTeamsAvConfirmModal(page);
    await page.waitForTimeout(500);
  }
  let teamsMessage: string | null = null;
  try {
    const read = await page.evaluate(READ_TEAMS_PREJOIN_VALIDATION);
    teamsMessage = typeof read === "string" && read ? read : null;
  } catch {}
  const err = new TeamsPreJoinBlockedError(botName, teamsMessage);
  log(`❌ ${err.message}`);
  throw err;
}

export async function joinMicrosoftTeams(
  page: Page,
  meetingUrl: string,
  botName: string,
  botConfig: BotConfig
): Promise<void> {
  // Step 1: Navigate to Teams meeting.
  // Logged REDACTED (origin + path): a Teams short link carries the meeting passcode in its
  // `?p=` query, and bot logs are read by humans and shipped off-box. Same rule the redirect
  // errors already follow — the query is dropped whole rather than filtered key by key.
  log(`Step 1: Navigating to Teams meeting: ${redactUrl(meetingUrl)}`);
  await page.goto(meetingUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(500);

  // Fix 2: Propagate JOINING callback failure — bot must NOT proceed if server rejected
  await callJoiningCallback(botConfig);
  log("Joining callback sent successfully");

  // Step 1b: the navigation landed somewhere. If that somewhere is the Microsoft sign-in host,
  // stop here — every step below hunts for pre-join controls that a sign-in page does not have,
  // and each one of them "succeeds" quietly by continuing.
  if (isMicrosoftLoginUrl(page.url())) throw authRedirectError(page.url());
  const requestedHost = meetingOriginHost(meetingUrl);

  log("Step 2: Looking for 'Continue on this browser' button...");
  try {
    const continueButton = page.locator(teamsContinueButtonSelectors[0]).first();
    await continueButton.waitFor({ timeout: 10000 });
    await continueButton.click();
    log("✅ Clicked 'Continue on this browser' button");
    // Brief wait before pre-join readiness loop takes over
    await page.waitForTimeout(500);
  } catch (error) {
    log("ℹ️ Continue button not found, continuing...");
  }

  log("Step 2.5: Waiting for Teams pre-join controls...");
  await waitForTeamsPreJoinReadiness(page, 45000, requestedHost);

  // NOTE: Steps 3-5 configure the pre-join screen BEFORE clicking "Join now".
  // The pre-join screen shows camera toggle, name input, and audio settings.
  // We must configure all of these before clicking "Join now" in Step 6.

  log("Step 3: Camera handling...");
  // Turn camera off to be unobtrusive
  try {
    const cameraButton = page.locator(teamsCameraButtonSelectors[0]);
    await cameraButton.waitFor({ timeout: 5000 });
    await cameraButton.click();
    log("✅ Camera turned off");
  } catch (error) {
    log("ℹ️ Camera button not found or already off");
  }

  log("Step 4: Trying to set display name...");
  try {
    const nameInput = page.locator(teamsNameInputSelectors.join(', ')).first();
    await nameInput.waitFor({ timeout: 5000 });
    await nameInput.fill(botName);
    log(`✅ Display name set to "${botName}"`);
    const disallowed = teamsDisplayNameDisallowedChars(botName);
    if (disallowed.length) {
      log(
        `⚠️ Display name contains characters Teams does not allow (${disallowed.join(" ")}); ` +
        `Teams accepts letters, numbers, spaces and - ' . _ @ — expect "Join now" to stay disabled`,
      );
    }
  } catch (error) {
    log("ℹ️ Display name input not found, continuing...");
  }

  log("Step 5: Ensuring Computer audio is selected...");
  try {
    const computerAudioRadio = page.locator(teamsComputerAudioRadioSelectors.join(', ')).first();
    const dontUseAudioRadio = page.locator(teamsDontUseAudioRadioSelectors.join(', ')).first();
    const computerAudioVisible = await computerAudioRadio.isVisible().catch(() => false);

    if (computerAudioVisible) {
      const dontUseAudioChecked =
        (await dontUseAudioRadio.isVisible().catch(() => false)) &&
        (await dontUseAudioRadio.getAttribute('aria-checked')) === 'true';

      if (dontUseAudioChecked) {
        log("⚠️ 'Don't use audio' detected. Switching to Computer audio...");
        await computerAudioRadio.click({ timeout: 5000 });
        await page.waitForTimeout(200);
      } else {
        await computerAudioRadio.click({ timeout: 5000 });
        await page.waitForTimeout(200);
      }
      log("✅ Computer audio selected.");
    } else {
      log("ℹ️ Audio radios not visible. Attempting to force-enable speaker...");
    }

    const speakerOnButton = page.locator(teamsSpeakerEnableSelectors.join(', ')).first();
    const speakerOffButton = page.locator(teamsSpeakerDisableSelectors.join(', ')).first();

    const speakerOnVisible = await speakerOnButton.isVisible().catch(() => false);
    const speakerOffVisible = await speakerOffButton.isVisible().catch(() => false);

    if (speakerOnVisible) {
      await speakerOnButton.click({ timeout: 5000 });
      await page.waitForTimeout(100);
      log("✅ Speaker enabled via toggle.");
    } else if (speakerOffVisible) {
      log("ℹ️ Speaker already enabled.");
    } else {
      log("ℹ️ Speaker controls not visible; continuing with defaults.");
    }

    await page.evaluate(() => {
      const audioEls = Array.from(document.querySelectorAll('audio'));
      audioEls.forEach((el: any) => {
        try {
          el.muted = false;
          el.autoplay = true;
          el.dataset.vexaTouched = 'true';
          if (typeof el.play === 'function') {
            el.play().catch(() => {});
          }
        } catch {}
      });
    });
  } catch (error: any) {
    log(`ℹ️ Could not enforce Computer audio: ${error.message}. Continuing...`);
  }

  log("Step 6: Clicking 'Join now' to enter the meeting...");
  // Use the more specific "Join now" selector first to avoid ambiguity
  const joinNowButton = page.locator('button:has-text("Join now")').first();
  const joinNowVisible = await joinNowButton.isVisible().catch(() => false);
  // Present but disabled is not "not found": it is Teams refusing the pre-join. Terminal (#1780).
  if (joinNowVisible) await assertTeamsJoinNowEnabled(page, joinNowButton, botName);
  try {
    if (joinNowVisible) {
      await joinNowButton.click();
      log("✅ Clicked 'Join now' button");
    } else {
      // Fall back to generic join selectors
      const fallbackJoinButton = page.locator(teamsJoinButtonSelectors.join(', ')).first();
      await fallbackJoinButton.waitFor({ timeout: 10000 });
      await fallbackJoinButton.click();
      log("✅ Clicked join button (fallback selector)");
    }
    // Brief wait for Teams to start processing the join request
    await page.waitForTimeout(1000);
  } catch (error: any) {
    // Keep Playwright's reason: "not found" and "found but not clickable" read the same otherwise.
    const reason = String(error?.message || error).split("\n")[0];
    log(`⚠️ Join button not found or not clickable — bot may not be able to enter the meeting (${reason})`);
  }

  // Step 6c: Handle the post-"Join now" AV-confirmation modal (Vexa-ai/vexa#467).
  //
  // Teams' anonymous "light meeting" flow pops "Are you sure you don't want
  // audio or video?" AFTER the Join-now click (camera + mic are off). It blocks
  // the join until dismissed, and leaves the pre-join "Join now" button in the
  // DOM — which admission.ts then mistakes for a lobby and loops on forever.
  // Poll briefly: dismiss the modal, re-click "Join now", and stop once we've
  // reached the lobby or been admitted. See modals.ts for the full rationale.
  log("Step 6c: Handling post-join AV-confirmation modal (if shown)...");
  for (let attempt = 0; attempt < 6; attempt++) {
    const dismissed = await dismissTeamsAvConfirmModal(page);
    if (dismissed) {
      const joinAgain = page.locator('button:has-text("Join now")').first();
      if (await joinAgain.isVisible().catch(() => false)) {
        await joinAgain.click().catch(() => {});
        log("✅ Re-clicked 'Join now' after dismissing AV-confirmation modal");
      }
    }

    // Stop as soon as we've left pre-join: lobby reached or already admitted.
    const inLobby = await page
      .locator('text=/Someone will let you in shortly|Waiting for someone to let you in|Waiting to be admitted/i')
      .first()
      .isVisible()
      .catch(() => false);
    const admitted = await page
      .locator('button[id="hangup-button"], button[aria-label="Leave"], button[data-tid="hangup-main-btn"]')
      .first()
      .isVisible()
      .catch(() => false);
    const modalStillThere = await isTeamsAvConfirmModalVisible(page);
    if (inLobby || admitted || (!dismissed && !modalStillThere)) {
      if (inLobby) log("✅ Reached Teams lobby after Join now");
      if (admitted) log("✅ Admitted to Teams meeting after Join now");
      break;
    }
    await page.waitForTimeout(1000);
  }

  // Mute mic for all bots after join. TTS bots unmute only when speaking
  // (handleSpeakCommand unmutes → speaks → re-mutes).
  log("Step 6b: Muting mic...");
  try {
    await page.keyboard.press("Control+Shift+M");
    await page.waitForTimeout(200);
    log("✅ Mic muted via Ctrl+Shift+M");
  } catch (error) {
    log("ℹ️ Could not mute mic via keyboard shortcut");
  }

  log("Step 7: Checking current state...");
}
