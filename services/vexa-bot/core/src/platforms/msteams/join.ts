import { Page } from "playwright";
import * as fs from "fs";
import * as path from "path";
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

/**
 * Snapshot the Teams pre-join page when Step 6 fails so we can write the
 * correct selector instead of guessing. Writes a JSON file next to the bot
 * log (PROCESS_LOGS_DIR or /tmp/vexa-bots) plus a PNG screenshot. Best-effort
 * only — never throws.
 */
async function dumpTeamsPreJoinDiagnostics(page: Page, label: string): Promise<void> {
  try {
    const dir = process.env.PROCESS_LOGS_DIR || "/tmp/vexa-bots";
    try { fs.mkdirSync(dir, { recursive: true }); } catch {}
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    const base = path.join(dir, `teams-prejoin-${label}-${stamp}`);

    const snapshot = await page.evaluate(() => {
      const isVisible = (el: Element): boolean => {
        const node = el as HTMLElement;
        const r = node.getBoundingClientRect();
        const s = window.getComputedStyle(node);
        return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none" && s.opacity !== "0";
      };
      const out: any[] = [];
      const nodes = document.querySelectorAll('button, [role="button"], input, a, [data-tid]');
      nodes.forEach((el) => {
        if (!isVisible(el)) return;
        const node = el as HTMLElement;
        out.push({
          tag: node.tagName.toLowerCase(),
          text: (node.innerText || "").replace(/\s+/g, " ").trim().slice(0, 120),
          aria: node.getAttribute("aria-label"),
          tid: node.getAttribute("data-tid"),
          role: node.getAttribute("role"),
          id: node.id || null,
          type: node.getAttribute("type"),
          placeholder: node.getAttribute("placeholder"),
          disabled: node.hasAttribute("disabled") || node.getAttribute("aria-disabled") === "true",
        });
      });
      return { url: location.href, title: document.title, count: out.length, items: out };
    });

    fs.writeFileSync(`${base}.json`, JSON.stringify(snapshot, null, 2));
    log(`📸 [Teams Diagnostics] DOM snapshot written: ${base}.json (count=${snapshot.count}, url=${snapshot.url})`);

    try {
      await page.screenshot({ path: `${base}.png`, fullPage: true });
      log(`📸 [Teams Diagnostics] Screenshot written: ${base}.png`);
    } catch (shotErr: any) {
      log(`ℹ️ [Teams Diagnostics] Screenshot failed: ${shotErr?.message || shotErr}`);
    }
  } catch (err: any) {
    log(`ℹ️ [Teams Diagnostics] dumpTeamsPreJoinDiagnostics failed: ${err?.message || err}`);
  }
}

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

