// Drive the Zoom classic-client reCAPTCHA over CDP: click "I'm not a robot",
// report whether it passed (Join enabled) or escalated to an image challenge.
const { chromium } = require('playwright');
(async () => {
  const b = await chromium.connectOverCDP('http://localhost:9222');
  const ctx = b.contexts()[0];
  const pages = ctx.pages();
  const page = pages.find(p => p.url().includes('zoom')) || pages[0];
  console.log('page:', page.url().slice(0, 70));

  const anchor = page.frames().find(f => f.url().includes('recaptcha') && f.url().includes('anchor'));
  if (!anchor) { console.log('NO_ANCHOR_FRAME'); }
  else {
    try {
      await anchor.locator('#recaptcha-anchor').click({ timeout: 8000 });
      console.log('clicked I-am-not-a-robot checkbox');
    } catch (e) { console.log('checkbox click err:', e.message); }
    await page.waitForTimeout(4500);
    const checked = await anchor.locator('#recaptcha-anchor').getAttribute('aria-checked').catch(() => null);
    console.log('aria-checked:', checked);
  }

  // Did an image challenge appear (bframe visible with content)?
  const bframe = page.frames().find(f => f.url().includes('recaptcha') && f.url().includes('bframe'));
  let challengeVisible = false;
  if (bframe) {
    challengeVisible = await bframe.locator('.rc-imageselect, #rc-imageselect, .rc-imageselect-instructions').first()
      .isVisible({ timeout: 1500 }).catch(() => false);
  }
  console.log('image_challenge_visible:', challengeVisible);

  const joinDisabled = await page.evaluate(() => {
    const b = document.querySelector('#joinBtn'); return b ? b.disabled : 'no-btn';
  });
  console.log('joinBtn.disabled:', joinDisabled);

  if (joinDisabled === false) {
    await page.locator('#joinBtn').click({ timeout: 5000 }).catch(e => console.log('join click err:', e.message));
    console.log('CLICKED JOIN');
  }
  await page.screenshot({ path: '/tmp/cap.jpg', type: 'jpeg', quality: 70 }).catch(() => {});
  await b.close();
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
