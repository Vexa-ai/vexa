/**
 * Structural regression tests for Google Meet admission rejection handling.
 *
 * The 2026-05-01 live repro showed host denial can leave waiting-room text
 * visible, so polling must check rejection before treating the page as still
 * waiting. A full Playwright DOM test would require brittle UI fixtures; this
 * source-shape check pins the safety invariant cheaply.
 */

import * as fs from 'fs';
import * as path from 'path';

let passed = 0;
let failed = 0;

function expectFileContains(
  name: string,
  filePath: string,
  needle: string | RegExp,
) {
  const body = fs.readFileSync(filePath, 'utf-8');
  const ok = typeof needle === 'string' ? body.includes(needle) : needle.test(body);
  if (ok) {
    console.log(`  \x1b[32mPASS\x1b[0m  ${name}`);
    passed++;
    return;
  }
  console.log(`  \x1b[31mFAIL\x1b[0m  ${name}`);
  console.log(`        needle: ${needle}`);
  console.log(`        in:     ${filePath}`);
  failed++;
}

function expectOrder(
  name: string,
  body: string,
  firstNeedle: string,
  secondNeedle: string,
) {
  const firstIndex = body.indexOf(firstNeedle);
  const secondIndex = firstIndex === -1 ? -1 : body.indexOf(secondNeedle, firstIndex);
  const ok = firstIndex !== -1 && secondIndex !== -1 && firstIndex < secondIndex;
  if (ok) {
    console.log(`  \x1b[32mPASS\x1b[0m  ${name}`);
    passed++;
    return;
  }
  console.log(`  \x1b[31mFAIL\x1b[0m  ${name}`);
  console.log(`        expected "${firstNeedle}" before "${secondNeedle}"`);
  failed++;
}

const ADMISSION_TS = path.join(__dirname, 'admission.ts');
const SELECTORS_TS = path.join(__dirname, 'selectors.ts');
const admissionBody = fs.readFileSync(ADMISSION_TS, 'utf-8');

console.log('\n=== Google Meet admission rejection handling ===');

expectFileContains(
  'admission.ts has reusable rejection guard',
  ADMISSION_TS,
  'throwIfGoogleAdmissionRejected',
);

expectOrder(
  'waiting-room loop checks rejection before waiting-room state',
  admissionBody,
  'throwIfGoogleAdmissionRejected(page, "waiting-room polling")',
  'const stillWaiting = await checkForWaitingRoomIndicators(page)',
);

expectOrder(
  'late waiting-room loop checks rejection before waiting-room state',
  admissionBody,
  'throwIfGoogleAdmissionRejected(page, "late waiting-room polling")',
  'const stillWaiting = await checkForWaitingRoomIndicators(page)',
);

expectFileContains(
  'selectors include host-denied request text',
  SELECTORS_TS,
  'denied your request',
);

expectFileContains(
  'selectors include retry affordance after denial',
  SELECTORS_TS,
  'Ask to join again',
);

console.log(`\n=== googlemeet admission summary: ${passed} passed, ${failed} failed ===`);
process.exit(failed > 0 ? 1 : 0);
