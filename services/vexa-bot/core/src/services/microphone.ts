import { Page } from 'playwright-core';
import { log } from '../utils';

/**
 * MicrophoneService
 *
 * Unified microphone toggle across meeting platforms.
 * The bot's mic is muted by default on join. This service unmutes it
 * when the voice agent needs to speak and re-mutes when done.
 */
export class MicrophoneService {
  private page: Page;
  private platform: string;
  private _isMuted: boolean = true;
  private muteTimerId: NodeJS.Timeout | null = null;

  constructor(page: Page, platform: string) {
    this.page = page;
    this.platform = platform;
  }

  /**
   * Ensure microphone is unmuted.
   * Returns true if mic is now unmuted, false if toggle failed.
   */
  async unmute(): Promise<boolean> {
    if (!this._isMuted) return true;

    this.clearMuteTimer();

    try {
      let success = false;
      if (this.platform === 'google_meet') {
        success = await this.toggleGoogleMeetMic(true);
      } else if (this.platform === 'teams') {
        success = await this.toggleTeamsMic(true);
      } else {
        log(`[Microphone] Unsupported platform for mic toggle: ${this.platform}`);
        return false;
      }

      if (success) {
        this._isMuted = false;
        log('[Microphone] Unmuted');
      }
      return success;
    } catch (err: any) {
      log(`[Microphone] Unmute failed: ${err.message}`);
      return false;
    }
  }

  /**
   * Mute the microphone.
   */
  async mute(): Promise<boolean> {
    if (this._isMuted) return true;

    this.clearMuteTimer();

    try {
      let success = false;
      if (this.platform === 'google_meet') {
        success = await this.toggleGoogleMeetMic(false);
      } else if (this.platform === 'teams') {
        success = await this.toggleTeamsMic(false);
      } else {
        return false;
      }

      if (success) {
        this._isMuted = true;
        log('[Microphone] Muted');
      }
      return success;
    } catch (err: any) {
      log(`[Microphone] Mute failed: ${err.message}`);
      return false;
    }
  }

  /**
   * Schedule auto-mute after a delay (for post-speech silence).
   * Cancel with clearMuteTimer() or a new unmute() call.
   */
  scheduleAutoMute(delayMs: number = 2000): void {
    this.clearMuteTimer();
    this.muteTimerId = setTimeout(() => {
      this.mute().catch((err) => {
        log(`[Microphone] Auto-mute failed: ${err.message}`);
      });
    }, delayMs);
  }

  /**
   * Cancel any scheduled auto-mute.
   */
  clearMuteTimer(): void {
    if (this.muteTimerId) {
      clearTimeout(this.muteTimerId);
      this.muteTimerId = null;
    }
  }

  get isMuted(): boolean {
    return this._isMuted;
  }

  // --- Google Meet ---

  private async toggleGoogleMeetMic(unmute: boolean): Promise<boolean> {
    if (this.page.isClosed()) return false;

    return await this.page.evaluate(async (shouldUnmute: boolean) => {
      // Use specific selectors for bot's own meeting controls (same as join flow)
      // Prioritize "Turn on/off microphone" to avoid matching participant tiles
      const selectors = [
        '[aria-label*="Turn on microphone"]',
        '[aria-label*="Turn off microphone"]',
        'button[aria-label*="Turn on microphone"]',
        'button[aria-label*="Turn off microphone"]',
        'button[aria-label*="microphone"]',
        'button[aria-label*="Microphone"]'
      ];

      for (const sel of selectors) {
        const btn = document.querySelector(sel) as HTMLElement | null;
        if (!btn) continue;

        const ariaLabel = (btn.getAttribute('aria-label') || '').toLowerCase();
        const isMuted = ariaLabel.includes('turn on') || ariaLabel.includes('unmute');

        // Click if state doesn't match desired state
        if ((shouldUnmute && isMuted) || (!shouldUnmute && !isMuted)) {
          btn.click();
          return true;
        }
        // Already in desired state
        if ((shouldUnmute && !isMuted) || (!shouldUnmute && isMuted)) {
          return true;
        }
      }
      return false;
    }, unmute);
  }

  // --- Microsoft Teams ---

  private async toggleTeamsMic(unmute: boolean): Promise<boolean> {
    if (this.page.isClosed()) return false;

    return await this.page.evaluate(async (shouldUnmute: boolean) => {
      const selectors = [
        '#microphone-button',
        'button[data-tid="toggle-mute"]',
        'button[aria-label*="Mute"]',
        'button[aria-label*="mute"]',
        'button[aria-label*="Unmute"]',
        'button[aria-label*="unmute"]',
        '[role="toolbar"] button[aria-label*="Mic"]',
        '[role="toolbar"] button[aria-label*="mic"]'
      ];

      for (const sel of selectors) {
        const btn = document.querySelector(sel) as HTMLElement | null;
        if (!btn) continue;

        const ariaLabel = (btn.getAttribute('aria-label') || '').toLowerCase();
        const isMuted = ariaLabel.includes('unmute') || ariaLabel.includes('turn on');

        if ((shouldUnmute && isMuted) || (!shouldUnmute && !isMuted)) {
          btn.click();
          return true;
        }
        return true;
      }
      return false;
    }, unmute);
  }
}
