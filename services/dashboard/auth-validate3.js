const { chromium } = require('playwright');

const TOKEN = 'vxa_user_jIwBRUBlQcLeV0aCuYXOtvzNnlC28wpttcPxOXET';
const USER = {
  id: 2,
  email: '2280905@gmail.com',
  name: '2280905',
  max_concurrent_bots: 3,
  created_at: '2026-03-23T18:25:20.956223'
};

// The Zustand persist state for vexa-auth
const authState = JSON.stringify({
  state: {
    user: USER,
    token: TOKEN,
    isAuthenticated: true,
    didLogout: false
  },
  version: 0
});

(async () => {
  const browser = await chromium.launch({ headless: true });
  
  const context = await browser.newContext({ 
    viewport: { width: 1280, height: 720 },
    storageState: {
      cookies: [{
        name: 'vexa-token',
        value: TOKEN,
        url: 'http://localhost:3002',
        httpOnly: false,
        secure: false,
        sameSite: 'Lax'
      }],
      origins: [{
        origin: 'http://localhost:3002',
        localStorage: [{
          name: 'vexa-auth',
          value: authState
        }]
      }]
    }
  });
  
  const page = await context.newPage();

  const pageErrors = [];
  const serverErrors = [];
  
  page.on('console', msg => {
    if (msg.type() === 'error') {
      const text = msg.text();
      // Filter out expected auth 401s from the list we care about
      if (!text.includes('401') && !text.includes('api/auth/me') && !text.includes('Failed to load resource')) {
        pageErrors.push(`CONSOLE_ERR: ${text}`);
      }
    }
  });
  page.on('pageerror', err => pageErrors.push(`UNCAUGHT: ${err.message}`));
  page.on('response', async resp => {
    if (resp.status() >= 500) {
      serverErrors.push({ url: resp.url(), status: resp.status() });
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
    serverErrors.length = 0;
    
    try {
      const resp = await page.goto(`http://localhost:3002${route.path}`, { waitUntil: 'networkidle', timeout: 30000 });
      
      await page.screenshot({ path: `/tmp/screenshots/${route.name}.png`, fullPage: true });

      const status = resp ? resp.status() : 0;
      const finalUrl = page.url();
      const pageTitle = await page.title();
      const bodyText = await page.evaluate(() => document.body ? document.body.innerText : '');
      const hasContent = bodyText.length > 50;
      const bodyPreview = bodyText.substring(0, 150).replace(/\n/g, ' ');
      const errorBoundary = bodyText.includes('Something went wrong') || 
                            bodyText.includes('Application error') ||
                            bodyText.includes('Error: ');

      const errorsSnapshot = [...pageErrors];
      const serverErrSnapshot = [...serverErrors];
      
      // Authenticated pages should NOT redirect to /login (except /login itself)
      const correctlyAuthenticated = route.path === '/login' ? 
        true :  // login page is fine whether redirected or not
        !finalUrl.includes('/login');
      
      results.push({ 
        route: route.path, 
        status, 
        hasContent, 
        errors: errorsSnapshot, 
        errorBoundary, 
        finalUrl, 
        pageTitle,
        correctlyAuthenticated,
        serverErrors: serverErrSnapshot
      });

      const pass = status === 200 && hasContent && errorsSnapshot.length === 0 && !errorBoundary && correctlyAuthenticated;
      console.log(`${pass ? 'PASS' : 'FAIL'} ${route.path} -> ${finalUrl}`);
      console.log(`     status=${status} authed=${correctlyAuthenticated} content=${hasContent} errors=${errorsSnapshot.length} boundary=${errorBoundary}`);
      console.log(`     title="${pageTitle}"`);
      console.log(`     preview: ${bodyPreview.substring(0, 120)}`);
      if (errorsSnapshot.length > 0) {
        console.log(`     JS ERRORS: ${errorsSnapshot.join(' | ')}`);
      }
      if (serverErrSnapshot.length > 0) {
        console.log(`     SERVER ERRORS: ${JSON.stringify(serverErrSnapshot)}`);
      }
    } catch (e) {
      results.push({ route: route.path, status: 0, hasContent: false, errors: [`EXCEPTION: ${e.message}`], errorBoundary: false, finalUrl: '', pageTitle: '', correctlyAuthenticated: false, serverErrors: [] });
      console.log(`FAIL ${route.path}: ${e.message}`);
    }
    console.log('');
  }

  await browser.close();

  const passed = results.filter(r => r.status === 200 && r.hasContent && r.errors.length === 0 && !r.errorBoundary && r.correctlyAuthenticated).length;
  const total = results.length;
  console.log(`SUMMARY: ${passed}/${total} pages clean`);
  
  process.exit(passed === total ? 0 : 1);
})();
