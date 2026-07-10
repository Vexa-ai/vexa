import { Page } from "playwright";
import { log, callJoiningCallback } from "../_host";
import { BotConfig } from "../_host";
import { isAdmitted } from "./admission";
import {
  jitsiNameInputSelector,
  jitsiJoinButtonSelector,
  jitsiPasswordInputSelector,
  jitsiGuestEntryTexts,
} from "./selectors";

// NOTE vs the other platforms: Jitsi Meet is SELF-HOSTABLE — meet.jit.si is just
// the canonical public deployment. The join layer therefore never rewrites the
// host: whatever URL the embedder hands in is the deployment we join. Per-speaker
// capture and recording are HOST concerns and stay outside this brick.

/**
 * Build the Jitsi meeting URL the bot navigates to.
 *
 * Input:  https://meet.jit.si/MyRoom            (or any self-hosted host)
 * Output: https://meet.jit.si/MyRoom#config.startWithAudioMuted=true&…
 *
 * Jitsi reads config overrides from URL hash params (`parseURLParams` JSON-parses
 * each value — booleans are bare, strings are double-quoted + URI-encoded). We
 * append:
 *   • config.startWithAudioMuted / startWithVideoMuted — a recorder bot is
 *     receive-only; muting via config is more reliable than clicking the
 *     prejoin toggles across deployment versions.
 *   • userInfo.displayName — so the bot is correctly named even on deployments
 *     that DISABLE the prejoin screen (no name field to type into).
 * Existing hash params are preserved; ours are appended only when absent, so an
 * embedder's explicit overrides always win.
 *
 * The room is the URL path — a deployment may serve rooms at a sub-path
 * (e.g. https://host/jitsi/Room), so any non-empty path is accepted. A bare
 * origin (no room) throws: there is nothing to join.
 */
export function buildJitsiMeetingUrl(
  meetingUrl: string,
  opts: { botName?: string } = {},
): string {
  let url: URL;
  try {
    url = new URL(meetingUrl);
  } catch (err: any) {
    throw new Error(`Invalid Jitsi meeting URL: ${meetingUrl} — ${err.message}`);
  }
  const room = url.pathname.replace(/\/+$/, "");
  if (!room || room === "") {
    throw new Error(`Cannot extract room from Jitsi URL (no path): ${meetingUrl}`);
  }

  const existing = url.hash.startsWith("#") ? url.hash.slice(1) : url.hash;
  const parts = existing ? [existing] : [];
  const has = (key: string): boolean => existing.includes(`${key}=`);

  if (!has("config.startWithAudioMuted")) parts.push("config.startWithAudioMuted=true");
  if (!has("config.startWithVideoMuted")) parts.push("config.startWithVideoMuted=true");
  if (opts.botName && !has("userInfo.displayName")) {
    // JSON string value: double-quoted, then URI-encoded (jitsi decodes + JSON.parses).
    parts.push(`userInfo.displayName=${encodeURIComponent(`"${opts.botName}"`)}`);
  }

  url.hash = parts.join("&");
  return url.toString();
}

/**
 * Clear a deployment's auth landing, if one fronts the app. Some self-hosted
 * deployments show "Sign in to Jitsi" (an SSO button + a guest option) before any
 * jitsi UI mounts; a recorder bot enters as a guest. Polls briefly for a guest-entry
 * button/link and clicks it DOM-direct; a deployment without a landing falls through
 * silently. Returns true if a guest entry was clicked.
 */
async function enterAsGuestIfGated(page: Page): Promise<boolean> {
  const deadline = Date.now() + 6000;
  while (Date.now() < deadline) {
    const clicked = await page.evaluate((phrases: string[]) => {
      const candidates = Array.from(
        document.querySelectorAll('button, a, [role="button"]'),
      ) as HTMLElement[];
      for (const el of candidates) {
        const text = (el.textContent || "").trim().toLowerCase();
        if (text && phrases.some((p) => text.includes(p))) {
          el.click();
          return text;
        }
      }
      return null;
    }, jitsiGuestEntryTexts).catch(() => null);
    if (clicked) {
      log(`[Jitsi] Auth landing detected — entered as guest (clicked "${clicked}")`);
      await page.waitForTimeout(2000);
      return true;
    }
    // No guest affordance yet — if the real jitsi UI is already up, there is no landing.
    const jitsiUiUp = await page.locator(jitsiNameInputSelector).first()
      .isVisible({ timeout: 200 }).catch(() => false);
    if (jitsiUiUp || await isAdmitted(page)) return false;
    await page.waitForTimeout(500);
  }
  return false;
}

