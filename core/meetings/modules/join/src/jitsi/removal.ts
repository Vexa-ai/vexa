import { Page } from "playwright";
import { log } from "../_host";
import {
  jitsiHangupButtonSelectors,
  jitsiRemovalTexts,
  jitsiPostMeetingIndicators,
} from "./selectors";

/**
 * Poll for removal / end-of-meeting. Returns a cleanup fn that stops polling.
 *
 * Signals, strongest first:
 *   1. `APP.conference.isJoined()` flips false — the app's own verdict; covers
 *      kick, conference termination, and connection loss uniformly.
 *   2. Removal/termination dialog text ("kicked out of the meeting", …).
 *   3. Navigation away from the deployment origin (a custom close page).
 *   4. Hangup control gone for N consecutive polls + a post-meeting indicator
 *      ("Rejoin" / feedback page) — the weakest, so it needs both.
 * A grace period suppresses everything while the conference media is still
 * initializing right after admission.
 */
export function startJitsiRemovalMonitor(
  page: Page | null,
  onRemoval?: () => void | Promise<void>,
): () => void {
  if (!page) return () => {};

  let stopped = false;
  let consecutiveHangupMisses = 0;
  let consecutiveNotJoined = 0;
  const HANGUP_MISS_THRESHOLD = 3;   // 3 misses × 3s poll = 9s
  const NOT_JOINED_THRESHOLD = 2;    // isJoined() false twice in a row (6s) — ride out reconnects
  const joinedAtMs = Date.now();
  const GRACE_PERIOD_MS = 20_000;

  const origin = (() => {
    try { return new URL(page.url()).origin; } catch { return null; }
  })();

  const triggerRemoval = async (reason: string) => {
    if (stopped) return;
    stopped = true;
    const elapsed = ((Date.now() - joinedAtMs) / 1000).toFixed(1);
    log(`[Jitsi] REMOVAL TRIGGERED (${elapsed}s after join): ${reason}`);
    log(`[Jitsi] Current URL at removal: ${page.url()}`);
    onRemoval && await onRemoval();
  };

  // Fast path: the app leaves the deployment origin (custom close page redirect).
  const onNavigated = (frame: any) => {
    if (stopped || frame !== page.mainFrame()) return;
    const url: string = frame.url();
    if (!url || url.startsWith("about:")) return;
    if (Date.now() - joinedAtMs < GRACE_PERIOD_MS) return;
    try {
      if (origin && new URL(url).origin !== origin) {
        triggerRemoval(`Navigation away from the Jitsi deployment: ${url}`);
      }
    } catch { /* unparsable URL — leave it to the poll loop */ }
  };
  page.on("framenavigated", onNavigated);

  const poll = async () => {
    if (stopped || !page || page.isClosed()) return;

    try {
      // 1. The app's own verdict. "not-joined" is authoritative but debounced —
      //    jitsi briefly reports false during an ICE restart / visitor-mode move.
      const joinedState = await page.evaluate(() => {
        try {
          const app = (globalThis as any).APP;
          if (app?.conference?.isJoined) return app.conference.isJoined() === true ? "joined" : "not-joined";
          return "no-api";
        } catch { return "no-api"; }
      }).catch(() => "no-api");

      if (joinedState === "not-joined" && Date.now() - joinedAtMs >= GRACE_PERIOD_MS) {
        consecutiveNotJoined++;
        if (consecutiveNotJoined >= NOT_JOINED_THRESHOLD) {
          await triggerRemoval(`APP.conference.isJoined() false ${consecutiveNotJoined}x`);
          return;
        }
      } else if (joinedState === "joined") {
        consecutiveNotJoined = 0;
      }

      // 2. Removal / termination dialog text.
      const detected = await page.evaluate((texts: string[]) => {
        const bodyText = (document.body?.innerText || "").toLowerCase();
        return texts.find((t) => bodyText.includes(t.toLowerCase())) || null;
      }, jitsiRemovalTexts).catch(() => null);
      if (detected) {
        await triggerRemoval(`Removal detected via text: "${detected}"`);
        return;
      }

      // 3/4. DOM fallback for builds without the APP global: hangup gone for
      //      N consecutive polls, confirmed by a post-meeting indicator.
      if (joinedState === "no-api") {
        let hangupVisible = false;
        for (const sel of jitsiHangupButtonSelectors) {
          if (await page.locator(sel).first().isVisible({ timeout: 300 }).catch(() => false)) {
            hangupVisible = true;
            break;
          }
        }
        if (!hangupVisible && Date.now() - joinedAtMs >= GRACE_PERIOD_MS) {
          consecutiveHangupMisses++;
          if (consecutiveHangupMisses >= HANGUP_MISS_THRESHOLD) {
            for (const sel of jitsiPostMeetingIndicators) {
              const post = await page.locator(sel).first().isVisible({ timeout: 300 }).catch(() => false);
              if (post) {
                await triggerRemoval(`Hangup gone ${consecutiveHangupMisses}x and post-meeting page shown (${sel})`);
                return;
              }
            }
          }
        } else if (hangupVisible) {
          if (consecutiveHangupMisses > 0) {
            log(`[Jitsi] Hangup control recovered after ${consecutiveHangupMisses} miss(es)`);
          }
          consecutiveHangupMisses = 0;
        }
      }
    } catch {
      // Page navigated away or context destroyed
      await triggerRemoval("Exception in removal poll — page likely navigated away");
      return;
    }

    if (!stopped) {
      setTimeout(poll, 3000);
    }
  };

  setTimeout(poll, 3000);

  return () => {
    stopped = true;
    page.off("framenavigated", onNavigated);
    log("[Jitsi] Removal monitor stopped");
  };
}
