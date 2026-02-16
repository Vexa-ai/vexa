import { Page } from "playwright";
import { log, callJoiningCallback } from "../../utils";
import { BotConfig } from "../../types";
import {
  teamsContinueButtonSelectors,
  teamsJoinButtonSelectors,
  teamsCameraButtonSelectors,
  teamsVideoOptionsButtonSelectors,
  teamsVirtualCameraOptionSelectors,
  teamsNameInputSelectors,
  teamsComputerAudioRadioSelectors,
  teamsDontUseAudioRadioSelectors,
  teamsSpeakerEnableSelectors,
  teamsSpeakerDisableSelectors
} from "./selectors";

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

async function waitForTeamsPreJoinReadiness(page: Page, timeoutMs: number): Promise<boolean> {
  const start = Date.now();
  let mediaWarmupAttempted = false;
  let continueClickAttempts = 0;

  while (Date.now() - start < timeoutMs) {
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
      await page.waitForTimeout(1200);
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

    await page.waitForTimeout(1000);
  }

  const finalUrl = page.url();
  log(`⚠️ Timed out waiting for Teams pre-join readiness after ${timeoutMs}ms (url=${finalUrl})`);
  return false;
}

async function trySelectCameraFromVideoOptions(page: Page): Promise<boolean> {
  const videoOptionsBtn = page.locator(teamsVideoOptionsButtonSelectors.join(", ")).first();
  const optionsVisible = await videoOptionsBtn.isVisible().catch(() => false);
  if (!optionsVisible) return false;

  try {
    const label = await videoOptionsBtn.getAttribute("aria-label");
    await videoOptionsBtn.click({ force: true });
    log(`ℹ️ Opened Teams video options${label ? ` ("${label}")` : ""}`);
    await page.waitForTimeout(700);
  } catch (err: any) {
    log(`ℹ️ Failed to open Teams video options: ${err?.message || err}`);
    return false;
  }

  try {
    const vexaOption = page.locator(teamsVirtualCameraOptionSelectors.join(", ")).first();
    const vexaVisible = await vexaOption.isVisible().catch(() => false);
    if (vexaVisible) {
      await vexaOption.click({ force: true });
      log('✅ Selected "Vexa Virtual Camera" in video options');
      await page.keyboard.press("Escape").catch(() => {});
      await page.waitForTimeout(600);
      return true;
    }
  } catch {}

  const fallback = await page.evaluate(() => {
    const normalize = (value: string | null | undefined): string =>
      (value || "").replace(/\s+/g, " ").trim();
    const isVisible = (el: Element): boolean => {
      const node = el as HTMLElement;
      const style = window.getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      return (
        rect.width > 0 &&
        rect.height > 0 &&
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        style.opacity !== "0"
      );
    };

    const candidates = Array.from(
      document.querySelectorAll('[role="menuitemradio"], [role="option"], button, [data-tid], [aria-label]')
    );

    for (const el of candidates) {
      if (!isVisible(el)) continue;
      const label = normalize((el as HTMLElement).innerText || el.getAttribute("aria-label"));
      if (!label) continue;
      const lower = label.toLowerCase();
      const isCameraDeviceCandidate =
        lower.includes("camera") &&
        !lower.includes("open video options") &&
        !lower.includes("video options") &&
        !lower.includes("turn on camera") &&
        !lower.includes("turn off camera") &&
        !lower.includes("turn camera on") &&
        !lower.includes("turn camera off") &&
        !lower.includes("turn on video") &&
        !lower.includes("turn off video") &&
        !lower.includes("no camera");
      if (!isCameraDeviceCandidate) continue;

      (el as HTMLElement).click();
      return { selected: true, label };
    }

    return { selected: false, label: null as string | null };
  });

  await page.keyboard.press("Escape").catch(() => {});
  await page.waitForTimeout(600);

  if (fallback.selected) {
    log(`ℹ️ Selected fallback camera option from video menu: "${fallback.label}"`);
    return true;
  }

  log("ℹ️ Video options opened but no camera device option was selectable");
  return false;
}

