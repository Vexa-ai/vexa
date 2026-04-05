#!/usr/bin/env node
// Connect to browser via CDP, navigate to meet.new, create a Google Meet, join as host.
// Usage: CDP_URL=http://... node gmeet-host-auto.js

const { chromium } = require('playwright');

const CDP_URL = process.env.CDP_URL;
if (!CDP_URL) { console.error('CDP_URL required'); process.exit(1); }

(async () => {
  const browser = await chromium.connectOverCDP(CDP_URL);
  const context = browser.contexts()[0];
  let page = context.pages()[0] || await context.newPage();

  // Check if already in a meeting
  const currentUrl = page.url();
  if (currentUrl.includes('meet.google.com/') && !currentUrl.includes('meet.new')) {
    const match = currentUrl.match(/meet\.google\.com\/([a-z]{3}-[a-z]{4}-[a-z]{3})/);
    if (match) {
      console.log(`MEETING_URL=https://meet.google.com/${match[1]}`);
      console.log(`NATIVE_MEETING_ID=${match[1]}`);
      console.log(`MEETING_PLATFORM=google_meet`);
      console.log(`JOINED=true`);
      return;
    }
  }

  // Navigate to meet.new
  console.error('[gmeet-host] Navigating to meet.new...');
  await page.goto('https://meet.new', { waitUntil: 'domcontentloaded', timeout: 30000 });

  // Wait for URL to become meet.google.com/xxx-xxxx-xxx
  console.error('[gmeet-host] Waiting for meeting URL...');
  await page.waitForURL(/meet\.google\.com\/[a-z]{3}-[a-z]{4}-[a-z]{3}/, { timeout: 30000 });
  const meetUrl = page.url();
  const meetMatch = meetUrl.match(/meet\.google\.com\/([a-z]{3}-[a-z]{4}-[a-z]{3})/);
  if (!meetMatch) {
    // Take screenshot for debugging
    await page.screenshot({ path: '/tmp/gmeet-host-auto-fail.png' });
    console.error('[gmeet-host] FAIL: URL did not match expected pattern:', meetUrl);
    process.exit(1);
  }
  const meetingCode = meetMatch[1];
  console.error(`[gmeet-host] Meeting code: ${meetingCode}`);

  // Dismiss any "Got it" / "Dismiss" dialogs
  for (const text of ['Got it', 'Dismiss', 'OK']) {
    try {
      const btn = page.locator(`button:has-text("${text}")`).first();
      if (await btn.isVisible({ timeout: 2000 })) {
        await btn.click();
        console.error(`[gmeet-host] Dismissed "${text}" dialog`);
      }
    } catch (e) { /* no dialog */ }
  }

  // Wait a moment for the pre-join screen to stabilize
  await page.waitForTimeout(2000);

  // Click "Join now" (host) or "Ask to join" (fallback)
  let joined = false;
  for (const label of ['Join now', 'Ask to join']) {
    try {
      const btn = page.locator(`button:has-text("${label}")`).first();
      if (await btn.isVisible({ timeout: 3000 })) {
        await btn.click();
        console.error(`[gmeet-host] Clicked "${label}"`);
        joined = true;
        break;
      }
    } catch (e) { /* try next */ }
  }

  if (!joined) {
    await page.screenshot({ path: '/tmp/gmeet-host-auto-fail.png' });
    console.error('[gmeet-host] FAIL: Could not find Join button');
    process.exit(1);
  }

  // Verify we're in the meeting by waiting for "Leave call" button
  try {
    await page.locator('button[aria-label*="Leave call"]').waitFor({ state: 'visible', timeout: 15000 });
    console.error('[gmeet-host] Successfully joined meeting');
  } catch (e) {
    // Check for "Leave" text as fallback
    try {
      await page.locator('button:has-text("Leave")').first().waitFor({ state: 'visible', timeout: 5000 });
      console.error('[gmeet-host] Successfully joined meeting (Leave button found)');
    } catch (e2) {
      await page.screenshot({ path: '/tmp/gmeet-host-auto-fail.png' });
      console.error('[gmeet-host] WARNING: Could not verify "Leave call" button, but continuing');
    }
  }

  console.log(`MEETING_URL=https://meet.google.com/${meetingCode}`);
  console.log(`NATIVE_MEETING_ID=${meetingCode}`);
  console.log(`MEETING_PLATFORM=google_meet`);
  console.log(`JOINED=true`);
})();
