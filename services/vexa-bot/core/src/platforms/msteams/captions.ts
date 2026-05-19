import { Page } from "playwright";
import { log } from "../../utils";

/**
 * Enable Teams live captions for the bot's browser session.
 *
 * Captions are per-user — the bot can always enable them for itself
 * regardless of meeting settings. Once enabled, the caption DOM elements
 * (data-tid="author" + data-tid="closed-caption-text") appear in the page
 * and are observed by the caption MutationObserver in recording.ts.
 *
 * Flow: More → Language and speech → Show live captions
 */
export async function enableTeamsLiveCaptions(page: Page): Promise<void> {
  log("[Captions] Attempting to enable Teams live captions...");
  await page.evaluate(() => {
    (window as any).__vexaAcceptanceSignals = {
      ...((window as any).__vexaAcceptanceSignals || {}),
      captions_attempted: true,
      captions_attempted_at: new Date().toISOString(),
    };
  }).catch(() => {});

  // Wait for the meeting UI to stabilize
  await page.waitForTimeout(3000);

  try {
    const inspectCaptionState = async (): Promise<{ enabled: boolean; evidence: string }> => {
      return await page.evaluate(() => {
        if (document.querySelector('[data-tid="closed-caption-renderer-wrapper"], [data-tid="closed-caption-text"]')) {
          return { enabled: true, evidence: 'caption-dom' };
        }
        const items = Array.from(document.querySelectorAll('[role="menuitem"], [role="menuitemcheckbox"], [role="menuitemradio"]'))
          .filter(el => (el as HTMLElement).offsetParent !== null)
          .map(el => (el.textContent || '').trim().toLowerCase());
        const offState = items.find(text =>
          text.includes('turn off live captions') ||
          text.includes('hide live captions') ||
          text.includes('stop live captions')
        );
        return offState
          ? { enabled: true, evidence: `menu-state:${offState}` }
          : { enabled: false, evidence: items.join(' | ') };
      });
    };

    const alreadyEnabled = await inspectCaptionState();
    if (alreadyEnabled.enabled) {
      log(`[Captions] Live captions already enabled (${alreadyEnabled.evidence})`);
      return;
    }

    let lastEvidence = alreadyEnabled.evidence;
    for (let attempt = 1; attempt <= 3; attempt++) {
      // Step 1: Click "More" button in the meeting toolbar.
      const moreButton = page.locator(
        '#callingButtons-showMoreBtn, button[aria-label="More"], button[aria-label="More options"]'
      ).first();
      await moreButton.click({ timeout: 8000 });
      log(`[Captions] Clicked More menu (attempt ${attempt}/3)`);
      await page.waitForTimeout(1000);

      const menuItems = await page.evaluate(() => {
        const items = document.querySelectorAll('[role="menuitem"], [role="menuitemcheckbox"], [role="menuitemradio"]');
        return Array.from(items).map(el => ({
          text: (el.textContent || '').trim().substring(0, 60),
          role: el.getAttribute('role') || '',
          visible: (el as HTMLElement).offsetParent !== null
        })).filter(i => i.visible);
      });
      log(`[Captions] Menu items: ${menuItems.map(i => i.text).join(' | ')}`);

      const enableResult = await page.evaluate(() => {
        const getVisibleItems = () => {
          const items = document.querySelectorAll('[role="menuitem"], [role="menuitemcheckbox"], [role="menuitemradio"]');
          return Array.from(items).filter(el => (el as HTMLElement).offsetParent !== null);
        };

        const clickCaptionsItem = (items: Element[]) => {
          for (const el of items) {
            const text = (el.textContent || '').trim().toLowerCase();
            if (
              text === 'captions' ||
              text === 'show live captions' ||
              text === 'turn on live captions' ||
              (text.includes('captions') && !text.includes('turn off') && !text.includes('hide') && !text.includes('stop'))
            ) {
              (el as HTMLElement).click();
              return (el.textContent || '').trim();
            }
          }
          return null;
        };

        const items = getVisibleItems();
        const direct = clickCaptionsItem(items);
        if (direct) return { clicked: direct, path: 'direct', available: '' };

        for (const el of items) {
          const text = (el.textContent || '').toLowerCase();
          if (text.includes('language') && text.includes('speech')) {
            (el as HTMLElement).click();
            return { clicked: (el.textContent || '').trim(), path: 'submenu', available: '' };
          }
        }

        return { clicked: null, path: 'none', available: items.map(el => (el.textContent || '').trim()).join(' | ') };
      });

      if (!enableResult.clicked) {
        lastEvidence = (enableResult as any).available || lastEvidence;
        await page.keyboard.press('Escape').catch(() => {});
        await page.waitForTimeout(1000);
        continue;
      }

      log(`[Captions] Clicked: "${enableResult.clicked}" (${enableResult.path})`);
      await page.waitForTimeout(1000);

      if (enableResult.path === 'submenu') {
        const clickedSub = await page.evaluate(() => {
          const items = document.querySelectorAll('[role="menuitem"], [role="menuitemcheckbox"], [role="menuitemradio"]');
          for (const el of items) {
            const text = (el.textContent || '').toLowerCase();
            if (
              (text.includes('show live captions') || text.includes('turn on live captions') || text === 'captions') &&
              (el as HTMLElement).offsetParent
            ) {
              (el as HTMLElement).click();
              return (el.textContent || '').trim();
            }
          }
          return null;
        });
        if (clickedSub) {
          log(`[Captions] Clicked submenu: "${clickedSub}"`);
        } else {
          log("[Captions] ⚠️ Could not find live captions in submenu");
        }
        await page.waitForTimeout(1500);
      }

      const state = await inspectCaptionState();
      lastEvidence = state.evidence;
      if (state.enabled) {
        await page.evaluate((evidence) => {
          (window as any).__vexaAcceptanceSignals = {
            ...((window as any).__vexaAcceptanceSignals || {}),
            captions_verified: true,
            captions_unverified: false,
            captions_evidence: evidence,
            captions_observed_at: new Date().toISOString(),
          };
        }, state.evidence).catch(() => {});
        log(`[Captions] ✅ Live captions enabled successfully (${state.evidence})`);
        return;
      }

      log(`[Captions] ⚠️ Captions not verified after attempt ${attempt}/3 (${state.evidence || 'no menu evidence'})`);
      await page.keyboard.press('Escape').catch(() => {});
      await page.waitForTimeout(1500);
    }

    await page.evaluate((evidence) => {
      (window as any).__vexaAcceptanceSignals = {
        ...((window as any).__vexaAcceptanceSignals || {}),
        captions_verified: false,
        captions_unverified: true,
        captions_evidence: evidence,
        captions_observed_at: new Date().toISOString(),
      };
    }, lastEvidence || "none").catch(() => {});
    throw new Error(`captions toggle was not verified after retries; evidence=${lastEvidence || 'none'}`);
  } catch (err: any) {
    // Close any open menu before re-throwing
    try {
      await page.keyboard.press('Escape');
    } catch {}
    throw err;
  }
}