/**
 * Handle a password-protected room. Jitsi surfaces the password prompt as a
 * dialog AFTER the join attempt. With a passcode in botConfig we fill + submit;
 * without one we fail fast with a structured reason — the dialog never
 * self-dismisses and the bot would otherwise sit on it until the lobby timeout.
 */
async function handlePasswordPrompt(page: Page, botConfig: BotConfig): Promise<void> {
  const pwField = page.locator(jitsiPasswordInputSelector).first();
  // The prompt arrives over the XMPP round-trip, seconds after the join click on a
  // slow deployment — wait long enough that a supplied passcode is actually used.
  const visible = await pwField
    .waitFor({ state: "visible", timeout: 5000 })
    .then(() => true)
    .catch(() => false);
  if (!visible) return;

  const passcode = (botConfig as any).passcode || "";
  if (!passcode) {
    throw new Error(
      "[Jitsi] password_required: room is password-protected but botConfig.passcode is empty; " +
      "pass the room password to the embedder",
    );
  }
  await pwField.fill(passcode);
  // Submit: Enter is the universal dialog confirm across jitsi versions.
  await page.keyboard.press("Enter");
  log("[Jitsi] Submitted room password");
  await page.waitForTimeout(1000);
}

export async function joinJitsiMeeting(
  page: Page,
  meetingUrl: string,
  botName: string,
  botConfig: BotConfig,
): Promise<void> {
  if (!page) throw new Error("[Jitsi] Page is required for Jitsi join");

  const navUrl = buildJitsiMeetingUrl(meetingUrl, { botName });
  log(`[Jitsi] Navigating to: ${navUrl}`);
  await page.goto(navUrl, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(2000);

  // Notify the host: joining
  await callJoiningCallback(botConfig);

  // Prejoin screen may be disabled per-deployment (config.prejoinConfig.enabled=false)
  // — in that case the app enters the conference (or the lobby) directly and the
  // name is already set via the userInfo.displayName hash param.
  if (await isAdmitted(page)) {
    log("[Jitsi] No prejoin screen — conference already live");
    return;
  }

  // A deployment may front the app with an auth landing ("Sign in …" + a guest
  // option) — clear it before waiting on any jitsi UI.
  await enterAsGuestIfGated(page);

  // waitFor (NOT isVisible — which returns the instantaneous state and ignores its
  // timeout): the prejoin screen mounts a beat after navigation / the auth landing.
  const nameField = page.locator(jitsiNameInputSelector).first();
  const havePrejoin = await nameField
    .waitFor({ state: "visible", timeout: 15000 })
    .then(() => true)
    .catch(() => false);
  if (havePrejoin) {
    // Fill the display name with REAL keyboard events — jitsi's prejoin is a React
    // form; typing (not a synthetic value-set) reliably enables the join button.
    const current = await nameField.inputValue().catch(() => "");
    if (current !== botName) {
      await nameField.click({ timeout: 5000 }).catch(() => {});
      await nameField.fill("");
      await page.keyboard.type(botName, { delay: 30 });
    }
    log(`[Jitsi] Name entered: "${botName}"`);

    // Click "Join meeting" DOM-direct first (immune to overlay hit-test stalls),
    // with a Playwright click as the fallback.
    const clicked = await page.evaluate((sel: string) => {
      const btn = document.querySelector(sel) as HTMLElement | null;
      if (!btn) return false;
      btn.click();
      return true;
    }, jitsiJoinButtonSelector.split(",")[0].trim()).catch(() => false);
    if (!clicked) {
      const joinBtn = page.locator(jitsiJoinButtonSelector).first();
      await joinBtn.waitFor({ state: "visible", timeout: 10000 });
      await joinBtn.click({ timeout: 10000 });
    }
    log("[Jitsi] Join clicked — waiting for the conference to load...");
  } else {
    // No prejoin and not yet in conference: the app may still be booting, or a
    // password/lobby dialog is up. Fall through — admission handles the wait;
    // the password check below handles the dialog.
    log("[Jitsi] No prejoin name field detected — proceeding to admission checks");
  }

  await page.waitForTimeout(2000);

  // Password-protected room: the prompt appears after the join attempt.
  await handlePasswordPrompt(page, botConfig);
}
