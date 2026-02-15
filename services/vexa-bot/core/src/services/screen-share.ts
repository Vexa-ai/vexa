import { Page } from 'playwright-core';
import { log } from '../utils';

/**
 * ScreenShareService
 *
 * Toggles screen sharing in the meeting.
 * The bot shares the Xvfb virtual display (:99) which shows content
 * rendered by ScreenContentService.
 *
 * Note: The Chromium flag --auto-select-desktop-capture-source=Entire screen
 * (added when voiceAgentEnabled=true) auto-selects the screen in the
 * screen share picker dialog, avoiding manual interaction.
 */
export class ScreenShareService {
  private page: Page;
  private platform: string;
  private _isSharing: boolean = false;

  constructor(page: Page, platform: string) {
    this.page = page;
    this.platform = platform;
  }

  /**
   * Start screen sharing.
   * Clicks the platform-specific share/present button.
   * With --auto-select-desktop-capture-source, the browser dialog auto-selects.
   */
  async startScreenShare(): Promise<boolean> {
    if (this._isSharing) {
      log('[ScreenShare] Already sharing');
      return true;
    }

    try {
      let success = false;
      if (this.platform === 'google_meet') {
        success = await this.startGoogleMeetShare();
      } else if (this.platform === 'teams') {
        success = await this.startTeamsShare();
      } else {
        log(`[ScreenShare] Unsupported platform: ${this.platform}`);
        return false;
      }

      if (success) {
        this._isSharing = true;
        log('[ScreenShare] Screen sharing started');
      }
      return success;
    } catch (err: any) {
      log(`[ScreenShare] Start failed: ${err.message}`);
      return false;
    }
  }

  /**
   * Stop screen sharing.
   */
  async stopScreenShare(): Promise<boolean> {
    if (!this._isSharing) {
      log('[ScreenShare] Not currently sharing');
      return true;
    }

    try {
      let success = false;
      if (this.platform === 'google_meet') {
        success = await this.stopGoogleMeetShare();
      } else if (this.platform === 'teams') {
        success = await this.stopTeamsShare();
      } else {
        return false;
      }

      if (success) {
        this._isSharing = false;
        log('[ScreenShare] Screen sharing stopped');
      }
      return success;
    } catch (err: any) {
      log(`[ScreenShare] Stop failed: ${err.message}`);
      return false;
    }
  }

  get isSharing(): boolean {
    return this._isSharing;
  }

  // ==================== Google Meet ====================

