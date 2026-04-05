const { chromium } = require('playwright');

const TOKEN = 'vxa_user_jIwBRUBlQcLeV0aCuYXOtvzNnlC28wpttcPxOXET';

(async () => {
  const browser = await chromium.launch({ headless: true });
  
  // Create context with the auth cookie pre-set
  const context = await browser.newContext({ 
    viewport: { width: 1280, height: 720 },
    storageState: {
      cookies: [{
        name: 'vexa-token',
        value: TOKEN,
        domain: 'localhost',
        path: '/',
        httpOnly: true,
        secure: false,
        sameSite: 'Lax'
      }]
    }
  });
  
  const page = await context.newPage();

  const pageErrors = [];
  const failedRequests = [];
  
  page.on('console', msg => {
    if (msg.type() === 'error') pageErrors.push(`CONSOLE_ERR: ${msg.text()}`);
  });
  page.on('pageerror', err => pageErrors.push(`UNCAUGHT: ${err.message}`));
  page.on('response', async resp => {
    const status = resp.status();
    const url = resp.url();
    // Only flag non-auth related 5xx errors
    if (status >= 500) {
      failedRequests.push({ url, status });
    }
  });

  const routes = [
    { path: '/', name: 'home' },
    { path: '/login', name: 'login' },
    { path: '/agent', name: 'agent' },
    { path: '/meetings', name: 'meetings' },
    { path: '/workspace', name: 'workspace' },
    { path: '/mcp', name: 'mcp' },
    { path: '/webhooks', name: 'webhooks' },
    { path: '/settings', name: 'settings' },
    { path: '/profile', name: 'profile' },
  ];

  const results = [];

  for (const route of routes) {
    pageErrors.length = 0;
    failedRequests.length = 0;
    
    try {
      const resp = await page.goto(`http://localhost:3002${route.path}`, { waitUntil: 'networkidle', timeout: 30000 });
      
      await page.screenshot({ path: `/tmp/screenshots/${route.name}.png`, fullPage: true });

      const status = resp ? resp.status() : 0;
      const finalUrl = page.url();
      const hasContent = await page.evaluate(() => (document.body ? document.body.innerText.length : 0) > 50);
      const pageTitle = await page.title();
      const bodyPreview = await page.evaluate(() => document.body ? document.body.innerText.substring(0, 150).replace(/\n/g, ' ') : '');
      const errorBoundary = await page.evaluate(() => {
        const text = document.body ? document.body.innerText : '';
        return text.includes('Something went wrong') || text.includes('Error Boundary') || text.includes('Application error');
      });

      // Filter out expected 401s (auth check) - only flag unexpected errors
      const criticalErrors = pageErrors.filter(e => 
        !e.includes('401') && !e.includes('Unauthorized') && !e.includes('api/auth/me')
      );

      results.push({ route: route.path, status, hasContent, errors: criticalErrors, errorBoundary, finalUrl, pageTitle });

      const pass = status === 200 && hasContent && criticalErrors.length === 0 && !errorBoundary;
      console.log(`${pass ? 'PASS' : 'WARN'} ${route.path} -> ${finalUrl} status=${status} content=${hasContent} criticalErrors=${criticalErrors.length} boundary=${errorBoundary}`);
      console.log(`     title="${pageTitle}" preview="${bodyPreview.substring(0, 100)}"`);
      if (criticalErrors.length > 0) {
        console.log(`     CRITICAL ERRORS: ${criticalErrors.join(' | ')}`);
      }
      if (failedRequests.length > 0) {
        console.log(`     5xx REQUESTS: ${JSON.stringify(failedRequests.slice(0, 3))}`);
      }
    } catch (e) {
      results.push({ route: route.path, status: 0, hasContent: false, errors: [`EXCEPTION: ${e.message}`], errorBoundary: false, finalUrl: '', pageTitle: '' });
      console.log(`FAIL ${route.path}: ${e.message}`);
    }
  }

  await browser.close();

  const passed = results.filter(r => r.status === 200 && r.hasContent && r.errors.length === 0 && !r.errorBoundary).length;
  const total = results.length;
  console.log(`\nSUMMARY: ${passed}/${total} pages clean (excluding expected 401 auth checks)`);
})();
