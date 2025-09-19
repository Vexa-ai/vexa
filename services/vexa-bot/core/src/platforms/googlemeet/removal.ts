import { Page } from "playwright";
import { log } from "../../utils";
import { googleRemovalIndicators } from "./selectors";

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
