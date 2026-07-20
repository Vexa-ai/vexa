import { Page } from "playwright";
import { log, callAwaitingAdmissionCallback } from "../_host";
import { BotConfig } from "../_host";
import { checkEscalation, triggerEscalation, getEscalationExtensionMs } from "../shared/escalation";
import { fillPasswordPromptIfPresent } from "./password";
import { AdmissionError } from "../shared/admission";
import {
  jitsiHangupButtonSelectors,
  jitsiConferenceIndicators,
  jitsiPrejoinScreenSelectors,
  jitsiLobbyIndicators,
  jitsiLobbyTexts,
  jitsiRejectionTexts,
  jitsiRemovalTexts,
} from "./selectors";

/** The app's own runtime verdict — the SAME probe join/admission/removal all trust.
 *  jitsi-meet exposes the APP global on every stock deployment; "no-api" = a custom
 *  build stripped it, so the caller falls back to DOM signals. */
export async function getAppJoinedState(page: Page): Promise<"joined" | "not-joined" | "no-api"> {
  return await page.evaluate(() => {
    try {
      const app = (globalThis as any).APP;
      if (app?.conference?.isJoined) return app.conference.isJoined() === true ? "joined" : "not-joined";
      return "no-api";
    } catch { return "no-api"; }
  }).catch(() => "no-api") as "joined" | "not-joined" | "no-api";
}

/** A fresh meet.jit.si room is members-only until a moderator arrives: the conference join fails
 *  with MEMBERS_ONLY, which jitsi reliably emits to the page console (`conference.connectionError.
 *  membersOnly … <room>@lobby.<host>`) — observed on every run. We latch that host-side (below)
 *  because it fires even when the DOM renders NO lobby affordances (a blank/error page) and the
 *  redux store isn't wired up yet pre-admission — the exact state that made a real lobby look like
 *  "unknown" and drove the illegal `joining → needs_help` escalation (#592). Attach BEFORE
 *  navigation (from join.ts) so the event is never missed; the flag lives on the page object. */
const MEMBERS_ONLY_RE = /conference\.connectionError\.membersOnly|MEMBERS_ONLY/i;

export function attachMembersOnlyWatch(page: Page): void {
  const p = page as any;
  if (p.__vexaMembersOnlyWatch) return; // idempotent per page
  p.__vexaMembersOnlyWatch = true;
  page.on("console", (msg) => {
    try { if (MEMBERS_ONLY_RE.test(msg.text())) p.__vexaMembersOnly = true; } catch { /* noop */ }
  });
}

function membersOnlyLatched(page: Page): boolean {
  return (page as any).__vexaMembersOnly === true;
}

/** The app's own lobby verdict via redux — a secondary signal (self-hosted / explicit-Lobby
 *  deployments where the store IS populated). On stock meet.jit.si the members-only latch above
 *  is the primary signal; this returns "no-api" pre-admission when the store isn't wired. */
export async function getLobbyState(page: Page): Promise<"lobby" | "not-lobby" | "no-api"> {
  return await page.evaluate(() => {
    try {
      const app = (globalThis as any).APP;
      const state = app?.store?.getState?.();
      if (!state) return "no-api";
      const conf = state["features/base/conference"];
      if (conf && conf.membersOnly) return "lobby"; // members-only lobby room JID when locked out
      const lobby = state["features/lobby"];
      if (lobby && lobby.knocking) return "lobby"; // explicit Lobby feature: knocking to be let in
      return "not-lobby";
    } catch { return "no-api"; }
  }).catch(() => "no-api") as "lobby" | "not-lobby" | "no-api";
}

/** The hangup control is footer-only — never rendered on the prejoin or lobby screens. */
export async function isHangupVisible(page: Page): Promise<boolean> {
  for (const sel of jitsiHangupButtonSelectors) {
    if (await page.locator(sel).first().isVisible({ timeout: 300 }).catch(() => false)) return true;
  }
  return false;
}

/**
 * Check if the bot is confirmed inside the conference.
 *
 * Primary:   `getAppJoinedState` — the app's own verdict; it cannot false-positive
 *            on lobby/prejoin (isJoined() is false while knocking).
 * Fallback1: a hangup control is visible (never on prejoin/lobby screens).
 * Fallback2: conference stage present (#largeVideoContainer) AND no prejoin or
 *            lobby indicators — for custom builds that strip both the APP
 *            global and the stock hangup classes. The stage alone is NOT
 *            sufficient (some builds mount it behind the lobby screen), hence
 *            the exclusions.
 */
export async function isAdmitted(page: Page): Promise<boolean> {
  try {
    const viaApp = await getAppJoinedState(page);
    if (viaApp === "joined") return true;
    if (viaApp === "not-joined") return false; // authoritative negative — skip DOM guesswork

    // APP global absent (custom build) — DOM fallbacks.
    if (await isHangupVisible(page)) return true;

    const inLobby = await isInLobby(page);
    if (inLobby) return false;

    const prejoinPresent = await page.evaluate((sels: string[]) => {
      return sels.some((s) => !!document.querySelector(s));
    }, jitsiPrejoinScreenSelectors).catch(() => true);
    if (prejoinPresent) return false;

    for (const sel of jitsiConferenceIndicators) {
      if (await page.locator(sel).first().isVisible({ timeout: 300 }).catch(() => false)) return true;
    }
    return false;
  } catch {
    return false;
  }
}