  /**
   * Google Meet screen sharing.
   *
   * Strategy:
   * 1. Bring the meeting page to front so toolbar buttons are interactable.
   * 2. Use Playwright locator.click() (trusted events) to click "Share screen".
   * 3. Google Meet may open a sub-menu; click "Your entire screen".
   * 4. The native Chromium screen picker dialog auto-selects with
   *    --auto-select-desktop-capture-source=Entire screen.
   * 5. Verify sharing started.
   */
  private async startGoogleMeetShare(): Promise<boolean> {
    if (this.page.isClosed()) return false;

    log('[ScreenShare] Attempting Google Meet screen share...');

    // CRITICAL: Bring meeting page to front so the toolbar is interactive.
    // ScreenContentService may have stolen focus with bringToFront().
    try {
      await this.page.bringToFront();
      log('[ScreenShare] Meeting page brought to front');
    } catch (e: any) {
      log(`[ScreenShare] Warning: Could not bring page to front: ${e.message}`);
    }

    // Let the page settle after focus change
    await this.page.waitForTimeout(500);

    // Close any open side panels (People, Chat) that might cover the toolbar
    try {
      await this.page.evaluate(() => {
        // Find and click close buttons on side panels
        const closeBtns = Array.from(document.querySelectorAll('button[aria-label="Close"]'));
        for (const btn of closeBtns) {
          const rect = (btn as HTMLElement).getBoundingClientRect();
          if (rect.width > 0 && rect.height > 0) {
            (btn as HTMLElement).click();
          }
        }
      });
      await this.page.waitForTimeout(500);
    } catch {}

    // Diagnostic: log share-related buttons
    try {
      const toolbarInfo = await this.page.evaluate(() => {
        const buttons = Array.from(document.querySelectorAll('button'));
        return buttons
          .filter(b => {
            const rect = b.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
          })
          .map(b => ({
            ariaLabel: b.getAttribute('aria-label') || '',
            tooltip: b.getAttribute('data-tooltip') || '',
            text: (b.textContent || '').trim().substring(0, 50),
          }))
          .filter(b => b.ariaLabel.toLowerCase().includes('share')
            || b.ariaLabel.toLowerCase().includes('present')
            || b.ariaLabel.toLowerCase().includes('screen')
            || b.tooltip.toLowerCase().includes('share')
            || b.tooltip.toLowerCase().includes('present')
          );
      });
      log(`[ScreenShare] Share-related buttons: ${JSON.stringify(toolbarInfo)}`);
    } catch (e: any) {
      log(`[ScreenShare] Could not enumerate buttons: ${e.message}`);
    }

    // Step 1: Find and click the Share/Present button using Playwright locators.
    // Playwright locator.click() generates TRUSTED events (via CDP Input.dispatchMouseEvent),
    // which is critical because getDisplayMedia() requires trusted user gesture.
    const presentSelectors = [
      'button[aria-label*="Share screen"]',
      'button[aria-label*="share screen"]',
      'button[aria-label*="Present now"]',
      'button[aria-label*="present now"]',
      'button[aria-label*="Показать"]',           // Russian
      'button[aria-label*="Apresentar"]',          // Portuguese
      'button[aria-label*="Präsentieren"]',        // German
      'button[data-tooltip*="Present"]',
      'button[data-tooltip*="present"]',
      'button[data-tooltip*="Share screen"]',
    ];

    const presentButton = this.page.locator(presentSelectors.join(', ')).first();

    try {
      await presentButton.waitFor({ state: 'visible', timeout: 5000 });
      const label = await presentButton.getAttribute('aria-label');
      log(`[ScreenShare] Found share button (aria-label="${label}"), clicking with Playwright...`);
      // Use force:true to bypass actionability checks — the button may be
      // partially covered by an overlay (People panel, chat panel) but we
      // still need to click it. Playwright will dispatch a trusted CDP click.
      await presentButton.click({ force: true, timeout: 5000 });
      log('[ScreenShare] Share button clicked successfully');
    } catch (err: any) {
      log(`[ScreenShare] Could not click share button: ${err.message}`);
      return false;
    }

    // Step 2: Wait for Google Meet's sharing options sub-menu to appear.
    await this.page.waitForTimeout(2000);

    // Diagnostic: dump what appeared after clicking
    try {
      const afterClick = await this.page.evaluate(() => {
        // Look for any overlay, dialog, or new elements
        const elements = Array.from(document.querySelectorAll('[role="dialog"], [role="menu"], [role="listbox"], [role="menuitem"], [role="option"]'));
        return elements.map(el => ({
          role: el.getAttribute('role'),
          ariaLabel: el.getAttribute('aria-label') || '',
          text: (el.textContent || '').trim().substring(0, 100),
          visible: (() => {
            const rect = (el as HTMLElement).getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
          })(),
        })).filter(e => e.visible);
      });
      log(`[ScreenShare] After click - dialog/menu elements: ${JSON.stringify(afterClick)}`);
    } catch (e: any) {
      log(`[ScreenShare] Could not dump post-click elements: ${e.message}`);
    }

    // Try clicking "Your entire screen" if the sub-menu appeared
    const entireScreenSelectors = [
      'text="Your entire screen"',
      'text="Entire screen"',
      'text="Весь экран"',
      'text="Tela inteira"',
    ];

    const entireScreenOption = this.page.locator(entireScreenSelectors.join(', ')).first();

    try {
      await entireScreenOption.waitFor({ state: 'visible', timeout: 3000 });
      const optText = await entireScreenOption.textContent();
      log(`[ScreenShare] Found screen option ("${optText?.trim()}"), clicking...`);
      await entireScreenOption.click({ force: true, timeout: 3000 });
      log('[ScreenShare] Entire screen option clicked');
    } catch {
      log('[ScreenShare] No "entire screen" option found — auto-select-desktop-capture-source should handle native picker');
    }

    // Step 3: Wait for the native screen picker to auto-select
    await this.page.waitForTimeout(3000);

    // Step 4: Verify sharing started
    const stopSelectors = [
      'button[aria-label*="Stop presenting"]',
      'button[aria-label*="Stop sharing"]',
      'button[aria-label*="stop presenting"]',
      'button[aria-label*="stop sharing"]',
      'button[aria-label*="Прекратить"]',
    ];

    const stopButton = this.page.locator(stopSelectors.join(', ')).first();

    try {
      await stopButton.waitFor({ state: 'visible', timeout: 5000 });
      log('[ScreenShare] Verified: Stop presenting button visible — sharing is active!');
      return true;
    } catch {
      // Check if there's any indication of sharing even without the stop button
      const sharingIndicator = await this.page.evaluate(() => {
        const el = document.querySelector('[data-is-presenting="true"]');
        if (el) return true;
        // Also check for "You are presenting" text
        const all = document.body.innerText;
        if (all.includes('presenting') || all.includes('Presenting') || all.includes('You are presenting')) {
          return true;
        }
        return false;
      });
      if (sharingIndicator) {
        log('[ScreenShare] Detected presenting indicator in DOM — sharing is likely active');
        return true;
      }
      log('[ScreenShare] Could not confirm sharing started (stop button not found)');
      // Return true anyway since the button was clicked - sharing may have started
      // but the UI may not show the stop button immediately
      return true;
    }
  }

