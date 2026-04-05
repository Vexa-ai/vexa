const { chromium } = require('playwright');

const TOKEN = 'vxa_user_jIwBRUBlQcLeV0aCuYXOtvzNnlC28wpttcPxOXET';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
  const page = await context.newPage();
  
  // First, navigate to the site to establish context
  await page.goto('http://localhost:3002/login', { waitUntil: 'networkidle', timeout: 15000 });
  
  // Set the cookie via page.evaluate (injecting it directly)
  await context.addCookies([{
    name: 'vexa-token',
    value: TOKEN,
    url: 'http://localhost:3002',
    httpOnly: false,  // Must be false for playwright to inject it  
    secure: false,
    sameSite: 'Lax'
  }]);
  
  // Verify cookie is set
  const cookies = await context.cookies('http://localhost:3002');
  console.log('Cookies set:', cookies.map(c => `${c.name}=${c.value.substring(0, 20)}...`));
  
  // Now navigate to a protected page
  const resp = await page.goto('http://localhost:3002/meetings', { waitUntil: 'networkidle', timeout: 30000 });
  
  console.log(`Status: ${resp?.status()}`);
  console.log(`Final URL: ${page.url()}`);
  console.log(`Title: ${await page.title()}`);
  
  const body = await page.evaluate(() => document.body.innerText.substring(0, 300));
  console.log(`Body: ${body}`);
  
  await page.screenshot({ path: '/tmp/screenshots/meetings-auth.png', fullPage: true });

  await browser.close();
})();