async function dismissTeamsNoMediaConfirmation(page: Page, context: string): Promise<boolean> {
  const noMediaButtonNamePattern = /Continue without audio or video|Ohne Audio oder Video fortfahren|Fortfahren ohne Audio oder Video/i;
  const noMediaConfirmSelectors = [
    'button:has-text("Continue without audio or video")',
    'button:has-text("Ohne Audio oder Video fortfahren")',
    'button:has-text("Fortfahren ohne Audio oder Video")',
    '[role="dialog"] button:has-text("Continue without audio or video")',
    '[role="dialog"] button:has-text("Ohne Audio oder Video fortfahren")',
    '[role="dialog"] button:has-text("Fortfahren ohne Audio oder Video")',
    'button[aria-label*="Continue without audio or video" i]',
    'button[aria-label*="Ohne Audio oder Video fortfahren" i]',
    'button[aria-label*="Fortfahren ohne Audio oder Video" i]'
  ];

  const isStillVisible = async (): Promise<boolean> => {
    return page
      .getByText(/Are you sure you don't want audio or video|Continue without audio or video|Ohne Audio oder Video fortfahren|Fortfahren ohne Audio oder Video/i)
      .first()
      .isVisible()
      .catch(() => false);
  };

  const clickAndVerify = async (label: string, click: () => Promise<void>): Promise<boolean> => {
    try {
      await click();
      await page.waitForTimeout(700);
      if (!(await isStillVisible())) {
        log(`✅ Confirmed Teams no-media dialog (${context}, ${label})`);
        return true;
      }
      log(`ℹ️ Teams no-media dialog still visible after click (${context}, ${label}); trying another method`);
    } catch (err: any) {
      log(`ℹ️ No-media dialog click failed (${context}, ${label}): ${err?.message || err}`);
    }
    return false;
  };

  const roleButton = page.getByRole("button", { name: noMediaButtonNamePattern }).first();
  if (await roleButton.isVisible().catch(() => false)) {
    if (await clickAndVerify("role=button", () => roleButton.click({ timeout: 3000, force: true }))) {
      return true;
    }
  }

  for (const selector of noMediaConfirmSelectors) {
    const button = page.locator(selector).first();
    const visible = await button.isVisible().catch(() => false);
    if (!visible) continue;

    if (await clickAndVerify(`selector=${selector}`, () => button.click({ timeout: 3000, force: true }))) {
      return true;
    }
  }

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
    const pattern = /Continue without audio or video|Ohne Audio oder Video fortfahren|Fortfahren ohne Audio oder Video/i;
    const candidates = Array.from(document.querySelectorAll('button, [role="button"]'));

    for (const el of candidates) {
      if (!isVisible(el)) continue;
      const node = el as HTMLElement;
      const label = normalize(node.innerText || node.getAttribute("aria-label"));
      if (!pattern.test(label)) continue;

      node.click();
      return { clicked: true, label };
    }

    return { clicked: false, label: null as string | null };
  }).catch(() => ({ clicked: false, label: null as string | null }));

  if (fallback.clicked) {
    if (await clickAndVerify(`dom-click label="${fallback.label}"`, async () => {})) {
      return true;
    }
  }

  const clickPoint = await page.evaluate(() => {
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
    const pattern = /Continue without audio or video|Ohne Audio oder Video fortfahren|Fortfahren ohne Audio oder Video/i;
    const candidates = Array.from(document.querySelectorAll('button, [role="button"]'));

    for (const el of candidates) {
      if (!isVisible(el)) continue;
      const node = el as HTMLElement;
      const label = normalize(node.innerText || node.getAttribute("aria-label"));
      if (!pattern.test(label)) continue;
      const rect = node.getBoundingClientRect();
      return {
        x: rect.left + rect.width / 2,
        y: rect.top + rect.height / 2,
        label
      };
    }

    return null as { x: number; y: number; label: string } | null;
  }).catch(() => null as { x: number; y: number; label: string } | null);

  if (clickPoint) {
    if (await clickAndVerify(`mouse-click label="${clickPoint.label}"`, () => page.mouse.click(clickPoint.x, clickPoint.y))) {
      return true;
    }
  }

  return false;
}

async function waitAndDismissTeamsNoMediaConfirmation(page: Page, context: string, timeoutMs: number): Promise<boolean> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (await dismissTeamsNoMediaConfirmation(page, context)) {
      return true;
    }
    await page.waitForTimeout(300);
  }
  return false;
}

