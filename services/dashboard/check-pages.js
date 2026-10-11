const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
  const page = await context.newPage();

  // Collect ALL network requests with error status
  const failedRequests = [];
  page.on('response', async resp => {
    const status = resp.status();
    if (status >= 400) {
      failedRequests.push({ url: resp.url(), status });
    }
  });

  // Test / route to understand what pages look like unauthenticated
  const resp = await page.goto('http://localhost:3002/', { waitUntil: 'networkidle', timeout: 30000 });
  const title = await page.title();
  const bodyText = await page.evaluate(() => document.body.innerText.substring(0, 300));
  const currentUrl = page.url();
  
  console.log(`Home page: status=${resp?.status()} title="${title}" url=${currentUrl}`);
  console.log(`Body preview: ${bodyText.substring(0, 200)}`);
  console.log(`Failed requests: ${JSON.stringify(failedRequests.slice(0, 5))}`);
  
  failedRequests.length = 0;

  // Check /agent  
  const resp2 = await page.goto('http://localhost:3002/agent', { waitUntil: 'networkidle', timeout: 30000 });
  const title2 = await page.title();
  const bodyText2 = await page.evaluate(() => document.body.innerText.substring(0, 300));
  const currentUrl2 = page.url();
  
  console.log(`\n/agent page: status=${resp2?.status()} title="${title2}" url=${currentUrl2}`);
  console.log(`Body preview: ${bodyText2.substring(0, 200)}`);
  console.log(`Failed requests on /agent: ${JSON.stringify(failedRequests.slice(0, 5))}`);

  await browser.close();
})();
