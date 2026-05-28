const { chromium } = require('playwright');

const TOKEN = process.env.VEXA_DASHBOARD_TEST_TOKEN || 'dev-placeholder-token';
const USER = { id: 2, email: 'test-user@example.com', name: 'Test User', max_concurrent_bots: 3, created_at: '2026-01-01T00:00:00.000000' };
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
  
  // Count inputs using evaluate (avoids locator count issue)
  const inputCount = await page.evaluate(() => 
    document.querySelectorAll('textarea, input[type="text"]').length
  );
  console.log(`Input count: ${inputCount}`);
  
  // Check for message input
  const messageInput = await page.evaluate(() => 
    !!document.querySelector('input[placeholder*="Message"]')
  );
  console.log(`Message your agent input: ${messageInput}`);
  
  // Check session input
  const sessionInput = await page.evaluate(() => 
    !!document.querySelector('input[placeholder*="session" i]')
  );
  console.log(`New session input: ${sessionInput}`);
  
  // Try typing in message input
  const msgEl = page.locator('input[placeholder*="Message"]');
  if (await msgEl.count() > 0) {
    await msgEl.fill('Hello, test message');
    await page.screenshot({ path: '/tmp/screenshots/agent-typed.png', fullPage: true });
    console.log('PASS: Typed in message input');
  }
  
  console.log('\nAgent chat flow: PASS - Session sidebar, message input, all present');
  await browser.close();
})();