async function waitForTeamsPreJoinReadiness(page: Page, timeoutMs: number): Promise<boolean> {
  const start = Date.now();
  let mediaWarmupAttempted = false;
  let continueClickAttempts = 0;

  while (Date.now() - start < timeoutMs) {
    if (await dismissTeamsNoMediaConfirmation(page, "pre-join readiness")) {
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
      await dismissTeamsNoMediaConfirmation(page, "after media warm-up");
    }

    await page.waitForTimeout(300);
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
    await page.waitForTimeout(300);
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
      await page.waitForTimeout(200);
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
  await page.waitForTimeout(200);

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
  
  // Fix 2: Propagate JOINING callback failure — bot must NOT proceed if server rejected
  await callJoiningCallback(botConfig);
  log("Joining callback sent successfully");

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
  await waitForTeamsPreJoinReadiness(page, 45000);
  await dismissTeamsNoMediaConfirmation(page, "after pre-join readiness");

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
          await page.waitForTimeout(300);
          turnOnVisible = await turnOnBtn.isVisible().catch(() => false);
          turnOffVisible = await turnOffBtn.isVisible().catch(() => false);
        }
      }

      if (turnOnVisible) {
        await turnOnBtn.click();
        log("✅ Camera/video turned ON for voice agent");
        await page.waitForTimeout(300);
      } else if (turnOffVisible) {
        log("ℹ️ Camera/video already ON");
      } else {
        const videoOptionsVisible = await videoOptionsBtn.isVisible().catch(() => false);
        if (videoOptionsVisible) {
          log("ℹ️ Only video options control is visible; trying keyboard toggle as fallback...");
          await page.keyboard.press("Control+Shift+O").catch(() => {});
          await page.waitForTimeout(300);
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
  let joinClicked = false;
  try {
    await waitAndDismissTeamsNoMediaConfirmation(page, "before Join click", 1500);

    // Try every known Join selector in order, picking the first visible match.
    for (const selector of teamsJoinButtonSelectors) {
      const candidate = page.locator(selector).first();
      const visible = await candidate.isVisible().catch(() => false);
      if (!visible) continue;
      try {
        await candidate.click({ timeout: 5000 });
        log(`✅ Clicked join button (selector=${selector})`);
        joinClicked = true;
        break;
      } catch (clickErr: any) {
        log(`ℹ️ Visible Join candidate failed to click (selector=${selector}): ${clickErr?.message || clickErr}`);
        if (await waitAndDismissTeamsNoMediaConfirmation(page, `retry Join selector ${selector}`, 4000)) {
          await candidate.click({ timeout: 5000 });
          log(`✅ Clicked join button after no-media dialog dismissal (selector=${selector})`);
          joinClicked = true;
          break;
        }
      }
    }

    if (!joinClicked) {
      // Last resort: wait for any join selector to appear, then click.
      const fallbackJoinButton = page.locator(teamsJoinButtonSelectors.join(', ')).first();
      await fallbackJoinButton.waitFor({ timeout: 10000 });
      await waitAndDismissTeamsNoMediaConfirmation(page, "before fallback Join click", 1500);
      await fallbackJoinButton.click({ timeout: 5000 });
      log("✅ Clicked join button (fallback wait selector)");
      joinClicked = true;
    }

    // Brief wait for Teams to start processing the join request
    await page.waitForTimeout(1000);

    // Step 6a: If Teams shows the "Continue without audio or video" confirmation
    // dialog (happens when getUserMedia returned no devices, e.g. on the
    // light-meetings page in containerized Edge), confirm it so the join
    // request actually proceeds.
    const noMediaConfirmed = await waitAndDismissTeamsNoMediaConfirmation(page, "after Join click", 12000);
    if (noMediaConfirmed) {
      await page.waitForTimeout(1000);
      const joinStillVisible = await page.locator(teamsJoinButtonSelectors.join(', ')).first().isVisible().catch(() => false);
      if (joinStillVisible) {
        log("ℹ️ Join button still visible after no-media confirmation; clicking Join now again...");
        await page.locator(teamsJoinButtonSelectors.join(', ')).first().click({ timeout: 5000 });
        await waitAndDismissTeamsNoMediaConfirmation(page, "after retry Join click", 8000);
      }
    }
  } catch (error: any) {
    log(`⚠️ Join button not found — Step 6 failed: ${error?.message || error}`);
    await dumpTeamsPreJoinDiagnostics(page, "step6-join-not-found").catch(() => {});
    // Fail fast so admission.ts does not enter a fake "in lobby" loop on the
    // still-visible pre-join screen.
    throw new Error(`Teams Step 6 join click failed: ${error?.message || error}`);
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
