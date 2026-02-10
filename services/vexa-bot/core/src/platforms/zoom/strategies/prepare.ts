import { Page } from 'playwright';
import { BotConfig } from '../../../types';
import { log } from '../../../utils';

export async function prepareZoomRecording(page: Page | null, botConfig: BotConfig): Promise<void> {
  // Zoom uses native SDK, not browser context
  // No browser function exposure needed
  log('[Zoom] Preparing for recording - SDK ready');
}
