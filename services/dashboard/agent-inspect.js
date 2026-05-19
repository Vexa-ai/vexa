const { chromium } = require('playwright');

const TOKEN = 'vxa_user_jIwBRUBlQcLeV0aCuYXOtvzNnlC28wpttcPxOXET';
const USER = { id: 2, email: '2280905@gmail.com', name: '2280905', max_concurrent_bots: 3, created_at: '2026-03-23T18:25:20.956223' };
const authState = JSON.stringify({ state: { user: USER, token: TOKEN, isAuthenticated: true, didLogout: false }, version: 0 });

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ 
    viewport: { width: 1280, height: 720 },
    storageState: {
      cookies: [{ name: 'vexa-token', value: TOKEN, url: 'http://localhost:3002', httpOnly: false, secure: false, sameSite: 'Lax' }],
      origins: [{ origin: 'http://localhost:3002', localStorage: [{ name: 'vexa-auth', value: authState }] }]
    }
  });
  const page = await context.newPage();
  
  await page.goto('http://localhost:3002/agent', { waitUntil: 'networkidle', timeout: 30000 });
  
  // Find all interactive input elements
  const inputs = await page.evaluate(() => {
    const all = Array.from(document.querySelectorAll('textarea, input, [contenteditable], [role="textbox"]'));
    return all.map(el => ({ tag: el.tagName, type: el.type || '', placeholder: el.placeholder || '', className: el.className.substring(0, 50) }));
  });
  console.log('Input elements found:', JSON.stringify(inputs, null, 2));
  
  const bodyText = await page.evaluate(() => document.body.innerText);
  console.log('\nBody text (first 500):', bodyText.substring(0, 500));
  
  await page.screenshot({ path: '/tmp/screenshots/agent-inspect.png', fullPage: true });
  await browser.close();
})();
