import { Page } from 'playwright';
import { BotConfig } from '../../../types';
import { AdmissionDecision } from '../../shared/meetingFlow';
import { log } from '../../../utils';

export async function waitForZoomAdmission(
  page: Page | null,
  timeoutMs: number,
  botConfig: BotConfig
): Promise<boolean | AdmissionDecision> {
  // Zoom SDK handles admission automatically during the join process
  // If we reach this point, we've already been admitted (from join.ts)
  log('[Zoom] Admission check - already admitted via SDK');
  return true;
}

export async function checkZoomAdmissionSilent(page: Page | null): Promise<boolean> {
  // SDK automatically waits for admission during join
  // This is a silent check, so just return true
  return true;
}