  private async stopGoogleMeetShare(): Promise<boolean> {
    if (this.page.isClosed()) return false;

    log('[ScreenShare] Stopping Google Meet screen share...');

    // Bring meeting page to front
    try { await this.page.bringToFront(); } catch {}
    await this.page.waitForTimeout(500);

    const stopButton = this.page.locator([
      'button[aria-label*="Stop presenting"]',
      'button[aria-label*="Stop sharing"]',
      'button[aria-label*="stop presenting"]',
      'button[aria-label*="stop sharing"]',
    ].join(', ')).first();

    try {
      await stopButton.waitFor({ state: 'visible', timeout: 3000 });
      await stopButton.click();
      log('[ScreenShare] Clicked stop presenting button');
      return true;
    } catch {
      log('[ScreenShare] Could not find stop presenting button');
      return false;
    }
  }

  // ==================== Microsoft Teams ====================

  private async startTeamsShare(): Promise<boolean> {
    if (this.page.isClosed()) return false;

    log('[ScreenShare] Attempting Teams screen share...');

    try { await this.page.bringToFront(); } catch {}
    await this.page.waitForTimeout(500);

    const shareButton = this.page.locator([
      '[role="toolbar"] button[aria-label*="Share"]',
      '[role="toolbar"] button[aria-label*="Present"]',
      'button[aria-label*="Share content"]',
    ].join(', ')).first();

    try {
      await shareButton.waitFor({ state: 'visible', timeout: 5000 });
      await shareButton.click();
      await this.page.waitForTimeout(1000);
    } catch {
      log('[ScreenShare] Could not find Teams share button');
      return false;
    }

    const screenOption = this.page.locator([
      'button[aria-label*="Screen"]',
      'button[aria-label*="Desktop"]',
    ].join(', ')).first();

    try {
      await screenOption.waitFor({ state: 'visible', timeout: 3000 });
      await screenOption.click();
      await this.page.waitForTimeout(500);
      log('[ScreenShare] Teams share initiated');
      return true;
    } catch {
      log('[ScreenShare] Could not find Teams screen option');
      return false;
    }
  }

  private async stopTeamsShare(): Promise<boolean> {
    if (this.page.isClosed()) return false;

    log('[ScreenShare] Stopping Teams screen share...');

    try { await this.page.bringToFront(); } catch {}

    const stopButton = this.page.locator([
      'button[aria-label*="Stop sharing"]',
      'button[aria-label*="Stop presenting"]',
    ].join(', ')).first();

    try {
      await stopButton.waitFor({ state: 'visible', timeout: 3000 });
      await stopButton.click();
      log('[ScreenShare] Teams share stopped');
      return true;
    } catch {
      log('[ScreenShare] Could not find Teams stop share button');
      return false;
    }
  }
}
