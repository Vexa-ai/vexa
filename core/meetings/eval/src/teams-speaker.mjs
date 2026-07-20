// A Teams "human" — headless Chromium joins the WEB CLIENT with a WAV as its microphone.
// Real WebRTC audio with known ground truth, no person and no Teams app.
//
//   TEAMS_URL=<meetup-join url> NAME=<display name> WAV=/abs/x.wav [HOLD_MS=180000] node teams-speaker.mjs
//
// The sibling of zoom-speaker.mjs, and it exists for the same reason: a quality run needs SPEECH in
// the room, and a person reading aloud is the one part of the loop that cannot be automated. A WAV
// cut from the cached TTS corpus carries its own transcript, so the scorer compares against what was
// actually said rather than against a second STT pass.
//
// The selectors are the ones @vexa/join already uses in production (modules/join/src/msteams/
// selectors.ts) — a speaker that drifts from the bot's own selector set would fail for reasons that
// say nothing about the pipeline.
const { chromium } = await import(new URL('../../modules/join/node_modules/playwright/index.mjs', import.meta.url).href);

const URL_ = process.env.TEAMS_URL;
const NAME = process.env.NAME ?? 'Speaker';
const WAV = process.env.WAV;
const HOLD_MS = Number(process.env.HOLD_MS ?? 180000);
if (!URL_) { console.error('TEAMS_URL is required'); process.exit(1); }

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
const log = (m) => console.log(`[teams:${NAME}] ${m}`);
log(`→ ${URL_.slice(0, 60)}…`);
await page.goto(URL_, { waitUntil: 'domcontentloaded', timeout: 60000 });

// Teams' launcher page offers the desktop app first; the browser client is behind "Continue on this
// browser". It renders late and sometimes not at all (a direct /_#/ link skips it), so this is a
// try-if-present, never a wait-for.
for (const sel of ['button:has-text("Continue on this browser")', 'button:has-text("Continue")']) {
  const el = page.locator(sel).first();
  if (await el.isVisible({ timeout: 8000 }).catch(() => false)) {
    await el.click().catch(() => {});
    log(`clicked ${sel}`);
    break;
  }
}

// Denied media permission makes Teams render a modal that BLOCKS the prejoin from appearing at all —
// the same trap join.ts documents. Clicking through it is what lets "Join now" exist.
const noMedia = page.locator('button:has-text("Continue without audio or video")').first();
if (await noMedia.isVisible({ timeout: 5000 }).catch(() => false)) {
  await noMedia.click().catch(() => {});
  log('cleared the no-media modal');
}

const nameInput = page.locator('input[placeholder*="name"], input[placeholder*="Name"], input[type="text"]').first();
await nameInput.waitFor({ state: 'visible', timeout: 60000 });
await nameInput.fill(NAME);
log('name entered');

const joinNow = page.locator('button:has-text("Join now")').first();
await joinNow.waitFor({ state: 'visible', timeout: 30000 });
await joinNow.click();
log('clicked Join now');

// A guest lands in the lobby until the host admits. Report which side of that line we are on every
// 15s: a speaker silently stuck in the lobby looks exactly like a capture failure downstream, and
// that ambiguity has cost a whole run before.
const admitted = async () => page.locator('#hangup-button, button[data-tid="hangup-main-btn"], button[aria-label*="Leave"]').first()
  .isVisible({ timeout: 2000 }).catch(() => false);
const deadline = Date.now() + HOLD_MS;
let wasIn = false;
while (Date.now() < deadline) {
  const inMeeting = await admitted();
  if (inMeeting !== wasIn) {
    // Clip offset zero for score_truth.py: the fake-capture file feeds the track from the moment
    // the meeting accepts it. Guessing this instant misattributes whole turns while leaving the
    // content numbers looking fine, which is the worst kind of wrong.
    if (inMeeting) log(`AUDIO_START=${(Date.now() / 1000).toFixed(3)}`);
    log(inMeeting ? 'ADMITTED — speaking' : 'left the meeting'); wasIn = inMeeting;
  }
  else if (!inMeeting) log('still in the lobby (nobody has admitted this speaker yet)');
  await page.waitForTimeout(15000);
}
await ctx.close();
