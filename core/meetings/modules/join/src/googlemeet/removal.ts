import { Page } from "playwright";
import { log } from "../_host";
import { googleRemovalIndicators } from "./selectors";
import { countRealParticipantTiles } from "./admission";

// Function to check if bot has been removed from the meeting
export async function checkForGoogleRemoval(page: Page): Promise<boolean> {
  try {
    // Check for removal indicators
    for (const selector of googleRemovalIndicators) {
      try {
        const element = await page.locator(selector).first();
        if (await element.isVisible()) {
          log(`🚨 Google Meet removal detected: Found removal indicator "${selector}"`);
          return true;
        }
      } catch (e) {
        // Continue checking other selectors
        continue;
      }
    }
    return false;
  } catch (error: any) {
    log(`Error checking for Google Meet removal: ${error.message}`);
    return false;
  }
}

// Start periodic removal monitoring from Node.js side
export function startGoogleRemovalMonitor(page: Page, onRemoval?: () => void | Promise<void>): () => void {
  log("Starting periodic Google Meet removal monitoring...");
  let removalDetected = false;
  
  const removalCheckInterval = setInterval(async () => {
    try {
      const isRemoved = await checkForGoogleRemoval(page);
      if (isRemoved && !removalDetected) {
        removalDetected = true; // Prevent duplicate detection
        log("🚨 Google Meet removal detected from Node.js side. Initiating graceful shutdown...");
        clearInterval(removalCheckInterval);
        
        try {
          // Attempt to click any dismiss buttons to close the modal gracefully
          await page.evaluate(() => {
            const clickIfVisible = (el: HTMLElement | null) => {
              if (!el) return;
              const rect = el.getBoundingClientRect();
              const cs = getComputedStyle(el);
              if (rect.width > 0 && rect.height > 0 && cs.display !== 'none' && cs.visibility !== 'hidden') {
                el.click();
              }
            };
            const btns = Array.from(document.querySelectorAll('button')) as HTMLElement[];
            for (const b of btns) {
              const t = (b.textContent || b.innerText || '').trim().toLowerCase();
              const a = (b.getAttribute('aria-label') || '').toLowerCase();
              if (t === 'dismiss' || a.includes('dismiss') || t === 'ok' || a.includes('ok')) { 
                clickIfVisible(b); 
                break; 
              }
            }
          });
        } catch {}
        
        // Signal removal to caller
        try { await onRemoval?.(); } catch {}
      }
    } catch (error: any) {
      log(`Error during Google Meet removal check: ${error.message}`);
    }
  }, 1500);

  // Return cleanup function
  return () => {
    clearInterval(removalCheckInterval);
  };
}

/** Alone-in-meeting watcher (the missing half of `automaticLeave.everyoneLeftTimeout`):
 *  the timeout was plumbed through invocation.v1 and consumed ONLY by the 4h max-active
 *  backstop derivation — no detector existed, so a bot whose host left simply sat in the
 *  empty meeting billing minutes until a manual stop (owner rounds 4–6). Polls the SAME
 *  real-participant-tile counter admission trusts (effects phantom excluded; the bot is
 *  itself one tile, so alone means count <= 1). Continuous aloneness for `timeoutMs`
 *  fires `onEveryoneLeft` ONCE; company re-arriving resets the clock. Count errors
 *  (navigation, teardown) reset the clock too — never a spurious leave on a flaky read. */
export function startGoogleAlonenessMonitor(
  page: Page,
  timeoutMs: number,
  onEveryoneLeft: () => void | Promise<void>,
  pollMs = 10_000,
  counter: (page: Page) => Promise<number> = countRealParticipantTiles,
): () => void {
  log(`Starting alone-in-meeting monitoring (leave after ${Math.round(timeoutMs / 1000)}s alone)...`);
  let aloneSinceMs: number | null = null;
  let fired = false;

  const interval = setInterval(async () => {
    if (fired) return;
    let real: number;
    try {
      real = await counter(page);
    } catch {
      aloneSinceMs = null; // flaky read — never a spurious leave
      return;
    }
    if (real > 1) {
      if (aloneSinceMs !== null) log("Company returned — aloneness clock reset.");
      aloneSinceMs = null;
      return;
    }
    const now = Date.now();
    if (aloneSinceMs === null) {
      aloneSinceMs = now;
      log(`Alone in the meeting (${real} tile) — leaving in ${Math.round(timeoutMs / 1000)}s unless company returns.`);
      return;
    }
    if (now - aloneSinceMs >= timeoutMs) {
      fired = true;
      clearInterval(interval);
      log("🕐 Alone past the everyone-left timeout — initiating graceful leave...");
      try { await onEveryoneLeft(); } catch { /* the caller owns failure handling */ }
    }
  }, pollMs);

  return () => clearInterval(interval);
}
