import { chromium } from 'playwright';

const BASE_URL = 'http://localhost:3002';
const USER_TOKEN = 'vxa_user_GTHNVLYFDps2UlZP80wFGeLBBEwJwC51aOn3h6vc';

async function main() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 900 }
  });

  // Set the vexa-token cookie to simulate logged-in session
  await context.addCookies([{
    name: 'vexa-token',
    value: USER_TOKEN,
    domain: 'localhost',
    path: '/',
    httpOnly: true,
    secure: false,
    sameSite: 'Lax'
  }]);

  const page = await context.newPage();

  // Capture console errors
  const consoleErrors = [];
  page.on('console', msg => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });

  // Capture network responses
  const networkErrors = [];
  page.on('response', resp => {
    if (resp.status() >= 400) {
      networkErrors.push(`${resp.status()} ${resp.url()}`);
    }
  });

  console.log('Step 1: Navigate to /meetings/40');
  const response = await page.goto(`${BASE_URL}/meetings/40`, { waitUntil: 'networkidle', timeout: 30000 });
  console.log(`  Page status: ${response?.status()}`);

  await page.screenshot({ path: '/tmp/agent-test-1-initial.png' });
  console.log('  Screenshot: /tmp/agent-test-1-initial.png');

  const currentUrl = page.url();
  console.log(`  Current URL: ${currentUrl}`);

  if (currentUrl.includes('signin') || currentUrl.includes('login') || currentUrl.includes('auth')) {
    console.log('  -> Still redirected to auth. Token may not be accepted.');
    const pageText = await page.textContent('body');
    console.log('  Page text preview:', pageText?.slice(0, 300));
    await browser.close();
    process.exit(1);
  }

  console.log('  -> Authenticated successfully!');

  // Check for meeting 40 - if doesn't exist, try others
  const pageTitle = await page.title();
  const pageText = await page.textContent('body');
  console.log(`  Page title: ${pageTitle}`);

  if (pageText?.includes('not found') || pageText?.includes('404') || pageText?.includes('No meeting')) {
    console.log('  Meeting 40 not found, trying meeting list...');
    // Navigate to meetings list to find a valid meeting
    await page.goto(`${BASE_URL}/meetings`, { waitUntil: 'networkidle', timeout: 30000 });
    await page.screenshot({ path: '/tmp/agent-test-1b-meetings-list.png' });

    // Find first meeting link
    const meetingLinks = await page.$$('a[href*="/meetings/"]');
    if (meetingLinks.length > 0) {
      const href = await meetingLinks[0].getAttribute('href');
      console.log(`  Found meeting link: ${href}`);
      await page.goto(`${BASE_URL}${href}`, { waitUntil: 'networkidle', timeout: 30000 });
      await page.screenshot({ path: '/tmp/agent-test-1c-meeting-page.png' });
      console.log(`  Navigated to: ${page.url()}`);
    } else {
      console.log('  No meeting links found on meetings page');
      await browser.close();
      process.exit(1);
    }
  }

  console.log('\nStep 2: Look for Agent/Bot button');

  // Wait for page to fully render
  await page.waitForTimeout(2000);
  await page.screenshot({ path: '/tmp/agent-test-2-loaded.png' });
  console.log('  Screenshot: /tmp/agent-test-2-loaded.png');

  // Look for bot/agent button - try various selectors
  const possibleSelectors = [
    '[data-testid="agent-button"]',
    'button[aria-label*="agent" i]',
    'button[aria-label*="bot" i]',
    'button[title*="agent" i]',
    'button[title*="bot" i]',
  ];

  let agentButton = null;
  for (const sel of possibleSelectors) {
    const el = await page.$(sel);
    if (el) {
      console.log(`  Found agent button with selector: ${sel}`);
      agentButton = el;
      break;
    }
  }

  if (!agentButton) {
    // Try finding by SVG content or class names
    console.log('  Trying broader search...');
    const buttons = await page.$$('button');
    console.log(`  Total buttons on page: ${buttons.length}`);
    for (const btn of buttons) {
      const text = await btn.textContent();
      const ariaLabel = await btn.getAttribute('aria-label');
      const innerHTML = await btn.innerHTML();
      const lowerHTML = innerHTML?.toLowerCase() || '';
      const lowerText = text?.toLowerCase() || '';
      const lowerLabel = ariaLabel?.toLowerCase() || '';

      if (lowerText.includes('agent') || lowerText.includes('bot') ||
          lowerLabel.includes('agent') || lowerLabel.includes('bot') ||
          lowerHTML.includes('lucide-bot') || lowerHTML.includes('bot-icon') ||
          lowerHTML.includes('messagebot') || lowerHTML.includes('sparkle') ||
          lowerHTML.includes('zap') || lowerHTML.includes('cpu')) {
        console.log(`  Found button: text="${text?.trim().slice(0,50)}" aria-label="${ariaLabel}"`);
        console.log(`    innerHTML: ${innerHTML?.slice(0,150)}`);
        agentButton = btn;
        break;
      }
    }
  }

  if (!agentButton) {
    // Dump all buttons for debug
    console.log('  Could not find agent button. Dumping all buttons:');
    const buttons = await page.$$('button');
    for (const btn of buttons.slice(0, 30)) {
      const text = await btn.textContent();
      const ariaLabel = await btn.getAttribute('aria-label');
      const innerHTML = await btn.innerHTML();
      console.log(`    button: text="${text?.trim().slice(0,40)}" aria-label="${ariaLabel}" innerHTML="${innerHTML?.slice(0,80)}"`);
    }

    // Also check the page structure
    console.log('\n  Page structure check:');
    const agentRelated = await page.$$('[class*="agent" i], [class*="bot" i], [id*="agent" i], [id*="bot" i]');
    for (const el of agentRelated.slice(0, 10)) {
      const tag = await el.evaluate(e => e.tagName);
      const cls = await el.getAttribute('class');
      const id = await el.getAttribute('id');
      console.log(`    ${tag} class="${cls?.slice(0,60)}" id="${id}"`);
    }

    await browser.close();
    console.log('\nFAIL: Agent button not found');
    process.exit(1);
  }

  console.log('\nStep 3: Click agent button');
  await agentButton.click();
  await page.waitForTimeout(2000);
  await page.screenshot({ path: '/tmp/agent-test-3-after-click.png' });
  console.log('  Screenshot: /tmp/agent-test-3-after-click.png');

  console.log('\nStep 4: Verify panel opened');
  const inputSelectors = [
    'input[placeholder*="message" i]',
    'input[placeholder*="ask" i]',
    'input[placeholder*="type" i]',
    'textarea[placeholder*="message" i]',
    'textarea[placeholder*="ask" i]',
    'textarea[placeholder*="type" i]',
    '[class*="agent"] input',
    '[class*="agent"] textarea',
    '[class*="chat"] input',
    '[class*="chat"] textarea',
    '[class*="panel"] input',
    '[class*="panel"] textarea',
  ];

  let inputField = null;
  for (const sel of inputSelectors) {
    const el = await page.$(sel);
    if (el) {
      const visible = await el.isVisible();
      if (visible) {
        console.log(`  Found visible input with selector: ${sel}`);
        inputField = el;
        break;
      }
    }
  }

  if (!inputField) {
    console.log('  No input field found. Checking page for agent panel...');
    const allInputs = await page.$$('input, textarea');
    console.log(`  Total inputs: ${allInputs.length}`);
    for (const inp of allInputs) {
      const ph = await inp.getAttribute('placeholder');
      const visible = await inp.isVisible();
      console.log(`    input placeholder="${ph}" visible=${visible}`);
    }

    // Check for error messages
    const bodyText = await page.textContent('body');
    const has403 = bodyText?.includes('403') || bodyText?.includes('Forbidden');
    const hasError = bodyText?.includes('error') || bodyText?.includes('Error');
    console.log(`  Has 403: ${has403}, Has error: ${hasError}`);
    console.log('  Network errors:', networkErrors.slice(0, 10));

    await browser.close();
    console.log('\nFAIL: Panel did not open - no input field found');
    process.exit(1);
  }

  console.log('\nStep 5: Type "hello" in input and submit');
  await inputField.fill('hello');
  await page.screenshot({ path: '/tmp/agent-test-4-typed.png' });
  console.log('  Screenshot: /tmp/agent-test-4-typed.png');

  // Try to submit
  await inputField.press('Enter');
  await page.waitForTimeout(3000);
  await page.screenshot({ path: '/tmp/agent-test-5-response.png' });
  console.log('  Screenshot: /tmp/agent-test-5-response.png');

  // Check for 403 errors on agent routes
  const agent403 = networkErrors.filter(e => e.includes('403') && (e.includes('agent') || e.includes('chat')));
  const agentErrors = networkErrors.filter(e => e.includes('agent') || e.includes('chat'));

  console.log('\n=== RESULTS ===');
  console.log('Authentication: PASS (not redirected to login)');
  console.log('Agent button: FOUND and CLICKED');
  console.log('Panel opened: YES (input field visible)');
  console.log('Agent 403 errors:', agent403.length === 0 ? 'NONE' : agent403.join(', '));
  console.log('Agent network errors:', agentErrors.length === 0 ? 'NONE' : agentErrors.join(', '));
  console.log('Console errors:', consoleErrors.slice(0, 3).join('\n') || 'NONE');
  console.log('\nSCREENSHOTS:');
  console.log('  /tmp/agent-test-1-initial.png  (initial page)');
  console.log('  /tmp/agent-test-2-loaded.png   (after load)');
  console.log('  /tmp/agent-test-3-after-click.png (after clicking agent button)');
  console.log('  /tmp/agent-test-4-typed.png    (after typing)');
  console.log('  /tmp/agent-test-5-response.png (after submit)');

  await browser.close();

  if (agent403.length > 0) {
    console.log('\nFAIL: 403 errors on agent routes');
    process.exit(1);
  }

  console.log('\nPASS: All checks passed');
  process.exit(0);
}

main().catch(err => {
  console.error('ERROR:', err.message);
  console.error(err.stack);
  process.exit(1);
});