/** Check if the bot is on the lobby (knocking) screen or a waiting-for-host dialog. */
async function isInLobby(page: Page): Promise<boolean> {
  try {
    // Authoritative first: the members-only conference-failure latched from the page console.
    // Reliable even on the blank/error render where the DOM affordances never appear — the
    // render that made a real lobby look like "unknown" and took the broken escalation path (#592).
    if (membersOnlyLatched(page)) return true;

    // Secondary: the app's own redux lobby verdict (self-hosted / explicit-Lobby deployments).
    if ((await getLobbyState(page)) === "lobby") return true;

    // DOM fallback: explicit lobby/knock screens + APP-stripped custom builds.
    for (const sel of jitsiLobbyIndicators) {
      const visible = await page.locator(sel).first().isVisible({ timeout: 200 }).catch(() => false);
      if (visible) return true;
    }
    return await page.evaluate((texts: string[]) => {
      const bodyText = document.body?.innerText || "";
      return texts.some((t) => bodyText.toLowerCase().includes(t.toLowerCase()));
    }, jitsiLobbyTexts).catch(() => false);
  } catch {
    return false;
  }
}

/** Detect a lobby decline, a kick, or a terminated conference (terminal states). */
async function isRejectedOrEnded(page: Page): Promise<string | null> {
  try {
    return await page.evaluate((texts: string[]) => {
      const bodyText = (document.body?.innerText || "").toLowerCase();
      for (const t of texts) if (bodyText.includes(t.toLowerCase())) return t;
      return null;
    }, [...jitsiRejectionTexts, ...jitsiRemovalTexts]).catch(() => null);
  } catch {
    return null;
  }
}

export async function waitForJitsiMeetingAdmission(
  page: Page,
  timeoutMs: number,
  botConfig: BotConfig,
): Promise<boolean> {
  if (!page) throw new Error("[Jitsi] Page required for admission check");

  log("[Jitsi] Checking admission state...");

  // Fast path: rooms without a lobby admit immediately.
  if (await isAdmitted(page)) {
    log("[Jitsi] Bot immediately admitted (no lobby)");
    return true;
  }

  const inLobby = await isInLobby(page);
  if (inLobby) {
    log("[Jitsi] Bot is in the lobby — waiting for a moderator to admit");
    try {
      await callAwaitingAdmissionCallback(botConfig);
    } catch (e: any) {
      log(`[Jitsi] Warning: awaiting_admission callback failed: ${e.message}`);
    }
  }

  // Poll loop
  const startTime = Date.now();
  const pollInterval = 2000;
  let unknownStateDuration = 0;
  const effectiveTimeout = () => timeoutMs + getEscalationExtensionMs();

  while (Date.now() - startTime < effectiveTimeout()) {
    await page.waitForTimeout(pollInterval);

    const terminal = await isRejectedOrEnded(page);
    if (terminal) {
      log(`[Jitsi] Terminal state during admission wait (matched: "${terminal}")`);
      // A lobby decline / kick is a permanent host verdict — a typed denial, not a transient
      // join_failure — so the driver records `awaiting_admission_rejected`, not a retry.
      throw new AdmissionError("denial", `Bot was rejected from the Jitsi meeting or meeting ended (matched: "${terminal}")`);
    }

    if (await isAdmitted(page)) {
      log("[Jitsi] Bot admitted — conference is live");
      return true;
    }

    // The room-password dialog arrives over the XMPP round-trip and may land
    // DURING this wait (after join.ts's early 5s check) — answer it here too.
    // Idempotent: fills only if the dialog is present and not already submitted;
    // throws password_required (fail fast) if no passcode was supplied.
    const pwResult = await fillPasswordPromptIfPresent(page, botConfig);
    if (pwResult === "submitted") {
      log("[Jitsi] Password dialog appeared during admission wait — submitted passcode");
      continue; // re-check admission promptly after the submit
    }

    // Track unknown state (neither admitted, nor lobby, nor terminal)
    const inLobbyNow = await isInLobby(page);
    if (!inLobbyNow) {
      unknownStateDuration += pollInterval;
    } else {
      unknownStateDuration = 0;
    }

    // Escalation check (VNC/human-help surfacing — same policy as the other platforms)
    const elapsedMs = Date.now() - startTime;
    const escalation = checkEscalation(elapsedMs, timeoutMs, unknownStateDuration);
    if (escalation) {
      await triggerEscalation(botConfig, escalation.reason);
    }

    const elapsed = Math.round(elapsedMs / 1000);
    log(`[Jitsi] Still waiting for admission... ${elapsed}s elapsed`);
  }

  // Timed out waiting to be let in. A typed `lobby_timeout` (not a bare Error) so the driver
  // reports `awaiting_admission_timeout` / stage=awaiting_admission — an honest terminal reason
  // — instead of collapsing to a generic, silently-retried `join_failure` (#592).
  throw new AdmissionError("lobby_timeout", `[Jitsi] Bot not admitted within ${effectiveTimeout()}ms timeout`);
}

export async function checkForJitsiAdmissionSilent(page: Page): Promise<boolean> {
  if (!page) return false;
  // Retry with a short delay — the jitsi UI briefly unmounts controls during
  // layout transitions (filmstrip resize, notification stacking) after admission.
  for (let attempt = 0; attempt < 3; attempt++) {
    if (await isAdmitted(page)) return true;
    if (attempt < 2) {
      await page.waitForTimeout(1000);
    }
  }
  return false;
}
