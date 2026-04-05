#!/usr/bin/env node
// Auto-admit guests in Google Meet by polling for "Admit" buttons.
// Usage: CDP_URL=ws://... node auto-admit.js [platform]
// Or:    node auto-admit.js <cdp_url> [platform]

const { chromium } = require('playwright');

const cdpUrl = process.argv[2] || process.env.CDP_URL;
const platform = process.argv[3] || process.env.PLATFORM || 'google_meet';

if (!cdpUrl) {
  console.error('Usage: node auto-admit.js <CDP_URL> [platform]');
  process.exit(1);
}

const POLL_INTERVAL = 3000;
let totalAdmitted = 0;

async function admitGuests(page) {
  let admitted = 0;

  // Strategy 1: Click "Admit" or "Admit all" buttons
  for (const label of ['Admit all', 'Admit']) {
    try {
      const buttons = page.locator(`button:has-text("${label}")`);
      const count = await buttons.count();
      for (let i = 0; i < count; i++) {
        const btn = buttons.nth(i);
        if (await btn.isVisible({ timeout: 500 })) {
          await btn.click();
          admitted++;
          console.log(`[auto-admit] Clicked "${label}" button`);
          await page.waitForTimeout(500);
        }
      }
    } catch (e) { /* no button */ }
  }

  // Strategy 2: Confirmation dialog with OK
  try {
    const okBtn = page.locator('button:has-text("OK")').first();
    if (await okBtn.isVisible({ timeout: 500 })) {
      await okBtn.click();
      console.log('[auto-admit] Clicked OK on confirmation dialog');
    }
  } catch (e) { /* no dialog */ }

  // Strategy 3: "Admit N guest(s)" pill - click the smallest matching element
  try {
    const pill = page.locator('text=/Admit \\d+ guest/').first();
    if (await pill.isVisible({ timeout: 500 })) {
      await pill.click();
      admitted++;
      console.log('[auto-admit] Clicked admit guests pill');
      await page.waitForTimeout(1000);
      // After clicking pill, there may be an "Admit all" button
      try {
        const admitAll = page.locator('button:has-text("Admit all")').first();
        if (await admitAll.isVisible({ timeout: 2000 })) {
          await admitAll.click();
          console.log('[auto-admit] Clicked "Admit all" after pill');
        }
      } catch (e) { /* no follow-up */ }
    }
  } catch (e) { /* no pill */ }

  return admitted;
}

(async () => {
  console.log(`[auto-admit] Connecting to ${cdpUrl}`);
  console.log(`[auto-admit] Platform: ${platform}`);

  const browser = await chromium.connectOverCDP(cdpUrl);
  const context = browser.contexts()[0];

  console.log('[auto-admit] Connected. Polling every', POLL_INTERVAL, 'ms');

  const poll = async () => {
    try {
      // Find the Google Meet page
      const pages = context.pages();
      let meetPage = null;
      for (const p of pages) {
        const url = p.url();
        if (url.includes('meet.google.com/') && !url.includes('meet.new')) {
          meetPage = p;
          break;
        }
      }

      if (!meetPage) {
        // Don't log every poll to reduce noise
        return;
      }

      const count = await admitGuests(meetPage);
      if (count > 0) {
        totalAdmitted += count;
        console.log(`[auto-admit] Admitted ${count} guest(s). Total: ${totalAdmitted}`);
      }
    } catch (e) {
      if (e.message.includes('Target closed') || e.message.includes('Connection closed')) {
        console.log('[auto-admit] Browser disconnected. Exiting.');
        process.exit(0);
      }
      // Ignore transient errors
    }
  };

  // Run forever until killed
  setInterval(poll, POLL_INTERVAL);
  // Run once immediately
  await poll();
})().catch(e => {
  console.error('[auto-admit] Fatal:', e.message);
  process.exit(1);
});