export async function joinMicrosoftTeams(page: Page, botConfig: BotConfig): Promise<void> {
  // Install RTCPeerConnection hook before any Teams scripts run - ensures remote audio tracks
  // are mirrored into hidden <audio> elements that BrowserAudioService can capture later.
  await page.addInitScript(() => {
    try {
      const win = window as any;
      if (win.__vexaRemoteAudioHookInstalled || typeof RTCPeerConnection !== 'function') {
        return;
      }

      win.__vexaRemoteAudioHookInstalled = true;
      win.__vexaInjectedAudioElements = win.__vexaInjectedAudioElements || [];
      const OriginalPC = RTCPeerConnection;

      function wrapPeerConnection(this: any, ...args: any[]) {
        const pc: RTCPeerConnection = new (OriginalPC as any)(...args);

        const handleTrack = (event: RTCTrackEvent) => {
          try {
            if (!event.track || event.track.kind !== 'audio') {
              return;
            }

            const stream = (event.streams && event.streams[0]) || new MediaStream([event.track]);

            const audioEl = document.createElement('audio');
            audioEl.autoplay = true;
            audioEl.muted = false;
            audioEl.volume = 1.0;
            audioEl.dataset.vexaInjected = 'true';
            audioEl.style.position = 'absolute';
            audioEl.style.left = '-9999px';
            audioEl.style.width = '1px';
            audioEl.style.height = '1px';
            audioEl.srcObject = stream;
            audioEl.play?.().catch(() => {});

            if (document.body) {
              document.body.appendChild(audioEl);
            } else {
              document.addEventListener('DOMContentLoaded', () => document.body?.appendChild(audioEl), { once: true });
            }

            (win.__vexaInjectedAudioElements as HTMLAudioElement[]).push(audioEl);
            win.__vexaCapturedRemoteAudioStreams = win.__vexaCapturedRemoteAudioStreams || [];
            win.__vexaCapturedRemoteAudioStreams.push(stream);

            win.logBot?.(`[Audio Hook] Injected remote audio element (track=${event.track.id}, readyState=${event.track.readyState}).`);
          } catch (hookError) {
            console.error('Vexa audio hook error:', hookError);
          }
        };

        pc.addEventListener('track', handleTrack);

        const originalOnTrack = Object.getOwnPropertyDescriptor(OriginalPC.prototype, 'ontrack');
        if (originalOnTrack && originalOnTrack.set) {
          Object.defineProperty(pc, 'ontrack', {
            set(handler: any) {
              if (typeof handler !== 'function') {
                return originalOnTrack.set!.call(this, handler);
              }
              const wrapped = function (this: RTCPeerConnection, event: RTCTrackEvent) {
                handleTrack(event);
                return handler.call(this, event);
              };
              return originalOnTrack.set!.call(this, wrapped);
            },
            get: originalOnTrack.get,
            configurable: true,
            enumerable: true
          });
        }

        return pc;
      }

      wrapPeerConnection.prototype = OriginalPC.prototype;
      Object.setPrototypeOf(wrapPeerConnection, OriginalPC);
      (window as any).RTCPeerConnection = wrapPeerConnection as any;

      win.logBot?.('[Audio Hook] RTCPeerConnection patched to mirror remote audio tracks.');
    } catch (initError) {
      console.error('Failed to install Vexa audio hook:', initError);
    }
  });

  // Step 1: Navigate to Teams meeting
  log(`Step 1: Navigating to Teams meeting: ${botConfig.meetingUrl}`);
  await page.goto(botConfig.meetingUrl!, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(500);
  
  try {
    await callJoiningCallback(botConfig);
    log("Joining callback sent successfully");
  } catch (callbackError: any) {
    log(`Warning: Failed to send joining callback: ${callbackError.message}. Continuing with join process...`);
  }

  log("Step 2: Looking for 'Continue on this browser' button...");
  try {
    const continueButton = page.locator(teamsContinueButtonSelectors[0]).first();
    await continueButton.waitFor({ timeout: 10000 });
    await continueButton.click();
    log("✅ Clicked 'Continue on this browser' button");
    // Wait for the pre-join page to fully load (camera preview, audio settings, name input)
    await page.waitForTimeout(3000);
  } catch (error) {
    log("ℹ️ Continue button not found, continuing...");
  }

  log("Step 2.5: Waiting for Teams pre-join controls...");
  await waitForTeamsPreJoinReadiness(page, 45000);

  // NOTE: Steps 3-5 configure the pre-join screen BEFORE clicking "Join now".
  // The pre-join screen shows camera toggle, name input, and audio settings.
  // We must configure all of these before clicking "Join now" in Step 6.

  log("Step 3: Camera handling...");
  if (botConfig.voiceAgentEnabled) {
    // Voice agent needs camera ON so the virtual camera canvas stream is sent via WebRTC.
    // The getUserMedia + enumerateDevices patches ensure Teams gets our canvas stream.
    // Try to turn camera ON if it's off.
    log("ℹ️ Voice agent enabled — keeping camera ON for virtual camera feed");
    try {
      const turnOnBtn = page.locator([
        'button[aria-label="Turn on video"]',
        'button[aria-label="Turn on camera"]',
        'button[aria-label="Turn camera on"]',
        'button[aria-label="Turn video on"]'
      ].join(', ')).first();
      const turnOffBtn = page.locator([
        'button[aria-label="Turn off video"]',
        'button[aria-label="Turn off camera"]',
        'button[aria-label="Turn camera off"]',
        'button[aria-label="Turn video off"]'
      ].join(', ')).first();
      const videoOptionsBtn = page.locator(teamsVideoOptionsButtonSelectors.join(", ")).first();

      let turnOnVisible = await turnOnBtn.isVisible().catch(() => false);
      let turnOffVisible = await turnOffBtn.isVisible().catch(() => false);

      if (!turnOnVisible && !turnOffVisible) {
        const selectedFromVideoOptions = await trySelectCameraFromVideoOptions(page);
        if (selectedFromVideoOptions) {
          await page.waitForTimeout(800);
          turnOnVisible = await turnOnBtn.isVisible().catch(() => false);
          turnOffVisible = await turnOffBtn.isVisible().catch(() => false);
        }
      }

      if (turnOnVisible) {
        await turnOnBtn.click();
        log("✅ Camera/video turned ON for voice agent");
        await page.waitForTimeout(1000);
      } else if (turnOffVisible) {
        log("ℹ️ Camera/video already ON");
      } else {
        const videoOptionsVisible = await videoOptionsBtn.isVisible().catch(() => false);
        if (videoOptionsVisible) {
          log("ℹ️ Only video options control is visible; trying keyboard toggle as fallback...");
          await page.keyboard.press("Control+Shift+O").catch(() => {});
          await page.waitForTimeout(800);
          const turnOffAfterShortcut = await turnOffBtn.isVisible().catch(() => false);
          if (turnOffAfterShortcut) {
            log("✅ Camera/video turned ON via keyboard shortcut");
          } else {
            log("ℹ️ Video options present but no camera ON state detected after fallback");
          }
        } else {
          log("ℹ️ No camera/video button found — may be unavailable in this container");
        }
      }
    } catch (error) {
      log("ℹ️ Could not enable camera for voice agent");
    }
  } else {
    // Normal bot mode — turn camera off to be unobtrusive
    try {
      const cameraButton = page.locator(teamsCameraButtonSelectors[0]);
      await cameraButton.waitFor({ timeout: 5000 });
      await cameraButton.click();
      log("✅ Camera turned off");
    } catch (error) {
      log("ℹ️ Camera button not found or already off");
    }
  }

  log("Step 4: Trying to set display name...");
  try {
    const nameInput = page.locator(teamsNameInputSelectors.join(', ')).first();
    await nameInput.waitFor({ timeout: 5000 });
    await nameInput.fill(botConfig.botName);
    log(`✅ Display name set to "${botConfig.botName}"`);
  } catch (error) {
    log("ℹ️ Display name input not found, continuing...");
  }

  log("Step 5: Ensuring Computer audio is selected...");
  try {
    await page.waitForTimeout(1000);
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
        await page.waitForTimeout(500);
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
      await page.waitForTimeout(300);
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
  try {
    // Use the more specific "Join now" selector first to avoid ambiguity
    const joinNowButton = page.locator('button:has-text("Join now")').first();
    const joinNowVisible = await joinNowButton.isVisible().catch(() => false);

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
    // Wait for Teams to transition from pre-join to lobby/meeting.
    // Teams needs several seconds to process the join request and show
    // either the waiting room or the meeting view.
    log("Waiting for Teams to process join request...");
    await page.waitForTimeout(8000);
  } catch (error) {
    log("⚠️ Join button not found — bot may not be able to enter the meeting");
  }

  log("Step 7: Checking current state...");
}
