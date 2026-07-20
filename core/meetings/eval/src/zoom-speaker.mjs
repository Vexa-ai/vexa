// A Zoom "human" — headless Chromium joins the WEB CLIENT with a WAV as its microphone.
// Real WebRTC audio with known ground truth, no person and no Zoom app.
//
//   ZOOM_URL=<join url> NAME=<display name> WAV=/abs/x.wav [HOLD_MS=180000] node zoom-speaker.mjs
//
// The /j/ link opens the native-app launcher; /wc/join/ is the browser client, which is the one a
// fake device can drive. The pwd travels as a query param either way.
const { chromium } = await import(new URL('../../modules/join/node_modules/playwright/index.mjs', import.meta.url).href);

const RAW = process.env.ZOOM_URL;
const NAME = process.env.NAME ?? 'Speaker';
const WAV = process.env.WAV;
const HOLD_MS = Number(process.env.HOLD_MS ?? 180000);
if (!RAW) { console.error('ZOOM_URL is required'); process.exit(1); }

// /j/<id>?pwd=… → /wc/join/<id>?pwd=… ; anything already on /wc/ is left alone.
const u = new URL(RAW);
const id = (u.pathname.match(/\/j\/(\d+)/) || [])[1];
const webUrl = id ? `${u.origin}/wc/join/${id}${u.search}` : RAW;

const args = [
  '--use-fake-ui-for-media-stream',
  '--use-fake-device-for-media-stream',
  '--autoplay-policy=no-user-gesture-required',
  '--disable-dev-shm-usage',
  '--no-sandbox',
];
if (WAV) args.push(`--use-file-for-fake-audio-capture=${WAV}%noloop`);

const ctx = await chromium.launchPersistentContext('', { headless: false, args, permissions: ['microphone', 'camera'] });
const page = ctx.pages()[0] ?? await ctx.newPage();
console.log(`[zoom:${NAME}] → ${webUrl}`);
await page.goto(webUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });

// The web client asks for a name, then Join. Selectors drift, so try the documented ones in order
// and report what was actually found rather than assuming a click landed.
// Zoom's pre-join renders late — the name field and Join button do not exist for ~10s after
// domcontentloaded. Probing early finds nothing and looks exactly like a selector break, which is
// what it looked like the first time. Wait for the field itself rather than a fixed sleep.
const nameInput = page.locator('#input-for-name');
await nameInput.waitFor({ state: 'visible', timeout: 45000 });
await nameInput.fill(NAME);
console.log(`[zoom:${NAME}] name entered`);

// Join enables only once a name is present, so click the enabled one rather than the first match.
const joinBtn = page.locator('button.preview-join-button, button:has-text("Join")').first();
await joinBtn.waitFor({ state: 'visible', timeout: 20000 });
for (let i = 0; i < 20 && (await joinBtn.getAttribute('class') || '').includes('disabled'); i++) {
  await page.waitForTimeout(500);
}
await joinBtn.click();
console.log(`[zoom:${NAME}] clicked Join`);
await page.waitForTimeout(8000);

// Zoom gates the mic behind a Join-Audio prompt; without it the WAV never reaches the meeting.
for (const sel of ['button:has-text("Join Audio by Computer")', 'button:has-text("Join Computer Audio")', 'button[aria-label*="Audio" i]']) {
  const el = page.locator(sel).first();
  if (await el.isVisible({ timeout: 3000 }).catch(() => false)) {
    await el.click().catch(() => { /* */ });
    console.log(`[zoom:${NAME}] audio joined via ${sel}`);
    break;
  }
}

const state = await page.evaluate(() => ({ url: location.href, body: document.body.innerText.slice(0, 220) }));
console.log(`[zoom:${NAME}] state ${JSON.stringify(state)}`);
console.log(`[zoom:${NAME}] holding ${HOLD_MS}ms`);
await page.waitForTimeout(HOLD_MS);
await ctx.close();
