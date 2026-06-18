const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
  const page = await context.newPage();

  const pageErrors = [];
  page.on('console', msg => {
    if (msg.type() === 'error') pageErrors.push(`CONSOLE_ERR: ${msg.text()}`);
  });
  page.on('pageerror', err => pageErrors.push(`UNCAUGHT: ${err.message}`));

  const routes = ['/', '/login', '/agent', '/meetings', '/workspace', '/mcp', '/webhooks', '/settings', '/profile'];

  const results = [];

  for (const route of routes) {
    pageErrors.length = 0;
    try {
      const resp = await page.goto(`http://localhost:3002${route}`, { waitUntil: 'networkidle', timeout: 30000 });

      const screenshotName = route === '/' ? 'home' : route.replace(/\//g, '-').replace(/^-/, '');
      await page.screenshot({ path: `/tmp/screenshots/${screenshotName}.png`, fullPage: true });

      const status = resp ? resp.status() : 0;
      const hasContent = await page.evaluate(() => (document.body ? document.body.innerText.length : 0) > 10);
      const errorBoundary = await page.evaluate(() => {
        const text = document.body ? document.body.innerText : '';
        return text.includes('went wrong') || text.includes('Something went wrong') || text.includes('Error Boundary');
      });

      const errorsSnapshot = [...pageErrors];
      results.push({ route, status, hasContent, errors: errorsSnapshot, errorBoundary });

      const pass = status === 200 && hasContent && errorsSnapshot.length === 0 && !errorBoundary;
      console.log(`${pass ? 'PASS' : 'FAIL'} ${route} status=${status} content=${hasContent} errors=${errorsSnapshot.length} boundary=${errorBoundary}`);
      if (errorsSnapshot.length > 0) {
        console.log(`  ERRORS: ${errorsSnapshot.slice(0, 3).join(' | ')}`);
      }
    } catch (e) {
      results.push({ route, status: 0, hasContent: false, errors: [`EXCEPTION: ${e.message}`], errorBoundary: false });
      console.log(`FAIL ${route}: ${e.message}`);
    }
  }

  await browser.close();

  const passed = results.filter(r => r.status === 200 && r.hasContent && r.errors.length === 0 && !r.errorBoundary).length;
  const total = results.length;
  console.log(`\nSUMMARY: ${passed}/${total} pages clean`);

  if (passed < total) {
    process.exit(1);
  }
})();
